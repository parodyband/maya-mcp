"""Native HTTP recovery contracts; deliberately withhold Maya's queue pump."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
import time
import urllib.error
import urllib.request
from unittest.mock import patch

import maya.standalone

PACKAGE = Path(os.environ["MAYA_MCP_TEST_PACKAGE"])
sys.path.insert(0, str(PACKAGE / "maya-mcp" / "scripts"))


def main() -> None:
    maya.standalone.initialize(name="python")
    try:
        import maya.cmds as cmds

        os.environ["MAYA_MCP_TOOL_PROFILE"] = "compact"
        os.environ["MAYA_MCP_ALLOW_UNSAFE_CODE"] = "1"
        cmds.loadPlugin("maya_mcp")
        cmds.file(new=True, force=True)
        discovery = json.loads(Path(json.loads(cmds.mayaMcpStatus())["discoveryFile"]).read_text())
        base_headers = {"Authorization": "Bearer " + discovery["token"],
                        "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}

        def post(headers, method, params=None):
            payload = {"jsonrpc": "2.0", "id": 1, "method": method}
            if params is not None:
                payload["params"] = params
            if method == "notifications/initialized":
                payload.pop("id")
            request = urllib.request.Request(discovery["url"], data=json.dumps(payload).encode(), headers=headers)
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.headers, json.loads(response.read() or "null")

        def session():
            response_headers, _ = post(base_headers, "initialize", {
                "protocolVersion": "2025-11-25", "capabilities": {},
                "clientInfo": {"name": "request-recovery-test", "version": "1"}})
            headers = {**base_headers, "MCP-Session-Id": response_headers["MCP-Session-Id"],
                       "MCP-Protocol-Version": "2025-11-25"}
            post(headers, "notifications/initialized")
            return headers

        def call(headers, name, args):
            body = post(headers, "tools/call", {"name": name, "arguments": args})[1]
            assert "result" in body, body
            return body["result"]

        def status(headers, key):
            return call(headers, "maya.request.status", {"request_id": key})["structuredContent"]

        def call_pumped(headers, name, arguments):
            responses, errors = [], []

            def worker():
                try:
                    responses.append(call(headers, name, arguments))
                except Exception as error:
                    errors.append(error)

            thread = threading.Thread(target=worker)
            thread.start()
            deadline = time.monotonic() + 4
            while thread.is_alive() and time.monotonic() < deadline:
                cmds.mayaMcpPump()
                time.sleep(0.005)
            thread.join(timeout=1)
            assert not thread.is_alive() and not errors, errors
            return responses[0]

        def workflow(key, node):
            return {"request_id": key, "steps": [{"id": "create", "tool": "maya.node.apply", "arguments": {
                "operations": [{"op": "create", "node_type": "transform", "name": node}]}}]}

        first, second = session(), session()
        listed = post(first, "tools/list")[1]["result"]["tools"]
        assert "maya.request.status" in {tool["name"] for tool in listed}
        args = workflow("create-once", "recoveryCube")
        queued = call(first, "maya.workflow.run", args)
        assert queued["structuredContent"]["data"]["state"] == "queued", queued
        assert not cmds.objExists("recoveryCube")
        duplicate = call(first, "maya.workflow.run", args)
        assert duplicate["structuredContent"]["data"]["state"] == "queued", duplicate
        assert json.loads(cmds.mayaMcpStatus())["pendingMainThreadRequests"] == 1
        assert status(first, "create-once")["data"]["state"] == "queued"
        assert status(second, "create-once")["error"]["code"] == "REQUEST_NOT_FOUND"
        conflict = call(first, "maya.workflow.run", workflow("create-once", "wrongCube"))
        assert conflict["structuredContent"]["error"]["code"] == "REQUEST_ID_CONFLICT"
        cmds.mayaMcpPump()
        completed = status(first, "create-once")
        assert completed["data"]["state"] == "completed", completed
        original = completed["data"]["result"]
        replay = call(first, "maya.workflow.run", args)
        assert replay == original
        assert cmds.ls("recoveryCube*") == ["recoveryCube"]
        assert not cmds.objExists("wrongCube")
        assert json.loads(cmds.mayaMcpStatus())["pendingMainThreadRequests"] == 0
        # Identical ID in another session represents independent work.
        call(second, "maya.workflow.run", args)
        cmds.mayaMcpPump()
        assert status(second, "create-once")["data"]["state"] == "completed"
        assert len(cmds.ls("recoveryCube*")) == 2

        # Both ordinary native calls and queued requests receive the authenticated
        # MCP owner, so a Python namespace token cannot be used by another client.
        opened_a = call_pumped(first, "maya.session", {"action": "open"})["structuredContent"]
        opened_b = call_pumped(second, "maya.session", {"action": "open"})["structuredContent"]
        assert opened_a["ok"] and opened_b["ok"], (opened_a, opened_b)
        token_a, token_b = opened_a["data"]["session_id"], opened_b["data"]["session_id"]
        assert token_a != token_b
        initial = call_pumped(first, "maya.script.execute", {
            "language": "python", "session_id": token_a, "source": "persisted_value = 41\nresult = persisted_value"})
        assert initial["structuredContent"]["data"]["result"] == 41, initial
        denied_args = {"request_id": "foreign-python-token", "language": "python", "session_id": token_a,
                       "source": "cmds.createNode('transform', name='foreignSessionMustNotMutate')"}
        call(second, "maya.script.execute", denied_args)
        cmds.mayaMcpPump()
        denied = status(second, "foreign-python-token")["data"]
        assert denied["state"] == "failed", denied
        assert denied["result"]["structuredContent"]["error"]["code"] == "SESSION_NOT_FOUND", denied
        assert not cmds.objExists("foreignSessionMustNotMutate")
        own_args = {"request_id": "own-python-token", "language": "python", "session_id": token_a,
                    "source": "persisted_value += 1\nresult = persisted_value"}
        call(first, "maya.script.execute", own_args)
        cmds.mayaMcpPump()
        owned = status(first, "own-python-token")["data"]
        assert owned["state"] == "completed", owned
        assert owned["result"]["structuredContent"]["data"]["result"] == 42, owned
        private_b = call_pumped(second, "maya.script.execute", {
            "language": "python", "session_id": token_b, "source": "result = 'persisted_value' in globals()"})
        assert private_b["structuredContent"]["data"]["result"] is False, private_b

        failure_args = {"request_id": "failed-script", "language": "python", "source": "raise RuntimeError('expected failure')"}
        call(first, "maya.script.execute", failure_args)
        cmds.mayaMcpPump()
        failed = status(first, "failed-script")
        assert failed["data"]["state"] == "failed", failed
        assert call(first, "maya.script.execute", failure_args) == failed["data"]["result"]

        from maya_mcp_runtime import state
        from maya_mcp_runtime.dispatcher import HANDLERS

        png = {"type": "image", "mimeType": "image/png",
               "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jJ1kAAAAASUVORK5CYII="}

        def image_result(_arguments, child_call):
            return state.result(child_call, {"capture": "fixture"}, "Capture", image_content=[png])

        image_args = {"request_id": "image-workflow", "steps": [{"id": "capture", "tool": "maya.context.get"}]}
        with patch.dict(HANDLERS, {"maya.context.get": image_result}):
            call(first, "maya.workflow.run", image_args)
            cmds.mayaMcpPump()
        recovered_image = call(first, "maya.request.status", {"request_id": "image-workflow"})
        image_data = recovered_image["structuredContent"]["data"]
        assert recovered_image["content"] == [png], recovered_image
        assert image_data["result_content"] == "images_at_top_level"
        assert image_data["image_content_indices"] == [0]
        assert all(item["type"] != "image" for item in image_data["result"]["content"])
        exact_image_replay = call(first, "maya.workflow.run", image_args)
        assert [item for item in exact_image_replay["content"] if item["type"] == "image"] == [png]
        assert exact_image_replay["structuredContent"] == image_data["result"]["structuredContent"]

        # Running work leaves the HTTP control plane responsive, including duplicates.
        slow = {"request_id": "slow-script", "language": "python", "source": "import time\ntime.sleep(0.5)\nresult = 42"}
        call(first, "maya.script.execute", slow)
        observations, errors = [], []

        def observer():
            try:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    value = status(first, "slow-script")
                    if value["data"]["state"] == "running":
                        observations.append(call(first, "maya.script.execute", slow))
                        return
                    time.sleep(0.005)
                raise AssertionError("did not observe running request")
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=observer)
        thread.start()
        cmds.mayaMcpPump()
        thread.join(timeout=4)
        assert not thread.is_alive() and not errors, errors
        assert observations[0]["structuredContent"]["data"]["state"] == "running", observations
        assert status(first, "slow-script")["data"]["result"]["structuredContent"]["data"]["result"] == 42

        for bad_id in [None, "", 123, "x" * 129]:
            bad = call(first, "maya.workflow.run", workflow(bad_id, "invalidIdCube"))
            assert bad["structuredContent"]["error"]["code"] == "INVALID_REQUEST_ID", bad

        # Reserve worst-case response storage before dispatch, with no ID eviction.
        accepted, refused = [], None
        for index in range(10):
            key = "capacity-" + str(index)
            response = call(first, "maya.workflow.run", workflow(key, "capacityCube" + str(index)))
            if response.get("isError"):
                assert response["structuredContent"]["error"]["code"] == "REQUEST_CACHE_FULL", response
                refused = index
                break
            accepted.append(key)
        assert accepted and refused is not None
        assert not cmds.objExists("capacityCube" + str(refused))
        while json.loads(cmds.mayaMcpStatus())["pendingMainThreadRequests"]:
            cmds.mayaMcpPump()
        for key in accepted:
            assert status(first, key)["data"]["state"] == "completed"
        assert status(first, "create-once")["data"]["result"] == original
        assert status(first, "capacity-" + str(refused))["error"]["code"] == "REQUEST_NOT_FOUND"
        assert not cmds.objExists("capacityCube" + str(refused))
        # Completed small results release the pessimistic reservation, so the
        # pending-request bound does not limit a normal serial agent session.
        for index in range(12):
            key = "serial-" + str(index)
            queued = call(first, "maya.workflow.run", workflow(key, "serialCube" + str(index)))
            assert not queued.get("isError"), queued
            cmds.mayaMcpPump()
            assert status(first, key)["data"]["state"] == "completed"

        # MCP DELETE closes recovery ownership; it is not cancellation. Already
        # queued work still executes, so clients must retrieve outcomes first.
        closing = session()
        call(closing, "maya.workflow.run", workflow("delete-pending", "afterSessionDelete"))
        assert not cmds.objExists("afterSessionDelete")
        delete_request = urllib.request.Request(discovery["url"], method="DELETE", headers=closing)
        with urllib.request.urlopen(delete_request, timeout=3) as deleted:
            assert deleted.status == 204
        assert json.loads(cmds.mayaMcpStatus())["pendingMainThreadRequests"] == 1
        cmds.mayaMcpPump()
        assert cmds.objExists("afterSessionDelete")
        try:
            status(closing, "delete-pending")
            raise AssertionError("Closed MCP session still accepted status requests")
        except urllib.error.HTTPError as error:
            assert error.code == 404
        print("MAYA_MCP_REQUEST_RECOVERY_TEST_RESULT=" + json.dumps({
            "queued_dedup": "passed", "running_dedup": "passed", "replay": "passed",
            "conflict": "passed", "session_isolation": "passed", "failure_replay": "passed",
            "capacity_preflight": "passed", "python_owner_isolation": "passed",
            "python_native_persistence": "passed", "delete_is_not_cancellation": "passed"}))
        cmds.unloadPlugin("maya_mcp")
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
