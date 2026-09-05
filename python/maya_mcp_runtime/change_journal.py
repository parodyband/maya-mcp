"""Bounded, scoped change hints, not a complete evaluated-scene history.

Callbacks capture native identities and messages only: no cmds queries, plug
value evaluation, or scene traversal on the Maya callback path. Attribute
callbacks do not cover playback/scrubbing or downstream evaluated changes.
"""

from __future__ import annotations

from collections import deque
import threading
import uuid
from typing import Any

import maya.api.OpenMaya as om

from . import state

MAX_EVENTS = 2048
MAX_WATCHED_NODES = 512
_events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
_callbacks: list[int] = []
_watched: dict[str, dict[str, Any]] = {}
_generation = uuid.uuid4().hex
_sequence = 0
_pending_revision = False
_node_count: int | None = None
_lock = threading.RLock()


def _append(kind: str, *, scene: bool = True, **data: Any) -> None:
    global _sequence, _pending_revision, _node_count
    with _lock:
        _sequence += 1
        _events.append({"sequence": _sequence, "kind": kind, **data})
        _pending_revision = _pending_revision or scene
        if kind == "coverage_gap":
            _node_count = None


def _identity(node: om.MObject) -> dict[str, str]:
    fn = om.MFnDependencyNode(node)
    return {"uuid": fn.uuid().asString(), "name": fn.name()}


def _node_added(node: om.MObject, *_: Any) -> None:
    _adjust_node_count(1)
    try:
        _append("node_added", **_identity(node))
    except Exception:
        _append("coverage_gap", reason="node_added_identity_unavailable")


def _node_removed(node: om.MObject, *_: Any) -> None:
    _adjust_node_count(-1)
    try:
        _append("node_removed", **_identity(node))
    except Exception:
        _append("coverage_gap", reason="node_removed_identity_unavailable")


def _attribute_changed(message: int, plug: om.MPlug, other: om.MPlug, node_id: str) -> None:
    # Evaluation alone is not an edit and can be extremely frequent.
    mask = (om.MNodeMessage.kAttributeSet | om.MNodeMessage.kConnectionMade
            | om.MNodeMessage.kConnectionBroken | om.MNodeMessage.kAttributeAdded
            | om.MNodeMessage.kAttributeRemoved | om.MNodeMessage.kAttributeRenamed
            | om.MNodeMessage.kAttributeLocked | om.MNodeMessage.kAttributeUnlocked
            | om.MNodeMessage.kAttributeKeyable | om.MNodeMessage.kAttributeUnkeyable
            | om.MNodeMessage.kAttributeArrayAdded | om.MNodeMessage.kAttributeArrayRemoved)
    if not message & mask:
        return
    try:
        _append("attribute_changed", uuid=node_id,
                attribute=plug.partialName(useLongNames=True), message=int(message))
    except Exception:
        _append("coverage_gap", uuid=node_id, reason="attribute_identity_unavailable")


def _name_changed(node: om.MObject, previous: str, node_id: str) -> None:
    try:
        _append("node_renamed", uuid=node_id, previous_name=previous,
                name=om.MFnDependencyNode(node).name())
    except Exception:
        _append("coverage_gap", uuid=node_id, reason="rename_identity_unavailable")


def _remove(callbacks: list[int]) -> None:
    while callbacks:
        try:
            om.MMessage.removeCallback(callbacks.pop())
        except (RuntimeError, ValueError):
            pass  # Maya can remove node callbacks during node destruction.


def _adjust_node_count(delta: int) -> None:
    global _node_count
    with _lock:
        if _node_count is not None:
            _node_count += delta
            if _node_count < 0:
                _node_count = None


def _rebuild_node_count() -> None:
    global _node_count
    _node_count = None
    if not _callbacks:
        return
    try:
        import maya.cmds as cmds
        # Once per install/reset, matching the fallback signature's definition.
        _node_count = len(cmds.ls(dependencyNodes=True) or [])
    except Exception:
        pass


def node_count() -> int | None:
    """O(1) callback-maintained count, or None when coverage is unavailable."""
    with _lock:
        return _node_count if _callbacks else None


def install() -> None:
    """Install global hints. Scene lifecycle is driven by state cleanup hooks."""
    if _callbacks:
        return
    try:
        _callbacks.append(om.MDGMessage.addNodeAddedCallback(_node_added, "dependNode"))
        _callbacks.append(om.MDGMessage.addNodeRemovedCallback(_node_removed, "dependNode"))
        _callbacks.append(om.MEventMessage.addEventCallback(
            "SelectionChanged", lambda *_: _append("selection_changed", scene=False)))
        _callbacks.append(om.MEventMessage.addEventCallback(
            "timeChanged", lambda *_: _append("time_changed", scene=False)))
        _rebuild_node_count()
    except Exception:
        shutdown()
        raise


