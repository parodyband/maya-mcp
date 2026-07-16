"""Geometry, lookdev, animation, rigging, file, and script tools."""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import traceback
from collections.abc import Callable
from typing import Any

import maya.cmds as cmds
import maya.mel as mel

from . import state


def _set_transform(node: str, arguments: dict[str, Any]) -> None:
    if "position" in arguments:
        cmds.xform(node, worldSpace=True, translation=arguments["position"])
    if "rotation" in arguments:
        cmds.xform(node, worldSpace=True, rotation=arguments["rotation"])
    if "scale" in arguments:
        cmds.xform(node, objectSpace=True, scale=arguments["scale"])


def geometry_apply(arguments: dict[str, Any], call: state.CallState) -> dict[str, Any]:
    kind = arguments["kind"]
    dimensions = arguments.get("dimensions", {})
    history = bool(arguments.get("construction_history", True))
    name = arguments.get("name")
    if kind == "curve" and not arguments.get("points"):
        raise state.ToolError("INVALID_ARGUMENT", "curve requires points")
    kwargs: dict[str, Any] = {"constructionHistory": history}
    if name:
        kwargs["name"] = name

    with state.undo_chunk(call, f"Create {kind}"):
        if kind == "cube":
            kwargs.update(
                width=dimensions.get("width", 1.0),
                height=dimensions.get("height", 1.0),
                depth=dimensions.get("depth", 1.0),
            )
            node = cmds.polyCube(**kwargs)[0]
        elif kind == "sphere":
            kwargs.update(
                radius=dimensions.get("radius", 1.0),
                subdivisionsX=int(dimensions.get("subdivisions_x", 20)),
                subdivisionsY=int(dimensions.get("subdivisions_y", 20)),
            )
            node = cmds.polySphere(**kwargs)[0]
        elif kind == "cylinder":
            kwargs.update(
                radius=dimensions.get("radius", 1.0),
                height=dimensions.get("height", 2.0),
                subdivisionsX=int(dimensions.get("subdivisions_x", 20)),
            )
            node = cmds.polyCylinder(**kwargs)[0]
        elif kind == "cone":
            kwargs.update(
                radius=dimensions.get("radius", 1.0),
                height=dimensions.get("height", 2.0),
                subdivisionsX=int(dimensions.get("subdivisions_x", 20)),
            )
            node = cmds.polyCone(**kwargs)[0]
        elif kind == "plane":
            kwargs.update(
                width=dimensions.get("width", 1.0),
                height=dimensions.get("height", 1.0),
                subdivisionsX=int(dimensions.get("subdivisions_x", 1)),
                subdivisionsY=int(dimensions.get("subdivisions_y", 1)),
            )
            node = cmds.polyPlane(**kwargs)[0]
        elif kind == "torus":
            kwargs.update(
                radius=dimensions.get("radius", 1.0),
                sectionRadius=dimensions.get("section_radius", 0.25),
                subdivisionsX=int(dimensions.get("subdivisions_x", 20)),
                subdivisionsY=int(dimensions.get("subdivisions_y", 12)),
            )
            node = cmds.polyTorus(**kwargs)[0]
        elif kind == "curve":
            points = arguments.get("points")
            curve_kwargs: dict[str, Any] = {
                "point": points,
                "degree": int(arguments.get("degree", 1)),
            }
            if name:
                curve_kwargs["name"] = name
            node = cmds.curve(**curve_kwargs)
        else:
            raise state.ToolError("INVALID_ARGUMENT", f"Unknown geometry kind: {kind}")
        state.mark_mutated(call)
        _set_transform(node, arguments)
        node_data = state.node_ref(node)
        shape_data = [
            state.node_ref(shape)
            for shape in (cmds.listRelatives(node, shapes=True, fullPath=True) or [])
        ]
        call.changes.append({"kind": "geometry.created", "target": node_data})
    state.bump_scene_revision()
    return state.result(
        call,
        {"transform": node_data, "shapes": shape_data, "kind": kind},
        f"Created {kind} geometry {node_data['name']}",
    )


def _material_info(material: str) -> dict[str, Any]:
    material = state.resolve_node(material)
    shading_groups = cmds.listConnections(
        material, source=False, destination=True, type="shadingEngine"
    ) or []
    return {
        "material": state.node_ref(material),
        "shading_groups": [state.node_ref(group) for group in shading_groups],
        "assignments": list(
            dict.fromkeys(
                item
                for group in shading_groups
                for item in (cmds.sets(group, query=True) or [])
            )
        ),
    }


