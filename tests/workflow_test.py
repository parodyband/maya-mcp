"""Real Maya contracts for compact discovery, composition and query cursors."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import sys
import threading
import time
import urllib.request
from unittest.mock import patch

import maya.standalone

PACKAGE = Path(os.environ["MAYA_MCP_TEST_PACKAGE"])
sys.path.insert(0, str(PACKAGE / "maya-mcp" / "scripts"))


def main() -> None:
    maya.standalone.initialize(name="python")
    try:
        import maya.cmds as cmds
        from maya_mcp_runtime import state
        from maya_mcp_runtime.catalog import TOOLS, catalog_json
        from maya_mcp_runtime.dispatcher import HANDLERS, dispatch_base64

        os.environ["MAYA_MCP_TOOL_PROFILE"] = "compact"
        cmds.loadPlugin("maya_mcp")
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True)

        def invoke(name, arguments):
            payload = base64.b64encode(json.dumps({"name": name, "arguments": arguments}).encode()).decode()
            result = json.loads(dispatch_base64(payload))
            assert json.loads(result["content"][-1]["text"]) == result["structuredContent"]
            return result["structuredContent"]

        def run(steps, **kwargs):
            return invoke("maya.workflow.run", {"steps": steps, **kwargs})

        def create(name, step_id="create"):
            return {"id": step_id, "tool": "maya.geometry.apply", "arguments": {"kind": "cube", "name": name}}

        compact = json.loads(catalog_json())
        assert len(compact["tools"]) == 8
        assert "maya.geometry.apply" not in {tool["name"] for tool in compact["tools"]}
        discovered = invoke("maya.tools.describe", {"names": ["maya.node.apply"]})
        assert discovered["ok"], discovered
        expected = next(tool for tool in TOOLS if tool["name"] == "maya.node.apply")
        assert discovered["data"]["tools"][0]["inputSchema"] == expected["inputSchema"]
        assert invoke("maya.tools.describe", {"query": "skin"})["data"]["count"] >= 1
        assert not invoke("maya.tools.describe", {"names": ["missing"]})["ok"]

        result = run([
            {**create("workflowCube"), "select": {}},
            {"id": "move", "tool": "maya.node.apply", "arguments": {"operations": [
                {"op": "set_transform", "node": {"$ref": "create#/data/transform"}, "translate": [2, 3, 4]}
            ]}, "select": {}},
            {"id": "inspect", "tool": "maya.scene.query", "arguments": {
                "scope": "nodes", "nodes": [{"$ref": "create#/data/transform"}],
                "include_attributes": ["translate"]},
             "select": {"translation": "/data/nodes/0/attributes/translate"}},
        ], if_scene_epoch=state.scene_epoch(), if_scene_revision=state.scene_revision())
        assert result["ok"], result
        assert result["data"]["completed"] == 3
        assert result["data"]["steps"][0]["data"] == {}
        assert result["data"]["steps"][2]["data"]["translation"] == [[2.0, 3.0, 4.0]], result
        cmds.undo()
        assert cmds.objExists("workflowCube") and cmds.getAttr("workflowCube.translate")[0] == (0, 0, 0)
        cmds.undo()
        assert not cmds.objExists("workflowCube")

        # Known malformed later steps must be rejected before the first edit.
        invalid_steps = [
            {"id": "bad", "tool": "maya.workflow.run", "arguments": {"steps": []}},
            {"id": "create", "tool": "maya.context.get"},
            {"id": "bad", "tool": "missing"},
            {"id": "bad", "tool": "maya.scene.query", "arguments": {"limit": 0}},
            {"id": "bad", "tool": "maya.scene.query", "arguments": {"nodes": [{"$ref": "later#/data"}]}},
        ]
        for invalid in invalid_steps:
            rejected = run([create("mustNotExist"), invalid])
            assert not rejected["ok"], rejected
            assert not cmds.objExists("mustNotExist"), rejected

        partial = run([
            create("retainedCube"),
            {"id": "bad", "tool": "maya.scene.query", "arguments": {"scope": "nodes", "nodes": ["missingNode"]}},
            create("skippedCube", "skipped"),
        ])
        assert not partial["ok"] and partial["error"]["code"] == "WORKFLOW_STOPPED", partial
        assert partial["data"]["completed"] == 1 and partial["data"]["skipped"] == ["skipped"]
        assert cmds.objExists("retainedCube") and not cmds.objExists("skippedCube")
        assert partial["changes"][0]["step_id"] == "create"
        wrong_type = run([
            {"id": "first", "tool": "maya.context.get"},
            {"id": "bad", "tool": "maya.scene.query", "arguments": {"limit": {"$ref": "first#/data"}}},
        ])
        assert wrong_type["data"]["steps"][1]["error"]["code"] == "INVALID_ARGUMENT", wrong_type
        selected = run([{**create("selectionCube"), "select": {"missing": "/missing"}}])
        assert selected["ok"] and cmds.objExists("selectionCube")
        assert selected["warnings"][0]["code"] == "OUTPUT_SELECTION_FAILED"
        assert not run([create("wrongEpoch")], if_scene_epoch="wrong")["ok"]
        assert not cmds.objExists("wrongEpoch")

        # Test output/retention limits through the same registry execution seam.
        # A synthetic read avoids allocating huge real Maya scenes for this gate.
        def large_read(_arguments, child_call):
            return state.result(child_call, {"blob": "x" * (600 * 1024)}, "Large read")

        with patch.dict(HANDLERS, {"maya.context.get": large_read}):
            bounded = run([{"id": "read", "tool": "maya.context.get"}, create("afterLargeRead", "create")])
            assert bounded["ok"] and cmds.objExists("afterLargeRead"), bounded
            assert bounded["data"]["steps"][0]["data_truncated"]
            assert len(json.dumps(bounded)) < 10000

        def excessive_read(_arguments, child_call):
            return state.result(child_call, {"blob": "x" * (4 * 1024 * 1024)}, "Excessive read")

        with patch.dict(HANDLERS, {"maya.context.get": excessive_read}):
            stopped = run([{"id": "read", "tool": "maya.context.get", "select": {}}, create("afterLimit", "skipped")])
            assert not stopped["ok"] and not cmds.objExists("afterLimit"), stopped
            assert stopped["data"]["completed"] == 1 and stopped["data"]["skipped"] == ["skipped"]
            assert stopped["warnings"][0]["code"] == "WORKFLOW_RESULT_LIMIT"

        def large_failure(_arguments, child_call):
            return state.failure(child_call, state.ToolError("LARGE_ERROR", "x" * (5 * 1024 * 1024),
                {"large": "y" * 100000, "partial_mutation_possible": True}))

        with patch.dict(HANDLERS, {"maya.context.get": large_failure}):
            failed = run([create("beforeHugeError"), {"id": "error", "tool": "maya.context.get"}])
            assert not failed["ok"] and cmds.objExists("beforeHugeError")
            assert failed["data"]["completed"] == 1 and len(json.dumps(failed)) < 40000
            error = failed["data"]["steps"][1]["error"]
            assert error["code"] == "LARGE_ERROR" and error["message_truncated"]
            assert error["details"]["partial_mutation_possible"] is True

        def oversized_image(_arguments, child_call):
            return state.result(child_call, {"raw_depth": "depth-must-not-appear-in-text"}, "Capture", image_content=[
                {"type": "image", "mimeType": "image/png", "data": "x" * (7 * 1024 * 1024)}])

        with patch.dict(HANDLERS, {"maya.context.get": oversized_image}):
            payload = base64.b64encode(json.dumps({"name": "maya.workflow.run", "arguments": {
                "steps": [{"id": "capture", "tool": "maya.context.get"}]}}).encode()).decode()
            response = json.loads(dispatch_base64(payload))
            assert response["structuredContent"]["ok"]
            assert response["structuredContent"]["warnings"][0]["code"] == "IMAGE_OUTPUT_TRUNCATED"
            assert "depth-must-not-appear-in-text" not in response["content"][0]["text"]

        escaped = run([
            {"id": "read", "tool": "maya.tools.describe", "arguments": {"names": ["maya.workflow.run"]}},
            {"id": "next", "tool": "maya.tools.describe", "arguments": {"names": [
                {"$ref": "read#/data/tools/0/name"}]}, "select": {"name": "/data/tools/0/name"}},
        ])
        assert escaped["ok"] and escaped["data"]["steps"][1]["data"]["name"] == "maya.workflow.run", escaped

        duplicate_items = run([create("invalidUnique"), {"id": "bad", "tool": "maya.node.apply", "arguments": {
            "operations": [{"op": "create_constraint", "skip_translate": ["x", "x"]}]}}])
        assert not duplicate_items["ok"] and not cmds.objExists("invalidUnique"), duplicate_items
        nonfinite = run([create("invalidNumber"), {"id": "bad", "tool": "maya.node.apply", "arguments": {
            "operations": [{"op": "set_transform", "node": "retainedCube", "translate": [float("nan"), 0, 0]}]}}])
        assert not nonfinite["ok"] and not cmds.objExists("invalidNumber"), nonfinite

        # Passing data into Python does not require source interpolation.
        os.environ["MAYA_MCP_ALLOW_UNSAFE_CODE"] = "1"
        value = "quotes ' \" and backslashes \\ and newlines\n"
        script = invoke("maya.script.execute", {"language": "python", "source": "result = arguments['value']", "arguments": {"value": value}})
        assert script["data"]["result"] == value, script
        os.environ["MAYA_MCP_ALLOW_UNSAFE_CODE"] = "0"
        denied = run([{"id": "script", "tool": "maya.script.execute", "arguments": {"language": "python", "source": "result = 1"}}])
        assert denied["data"]["steps"][0]["error"]["code"] == "CAPABILITY_DISABLED", denied

        for name in ["pageC", "pageA", "pageB"]:
            cmds.createNode("transform", name=name)
        query = {"name_glob": "page*", "node_types": ["transform"], "limit": 1}
        names = []
        cursor = None
        while True:
            page = invoke("maya.scene.query", {**query, **({"cursor": cursor} if cursor else {})})
            assert page["ok"], page
            names.extend(node["name"] for node in page["data"]["nodes"])
            cursor = page["data"]["next_cursor"]
            if not cursor:
                break
        assert names == ["pageA", "pageB", "pageC"], names
        cursor = invoke("maya.scene.query", query)["data"]["next_cursor"]
        cmds.createNode("transform", name="pageD")
        stale = invoke("maya.scene.query", {**query, "cursor": cursor})
        assert stale["error"]["code"] == "STALE_CURSOR", stale
        invalid_cursor = invoke("maya.scene.query", {**query, "cursor": base64.b64encode(b"[]").decode()})
        assert invalid_cursor["error"]["code"] == "INVALID_ARGUMENT", invalid_cursor

        # Verify discovery and hidden-operation execution over real native HTTP.
        status = json.loads(cmds.mayaMcpStatus())
        discovery = json.loads(Path(status["discoveryFile"]).read_text())
        headers = {"Authorization": "Bearer " + discovery["token"], "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}

        def post(payload):
            request = urllib.request.Request(discovery["url"], data=json.dumps(payload).encode(), headers=headers)
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.headers, json.loads(response.read() or "null")

        response_headers, _ = post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "workflow-test", "version": "1"}}})
        headers.update({"MCP-Session-Id": response_headers["MCP-Session-Id"], "MCP-Protocol-Version": "2025-11-25"})
        post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        _, listing = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert len(listing["result"]["tools"]) == 8, listing
        responses, errors = [], []

        def worker():
            try:
                responses.append(post({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                    "name": "maya.workflow.run", "arguments": {"steps": [
                        create("nativeWorkflowCube"),
                        {"id": "move", "tool": "maya.node.apply", "arguments": {"operations": [
                            {"op": "set_transform", "node": {"$ref": "create#/data/transform"}, "translate": [5, 0, 0]}
                        ]}},
                    ]}}})[1])
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        deadline = time.monotonic() + 35
        while thread.is_alive() and time.monotonic() < deadline:
            cmds.mayaMcpPump()
            time.sleep(0.005)
        thread.join(timeout=1)
        assert not thread.is_alive() and not errors, errors
        assert responses[0]["result"]["structuredContent"]["ok"], responses
        assert responses[0]["result"]["structuredContent"]["undo"]["available"], responses
        assert responses[0]["result"]["structuredContent"]["data"]["undo_scope"] == "native_request"
        assert cmds.objExists("nativeWorkflowCube")
        assert cmds.getAttr("nativeWorkflowCube.translateX") == 5
        cmds.undo()
        assert not cmds.objExists("nativeWorkflowCube")
        responses.clear()
        def failure_worker():
            try:
                responses.append(post({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
                    "name": "maya.workflow.run", "arguments": {"steps": [
                        create("nativeBeforeFailure"),
                        {"id": "bad", "tool": "maya.node.apply", "arguments": {"operations": [
                            {"id": "temporary", "op": "create", "node_type": "transform", "name": "nativeRolledBack"},
                            {"op": "set_attribute", "node": "$temporary", "attribute": "nonexistent", "value": 1},
                        ]}},
                    ]}}})[1])
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=failure_worker, daemon=True)
        thread.start()
        deadline = time.monotonic() + 35
        while thread.is_alive() and time.monotonic() < deadline:
            cmds.mayaMcpPump()
            time.sleep(0.005)
        thread.join(timeout=1)
        assert not thread.is_alive() and not errors, errors
        assert responses[0]["result"]["isError"], responses
        assert cmds.objExists("nativeBeforeFailure"), responses
        assert not cmds.objExists("nativeRolledBack"), responses
        cmds.undo()
        assert not cmds.objExists("nativeBeforeFailure")
        compact_bytes = len(json.dumps(compact["tools"], separators=(",", ":")))
        full_bytes = len(json.dumps(TOOLS, separators=(",", ":")))
        print("MAYA_MCP_WORKFLOW_TEST_RESULT=" + json.dumps({"workflow": "passed", "native_compact": "passed",
              "pagination": "passed", "compact_tools": 8, "full_tools": len(TOOLS),
              "compact_schema_bytes": compact_bytes, "full_schema_bytes": full_bytes}))
        cmds.unloadPlugin("maya_mcp")
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
