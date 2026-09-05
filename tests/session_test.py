"""Real Maya persistent Python namespace and SDK contracts."""
from __future__ import annotations

import os
from pathlib import Path
import sys
from unittest.mock import patch

import maya.standalone

PACKAGE = Path(os.environ["MAYA_MCP_TEST_PACKAGE"])
sys.path.insert(0, str(PACKAGE / "maya-mcp" / "scripts"))


def main() -> None:
    maya.standalone.initialize(name="python")
    try:
        import maya.cmds as cmds
        from maya_mcp_runtime import sessions, state
        from maya_mcp_runtime.dispatcher import invoke_tool

        cmds.loadPlugin("maya_mcp")
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True)
        os.environ["MAYA_MCP_ALLOW_UNSAFE_CODE"] = "1"
        owner = ["client-a"]

        def invoke(name, arguments):
            return invoke_tool(name, arguments)["structuredContent"]

        def cell(source, token=None, **kwargs):
            args = {"language": "python", "source": source, **kwargs}
            if token:
                args["session_id"] = token
            return invoke("maya.script.execute", args)

        def action(verb, token=None):
            args = {"action": verb}
            if token:
                args["session_id"] = token
            return invoke("maya.session", args)

        with patch.object(state, "current_client_session", side_effect=lambda: owner[0]):
            opened = action("open")
            assert opened["ok"], opened
            token = opened["data"]["session_id"]
            assert cell("value = arguments['initial']\ndef increment():\n    global value\n    value += 1\n    return value\nresult = increment()", token, arguments={"initial": 40})["data"]["result"] == 41
            assert cell("result = increment()", token)["data"]["result"] == 42
            assert cell("pass", token)["data"]["result"] is None
            assert cell("result = 'value' in globals()")["data"]["result"] is False
            assert cell("result = arguments", token)["data"]["result"] == {}

            owner[0] = "client-b"
            denied = cell("cmds.createNode('transform', name='ownerLeak')", token)
            assert not denied["ok"] and denied["error"]["code"] == "SESSION_NOT_FOUND", denied
            assert not cmds.objExists("ownerLeak")
            assert not action("status", token)["ok"]
            owner[0] = "client-a"
            with patch.dict(os.environ, {"MAYA_MCP_ALLOW_UNSAFE_CODE": "0"}):
                disabled = cell("value = 0", token)
                assert disabled["error"]["code"] == "CAPABILITY_DISABLED", disabled
            assert cell("result = value", token)["data"]["result"] == 42

            made = cell("cube = maya.call('maya.geometry.apply', {'kind':'cube', 'name':'sessionCube'})\nhandle = maya.node('sessionCube')\nresult = handle.name", token)
            assert made["ok"] and made["data"]["sdk_calls"] == 1, made
            cmds.rename("sessionCube", "sessionRenamed")
            assert cell("result = handle.name", token)["data"]["result"].endswith("sessionRenamed")
            cmds.delete("sessionRenamed")
            assert not cell("result = handle.name", token)["ok"]
            kept = cell("key = maya.keep({'values':[1,2,3]})\ncopy = maya.get(key)\ncopy['values'].append(4)\nresult = maya.get(key)", token)
            assert kept["data"]["result"] == {"values": [1, 2, 3]}, kept
            assert cell("result = maya.release(key)", token)["data"]["result"] is True
            assert not cell("result = maya.get(key)", token)["ok"]
            bounded = cell("result = maya.keep('x' * (4 * 1024 * 1024))", token)
            assert not bounded["ok"], bounded
            assert action("status", token)["data"]["result_count"] == 0
            for _ in range(sessions.MAX_RESULTS):
                assert cell("maya.keep(1)", token)["ok"]
            assert not cell("maya.keep(1)", token)["ok"]
            assert action("reset", token)["ok"]
            assert cell("result = 'value' in globals()", token)["data"]["result"] is False
            assert action("status", token)["data"]["result_count"] == 0
            assert not cell("maya.call('maya.script.execute', {'language':'python','source':'pass'})", token)["ok"]
            assert not cell("maya.call('maya.workflow.run', {'steps':[]})", token)["ok"]
            assert not cell("for i in range(65):\n    maya.call('maya.context.get', {})", token)["ok"]
            assert cell("old_sdk = maya", token)["ok"]
            assert not cell("old_sdk.keep(1)", token)["ok"]
            edited = cell("maya.call('maya.geometry.apply', {'kind':'cube','name':'sessionUndo'})", token, undo="chunk")
            assert edited["ok"], edited
            cmds.undo()
            assert not cmds.objExists("sessionUndo")
            cmds.redo()
            assert cmds.objExists("sessionUndo")

            # SDK image blocks survive the script envelope without becoming JSON strings.
            real_invoke = invoke_tool
            def fake_observe(name, arguments, **kwargs):
                if name == "maya.observe":
                    return {"content": [{"type":"image", "mimeType":"image/png", "data":"AAAA"}],
                            "structuredContent": {"ok":True, "data":{"observation_id":"test-observation"}}}
                return real_invoke(name, arguments, **kwargs)
            with patch("maya_mcp_runtime.dispatcher.invoke_tool", side_effect=fake_observe):
                image_result = real_invoke("maya.script.execute", {"language":"python", "source":"result = maya.observe()", "session_id":token})
            assert image_result["content"][0]["type"] == "image", image_result
            assert all(item["type"] == "image" for item in image_result["content"])
            assert image_result["structuredContent"]["data"]["result"]["observation_id"] == "test-observation"
            assert image_result["structuredContent"]["data"]["images"][0]["content_index"] == 0

            assert action("close", token)["ok"]
            assert not cell("pass", token)["ok"]
            token = action("open")["data"]["session_id"]
            assert cell("retained = 5", token)["ok"]
            cmds.file(new=True, force=True)
            assert not cell("pass", token)["ok"]
            assert not sessions._sessions
            token = action("open")["data"]["session_id"]
            with patch.object(sessions.time, "monotonic", return_value=sessions._sessions[token].touched + sessions.IDLE_TTL_SECONDS + 1):
                assert not action("status", token)["ok"]
            token = action("open")["data"]["session_id"]
            namespace, sdk = sessions.begin_cell(token, {})
            with patch.object(sessions.time, "monotonic", return_value=sessions._sessions[token].touched + sessions.IDLE_TTL_SECONDS + 1):
                assert action("status", token)["ok"]
            assert not action("reset", token)["ok"]
            assert not action("close", token)["ok"]
            sessions.end_cell(sdk)
            sessions.clear_sessions()
            for _ in range(sessions.MAX_SESSIONS):
                assert action("open")["ok"]
            assert not action("open")["ok"]
            sessions.clear_sessions()
            token = action("open")["data"]["session_id"]
            invalidated = cell("cmds.file(new=True, force=True)\nmaya.keep(1)", token)
            assert not invalidated["ok"] and not sessions._sessions, invalidated
        print("Persistent session contracts passed")
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
