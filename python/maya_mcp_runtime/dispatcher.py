"""Base64 JSON bridge entry points called by the native plug-in."""

from __future__ import annotations

import base64
import json
import re
from collections import Counter
from typing import Any

import maya.cmds as cmds

from . import state
from .catalog import CATALOG
from .tools_core import CORE_HANDLERS
from .tools_domain import DOMAIN_HANDLERS
from .tools_rig_preview import RIG_PREVIEW_HANDLERS
from .tools_vision import VISION_HANDLERS
from .tools_viewport import VIEWPORT_HANDLERS

HANDLERS = {
    **CORE_HANDLERS,
    **DOMAIN_HANDLERS,
    **RIG_PREVIEW_HANDLERS,
    **VISION_HANDLERS,
    **VIEWPORT_HANDLERS,
}
TOOL_DEFINITIONS = {tool["name"]: tool for tool in CATALOG["tools"]}
_PUMP_INTERVAL_MS = 10
_pump_timer: Any | None = None


def _pump_native_queue() -> None:
    try:
        cmds.mayaMcpPump()
    except RuntimeError:
        # The timer is stopped during normal plug-in teardown. Avoid leaking a
        # Python exception if Maya is already shutting down the command layer.
        pass


def install_pump_timer() -> None:
    """Keep native MCP dispatch responsive while Maya is playing or rendering."""
    global _pump_timer
    remove_pump_timer()
    if cmds.about(batch=True):
        return
    try:
        from PySide6 import QtCore
    except ImportError:
        from PySide2 import QtCore

    timer = QtCore.QTimer()
    timer.setInterval(_PUMP_INTERVAL_MS)
    try:
        timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
    except AttributeError:
        timer.setTimerType(QtCore.Qt.PreciseTimer)
    timer.timeout.connect(_pump_native_queue)
    timer.start()
    _pump_timer = timer


