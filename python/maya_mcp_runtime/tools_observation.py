"""A bounded observe/act/observe loop with explicit coherence and coverage."""

from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
import time
import uuid
from typing import Any

import maya.cmds as cmds

from . import change_journal, state
from .tools_core import scene_query
from .tools_viewport import _active_view, _camera_metadata, viewport_capture
from .tools_vision import viewport_scene_map

_observations: OrderedDict[str, dict[str, Any]] = OrderedDict()
_OBSERVATION_TTL = 300
_OBSERVATION_LIMIT = 64


def _clear(_reason: str) -> None:
    _observations.clear()


state.register_lifecycle_cleanup(_clear)


def _camera_stamp() -> dict[str, Any] | None:
    if cmds.about(batch=True):
        return None
    view = _active_view()
    return {"camera": _camera_metadata(view), "width": int(view.portWidth()), "height": int(view.portHeight())}


def _stamp() -> dict[str, Any]:
    state._sync_external_scene_changes()
    camera = _camera_stamp()
    return {"scene_epoch": state.scene_epoch(), "scene_revision": state.scene_revision(),
            "context_revision": state.context_revision(), "cursor": change_journal.cursor(),
            "camera": hashlib.sha256(json.dumps(camera, sort_keys=True).encode()).hexdigest()}


def require_observation(observation_id: str) -> None:
    record = _observations.get(observation_id)
    if (record is None or record["owner"] != state.current_client_session()
            or time.monotonic() - record["created"] > _OBSERVATION_TTL):
        raise state.ToolError("OBSERVATION_EXPIRED", "Observe Maya again before editing")
    if not record["coherent"] or record["stamp"] != _stamp():
        raise state.ToolError("STALE_OBSERVATION", "Maya changed after this observation; observe again")