def material_apply(arguments: dict[str, Any], call: state.CallState) -> dict[str, Any]:
    action = arguments["action"]
    if action == "inspect":
        if "material" not in arguments:
            raise state.ToolError("INVALID_ARGUMENT", "inspect requires material")
        data = _material_info(state.resolve_node(arguments["material"]))
        return state.result(call, data, "Inspected Maya material assignments")

    targets = [state.resolve_node(item) for item in arguments.get("targets", [])]
    if not targets:
        raise state.ToolError("INVALID_ARGUMENT", f"{action} requires targets")
    with state.undo_chunk(call, "Apply Maya material"):
        if action == "create_assign":
            shader_type = arguments.get("shader_type", "standardSurface")
            name = arguments.get("name") or f"{shader_type}_MCP"
            material = cmds.shadingNode(shader_type, asShader=True, name=name)
            state.mark_mutated(call)
            shading_group = cmds.sets(
                renderable=True,
                noSurfaceShader=True,
                empty=True,
                name=f"{material}SG",
            )
            cmds.connectAttr(
                f"{material}.outColor",
                f"{shading_group}.surfaceShader",
                force=True,
            )
            color_attr = "baseColor" if cmds.attributeQuery(
                "baseColor", node=material, exists=True
            ) else "color"
            roughness_attr = "specularRoughness"
            if "base_color" in arguments:
                cmds.setAttr(
                    f"{material}.{color_attr}",
                    *arguments["base_color"],
                    type="double3",
                )
            if "metalness" in arguments and cmds.attributeQuery(
                "metalness", node=material, exists=True
            ):
                cmds.setAttr(f"{material}.metalness", arguments["metalness"])
            if "roughness" in arguments and cmds.attributeQuery(
                roughness_attr, node=material, exists=True
            ):
                cmds.setAttr(
                    f"{material}.{roughness_attr}", arguments["roughness"]
                )
        elif action == "assign":
            if "material" not in arguments:
                raise state.ToolError("INVALID_ARGUMENT", "assign requires material")
            material = state.resolve_node(arguments["material"])
            groups = cmds.listConnections(
                material, destination=True, type="shadingEngine"
            ) or []
            if groups:
                shading_group = groups[0]
            else:
                shading_group = cmds.sets(
                    renderable=True,
                    noSurfaceShader=True,
                    empty=True,
                    name=f"{material}SG",
                )
                state.mark_mutated(call)
                cmds.connectAttr(
                    f"{material}.outColor",
                    f"{shading_group}.surfaceShader",
                    force=True,
                )
        else:
            raise state.ToolError("INVALID_ARGUMENT", f"Unknown material action: {action}")
        cmds.sets(targets, edit=True, forceElement=shading_group)
        state.mark_mutated(call)
        info = _material_info(material)
        call.changes.append(
            {
                "kind": "material.assigned",
                "material": info["material"],
                "targets": [state.node_ref(target) for target in targets],
            }
        )
    state.bump_scene_revision()
    return state.result(call, info, f"Assigned {material} to {len(targets)} target(s)")


