"""Core Maya context, graph, selection, and history tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import maya.cmds as cmds

from . import state

_CONNECTION_ITEM_LIMIT = 500
_ATTRIBUTE_SEQUENCE_LIMIT = 256
_ATTRIBUTE_STRING_LIMIT = 8192


def _bounded_attribute_value(value: Any, depth: int = 0) -> Any:
    if depth >= 6:
        return {"truncated": True, "reason": "maximum nesting depth"}
    if isinstance(value, str):
        if len(value) <= _ATTRIBUTE_STRING_LIMIT:
            return value
        return {
            "value_prefix": value[:_ATTRIBUTE_STRING_LIMIT],
            "length": len(value),
            "truncated": True,
        }
    if isinstance(value, (list, tuple)):
        bounded = [
            _bounded_attribute_value(item, depth + 1)
            for item in value[:_ATTRIBUTE_SEQUENCE_LIMIT]
        ]
        if len(value) <= _ATTRIBUTE_SEQUENCE_LIMIT:
            return bounded
        return {
            "items": bounded,
            "total_items": len(value),
            "truncated": True,
        }
    if isinstance(value, dict):
        items = list(value.items())
        bounded = {
            str(key): _bounded_attribute_value(item, depth + 1)
            for key, item in items[:_ATTRIBUTE_SEQUENCE_LIMIT]
        }
        if len(items) <= _ATTRIBUTE_SEQUENCE_LIMIT:
            return bounded
        return {
            "entries": bounded,
            "total_entries": len(items),
            "truncated": True,
        }
    return value


def _connections(
    node: str, *, source: bool, destination: bool
) -> tuple[list[str], int]:
    values = cmds.listConnections(
        node,
        source=source,
        destination=destination,
        plugs=True,
        connections=True,
    ) or []
    return values[:_CONNECTION_ITEM_LIMIT], len(values)


def context_get(arguments: dict[str, Any], call: state.CallState) -> dict[str, Any]:
    del arguments
    return state.result(call, state.maya_context(), "Read the current Maya context")


def _node_details(
    node: str,
    attributes: list[str],
    connection_mode: str,
) -> dict[str, Any]:
    details = state.node_ref(node)
    if attributes:
        details["attributes"] = {
            attribute: _bounded_attribute_value(
                state.safe_get_attr(f"{node}.{attribute}")
            )
            for attribute in attributes
            if cmds.attributeQuery(attribute, node=node, exists=True)
        }
    if connection_mode != "none":
        incoming = connection_mode in ("incoming", "both")
        outgoing = connection_mode in ("outgoing", "both")
        incoming_values, incoming_total = (
            _connections(node, source=True, destination=False)
            if incoming
            else ([], 0)
        )
        outgoing_values, outgoing_total = (
            _connections(node, source=False, destination=True)
            if outgoing
            else ([], 0)
        )
        details["connections"] = {
            "incoming": incoming_values,
            "incoming_total": incoming_total,
            "incoming_truncated": incoming_total > _CONNECTION_ITEM_LIMIT,
            "outgoing": outgoing_values,
            "outgoing_total": outgoing_total,
            "outgoing_truncated": outgoing_total > _CONNECTION_ITEM_LIMIT,
            "limit_per_direction": _CONNECTION_ITEM_LIMIT,
        }
    return details


def scene_query(arguments: dict[str, Any], call: state.CallState) -> dict[str, Any]:
    scope = arguments.get("scope", "scene")
    if scope == "selection":
        nodes = cmds.ls(selection=True, long=True, objectsOnly=True) or []
    elif scope == "nodes":
        nodes = [state.resolve_node(item) for item in arguments.get("nodes", [])]
    elif scope == "subtree":
        if "root" not in arguments:
            raise state.ToolError("INVALID_ARGUMENT", "subtree scope requires root")
        root = state.resolve_node(arguments["root"])
        descendants = cmds.listRelatives(root, allDescendents=True, fullPath=True) or []
        nodes = [root, *reversed(descendants)]
    elif scope == "scene":
        nodes = cmds.ls(long=True, dependencyNodes=True) or []
    else:
        raise state.ToolError("INVALID_ARGUMENT", f"Unknown query scope: {scope}")

    nodes = list(dict.fromkeys(nodes))
    node_types = set(arguments.get("node_types", []))
    include_shapes = bool(arguments.get("include_shapes", True))
    pattern = arguments.get("name_glob")
    filtered: list[str] = []
    for node in nodes:
        if not cmds.objExists(node):
            continue
        node_type = cmds.nodeType(node)
        if node_types and node_type not in node_types:
            continue
        if not include_shapes and cmds.objectType(node, isAType="shape"):
            continue
        if not state.matches_name(node, pattern):
            continue
        filtered.append(node)

    limit = int(arguments.get("limit", 200))
    attributes = list(arguments.get("include_attributes", []))
    connection_mode = arguments.get("include_connections", "none")
    records = [
        _node_details(node, attributes, connection_mode)
        for node in filtered[:limit]
    ]
    data = {
        "scope": scope,
        "nodes": records,
        "count": len(records),
        "total_matches": len(filtered),
        "truncated": len(filtered) > limit,
        "limit": limit,
    }
    return state.result(call, data, f"Found {len(filtered)} matching Maya nodes")


def _resolve_alias(selector: Any, aliases: dict[str, Any]) -> str:
    if isinstance(selector, str) and selector.startswith("$"):
        alias = selector[1:]
        if alias not in aliases:
            raise state.ToolError(
                "INVALID_STEP_REFERENCE",
                f"Unknown earlier operation id: {selector}",
            )
        target = aliases[alias]
        if isinstance(target, str) and target.startswith("__planned__"):
            return target
        return state.resolve_node(target)
    return state.resolve_node(selector)


def _resolve_plug(value: str, aliases: dict[str, Any]) -> str:
    if value.startswith("$"):
        alias_and_attr = value[1:].split(".", 1)
        if alias_and_attr[0] not in aliases or len(alias_and_attr) != 2:
            raise state.ToolError(
                "INVALID_STEP_REFERENCE",
                f"Invalid plug step reference: {value}",
            )
        node = _resolve_alias("$" + alias_and_attr[0], aliases)
        return f"{node}.{alias_and_attr[1]}"
    if "." not in value:
        raise state.ToolError("INVALID_PLUG", f"Expected node.attribute: {value}")
    node, attribute = value.split(".", 1)
    return f"{state.resolve_node(node)}.{attribute}"


def _set_attribute(plug: str, value: Any, attribute_type: str | None) -> None:
    if attribute_type == "string" or isinstance(value, str):
        cmds.setAttr(plug, value, type="string")
    elif attribute_type == "matrix":
        cmds.setAttr(plug, *value, type="matrix")
    elif isinstance(value, (list, tuple)):
        if len(value) == 1 and isinstance(value[0], (list, tuple)):
            value = value[0]
        cmds.setAttr(plug, *value)
    else:
        cmds.setAttr(plug, value)


def _control_points(shape: str, size: float) -> list[tuple[float, float, float]]:
    if shape == "square":
        return [
            (0, -size, -size),
            (0, -size, size),
            (0, size, size),
            (0, size, -size),
            (0, -size, -size),
        ]
    if shape == "diamond":
        return [
            (0, 0, size),
            (0, size, 0),
            (0, 0, -size),
            (0, -size, 0),
            (0, 0, size),
        ]
    if shape == "arrow":
        return [
            (0, 0, size),
            (0, size * 0.55, size * 0.2),
            (0, size * 0.25, size * 0.2),
            (0, size * 0.25, -size),
            (0, -size * 0.25, -size),
            (0, -size * 0.25, size * 0.2),
            (0, -size * 0.55, size * 0.2),
            (0, 0, size),
        ]
    return [
        (-size, -size, -size),
        (-size, -size, size),
        (-size, size, size),
        (-size, size, -size),
        (-size, -size, -size),
        (size, -size, -size),
        (size, -size, size),
        (-size, -size, size),
        (-size, size, size),
        (size, size, size),
        (size, -size, size),
        (size, -size, -size),
        (size, size, -size),
        (-size, size, -size),
        (size, size, -size),
        (size, size, size),
    ]


def _create_control(operation: dict[str, Any], aliases: dict[str, Any]) -> str:
    name = str(operation["name"])
    shape = operation.get("shape", "circle")
    size = float(operation.get("size", 1.0))
    if shape == "circle":
        node = cmds.circle(
            name=name,
            normal=operation.get("normal", [1.0, 0.0, 0.0]),
            radius=size,
            constructionHistory=False,
        )[0]
    else:
        if shape == "custom":
            points = [
                tuple(float(component) * size for component in point)
                for point in operation["points"]
            ]
        else:
            points = _control_points(shape, size)
        degree = int(operation.get("degree", 1))
        node = cmds.curve(name=name, degree=degree, point=points)
        if operation.get("closed", False) and points[0] != points[-1]:
            node = cmds.closeCurve(
                node,
                replaceOriginal=True,
                preserveShape=0,
                constructionHistory=False,
            )[0]

    if operation.get("parent"):
        node = (
            cmds.parent(node, _resolve_alias(operation["parent"], aliases))
            or [node]
        )[0]
    world = operation.get("space", "world") == "world"
    if "matrix" in operation:
        cmds.xform(node, matrix=operation["matrix"], worldSpace=world)
    if "translate" in operation:
        cmds.xform(node, translation=operation["translate"], worldSpace=world)
    if "rotate" in operation:
        cmds.xform(node, rotation=operation["rotate"], worldSpace=world)
    if "scale" in operation:
        cmds.xform(node, scale=operation["scale"], worldSpace=world)

    for curve_shape in cmds.listRelatives(
        node, shapes=True, fullPath=True, type="nurbsCurve"
    ) or []:
        if "color_rgb" in operation:
            cmds.setAttr(f"{curve_shape}.overrideEnabled", 1)
            cmds.setAttr(f"{curve_shape}.overrideRGBColors", 1)
            cmds.setAttr(
                f"{curve_shape}.overrideColorRGB", *operation["color_rgb"]
            )
        elif "color" in operation:
            cmds.setAttr(f"{curve_shape}.overrideEnabled", 1)
            cmds.setAttr(f"{curve_shape}.overrideRGBColors", 0)
            cmds.setAttr(f"{curve_shape}.overrideColor", int(operation["color"]))
        if "line_width" in operation and cmds.attributeQuery(
            "lineWidth", node=curve_shape, exists=True
        ):
            cmds.setAttr(f"{curve_shape}.lineWidth", operation["line_width"])
    return node


def _validate_operation(operation: dict[str, Any], aliases: dict[str, Any]) -> None:
    op = operation["op"]
    if op == "create":
        if not operation.get("node_type"):
            raise state.ToolError("INVALID_ARGUMENT", "create requires node_type")
        if operation.get("parent"):
            _resolve_alias(operation["parent"], aliases)
    elif op in {
        "duplicate",
        "rename",
        "delete",
        "parent",
        "set_transform",
        "set_attribute",
        "add_attribute",
    }:
        if "node" not in operation:
            raise state.ToolError("INVALID_ARGUMENT", f"{op} requires node")
        _resolve_alias(operation["node"], aliases)
        if op == "rename" and not operation.get("name"):
            raise state.ToolError("INVALID_ARGUMENT", "rename requires name")
        if op == "set_transform" and not any(
            key in operation for key in ("matrix", "translate", "rotate", "scale")
        ):
            raise state.ToolError(
                "INVALID_ARGUMENT", "set_transform requires a transform value"
            )
        if op == "set_attribute" and (
            not operation.get("attribute") or "value" not in operation
        ):
            raise state.ToolError(
                "INVALID_ARGUMENT",
                "set_attribute requires attribute and value",
            )
        if op == "add_attribute" and not operation.get("attribute"):
            raise state.ToolError(
                "INVALID_ARGUMENT", "add_attribute requires attribute"
            )
        if op == "parent" and operation.get("parent"):
            _resolve_alias(operation["parent"], aliases)
    elif op in {"connect", "disconnect"}:
        _resolve_plug(operation.get("source", ""), aliases)
        _resolve_plug(operation.get("destination", ""), aliases)
    elif op == "create_control":
        if not operation.get("name"):
            raise state.ToolError(
                "INVALID_ARGUMENT", "create_control requires name"
            )
        if operation.get("shape", "circle") == "custom" and not operation.get(
            "points"
        ):
            raise state.ToolError(
                "INVALID_ARGUMENT", "custom create_control requires points"
            )
        if operation.get("parent"):
            _resolve_alias(operation["parent"], aliases)
    elif op == "create_ik_handle":
        if "start_joint" not in operation or "end_joint" not in operation:
            raise state.ToolError(
                "INVALID_ARGUMENT",
                "create_ik_handle requires start_joint and end_joint",
            )
        _resolve_alias(operation["start_joint"], aliases)
        _resolve_alias(operation["end_joint"], aliases)
        if operation.get("curve"):
            _resolve_alias(operation["curve"], aliases)
        if operation.get("parent"):
            _resolve_alias(operation["parent"], aliases)
    elif op == "create_constraint":
        if not operation.get("constraint_type"):
            raise state.ToolError(
                "INVALID_ARGUMENT", "create_constraint requires constraint_type"
            )
        if not operation.get("drivers") or "driven" not in operation:
            raise state.ToolError(
                "INVALID_ARGUMENT",
                "create_constraint requires drivers and driven",
            )
        for driver in operation["drivers"]:
            _resolve_alias(driver, aliases)
        _resolve_alias(operation["driven"], aliases)
        if operation.get("constraint_type") == "pole_vector" and len(
            operation["drivers"]
        ) != 1:
            raise state.ToolError(
                "INVALID_ARGUMENT",
                "pole_vector constraints require exactly one driver",
            )
        if operation.get("world_up_object"):
            _resolve_alias(operation["world_up_object"], aliases)
    elif op == "set_driven_keys":
        if not operation.get("driver_plug") or not operation.get("driven_plug"):
            raise state.ToolError(
                "INVALID_ARGUMENT",
                "set_driven_keys requires driver_plug and driven_plug",
            )
        if not operation.get("driven_keys"):
            raise state.ToolError(
                "INVALID_ARGUMENT", "set_driven_keys requires driven_keys"
            )
        _resolve_plug(operation["driver_plug"], aliases)
        _resolve_plug(operation["driven_plug"], aliases)
    else:
        raise state.ToolError("INVALID_ARGUMENT", f"Unsupported node operation: {op}")


def node_apply(arguments: dict[str, Any], call: state.CallState) -> dict[str, Any]:
    operations = arguments["operations"]
    state.require_revision(arguments.get("if_scene_revision"))
    validate_only = bool(arguments.get("validate_only", False))
    plan: list[dict[str, Any]] = []
    preflight_aliases: dict[str, Any] = {}
    alias_producing_ops = {
        "create",
        "duplicate",
        "rename",
        "parent",
        "set_transform",
        "create_control",
        "create_ik_handle",
        "create_constraint",
    }
    for index, operation in enumerate(operations):
        _validate_operation(operation, preflight_aliases)
        if (
            operation["op"] in alias_producing_ops
            and operation.get("id")
        ):
            preflight_aliases[operation["id"]] = (
                "__planned__" + operation["id"]
            )
        plan.append(
            {
                "index": index,
                "id": operation.get("id"),
                "op": operation["op"],
                "validated": True,
            }
        )
    if validate_only:
        return state.result(
            call,
            {"valid": True, "plan": plan},
            f"Validated {len(plan)} Maya node operations without editing",
        )

    label = arguments.get("label") or "Maya MCP node operations"
    aliases: dict[str, Any] = {}
    outputs: list[dict[str, Any]] = []
    with state.undo_chunk(call, label):
        for index, operation in enumerate(operations):
            op = operation["op"]
            operation_id = operation.get("id")
            output: dict[str, Any] = {"index": index, "id": operation_id, "op": op}

            if op == "create":
                kwargs: dict[str, Any] = {}
                if operation.get("name"):
                    kwargs["name"] = operation["name"]
                if operation.get("parent"):
                    kwargs["parent"] = _resolve_alias(operation["parent"], aliases)
                node = cmds.createNode(operation["node_type"], **kwargs)
                state.mark_mutated(call)
                output["node"] = state.node_ref(node)
                call.changes.append({"kind": "node.created", "target": output["node"]})
            elif op == "duplicate":
                source = _resolve_alias(operation["node"], aliases)
                kwargs = {"returnRootsOnly": True}
                if operation.get("name"):
                    kwargs["name"] = operation["name"]
                node = (cmds.duplicate(source, **kwargs) or [None])[0]
                if not node:
                    raise state.ToolError("MAYA_ERROR", f"Could not duplicate {source}")
                state.mark_mutated(call)
                output["node"] = state.node_ref(node)
                call.changes.append({"kind": "node.duplicated", "target": output["node"]})
            elif op == "rename":
                source = _resolve_alias(operation["node"], aliases)
                if not operation.get("name"):
                    raise state.ToolError("INVALID_ARGUMENT", "rename requires name")
                node = cmds.rename(source, operation["name"])
                state.mark_mutated(call)
                output["node"] = state.node_ref(node)
                call.changes.append({"kind": "node.renamed", "target": output["node"]})
            elif op == "delete":
                source = _resolve_alias(operation["node"], aliases)
                previous = state.node_ref(source)
                cmds.delete(source)
                state.mark_mutated(call)
                output["deleted"] = previous
                call.changes.append({"kind": "node.deleted", "target": previous})
                node = ""
            elif op == "parent":
                source = _resolve_alias(operation["node"], aliases)
                if operation.get("parent"):
                    parent = _resolve_alias(operation["parent"], aliases)
                    node = (cmds.parent(source, parent) or [source])[0]
                else:
                    node = (cmds.parent(source, world=True) or [source])[0]
                state.mark_mutated(call)
                output["node"] = state.node_ref(node)
                call.changes.append({"kind": "node.parented", "target": output["node"]})
            elif op == "set_transform":
                node = _resolve_alias(operation["node"], aliases)
                world = operation.get("space", "world") == "world"
                if "matrix" in operation:
                    cmds.xform(node, matrix=operation["matrix"], worldSpace=world)
                    state.mark_mutated(call)
                if "translate" in operation:
                    cmds.xform(node, translation=operation["translate"], worldSpace=world)
                    state.mark_mutated(call)
                if "rotate" in operation:
                    cmds.xform(node, rotation=operation["rotate"], worldSpace=world)
                    state.mark_mutated(call)
                if "scale" in operation:
                    cmds.xform(node, scale=operation["scale"], worldSpace=world)
                    state.mark_mutated(call)
                output["node"] = state.node_ref(node)
                call.changes.append({"kind": "transform.changed", "target": output["node"]})
            elif op == "set_attribute":
                node = _resolve_alias(operation["node"], aliases)
                attribute = operation.get("attribute")
                if not attribute or "value" not in operation:
                    raise state.ToolError(
                        "INVALID_ARGUMENT",
                        "set_attribute requires attribute and value",
                    )
                plug = f"{node}.{attribute}"
                if not cmds.objExists(plug):
                    raise state.ToolError("TARGET_NOT_FOUND", f"Plug does not exist: {plug}")
                if cmds.getAttr(plug, lock=True) and not operation.get("force", False):
                    raise state.ToolError("PLUG_LOCKED", f"Plug is locked: {plug}")
                if operation.get("force", False):
                    cmds.setAttr(plug, lock=False)
                _set_attribute(plug, operation["value"], operation.get("attribute_type"))
                state.mark_mutated(call)
                output["plug"] = plug
                output["value"] = state.safe_get_attr(plug)
                call.changes.append({"kind": "attribute.changed", "plug": plug})
                node = ""
            elif op == "add_attribute":
                node = _resolve_alias(operation["node"], aliases)
                attribute = operation.get("attribute")
                attribute_type = operation.get("attribute_type", "double")
                if not attribute:
                    raise state.ToolError("INVALID_ARGUMENT", "add_attribute requires attribute")
                if cmds.attributeQuery(attribute, node=node, exists=True):
                    raise state.ToolError(
                        "CONNECTION_CONFLICT",
                        f"Attribute already exists: {node}.{attribute}",
                    )
                add_args: dict[str, Any] = {"longName": attribute}
                if operation.get("nice_name"):
                    add_args["niceName"] = operation["nice_name"]
                if attribute_type == "string":
                    add_args["dataType"] = "string"
                elif attribute_type == "enum" or operation.get("enum_names"):
                    add_args["attributeType"] = "enum"
                    add_args["enumName"] = ":".join(operation["enum_names"])
                else:
                    add_args["attributeType"] = attribute_type
                if "min_value" in operation:
                    add_args["minValue"] = operation["min_value"]
                if "max_value" in operation:
                    add_args["maxValue"] = operation["max_value"]
                if "default_value" in operation and attribute_type != "string":
                    add_args["defaultValue"] = operation["default_value"]
                if "keyable" in operation:
                    add_args["keyable"] = bool(operation["keyable"])
                cmds.addAttr(node, **add_args)
                state.mark_mutated(call)
                plug = f"{node}.{attribute}"
                if "value" in operation or (
                    "default_value" in operation and attribute_type == "string"
                ):
                    _set_attribute(
                        plug,
                        operation.get("value", operation.get("default_value")),
                        attribute_type,
                    )
                if "channel_box" in operation:
                    cmds.setAttr(
                        plug, channelBox=bool(operation["channel_box"])
                    )
                if "locked" in operation:
                    cmds.setAttr(plug, lock=bool(operation["locked"]))
                output["plug"] = plug
                output["value"] = state.safe_get_attr(plug)
                call.changes.append({"kind": "attribute.added", "plug": output["plug"]})
                node = ""
            elif op == "create_control":
                node = _create_control(operation, aliases)
                state.mark_mutated(call)
                output["node"] = state.node_ref(node)
                output["shapes"] = [
                    state.node_ref(curve_shape)
                    for curve_shape in (
                        cmds.listRelatives(
                            node,
                            shapes=True,
                            fullPath=True,
                            type="nurbsCurve",
                        )
                        or []
                    )
                ]
                call.changes.append(
                    {"kind": "rig.control_created", "target": output["node"]}
                )
            elif op == "create_ik_handle":
                start_joint = _resolve_alias(operation["start_joint"], aliases)
                end_joint = _resolve_alias(operation["end_joint"], aliases)
                for role, joint in (
                    ("start_joint", start_joint),
                    ("end_joint", end_joint),
                ):
                    if cmds.nodeType(joint) != "joint":
                        raise state.ToolError(
                            "INVALID_TARGET", f"{role} is not a joint: {joint}"
                        )
                solver = operation.get("solver", "ikRPsolver")
                ik_args: dict[str, Any] = {
                    "startJoint": start_joint,
                    "endEffector": end_joint,
                    "solver": solver,
                }
                if operation.get("name"):
                    ik_args["name"] = operation["name"]
                if solver == "ikSplineSolver":
                    if operation.get("curve"):
                        ik_args["curve"] = _resolve_alias(
                            operation["curve"], aliases
                        )
                        ik_args["createCurve"] = False
                    else:
                        ik_args["createCurve"] = bool(
                            operation.get("create_curve", True)
                        )
                ik_nodes = cmds.ikHandle(**ik_args) or []
                if len(ik_nodes) < 2:
                    raise state.ToolError(
                        "MAYA_ERROR", "Maya did not create an IK handle and effector"
                    )
                node = ik_nodes[0]
                if operation.get("parent"):
                    node = (
                        cmds.parent(
                            node, _resolve_alias(operation["parent"], aliases)
                        )
                        or [node]
                    )[0]
                state.mark_mutated(call)
                output["node"] = state.node_ref(node)
                output["effector"] = state.node_ref(ik_nodes[1])
                if len(ik_nodes) > 2 and cmds.objExists(ik_nodes[2]):
                    output["curve"] = state.node_ref(ik_nodes[2])
                output["solver"] = solver
                call.changes.append(
                    {"kind": "rig.ik_handle_created", "target": output["node"]}
                )
            elif op == "create_constraint":
                drivers = [
                    _resolve_alias(driver, aliases)
                    for driver in operation["drivers"]
                ]
                driven = _resolve_alias(operation["driven"], aliases)
                constraint_type = operation["constraint_type"]
                constraint_args: dict[str, Any] = {}
                if operation.get("name"):
                    constraint_args["name"] = operation["name"]
                if constraint_type != "pole_vector":
                    constraint_args["maintainOffset"] = bool(
                        operation.get("maintain_offset", True)
                    )
                if constraint_type == "parent":
                    if operation.get("skip_translate"):
                        constraint_args["skipTranslate"] = operation[
                            "skip_translate"
                        ]
                    if operation.get("skip_rotate"):
                        constraint_args["skipRotate"] = operation["skip_rotate"]
                    result_nodes = cmds.parentConstraint(
                        drivers, driven, **constraint_args
                    )
                elif constraint_type == "orient":
                    if operation.get("skip_rotate"):
                        constraint_args["skip"] = operation["skip_rotate"]
                    result_nodes = cmds.orientConstraint(
                        drivers, driven, **constraint_args
                    )
                elif constraint_type == "point":
                    if operation.get("skip_translate"):
                        constraint_args["skip"] = operation["skip_translate"]
                    result_nodes = cmds.pointConstraint(
                        drivers, driven, **constraint_args
                    )
                elif constraint_type == "scale":
                    if operation.get("skip_translate"):
                        constraint_args["skip"] = operation["skip_translate"]
                    result_nodes = cmds.scaleConstraint(
                        drivers, driven, **constraint_args
                    )
                elif constraint_type == "aim":
                    if operation.get("aim_vector"):
                        constraint_args["aimVector"] = operation["aim_vector"]
                    if operation.get("up_vector"):
                        constraint_args["upVector"] = operation["up_vector"]
                    if operation.get("world_up_type"):
                        constraint_args["worldUpType"] = operation[
                            "world_up_type"
                        ]
                    if operation.get("world_up_object"):
                        constraint_args["worldUpObject"] = _resolve_alias(
                            operation["world_up_object"], aliases
                        )
                    if operation.get("skip_rotate"):
                        constraint_args["skip"] = operation["skip_rotate"]
                    result_nodes = cmds.aimConstraint(
                        drivers, driven, **constraint_args
                    )
                else:
                    result_nodes = cmds.poleVectorConstraint(
                        drivers[0], driven, **constraint_args
                    )
                if not result_nodes:
                    raise state.ToolError(
                        "MAYA_ERROR", f"Maya did not create {constraint_type} constraint"
                    )
                node = result_nodes[0]
                state.mark_mutated(call)
                output["node"] = state.node_ref(node)
                output["drivers"] = [state.node_ref(item) for item in drivers]
                output["driven"] = state.node_ref(driven)
                output["constraint_type"] = constraint_type
                call.changes.append(
                    {"kind": "rig.constraint_created", "target": output["node"]}
                )
            elif op == "set_driven_keys":
                driver_plug = _resolve_plug(operation["driver_plug"], aliases)
                driven_plug = _resolve_plug(operation["driven_plug"], aliases)
                if not cmds.objExists(driver_plug) or not cmds.objExists(
                    driven_plug
                ):
                    raise state.ToolError(
                        "TARGET_NOT_FOUND",
                        f"Driven-key plug does not exist: {driver_plug} -> {driven_plug}",
                    )
                for key in operation["driven_keys"]:
                    key_args: dict[str, Any] = {
                        "currentDriver": driver_plug,
                        "driverValue": key["driver_value"],
                        "value": key["value"],
                    }
                    if key.get("in_tangent"):
                        key_args["inTangentType"] = key["in_tangent"]
                    if key.get("out_tangent"):
                        key_args["outTangentType"] = key["out_tangent"]
                    cmds.setDrivenKeyframe(driven_plug, **key_args)
                state.mark_mutated(call)
                output.update(
                    {
                        "driver_plug": driver_plug,
                        "driven_plug": driven_plug,
                        "key_count": len(operation["driven_keys"]),
                    }
                )
                call.changes.append(
                    {
                        "kind": "rig.driven_keys_created",
                        "driver": driver_plug,
                        "driven": driven_plug,
                    }
                )
                node = ""
            elif op in {"connect", "disconnect"}:
                source = _resolve_plug(operation.get("source", ""), aliases)
                destination = _resolve_plug(operation.get("destination", ""), aliases)
                if op == "connect":
                    cmds.connectAttr(
                        source,
                        destination,
                        force=bool(operation.get("force", False)),
                    )
                else:
                    cmds.disconnectAttr(source, destination)
                state.mark_mutated(call)
                output.update({"source": source, "destination": destination})
                call.changes.append(
                    {"kind": f"plugs.{op}ed", "source": source, "destination": destination}
                )
                node = ""
            else:
                raise state.ToolError("INVALID_ARGUMENT", f"Unsupported operation: {op}")

            if operation_id:
                if output.get("node"):
                    aliases[operation_id] = output["node"]
                elif node:
                    aliases[operation_id] = state.node_ref(node)
            outputs.append(output)

    state.bump_scene_revision()
    alias_results = {}
    for key, value in aliases.items():
        try:
            alias_results[key] = state.node_ref(
                _resolve_alias("$" + key, aliases)
            )
        except state.ToolError:
            alias_results[key] = {
                "deleted": True,
                "last_name": (
                    value.get("long_name")
                    if isinstance(value, dict)
                    else str(value)
                ),
            }
    return state.result(
        call,
        {"operations": outputs, "aliases": alias_results},
        f"Applied {len(outputs)} Maya node operations",
    )


def selection_set(arguments: dict[str, Any], call: state.CallState) -> dict[str, Any]:
    mode = arguments["mode"]
    items = [
        state.resolve_selection_item(item) for item in arguments.get("items", [])
    ]
    if mode == "clear":
        cmds.select(clear=True)
    else:
        flags: dict[str, bool] = {
            "replace": mode == "replace",
            "add": mode == "add",
            "deselect": mode == "remove",
            "toggle": mode == "toggle",
        }
        cmds.select(items, **flags)
    state.bump_context_revision()
    selection = state.selection_snapshot()
    return state.result(
        call,
        {"mode": mode, **selection},
        f"Selection now contains {selection['entry_count']} compact entries",
    )


def history_apply(arguments: dict[str, Any], call: state.CallState) -> dict[str, Any]:
    action = arguments["action"]
    steps = int(arguments.get("steps", 1))
    applied = 0
    for _ in range(steps):
        try:
            if action == "undo":
                cmds.undo()
            else:
                cmds.redo()
            applied += 1
        except RuntimeError:
            break
    if applied:
        state.bump_scene_revision()
    return state.result(
        call,
        {
            "action": action,
            "requested_steps": steps,
            "applied_steps": applied,
            "undo_name": cmds.undoInfo(query=True, undoName=True) or "",
            "redo_name": cmds.undoInfo(query=True, redoName=True) or "",
        },
        f"Applied {applied} {action} step(s)",
    )


CORE_HANDLERS: dict[str, Callable[[dict[str, Any], state.CallState], dict[str, Any]]] = {
    "maya.context.get": context_get,
    "maya.scene.query": scene_query,
    "maya.node.apply": node_apply,
    "maya.selection.set": selection_set,
    "maya.history.apply": history_apply,
}
