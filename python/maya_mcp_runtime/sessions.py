"""Owner-bound Python namespaces and a small scene-aware agent SDK.

This is trusted host Python, not a sandbox. Namespace objects are not memory
bounded; only the explicit JSON result store, session count and idle age are.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import maya.cmds as cmds

from . import state

MAX_SESSIONS = 16
IDLE_TTL_SECONDS = 1800
MAX_RESULTS = 64
MAX_RESULT_BYTES = 4 * 1024 * 1024
MAX_CELL_CALLS = 64
MAX_CELL_IMAGES = 8
MAX_CELL_IMAGE_BYTES = 6 * 1024 * 1024


@dataclass
class Session:
    token: str
    owner: str
    epoch: str
    touched: float = field(default_factory=time.monotonic)
    namespace: dict[str, Any] = field(default_factory=dict)
    results: dict[str, bytes] = field(default_factory=dict)
    active: bool = False
    closed: bool = False

    def check(self) -> None:
        if self.owner != state.current_client_session():
            raise state.ToolError("SESSION_NOT_FOUND", "Session is unavailable for this client")
        if self.closed or self.epoch != state.scene_epoch():
            raise state.ToolError("STALE_SESSION", "Session expired or its scene was replaced")


_sessions: dict[str, Session] = {}


def clear_sessions(reason: str = "reset") -> None:
    for session in _sessions.values():
        session.closed = True
        session.results.clear()
        if not session.active:
            session.namespace.clear()
    _sessions.clear()


state.register_lifecycle_cleanup(clear_sessions)


def _expire() -> None:
    now = time.monotonic()
    for token, session in list(_sessions.items()):
        if not session.active and now - session.touched >= IDLE_TTL_SECONDS:
            session.closed = True
            session.namespace.clear()
            session.results.clear()
            del _sessions[token]


def _lookup(token: str) -> Session:
    _expire()
    session = _sessions.get(token)
    if session is None:
        raise state.ToolError("SESSION_NOT_FOUND", "Session is missing or expired; open a new session")
    session.check()
    session.touched = time.monotonic()
    return session


def session_tool(arguments: dict[str, Any], call: state.CallState) -> dict[str, Any]:
    _expire()
    action = arguments["action"]
    token = arguments.get("session_id")
    if action == "open":
        if token:
            raise state.ToolError("INVALID_ARGUMENT", "open allocates a new session_id")
        if len(_sessions) >= MAX_SESSIONS:
            raise state.ToolError("SESSION_LIMIT", "Close an idle session before opening another")
        session = Session(uuid.uuid4().hex, state.current_client_session(), state.scene_epoch())
        _sessions[session.token] = session
    else:
        if not token:
            raise state.ToolError("INVALID_ARGUMENT", f"{action} requires session_id")
        session = _lookup(token)
        if action in {"reset", "close"}:
            if session.active:
                raise state.ToolError("SESSION_BUSY", "Cannot reset or close an executing session")
            session.namespace.clear()
            session.results.clear()
            if action == "close":
                session.closed = True
                del _sessions[session.token]
        elif action != "status":
            raise state.ToolError("INVALID_ARGUMENT", f"Unknown session action: {action}")
    return state.result(call, {
        "session_id": session.token, "action": action,
        "scene_epoch": session.epoch, "active": session.active,
        "closed": session.closed, "idle_ttl_seconds": IDLE_TTL_SECONDS,
        "global_count": len(session.namespace), "result_count": len(session.results),
        "result_bytes": sum(map(len, session.results.values())),
        "limits": {"sessions": MAX_SESSIONS, "results": MAX_RESULTS,
                   "result_bytes": MAX_RESULT_BYTES, "calls_per_cell": MAX_CELL_CALLS},
        "sdk": {
            "call": "maya.call(tool_name, arguments_dict) -> structured data; raises on failure",
            "observe": "maya.observe(**options) -> observation data; also emits viewport image",
            "node": "maya.node(selector) -> handle; handle.name resolves live path, handle.selector is a canonical selector",
            "results": "maya.keep(JSON_value) -> token; maya.get(token) -> copy; maya.release(token)",
            "example": "cube = maya.node(maya.call('maya.geometry.apply', {'kind': 'cube'})['transform']); result = cube.name",
            "limits": "Use bounded loops. Raw Python has full host privileges and no memory sandbox. Do not retain a previous cell's maya SDK object.",
        },
    }, f"Python session {action}")


class NodeHandle:
    def __init__(self, session: Session, selector: Any):
        session.check()
        ref = state.node_ref(state.resolve_node(selector))
        self._session = session
        self._selector = {"scene_epoch": ref["scene_epoch"], "node_id": ref["node_id"]}

    @property
    def name(self) -> str:
        self._session.check()
        return state.resolve_node(self._selector)

    @property
    def selector(self) -> dict[str, str]:
        self.name
        return dict(self._selector)

    def __str__(self) -> str:
        return self.name


class MayaSDK:
    def __init__(self, session: Session):
        self._session = session
        self.calls = 0
        self.images: list[dict[str, Any]] = []
        self.image_sources: list[dict[str, Any]] = []
        self._image_bytes = 0
        self._finished = False

    def _check(self) -> None:
        self._session.check()
        if self._finished:
            raise state.ToolError("STALE_CELL", "Use the current cell's maya SDK")

    def call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        self._check()
        if tool in {"maya.script.execute", "maya.session", "maya.workflow.run"}:
            raise state.ToolError("RECURSIVE_CALL", "SDK calls cannot execute scripts, sessions or workflows")
        if self.calls >= MAX_CELL_CALLS:
            raise state.ToolError("WORK_LIMIT_EXCEEDED", "Cell SDK call limit exceeded")
        self.calls += 1
        from .dispatcher import invoke_tool
        response = invoke_tool(tool, arguments or {})
        envelope = response["structuredContent"]
        for image in response.get("content", []):
            if image.get("type") != "image":
                continue
            size = len(image.get("data", ""))
            if len(self.images) >= MAX_CELL_IMAGES or self._image_bytes + size > MAX_CELL_IMAGE_BYTES:
                raise state.ToolError("WORK_LIMIT_EXCEEDED", "Cell image output limit exceeded")
            self.image_sources.append({"content_index": len(self.images), "tool": tool, "call": self.calls})
            self.images.append(image)
            self._image_bytes += size
        if not envelope.get("ok"):
            error = envelope.get("error", {})
            raise state.ToolError(error.get("code", "SDK_CALL_FAILED"), error.get("message", "SDK call failed"), error.get("details"))
        return envelope.get("data")

    def observe(self, **arguments: Any) -> Any:
        return self.call("maya.observe", arguments)

    def node(self, selector: Any) -> NodeHandle:
        self._check()
        return NodeHandle(self._session, selector)

    def keep(self, value: Any) -> str:
        self._check()
        try:
            encoded = json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError, OverflowError, RecursionError) as error:
            raise state.ToolError("INVALID_ARGUMENT", "Result handles require finite JSON data") from error
        results = self._session.results
        if len(results) >= MAX_RESULTS or sum(map(len, results.values())) + len(encoded) > MAX_RESULT_BYTES:
            raise state.ToolError("RESULT_LIMIT", "Release stored results before retaining more data")
        token = uuid.uuid4().hex
        results[token] = encoded
        return token

    def get(self, token: str) -> Any:
        self._check()
        if token not in self._session.results:
            raise state.ToolError("RESULT_NOT_FOUND", "Unknown result handle")
        return json.loads(self._session.results[token])

    def release(self, token: str) -> bool:
        self._check()
        return self._session.results.pop(token, None) is not None


def begin_cell(token: str | None, arguments: dict[str, Any]) -> tuple[dict[str, Any], MayaSDK]:
    session = _lookup(token) if token else Session("", state.current_client_session(), state.scene_epoch())
    if session.active:
        raise state.ToolError("SESSION_BUSY", "Session is already executing")
    session.active = True
    sdk = MayaSDK(session)
    session.namespace.pop("result", None)
    session.namespace.update(__name__="__maya_mcp__", cmds=cmds, arguments=arguments, maya=sdk)
    return session.namespace, sdk


def end_cell(sdk: MayaSDK) -> None:
    sdk._finished = True
    session = sdk._session
    session.active = False
    session.touched = time.monotonic()
    if session.closed:
        session.namespace.clear()
