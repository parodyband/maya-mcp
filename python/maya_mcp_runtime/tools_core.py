"""Core Maya context, graph, selection, and history tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import maya.cmds as cmds

from . import state


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
            attribute: state.safe_get_attr(f"{node}.{attribute}")
            for attribute in attributes
            if cmds.attributeQuery(attribute, node=node, exists=True)
        }
    if connection_mode != "none":
        incoming = connection_mode in ("incoming", "both")
        outgoing = connection_mode in ("outgoing", "both")
        details["connections"] = {
            "incoming": (
                cmds.listConnections(
                    node,
                    source=True,
                    destination=False,
                    plugs=True,
                    connections=True,
                )
                or []
                if incoming
                else []
            ),
            "outgoing": (
                cmds.listConnections(
                    node,
                    source=False,
                    destination=True,
                    plugs=True,
                    connections=True,
                )
                or []
                if outgoing
                else []
            ),
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
    else:
        raise state.ToolError("INVALID_ARGUMENT", f"Unsupported node operation: {op}")


def node_apply(arguments: dict[str, Any], call: state.CallState) -> dict[str, Any]:
    operations = arguments["operations"]
    state.require_revision(arguments.get("if_scene_revision"))
    validate_only = bool(arguments.get("validate_only", False))
    plan: list[dict[str, Any]] = []
    preflight_aliases: dict[str, Any] = {}
    alias_producing_ops = {
        "create", "duplicate", "rename", "parent", "set_transform"
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
                if attribute_type == "string":
                    cmds.addAttr(node, longName=attribute, dataType="string")
                else:
                    cmds.addAttr(node, longName=attribute, attributeType=attribute_type)
                state.mark_mutated(call)
                if "value" in operation:
                    _set_attribute(
                        f"{node}.{attribute}",
                        operation["value"],
                        attribute_type,
                    )
                output["plug"] = f"{node}.{attribute}"
                call.changes.append({"kind": "attribute.added", "plug": output["plug"]})
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
    items = [state.resolve_node(item) for item in arguments.get("items", [])]
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
    selection = state.selection_refs()
    return state.result(
        call,
        {"selection": selection, "mode": mode},
        f"Selection now contains {len(selection)} items",
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
