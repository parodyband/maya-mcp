"""Viewport capture and screen/world grounding tools."""

from __future__ import annotations

import base64
import binascii
import json
import os
import tempfile
import uuid
from collections.abc import Callable
from typing import Any

import maya.api.OpenMaya as om
import maya.api.OpenMayaUI as omui
import maya.cmds as cmds

from . import state


_NATIVE_CAPTURE_SCHEMA_VERSION = 1
_NATIVE_BASE64_BUDGET_CHARS = 4 * 1024 * 1024
_MAX_COLOR_BASE64_CHARS = 8 * 1024 * 1024
_MAX_CAPTURE_DIMENSION = 2048
_DEPTH_FORMATS = {
    "kD24S8": (4, "depth24_stencil8"),
    "kD24X8": (4, "depth24_unused8"),
    "kD32_FLOAT": (4, "depth_float32"),
    "kR24G8": (4, "r24_g8"),
    "kR24X8": (4, "r24_unused8"),
}


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


def _view_to_world(
    view: omui.M3dView, x: int, y: int
) -> tuple[om.MPoint, om.MVector]:
    """Return a viewport ray using Maya 2027's output-argument API."""

    origin = om.MPoint()
    direction = om.MVector()
    view.viewToWorld(int(x), int(y), origin, direction)
    return origin, direction


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


def _native_contract_error(message: str, field: str) -> state.ToolError:
    return state.ToolError(
        "NATIVE_VIEWPORT_CAPTURE_INVALID",
        message,
        {"field": field},
    )


def _native_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _native_contract_error(
            "Native VP2 capture returned an invalid object field", field
        )
    return value


