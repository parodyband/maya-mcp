"""Transient, vision-friendly rig placement previews.

Preview creation, update, and cancellation use direct Maya API edits, so they
do not enter Maya's global undo queue or temporarily disable undo. The real
scene dirty state is left untouched: synchronous third-party callback edits
are therefore never hidden. Only ``accept`` creates permanent rig nodes, and
that change is recorded as one Maya undo chunk.
"""

from __future__ import annotations

import contextlib
import copy
import json
import math
import re
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import maya.api.OpenMaya as om
import maya.cmds as cmds

from . import state

_TAG_ATTRIBUTE = "mayaMcpRigPreview"
_ID_ATTRIBUTE = "mayaMcpPreviewId"
_EPOCH_ATTRIBUTE = "mayaMcpSceneEpoch"
_REVISION_ATTRIBUTE = "mayaMcpPreviewRevision"
_SPEC_ATTRIBUTE = "mayaMcpPreviewSpec"
_PREVIEWS: dict[str, dict[str, Any]] = {}

# Previews have no timer-based expiry. These two hard caps bound both retained
# Maya state and the output of ``action=list`` until a caller cancels a preview
# or Maya opens/creates another scene.
_MAX_ACTIVE_PREVIEWS = 16
_MAX_TOTAL_OWNED_NODES = 8192

_JOINT_RGB = [1.0, 0.62, 0.05]
_BONE_RGB = [1.0, 0.22, 0.08]
_CONTROL_RGB = [0.05, 0.78, 1.0]


def _absolute_name(name: str) -> str:
    """Return a Maya name rooted independently of the active namespace."""

    return name if name.startswith(":") else f":{name}"


@contextlib.contextmanager
def _root_namespace() -> Iterator[None]:
    previous = str(cmds.namespaceInfo(currentNamespace=True, absoluteName=True))
    cmds.namespace(set=":")
    try:
        yield
    finally:
        cmds.namespace(set=previous)


def _dependency_object(node: str) -> om.MObject:
    selection = om.MSelectionList()
    selection.add(node)
    return selection.getDependNode(0)


def _object_uuid(node: om.MObject) -> str:
    return om.MFnDependencyNode(node).uuid().asString()


def _object_name(node: om.MObject) -> str:
    if node.hasFn(om.MFn.kDagNode):
        return om.MFnDagNode(node).fullPathName()
    return om.MFnDependencyNode(node).name()


def _set_exact_name(node: om.MObject, name: str) -> str:
    dependency = om.MFnDependencyNode(node)
    expected = name.lstrip(":")
    actual = dependency.setName(_absolute_name(expected))
    if actual != expected:
        raise state.ToolError(
            "PREVIEW_NAME_COLLISION",
            f"Maya renamed an internal preview node from {expected} to {actual}",
            {"expected": expected, "actual": actual},
        )
    return actual


def _add_api_string_attribute(
    dependency: om.MFnDependencyNode, name: str, value: str
) -> None:
    if not dependency.hasAttribute(name):
        attribute = om.MFnTypedAttribute().create(
            name, name, om.MFnData.kString
        )
        dependency.addAttribute(attribute)
    dependency.findPlug(name, False).setString(value)


def _tag_object(
    node: om.MObject, preview_id: str, epoch: str, revision: int
) -> None:
    dependency = om.MFnDependencyNode(node)
    dependency.setDoNotWrite(True)
    if not dependency.hasAttribute(_TAG_ATTRIBUTE):
        attribute = om.MFnNumericAttribute().create(
            _TAG_ATTRIBUTE,
            _TAG_ATTRIBUTE,
            om.MFnNumericData.kBoolean,
            False,
        )
        dependency.addAttribute(attribute)
    dependency.findPlug(_TAG_ATTRIBUTE, False).setBool(True)
    _add_api_string_attribute(dependency, _ID_ATTRIBUTE, preview_id)
    _add_api_string_attribute(dependency, _EPOCH_ATTRIBUTE, epoch)
    if not dependency.hasAttribute(_REVISION_ATTRIBUTE):
        attribute = om.MFnNumericAttribute().create(
            _REVISION_ATTRIBUTE,
            _REVISION_ATTRIBUTE,
            om.MFnNumericData.kLong,
            0,
        )
        dependency.addAttribute(attribute)
    dependency.findPlug(_REVISION_ATTRIBUTE, False).setInt(revision)


def _own_object(
    node: om.MObject,
    owned: list[dict[str, str]],
    preview_id: str,
    epoch: str,
    revision: int,
) -> dict[str, str]:
    """Record and tag exactly one node returned by our own API operation."""

    entry = {"uuid": _object_uuid(node), "long_name": _object_name(node)}
    # Record before tagging so a later attribute failure can still roll back
    # this exact UUID. Descendants are intentionally never enumerated here.
    owned.append(entry)
    _tag_object(node, preview_id, epoch, revision)
    entry["long_name"] = _object_name(node)
    return entry


def _create_owned_transform(
    name: str,
    parent: om.MObject,
    owned: list[dict[str, str]],
    preview_id: str,
    epoch: str,
    revision: int,
) -> tuple[om.MObject, dict[str, str]]:
    node = om.MFnTransform().create(parent)
    _set_exact_name(node, name)
    return node, _own_object(node, owned, preview_id, epoch, revision)


def _create_owned_dependency(
    node_type: str,
    name: str,
    owned: list[dict[str, str]],
    preview_id: str,
    epoch: str,
    revision: int,
) -> tuple[om.MObject, dict[str, str]]:
    node = om.MFnDependencyNode().create(node_type)
    _set_exact_name(node, name)
    return node, _own_object(node, owned, preview_id, epoch, revision)


def _create_owned_curve(
    name: str,
    points: list[tuple[float, float, float]],
    parent: om.MObject,
    owned: list[dict[str, str]],
    preview_id: str,
    epoch: str,
    revision: int,
) -> tuple[om.MObject, om.MObject, dict[str, str], dict[str, str]]:
    transform, transform_entry = _create_owned_transform(
        name, parent, owned, preview_id, epoch, revision
    )
    curve = om.MFnNurbsCurve().create(
        [om.MPoint(*point) for point in points],
        [float(index) for index in range(len(points))],
        1,
        om.MFnNurbsCurve.kOpen,
        False,
        False,
        transform,
    )
    _set_exact_name(curve, f"{name}Shape")
    curve_entry = _own_object(
        curve, owned, preview_id, epoch, revision
    )
    return transform, curve, transform_entry, curve_entry


def _set_api_rgb_override(node: om.MObject, color: list[float]) -> None:
    dependency = om.MFnDependencyNode(node)
    dependency.findPlug("overrideEnabled", False).setBool(True)
    dependency.findPlug("overrideRGBColors", False).setBool(True)
    rgb = dependency.findPlug("overrideColorRGB", False)
    for index, value in enumerate(color):
        rgb.child(index).setFloat(float(value))
    dependency.findPlug("overrideDisplayType", False).setInt(2)
    if dependency.hasAttribute("lineWidth"):
        dependency.findPlug("lineWidth", False).setFloat(2.0)


