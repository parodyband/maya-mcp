"""Viewport capture and screen/world grounding tools."""

from __future__ import annotations

import base64
import os
import tempfile
import uuid
from collections.abc import Callable
from typing import Any

import maya.api.OpenMaya as om
import maya.api.OpenMayaUI as omui
import maya.cmds as cmds

from . import state


def _active_view() -> omui.M3dView:
    if cmds.about(batch=True):
        raise state.ToolError(
            "VIEWPORT_UNAVAILABLE",
            "Viewport tools require interactive Maya and are unavailable in batch mode",
        )
    try:
        return omui.M3dView.active3dView()
    except RuntimeError as error:
        raise state.ToolError(
            "VIEWPORT_UNAVAILABLE",
            "Maya has no active 3D viewport",
        ) from error


def _matrix_values(matrix: om.MMatrix) -> list[float]:
    return [float(matrix[index]) for index in range(16)]


def _world_to_view(
    view: omui.M3dView, point: list[float] | tuple[float, float, float]
) -> dict[str, Any]:
    projected = view.worldToView(om.MPoint(*point))
    if len(projected) == 3:
        x, y, visible = projected
    else:
        x, y = projected
        visible = 0 <= x < view.portWidth() and 0 <= y < view.portHeight()
    return {
        "world": [float(point[0]), float(point[1]), float(point[2])],
        "screen": {"x": int(x), "y": int(y), "origin": "bottom-left"},
        "inside_view": bool(visible),
    }


def _camera_metadata(view: omui.M3dView) -> dict[str, Any]:
    camera_path = view.getCamera()
    camera_name = camera_path.fullPathName()
    camera_shape = camera_name
    try:
        if cmds.nodeType(camera_name) == "transform":
            shapes = cmds.listRelatives(
                camera_name, shapes=True, fullPath=True, type="camera"
            ) or []
            camera_shape = shapes[0] if shapes else camera_name
    except RuntimeError:
        pass
    return {
        "node": state.node_ref(camera_name),
        "shape": state.node_ref(camera_shape),
        "model_view_matrix": {
            "layout": "row-major",
            "values": _matrix_values(view.modelViewMatrix()),
        },
        "projection_matrix": {
            "layout": "row-major",
            "values": _matrix_values(view.projectionMatrix()),
        },
        "near_clip": state.safe_get_attr(f"{camera_shape}.nearClipPlane"),
        "far_clip": state.safe_get_attr(f"{camera_shape}.farClipPlane"),
    }


def viewport_capture(
    arguments: dict[str, Any], call: state.CallState
) -> dict[str, Any]:
    view = _active_view()
    try:
        cmds.refresh(force=True)
        image = om.MImage()
        view.readColorBuffer(image, True)
        source_width, source_height = image.getSize()
        requested_width = int(arguments.get("width", source_width))
        requested_height = int(arguments.get("height", source_height))
        if (requested_width, requested_height) != (source_width, source_height):
            image.resize(requested_width, requested_height, False)

        image_format = arguments.get("format", "png")
        suffix = ".jpg" if image_format == "jpg" else ".png"
        handle, path = tempfile.mkstemp(prefix="maya-mcp-", suffix=suffix)
        os.close(handle)
        try:
            image.writeToFile(path, image_format)
            with open(path, "rb") as stream:
                encoded = base64.b64encode(stream.read()).decode("ascii")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

        width, height = image.getSize()
        joint_projections = []
        all_joints = cmds.ls(type="joint", long=True) or []
        if arguments.get("include_joint_projections", True):
            scale_x = float(width) / float(source_width)
            scale_y = float(height) / float(source_height)
            for joint in all_joints[:500]:
                point = cmds.xform(
                    joint, query=True, worldSpace=True, translation=True
                )
                projection = _world_to_view(view, point)
                projection["screen"]["x"] = int(
                    round(projection["screen"]["x"] * scale_x)
                )
                projection["screen"]["y"] = int(
                    round(projection["screen"]["y"] * scale_y)
                )
                projection["inside_view"] = (
                    0 <= projection["screen"]["x"] < width
                    and 0 <= projection["screen"]["y"] < height
                )
                projection["node"] = state.node_ref(joint)
                joint_projections.append(projection)

        data = {
            "capture_id": f"capture:{uuid.uuid4().hex}",
            "resolution": {
                "width": int(width),
                "height": int(height),
                "source_width": int(source_width),
                "source_height": int(source_height),
            },
            "format": image_format,
            "mime_type": "image/jpeg" if image_format == "jpg" else "image/png",
            "coordinate_system": {
                "screen_origin": "bottom-left",
                "world_up_axis": cmds.upAxis(query=True, axis=True),
                "linear_unit": cmds.currentUnit(query=True, linear=True),
            },
            "time": {
                "value": cmds.currentTime(query=True),
                "unit": cmds.currentUnit(query=True, time=True),
            },
            "camera": _camera_metadata(view),
            "selection": state.selection_refs(),
            "joint_projections": joint_projections,
            "joint_projection_total": len(all_joints),
            "joint_projections_truncated": len(all_joints) > 500,
        }
        image_content = [
            {
                "type": "image",
                "data": encoded,
                "mimeType": data["mime_type"],
                "annotations": {
                    "audience": ["assistant", "user"],
                    "priority": 1.0,
                },
            }
        ]
        return state.result(
            call,
            data,
            f"Captured the Maya viewport at {width}x{height}",
            image_content=image_content,
        )
    except state.ToolError:
        raise
    except Exception as error:
        raise state.ToolError(
            "VIEWPORT_CAPTURE_FAILED",
            str(error),
            {"type": type(error).__name__},
        ) from error


