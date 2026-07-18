"""Interactive Maya 2027 validation for MCP viewport grounding tools.

Launched by scripts/test-viewport-interactive.ps1 in a separate Maya process.
HTTP calls run on a worker while Maya's UI loop dispatches them on the main
thread through the native timer callback.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import maya.api.OpenMayaUI as omui
import maya.cmds as cmds
import maya.utils as maya_utils

PROTOCOL_VERSION = "2025-11-25"

_worker: threading.Thread | None = None
_outcome: dict[str, Any] | None = None
_deadline = 0.0
_evidence_dir: Path | None = None
_result_path: Path | None = None
_plugin_path: Path | None = None
_launch_authorized = False


class ValidationError(RuntimeError):
    """A failed interactive viewport assertion."""


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    os.replace(temporary, path)


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def _validate_launch_guard(run_id: str) -> tuple[Path, Path, Path]:
    """Authorize destructive test setup only for the isolated child launcher."""

    expected = os.environ.get("MAYA_MCP_VIEWPORT_RUN_ID", "")
    _assert(
        len(run_id) == 32
        and all(character in "0123456789abcdef" for character in run_id),
        "Interactive viewport validation requires a 32-character launch ID",
    )
    _assert(run_id == expected, "Interactive viewport launch ID does not match")

    evidence = Path(
        os.environ.get("MAYA_MCP_VIEWPORT_EVIDENCE_DIR", "")
    ).resolve()
    result = Path(os.environ.get("MAYA_MCP_VIEWPORT_RESULT", "")).resolve()
    maya_app = Path(os.environ.get("MAYA_APP_DIR", "")).resolve()
    local_app_data = Path(os.environ.get("LOCALAPPDATA", "")).resolve()
    plugin = Path(
        os.environ.get("MAYA_MCP_VIEWPORT_PLUGIN", "")
    ).resolve()

    _assert(evidence.name.endswith(f"-{run_id}"), "Evidence path is not run-scoped")
    _assert(result == evidence / "result.json", "Result path escaped evidence root")
    _assert(maya_app == evidence / "maya-app", "MAYA_APP_DIR is not isolated")
    _assert(
        local_app_data == evidence / "local-app-data",
        "LOCALAPPDATA is not isolated",
    )
    _assert(plugin.is_file(), f"Viewport test plug-in does not exist: {plugin}")
    return evidence, result, plugin


def _main_thread(function: Callable[..., Any], *arguments: Any) -> Any:
    return maya_utils.executeInMainThreadWithResult(lambda: function(*arguments))


class McpClient:
    """Minimal authenticated Streamable HTTP client for the test process."""

    def __init__(self, endpoint: str, token: str) -> None:
        self.endpoint = endpoint
        self.token = token
        self.session_id: str | None = None
        self.next_id = 1
        self.last_tool_result: dict[str, Any] | None = None

    def _request(
        self, payload: dict[str, Any], *, method: str = "POST"
    ) -> tuple[Any, Any]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Origin": "http://localhost",
        }
        if self.session_id:
            headers["MCP-Session-Id"] = self.session_id
            headers["MCP-Protocol-Version"] = PROTOCOL_VERSION
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8") if method == "POST" else None,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            body = response.read()
            return response.headers, json.loads(body) if body else None

    def initialize(self) -> dict[str, Any]:
        headers, response = self._request(
            {
                "jsonrpc": "2.0",
                "id": self.next_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "maya-mcp-interactive-viewport-test",
                        "version": "1.0",
                    },
                },
            }
        )
        self.next_id += 1
        _assert("error" not in response, f"MCP initialize failed: {response}")
        self.session_id = headers.get("MCP-Session-Id")
        _assert(bool(self.session_id), "MCP initialize returned no session ID")
        _assert(
            response["result"]["protocolVersion"] == PROTOCOL_VERSION,
            f"Unexpected MCP protocol response: {response}",
        )
        self._request(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
        )
        return response["result"]

    def list_tools(self) -> list[dict[str, Any]]:
        _, response = self._request(
            {
                "jsonrpc": "2.0",
                "id": self.next_id,
                "method": "tools/list",
                "params": {},
            }
        )
        self.next_id += 1
        _assert("error" not in response, f"tools/list failed: {response}")
        return response["result"]["tools"]

    def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        _, response = self._request(
            {
                "jsonrpc": "2.0",
                "id": self.next_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        self.next_id += 1
        _assert("error" not in response, f"{name} returned JSON-RPC error: {response}")
        result = response["result"]
        self.last_tool_result = result
        _assert(not result.get("isError"), f"{name} failed: {result}")
        structured = result.get("structuredContent")
        _assert(isinstance(structured, dict), f"{name} has no structured content")
        _assert(structured.get("ok") is True, f"{name} returned an invalid envelope")
        images = [
            item for item in result.get("content", []) if item.get("type") == "image"
        ]
        return structured, images

    def close(self) -> None:
        if not self.session_id:
            return
        try:
            self._request({}, method="DELETE")
        except (OSError, urllib.error.HTTPError):
            pass
        finally:
            self.session_id = None


def _aim_camera(camera: str, target: str) -> None:
    constraint = cmds.aimConstraint(
        target,
        camera,
        aimVector=(0.0, 0.0, -1.0),
        upVector=(0.0, 1.0, 0.0),
        worldUpType="scene",
    )[0]
    cmds.delete(constraint)


def _setup_scene() -> dict[str, Any]:
    cmds.file(new=True, force=True)
    panels = cmds.getPanel(type="modelPanel") or []
    _assert(bool(panels), "Interactive Maya created no model panel")
    focused = cmds.getPanel(withFocus=True)
    panel = focused if focused in panels else panels[-1]

    target = cmds.polyCube(
        name="mayaMcpViewportTarget", width=4.0, height=4.0, depth=4.0
    )[0]
    sentinel = cmds.spaceLocator(name="mayaMcpViewportSelectionSentinel")[0]
    cmds.xform(sentinel, worldSpace=True, translation=(100.0, 100.0, 100.0))
    cmds.select(clear=True)
    root_joint = cmds.joint(
        name="mayaMcpViewportRoot_JNT", position=(-1.0, -1.5, 0.0)
    )
    end_joint = cmds.joint(
        name="mayaMcpViewportEnd_JNT", position=(-1.0, 1.5, 0.0)
    )
    cmds.select(clear=True)

    perspective, perspective_shape = cmds.camera(name="mayaMcpViewportPerspective")
    cmds.xform(perspective, worldSpace=True, translation=(11.0, 8.0, 11.0))
    cmds.setAttr(f"{perspective_shape}.focalLength", 50.0)
    _aim_camera(perspective, target)
    orthographic, orthographic_shape = cmds.camera(
        name="mayaMcpViewportOrthographic", orthographic=True
    )
    cmds.xform(orthographic, worldSpace=True, translation=(10.0, 7.0, 10.0))
    cmds.setAttr(f"{orthographic_shape}.orthographicWidth", 9.0)
    _aim_camera(orthographic, target)

    cmds.modelEditor(
        panel,
        edit=True,
        rendererName="vp2Renderer",
        displayAppearance="smoothShaded",
        displayTextures=False,
        grid=False,
        headsUpDisplay=False,
        selectionHiliteDisplay=True,
        allObjects=True,
    )
    cmds.lookThru(panel, perspective)
    cmds.setFocus(panel)
    cmds.select(sentinel, replace=True)
    cmds.refresh(force=True)
    camera_position = cmds.xform(
        perspective, query=True, worldSpace=True, translation=True
    )
    vertex_components = cmds.ls(f"{target}.vtx[*]", flatten=True) or []
    _assert(bool(vertex_components), "Viewport target has no vertices")

    def camera_distance(component: str) -> float:
        position = cmds.xform(
            component, query=True, worldSpace=True, translation=True
        )
        return sum(
            (float(position[index]) - float(camera_position[index])) ** 2
            for index in range(3)
        )

    vertex_component = min(vertex_components, key=camera_distance)
    vertex = cmds.xform(
        vertex_component, query=True, worldSpace=True, translation=True
    )
    return {
        "panel": panel,
        "target": cmds.ls(target, long=True)[0],
        "target_short": target,
        "sentinel": cmds.ls(sentinel, long=True)[0],
        "perspective": cmds.ls(perspective, long=True)[0],
        "orthographic": cmds.ls(orthographic, long=True)[0],
        "root_joint": cmds.ls(root_joint, long=True)[0],
        "end_joint": cmds.ls(end_joint, long=True)[0],
        "vertex": [float(value) for value in vertex],
        "vertex_component": (
            cmds.ls(vertex_component, long=True, flatten=True) or [vertex_component]
        )[0],
    }


def _configure_view(
    panel: str,
    camera: str,
    appearance: str,
    selection: str,
    component_mode: bool = False,
) -> list[str]:
    cmds.lookThru(panel, camera)
    cmds.modelEditor(panel, edit=True, displayAppearance=appearance)
    cmds.setFocus(panel)
    if component_mode:
        cmds.selectMode(component=True)
        cmds.selectType(allComponents=False)
        cmds.selectType(polymeshVertex=True)
        cmds.hilite(selection.split(".", 1)[0], replace=True)
    else:
        cmds.selectMode(object=True)
    cmds.select(selection, replace=True)
    cmds.refresh(force=True)
    return cmds.ls(selection=True, long=True, flatten=True) or []


def _set_isolate(panel: str, target: str, enabled: bool, sentinel: str) -> None:
    if enabled:
        cmds.selectMode(object=True)
        cmds.select(target, replace=True)
        cmds.isolateSelect(panel, state=True)
        cmds.isolateSelect(panel, addSelected=True)
    else:
        cmds.isolateSelect(panel, state=False)
        cmds.select(sentinel, replace=True)
    cmds.setFocus(panel)
    cmds.refresh(force=True)


def _set_playback(enabled: bool) -> None:
    if enabled:
        cmds.playbackOptions(minTime=1, maxTime=12)
        cmds.currentTime(1)
        cmds.play(forward=True)
    else:
        cmds.play(state=False)
    cmds.refresh(force=True)


def _selection() -> list[str]:
    return cmds.ls(selection=True, long=True, flatten=True) or []


def _panel_metrics(panel: str) -> dict[str, Any]:
    view = omui.M3dView.active3dView()
    metrics: dict[str, Any] = {
        "panel": panel,
        "port_width": int(view.portWidth()),
        "port_height": int(view.portHeight()),
        "control_width": int(cmds.control(panel, query=True, width=True)),
        "control_height": int(cmds.control(panel, query=True, height=True)),
    }
    try:
        import maya.OpenMayaUI as omui_legacy
        from PySide6 import QtWidgets
        from shiboken6 import wrapInstance

        pointer = omui_legacy.MQtUtil.findControl(panel)
        if pointer:
            widget = wrapInstance(int(pointer), QtWidgets.QWidget)
            metrics["device_pixel_ratio"] = float(widget.devicePixelRatioF())
    except (ImportError, RuntimeError, TypeError):
        metrics["device_pixel_ratio"] = None
    try:
        metrics["renderer"] = cmds.modelEditor(panel, query=True, rendererName=True)
        metrics["render_override"] = cmds.modelEditor(
            panel, query=True, rendererOverrideName=True
        )
    except RuntimeError:
        metrics["renderer"] = None
        metrics["render_override"] = None
    return metrics


def _camera_name(data: dict[str, Any]) -> str:
    for key in ("shape", "node"):
        reference = data["camera"].get(key, {})
        if isinstance(reference, dict):
            name = reference.get("long_name") or reference.get("name")
            if name:
                return str(name)
    return ""


def _decoded_color_stats(binary: bytes) -> dict[str, Any]:
    """Sample decoded pixels, not compressed bytes, for a useful image gate."""

    from PySide6.QtGui import QImage

    image = QImage.fromData(binary)
    _assert(not image.isNull(), "Viewport image could not be decoded")
    width = image.width()
    height = image.height()
    _assert(width > 0 and height > 0, "Decoded viewport image is empty")
    step_x = max(1, width // 32)
    step_y = max(1, height // 32)
    xs = sorted({*range(0, width, step_x), width - 1})
    ys = sorted({*range(0, height, step_y), height - 1})
    colors = []
    luminances = []
    for y in ys:
        for x in xs:
            color = image.pixelColor(x, y)
            rgb = (color.red(), color.green(), color.blue())
            colors.append(rgb)
            luminances.append(
                0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
            )
    unique_colors = len(set(colors))
    channel_range = max(
        max(color[channel] for color in colors)
        - min(color[channel] for color in colors)
        for channel in range(3)
    )
    mean = sum(luminances) / len(luminances)
    variance = sum((value - mean) ** 2 for value in luminances) / len(
        luminances
    )
    _assert(unique_colors >= 4, "Decoded viewport color image is nearly constant")
    _assert(channel_range >= 8, "Decoded viewport color range is too small")
    _assert(variance >= 1.0, "Decoded viewport luminance variance is too small")
    return {
        "sample_count": len(colors),
        "unique_rgb": unique_colors,
        "max_channel_range": channel_range,
        "luminance_variance": round(variance, 3),
    }


def _save_capture(
    client: McpClient,
    evidence_dir: Path,
    label: str,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    structured, images = client.call_tool("maya.viewport.capture", arguments)
    _assert(len(images) == 1, f"{label} capture returned {len(images)} images")
    data = structured["data"]
    image = images[0]
    encoded_image = image.get("data", "")
    _assert(isinstance(encoded_image, str), f"{label} image data is not text")
    limits = data.get("limits", {})
    _assert(
        int(limits.get("max_dimension", 0)) == 2048,
        f"{label} omitted the color dimension limit",
    )
    _assert(
        int(limits.get("max_color_base64_chars", 0)) == 8 * 1024 * 1024,
        f"{label} omitted the encoded color limit",
    )
    _assert(
        int(limits.get("color_base64_chars", -1)) == len(encoded_image),
        f"{label} encoded color length metadata is inconsistent",
    )
    _assert(
        len(encoded_image) <= int(limits["max_color_base64_chars"]),
        f"{label} exceeded the encoded color response limit",
    )
    binary = base64.b64decode(encoded_image, validate=True)
    mime = image.get("mimeType")
    _assert(
        mime == data["mime_type"],
        f"{label} MIME mismatch: {mime} != {data['mime_type']}",
    )
    if mime == "image/png":
        _assert(binary.startswith(b"\x89PNG\r\n\x1a\n"), f"{label} is not a PNG")
        suffix = ".png"
    elif mime == "image/jpeg":
        _assert(binary.startswith(b"\xff\xd8\xff"), f"{label} is not a JPEG")
        suffix = ".jpg"
    else:
        raise ValidationError(f"{label} returned unsupported MIME type {mime}")
    _assert(len(binary) > 128, f"{label} image is unexpectedly small")
    color_stats = _decoded_color_stats(binary)
    path = evidence_dir / f"{label}{suffix}"
    path.write_bytes(binary)
    camera = data["camera"]
    _assert(
        len(camera["model_view_matrix"]["values"]) == 16,
        "Invalid model-view matrix",
    )
    _assert(
        len(camera["projection_matrix"]["values"]) == 16,
        "Invalid projection matrix",
    )
    return data, {
        "path": str(path),
        "bytes": len(binary),
        "sha256": hashlib.sha256(binary).hexdigest(),
        "mime_type": mime,
        "resolution": data["resolution"],
        "camera": _camera_name(data),
        "decoded_color": color_stats,
    }


def _validate_ray(ray: dict[str, Any], label: str) -> None:
    origin = ray["origin"]
    direction = ray["direction"]
    _assert(len(origin) == 3 and len(direction) == 3, f"{label} ray is not 3D")
    _assert(
        all(math.isfinite(float(value)) for value in origin + direction),
        f"{label} ray is non-finite",
    )
    length = math.sqrt(sum(float(value) ** 2 for value in direction))
    _assert(
        0.99 <= length <= 1.01,
        f"{label} ray direction is not normalized: {length}",
    )


def _validate_pick(
    client: McpClient,
    scene: dict[str, Any],
    label: str,
    appearance: str,
    world_point: list[float],
    component_mode: bool,
) -> dict[str, Any]:
    incoming_item = (
        scene["vertex_component"] if component_mode else scene["sentinel"]
    )
    incoming = _main_thread(
        _configure_view,
        scene["panel"],
        scene["perspective"],
        appearance,
        incoming_item,
        component_mode,
    )
    projected, _ = client.call_tool(
        "maya.viewport.project", {"world_points": [world_point]}
    )
    point = projected["data"]["projections"][0]
    _assert(point["inside_view"], f"{label} target point is outside the viewport")
    screen = point["screen"]
    picked, _ = client.call_tool(
        "maya.viewport.pick",
        {
            "x": screen["x"],
            "y": screen["y"],
            "radius": 28 if component_mode else 18,
        },
    )
    data = picked["data"]
    outgoing = _main_thread(_selection)
    _assert(data["selection_preserved"] is True, f"{label} preservation flag is false")
    _assert(outgoing == incoming, f"{label} changed selection: {incoming} -> {outgoing}")
    _assert(bool(data["hits"]), f"{label} returned no viewport hit")
    hit_names = [
        hit["node"].get("long_name", hit["node"].get("name", ""))
        for hit in data["hits"]
    ]
    _assert(
        any(scene["target_short"] in name for name in hit_names),
        f"{label} did not hit {scene['target_short']}: {hit_names}",
    )
    if component_mode:
        _assert(
            any("component" in hit for hit in data["hits"]),
            f"{label} did not return a component",
        )
    _validate_ray(data["world_ray"], label)
    return {
        "screen": data["screen"],
        "hit_count": len(data["hits"]),
        "hit_names": hit_names,
        "component_mode": component_mode,
        "selection_before": incoming,
        "selection_after": outgoing,
    }


def _native_capture_command_available() -> bool:
    return callable(getattr(cmds, "mayaMcpVp2Capture", None))


def _invoke_native_capture(request: dict[str, Any]) -> dict[str, Any]:
    encoded = cmds.mayaMcpVp2Capture(
        request=json.dumps(request, separators=(",", ":"))
    )
    try:
        result = json.loads(encoded)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValidationError("mayaMcpVp2Capture returned invalid JSON") from error
    _assert(isinstance(result, dict), "mayaMcpVp2Capture returned a non-object")
    return result


def _validate_native_depth(
    result: dict[str, Any], max_dimension: int, evidence_path: Path
) -> dict[str, Any]:
    _assert(result.get("schema_version") == 1, "Native capture schema mismatch")
    _assert(result.get("ok") is True, f"Native VP2 capture failed: {result}")
    request = result["request"]
    _assert(
        request
        == {
            "depth": True,
            "color": False,
            "object_id": False,
            "max_dimension": max_dimension,
        },
        f"Native capture echoed the wrong request: {request}",
    )
    source_view = result["source"]
    _assert(source_view["kind"] == "active_viewport_2", "Wrong VP2 source kind")
    _assert(
        int(source_view["viewport_width"]) > 0
        and int(source_view["viewport_height"]) > 0,
        "Native capture reported an invalid viewport size",
    )

    capabilities = result["capabilities"]
    _assert(capabilities["depth"]["supported"] is True, "Depth not supported")
    _assert(
        capabilities["object_id"]["supported"] is False,
        "Object-ID capability must remain explicitly unsupported",
    )
    limits = result["limits"]
    _assert(int(limits["hard_max_dimension"]) == 1024, "Wrong dimension limit")
    _assert(
        int(limits["base64_budget_chars"]) == 4 * 1024 * 1024,
        "Wrong native payload budget",
    )

    passes = result["passes"]
    _assert("depth" in passes and "color" not in passes, "Wrong native passes")
    depth = passes["depth"]
    source = depth["source"]
    sample = depth["sample"]
    payload = depth["payload"]
    source_width = int(source["width"])
    source_height = int(source["height"])
    source_stride = int(source["raster_format"]["pixel_stride_bytes"])
    sample_width = int(sample["width"])
    sample_height = int(sample["height"])
    sample_stride = int(sample["pixel_stride_bytes"])
    byte_count = int(sample["byte_count"])
    _assert(source_width > 0 and source_height > 0, "Invalid depth source size")
    _assert(source_stride > 0 and sample_stride == source_stride, "Invalid stride")
    _assert(
        0 < sample_width <= source_width and 0 < sample_height <= source_height,
        "Native depth sample was upscaled or has invalid dimensions",
    )
    _assert(
        max(sample_width, sample_height) <= max_dimension,
        "Native depth sample exceeded max_dimension",
    )
    _assert(
        int(sample["row_stride_bytes"]) == sample_width * sample_stride,
        "Native depth row stride is inconsistent",
    )
    _assert(
        byte_count == sample_width * sample_height * sample_stride,
        "Native depth byte count is inconsistent",
    )
    _assert(
        int(source["row_pitch_bytes"]) >= source_width * source_stride,
        "Native depth source row pitch is too small",
    )
    _assert(sample["filter"] == "nearest", "Unexpected native sample filter")
    _assert(
        sample["source_row_order_preserved"] is True,
        "Native depth row order was not preserved",
    )
    _assert(payload["encoding"] == "base64", "Wrong native payload encoding")
    _assert(
        payload["media_type"]
        == "application/vnd.autodesk.maya.render-target",
        "Wrong native payload media type",
    )
    encoded = payload["data"]
    _assert(isinstance(encoded, str), "Native depth payload is not text")
    _assert(
        int(payload["base64_chars"]) == len(encoded),
        "Native base64 length metadata is inconsistent",
    )
    _assert(
        int(limits["base64_chars"]) == len(encoded),
        "Native response base64 total is inconsistent",
    )
    binary = base64.b64decode(encoded, validate=True)
    _assert(len(binary) == byte_count, "Decoded native depth size is inconsistent")
    pixels = {
        binary[offset : offset + sample_stride]
        for offset in range(0, len(binary), sample_stride)
    }
    _assert(
        len(pixels) >= 2,
        "Native depth payload is constant and contains no scene/background separation",
    )
    evidence_path.write_bytes(binary)
    return {
        "status": "verified",
        "path": str(evidence_path),
        "bytes": len(binary),
        "sha256": hashlib.sha256(binary).hexdigest(),
        "source": [source_width, source_height],
        "sample": [sample_width, sample_height],
        "unique_pixel_values": len(pixels),
        "raster_format": source["raster_format"],
        "draw_api": source_view["draw_api"],
    }


def _validate_depth_grounding(
    native_capture: dict[str, Any], scene_map: dict[str, Any]
) -> dict[str, Any]:
    """Confirm the mapped target and viewport background differ in depth."""

    depth = native_capture["passes"]["depth"]
    sample = depth["sample"]
    payload = depth["payload"]
    binary = base64.b64decode(payload["data"], validate=True)
    width = int(sample["width"])
    height = int(sample["height"])
    stride = int(sample["pixel_stride_bytes"])
    objects = scene_map["objects"]
    _assert(len(objects) == 1, "Depth grounding requires one mapped target")
    box = objects[0]["screen_bounds"]["top_left"]
    center_x = (float(box["min"][0]) + float(box["max"][0])) * 0.5
    center_y = (float(box["min"][1]) + float(box["max"][1])) * 0.5
    map_width = int(scene_map["resolution"]["width"])
    map_height = int(scene_map["resolution"]["height"])
    sample_x = min(width - 1, max(0, int(center_x * width / map_width)))
    sample_y_top = min(
        height - 1, max(0, int(center_y * height / map_height))
    )

    def pixel(x: int, y: int) -> bytes:
        offset = (y * width + x) * stride
        return binary[offset : offset + stride]

    corners = [
        pixel(1 if width > 2 else 0, 1 if height > 2 else 0),
        pixel(max(0, width - 2), 1 if height > 2 else 0),
        pixel(1 if width > 2 else 0, max(0, height - 2)),
        pixel(max(0, width - 2), max(0, height - 2)),
    ]
    background = max(set(corners), key=corners.count)
    background_agreement = corners.count(background)
    _assert(
        background_agreement >= 2,
        "Viewport corners do not establish a stable background depth",
    )
    target_values: set[bytes] = set()
    for base_y in (sample_y_top, height - 1 - sample_y_top):
        for delta_y in (-1, 0, 1):
            for delta_x in (-1, 0, 1):
                target_values.add(
                    pixel(
                        min(width - 1, max(0, sample_x + delta_x)),
                        min(height - 1, max(0, base_y + delta_y)),
                    )
                )
    foreground = [value for value in target_values if value != background]
    _assert(
        bool(foreground),
        "Mapped target depth does not differ from the viewport background",
    )
    return {
        "status": "verified",
        "mapped_node": objects[0]["node"]["long_name"],
        "sample_pixel": [sample_x, sample_y_top],
        "background_corner_agreement": background_agreement,
        "target_value_count": len(target_values),
        "foreground_value_count": len(foreground),
        "background_value_hex": background.hex(),
    }


def _validate_object_id_rejection(result: dict[str, Any]) -> dict[str, Any]:
    _assert(result.get("schema_version") == 1, "Object-ID schema mismatch")
    _assert(result.get("ok") is False, "Native object-ID unexpectedly succeeded")
    _assert(
        result.get("error", {}).get("code") == "UNSUPPORTED_PASS",
        f"Unexpected object-ID error: {result}",
    )
    _assert(
        result.get("capabilities", {}).get("object_id", {}).get("supported")
        is False,
        "Object-ID rejection omitted its unsupported capability",
    )
    return {"status": "verified_unsupported", "error": result["error"]}


def _maya_node_existence(names: list[str]) -> dict[str, bool]:
    return {name: bool(cmds.objExists(name)) for name in names}


def _validate_rig_preview_lifecycle(client: McpClient) -> dict[str, Any]:
    """Exercise transient review/cancel and permanent accept/undo over MCP."""

    review_create, _ = client.call_tool(
        "maya.rig.preview",
        {
            "action": "create",
            "name": "Interactive Review Preview",
            "joints": [
                {
                    "id": "reviewRoot",
                    "name": "mayaMcpInteractiveReviewRoot_JNT",
                    "position": [-3.0, 0.0, 0.0],
                }
            ],
        },
    )
    handle_v1 = review_create["data"]["handle"]
    preview_root_v1 = review_create["data"]["grouping"]["root"]["long_name"]
    _assert(
        _main_thread(lambda: bool(cmds.objExists(preview_root_v1))),
        "Rig preview create did not produce its transient root",
    )
    review_query, _ = client.call_tool(
        "maya.rig.preview", {"action": "query", "handle": handle_v1}
    )
    _assert(
        review_query["data"]["handle"] == handle_v1,
        "Rig preview query returned the wrong handle",
    )
    review_update, _ = client.call_tool(
        "maya.rig.preview",
        {
            "action": "update",
            "handle": handle_v1,
            "joint_color": [0.2, 1.0, 0.3],
            "control_color": [0.8, 0.2, 1.0],
        },
    )
    handle_v2 = review_update["data"]["handle"]
    _assert(
        handle_v2["preview_id"] == handle_v1["preview_id"]
        and handle_v2["revision"] == handle_v1["revision"] + 1,
        "Rig preview update did not advance the handle revision",
    )
    preview_root_v2 = review_update["data"]["grouping"]["root"]["long_name"]
    cancelled, _ = client.call_tool(
        "maya.rig.preview", {"action": "cancel", "handle": handle_v2}
    )
    _assert(
        cancelled["data"]["status"] == "cancelled",
        "Rig preview cancel returned the wrong status",
    )
    _assert(
        not _main_thread(lambda: bool(cmds.objExists(preview_root_v2))),
        "Rig preview cancel left its transient root in the scene",
    )

    output_names = [
        "mayaMcpInteractiveAcceptRoot_JNT",
        "mayaMcpInteractiveAcceptTip_JNT",
        "mayaMcpInteractiveAcceptRoot_ZERO",
        "mayaMcpInteractiveAcceptRoot_CTRL",
    ]
    accept_create, _ = client.call_tool(
        "maya.rig.preview",
        {
            "action": "create",
            "name": "Interactive Accept Preview",
            "joints": [
                {
                    "id": "acceptRoot",
                    "name": output_names[0],
                    "position": [3.0, -1.0, 0.0],
                },
                {
                    "id": "acceptTip",
                    "name": output_names[1],
                    "position": [3.0, 2.0, 0.0],
                    "parent_id": "acceptRoot",
                },
            ],
            "controls": [
                {
                    "id": "acceptControl",
                    "name": output_names[3],
                    "offset_name": output_names[2],
                    "target_joint_id": "acceptRoot",
                    "shape": "circle",
                    "size": 1.25,
                }
            ],
        },
    )
    accept_handle = accept_create["data"]["handle"]
    accepted, _ = client.call_tool(
        "maya.rig.preview",
        {
            "action": "accept",
            "handle": accept_handle,
            "if_scene_revision": accept_create["revisions"]["scene_after"],
        },
    )
    _assert(
        accepted["data"]["status"] == "accepted",
        "Rig preview accept returned the wrong status",
    )
    _assert(
        accepted["undo"] == {
            "available": True,
            "label": "Accept rig preview",
        },
        "Rig preview accept did not expose one undoable transaction",
    )
    _assert(
        accepted["data"]["output"]["counts"] == {"joints": 2, "controls": 1},
        "Rig preview accept returned unexpected output counts",
    )
    exists_after_accept = _main_thread(_maya_node_existence, output_names)
    _assert(
        all(exists_after_accept.values()),
        f"Rig preview accepted outputs are missing: {exists_after_accept}",
    )
    _main_thread(cmds.undo)
    exists_after_undo = _main_thread(_maya_node_existence, output_names)
    _assert(
        not any(exists_after_undo.values()),
        f"Undo left accepted rig outputs behind: {exists_after_undo}",
    )
    return {
        "status": "verified",
        "review": {
            "created_revision": handle_v1["revision"],
            "updated_revision": handle_v2["revision"],
            "cancelled": True,
        },
        "accept": {
            "counts": accepted["data"]["output"]["counts"],
            "undo_label": accepted["undo"]["label"],
            "outputs_removed_by_undo": True,
        },
    }


def _run_validation(
    endpoint: str,
    token: str,
    plugin_version: str,
    scene: dict[str, Any],
    evidence_dir: Path,
) -> dict[str, Any]:
    started = _utc_now()
    client = McpClient(endpoint, token)
    captures: dict[str, Any] = {}
    checks: dict[str, Any] = {}
    try:
        initialized = client.initialize()
        tools = client.list_tools()
        names = {tool["name"] for tool in tools}
        required = {
            "maya.viewport.capture",
            "maya.viewport.scene_map",
            "maya.viewport.project",
            "maya.viewport.pick",
            "maya.rig.preview",
        }
        _assert(required.issubset(names), f"Missing viewport tools: {required - names}")
        capture_tool = next(
            tool for tool in tools if tool["name"] == "maya.viewport.capture"
        )
        capture_properties = capture_tool.get("inputSchema", {}).get(
            "properties", {}
        )
        mcp_depth_available = {
            "include_depth",
            "depth_max_dimension",
        }.issubset(capture_properties)
        _assert(
            capture_properties.get("width", {}).get("maximum") == 2048
            and capture_properties.get("height", {}).get("maximum") == 2048,
            "Viewport capture schema does not expose the 2048 color limit",
        )
        _assert(
            mcp_depth_available,
            "Release viewport capture must expose native depth arguments",
        )
        native_command_available = _main_thread(
            _native_capture_command_available
        )
        _assert(
            native_command_available,
            "Release viewport validation requires mayaMcpVp2Capture",
        )
        _main_thread(
            _configure_view,
            scene["panel"],
            scene["perspective"],
            "smoothShaded",
            scene["sentinel"],
            False,
        )
        native_arguments: dict[str, Any] = {
            "format": "png",
            "include_depth": True,
            "depth_max_dimension": 256,
        }
        native, captures["perspective_native_png"] = _save_capture(
            client,
            evidence_dir,
            "perspective-native",
            native_arguments,
        )
        direct_depth = _main_thread(
            _invoke_native_capture,
            {
                "depth": True,
                "color": False,
                "object_id": False,
                "max_dimension": 256,
            },
        )
        checks["native_vp2_command_depth"] = _validate_native_depth(
            direct_depth,
            256,
            evidence_dir / "native-command-depth.bin",
        )
        object_id = _main_thread(
            _invoke_native_capture,
            {
                "depth": False,
                "color": False,
                "object_id": True,
                "max_dimension": 64,
            },
        )
        checks["native_vp2_object_id"] = _validate_object_id_rejection(
            object_id
        )
        _assert(
            native["channels"]["depth"]["included"] is True,
            "MCP capture did not mark requested depth as included",
        )
        _assert(
            native["channels"]["object_id"]["supported"] is False,
            "MCP capture did not report object-ID as unsupported",
        )
        checks["mcp_native_depth"] = _validate_native_depth(
            native["native_capture"],
            256,
            evidence_dir / "mcp-native-depth.bin",
        )
        raw_depth = native["native_capture"]["passes"]["depth"]["payload"]["data"]
        result_content = (client.last_tool_result or {}).get("content", [])
        text_items = [
            item.get("text", "")
            for item in result_content
            if item.get("type") == "text"
        ]
        _assert(bool(text_items), "Depth capture omitted its text fallback")
        _assert(
            all(raw_depth not in item for item in text_items),
            "Native depth payload was duplicated into the text fallback",
        )
        _assert(
            any("data_omitted_from_text" in item for item in text_items),
            "Depth text fallback did not explain payload redaction",
        )
        scene_map_envelope, _ = client.call_tool(
            "maya.viewport.scene_map",
            {
                "nodes": [scene["target"]],
                "width": native["resolution"]["source_width"],
                "height": native["resolution"]["source_height"],
                "max_nodes": 4,
                "max_candidates": 4,
            },
        )
        scene_map = scene_map_envelope["data"]
        _assert(
            scene_map["resolution"]["width"]
            == native["resolution"]["source_width"]
            and scene_map["resolution"]["height"]
            == native["resolution"]["source_height"],
            "Scene map and native capture resolutions differ",
        )
        _assert(
            len(scene_map["objects"]) == 1,
            f"Scene map did not return exactly the target: {scene_map}",
        )
        mapped_target = scene_map["objects"][0]
        _assert(
            mapped_target["node"]["long_name"] == scene["target"],
            "Scene map returned the wrong canonical target",
        )
        _assert("mesh" in mapped_target["types"], "Target scene-map type is wrong")
        top_left_box = mapped_target["screen_bounds"]["top_left"]
        pivot = mapped_target["pivot"]["screen_top_left"]
        _assert(
            top_left_box["min"][0] <= pivot[0] <= top_left_box["max"][0]
            and top_left_box["min"][1] <= pivot[1] <= top_left_box["max"][1],
            "Mapped target pivot is outside its conservative screen bounds",
        )
        checks["scene_map_correlation"] = {
            "status": "verified",
            "node": mapped_target["node"],
            "screen_bounds": mapped_target["screen_bounds"],
            "pivot": mapped_target["pivot"],
            "camera_depth": mapped_target["camera_depth"],
        }
        checks["mcp_depth_grounding"] = _validate_depth_grounding(
            native["native_capture"], scene_map
        )
        resized, captures["perspective_resized_png"] = _save_capture(
            client,
            evidence_dir,
            "perspective-resized",
            {"width": 640, "height": 360, "format": "png"},
        )
        jpeg, captures["perspective_jpeg"] = _save_capture(
            client,
            evidence_dir,
            "perspective-jpeg",
            {"width": 512, "height": 320, "format": "jpg"},
        )
        _assert(
            resized["resolution"]["width"] == 640
            and resized["resolution"]["height"] == 360,
            f"Resized PNG has wrong resolution: {resized['resolution']}",
        )
        _assert(
            jpeg["resolution"]["width"] == 512
            and jpeg["resolution"]["height"] == 320,
            f"JPEG has wrong resolution: {jpeg['resolution']}",
        )
        _assert(
            scene["perspective"].split("|")[-1] in _camera_name(native),
            f"Perspective capture reported the wrong camera: {_camera_name(native)}",
        )

        native_joints = {
            item["node"]["node_id"]: item for item in native["joint_projections"]
        }
        resized_joints = {
            item["node"]["node_id"]: item for item in resized["joint_projections"]
        }
        shared = native_joints.keys() & resized_joints.keys()
        _assert(bool(shared), "No shared joint projection metadata")
        scale_x = 640.0 / float(native["resolution"]["source_width"])
        scale_y = 360.0 / float(native["resolution"]["source_height"])
        max_error = 0.0
        for node_id in shared:
            source = native_joints[node_id]["screen"]
            target = resized_joints[node_id]["screen"]
            error = max(
                abs(float(target["x"]) - round(float(source["x"]) * scale_x)),
                abs(float(target["y"]) - round(float(source["y"]) * scale_y)),
            )
            max_error = max(max_error, error)
        _assert(max_error <= 2.0, f"Resized joint projection error is {max_error}px")
        checks["resized_joint_projection_max_error_px"] = max_error

        _main_thread(
            _configure_view,
            scene["panel"],
            scene["orthographic"],
            "smoothShaded",
            scene["sentinel"],
            False,
        )
        ortho, captures["orthographic_png"] = _save_capture(
            client,
            evidence_dir,
            "orthographic",
            {"width": 640, "height": 360, "format": "png"},
        )
        _assert(
            scene["orthographic"].split("|")[-1] in _camera_name(ortho),
            f"Orthographic capture reported the wrong camera: {_camera_name(ortho)}",
        )
        _main_thread(
            _configure_view,
            scene["panel"],
            scene["perspective"],
            "smoothShaded",
            scene["sentinel"],
            False,
        )
        projected, _ = client.call_tool(
            "maya.viewport.project",
            {
                "world_points": [[0.0, 0.0, 0.0]],
                "nodes": [scene["target"]],
                "screen_points": [[10, 10]],
            },
        )
        data = projected["data"]
        _assert(len(data["projections"]) == 2, "Projection count mismatch")
        _assert(len(data["rays"]) == 1, "Screen ray count mismatch")
        _assert(
            all(item["inside_view"] for item in data["projections"]),
            "Target projection is outside the viewport",
        )
        _validate_ray(data["rays"][0], "project")

        metrics = _main_thread(_panel_metrics, scene["panel"])
        viewport = data["viewport"]
        _assert(
            viewport["width"] == native["resolution"]["source_width"]
            and viewport["height"] == native["resolution"]["source_height"],
            "Projection coordinates and color-buffer pixels use different dimensions",
        )
        _assert(
            viewport["width"] == metrics["port_width"]
            and viewport["height"] == metrics["port_height"],
            "MCP viewport size does not match M3dView port size",
        )
        checks["viewport_metrics"] = metrics
        checks["pick_shaded_object"] = _validate_pick(
            client,
            scene,
            "shaded object pick",
            "smoothShaded",
            [0.0, 0.0, 0.0],
            False,
        )
        checks["pick_wireframe_object"] = _validate_pick(
            client,
            scene,
            "wireframe object pick",
            "wireframe",
            scene["vertex"],
            False,
        )
        checks["pick_component"] = _validate_pick(
            client,
            scene,
            "component pick",
            "smoothShaded",
            scene["vertex"],
            True,
        )
        _main_thread(
            _configure_view,
            scene["panel"],
            scene["perspective"],
            "smoothShaded",
            scene["sentinel"],
            False,
        )
        checks["rig_preview_lifecycle"] = _validate_rig_preview_lifecycle(client)

        _main_thread(
            _set_isolate,
            scene["panel"],
            scene["target"],
            True,
            scene["sentinel"],
        )
        try:
            _, captures["isolate_select_png"] = _save_capture(
                client,
                evidence_dir,
                "isolate-select",
                {"width": 400, "height": 300, "format": "png"},
            )
        finally:
            _main_thread(
                _set_isolate,
                scene["panel"],
                scene["target"],
                False,
                scene["sentinel"],
            )
        _main_thread(_set_playback, True)
        try:
            _, captures["playback_png"] = _save_capture(
                client,
                evidence_dir,
                "playback",
                {"width": 400, "height": 300, "format": "png"},
            )
        finally:
            _main_thread(_set_playback, False)

        checks["render_override"] = {
            "active": bool(metrics.get("render_override")),
            "name": metrics.get("render_override"),
            "status": (
                "captured" if metrics.get("render_override") else "not_configured"
            ),
        }
        return {
            "schema_version": 1,
            "passed": True,
            "started_at": started,
            "finished_at": _utc_now(),
            "maya_version": str(_main_thread(lambda: cmds.about(version=True))),
            "plugin_version": plugin_version,
            "protocol_version": initialized["protocolVersion"],
            "endpoint": endpoint,
            "dispatcher": "Maya timer plus Qt playback heartbeat",
            "captures": captures,
            "checks": checks,
        }
    finally:
        client.close()


def _worker_entry(
    endpoint: str,
    token: str,
    plugin_version: str,
    scene: dict[str, Any],
    evidence_dir: Path,
) -> None:
    global _outcome
    try:
        _outcome = _run_validation(
            endpoint, token, plugin_version, scene, evidence_dir
        )
    except BaseException as error:
        _outcome = {
            "schema_version": 1,
            "passed": False,
            "finished_at": _utc_now(),
            "error": {
                "type": type(error).__name__,
                "message": str(error),
                "traceback": traceback.format_exc(),
            },
        }


def _schedule_poll() -> None:
    try:
        from PySide6 import QtCore
    except ImportError:
        from PySide2 import QtCore
    QtCore.QTimer.singleShot(100, _poll)


def _poll() -> None:
    global _outcome
    if _worker is not None and _worker.is_alive():
        if time.monotonic() >= _deadline:
            _outcome = {
                "schema_version": 1,
                "passed": False,
                "finished_at": _utc_now(),
                "error": {
                    "type": "TimeoutError",
                    "message": "Interactive viewport worker exceeded its deadline",
                },
            }
            _finish()
        else:
            _schedule_poll()
        return
    _finish()


def _finish() -> None:
    global _outcome
    if not _launch_authorized:
        raise RuntimeError("Refusing to close Maya without an isolated launch guard")
    if _outcome is None:
        _outcome = {
            "schema_version": 1,
            "passed": False,
            "finished_at": _utc_now(),
            "error": {
                "type": "RuntimeError",
                "message": "Worker produced no result",
            },
        }
    if _worker is not None and _worker.is_alive():
        _outcome["plugin_unload"] = "skipped: timed-out worker still active"
    else:
        try:
            if cmds.pluginInfo("maya_mcp", query=True, loaded=True):
                cmds.unloadPlugin("maya_mcp", force=True)
            _outcome["plugin_unload"] = "passed"
        except BaseException as error:
            _outcome["plugin_unload"] = f"failed: {error}"
            _outcome["passed"] = False
    if _result_path is not None:
        _atomic_write_json(_result_path, _outcome)
    cmds.quit(force=True)


def _start() -> None:
    global _worker, _deadline, _evidence_dir, _result_path, _outcome
    try:
        _assert(_launch_authorized, "Viewport test launch is not authorized")
        _assert(_evidence_dir is not None, "Evidence path was not initialized")
        _assert(_result_path is not None, "Result path was not initialized")
        _assert(_plugin_path is not None, "Plug-in path was not initialized")
        _evidence_dir.mkdir(parents=True, exist_ok=True)

        cmds.loadPlugin(str(_plugin_path), quiet=True)
        import maya_mcp_runtime

        packaged_scripts = (_plugin_path.parent.parent / "scripts").resolve()
        runtime_path = Path(maya_mcp_runtime.__file__).resolve()
        _assert(
            packaged_scripts in runtime_path.parents,
            "Interactive gate imported runtime code outside the built package: "
            f"{runtime_path} (expected below {packaged_scripts})",
        )
        _assert(
            maya_mcp_runtime.__version__ == "0.4.1",
            "Interactive gate imported the wrong Python runtime version: "
            f"{maya_mcp_runtime.__version__}",
        )
        status = json.loads(cmds.mayaMcpStatus())
        _assert(status.get("running") is True, f"Maya MCP did not start: {status}")
        _assert(
            status.get("version") == "0.4.1",
            f"Interactive gate expected Maya MCP 0.4.1, got {status.get('version')}",
        )
        discovery_path = Path(status["discoveryFile"]).resolve()
        local_app_data = Path(os.environ["LOCALAPPDATA"]).resolve()
        _assert(
            _is_within(discovery_path, local_app_data),
            f"Discovery file escaped isolated LOCALAPPDATA: {discovery_path}",
        )
        with discovery_path.open("r", encoding="utf-8") as stream:
            discovery = json.load(stream)
        endpoint = urllib.parse.urlsplit(str(discovery["url"]))
        _assert(
            endpoint.scheme == "http"
            and endpoint.hostname in {"127.0.0.1", "::1", "localhost"}
            and endpoint.path == "/mcp"
            and endpoint.username is None
            and endpoint.password is None,
            "Discovery URL is not the isolated loopback MCP endpoint: "
            f"{endpoint.geturl()}",
        )
        token = discovery["token"]
        _assert(
            len(token) == 64,
            "Discovery token is not a 256-bit hexadecimal secret",
        )
        scene = _setup_scene()
        timeout = int(
            os.environ.get("MAYA_MCP_VIEWPORT_TIMEOUT_SECONDS", "240")
        )
        # Leave the launcher enough time to write the failure result and quit.
        _deadline = time.monotonic() + max(10, timeout - 15)
        _worker = threading.Thread(
            target=_worker_entry,
            args=(
                endpoint.geturl(),
                token,
                status["version"],
                scene,
                _evidence_dir,
            ),
            name="maya-mcp-viewport-validation",
            daemon=True,
        )
        _worker.start()
        _schedule_poll()
    except BaseException as error:
        _outcome = {
            "schema_version": 1,
            "passed": False,
            "finished_at": _utc_now(),
            "error": {
                "type": type(error).__name__,
                "message": str(error),
                "traceback": traceback.format_exc(),
            },
        }
        _finish()


def install(run_id: str) -> None:
    """Schedule validation after Maya finishes creating its main window."""

    global _evidence_dir, _result_path, _plugin_path, _launch_authorized
    evidence, result, plugin = _validate_launch_guard(run_id)
    _evidence_dir = evidence
    _result_path = result
    _plugin_path = plugin
    _launch_authorized = True
    maya_utils.executeDeferred(_start)