def animation_apply(arguments: dict[str, Any], call: state.CallState) -> dict[str, Any]:
    action = arguments["action"]
    targets = [state.resolve_node(item) for item in arguments["targets"]]
    attributes = arguments.get("attributes", [])
    plugs = [
        f"{target}.{attribute}"
        for target in targets
        for attribute in attributes
        if cmds.objExists(f"{target}.{attribute}")
    ]
    if action == "inspect":
        data = []
        for plug in plugs:
            times = cmds.keyframe(plug, query=True, timeChange=True) or []
            values = cmds.keyframe(plug, query=True, valueChange=True) or []
            data.append(
                {
                    "plug": plug,
                    "keys": [
                        {"time": time_value, "value": value}
                        for time_value, value in zip(times, values)
                    ],
                }
            )
        return state.result(call, {"curves": data}, f"Inspected {len(plugs)} animation plugs")

    if not plugs:
        raise state.ToolError(
            "TARGET_NOT_FOUND",
            "No valid target attributes were supplied for animation",
        )
    if action == "set_keys" and not arguments.get("keys"):
        raise state.ToolError("INVALID_ARGUMENT", "set_keys requires keys")
    with state.undo_chunk(call, f"Maya MCP animation {action}"):
        if action == "set_keys":
            keys = arguments.get("keys", [])
            for plug in plugs:
                for key in keys:
                    kwargs: dict[str, Any] = {"time": key["time"]}
                    if "value" in key:
                        kwargs["value"] = key["value"]
                    cmds.setKeyframe(plug, **kwargs)
                    state.mark_mutated(call)
                    if key.get("in_tangent") or key.get("out_tangent"):
                        cmds.keyTangent(
                            plug,
                            edit=True,
                            time=(key["time"], key["time"]),
                            inTangentType=key.get("in_tangent", "auto"),
                            outTangentType=key.get("out_tangent", "auto"),
                        )
        elif action == "delete_keys":
            time_range = arguments.get("time_range")
            kwargs = {"time": tuple(time_range)} if time_range else {}
            cmds.cutKey(plugs, clear=True, **kwargs)
            state.mark_mutated(call)
        else:
            raise state.ToolError("INVALID_ARGUMENT", f"Unknown animation action: {action}")
        call.changes.append({"kind": f"animation.{action}", "plugs": plugs})
    state.bump_scene_revision()
    return state.result(
        call,
        {"action": action, "plugs": plugs, "key_count": len(arguments.get("keys", []))},
        f"Applied {action} to {len(plugs)} animation plugs",
    )


def _joint_record(joint: str) -> dict[str, Any]:
    record = state.node_ref(joint)
    record["parent"] = (
        state.node_ref(parent)
        if (parent := (cmds.listRelatives(joint, parent=True, fullPath=True) or [None])[0])
        else None
    )
    record["world_position"] = cmds.xform(
        joint, query=True, worldSpace=True, translation=True
    )
    record["joint_orient_degrees"] = cmds.getAttr(f"{joint}.jointOrient")[0]
    record["rotate_order"] = cmds.getAttr(f"{joint}.rotateOrder")
    return record


def rig_skeleton(arguments: dict[str, Any], call: state.CallState) -> dict[str, Any]:
    action = arguments["action"]
    if action == "inspect":
        if "root" not in arguments:
            raise state.ToolError("INVALID_ARGUMENT", "inspect requires root")
        root = state.resolve_node(arguments["root"])
        if cmds.nodeType(root) != "joint":
            raise state.ToolError("INVALID_TARGET", f"Root is not a joint: {root}")
        descendants = cmds.listRelatives(
            root, allDescendents=True, fullPath=True, type="joint"
        ) or []
        joints = [root, *reversed(descendants)]
        return state.result(
            call,
            {"root": state.node_ref(root), "joints": [_joint_record(joint) for joint in joints]},
            f"Inspected a {len(joints)}-joint skeleton",
        )

    if action != "create_chain":
        raise state.ToolError("INVALID_ARGUMENT", f"Unknown skeleton action: {action}")
    definitions = arguments.get("joints", [])
    if not definitions:
        raise state.ToolError("INVALID_ARGUMENT", "create_chain requires joints")
    previous_selection = cmds.ls(selection=True, long=True) or []
    created: list[str] = []
    with state.undo_chunk(call, "Create joint chain"):
        try:
            cmds.select(clear=True)
            for definition in definitions:
                kwargs: dict[str, Any] = {
                    "position": definition["position"],
                    "absolute": True,
                }
                if definition.get("name"):
                    kwargs["name"] = definition["name"]
                if definition.get("radius"):
                    kwargs["radius"] = definition["radius"]
                created.append(cmds.joint(**kwargs))
                state.mark_mutated(call)
            if arguments.get("orient", True) and len(created) > 1:
                cmds.joint(
                    created[0],
                    edit=True,
                    orientJoint=arguments.get("primary_axis", "xyz"),
                    secondaryAxisOrient=arguments.get("secondary_axis", "yup"),
                    children=True,
                    zeroScaleOrient=True,
                )
                cmds.setAttr(f"{created[-1]}.jointOrient", 0.0, 0.0, 0.0)
            if arguments.get("parent"):
                created[0] = (
                    cmds.parent(created[0], state.resolve_node(arguments["parent"])) or [created[0]]
                )[0]
        finally:
            if previous_selection:
                cmds.select(previous_selection, replace=True)
            else:
                cmds.select(clear=True)
        records = [_joint_record(joint) for joint in created]
        call.changes.append({"kind": "rig.joint_chain_created", "joints": records})
    state.bump_scene_revision()
    return state.result(
        call,
        {"root": records[0], "joints": records},
        f"Created a {len(records)}-joint chain",
    )