def remove_pump_timer() -> None:
    global _pump_timer
    timer = _pump_timer
    _pump_timer = None
    if timer is None:
        return
    timer.stop()
    try:
        timer.timeout.disconnect(_pump_native_queue)
    except (RuntimeError, TypeError):
        pass


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _validate(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    if "oneOf" in schema:
        errors = []
        matches = 0
        for option in schema["oneOf"]:
            try:
                _validate(value, option, path)
                matches += 1
            except state.ToolError as error:
                errors.append(str(error))
        if matches != 1:
            raise state.ToolError(
                "INVALID_ARGUMENT",
                f"{path} must match exactly one allowed shape",
                {"errors": errors},
            )
        return
    if "const" in schema and value != schema["const"]:
        raise state.ToolError("INVALID_ARGUMENT", f"{path} must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise state.ToolError(
            "INVALID_ARGUMENT",
            f"{path} must be one of {schema['enum']!r}",
        )
    expected = schema.get("type")
    if expected and not _type_matches(value, expected):
        raise state.ToolError(
            "INVALID_ARGUMENT",
            f"{path} must be {expected}, got {type(value).__name__}",
        )
    if "anyOf" in schema:
        errors = []
        matches = 0
        for option in schema["anyOf"]:
            try:
                _validate(value, option, path)
                matches += 1
            except state.ToolError as error:
                errors.append(str(error))
        if matches == 0:
            raise state.ToolError(
                "INVALID_ARGUMENT",
                f"{path} must match at least one allowed shape",
                {"errors": errors},
            )
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        missing = [key for key in schema.get("required", []) if key not in value]
        if missing:
            raise state.ToolError(
                "INVALID_ARGUMENT",
                f"{path} is missing required fields",
                {"missing": missing},
            )
        additional = schema.get("additionalProperties", True)
        unknown = [key for key in value if key not in properties]
        if additional is False and unknown:
            raise state.ToolError(
                "INVALID_ARGUMENT",
                f"{path} contains unknown fields",
                {"unknown": unknown},
            )
        for key, item in value.items():
            if key in properties:
                _validate(item, properties[key], f"{path}.{key}")
            elif isinstance(additional, dict):
                _validate(item, additional, f"{path}.{key}")
        if len(value) < int(schema.get("minProperties", 0)):
            raise state.ToolError("INVALID_ARGUMENT", f"{path} has too few fields")
    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            raise state.ToolError("INVALID_ARGUMENT", f"{path} has too few items")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            raise state.ToolError("INVALID_ARGUMENT", f"{path} has too many items")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                _validate(item, schema["items"], f"{path}[{index}]")
    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            raise state.ToolError("INVALID_ARGUMENT", f"{path} is too short")
        if "maxLength" in schema and len(value) > int(schema["maxLength"]):
            raise state.ToolError("INVALID_ARGUMENT", f"{path} is too long")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            raise state.ToolError(
                "INVALID_ARGUMENT",
                f"{path} does not match the required pattern",
            )
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise state.ToolError("INVALID_ARGUMENT", f"{path} is below its minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise state.ToolError("INVALID_ARGUMENT", f"{path} is above its maximum")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise state.ToolError(
                "INVALID_ARGUMENT", f"{path} must exceed its minimum"
            )


def _decode(encoded: str) -> dict[str, Any]:
    try:
        raw = base64.b64decode(encoded, validate=True)
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise state.ToolError("INVALID_ARGUMENT", "Invalid native bridge payload") from error
    if not isinstance(payload, dict):
        raise state.ToolError("INVALID_ARGUMENT", "Bridge payload must be an object")
    return payload


def dispatch_base64(encoded: str) -> str:
    call = state.begin_call()
    try:
        payload = _decode(encoded)
        name = payload.get("name")
        arguments = payload.get("arguments", {})
        if name not in TOOL_DEFINITIONS or name not in HANDLERS:
            raise state.ToolError("TOOL_NOT_FOUND", f"Unknown Maya MCP tool: {name}")
        _validate(arguments, TOOL_DEFINITIONS[name]["inputSchema"])
        response = HANDLERS[name](arguments, call)
    except Exception as error:
        response = state.failure(call, error)
    return json.dumps(response, ensure_ascii=True, separators=(",", ":"))


def _scene_summary() -> dict[str, Any]:
    nodes = cmds.ls(dependencyNodes=True, long=True) or []
    types = Counter(cmds.nodeType(node) for node in nodes if cmds.objExists(node))
    assemblies = cmds.ls(assemblies=True, long=True) or []
    references = cmds.file(query=True, reference=True) or []
    return {
        "scene": state.maya_context()["scene"],
        "node_count": len(nodes),
        "node_types": dict(sorted(types.items(), key=lambda item: (-item[1], item[0]))),
        "top_level_dag": [state.node_ref(node) for node in assemblies[:200]],
        "top_level_truncated": len(assemblies) > 200,
        "references": references,
    }


def read_resource_base64(encoded: str) -> str:
    payload = _decode(encoded)
    uri = payload.get("uri")
    if uri == "maya://context":
        data = state.maya_context()
    elif uri == "maya://scene/summary":
        data = _scene_summary()
    elif uri == "maya://selection":
        selection = state.selection_snapshot()
        data = {
            "scene_epoch": state.scene_epoch(),
            "context_revision": state.context_revision(),
            **selection,
        }
    elif uri == "maya://timeline":
        context = state.maya_context()
        key_curves = cmds.ls(type="animCurve") or []
        data = {
            **context["timeline"],
            "unit": context["units"]["time"],
            "animation_curve_count": len(key_curves),
        }
    else:
        raise state.ToolError("RESOURCE_NOT_FOUND", f"Unknown Maya resource: {uri}")
    response = {
        "contents": [
            {
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(
                    state.json_safe(data),
                    ensure_ascii=True,
                    separators=(",", ":"),
                ),
            }
        ]
    }
    return json.dumps(response, ensure_ascii=True, separators=(",", ":"))