def _bounded_nodes(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    output = []
    size = 0
    truncated = False
    for record in records:
        encoded = json.dumps(record, ensure_ascii=True, separators=(",", ":"))
        if len(encoded) > 65536:
            record = {key: value for key, value in record.items() if key != "attributes"}
            record["attributes_truncated"] = True
            encoded = json.dumps(record, ensure_ascii=True, separators=(",", ":"))
            truncated = True
        if size + len(encoded) > 512 * 1024:
            truncated = True
            break
        output.append(record)
        size += len(encoded)
    return output, truncated


def observe(arguments: dict[str, Any], call: state.CallState) -> dict[str, Any]:
    limit = arguments.get("max_nodes", 40)
    selectors = arguments.get("nodes")
    if selectors is not None:
        nodes = [state.resolve_node(item) for item in selectors[:limit]]
    else:
        nodes = (cmds.ls(selection=True, long=True, objectsOnly=True) or [])[:limit]
        if not nodes:
            nodes = cmds.ls(type="transform", long=True, head=limit) or []
    watched = list(nodes)
    for node in nodes:
        watched.extend(cmds.listRelatives(node, shapes=True, fullPath=True) or [])
        current = node
        # Ancestor transforms affect world space even when the leaf is unchanged.
        for _ in range(64):
            parents = cmds.listRelatives(current, parent=True, fullPath=True) or []
            if not parents:
                break
            current = parents[0]
            watched.append(current)
    interactive = not cmds.about(batch=True)
    if interactive:
        camera = _active_view().getCamera().fullPathName()
        watched.append(camera)
        watched.extend(cmds.listRelatives(camera, parent=True, fullPath=True) or [])
    coverage = change_journal.watch(list(dict.fromkeys(watched)))
    before = _stamp()
    images: list[dict[str, Any]] = []
    capture_data = None
    scene_map_data = None
    image_status = "not_requested"
    if arguments.get("image", True):
        if not interactive:
            image_status = "unavailable"
            call.warnings.append({"code": "VIEWPORT_UNAVAILABLE", "message": "Maya batch mode has no viewport; structural observation is available"})
        else:
            # Synchronous refresh allows Maya to evaluate/draw the edits before
            # readback. It is not a guarantee that arbitrary async renderers idle.
            cmds.refresh(force=True)
            capture = viewport_capture({"width": arguments.get("width", 960),
                "height": arguments.get("height", 540), "format": "png",
                "include_depth": arguments.get("include_depth", False),
                "depth_max_dimension": 128}, state.begin_call())
            images = [item for item in capture["content"] if item["type"] == "image"]
            capture_data = capture["structuredContent"]["data"]
            call.warnings.extend(capture["structuredContent"]["warnings"])
            image_status = "captured"
            if nodes:
                mapped = viewport_scene_map({"nodes": nodes, "width": capture_data["resolution"]["width"],
                    "height": capture_data["resolution"]["height"], "max_nodes": limit}, state.begin_call())
                scene_map_data = mapped["structuredContent"]["data"]
                call.warnings.extend(mapped["structuredContent"]["warnings"])
    query = scene_query({"scope": "nodes", "nodes": nodes, "limit": limit,
                         "include_attributes": arguments.get("attributes", ["translate", "rotate", "scale", "visibility"])}, state.begin_call())
    records, truncated = _bounded_nodes(query["structuredContent"]["data"]["nodes"])
    context = state.maya_context()
    after = _stamp()
    coherent = before == after and not coverage.get("rejected_nodes")
    if before != after:
        call.warnings.append({"code": "OBSERVATION_CHANGED", "message": "Detectable scene, time, selection or camera changes occurred during observation; observe again before using it as an edit guard"})
    if coverage.get("rejected_nodes"):
        call.warnings.append({"code": "OBSERVATION_COVERAGE", "message": "Some requested nodes could not be watched; this observation cannot guard an edit"})
    observation_id = "observation:" + uuid.uuid4().hex
    _observations[observation_id] = {"owner": call.client_session, "created": time.monotonic(),
                                   "stamp": after, "coherent": coherent}
    while len(_observations) > _OBSERVATION_LIMIT:
        _observations.popitem(last=False)
    data = {"observation_id": observation_id, "context": context, "nodes": records,
            "nodes_truncated": truncated or (selectors is not None and len(selectors) > limit),
            "image_status": image_status, "capture": capture_data, "scene_map": scene_map_data,
            "cursor": after["cursor"], "coherence": {"unchanged_during_observation": coherent,
                "before": before, "after": after, "snapshot_isolation": False},
            "coverage": coverage}
    if "since" in arguments:
        data["changes"] = change_journal.read(arguments["since"], limit=200)
    response = state.result(call, data, f"Observed {len(records)} Maya nodes; image {image_status}", image_content=images)
    if images:
        response["content"] = images
    return response


def changes(arguments: dict[str, Any], call: state.CallState) -> dict[str, Any]:
    data = change_journal.read(arguments.get("cursor"), arguments.get("limit", 200))
    return state.result(call, data, f"Read {len(data['events'])} scoped Maya changes")


def frame(arguments: dict[str, Any], call: state.CallState) -> dict[str, Any]:
    view = _active_view()
    camera = view.getCamera().fullPathName()
    nodes = [state.resolve_node(item) for item in arguments["nodes"]]
    with state.undo_chunk(call, "Frame Maya nodes"):
        state.mark_mutated(call)
        cmds.viewFit(camera, *nodes, fitFactor=arguments.get("fit_factor", 0.8), animate=False)
        call.changes.append({"kind": "camera.framed", "camera": state.node_ref(camera)})
    state.bump_scene_revision()
    state.bump_context_revision()
    return state.result(call, {"camera": state.node_ref(camera)}, "Framed Maya nodes")


def attach_feedback(response: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Observation failure never changes a completed edit into a retryable failure."""
    from .dispatcher import invoke_tool
    feedback = invoke_tool("maya.observe", config)
    envelope = response["structuredContent"]
    observation = feedback["structuredContent"]
    envelope["data"]["observation"] = observation
    if not observation["ok"]:
        envelope["warnings"].append({"code": "POST_OBSERVATION_FAILED", "message": "Execution outcome is unchanged; inspect observation error and do not repeat completed edits"})
    images = [item for item in response.get("content", []) if item.get("type") == "image"]
    image_bytes = sum(len(item["data"]) for item in images)
    indexes = []
    for item in feedback.get("content", []):
        if item.get("type") != "image":
            continue
        if image_bytes + len(item["data"]) <= 8 * 1024 * 1024:
            indexes.append(len(images))
            images.append(item)
            image_bytes += len(item["data"])
        else:
            envelope["warnings"].append({"code": "POST_IMAGE_TRUNCATED", "message": "Image output budget exceeded"})
    envelope["data"]["observation_image_indices"] = indexes
    # Avoid losing the execution report to the native transport budget.
    if len(json.dumps(envelope, ensure_ascii=True)) > 3 * 1024 * 1024:
        envelope["data"]["observation"] = {"ok": observation["ok"], "data_truncated": True,
            "observation_id": observation.get("data", {}).get("observation_id")}
        envelope["warnings"].append({"code": "POST_OBSERVATION_TRUNCATED", "message": "Use a smaller observation scope"})
    response["content"] = images or [{"type": "text", "text": envelope["summary"] +
        "; post-action observation attached in structuredContent."}]
    return response


OBSERVATION_HANDLERS = {"maya.observe": observe, "maya.scene.changes": changes, "maya.viewport.frame": frame}