def _set_transform(
    node: om.MObject,
    *,
    matrix: list[float] | None = None,
    position: list[float] | None = None,
    rotation: list[float] | None = None,
) -> None:
    transform = om.MFnTransform(node)
    if matrix is not None:
        transform.setTransformation(
            om.MTransformationMatrix(om.MMatrix(matrix))
        )
    if position is not None:
        transform.setTranslation(
            om.MVector(*(float(value) for value in position)),
            om.MSpace.kTransform,
        )
    if rotation is not None:
        transform.setRotation(
            om.MEulerRotation(
                *(math.radians(float(value)) for value in rotation)
            ),
            om.MSpace.kTransform,
        )


def _joint_marker_points(radius: float) -> list[tuple[float, float, float]]:
    r = float(radius)
    return [
        (r, 0, 0), (0, r, 0), (-r, 0, 0), (0, -r, 0), (r, 0, 0),
        (0, 0, r), (-r, 0, 0), (0, 0, -r), (r, 0, 0), (0, r, 0),
        (0, 0, r), (0, -r, 0), (0, 0, -r), (0, r, 0),
    ]


def _control_points(
    shape: str, size: float
) -> list[tuple[float, float, float]]:
    if shape == "circle":
        segments = 32
        return [
            (
                0.0,
                math.cos((2.0 * math.pi * index) / segments) * size,
                math.sin((2.0 * math.pi * index) / segments) * size,
            )
            for index in range(segments + 1)
        ]
    if shape == "square":
        return [
            (0, -size, -size), (0, -size, size), (0, size, size),
            (0, size, -size), (0, -size, -size),
        ]
    return [
        (-size, -size, -size), (-size, -size, size), (-size, size, size),
        (-size, size, -size), (-size, -size, -size), (size, -size, -size),
        (size, -size, size), (-size, -size, size), (-size, size, size),
        (size, size, size), (size, -size, size), (size, -size, -size),
        (size, size, -size), (-size, size, -size), (size, size, -size),
        (size, size, size),
    ]