def _native_int(value: Any, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _native_contract_error(
            f"Native VP2 capture returned an invalid integer for {field}", field
        )
    return value


def _native_depth_capture(max_dimension: int) -> dict[str, Any]:
    if not 1 <= max_dimension <= 1024:
        raise state.ToolError(
            "INVALID_ARGUMENT",
            "depth_max_dimension must be between 1 and 1024",
        )
    requested = {
        "depth": True,
        "color": False,
        "object_id": False,
        "max_dimension": max_dimension,
    }
    request = json.dumps(requested, separators=(",", ":"))
    try:
        encoded_result = cmds.mayaMcpVp2Capture(request=request)
    except (AttributeError, RuntimeError) as error:
        raise state.ToolError(
            'NATIVE_VIEWPORT_CAPTURE_FAILED',
            str(error),
            {'type': type(error).__name__},
        ) from error
    try:
        result = json.loads(encoded_result)
    except (TypeError, json.JSONDecodeError) as error:
        raise state.ToolError(
            'NATIVE_VIEWPORT_CAPTURE_FAILED',
            'The native VP2 command returned invalid JSON',
        ) from error
    if not isinstance(result, dict):
        raise _native_contract_error(
            "The native VP2 command returned a non-object result", "$"
        )
    if result.get("schema_version") != _NATIVE_CAPTURE_SCHEMA_VERSION:
        raise _native_contract_error(
            "Native VP2 capture returned an unsupported schema version",
            "$.schema_version",
        )
    if result.get("ok") is not True:
        native_error = result.get("error") or {}
        raise state.ToolError(
            str(native_error.get("code") or "NATIVE_VIEWPORT_CAPTURE_FAILED"),
            str(native_error.get("message") or "Native VP2 depth capture failed"),
            {
                "retryable": bool(native_error.get("retryable", False)),
                "capabilities": result.get("capabilities", {}),
                "limits": result.get("limits", {}),
            },
        )

    if result.get("request") != requested:
        raise _native_contract_error(
            "Native VP2 capture did not echo the exact requested passes and limit",
            "$.request",
        )

    capture_source = _native_object(result.get("source"), "$.source")
    if capture_source.get("kind") != "active_viewport_2":
        raise _native_contract_error(
            "Native VP2 capture reported an unexpected source kind",
            "$.source.kind",
        )
    _native_int(
        capture_source.get("viewport_width"),
        "$.source.viewport_width",
        minimum=1,
    )
    _native_int(
        capture_source.get("viewport_height"),
        "$.source.viewport_height",
        minimum=1,
    )
    draw_api = _native_object(capture_source.get("draw_api"), "$.source.draw_api")
    if not isinstance(draw_api.get("name"), str) or not draw_api["name"]:
        raise _native_contract_error(
            "Native VP2 capture omitted the draw API name", "$.source.draw_api.name"
        )
    _native_int(draw_api.get("value"), "$.source.draw_api.value")

    limits = _native_object(result.get("limits"), "$.limits")
    if (
        _native_int(
            limits.get("hard_max_dimension"),
            "$.limits.hard_max_dimension",
            minimum=1,
        )
        != 1024
    ):
        raise _native_contract_error(
            "Native VP2 capture reported the wrong hard dimension limit",
            "$.limits.hard_max_dimension",
        )
    if (
        _native_int(
            limits.get("base64_budget_chars"),
            "$.limits.base64_budget_chars",
            minimum=1,
        )
        != _NATIVE_BASE64_BUDGET_CHARS
    ):
        raise _native_contract_error(
            "Native VP2 capture reported the wrong encoded payload budget",
            "$.limits.base64_budget_chars",
        )

    passes = _native_object(result.get("passes"), "$.passes")
    if set(passes) != {"depth"}:
        raise _native_contract_error(
            "Native VP2 capture returned passes that do not match the request",
            "$.passes",
        )
    depth = _native_object(passes.get("depth"), "$.passes.depth")
    source = _native_object(depth.get("source"), "$.passes.depth.source")
    sample = _native_object(depth.get("sample"), "$.passes.depth.sample")
    payload = _native_object(depth.get("payload"), "$.passes.depth.payload")

    source_width = _native_int(
        source.get("width"), "$.passes.depth.source.width", minimum=1
    )
    source_height = _native_int(
        source.get("height"), "$.passes.depth.source.height", minimum=1
    )
    raster_format = _native_object(
        source.get("raster_format"), "$.passes.depth.source.raster_format"
    )
    format_name = raster_format.get("name")
    if format_name not in _DEPTH_FORMATS:
        raise _native_contract_error(
            "Native VP2 depth used an unsupported raster format",
            "$.passes.depth.source.raster_format.name",
        )
    expected_stride, expected_layout = _DEPTH_FORMATS[str(format_name)]
    if raster_format.get("layout") != expected_layout:
        raise _native_contract_error(
            "Native VP2 depth layout does not match its raster format",
            "$.passes.depth.source.raster_format.layout",
        )
    source_stride = _native_int(
        raster_format.get("pixel_stride_bytes"),
        "$.passes.depth.source.raster_format.pixel_stride_bytes",
        minimum=1,
    )
    if source_stride != expected_stride:
        raise _native_contract_error(
            "Native VP2 depth pixel stride does not match its raster format",
            "$.passes.depth.source.raster_format.pixel_stride_bytes",
        )
    _native_int(
        raster_format.get("value"),
        "$.passes.depth.source.raster_format.value",
    )
    source_row_pitch = _native_int(
        source.get("row_pitch_bytes"),
        "$.passes.depth.source.row_pitch_bytes",
        minimum=1,
    )
    if source_row_pitch < source_width * source_stride:
        raise _native_contract_error(
            "Native VP2 depth source row pitch is smaller than one row",
            "$.passes.depth.source.row_pitch_bytes",
        )
    source_slice_pitch = _native_int(
        source.get("slice_pitch_bytes"),
        "$.passes.depth.source.slice_pitch_bytes",
        minimum=1,
    )
    minimum_slice_pitch = (
        (source_height - 1) * source_row_pitch
        + source_width * source_stride
    )
    if source_slice_pitch < minimum_slice_pitch:
        raise _native_contract_error(
            "Native VP2 depth source slice pitch is smaller than addressable pixels",
            "$.passes.depth.source.slice_pitch_bytes",
        )
    if source.get("row_order") != "renderer_native":
        raise _native_contract_error(
            "Native VP2 depth row order is not explicit",
            "$.passes.depth.source.row_order",
        )
    if source.get("byte_order") != "native":
        raise _native_contract_error(
            "Native VP2 depth byte order is not explicit",
            "$.passes.depth.source.byte_order",
        )
    _native_int(
        source.get("sample_count"),
        "$.passes.depth.source.sample_count",
    )
    array_slices = _native_int(
        source.get("array_slices"),
        "$.passes.depth.source.array_slices",
        minimum=1,
    )
    if array_slices != 1:
        raise _native_contract_error(
            "Native VP2 depth array targets are not supported",
            "$.passes.depth.source.array_slices",
        )
    if source.get("cube_map") is not False:
        raise _native_contract_error(
            "Native VP2 depth cube-map targets are not supported",
            "$.passes.depth.source.cube_map",
        )

    sample_width = _native_int(
        sample.get("width"), "$.passes.depth.sample.width", minimum=1
    )
    sample_height = _native_int(
        sample.get("height"), "$.passes.depth.sample.height", minimum=1
    )
    if (
        sample_width > source_width
        or sample_height > source_height
        or max(sample_width, sample_height) > max_dimension
    ):
        raise _native_contract_error(
            "Native VP2 depth sample dimensions are invalid or exceed the request",
            "$.passes.depth.sample",
        )
    sample_stride = _native_int(
        sample.get("pixel_stride_bytes"),
        "$.passes.depth.sample.pixel_stride_bytes",
        minimum=1,
    )
    if sample_stride != source_stride:
        raise _native_contract_error(
            "Native VP2 depth sample and source strides differ",
            "$.passes.depth.sample.pixel_stride_bytes",
        )
    sample_row_stride = _native_int(
        sample.get("row_stride_bytes"),
        "$.passes.depth.sample.row_stride_bytes",
        minimum=1,
    )
    if sample_row_stride != sample_width * sample_stride:
        raise _native_contract_error(
            "Native VP2 depth sample row stride is inconsistent",
            "$.passes.depth.sample.row_stride_bytes",
        )
    byte_count = _native_int(
        sample.get("byte_count"),
        "$.passes.depth.sample.byte_count",
        minimum=1,
    )
    if byte_count != sample_row_stride * sample_height:
        raise _native_contract_error(
            "Native VP2 depth byte count is inconsistent",
            "$.passes.depth.sample.byte_count",
        )
    if (
        sample.get("filter") != "nearest"
        or sample.get("source_row_order_preserved") is not True
    ):
        raise _native_contract_error(
            "Native VP2 depth sampling metadata is invalid",
            "$.passes.depth.sample",
        )

    if (
        payload.get("encoding") != "base64"
        or payload.get("media_type")
        != "application/vnd.autodesk.maya.render-target"
    ):
        raise _native_contract_error(
            "Native VP2 depth payload metadata is invalid",
            "$.passes.depth.payload",
        )
    raw_data = payload.get("data")
    if not isinstance(raw_data, str):
        raise _native_contract_error(
            "Native VP2 depth payload is missing",
            "$.passes.depth.payload.data",
        )
    if len(raw_data) > _NATIVE_BASE64_BUDGET_CHARS:
        raise _native_contract_error(
            "Native VP2 depth payload exceeded the encoded limit",
            "$.passes.depth.payload.data",
        )
    payload_chars = _native_int(
        payload.get("base64_chars"),
        "$.passes.depth.payload.base64_chars",
    )
    if payload_chars != len(raw_data):
        raise _native_contract_error(
            "Native VP2 depth encoded length does not match its metadata",
            "$.passes.depth.payload.base64_chars",
        )
    if (
        _native_int(limits.get("base64_chars"), "$.limits.base64_chars")
        != payload_chars
    ):
        raise _native_contract_error(
            "Native VP2 response encoded total does not match its payload",
            "$.limits.base64_chars",
        )
    try:
        decoded_size = len(base64.b64decode(raw_data, validate=True))
    except (ValueError, binascii.Error) as error:
        raise _native_contract_error(
            "Native VP2 depth payload is not valid base64",
            "$.passes.depth.payload.data",
        ) from error
    if decoded_size != byte_count:
        raise _native_contract_error(
            "Native VP2 depth decoded length does not match its metadata",
            "$.passes.depth.payload.data",
        )
    return result


def _capture_dimensions(
    arguments: dict[str, Any], source_width: int, source_height: int
) -> tuple[int, int]:
    requested_width = arguments.get("width")
    requested_height = arguments.get("height")
    if requested_width is None and requested_height is None:
        scale = min(
            1.0,
            float(_MAX_CAPTURE_DIMENSION)
            / float(max(source_width, source_height)),
        )
        width = max(1, int(round(source_width * scale)))
        height = max(1, int(round(source_height * scale)))
    elif requested_width is None:
        height = int(requested_height)
        width = max(1, int(round(source_width * height / source_height)))
    elif requested_height is None:
        width = int(requested_width)
        height = max(1, int(round(source_height * width / source_width)))
    else:
        width = int(requested_width)
        height = int(requested_height)
    if not (
        64 <= width <= _MAX_CAPTURE_DIMENSION
        and 64 <= height <= _MAX_CAPTURE_DIMENSION
    ):
        raise state.ToolError(
            "INVALID_CAPTURE_DIMENSIONS",
            "Viewport capture dimensions must each be between 64 and 2048 pixels",
            {
                "width": width,
                "height": height,
                "max_dimension": _MAX_CAPTURE_DIMENSION,
            },
        )
    return width, height


def _encoded_base64_chars(byte_count: int) -> int:
    return 4 * ((byte_count + 2) // 3)


def _native_capture_text_summary(native_capture: dict[str, Any]) -> dict[str, Any]:
    """Return native metadata without repeating binary payloads in text."""

    summary = {key: value for key, value in native_capture.items() if key != "passes"}
    summarized_passes: dict[str, Any] = {}
    for name, pass_data in (native_capture.get("passes") or {}).items():
        if not isinstance(pass_data, dict):
            continue
        pass_summary = {
            key: value for key, value in pass_data.items() if key != "payload"
        }
        payload = pass_data.get("payload")
        if isinstance(payload, dict):
            payload_summary = {
                key: value for key, value in payload.items() if key != "data"
            }
            payload_summary["data_omitted_from_text"] = True
            payload_summary["data_location"] = (
                f"structuredContent.data.native_capture.passes.{name}.payload.data"
            )
            pass_summary["payload"] = payload_summary
        summarized_passes[str(name)] = pass_summary
    summary["passes"] = summarized_passes
    return summary


def _redact_native_payload_from_text(
    response: dict[str, Any], native_capture: dict[str, Any]
) -> None:
    structured = response.get("structuredContent")
    if not isinstance(structured, dict):
        return
    text_structured = dict(structured)
    structured_data = structured.get("data")
    if isinstance(structured_data, dict):
        text_data = dict(structured_data)
        text_data["native_capture"] = _native_capture_text_summary(native_capture)
        text_structured["data"] = text_data
    encoded_text = json.dumps(
        text_structured, ensure_ascii=True, separators=(",", ":")
    )
    for content in response.get("content", []):
        if content.get("type") == "text":
            content["text"] = encoded_text


def viewport_capture(
    arguments: dict[str, Any], call: state.CallState
) -> dict[str, Any]:
    view = _active_view()
    try:
        cmds.refresh(force=True)
        image = om.MImage()
        view.readColorBuffer(image, True)
        native_capture = None
        if arguments.get('include_depth', False):
            native_capture = _native_depth_capture(
                int(arguments.get('depth_max_dimension', 512))
            )
        source_width, source_height = image.getSize()
        requested_width, requested_height = _capture_dimensions(
            arguments, int(source_width), int(source_height)
        )
        if (requested_width, requested_height) != (source_width, source_height):
            image.resize(requested_width, requested_height, False)

        image_format = arguments.get("format", "png")
        suffix = ".jpg" if image_format == "jpg" else ".png"
        handle, path = tempfile.mkstemp(prefix="maya-mcp-", suffix=suffix)
        os.close(handle)
        try:
            image.writeToFile(path, image_format)
            file_size = os.path.getsize(path)
            encoded_chars = _encoded_base64_chars(file_size)
            if encoded_chars > _MAX_COLOR_BASE64_CHARS:
                raise state.ToolError(
                    "VIEWPORT_CAPTURE_TOO_LARGE",
                    "Encoded viewport color image exceeds the MCP response limit",
                    {
                        "encoded_chars": encoded_chars,
                        "max_encoded_chars": _MAX_COLOR_BASE64_CHARS,
                        "file_bytes": file_size,
                        "format": image_format,
                        "resolution": {
                            "width": requested_width,
                            "height": requested_height,
                        },
                        "suggestion": (
                            "Request smaller width/height values or use JPEG"
                        ),
                    },
                )
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
            "limits": {
                "max_dimension": _MAX_CAPTURE_DIMENSION,
                "max_color_base64_chars": _MAX_COLOR_BASE64_CHARS,
                "color_base64_chars": len(encoded),
            },
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
            "channels": {
                "color": {
                    "included": True,
                    "representation": "MCP ImageContent",
                    "content_index": 0,
                },
                "depth": {
                    "supported": True,
                    "included": native_capture is not None,
                    "representation": (
                        "bounded raw VP2 render target in native_capture.passes.depth"
                        if native_capture is not None
                        else None
                    ),
                },
                "object_id": {
                    "supported": False,
                    "reason": (
                        "Maya does not expose a stable object-ID target through "
                        "the active VP2 capture path; use maya.viewport.scene_map "
                        "and maya.viewport.pick for identity grounding."
                    ),
                },
            },
        }
        if native_capture is not None:
            data["native_capture"] = native_capture
        image_content = [
            {
                "type": "image",
                "data": encoded,
                "mimeType": data["mime_type"],
            }
        ]
        response = state.result(
            call,
            data,
            f"Captured the Maya viewport at {width}x{height}",
            image_content=image_content,
        )
        # Keep the visible MCP result image-only. Some otherwise image-capable
        # hosts reject a mixed ImageContent + TextContent response even though
        # the protocol permits it. Capture metadata remains available through
        # structuredContent without duplicating the image's base64 payload.
        response["content"] = image_content
        return response
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
        origin, direction = _view_to_world(
            view, int(screen[0]), int(screen[1])
        )
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
    call.warnings.append(
        {
            "code": "SELECTION_CALLBACKS_MAY_RUN",
            "message": (
                "Viewport picking temporarily changes Maya's active selection; "
                "the original selection is restored, but SelectionChanged "
                "callbacks may run"
            ),
        }
    )
    try:
        # Maya's surface selection method is useful for shaded object picks,
        # but it does not reliably hit point components. Match Maya's own
        # component-selection behavior by using the wireframe method whenever
        # component mode is active.
        method = om.MGlobal.selectionMethod()
        if om.MGlobal.selectionMode() == om.MGlobal.kSelectComponentMode:
            method = om.MGlobal.kWireframeSelectMethod
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

    origin, direction = _view_to_world(view, x, y)
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
