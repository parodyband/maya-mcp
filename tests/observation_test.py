"""Real Maya observation guards and post-action feedback without a GPU."""
from __future__ import annotations

import os
from pathlib import Path
import sys
from unittest.mock import patch

import maya.standalone

sys.path.insert(0, str(Path(os.environ["MAYA_MCP_TEST_PACKAGE"]) / "maya-mcp" / "scripts"))


def main():
    maya.standalone.initialize(name="python")
    try:
        import maya.cmds as cmds
        from maya_mcp_runtime import state
        from maya_mcp_runtime.dispatcher import invoke_tool
        from maya_mcp_runtime.tools_observation import attach_feedback

        cmds.loadPlugin("maya_mcp")
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True)
        cube = cmds.polyCube(name="observedCube")[0]
        def invoke(name, arguments):
            return invoke_tool(name, arguments)["structuredContent"]
        observed = invoke("maya.observe", {"nodes": [cube], "image": False})
        assert observed["ok"], observed
        assert observed["data"]["coherence"]["unchanged_during_observation"]
        assert observed["data"]["nodes"][0]["name"] == cube
        with patch.object(cmds, "ls", side_effect=AssertionError("Signature performed a full node scan")):
            state._capture_scene_signature()
        cursor = observed["data"]["cursor"]
        obs_id = observed["data"]["observation_id"]
        steps = [{"id": "move", "tool": "maya.node.apply", "arguments": {"operations": [
            {"op": "set_transform", "node": cube, "translate": [3, 2, 1]}]}}]
        edited = invoke("maya.workflow.run", {"steps": steps, "if_observation": obs_id,
            "observe": {"image": False, "nodes": [cube], "since": cursor}})
        assert edited["ok"], edited
        feedback = edited["data"]["observation"]
        assert feedback["ok"] and feedback["data"]["nodes"][0]["attributes"]["translate"] == [[3, 2, 1]], edited
        assert feedback["data"]["changes"]["events"], feedback
        stale = invoke("maya.workflow.run", {"steps": steps, "if_observation": obs_id})
        assert stale["error"]["code"] == "STALE_OBSERVATION", stale

        # Attribute edits with undo disabled can leave the old signature intact.
        cmds.undoInfo(stateWithoutFlush=False)
        cmds.setAttr(cube + ".translateX", 4)
        before = invoke("maya.observe", {"image": False, "nodes": [cube]})
        old_revision = state.scene_revision()
        cmds.setAttr(cube + ".translateX", 5)
        latest = invoke("maya.scene.changes", {"cursor": before["data"]["cursor"]})
        assert latest["ok"] and latest["data"]["events"], latest
        assert state.scene_revision() > old_revision
        stale = invoke("maya.workflow.run", {"steps": steps, "if_observation": before["data"]["observation_id"]})
        assert stale["error"]["code"] == "STALE_OBSERVATION", stale
        cmds.undoInfo(stateWithoutFlush=True)

        result = invoke("maya.workflow.run", {"steps": [{"id": "new", "tool": "maya.geometry.apply", "arguments": {
            "kind": "cube", "name": "feedbackRefCube"}}], "observe": {"image": False,
            "nodes": [{"$ref": "new#/data/transform"}]}})
        assert result["ok"] and result["data"]["observation"]["data"]["nodes"][0]["name"] == "feedbackRefCube", result
        invalid = invoke("maya.workflow.run", {"steps": [{"id": "new", "tool": "maya.geometry.apply", "arguments": {
            "kind": "cube", "name": "mustNotCreate"}}], "observe": {"width": 0}})
        assert not invalid["ok"] and not cmds.objExists("mustNotCreate"), invalid
        failed_feedback = invoke("maya.workflow.run", {"steps": [{"id": "new", "tool": "maya.geometry.apply", "arguments": {
            "kind": "cube", "name": "keptAfterFeedbackFailure"}}], "observe": {"image": False, "nodes": ["doesNotExist"]}})
        assert failed_feedback["ok"] and not failed_feedback["data"]["observation"]["ok"], failed_feedback
        assert cmds.objExists("keptAfterFeedbackFailure")
        failed = invoke("maya.workflow.run", {"steps": [{"id": "bad", "tool": "maya.scene.query", "arguments": {
            "scope": "nodes", "nodes": ["missing"]}}], "observe": {"image": False, "nodes": [cube]}})
        assert not failed["ok"] and failed["data"]["observation"]["ok"], failed

        os.environ["MAYA_MCP_ALLOW_UNSAFE_CODE"] = "1"
        script = invoke("maya.script.execute", {"language": "python", "source": "cmds.setAttr('observedCube.translateY', 9)",
                        "observe": {"image": False, "nodes": [cube]}})
        assert script["ok"] and script["data"]["observation"]["data"]["nodes"][0]["attributes"]["translate"][0][1] == 9, script
        partial = invoke("maya.script.execute", {"language": "python", "source": "cmds.setAttr('observedCube.translateY', 10)\nraise RuntimeError('stop')",
                        "observe": {"image": False, "nodes": [cube]}})
        assert not partial["ok"] and partial["data"]["observation"]["ok"], partial

        observed = invoke("maya.observe", {"nodes": [cube], "image": False})
        with patch.object(state, "current_client_session", return_value="other-client"):
            rejected = invoke("maya.workflow.run", {"steps": steps, "if_observation": observed["data"]["observation_id"]})
        assert rejected["error"]["code"] == "OBSERVATION_EXPIRED", rejected
        cmds.file(new=True, force=True)
        assert invoke("maya.scene.changes", {"cursor": cursor})["data"]["requires_observation"]
        cmds.unloadPlugin("maya_mcp")
        print("MAYA_MCP_OBSERVATION_TEST_RESULT=passed guards=true feedback=true scoped_changes=true partial_failure=true")
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
