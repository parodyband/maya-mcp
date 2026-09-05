"""On-demand discovery and bounded, sequential composition of Maya tools.

The existing handlers remain the execution and undo authority. This module
never evaluates code or attempts to merge unrelated undo policies.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from . import state
from .catalog import TOOLS

_OUTPUT_BYTES = 512 * 1024
_RETAINED_BYTES = 4 * 1024 * 1024
_IMAGE_BYTES = 6 * 1024 * 1024


def describe(arguments: dict[str, Any], call: state.CallState) -> dict[str, Any]:
    definitions = {tool["name"]: tool for tool in TOOLS}
    names = arguments.get("names")
    if names:
        unknown = [name for name in names if name not in definitions]
        if unknown:
            raise state.ToolError("TOOL_NOT_FOUND", "Unknown Maya operations", {"names": unknown})
        matches = [definitions[name] for name in dict.fromkeys(names)]
    else:
        terms = arguments.get("query", "").lower().split()
        matches = [tool for tool in TOOLS if all(
            term in (tool["name"] + " " + tool["description"]).lower() for term in terms
        )]
    records = [{key: tool[key] for key in (
        ("name", "description", "annotations", "inputSchema") if names else
        ("name", "description", "annotations")
    )} for tool in matches]
    return state.result(call, {"tools": records, "count": len(records)},
                        f"Found {len(records)} Maya operations")


def _pointer_parts(pointer: str) -> list[str]:
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        raise state.ToolError("INVALID_ARGUMENT", "JSON Pointer must be empty or start with /")
    if re.search(r"~(?![01])", pointer):
        raise state.ToolError("INVALID_ARGUMENT", "Invalid JSON Pointer escape")
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer.split("/")[1:]]


def _pointer(value: Any, pointer: str) -> Any:
    for part in _pointer_parts(pointer):
        try:
            if isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", part):
                value = value[int(part)]
            elif isinstance(value, dict):
                value = value[part]
            else:
                raise KeyError(part)
        except (KeyError, IndexError, ValueError) as error:
            raise state.ToolError("REFERENCE_NOT_FOUND", "Result has no value at JSON Pointer",
                                  {"pointer": pointer}) from error
    return copy.deepcopy(value)


def _references(value: Any, prior: dict[str, Any], *, resolve: bool, depth: int = 0) -> Any:
    if depth > 32:
        raise state.ToolError("INVALID_ARGUMENT", "Workflow arguments exceed 32 nesting levels")
    if isinstance(value, dict):
        if "$ref" in value:
            reference = value["$ref"]
            if set(value) != {"$ref"} or not isinstance(reference, str) or "#" not in reference:
                raise state.ToolError("INVALID_ARGUMENT", 'A reference must be {"$ref":"step#/pointer"}')
            step_id, pointer = reference.split("#", 1)
            _pointer_parts(pointer)
            if step_id not in prior:
                raise state.ToolError("INVALID_ARGUMENT", "References must name an earlier step",
                                      {"reference": reference})
            return _pointer(prior[step_id], pointer) if resolve else value
        return {key: _references(item, prior, resolve=resolve, depth=depth + 1)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_references(item, prior, resolve=resolve, depth=depth + 1) for item in value]
    return value


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))


def _error_record(error: dict[str, Any]) -> dict[str, Any]:
    """Preserve actionable small fields without letting exception text hide edits."""
    details: dict[str, Any] = {}
    remaining = 32768
    original = error.get("details", {})
    for key, value in list(original.items())[:32]:
        size = _size({key: value})
        if size <= remaining:
            details[key] = value
            remaining -= size
        else:
            details[key[:128]] = {"truncated": True, "original_bytes": size}
    if len(original) > 32:
        details["details_truncated"] = True
    message = str(error.get("message", ""))
    return {"code": str(error.get("code", "MAYA_ERROR"))[:128],
            "message": message[:8192], "message_truncated": len(message) > 8192,
            "details": details}


def run(arguments: dict[str, Any], call: state.CallState) -> dict[str, Any]:
    # Lazy import keeps the registry independent of its execution adapter.
    from .dispatcher import HANDLERS, TOOL_DEFINITIONS, _validate, invoke_tool

    if arguments.get("if_scene_epoch", state.scene_epoch()) != state.scene_epoch():
        raise state.ToolError("SCENE_EPOCH_MISMATCH", "The workflow targets a different scene")
    state.require_revision(arguments.get("if_scene_revision"))
    steps = arguments["steps"]
    prior: dict[str, Any] = {}
    # Catch invalid known inputs, forward references and recursion before edits.
    # Scene-dependent validation necessarily happens at each step's execution.
    for step in steps:
        name = step["tool"]
        if name == "maya.workflow.run":
            raise state.ToolError("INVALID_ARGUMENT", "Nested workflows are not supported")
        if name not in TOOL_DEFINITIONS or name not in HANDLERS:
            raise state.ToolError("TOOL_NOT_FOUND", f"Unknown Maya operation: {name}")
        if step["id"] in prior:
            raise state.ToolError("INVALID_ARGUMENT", "Workflow step IDs must be unique")
        values = step.get("arguments", {})
        _references(values, prior, resolve=False)
        _validate(values, TOOL_DEFINITIONS[name]["inputSchema"], references=True)
        for pointer in step.get("select", {}).values():
            _pointer_parts(pointer)
        prior[step["id"]] = None

    if "observe" in arguments:
        _references(arguments["observe"], prior, resolve=False)
        _validate(arguments["observe"], TOOL_DEFINITIONS["maya.observe"]["inputSchema"], references=True)

    prior.clear()
    records: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    had_images = False
    output_bytes = retained_bytes = image_bytes = change_bytes = warning_bytes = 0
    failed_step: str | None = None
    for index, step in enumerate(steps):
        try:
            values = _references(step.get("arguments", {}), prior, resolve=True)
            response = invoke_tool(step["tool"], values)
        except Exception as error:
            response = state.failure(state.begin_call(), error)
        structured = response["structuredContent"]
        prior[step["id"]] = structured
        retained_bytes += _size(structured)
        record = {"id": step["id"], "tool": step["tool"], **{
            key: structured[key] for key in ("ok", "summary", "revisions", "undo", "timing_ms")
        }}
        if len(record["summary"]) > 8192:
            record["summary"] = record["summary"][:8192]
            record["summary_truncated"] = True
        record["undo"] = {**record["undo"], "label": record["undo"].get("label", "")[:120]}
        record["undo"]["scope"] = "native_request" if call.native_undo_group else "operation"
        if "error" in structured:
            record["error"] = _error_record(structured["error"])
        # Preserve child recording/rollback evidence. Aggregate changes make
        # partial completion visible even when selected data is suppressed.
        for change in structured["changes"]:
            size = _size(change)
            if change_bytes + size <= _OUTPUT_BYTES:
                call.changes.append({**change, "step_id": step["id"]})
                change_bytes += size
            else:
                record["changes_truncated"] = True
        for warning in structured["warnings"]:
            size = _size(warning)
            if warning_bytes + size <= 65536:
                call.warnings.append({**warning, "step_id": step["id"]})
                warning_bytes += size
            else:
                record["warnings_truncated"] = True
        try:
            data = ({key: _pointer(structured, pointer) for key, pointer in step["select"].items()}
                    if "select" in step else structured["data"])
        except state.ToolError as error:
            # Output selection happens after execution. Do not label a
            # successful edit as a failed edit that should be retried.
            data = {}
            call.warnings.append({"code": "OUTPUT_SELECTION_FAILED", "step_id": step["id"],
                                  "message": str(error), "details": error.details})
        size = _size(data)
        if output_bytes + size <= _OUTPUT_BYTES:
            record["data"] = data
            output_bytes += size
        else:
            record["data"] = {}
            record["data_truncated"] = True
            call.warnings.append({"code": "OUTPUT_TRUNCATED", "step_id": step["id"],
                                  "message": "Workflow output budget exceeded; use select for smaller results"})
        for item in response.get("content", []):
            if item.get("type") != "image":
                continue
            had_images = True
            size = _size(item)
            if image_bytes + size <= _IMAGE_BYTES:
                record.setdefault("image_content_indices", []).append(len(images))
                images.append(item)
                image_bytes += size
            else:
                call.warnings.append({"code": "IMAGE_OUTPUT_TRUNCATED", "step_id": step["id"],
                                      "message": "Workflow image budget exceeded"})
        records.append(record)
        if not structured["ok"]:
            failed_step = step["id"]
            break
        if retained_bytes > _RETAINED_BYTES and index + 1 < len(steps):
            failed_step = steps[index + 1]["id"]
            call.warnings.append({"code": "WORKFLOW_RESULT_LIMIT", "message":
                                  "Stopped before the next step: retained results exceeded 4 MiB"})
            break

    data = {"steps": records, "completed": sum(record["ok"] for record in records),
            "failed_step": failed_step, "skipped": [step["id"] for step in steps[len(records):]],
            "atomic": False, "undo_scope": "native_request" if call.native_undo_group else "operation"}
    # Maya's undo-enabled native Python invocation groups recorded edits from
    # this request. Child chunks still provide local rollback; they are not
    # independently addressable history entries after the request completes.
    # Files, scripts and explicit history operations can invalidate undo state.
    call.undo_available = (call.native_undo_group
                           and any(record["undo"]["available"] for record in records)
                           and not any(record["tool"] in {"maya.file.apply", "maya.script.execute", "maya.history.apply"}
                                       for record in records))
    if call.undo_available:
        call.undo_label = "Maya MCP workflow"
    result = state.result(call, data, f"Completed {data['completed']} of {len(steps)} workflow steps",
                          image_content=images)
    if images:
        # Match viewport.capture's image-only compatibility contract. Metadata
        # remains in structuredContent; do not expose raw depth as visible text.
        result["content"] = images
    if failed_step:
        result["isError"] = True
        envelope = result["structuredContent"]
        envelope["ok"] = False
        envelope["error"] = {"code": "WORKFLOW_STOPPED", "message": "Workflow stopped; inspect completed steps before retrying",
                             "details": {"failed_step": failed_step}}
        if not had_images:
            result["content"][-1]["text"] = json.dumps(envelope, ensure_ascii=True, separators=(",", ":"))
    if had_images and not images:
        # A dropped image must not expose renderer payloads through the normal
        # text fallback. Keep recovery data in the structured envelope.
        result["content"] = [{"type": "text", "text":
            result["structuredContent"]["summary"] +
            "; image output exceeded the workflow budget. Inspect structuredContent for step status and warnings."}]
    if "observe" in arguments:
        from .tools_observation import attach_feedback
        try:
            config = _references(arguments["observe"], prior, resolve=True)
        except state.ToolError as error:
            result["structuredContent"]["warnings"].append({"code": "POST_OBSERVATION_FAILED", "message": str(error)})
            if not had_images:
                result["content"] = [{"type": "text", "text": json.dumps(result["structuredContent"], ensure_ascii=True, separators=(",", ":"))}]
        else:
            result = attach_feedback(result, config)
    return result


WORKFLOW_HANDLERS = {"maya.tools.describe": describe, "maya.workflow.run": run}