def viewport_project(
    arguments: dict[str, Any], call: state.CallState
) -> dict[str, Any]:
    view = _active_view()
    projections = []
    for point in arguments.get("world_points", []):
        projections.append(_world_to_view(view, point))
    for selector in arguments.get("nodes", []):
        node = state.resolve_node(selector)
        point = cmds.xform(
            node, query=True, worldSpace=True, rotatePivot=True
        )
        projection = _world_to_view(view, point)
        projection["node"] = state.node_ref(node)
        projections.append(projection)

    rays = []
    for screen in arguments.get("screen_points", []):
        origin, direction = view.viewToWorld(int(screen[0]), int(screen[1]))
        rays.append(
            {
                "screen": {
                    "x": int(screen[0]),
                    "y": int(screen[1]),
                    "origin": "bottom-left",
                },
                "origin": [origin.x, origin.y, origin.z],
                "direction": [direction.x, direction.y, direction.z],
            }
        )
    return state.result(
        call,
        {
            "viewport": {
                "width": int(view.portWidth()),
                "height": int(view.portHeight()),
            },
            "projections": projections,
            "rays": rays,
            "camera": _camera_metadata(view),
        },
        f"Projected {len(projections)} world points and {len(rays)} screen rays",
    )


def viewport_pick(
    arguments: dict[str, Any], call: state.CallState
) -> dict[str, Any]:
    view = _active_view()
    x = int(arguments["x"])
    y = int(arguments["y"])
    radius = int(arguments.get("radius", 2))
    incoming = om.MGlobal.getActiveSelectionList()
    try:
        method = om.MGlobal.selectionMethod()
        if radius == 0:
            om.MGlobal.selectFromScreen(
                x,
                y,
                listAdjustment=om.MGlobal.kReplaceList,
                selectMethod=method,
            )
        else:
            om.MGlobal.selectFromScreen(
                x - radius,
                y - radius,
                x + radius,
                y + radius,
                listAdjustment=om.MGlobal.kReplaceList,
                selectMethod=method,
            )
        picked_strings = cmds.ls(selection=True, long=True, flatten=True) or []
    finally:
        om.MGlobal.setActiveSelectionList(incoming, om.MGlobal.kReplaceList)

    origin, direction = view.viewToWorld(x, y)
    records = []
    for item in picked_strings:
        node = item.split(".", 1)[0]
        record = {"node": state.node_ref(node)}
        if "." in item:
            record["component"] = item
        records.append(record)
    return state.result(
        call,
        {
            "screen": {"x": x, "y": y, "origin": "bottom-left", "radius": radius},
            "hits": records,
            "world_ray": {
                "origin": [origin.x, origin.y, origin.z],
                "direction": [direction.x, direction.y, direction.z],
            },
            "selection_preserved": True,
        },
        f"Picked {len(records)} Maya viewport hit(s)",
    )


VIEWPORT_HANDLERS: dict[
    str, Callable[[dict[str, Any], state.CallState], dict[str, Any]]
] = {
    "maya.viewport.capture": viewport_capture,
    "maya.viewport.project": viewport_project,
    "maya.viewport.pick": viewport_pick,
}