def reset(reason: str = "scene_reset") -> None:
    """Discard scene-bound watches and invalidate all previous cursors."""
    global _sequence, _generation, _pending_revision
    with _lock:
        for entry in _watched.values():
            _remove(entry["callbacks"])
        _watched.clear()
        _events.clear()
        _sequence = 0
        _generation = uuid.uuid4().hex
        _pending_revision = False
        _rebuild_node_count()


def shutdown() -> None:
    _remove(_callbacks)
    reset("plugin_shutdown")


def consume_pending_revision() -> bool:
    """Drain the scene-edit flag for state._sync_external_scene_changes."""
    global _pending_revision
    with _lock:
        pending = _pending_revision
        _pending_revision = False
        return pending


def coverage() -> dict[str, Any]:
    with _lock:
        watched = []
        for node_id, entry in _watched.items():
            handle = entry["handle"]
            if handle.isValid() and handle.isAlive():
                watched.append({"uuid": node_id, "name": om.MFnDependencyNode(handle.object()).name()})
        return {
            "installed": bool(_callbacks), "scope": "watched_nodes",
            "global_events": ["node_added", "node_removed", "selection_changed", "time_changed"] if _callbacks else [],
            "watched_nodes": watched, "watch_limit": MAX_WATCHED_NODES,
            "event_capacity": MAX_EVENTS, "complete_scene_tracking": False,
            "limitations": ["Attributes and names are tracked only for watched nodes.",
                            "DAG parenting, geometry component edits, downstream evaluation and playback attribute changes are not comprehensively tracked.",
                            "Node-added names can be provisional; resolve UUIDs when inspecting."],
        }


def watch(nodes: list[str]) -> dict[str, Any]:
    """Add bounded attribute/name watches; report unresolved or excess targets."""
    install()
    rejected = []
    with _lock:
        for node_id, entry in list(_watched.items()):
            if not entry["handle"].isValid() or not entry["handle"].isAlive():
                _remove(entry["callbacks"])
                del _watched[node_id]
        for name in nodes:
            callbacks: list[int] = []
            try:
                selection = om.MSelectionList()
                selection.add(name)
                node = selection.getDependNode(0)
                node_id = om.MFnDependencyNode(node).uuid().asString()
                if node_id in _watched:
                    if _watched[node_id]["handle"].object() != node:
                        rejected.append({"node": name, "reason": "duplicate_uuid"})
                        _append("coverage_gap", uuid=node_id, reason="duplicate_uuid")
                    continue
                if len(_watched) >= MAX_WATCHED_NODES:
                    rejected.append({"node": name, "reason": "watch_limit"})
                    continue
                callbacks.append(om.MNodeMessage.addAttributeChangedCallback(node, _attribute_changed, node_id))
                callbacks.append(om.MNodeMessage.addNameChangedCallback(node, _name_changed, node_id))
                _watched[node_id] = {"handle": om.MObjectHandle(node), "callbacks": callbacks}
            except Exception:
                _remove(callbacks)
                rejected.append({"node": name, "reason": "unresolved_or_unwatchable"})
    return {**coverage(), "rejected_nodes": rejected}


def _cursor(sequence: int) -> str:
    return f"{state.scene_epoch()}:{_generation}:{sequence}"


def cursor() -> str:
    with _lock:
        return _cursor(_sequence)


def read(cursor: str | None = None, limit: int = 200) -> dict[str, Any]:
    """Read a bounded page. Use next_cursor, not latest_cursor, to continue."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_EVENTS:
        raise state.ToolError("INVALID_ARGUMENT", f"limit must be between 1 and {MAX_EVENTS}.")
    with _lock:
        reason = None
        sequence = _sequence
        if cursor is None:
            reason = "initial_observation_required"
        else:
            try:
                epoch, generation, raw = cursor.split(":")
                sequence = int(raw)
                if epoch != state.scene_epoch() or generation != _generation:
                    reason = "scene_or_journal_reset"
                elif sequence < 0 or sequence > _sequence:
                    reason = "invalid_cursor"
                elif _events and sequence < _events[0]["sequence"] - 1:
                    reason = "journal_overflow"
            except (ValueError, AttributeError):
                reason = "invalid_cursor"
        events = [] if reason else [dict(event) for event in _events if event["sequence"] > sequence][:limit]
        if any(event["kind"] == "coverage_gap" for event in events):
            reason = "callback_coverage_gap"
        next_sequence = events[-1]["sequence"] if events else (_sequence if reason else sequence)
        return {"events": events, "next_cursor": _cursor(next_sequence),
                "latest_cursor": _cursor(_sequence), "has_more": next_sequence < _sequence,
                "requires_observation": reason is not None, "reason": reason,
                "coverage": coverage()}
