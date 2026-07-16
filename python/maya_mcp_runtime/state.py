"""Shared scene identity, result-envelope, and safety helpers."""

from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import json
import math
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterator

import maya.cmds as cmds
import maya.api.OpenMaya as om

SCHEMA_VERSION = "1.0"
_scene_epoch = uuid.uuid4().hex
_scene_revision = 0
_context_revision = 0
_node_registry: dict[str, dict[str, Any]] = {}
_last_scene_signature: tuple[Any, ...] | None = None
_callback_ids: list[int] = []


class ToolError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass
class CallState:
    request_id: str
    started: float
    scene_before: int
    changes: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    undo_available: bool = False
    undo_label: str = ""
    mutation_started: bool = False
    rolled_back: bool = False


def begin_call() -> CallState:
    _sync_external_scene_changes()
    return CallState(
        request_id=uuid.uuid4().hex,
        started=time.perf_counter(),
        scene_before=_scene_revision,
        changes=[],
        warnings=[],
    )


def scene_epoch() -> str:
    return _scene_epoch


def scene_revision() -> int:
    return _scene_revision


def context_revision() -> int:
    return _context_revision


def _capture_scene_signature() -> tuple[Any, ...]:
    return (
        cmds.file(query=True, sceneName=True) or "",
        bool(cmds.file(query=True, modified=True)),
        cmds.undoInfo(query=True, undoName=True) or "",
        cmds.undoInfo(query=True, redoName=True) or "",
        len(cmds.ls(dependencyNodes=True) or []),
    )


def _sync_external_scene_changes() -> None:
    global _last_scene_signature, _scene_revision
    signature = _capture_scene_signature()
    if _last_scene_signature is None:
        _last_scene_signature = signature
    elif signature != _last_scene_signature:
        _scene_revision += 1
        _last_scene_signature = signature


def _scene_replaced(*_: Any) -> None:
    reset_scene_epoch()


def _context_changed(*_: Any) -> None:
    bump_context_revision()


def install_callbacks() -> None:
    if _callback_ids:
        return
    _callback_ids.extend(
        [
            om.MSceneMessage.addCallback(
                om.MSceneMessage.kAfterNew, _scene_replaced
            ),
            om.MSceneMessage.addCallback(
                om.MSceneMessage.kAfterOpen, _scene_replaced
            ),
            om.MEventMessage.addEventCallback(
                "SelectionChanged", _context_changed
            ),
            om.MEventMessage.addEventCallback("timeChanged", _context_changed),
        ]
    )


def shutdown_callbacks() -> None:
    while _callback_ids:
        callback_id = _callback_ids.pop()
        try:
            om.MMessage.removeCallback(callback_id)
        except RuntimeError:
            pass


def reset_scene_epoch() -> str:
    global _scene_epoch, _scene_revision, _context_revision, _last_scene_signature
    _scene_epoch = uuid.uuid4().hex
    _scene_revision = 0
    _context_revision = 0
    _node_registry.clear()
    _last_scene_signature = _capture_scene_signature()
    return _scene_epoch


def bump_scene_revision() -> int:
    global _scene_revision, _last_scene_signature
    _scene_revision += 1
    _last_scene_signature = _capture_scene_signature()
    return _scene_revision


def bump_context_revision() -> int:
    global _context_revision
    _context_revision += 1
    return _context_revision


def mark_mutated(call: CallState) -> None:
    call.mutation_started = True


def json_safe(value: Any, depth: int = 0) -> Any:
    if depth > 10:
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item, depth + 1) for item in value]
    try:
        return [json_safe(item, depth + 1) for item in value]
    except TypeError:
        return str(value)


