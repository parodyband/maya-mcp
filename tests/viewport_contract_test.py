"""Batch-safe contract tests for viewport payload and selector validation."""

from __future__ import annotations

import base64
import copy
import json
import sys
from pathlib import Path

import maya.standalone


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SCRIPTS = (
    ROOT
    / "build"
    / "maya2027-mcp-vs2022"
    / "package"
    / "maya-mcp"
    / "scripts"
).resolve()
sys.path.insert(0, str(PACKAGE_SCRIPTS))


def _assert_packaged_runtime() -> None:
    import maya_mcp_runtime

    runtime_path = Path(maya_mcp_runtime.__file__).resolve()
    assert PACKAGE_SCRIPTS in runtime_path.parents, runtime_path
    assert maya_mcp_runtime.__version__ == "0.4.1"


def _native_result(max_dimension: int = 64) -> dict:
    raw = bytes(range(16))
    encoded = base64.b64encode(raw).decode("ascii")
    return {
        "schema_version": 1,
        "ok": True,
        "request": {
            "depth": True,
            "color": False,
            "object_id": False,
            "max_dimension": max_dimension,
        },
        "source": {
            "kind": "active_viewport_2",
            "viewport_width": 640,
            "viewport_height": 480,
            "draw_api": {"name": "kDirectX11", "value": 4},
            "draw_api_version": "test",
        },
        "capabilities": {
            "depth": {"supported": True},
            "color": {"supported": True, "default": False},
            "object_id": {"supported": False},
        },
        "limits": {
            "default_max_dimension": 512,
            "hard_max_dimension": 1024,
            "base64_budget_chars": 4 * 1024 * 1024,
            "base64_chars": len(encoded),
        },
        "passes": {
            "depth": {
                "source": {
                    "width": 2,
                    "height": 2,
                    "row_pitch_bytes": 8,
                    "slice_pitch_bytes": 16,
                    "sample_count": 1,
                    "array_slices": 1,
                    "cube_map": False,
                    "raster_format": {
                        "name": "kD32_FLOAT",
                        "value": 42,
                        "layout": "depth_float32",
                        "pixel_stride_bytes": 4,
                    },
                    "row_order": "renderer_native",
                    "byte_order": "native",
                },
                "sample": {
                    "width": 2,
                    "height": 2,
                    "filter": "nearest",
                    "row_stride_bytes": 8,
                    "pixel_stride_bytes": 4,
                    "byte_count": 16,
                    "source_row_order_preserved": True,
                },
                "payload": {
                    "encoding": "base64",
                    "media_type": "application/vnd.autodesk.maya.render-target",
                    "base64_chars": len(encoded),
                    "data": encoded,
                },
            }
        },
    }


def main() -> None:
    maya.standalone.initialize(name="python")
    try:
        _assert_packaged_runtime()
        import maya.cmds as cmds

        from maya_mcp_runtime import state
        from maya_mcp_runtime.catalog import (
            CATALOG,
            NODE_SELECTOR,
            RIG_PREVIEW_HANDLE,
        )
        from maya_mcp_runtime.dispatcher import _validate
        from maya_mcp_runtime.tools_viewport import (
            _native_depth_capture,
            _redact_native_payload_from_text,
        )

        response = _native_result()
        had_command = hasattr(cmds, "mayaMcpVp2Capture")
        original_command = getattr(cmds, "mayaMcpVp2Capture", None)
        try:
            cmds.mayaMcpVp2Capture = lambda **_: json.dumps(response)
            assert _native_depth_capture(64) == response

            corruptions = [
                ("schema", lambda item: item.update(schema_version=2)),
                (
                    "request_echo",
                    lambda item: item["request"].update(max_dimension=63),
                ),
                (
                    "format_layout",
                    lambda item: item["passes"]["depth"]["source"][
                        "raster_format"
                    ].update(layout="unknown"),
                ),
                (
                    "row_stride",
                    lambda item: item["passes"]["depth"]["sample"].update(
                        row_stride_bytes=7
                    ),
                ),
                (
                    "payload_length",
                    lambda item: item["passes"]["depth"]["payload"].update(
                        base64_chars=1
                    ),
                ),
                (
                    "decoded_length",
                    lambda item: item["passes"]["depth"]["sample"].update(
                        byte_count=12
                    ),
                ),
            ]
            for label, corrupt in corruptions:
                invalid = copy.deepcopy(response)
                corrupt(invalid)
                cmds.mayaMcpVp2Capture = lambda item=invalid, **_: json.dumps(
                    item
                )
                try:
                    _native_depth_capture(64)
                except state.ToolError as error:
                    assert error.code == "NATIVE_VIEWPORT_CAPTURE_INVALID", label
                    assert error.details.get("field"), label
                else:
                    raise AssertionError(
                        f"Malformed native depth contract was accepted: {label}"
                    )
        finally:
            if had_command:
                cmds.mayaMcpVp2Capture = original_command
            else:
                delattr(cmds, "mayaMcpVp2Capture")

        raw_depth = response["passes"]["depth"]["payload"]["data"]
        envelope = {
            "structuredContent": {"data": {"native_capture": response}},
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {"data": {"native_capture": response}},
                        separators=(",", ":"),
                    ),
                }
            ],
        }
        _redact_native_payload_from_text(envelope, response)
        fallback = envelope["content"][0]["text"]
        assert raw_depth not in fallback
        assert "data_omitted_from_text" in fallback

        full_reference = {
            "node_id": "node:" + "a" * 32 + ":" + "b" * 24,
            "scene_epoch": "a" * 32,
            "uuid": "maya-uuid",
            "name": "probe",
        }
        _validate(full_reference, NODE_SELECTOR)
        bounded_reference = {
            **full_reference,
            "dag_paths": ["|probe"],
            "dag_paths_truncated": True,
            "dag_path_limit": 1,
            "instanced": True,
        }
        _validate(bounded_reference, NODE_SELECTOR)
        try:
            _validate({"uuid": "metadata-only"}, NODE_SELECTOR)
        except state.ToolError:
            pass
        else:
            raise AssertionError("Metadata-only NODE_SELECTOR was accepted")
        pick_tool = next(
            tool for tool in CATALOG["tools"] if tool["name"] == "maya.viewport.pick"
        )
        assert pick_tool["annotations"]["readOnlyHint"] is False
        _validate(
            {
                "preview_id": "rig-preview:" + "c" * 32,
                "scene_epoch": "d" * 32,
                "revision": 1,
            },
            RIG_PREVIEW_HANDLE,
        )
        try:
            _validate(
                {
                    "preview_id": "rig-preview:" + "c" * 32 + "-suffix",
                    "scene_epoch": "d" * 32,
                    "revision": 1,
                },
                RIG_PREVIEW_HANDLE,
            )
        except state.ToolError:
            pass
        else:
            raise AssertionError("Unanchored rig-preview handle was accepted")
        project_schema = next(
            tool["inputSchema"]
            for tool in CATALOG["tools"]
            if tool["name"] == "maya.viewport.project"
        )
        try:
            _validate(
                {"world_points": [[0.0, 0.0, 0.0]] * 1001},
                project_schema,
            )
        except state.ToolError:
            pass
        else:
            raise AssertionError("Unbounded viewport project array was accepted")

        print(
            "MAYA_MCP_VIEWPORT_CONTRACT_TEST_RESULT="
            + json.dumps(
                {
                    "depth_validation": "passed",
                    "selector_schema": "passed",
                    "project_array_bounds": "passed",
                    "text_redaction": "passed",
                },
                sort_keys=True,
            )
        )
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