def _control_curve(shape: str, name: str, size: float) -> str:
    if shape == "circle":
        return cmds.circle(
            name=name, normal=(1, 0, 0), radius=size, constructionHistory=False
        )[0]
    if shape == "square":
        points = [
            (0, -size, -size),
            (0, -size, size),
            (0, size, size),
            (0, size, -size),
            (0, -size, -size),
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


def rig_controls(arguments: dict[str, Any], call: state.CallState) -> dict[str, Any]:
    action = arguments["action"]
    targets = [state.resolve_node(item) for item in arguments.get("targets", [])]
    if action == "inspect":
        records = []
        for target in targets:
            shapes = cmds.listRelatives(target, shapes=True, fullPath=True, type="nurbsCurve") or []
            if shapes:
                records.append(
                    {"control": state.node_ref(target), "shapes": [state.node_ref(item) for item in shapes]}
                )
        return state.result(call, {"controls": records}, f"Inspected {len(records)} controls")
    if action != "create" or not targets:
        raise state.ToolError("INVALID_ARGUMENT", "create controls requires targets")

    created: list[dict[str, Any]] = []
    pending: dict[str, dict[str, str | None]] = {}
    with state.undo_chunk(call, "Create rig controls"):
        for target in targets:
            base = target.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
            for suffix in ("_JNT", "_jnt", "_BIND", "_bind"):
                if base.endswith(suffix):
                    base = base[: -len(suffix)]
            control = _control_curve(
                arguments.get("shape", "circle"),
                f"{base}{arguments.get('suffix', '_CTRL')}",
                float(arguments.get("size", 1.0)),
            )
            state.mark_mutated(call)
            group = cmds.group(control, name=f"{control}_ZERO")
            matrix = cmds.xform(target, query=True, worldSpace=True, matrix=True)
            cmds.xform(group, worldSpace=True, matrix=matrix)
            if arguments.get("color") is not None:
                for shape in cmds.listRelatives(control, shapes=True, fullPath=True) or []:
                    cmds.setAttr(f"{shape}.overrideEnabled", 1)
                    cmds.setAttr(f"{shape}.overrideColor", int(arguments["color"]))
            constraint = arguments.get("constraint", "none")
            constraint_node = None
            constraint_args = {
                "maintainOffset": bool(arguments.get("maintain_offset", True))
            }
            if constraint == "parent":
                constraint_node = cmds.parentConstraint(control, target, **constraint_args)[0]
            elif constraint == "orient":
                constraint_node = cmds.orientConstraint(control, target, **constraint_args)[0]
            elif constraint == "point":
                constraint_node = cmds.pointConstraint(control, target, **constraint_args)[0]
            pending[target] = {
                "control": control,
                "group": group,
                "constraint": constraint_node,
            }

        if arguments.get("parent_hierarchy"):
            for target, nodes in pending.items():
                target_parent = (
                    cmds.listRelatives(target, parent=True, fullPath=True) or [None]
                )[0]
                if target_parent in pending:
                    cmds.parent(
                        nodes["group"],
                        pending[target_parent]["control"],
                    )

        for target, nodes in pending.items():
            record = {
                "target": state.node_ref(target),
                "control": state.node_ref(str(nodes["control"])),
                "offset_group": state.node_ref(str(nodes["group"])),
                "constraint": (
                    state.node_ref(str(nodes["constraint"]))
                    if nodes["constraint"]
                    else None
                ),
            }
            created.append(record)
        call.changes.append({"kind": "rig.controls_created", "controls": created})
    state.bump_scene_revision()
    return state.result(call, {"controls": created}, f"Created {len(created)} rig controls")


def _skin_info(geometry: str) -> dict[str, Any]:
    history = cmds.listHistory(geometry, pruneDagObjects=True) or []
    clusters = [item for item in history if cmds.nodeType(item) == "skinCluster"]
    mesh_shapes = (
        [geometry]
        if cmds.nodeType(geometry) == "mesh"
        else (
            cmds.listRelatives(
                geometry, shapes=True, fullPath=True, type="mesh"
            )
            or []
        )
    )
    return {
        "geometry": state.node_ref(geometry),
        "vertex_count": sum(
            int(cmds.polyEvaluate(shape, vertex=True) or 0)
            for shape in mesh_shapes
        ),
        "skin_clusters": [
            {
                "cluster": state.node_ref(cluster),
                "influences": [
                    state.node_ref(influence)
                    for influence in (cmds.skinCluster(cluster, query=True, influence=True) or [])
                ],
                "weighted_influences": [
                    state.node_ref(influence)
                    for influence in (cmds.skinCluster(cluster, query=True, weightedInfluence=True) or [])
                ],
            }
            for cluster in clusters
        ],
    }


def rig_skin(arguments: dict[str, Any], call: state.CallState) -> dict[str, Any]:
    action = arguments["action"]
    geometry = [state.resolve_node(item) for item in arguments["geometry"]]
    if action == "inspect":
        data = [_skin_info(item) for item in geometry]
        return state.result(call, {"geometry": data}, f"Inspected skin on {len(data)} objects")
    influences = (
        [state.resolve_node(item) for item in arguments.get("influences", [])]
        if action == "bind"
        else []
    )
    if action == "bind" and not influences:
        raise state.ToolError("INVALID_ARGUMENT", "bind requires influences")

    with state.undo_chunk(call, f"Skin {action}"):
        if action == "bind":
            clusters = []
            for item in geometry:
                cluster = cmds.skinCluster(
                    influences,
                    item,
                    toSelectedBones=True,
                    maximumInfluences=int(arguments.get("max_influences", 4)),
                    dropoffRate=float(arguments.get("dropoff_rate", 4.0)),
                    normalizeWeights=1 if arguments.get("normalize", True) else 0,
                )[0]
                state.mark_mutated(call)
                clusters.append(state.node_ref(cluster))
        elif action == "unbind":
            clusters = []
            for item in geometry:
                info = _skin_info(item)
                for cluster_info in info["skin_clusters"]:
                    cluster = state.resolve_node(cluster_info["cluster"])
                    cmds.skinCluster(cluster, edit=True, unbind=True)
                    state.mark_mutated(call)
                    clusters.append(cluster_info["cluster"])
        else:
            raise state.ToolError("INVALID_ARGUMENT", f"Unknown skin action: {action}")
        call.changes.append({"kind": f"skin.{action}", "clusters": clusters})
    state.bump_scene_revision()
    return state.result(
        call,
        {"action": action, "clusters": clusters, "geometry": [state.node_ref(item) for item in geometry]},
        f"Applied skin {action} to {len(geometry)} object(s)",
    )


def file_apply(arguments: dict[str, Any], call: state.CallState) -> dict[str, Any]:
    action = arguments["action"]
    if action == "query":
        return state.result(
            call,
            {
                "path": cmds.file(query=True, sceneName=True) or "",
                "modified": bool(cmds.file(query=True, modified=True)),
                "type": cmds.file(query=True, type=True) or [],
                "references": cmds.file(query=True, reference=True) or [],
            },
            "Read Maya scene file state",
        )

    path = arguments.get("path")
    if action not in ("save",) and not path:
        raise state.ToolError("INVALID_ARGUMENT", f"{action} requires path")
    if action == "open" and cmds.file(query=True, modified=True):
        policy = arguments.get("dirty_policy", "error")
        if policy == "error":
            raise state.ToolError(
                "DIRTY_SCENE",
                "The current scene has unsaved changes; choose save or discard",
            )
        if policy == "save":
            current = cmds.file(query=True, sceneName=True)
            if not current:
                raise state.ToolError("DIRTY_SCENE", "Current scene has no path to save")
            cmds.file(save=True)

    if action == "save":
        if not cmds.file(query=True, sceneName=True):
            raise state.ToolError("INVALID_ARGUMENT", "save requires a named Maya scene")
        result_path = cmds.file(save=True, force=bool(arguments.get("force", False)))
    elif action == "save_as":
        cmds.file(rename=os.path.abspath(path))
        save_args: dict[str, Any] = {
            "save": True,
            "force": bool(arguments.get("force", False)),
        }
        if arguments.get("file_type"):
            save_args["type"] = arguments["file_type"]
        result_path = cmds.file(**save_args)
    elif action == "open":
        result_path = cmds.file(
            os.path.abspath(path),
            open=True,
            force=arguments.get("dirty_policy") == "discard" or bool(arguments.get("force", False)),
        )
    elif action == "import":
        import_args: dict[str, Any] = {"i": True, "returnNewNodes": True}
        if arguments.get("namespace"):
            import_args["namespace"] = arguments["namespace"]
        result_path = cmds.file(os.path.abspath(path), **import_args)
    elif action == "reference":
        reference_args: dict[str, Any] = {"reference": True}
        if arguments.get("namespace"):
            reference_args["namespace"] = arguments["namespace"]
        result_path = cmds.file(os.path.abspath(path), **reference_args)
    elif action == "export_selection":
        export_args: dict[str, Any] = {
            "exportSelected": True,
            "force": bool(arguments.get("force", False)),
        }
        if arguments.get("file_type"):
            export_args["type"] = arguments["file_type"]
        result_path = cmds.file(os.path.abspath(path), **export_args)
    else:
        raise state.ToolError("INVALID_ARGUMENT", f"Unknown file action: {action}")
    if action != "open":
        state.bump_scene_revision()
    call.changes.append({"kind": f"file.{action}", "path": state.json_safe(result_path)})
    return state.result(
        call,
        {"action": action, "result": state.json_safe(result_path)},
        f"Completed Maya file operation: {action}",
    )


def script_execute(arguments: dict[str, Any], call: state.CallState) -> dict[str, Any]:
    if os.getenv("MAYA_MCP_ALLOW_UNSAFE_CODE", "").lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        raise state.ToolError(
            "CAPABILITY_DISABLED",
            "Unsafe script execution is disabled. Set MAYA_MCP_ALLOW_UNSAFE_CODE=1 before starting Maya to enable it.",
        )
    language = arguments["language"]
    source = arguments["source"]
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    stdout = io.StringIO()
    stderr = io.StringIO()
    execution_result: Any = None

    def execute() -> None:
        nonlocal execution_result
        if language == "python":
            namespace: dict[str, Any] = {
                "__name__": "__maya_mcp__",
                "cmds": cmds,
                "arguments": {},
            }
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exec(compile(source, "<maya-mcp>", "exec"), namespace, namespace)
                if arguments.get("return_expression"):
                    execution_result = eval(
                        compile(
                            arguments["return_expression"],
                            "<maya-mcp-return>",
                            "eval",
                        ),
                        namespace,
                        namespace,
                    )
                elif "result" in namespace:
                    execution_result = namespace["result"]
        elif language == "mel":
            execution_result = mel.eval(source)
        else:
            raise state.ToolError("INVALID_ARGUMENT", f"Unknown script language: {language}")

    try:
        if arguments.get("undo", "none") == "chunk":
            with state.undo_chunk(
                call,
                arguments.get("label") or "Maya MCP script",
                rollback_on_error=False,
            ):
                execute()
                state.mark_mutated(call)
        else:
            execute()
    except Exception as error:
        raise state.ToolError(
            "SCRIPT_ERROR",
            str(error),
            {
                "language": language,
                "sha256": digest,
                "traceback": traceback.format_exc()[-32768:],
                "stdout": stdout.getvalue()[-262144:],
                "stderr": stderr.getvalue()[-262144:],
            },
        ) from error
    state.bump_scene_revision()
    call.changes.append(
        {"kind": "script.executed", "language": language, "sha256": digest}
    )
    return state.result(
        call,
        {
            "language": language,
            "sha256": digest,
            "result": state.json_safe(execution_result),
            "stdout": stdout.getvalue()[-262144:],
            "stderr": stderr.getvalue()[-262144:],
            "output_truncated": len(stdout.getvalue()) > 262144 or len(stderr.getvalue()) > 262144,
        },
        f"Executed {language} script {digest[:12]}",
    )


DOMAIN_HANDLERS: dict[str, Callable[[dict[str, Any], state.CallState], dict[str, Any]]] = {
    "maya.geometry.apply": geometry_apply,
    "maya.material.apply": material_apply,
    "maya.animation.apply": animation_apply,
    "maya.rig.skeleton": rig_skeleton,
    "maya.rig.controls": rig_controls,
    "maya.rig.skin": rig_skin,
    "maya.file.apply": file_apply,
    "maya.script.execute": script_execute,
}