def result(
    call: CallState,
    data: Any,
    summary: str,
    *,
    image_content: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    structured = {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "request_id": call.request_id,
        "scene_epoch": _scene_epoch,
        "revisions": {
            "scene_before": call.scene_before,
            "scene_after": _scene_revision,
            "context": _context_revision,
        },
        "summary": summary,
        "data": json_safe(data),
        "changes": json_safe(
            [
                {**change, "rolled_back": True}
                if call.rolled_back
                else change
                for change in call.changes
            ]
        ),
        "warnings": json_safe(
            [
                *call.warnings,
                *(
                    [{
                        "code": "TRANSACTION_ROLLED_BACK",
                        "message": "Maya MCP rolled back the transaction after an error",
                    }]
                    if call.rolled_back
                    else []
                ),
            ]
        ),
        "undo": {
            "available": call.undo_available,
            "label": call.undo_label,
        },
        "timing_ms": round((time.perf_counter() - call.started) * 1000.0, 3),
    }
    content = list(image_content or [])
    content.append(
        {
            "type": "text",
            "text": json.dumps(structured, ensure_ascii=True, separators=(",", ":")),
        }
    )
    return {
        "content": content,
        "structuredContent": structured,
        "isError": False,
    }


def failure(
    call: CallState,
    error: ToolError | Exception,
) -> dict[str, Any]:
    if isinstance(error, ToolError):
        code = error.code
        details = error.details
    else:
        code = "MAYA_ERROR"
        details = {"type": type(error).__name__}
    structured = {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "request_id": call.request_id,
        "scene_epoch": _scene_epoch,
        "revisions": {
            "scene_before": call.scene_before,
            "scene_after": _scene_revision,
            "context": _context_revision,
        },
        "summary": str(error),
        "data": {},
        "changes": json_safe(
            [
                {**change, "rolled_back": True}
                if call.rolled_back
                else change
                for change in call.changes
            ]
        ),
        "warnings": json_safe(
            [
                *call.warnings,
                *(
                    [{
                        "code": "TRANSACTION_ROLLED_BACK",
                        "message": "Maya MCP rolled back the transaction after an error",
                    }]
                    if call.rolled_back
                    else []
                ),
            ]
        ),
        "undo": {
            "available": call.undo_available,
            "label": call.undo_label,
        },
        "timing_ms": round((time.perf_counter() - call.started) * 1000.0, 3),
        "error": {
            "code": code,
            "message": str(error),
            "details": json_safe(details),
        },
    }
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(structured, ensure_ascii=True, separators=(",", ":")),
            }
        ],
        "structuredContent": structured,
        "isError": True,
    }


def _node_identity_data(node: str) -> tuple[str, str | None, list[str]]:
    names = cmds.ls(node, long=True, objectsOnly=True) or []
    if not names:
        raise ToolError("TARGET_NOT_FOUND", f"Maya node does not exist: {node}")
    long_name = names[0]
    dag_paths = cmds.ls(long_name, long=True, allPaths=True) or [long_name]
    uuids = cmds.ls(long_name, uuid=True) or [""]
    reference_node: str | None = None
    try:
        if cmds.referenceQuery(long_name, isNodeReferenced=True):
            reference_node = cmds.referenceQuery(long_name, referenceNode=True)
    except RuntimeError:
        pass
    identity = json.dumps(
        {
            "epoch": _scene_epoch,
            "uuid": uuids[0],
            "reference": reference_node,
            "paths": sorted(dag_paths),
        },
        sort_keys=True,
    )
    opaque = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"node:{_scene_epoch}:{opaque}", reference_node, dag_paths


def node_ref(node: str) -> dict[str, Any]:
    node_id, reference_node, dag_paths = _node_identity_data(node)
    long_name = (cmds.ls(node, long=True, objectsOnly=True) or [node])[0]
    uuid_values = cmds.ls(long_name, uuid=True) or [""]
    locked = bool((cmds.lockNode(long_name, query=True, lock=True) or [False])[0])
    reference = {
        "node_id": node_id,
        "scene_epoch": _scene_epoch,
        "uuid": uuid_values[0],
        "reference_node": reference_node,
        "name": long_name.rsplit("|", 1)[-1],
        "long_name": long_name,
        "type": cmds.nodeType(long_name),
        "dag_paths": dag_paths,
        "referenced": reference_node is not None,
        "locked": locked,
    }
    _node_registry[node_id] = reference
    return reference


def _resolve_registered(node_id: str) -> str:
    reference = _node_registry.get(node_id)
    if reference is None:
        raise ToolError(
            "STALE_NODE_ID",
            f"Unknown or stale node_id: {node_id}",
            {"scene_epoch": _scene_epoch},
        )
    path = reference["long_name"]
    if cmds.objExists(path):
        current_uuids = cmds.ls(path, uuid=True) or []
        try:
            current_reference = (
                cmds.referenceQuery(path, referenceNode=True)
                if cmds.referenceQuery(path, isNodeReferenced=True)
                else None
            )
        except RuntimeError:
            current_reference = None
        if (
            current_uuids
            and current_uuids[0] == reference["uuid"]
            and current_reference == reference["reference_node"]
        ):
            return path
    candidates = []
    for candidate in cmds.ls(dependencyNodes=True, long=True) or []:
        candidate_uuids = cmds.ls(candidate, uuid=True) or []
        if candidate_uuids and candidate_uuids[0] == reference["uuid"]:
            candidates.append(candidate)
    matching = []
    for candidate in candidates:
        try:
            candidate_reference = (
                cmds.referenceQuery(candidate, referenceNode=True)
                if cmds.referenceQuery(candidate, isNodeReferenced=True)
                else None
            )
        except RuntimeError:
            candidate_reference = None
        if candidate_reference == reference["reference_node"]:
            matching.append(candidate)
    if len(matching) == 1:
        return matching[0]
    raise ToolError(
        "STALE_NODE_ID",
        f"Could not resolve stale node_id: {node_id}",
        {"candidates": matching},
    )


