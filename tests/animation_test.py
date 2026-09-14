"""Real Maya tests for animation evidence, layer edits, and motion diagnostics."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import maya.standalone

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = os.environ.get("MAYA_MCP_TEST_PACKAGE")
sys.path.insert(0, str(Path(PACKAGE)/"maya-mcp"/"scripts" if PACKAGE else ROOT/"python"))


def main():
    maya.standalone.initialize(name="python")
    try:
        import maya.cmds as cmds
        from maya_mcp_runtime import animation_store as store, state
        from maya_mcp_runtime.animation_math import angle, derivatives
        from maya_mcp_runtime.dispatcher import invoke_tool
        state.install_callbacks()
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True)
        cmds.currentUnit(time="film", linear="cm")
        assertions = []

        def call(name, arguments, error=None):
            result = invoke_tool("maya.animation."+name, arguments)["structuredContent"]
            if error:
                assert not result["ok"] and result["error"]["code"] == error, result
            else:
                assert result["ok"], result
            return result["data"]

        def keys(node, attribute, values, **options):
            return call("edit", {"action": "set_keys", "edits": [{"node": node, "attribute": attribute,
                "keys": [{"time": t, "value": v, "in_tangent": "linear", "out_tangent": "linear"} for t,v in values]}], **options})

        def evidence(token):
            return call("artifact", {"action": "get", "artifact_id": token, "limit": 48})

        mover = cmds.createNode("transform", name="mover")
        foot = cmds.createNode("transform", name="foot", parent=mover)
        chest = cmds.createNode("transform", name="chest")
        keys(mover, "translateX", [(0, 0), (10, 10)])
        profile = call("profile", {"action": "create", "label": "Test rig", "mappings": [
            {"id": "foot", "node": foot, "offset": [0, 0, 0]},
            {"id": "chest", "node": chest, "parent": "foot"}]})["profile_id"]
        cmds.currentTime(7)
        cmds.select(chest)
        cmds.autoKeyframe(state=True)
        old_undo = cmds.undoInfo(query=True, undoName=True)
        baseline = call("sample", {"profile_id": profile, "time_range": [0, 10], "contacts": [
            {"landmark": "foot", "time_range": [0, 10], "support": mover}]})["artifact_id"]
        assert cmds.currentTime(query=True) == 7
        assert cmds.autoKeyframe(query=True, state=True)
        assert cmds.ls(selection=True) == [chest]
        # Reading should not disturb the prior authored undo item.
        assert cmds.undoInfo(query=True, undoName=True) == old_undo
        data = evidence(baseline)
        assert len(data["samples"]) == 11 and data["fps"] == 24
        assert abs(data["samples"][5]["points"]["foot"]["position"][0]-5) < 1e-8
        assert abs(data["samples"][5]["points"]["foot"]["velocity"][0]-24) < 1e-7
        assert max(s["contacts"]["0"]["drift"] for s in data["samples"]) < 1e-8
        assert not call("analyze", {"artifact_id": baseline})["findings"]
        assertions.append("world evaluation, derivatives, moving support, context/undo preservation")
        root_loop = call("analyze", {"artifact_id": baseline, "loop": True, "root_motion": True, "root_landmark": "foot"})
        assert any(f["kind"] == "loop_position" and f["landmark"] == "chest" for f in root_loop["findings"])
        call("analyze", {"artifact_id": baseline, "loop": True, "root_motion": True}, "INVALID_ARGUMENT")
        reachable = call("sample", {"frames": [10], "landmarks": [
            {"id": "root", "node": mover}, {"id": "end", "node": chest, "reach_root": "root", "max_reach": 2}]})["artifact_id"]
        assert call("analyze", {"artifact_id": reachable})["findings"][0]["kind"] == "reach_exceeded"

        cmds.autoKeyframe(state=False)
        keys(foot, "translateX", [(0, 0), (10, 2)])
        keys(foot, "translateY", [(0, 0), (10, -0.5)])
        slipped = call("sample", {"profile_id": profile, "time_range": [0, 10], "contacts": [
            {"landmark": "foot", "time_range": [0, 10], "support": mover}]})["artifact_id"]
        findings = call("analyze", {"artifact_id": slipped})["findings"]
        assert {f["kind"] for f in findings} == {"contact_drift", "contact_penetration"}, findings
        comparison = call("compare", {"before": baseline, "after": slipped, "image_pairs": 0})
        assert comparison["paired_frames"] == 11 and comparison["changes"][0]["max_position_delta"] > 2
        assert evidence(baseline)["samples"][-1]["points"]["foot"]["position"] == [10.0, 0.0, 0.0]
        assertions.append("contact drift/penetration and immutable take comparison")

        cmds.currentUnit(linear="m")
        metric = call("sample", {"landmarks": [{"id": "root", "node": mover, "offset": [1, 0, 0]}], "frames": [10]})["artifact_id"]
        metric_data = evidence(metric)
        assert metric_data["units"]["linear"] == "m"
        assert abs(metric_data["samples"][0]["points"]["root"]["position"][0]-1.1) < 1e-8, metric_data
        exported = call("profile", {"action": "export", "profile_id": profile})
        assert exported["linear_unit"] == "m" and isinstance(exported["create_arguments"]["mappings"][0]["node"], str)
        call("compare", {"before": baseline, "after": metric, "image_pairs": 0}, "INCOMPATIBLE_TAKES")
        cmds.currentUnit(linear="cm")
        assertions.append("Maya internal centimeter conversion and incompatible take rejection")

        renamed = cmds.rename(foot, "renamedFoot")
        assert call("sample", {"profile_id": profile, "frames": [0]})["sample_count"] == 1
        inspected = call("describe", {"targets": [mover], "attributes": ["translateX"], "time_range": [5, 10], "key_limit": 1})
        assert inspected["channels"][0]["curves"][0]["keys"][0]["time"] == 10, inspected
        assert inspected["channels"][0]["curves"][0]["keys"][0]["out_tangent"] == "linear"
        assert inspected["channels"][0]["curves"][0]["keys"][0]["breakdown"] is False
        cmds.setKeyframe(chest+".tx", time=0, value=0, breakdown=True)
        breakdown = call("describe", {"targets": [chest], "attributes": ["translateX"]})
        assert breakdown["channels"][0]["curves"][0]["keys"][0]["breakdown"] is True
        driven = cmds.createNode("transform", name="driven")
        cmds.setDrivenKeyframe(driven+".ty", currentDriver=chest+".ty", driverValue=0, value=1)
        cmds.setDrivenKeyframe(driven+".ty", currentDriver=chest+".ty", driverValue=1, value=3)
        info = call("describe", {"targets": [driven], "attributes": ["translateY"], "time_range": [10, 20]})
        curve = info["channels"][0]["curves"][0]
        assert curve["input_domain"] == "unitless" and not curve["time_range_applied"] and curve["keys"][0]["input"] == 0, info
        call("edit", {"action": "set_keys", "edits": [{"node": driven, "attribute": "translateY", "keys": [{"time": 1, "value": 4}]}]}, "DRIVEN_CHANNEL")
        assert angle([0, 0, 0, 1], [0, 0, 0, -1]) == 0
        assert angle([0, 0, 0, 2], [0, 0, 0, -3]) == 0
        numerical = [{"seconds": t, "points": {"p": {"position": [t*t, 0, 0]}}} for t in (0, 1, 3)]
        derivatives(numerical)
        assert numerical[1]["points"]["p"]["velocity"] == [2.0, 0.0, 0.0]
        assertions.append("profile identity survives rename; scoped tangent inspection; nonuniform differences")

        keyed = cmds.createNode("transform", name="keyed")
        result = call("edit", {"action": "set_keys", "edits": [
            {"node": keyed, "attribute": "translateX", "keys": [{"time": 1, "value": 2}]},
            {"node": keyed, "attribute": "translateY", "keys": [{"time": 1, "value": 8}]}]})
        assert result["keys"] == 2 and cmds.getAttr(keyed+".ty", time=1) == 8
        cmds.undo()
        assert not cmds.keyframe(keyed, query=True, keyframeCount=True)
        cmds.setAttr(keyed+".ty", lock=True)
        call("edit", {"action": "set_keys", "edits": [
            {"node": keyed, "attribute": "translateX", "keys": [{"time": 1, "value": 2}]},
            {"node": keyed, "attribute": "translateY", "keys": [{"time": 1, "value": 8}]}]}, "LOCKED_TARGET")
        assert not cmds.keyframe(keyed, query=True, keyframeCount=True)
        cmds.setAttr(keyed+".ty", lock=False)
        original = cmds.setKeyframe
        counter = [0]
        def fail_second(*args, **kwargs):
            counter[0] += 1
            if counter[0] == 2:
                raise RuntimeError("Injected failure")
            return original(*args, **kwargs)
        with patch.object(cmds, "setKeyframe", fail_second):
            call("edit", {"action": "set_keys", "edits": [{"node": keyed, "attribute": "translateX", "keys": [
                {"time": 1, "value": 2}, {"time": 2, "value": 3}]}]}, "MAYA_ERROR")
        assert not cmds.keyframe(keyed, query=True, keyframeCount=True)
        assertions.append("distinct channel values, undo, preflight and injected failure rollback")

        keys(keyed, "translateX", [(0, 0), (2, 2), (5, 5), (10, 10)])
        call("edit", {"action": "retime", "edits": [{"node": keyed, "attribute": "translateX"}],
                       "time_range": [2, 5], "time_scale": 2, "protected_ranges": [[10, 10]]})
        assert cmds.keyframe(keyed+".tx", query=True, timeChange=True) == [0, 2, 8, 10]
        cmds.undo()
        call("edit", {"action": "retime", "edits": [{"node": keyed, "attribute": "translateX"}],
                       "time_range": [2, 5], "time_offset": 8}, "KEY_COLLISION")
        call("edit", {"action": "delete_keys", "edits": [{"node": keyed, "attribute": "translateX"}],
                       "time_range": [2, 5], "protected_ranges": [[4, 6]]}, "PROTECTED_RANGE")
        call("edit", {"action": "tangents", "edits": [{"node": keyed, "attribute": "translateX",
                       "keys": [{"time": 2, "out_tangent": "flat"}]}]})
        assert cmds.keyTangent(keyed+".tx", query=True, time=(2, 2), outTangentType=True) == ["flat"]
        call("edit", {"action": "tangents", "edits": [{"node": keyed, "attribute": "translateX", "keys": [{"time": 2, "out_weight": 1}]}]}, "INVALID_ARGUMENT")
        assertions.append("retime, collisions, protected keys and tangent editing")

        keys(keyed, "translateX", [(2, 20), (8, 30)], new_layer="CandidateA", time_range=[2, 8])
        assert abs(cmds.getAttr(keyed+".tx", time=2)-20) < 1e-8
        assert abs(cmds.getAttr(keyed+".tx", time=10)-10) < 1e-8
        call("layer", {"layer": "CandidateA", "mute": True})
        assert abs(cmds.getAttr(keyed+".tx", time=2)-2) < 1e-8
        call("layer", {"layer": "CandidateA", "mute": False})
        call("edit", {"action": "set_keys", "edits": [{"node": keyed, "attribute": "translateX",
            "keys": [{"time": 2, "value": 5}]}]}, "AMBIGUOUS_LAYER")
        description = call("describe", {"targets": [keyed], "attributes": ["translateX"]})
        assert any(c["layer"] == "CandidateA" for c in description["channels"][0]["curves"]), description
        cmds.undoInfo(state=False)
        call("edit", {"action": "set_keys", "edits": [{"node": keyed, "attribute": "translateY", "keys": [{"time": 1, "value": 0}]}]}, "UNDO_DISABLED")
        cmds.undoInfo(state=True)
        assertions.append("explicit candidate layer isolation, gating, mute and layer-aware inspection")

        call("artifact", {"action": "notes", "artifact_id": baseline, "notes": [{"text": "Hold the gaze", "time_range": [3, 5], "kind": "beat"}]})
        assert evidence(baseline)["notes"][0]["text"] == "Hold the gaze"
        owner = state._client_session.set("another-client")
        try:
            call("artifact", {"action": "get", "artifact_id": baseline}, "ARTIFACT_NOT_FOUND")
            assert not call("artifact", {"action": "list"})["artifacts"]
        finally:
            state._client_session.reset(owner)
        call("sample", {"profile_id": profile, "time_range": [0, 1000]}, "WORK_LIMIT_EXCEEDED")
        call("sample", {"profile_id": profile, "time_range": [10, 0]}, "INVALID_ARGUMENT")
        call("capture", {"profile_id": profile, "frames": [0]}, "VIEWPORT_UNAVAILABLE")
        call("sample", {"profile_id": profile, "frames": [0], "unknown": True}, "INVALID_ARGUMENT")
        initial = cmds.currentTime(query=True)
        import itertools
        from types import SimpleNamespace
        from maya_mcp_runtime import tools_animation
        ticks = itertools.count(1)
        with patch.object(tools_animation, "time", SimpleNamespace(perf_counter=lambda: next(ticks)*0.2)):
            partial = call("sample", {"profile_id": profile, "time_range": [0, 10], "max_seconds": 1})
        assert not partial["complete"] and 0 < partial["sample_count"] < 11 and partial["missing_frames"]
        sequential = call("sample", {"profile_id": profile, "frames": [2, 5], "preroll": 2, "evaluation": "sequential"})
        assert sequential["sample_count"] == 2 and cmds.currentTime(query=True) == initial
        with patch.object(cmds, "xform", side_effect=RuntimeError("Injected sampling failure")):
            call("sample", {"profile_id": profile, "frames": [3]}, "MAYA_ERROR")
        assert cmds.currentTime(query=True) == initial
        call("artifact", {"action": "release", "artifact_id": baseline})
        call("artifact", {"action": "get", "artifact_id": baseline}, "ARTIFACT_NOT_FOUND")
        cmds.file(new=True, force=True)
        call("artifact", {"action": "get", "artifact_id": slipped}, "ARTIFACT_NOT_FOUND")
        assertions.append("notes, client ownership, bounds, batch refusal, failure restoration, cleanup")
        print(json.dumps({"passed": True, "groups": assertions}, indent=2))
    finally:
        try:
            state.shutdown_callbacks()
        finally:
            maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