def _safe_token(value: str, fallback: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")
    if not token:
        token = fallback
    if token[0].isdigit():
        token = f"n_{token}"
    return token[:48]


def _long_name(node: str) -> str:
    return (cmds.ls(node, long=True, objectsOnly=True) or [node])[0]


def _node_uuid(node: str) -> str:
    values = cmds.ls(node, uuid=True) or []
    return values[0] if values else ""


def _ownership_matches(node: str, record: dict[str, Any]) -> bool:
    try:
        return bool(cmds.getAttr(f"{node}.{_TAG_ATTRIBUTE}")) and str(
            cmds.getAttr(f"{node}.{_ID_ATTRIBUTE}")
        ) == record["preview_id"] and str(
            cmds.getAttr(f"{node}.{_EPOCH_ATTRIBUTE}")
        ) == record["scene_epoch"]
    except (RuntimeError, ValueError):
        return False


def _resolve_uuid_entry(entry: dict[str, str]) -> str | None:
    candidate = entry["long_name"]
    if (
        cmds.objExists(candidate)
        and _node_uuid(candidate) == entry["uuid"]
    ):
        return _long_name(candidate)
    for node in cmds.ls(dependencyNodes=True, long=True) or []:
        if _node_uuid(node) == entry["uuid"]:
            return _long_name(node)
    return None


def _resolve_entry(entry: dict[str, str], record: dict[str, Any]) -> str | None:
    node = _resolve_uuid_entry(entry)
    if node is not None and _ownership_matches(node, record):
        return node
    return None


def _entry_by_uuid(record: dict[str, Any], node_uuid: str) -> dict[str, str] | None:
    return next(
        (entry for entry in record["owned_nodes"] if entry["uuid"] == node_uuid),
        None,
    )


def _node_by_uuid(record: dict[str, Any], node_uuid: str | None) -> str | None:
    if not node_uuid:
        return None
    entry = _entry_by_uuid(record, node_uuid)
    return _resolve_entry(entry, record) if entry else None


def _indexed_override(node: str, color: int | None) -> None:
    if color is None:
        return
    for shape in cmds.listRelatives(node, shapes=True, fullPath=True) or []:
        cmds.setAttr(f"{shape}.overrideEnabled", True)
        cmds.setAttr(f"{shape}.overrideRGBColors", False)
        cmds.setAttr(f"{shape}.overrideColor", int(color))


def _control_curve(shape: str, name: str, size: float) -> str:
    if shape == "circle":
        return cmds.circle(
            name=name, normal=(1, 0, 0), radius=size, constructionHistory=False
        )[0]
    if shape == "square":
        points = [
            (0, -size, -size), (0, -size, size), (0, size, size),
            (0, size, -size), (0, -size, -size),
        ]
    else:
        points = [
            (-size, -size, -size), (-size, -size, size), (-size, size, size),
            (-size, size, -size), (-size, -size, -size), (size, -size, -size),
            (size, -size, size), (-size, -size, size), (-size, size, size),
            (size, size, size), (size, -size, size), (size, -size, -size),
            (size, size, -size), (-size, size, -size), (size, size, -size),
            (size, size, size),
        ]
    return cmds.curve(name=name, degree=1, point=points)


def _validate_graph(items: list[dict[str, Any]], kind: str) -> None:
    identifiers = [str(item["id"]) for item in items]
    if len(identifiers) != len(set(identifiers)):
        raise state.ToolError("INVALID_ARGUMENT", f"{kind} ids must be unique")
    by_id = {str(item["id"]): item for item in items}
    for item in items:
        parent_id = item.get("parent_id")
        if parent_id is not None and parent_id not in by_id:
            raise state.ToolError(
                "INVALID_ARGUMENT",
                f"{kind} {item['id']} has unknown parent_id {parent_id}",
            )
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in visiting:
            raise state.ToolError(
                "INVALID_ARGUMENT", f"{kind} hierarchy contains a cycle"
            )
        if identifier in visited:
            return
        visiting.add(identifier)
        parent_id = by_id[identifier].get("parent_id")
        if parent_id is not None:
            visit(str(parent_id))
        visiting.remove(identifier)
        visited.add(identifier)

    for identifier in identifiers:
        visit(identifier)


def _ordered(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(item["id"]): item for item in items}
    ordered: list[dict[str, Any]] = []
    emitted: set[str] = set()

    def emit(identifier: str) -> None:
        if identifier in emitted:
            return
        item = by_id[identifier]
        if item.get("parent_id") is not None:
            emit(str(item["parent_id"]))
        ordered.append(item)
        emitted.add(identifier)

    for item in items:
        emit(str(item["id"]))
    return ordered


def _normalize_spec(
    arguments: dict[str, Any], existing: dict[str, Any] | None = None
) -> dict[str, Any]:
    spec = copy.deepcopy(existing) if existing is not None else {
        "name": "Rig Preview", "joints": [], "controls": [], "orient": True,
        "primary_axis": "xyz", "secondary_axis": "yup",
        "joint_color": list(_JOINT_RGB), "bone_color": list(_BONE_RGB),
        "control_color": list(_CONTROL_RGB),
    }
    fields = {
        "name", "joints", "controls", "parent", "orient", "primary_axis",
        "secondary_axis", "joint_color", "bone_color", "control_color",
    }
    for field in fields:
        if field in arguments:
            spec[field] = copy.deepcopy(arguments[field])
    if spec.get("parent") is not None:
        spec["parent"] = state.node_ref(state.resolve_node(spec["parent"]))
    for control in spec["controls"]:
        if control.get("target") is not None:
            control["target"] = state.node_ref(state.resolve_node(control["target"]))
    if not spec["joints"] and not spec["controls"]:
        raise state.ToolError(
            "INVALID_ARGUMENT", "A rig preview requires joints or controls"
        )
    _validate_graph(spec["joints"], "joint")
    _validate_graph(spec["controls"], "control")
    joint_ids = {str(item["id"]) for item in spec["joints"]}
    for control in spec["controls"]:
        target_joint_id = control.get("target_joint_id")
        if control.get("target") is not None and target_joint_id is not None:
            raise state.ToolError(
                "INVALID_ARGUMENT",
                f"Control {control['id']} cannot use both target and target_joint_id",
            )
        if target_joint_id is not None and target_joint_id not in joint_ids:
            raise state.ToolError(
                "INVALID_ARGUMENT",
                f"Control {control['id']} has unknown target_joint_id {target_joint_id}",
            )
        if control.get("target") is None and target_joint_id is None and control.get("position") is None:
            raise state.ToolError(
                "INVALID_ARGUMENT",
                f"Control {control['id']} requires target, target_joint_id, or position",
            )
        if control.get("constraint", "none") != "none" and control.get("target") is None and target_joint_id is None:
            raise state.ToolError(
                "INVALID_ARGUMENT",
                f"Control {control['id']} cannot constrain without a target",
            )
    return spec


def _control_preview_transform(
    control: dict[str, Any], joint_positions: dict[str, list[float]]
) -> tuple[list[float] | None, list[float] | None, list[float] | None]:
    matrix: list[float] | None = None
    if control.get("target") is not None:
        target = state.resolve_node(control["target"])
        matrix = list(cmds.xform(target, query=True, worldSpace=True, matrix=True))
    position = control.get("position")
    if position is None and control.get("target_joint_id") is not None:
        position = joint_positions[str(control["target_joint_id"])]
    return matrix, position, control.get("rotation")


def _retain_cleanup_record(
    record: dict[str, Any],
    *,
    status: str,
    cause: Exception,
) -> dict[str, Any] | None:
    """Keep every surviving created UUID addressable by a cleanup handle."""

    retained = copy.deepcopy(record)
    survivors: list[dict[str, str]] = []
    resolved: dict[str, str] = {}
    for entry in retained["owned_nodes"]:
        node = _resolve_uuid_entry(entry)
        if node is not None:
            entry["long_name"] = node
            survivors.append(entry)
            resolved[entry["uuid"]] = node
    if not survivors:
        return None

    cleanup_id = f"rig-preview:{uuid.uuid4().hex}"
    retained["preview_id"] = cleanup_id
    retained["owned_nodes"] = survivors
    retained["status"] = status
    retained["cleanup_only"] = True
    surviving_uuids = set(resolved)
    retained["roles"] = {
        role: node_uuid if node_uuid in surviving_uuids else None
        for role, node_uuid in retained.get("roles", {}).items()
    }
    tag_errors: list[dict[str, str]] = []
    for entry in survivors:
        node = resolved[entry["uuid"]]
        try:
            obj = _dependency_object(node)
            _tag_object(
                obj,
                cleanup_id,
                retained["scene_epoch"],
                int(retained["revision"]),
            )
            entry["long_name"] = _object_name(obj)
        except Exception as error:
            tag_errors.append(
                {
                    "uuid": entry["uuid"],
                    "node": node,
                    "type": type(error).__name__,
                    "message": str(error),
                }
            )
    retained["cleanup_diagnostics"] = {
        "cause": {"type": type(cause).__name__, "message": str(cause)},
        "tag_errors": tag_errors,
    }
    _PREVIEWS[cleanup_id] = retained
    return retained


def _estimated_owned_nodes(spec: dict[str, Any]) -> int:
    bones = sum(
        1 for joint in spec["joints"] if joint.get("parent_id") is not None
    )
    # Root, three grouping transforms, and display layer; every marker/bone/
    # control curve contributes one transform and one curve shape.
    return (
        5
        + (2 * len(spec["joints"]))
        + (2 * bones)
        + (2 * len(spec["controls"]))
    )


def _current_owned_count() -> int:
    return sum(len(record["owned_nodes"]) for record in _PREVIEWS.values())


def _active_preview_count() -> int:
    return sum(
        1
        for record in _PREVIEWS.values()
        if record.get("status", "active") == "active"
    )


def _enforce_preview_capacity(
    spec: dict[str, Any], *, replacing: dict[str, Any] | None = None
) -> None:
    active_count = _active_preview_count()
    if replacing is None and active_count >= _MAX_ACTIVE_PREVIEWS:
        raise state.ToolError(
            "PREVIEW_LIMIT_EXCEEDED",
            "The active rig-preview limit has been reached",
            {
                "active_previews": active_count,
                "max_active_previews": _MAX_ACTIVE_PREVIEWS,
            },
        )
    projected_nodes = _current_owned_count() + _estimated_owned_nodes(spec)
    if projected_nodes > _MAX_TOTAL_OWNED_NODES:
        raise state.ToolError(
            "PREVIEW_NODE_LIMIT_EXCEEDED",
            "Creating this rig preview would exceed the owned-node limit",
            {
                "owned_nodes": _current_owned_count(),
                "requested_nodes": _estimated_owned_nodes(spec),
                "projected_nodes": projected_nodes,
                "max_owned_nodes": _MAX_TOTAL_OWNED_NODES,
                "replacing_preview": (
                    _preview_handle(replacing) if replacing is not None else None
                ),
            },
        )


def _build_preview(
    preview_id: str, revision: int, spec: dict[str, Any]
) -> dict[str, Any]:
    epoch = state.scene_epoch()
    short_id = preview_id.rsplit(":", 1)[-1][:10].upper()
    prefix = f"MAYA_MCP_RIG_PREVIEW_{short_id}_R{revision:04d}"
    owned: list[dict[str, str]] = []
    root_entry: dict[str, str] | None = None
    display_layer_entry: dict[str, str] | None = None
    joint_marker_nodes: list[dict[str, Any]] = []
    control_marker_nodes: list[dict[str, Any]] = []
    try:
        reserved = [f"{prefix}_GRP", f"{prefix}_LYR"]
        if any(cmds.objExists(_absolute_name(name)) for name in reserved):
            raise state.ToolError(
                "PREVIEW_NAME_COLLISION",
                "Could not allocate unique Maya MCP preview grouping nodes",
            )
        root, root_entry = _create_owned_transform(
            f"{prefix}_GRP",
            om.MObject.kNullObj,
            owned,
            preview_id,
            epoch,
            revision,
        )
        joints_group, _ = _create_owned_transform(
            f"{prefix}_JOINTS_GRP",
            root,
            owned,
            preview_id,
            epoch,
            revision,
        )
        bones_group, _ = _create_owned_transform(
            f"{prefix}_BONES_GRP",
            root,
            owned,
            preview_id,
            epoch,
            revision,
        )
        controls_group, _ = _create_owned_transform(
            f"{prefix}_CONTROLS_GRP",
            root,
            owned,
            preview_id,
            epoch,
            revision,
        )
        joint_positions = {
            str(item["id"]): [float(value) for value in item["position"]]
            for item in spec["joints"]
        }
        for index, joint in enumerate(spec["joints"]):
            identifier = str(joint["id"])
            token = _safe_token(identifier, f"joint_{index:03d}")
            marker, marker_shape, marker_entry, _ = _create_owned_curve(
                f"{prefix}_J_{index:03d}_{token}_MRK",
                _joint_marker_points(float(joint.get("radius", 0.5))),
                joints_group,
                owned,
                preview_id,
                epoch,
                revision,
            )
            _set_transform(marker, position=joint_positions[identifier])
            _set_api_rgb_override(marker_shape, list(spec["joint_color"]))
            bone_entry = None
            if joint.get("parent_id") is not None:
                parent_position = joint_positions[str(joint["parent_id"])]
                _, bone_shape, bone_entry, _ = _create_owned_curve(
                    f"{prefix}_B_{index:03d}_{token}_CRV",
                    [
                        tuple(parent_position),
                        tuple(joint_positions[identifier]),
                    ],
                    bones_group,
                    owned,
                    preview_id,
                    epoch,
                    revision,
                )
                _set_api_rgb_override(bone_shape, list(spec["bone_color"]))
            joint_marker_nodes.append(
                {
                    "id": identifier,
                    "marker": marker_entry["uuid"],
                    "bone": bone_entry["uuid"] if bone_entry else None,
                }
            )

        for index, control in enumerate(spec["controls"]):
            identifier = str(control["id"])
            token = _safe_token(identifier, f"control_{index:03d}")
            marker, marker_shape, marker_entry, _ = _create_owned_curve(
                f"{prefix}_C_{index:03d}_{token}_MRK",
                _control_points(
                    str(control.get("shape", "circle")),
                    float(control.get("size", 1.0)),
                ),
                controls_group,
                owned,
                preview_id,
                epoch,
                revision,
            )
            matrix, position, rotation = _control_preview_transform(
                control, joint_positions
            )
            _set_transform(
                marker,
                matrix=matrix,
                position=position,
                rotation=rotation,
            )
            _set_api_rgb_override(marker_shape, list(spec["control_color"]))
            control_marker_nodes.append(
                {"id": identifier, "marker": marker_entry["uuid"]}
            )

        display_layer, display_layer_entry = _create_owned_dependency(
            "displayLayer",
            f"{prefix}_LYR",
            owned,
            preview_id,
            epoch,
            revision,
        )
        om.MFnDependencyNode(display_layer).findPlug(
            "displayType", False
        ).setInt(2)
        _add_api_string_attribute(
            om.MFnDependencyNode(root),
            _SPEC_ATTRIBUTE,
            json.dumps(
                state.json_safe(spec),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    except Exception as error:
        if owned and not _rollback_owned_entries(owned):
            partial_record = {
                "preview_id": preview_id,
                "scene_epoch": epoch,
                "revision": revision,
                "status": "build_cleanup_failed",
                "spec": copy.deepcopy(spec),
                "owned_nodes": copy.deepcopy(owned),
                "roles": {
                    "root": (
                        root_entry["uuid"]
                        if root_entry is not None
                        else None
                    ),
                    "display_layer": (
                        display_layer_entry["uuid"]
                        if display_layer_entry is not None
                        else None
                    ),
                },
                "joint_markers": copy.deepcopy(joint_marker_nodes),
                "control_markers": copy.deepcopy(control_marker_nodes),
            }
            retained = _retain_cleanup_record(
                partial_record,
                status="build_cleanup_failed",
                cause=error,
            )
            raise state.ToolError(
                "PREVIEW_BUILD_ROLLBACK_FAILED",
                "Rig preview construction failed and Maya could not remove "
                "every explicitly created node; surviving UUIDs remain "
                "tracked by a cleanup handle",
                {
                    "cleanup_handle": (
                        _preview_handle(retained)
                        if retained is not None
                        else None
                    ),
                    "owned_nodes": (
                        copy.deepcopy(retained["owned_nodes"])
                        if retained is not None
                        else []
                    ),
                    "cause": {
                        "type": type(error).__name__,
                        "message": str(error),
                    },
                },
            ) from error
        raise

    return {
        "preview_id": preview_id,
        "scene_epoch": epoch,
        "revision": revision,
        "status": "active",
        "spec": spec,
        "owned_nodes": owned,
        "roles": {
            "root": root_entry["uuid"],
            "display_layer": display_layer_entry["uuid"],
        },
        "joint_markers": joint_marker_nodes,
        "control_markers": control_marker_nodes,
    }


def _preflight_destroy(record: dict[str, Any]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    missing: list[str] = []
    tampered: list[dict[str, str]] = []
    for entry in record["owned_nodes"]:
        node = _resolve_uuid_entry(entry)
        if node is None:
            missing.append(entry["uuid"])
        elif not _ownership_matches(node, record):
            tampered.append({"uuid": entry["uuid"], "node": node})
        else:
            resolved[entry["uuid"]] = node
    if missing:
        raise state.ToolError(
            "PREVIEW_DAMAGED",
            "The preview is missing owned nodes; cleanup was refused",
            {"missing_uuids": missing},
        )
    if tampered:
        raise state.ToolError(
            "PREVIEW_TAMPERED",
            "Preview ownership tags changed; cleanup was refused",
            {"nodes": tampered},
        )
    owned_uuids = {entry["uuid"] for entry in record["owned_nodes"]}
    if not record.get("cleanup_only", False):
        for role in ("root", "display_layer"):
            role_uuid = record["roles"].get(role)
            if not role_uuid or role_uuid not in owned_uuids:
                raise state.ToolError(
                    "PREVIEW_DAMAGED",
                    f"The preview has an invalid {role} ownership reference",
                    {"role": role, "uuid": role_uuid},
                )
    _validate_deletion_roots(resolved, owned_uuids)
    return resolved


def _deletion_roots(resolved: dict[str, str]) -> list[tuple[str, str]]:
    owned_uuids = set(resolved)
    roots: list[tuple[str, str]] = []
    for node_uuid, node in resolved.items():
        obj = _dependency_object(node)
        if not obj.hasFn(om.MFn.kDagNode):
            roots.append((node_uuid, node))
            continue
        dag = om.MFnDagNode(obj)
        owned_parent = False
        for index in range(dag.parentCount()):
            parent = dag.parent(index)
            if (
                parent.hasFn(om.MFn.kDependencyNode)
                and _object_uuid(parent) in owned_uuids
            ):
                owned_parent = True
                break
        if not owned_parent:
            roots.append((node_uuid, node))
    # Remove DAG roots first, then independent DG nodes such as display layers.
    return sorted(
        roots,
        key=lambda item: (
            1
            if _dependency_object(item[1]).hasFn(om.MFn.kDagNode)
            else 2,
            item[0],
        ),
    )


def _validate_deletion_roots(
    resolved: dict[str, str], owned_uuids: set[str]
) -> None:
    for _, root in _deletion_roots(resolved):
        obj = _dependency_object(root)
        if not obj.hasFn(om.MFn.kDagNode):
            continue
        for descendant in cmds.listRelatives(
            root, allDescendents=True, fullPath=True
        ) or []:
            descendant_uuid = _node_uuid(descendant)
            if descendant_uuid not in owned_uuids:
                raise state.ToolError(
                    "PREVIEW_TAMPERED",
                    "The preview contains a descendant not owned by Maya MCP; "
                    "cleanup was refused",
                    {
                        "root": root,
                        "node": descendant,
                        "uuid": descendant_uuid,
                    },
                )


def _delete_resolved_entries(
    entries: list[dict[str, str]], resolved: dict[str, str]
) -> None:
    applied: list[om.MDGModifier] = []
    try:
        for _, node in _deletion_roots(resolved):
            obj = _dependency_object(node)
            if obj.hasFn(om.MFn.kDagNode):
                modifier: om.MDGModifier = om.MDagModifier()
                modifier.deleteNode(obj, False)
            else:
                modifier = om.MDGModifier()
                modifier.deleteNode(obj)
            modifier.doIt()
            applied.append(modifier)
        survivors = [
            entry["uuid"]
            for entry in entries
            if _resolve_uuid_entry(entry) is not None
        ]
        if survivors:
            raise state.ToolError(
                "PREVIEW_CLEANUP_FAILED",
                "Owned preview nodes survived cleanup",
                {"surviving_uuids": survivors},
            )
    except Exception as error:
        rollback_errors: list[str] = []
        for modifier in reversed(applied):
            try:
                modifier.undoIt()
            except Exception as rollback_error:
                rollback_errors.append(str(rollback_error))
        if isinstance(error, state.ToolError):
            if rollback_errors:
                error.details["rollback_errors"] = rollback_errors
            raise
        raise state.ToolError(
            "PREVIEW_CLEANUP_FAILED",
            "Maya failed while deleting owned preview nodes",
            {
                "type": type(error).__name__,
                "message": str(error),
                "rollback_errors": rollback_errors,
            },
        ) from error


def _rollback_owned_entries(entries: list[dict[str, str]]) -> bool:
    """Best-effort rollback for a preview that failed during construction."""

    resolved = {
        entry["uuid"]: node
        for entry in entries
        if (node := _resolve_uuid_entry(entry)) is not None
    }
    if not resolved:
        return True
    try:
        _validate_deletion_roots(
            resolved, {entry["uuid"] for entry in entries}
        )
        _delete_resolved_entries(entries, resolved)
    except Exception:
        return False
    return True


def _destroy_preview(record: dict[str, Any], *, strict: bool) -> bool:
    try:
        resolved = _preflight_destroy(record)
    except state.ToolError:
        if strict:
            raise
        # A completed scene replacement may already have removed every node.
        # That is the only non-strict missing-node case considered successful.
        if not any(
            _resolve_uuid_entry(entry) is not None
            for entry in record["owned_nodes"]
        ):
            return True
        return False
    _delete_resolved_entries(record["owned_nodes"], resolved)
    return True


def _preview_handle(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "preview_id": record["preview_id"],
        "scene_epoch": record["scene_epoch"],
        "revision": record["revision"],
    }


def _preview_view(record: dict[str, Any]) -> dict[str, Any]:
    roles = {
        role: (
            state.node_ref(node)
            if (node := _node_by_uuid(record, node_uuid)) is not None
            else None
        )
        for role, node_uuid in record["roles"].items()
    }
    joints_by_id = {str(item["id"]): item for item in record["spec"]["joints"]}
    controls_by_id = {str(item["id"]): item for item in record["spec"]["controls"]}
    joint_markers = []
    for marker in record["joint_markers"]:
        definition = joints_by_id[marker["id"]]
        marker_node = _node_by_uuid(record, marker["marker"])
        bone_node = _node_by_uuid(record, marker["bone"])
        joint_markers.append(
            {
                "id": marker["id"],
                "name": definition.get("name") or f"{marker['id']}_JNT",
                "position": definition["position"],
                "parent_id": definition.get("parent_id"),
                "radius": float(definition.get("radius", 0.5)),
                "marker": state.node_ref(marker_node) if marker_node else None,
                "bone": state.node_ref(bone_node) if bone_node else None,
            }
        )
    control_markers = []
    for marker in record["control_markers"]:
        definition = controls_by_id[marker["id"]]
        marker_node = _node_by_uuid(record, marker["marker"])
        control_markers.append(
            {
                "id": marker["id"],
                "name": definition.get("name") or f"{marker['id']}_CTRL",
                "target_joint_id": definition.get("target_joint_id"),
                "target": definition.get("target"),
                "position": definition.get("position"),
                "rotation": definition.get("rotation"),
                "parent_id": definition.get("parent_id"),
                "shape": definition.get("shape", "circle"),
                "size": float(definition.get("size", 1.0)),
                "marker": state.node_ref(marker_node) if marker_node else None,
            }
        )
    view = {
        "handle": _preview_handle(record),
        "status": record.get("status", "active"),
        "name": record["spec"]["name"],
        "spec": state.json_safe(copy.deepcopy(record["spec"])),
        "grouping": roles,
        "joint_markers": joint_markers,
        "control_markers": control_markers,
        "counts": {"joints": len(joint_markers), "controls": len(control_markers)},
        "display": {
            "joint_rgb": record["spec"]["joint_color"],
            "bone_rgb": record["spec"]["bone_color"],
            "control_rgb": record["spec"]["control_color"],
            "selectable": False,
        },
    }
    if record.get("accepted_output") is not None:
        view["accepted_output"] = record["accepted_output"]
    if record.get("cleanup_diagnostics") is not None:
        view["cleanup_diagnostics"] = state.json_safe(
            copy.deepcopy(record["cleanup_diagnostics"])
        )
    return view


def _require_preview(arguments: dict[str, Any]) -> dict[str, Any]:
    handle = arguments.get("handle")
    if not isinstance(handle, dict):
        raise state.ToolError(
            "INVALID_ARGUMENT",
            f"{arguments.get('action', 'This action')} requires a preview handle",
        )
    requested_epoch = str(handle.get("scene_epoch", ""))
    current_epoch = state.scene_epoch()
    if requested_epoch != current_epoch:
        raise state.ToolError(
            "SCENE_EPOCH_MISMATCH",
            "The rig preview belongs to a different Maya scene epoch",
            {"expected": requested_epoch, "actual": current_epoch},
        )
    preview_id = str(handle.get("preview_id", ""))
    record = _PREVIEWS.get(preview_id)
    if record is None:
        raise state.ToolError(
            "PREVIEW_NOT_FOUND",
            "The rig preview no longer exists",
            {"preview_id": preview_id},
        )
    requested_revision = int(handle.get("revision", 0))
    if requested_revision != int(record["revision"]):
        raise state.ToolError(
            "PREVIEW_REVISION_CONFLICT",
            "The rig preview changed after this handle was issued",
            {
                "preview_id": preview_id,
                "expected": requested_revision,
                "actual": record["revision"],
                "current_handle": _preview_handle(record),
            },
        )
    return record


_OUTPUT_NAME_RE = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_]*:)*[A-Za-z_][A-Za-z0-9_]*$"
)


def _output_name(
    definition: dict[str, Any],
    field: str,
    suffix: str,
    fallback: str,
) -> str:
    value = definition.get(field)
    if value is not None:
        return str(value)
    token = _safe_token(str(definition["id"]), fallback)
    return f"{token}{suffix}"


def _ensure_permanent_target(node: str, role: str) -> str:
    if cmds.attributeQuery(_TAG_ATTRIBUTE, node=node, exists=True):
        try:
            tagged = bool(cmds.getAttr(f"{node}.{_TAG_ATTRIBUTE}"))
        except RuntimeError as error:
            raise state.ToolError(
                "INVALID_TARGET",
                f"{role} has an unreadable rig-preview ownership tag",
                {"node": state.node_ref(node)},
            ) from error
        if tagged:
            raise state.ToolError(
                "INVALID_TARGET",
                f"{role} cannot be another transient rig preview node",
                {"node": state.node_ref(node)},
            )
    return node


def _require_exact_created_name(node: str, expected: str, role: str) -> str:
    """Reject Maya's automatic numeric suffixing inside the undo transaction."""

    matches = cmds.ls(node, long=True, objectsOnly=True) or []
    if len(matches) != 1:
        raise state.ToolError(
            "OUTPUT_NAME_CHANGED",
            f"Maya did not return exactly one created node for {role}",
            {"expected": expected, "matches": matches[:20]},
        )
    actual = matches[0].rsplit("|", 1)[-1]
    expected_leaf = expected.lstrip(":")
    if actual != expected_leaf:
        raise state.ToolError(
            "OUTPUT_NAME_CHANGED",
            f"Maya renamed {role} from {expected_leaf} to {actual}",
            {"expected": expected_leaf, "actual": actual, "role": role},
        )
    # Keep the unique short name as the working identifier. DAG long paths
    # become stale as controls, groups, and joints are parented below.
    return actual


def _preflight_accept(record: dict[str, Any]) -> dict[str, Any]:
    resolved = _preflight_destroy(record)
    missing = [
        entry["uuid"]
        for entry in record["owned_nodes"]
        if entry["uuid"] not in resolved
    ]
    if missing:
        raise state.ToolError(
            "PREVIEW_DAMAGED",
            "The rig preview is missing owned nodes and cannot be accepted",
            {"missing_uuids": missing},
        )

    spec = record["spec"]
    parent = None
    if spec.get("parent") is not None:
        parent = _ensure_permanent_target(
            state.resolve_node(spec["parent"]), "The output parent"
        )
    targets: dict[str, str] = {}
    for control in spec["controls"]:
        identifier = str(control["id"])
        if control.get("target") is not None:
            targets[identifier] = _ensure_permanent_target(
                state.resolve_node(control["target"]),
                f"Control {identifier}'s target",
            )

    reserved: dict[str, str] = {}

    def reserve(name: str, role: str) -> str:
        if len(name) > 128 or not _OUTPUT_NAME_RE.fullmatch(name):
            raise state.ToolError(
                "INVALID_OUTPUT_NAME",
                f"Invalid Maya output name for {role}: {name}",
                {"name": name, "role": role},
            )
        namespace = name.rsplit(":", 1)[0] if ":" in name else ""
        absolute_namespace = _absolute_name(namespace) if namespace else ""
        if namespace and not cmds.namespace(exists=absolute_namespace):
            raise state.ToolError(
                "INVALID_OUTPUT_NAME",
                f"Output namespace does not exist for {role}: {namespace}",
                {
                    "name": name,
                    "role": role,
                    "namespace": absolute_namespace,
                },
            )
        if name in reserved:
            raise state.ToolError(
                "OUTPUT_NAME_CONFLICT",
                f"Two rig outputs request the same Maya name: {name}",
                {"name": name, "roles": [reserved[name], role]},
            )
        absolute_name = _absolute_name(name)
        if cmds.objExists(absolute_name):
            raise state.ToolError(
                "OUTPUT_NAME_CONFLICT",
                f"A Maya node already uses the requested output name: {name}",
                {
                    "name": name,
                    "role": role,
                    "existing": [
                        state.node_ref(node)
                        for node in (
                            cmds.ls(
                                absolute_name,
                                long=True,
                                objectsOnly=True,
                            )
                            or []
                        )
                    ],
                },
            )
        reserved[name] = role
        return name

    joint_names: dict[str, str] = {}
    for index, joint in enumerate(_ordered(spec["joints"])):
        identifier = str(joint["id"])
        joint_names[identifier] = reserve(
            _output_name(joint, "name", "_JNT", f"joint_{index:03d}"),
            f"joint {identifier}",
        )

    control_names: dict[str, str] = {}
    control_shape_names: dict[str, str] = {}
    group_names: dict[str, str] = {}
    constraint_names: dict[str, str] = {}
    constraint_suffix = {
        "parent": "PAR_CON",
        "orient": "ORI_CON",
        "point": "PNT_CON",
    }
    for index, control in enumerate(_ordered(spec["controls"])):
        identifier = str(control["id"])
        control_name = reserve(
            _output_name(control, "name", "_CTRL", f"control_{index:03d}"),
            f"control {identifier}",
        )
        control_names[identifier] = control_name
        control_shape_names[identifier] = reserve(
            f"{control_name}Shape",
            f"control {identifier} curve shape",
        )
        group_names[identifier] = reserve(
            str(control.get("offset_name") or f"{control_name}_ZERO"),
            f"control {identifier} offset group",
        )
        constraint = str(control.get("constraint", "none"))
        if constraint != "none":
            constraint_names[identifier] = reserve(
                str(
                    control.get("constraint_name")
                    or f"{control_name}_{constraint_suffix[constraint]}"
                ),
                f"control {identifier} {constraint} constraint",
            )

    return {
        "parent": parent,
        "targets": targets,
        "joint_names": joint_names,
        "control_names": control_names,
        "control_shape_names": control_shape_names,
        "group_names": group_names,
        "constraint_names": constraint_names,
    }


def _accept_nodes(
    record: dict[str, Any],
    plan: dict[str, Any],
    call: state.CallState,
) -> dict[str, Any]:
    spec = record["spec"]
    ordered_joints = _ordered(spec["joints"])
    ordered_controls = _ordered(spec["controls"])
    joint_nodes: dict[str, str] = {}

    for joint in ordered_joints:
        identifier = str(joint["id"])
        node = cmds.createNode("joint", name=plan["joint_names"][identifier])
        node = _require_exact_created_name(
            node, plan["joint_names"][identifier], f"joint {identifier}"
        )
        state.mark_mutated(call)
        cmds.setAttr(f"{node}.radius", float(joint.get("radius", 0.5)))
        cmds.xform(
            node,
            worldSpace=True,
            translation=[float(value) for value in joint["position"]],
        )
        joint_nodes[identifier] = node

    for joint in ordered_joints:
        identifier = str(joint["id"])
        parent_id = joint.get("parent_id")
        if parent_id is not None:
            cmds.parent(
                joint_nodes[identifier],
                joint_nodes[str(parent_id)],
                absolute=True,
            )
        elif plan["parent"] is not None:
            cmds.parent(joint_nodes[identifier], plan["parent"], absolute=True)

    if spec.get("orient", True):
        parent_ids = {
            str(joint["parent_id"])
            for joint in ordered_joints
            if joint.get("parent_id") is not None
        }
        root_ids = [
            str(joint["id"])
            for joint in ordered_joints
            if joint.get("parent_id") is None
        ]
        for identifier in root_ids:
            if identifier in parent_ids:
                cmds.joint(
                    joint_nodes[identifier],
                    edit=True,
                    orientJoint=spec.get("primary_axis", "xyz"),
                    secondaryAxisOrient=spec.get("secondary_axis", "yup"),
                    children=True,
                    zeroScaleOrient=True,
                )
        for joint in ordered_joints:
            identifier = str(joint["id"])
            if identifier not in parent_ids:
                cmds.setAttr(
                    f"{joint_nodes[identifier]}.jointOrient", 0.0, 0.0, 0.0
                )

    control_nodes: dict[str, str] = {}
    group_nodes: dict[str, str] = {}
    for control in ordered_controls:
        identifier = str(control["id"])
        node = _control_curve(
            str(control.get("shape", "circle")),
            plan["control_names"][identifier],
            float(control.get("size", 1.0)),
        )
        node = _require_exact_created_name(
            node,
            plan["control_names"][identifier],
            f"control {identifier}",
        )
        shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
        if len(shapes) != 1:
            raise state.ToolError(
                "OUTPUT_NAME_CHANGED",
                f"Control {identifier} did not create exactly one curve shape",
                {"control": node, "shapes": shapes[:20]},
            )
        expected_shape_name = plan["control_shape_names"][identifier]
        actual_shape_name = shapes[0].rsplit("|", 1)[-1]
        if actual_shape_name != expected_shape_name.lstrip(":"):
            shapes[0] = cmds.rename(
                shapes[0], _absolute_name(expected_shape_name)
            )
        _require_exact_created_name(
            shapes[0],
            expected_shape_name,
            f"control {identifier} curve shape",
        )
        state.mark_mutated(call)
        group = cmds.group(node, name=plan["group_names"][identifier])
        group = _require_exact_created_name(
            group,
            plan["group_names"][identifier],
            f"control {identifier} offset group",
        )
        target = plan["targets"].get(identifier)
        target_joint_id = control.get("target_joint_id")
        if target_joint_id is not None:
            target = joint_nodes[str(target_joint_id)]
        if target is not None:
            matrix = cmds.xform(target, query=True, worldSpace=True, matrix=True)
            cmds.xform(group, worldSpace=True, matrix=matrix)
        if control.get("position") is not None:
            cmds.xform(
                group,
                worldSpace=True,
                translation=[float(value) for value in control["position"]],
            )
        if control.get("rotation") is not None:
            cmds.xform(
                group,
                worldSpace=True,
                rotation=[float(value) for value in control["rotation"]],
            )
        _indexed_override(node, control.get("color"))
        control_nodes[identifier] = node
        group_nodes[identifier] = group

    for control in ordered_controls:
        identifier = str(control["id"])
        parent_id = control.get("parent_id")
        if parent_id is not None:
            cmds.parent(
                group_nodes[identifier],
                control_nodes[str(parent_id)],
                absolute=True,
            )
        elif plan["parent"] is not None:
            cmds.parent(group_nodes[identifier], plan["parent"], absolute=True)

    constraint_nodes: dict[str, str] = {}
    for control in ordered_controls:
        identifier = str(control["id"])
        constraint = str(control.get("constraint", "none"))
        if constraint == "none":
            continue
        target = plan["targets"].get(identifier)
        if control.get("target_joint_id") is not None:
            target = joint_nodes[str(control["target_joint_id"])]
        kwargs = {
            "maintainOffset": bool(control.get("maintain_offset", True)),
            "name": plan["constraint_names"][identifier],
        }
        if constraint == "parent":
            constraint_node = cmds.parentConstraint(
                control_nodes[identifier], target, **kwargs
            )[0]
        elif constraint == "orient":
            constraint_node = cmds.orientConstraint(
                control_nodes[identifier], target, **kwargs
            )[0]
        else:
            constraint_node = cmds.pointConstraint(
                control_nodes[identifier], target, **kwargs
            )[0]
        constraint_node = _require_exact_created_name(
            constraint_node,
            plan["constraint_names"][identifier],
            f"control {identifier} {constraint} constraint",
        )
        constraint_nodes[identifier] = constraint_node

    joints = [
        {
            "id": str(joint["id"]),
            "parent_id": joint.get("parent_id"),
            "node": state.node_ref(joint_nodes[str(joint["id"])]),
        }
        for joint in ordered_joints
    ]
    controls = []
    for control in ordered_controls:
        identifier = str(control["id"])
        target = plan["targets"].get(identifier)
        if control.get("target_joint_id") is not None:
            target = joint_nodes[str(control["target_joint_id"])]
        controls.append(
            {
                "id": identifier,
                "parent_id": control.get("parent_id"),
                "target_joint_id": control.get("target_joint_id"),
                "target": state.node_ref(target) if target is not None else None,
                "control": state.node_ref(control_nodes[identifier]),
                "offset_group": state.node_ref(group_nodes[identifier]),
                "constraint": (
                    state.node_ref(constraint_nodes[identifier])
                    if identifier in constraint_nodes
                    else None
                ),
            }
        )
    output = {
        "joints": joints,
        "controls": controls,
        "counts": {"joints": len(joints), "controls": len(controls)},
    }
    call.changes.append(
        {
            "kind": "rig.preview_accepted",
            "preview": _preview_handle(record),
            "output": output,
        }
    )
    return output


def _replace_preview_record(
    record: dict[str, Any], spec: dict[str, Any]
) -> dict[str, Any]:
    replacement = _build_preview(
        record["preview_id"], int(record["revision"]) + 1, spec
    )
    try:
        _destroy_preview(record, strict=True)
    except Exception as error:
        cleanup_error: Exception | None = None
        try:
            replacement_cleaned = _destroy_preview(
                replacement, strict=False
            )
        except Exception as caught_cleanup_error:
            replacement_cleaned = False
            cleanup_error = caught_cleanup_error
        if not replacement_cleaned:
            retained = _retain_cleanup_record(
                replacement,
                status="update_cleanup_failed",
                cause=cleanup_error or error,
            )
            raise state.ToolError(
                "PREVIEW_UPDATE_ROLLBACK_FAILED",
                "The original preview was retained, but Maya could not "
                "fully remove its uncommitted replacement; surviving "
                "UUIDs remain tracked by a cleanup handle",
                {
                    "original_handle": _preview_handle(record),
                    "cleanup_handle": (
                        _preview_handle(retained)
                        if retained is not None
                        else None
                    ),
                    "replacement_nodes": (
                        copy.deepcopy(retained["owned_nodes"])
                        if retained is not None
                        else []
                    ),
                    "cause": {
                        "type": type(error).__name__,
                        "message": str(error),
                    },
                    "cleanup_error": (
                        {
                            "type": type(cleanup_error).__name__,
                            "message": str(cleanup_error),
                        }
                        if cleanup_error is not None
                        else None
                    ),
                },
            ) from error
        raise
    return replacement


def rig_preview(
    arguments: dict[str, Any], call: state.CallState
) -> dict[str, Any]:
    action = arguments["action"]
    if action == "list":
        previews = [
            _preview_view(record)
            for record in sorted(
                _PREVIEWS.values(), key=lambda item: item["preview_id"]
            )
            if record["scene_epoch"] == state.scene_epoch()
        ][:_MAX_ACTIVE_PREVIEWS]
        return state.result(
            call,
            {
                "previews": previews,
                "count": len(previews),
                "limits": {
                    "max_active_previews": _MAX_ACTIVE_PREVIEWS,
                    "max_owned_nodes": _MAX_TOTAL_OWNED_NODES,
                    "owned_nodes": _current_owned_count(),
                },
            },
            f"Listed {len(previews)} active rig preview(s)",
        )

    if action == "create":
        spec = _normalize_spec(arguments)
        _enforce_preview_capacity(spec)
        preview_id = f"rig-preview:{uuid.uuid4().hex}"
        with state.transient_scene_signature(_estimated_owned_nodes(spec)):
            record = _build_preview(preview_id, 1, spec)
        _PREVIEWS[preview_id] = record
        call.changes.append(
            {
                "kind": "rig.preview_created",
                "transient": True,
                "handle": _preview_handle(record),
            }
        )
        return state.result(
            call,
            _preview_view(record),
            f"Created rig preview {preview_id}",
        )

    record = _require_preview(arguments)
    if action == "query":
        return state.result(
            call,
            _preview_view(record),
            f"Read rig preview {record['preview_id']}",
        )
    if (
        action in {"update", "accept"}
        and record.get("status", "active") != "active"
    ):
        raise state.ToolError(
            "PREVIEW_NOT_ACTIVE",
            "This rig preview was already accepted; only query or transient "
            "cleanup is available",
            {
                "handle": _preview_handle(record),
                "status": record.get("status"),
            },
        )
    if action == "update":
        spec = _normalize_spec(arguments, record["spec"])
        _enforce_preview_capacity(spec, replacing=record)
        expected_delta = _estimated_owned_nodes(spec) - len(record["owned_nodes"])
        with state.transient_scene_signature(expected_delta):
            replacement = _replace_preview_record(record, spec)
        _PREVIEWS[record["preview_id"]] = replacement
        call.changes.append(
            {
                "kind": "rig.preview_updated",
                "transient": True,
                "previous_handle": _preview_handle(record),
                "handle": _preview_handle(replacement),
            }
        )
        return state.result(
            call,
            _preview_view(replacement),
            f"Updated rig preview {record['preview_id']} to revision {replacement['revision']}",
        )
    if action == "cancel":
        previous_status = record.get("status", "active")
        with state.transient_scene_signature(-len(record["owned_nodes"])):
            _destroy_preview(record, strict=True)
            _PREVIEWS.pop(record["preview_id"], None)
        handle = _preview_handle(record)
        result_status = (
            "cancelled"
            if previous_status == "active"
            else "cleanup_completed"
        )
        call.changes.append(
            {
                "kind": (
                    "rig.preview_cancelled"
                    if previous_status == "active"
                    else "rig.preview_cleanup_completed"
                ),
                "transient": True,
                "handle": handle,
            }
        )
        return state.result(
            call,
            {"handle": handle, "status": result_status},
            (
                f"Cancelled rig preview {record['preview_id']}"
                if previous_status == "active"
                else f"Cleaned accepted rig preview {record['preview_id']}"
            ),
        )
    if action == "accept":
        if not bool(cmds.undoInfo(query=True, state=True)):
            raise state.ToolError(
                "UNDO_DISABLED",
                "Accepting a rig preview requires Maya undo to be enabled",
                {"handle": _preview_handle(record)},
            )
        state.require_revision(arguments.get("if_scene_revision"))
        plan = _preflight_accept(record)
        previous_selection = cmds.ls(selection=True, long=True) or []
        with state.undo_chunk(call, "Accept rig preview"):
            try:
                with _root_namespace():
                    output = _accept_nodes(record, plan, call)
                # Validate cleanup once more before the permanent chunk commits.
                _preflight_destroy(record)
            finally:
                try:
                    if previous_selection:
                        cmds.select(previous_selection, replace=True)
                    else:
                        cmds.select(clear=True)
                except RuntimeError:
                    pass
        cleanup_failed = False
        try:
            _destroy_preview(record, strict=True)
        except Exception as error:
            # Permanent rig nodes have already committed as one undo chunk.
            # Never report the overall operation as failed after that boundary.
            cleanup_failed = True
            record["status"] = "accepted_cleanup_failed"
            record["accepted_output"] = output
            call.warnings.append(
                {
                    "code": "PREVIEW_CLEANUP_FAILED",
                    "message": (
                        "The rig was accepted, but its transient preview could "
                        "not be fully removed; call cancel with the same handle "
                        "to retry preview-only cleanup"
                    ),
                    "details": {
                        "type": type(error).__name__,
                        "message": str(error),
                        "handle": _preview_handle(record),
                    },
                }
            )
        else:
            _PREVIEWS.pop(record["preview_id"], None)
        state.bump_scene_revision()
        return state.result(
            call,
            {
                "handle": _preview_handle(record),
                "status": (
                    "accepted_preview_cleanup_failed"
                    if cleanup_failed
                    else "accepted"
                ),
                "output": output,
            },
            (
                f"Accepted rig preview {record['preview_id']} with a transient "
                "cleanup warning"
                if cleanup_failed
                else f"Accepted rig preview {record['preview_id']}"
            ),
        )
    raise state.ToolError(
        "INVALID_ARGUMENT", f"Unknown rig preview action: {action}"
    )


def _cleanup_previews(_: str) -> None:
    for preview_id, record in tuple(_PREVIEWS.items()):
        try:
            cleaned = _destroy_preview(record, strict=False)
        except Exception:
            cleaned = False
        if cleaned:
            _PREVIEWS.pop(preview_id, None)


state.register_lifecycle_cleanup(_cleanup_previews)


RIG_PREVIEW_HANDLERS: dict[
    str, Callable[[dict[str, Any], state.CallState], dict[str, Any]]
] = {"maya.rig.preview": rig_preview}