def resolve_node(selector: Any) -> str:
    if isinstance(selector, dict):
        if selector.get("scene_epoch") not in (None, _scene_epoch):
            raise ToolError(
                "SCENE_EPOCH_MISMATCH",
                "The node reference belongs to a different Maya scene epoch",
            )
        if selector.get("node_id"):
            return _resolve_registered(str(selector["node_id"]))
        selector = (
            selector.get("dag_path")
            or selector.get("long_name")
            or (selector.get("dag_paths") or [None])[0]
            or selector.get("name")
        )
    if not isinstance(selector, str) or not selector:
        raise ToolError("INVALID_TARGET", "A non-empty Maya node selector is required")
    if selector.startswith("node:"):
        return _resolve_registered(selector)
    matches = cmds.ls(selector, long=True) or []
    if not matches:
        raise ToolError("TARGET_NOT_FOUND", f"Maya target does not exist: {selector}")
    unique = list(dict.fromkeys(matches))
    if len(unique) > 1:
        raise ToolError(
            "TARGET_AMBIGUOUS",
            f"Maya target is ambiguous: {selector}",
            {"candidates": unique[:50]},
        )
    return unique[0]


def selection_refs() -> list[dict[str, Any]]:
    references = []
    for item in cmds.ls(selection=True, long=True, flatten=True) or []:
        base = item.split(".", 1)[0]
        reference = node_ref(base)
        if "." in item:
            reference = dict(reference)
            reference["component"] = item
        references.append(reference)
    return references


def safe_get_attr(plug: str) -> Any:
    try:
        value = cmds.getAttr(plug)
        return json_safe(value)
    except (RuntimeError, ValueError) as error:
        return {"error": str(error)}


def maya_context() -> dict[str, Any]:
    panel = ""
    camera = ""
    try:
        panel = cmds.getPanel(withFocus=True) or ""
        if panel and cmds.getPanel(typeOf=panel) == "modelPanel":
            camera = cmds.modelPanel(panel, query=True, camera=True) or ""
    except RuntimeError:
        pass
    renderer = ""
    try:
        renderer = cmds.getAttr("defaultRenderGlobals.currentRenderer")
    except RuntimeError:
        pass
    return {
        "maya": {
            "version": cmds.about(version=True),
            "api_version": int(cmds.about(apiVersion=True)),
            "batch": bool(cmds.about(batch=True)),
        },
        "scene": {
            "path": cmds.file(query=True, sceneName=True) or "",
            "modified": bool(cmds.file(query=True, modified=True)),
            "epoch": _scene_epoch,
            "revision": _scene_revision,
            "context_revision": _context_revision,
        },
        "workspace": cmds.workspace(query=True, rootDirectory=True),
        "units": {
            "linear": cmds.currentUnit(query=True, linear=True),
            "angle": cmds.currentUnit(query=True, angle=True),
            "time": cmds.currentUnit(query=True, time=True),
            "up_axis": cmds.upAxis(query=True, axis=True),
        },
        "timeline": {
            "current": cmds.currentTime(query=True),
            "playback_min": cmds.playbackOptions(query=True, minTime=True),
            "playback_max": cmds.playbackOptions(query=True, maxTime=True),
            "animation_start": cmds.playbackOptions(query=True, animationStartTime=True),
            "animation_end": cmds.playbackOptions(query=True, animationEndTime=True),
        },
        "selection": selection_refs(),
        "viewport": {"panel": panel, "camera": camera},
        "renderer": renderer,
        "undo": {
            "enabled": bool(cmds.undoInfo(query=True, state=True)),
            "undo_name": cmds.undoInfo(query=True, undoName=True) or "",
            "redo_name": cmds.undoInfo(query=True, redoName=True) or "",
        },
    }


@contextlib.contextmanager
def undo_chunk(
    call: CallState,
    label: str,
    *,
    rollback_on_error: bool = True,
) -> Iterator[None]:
    opened = False
    undo_enabled = bool(cmds.undoInfo(query=True, state=True))
    internal_label = f"{label} [{call.request_id[:8]}]"
    try:
        cmds.undoInfo(openChunk=True, chunkName=internal_label)
        opened = True
        yield
    except Exception:
        if opened:
            cmds.undoInfo(closeChunk=True)
            opened = False
            if call.mutation_started and rollback_on_error and undo_enabled:
                try:
                    cmds.undo()
                    call.rolled_back = True
                except RuntimeError:
                    pass
        raise
    finally:
        if opened:
            cmds.undoInfo(closeChunk=True)
    call.undo_available = call.mutation_started and undo_enabled
    call.undo_label = label


def require_revision(expected: int | None) -> None:
    if expected is not None and int(expected) != _scene_revision:
        raise ToolError(
            "REVISION_CONFLICT",
            "The Maya scene changed after this operation was planned",
            {"expected": int(expected), "actual": _scene_revision},
        )


def matches_name(node: str, pattern: str | None) -> bool:
    if not pattern:
        return True
    return fnmatch.fnmatchcase(node, pattern) or fnmatch.fnmatchcase(
        node.rsplit("|", 1)[-1], pattern
    )
