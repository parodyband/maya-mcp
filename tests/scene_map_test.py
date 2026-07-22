"""Batch-safe unit coverage for viewport scene grounding."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import maya.standalone


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = Path(
    os.environ.get(
        "MAYA_MCP_TEST_PACKAGE",
        ROOT / "build" / "maya2027-mcp-vs2022" / "package",
    )
).resolve()
PACKAGE_SCRIPTS = (PACKAGE_ROOT / "maya-mcp" / "scripts").resolve()
sys.path.insert(0, str(PACKAGE_SCRIPTS))


def _assert_packaged_runtime() -> None:
    import maya_mcp_runtime

    runtime_path = Path(maya_mcp_runtime.__file__).resolve()
    assert PACKAGE_SCRIPTS in runtime_path.parents, runtime_path
    assert maya_mcp_runtime.__version__ == "0.5.0"


class FakeView:
    """Small orthographic view double; no GUI or GPU context is required."""

    @staticmethod
    def portWidth() -> int:
        return 1000

    @staticmethod
    def portHeight() -> int:
        return 800

    @staticmethod
    def modelViewMatrix():
        import maya.api.OpenMaya as om

        return om.MMatrix()

    @staticmethod
    def worldToView(point):
        x = int(round(float(point.x) * 50.0 + 500.0))
        y = int(round(float(point.y) * 50.0 + 400.0))
        visible = point.z < 0.0 and 0 <= x < 1000 and 0 <= y < 800
        return x, y, visible


def main() -> None:
    maya.standalone.initialize(name="python")
    try:
        _assert_packaged_runtime()
        import maya.cmds as cmds

        from maya_mcp_runtime import state
        from maya_mcp_runtime.tools_vision import build_scene_map

        cmds.file(new=True, force=True)
        cube = cmds.polyCube(name="visionProbe")[0]
        cmds.xform(cube, worldSpace=True, translation=[0.0, 0.0, -10.0])

        mapped = build_scene_map(
            {
                "nodes": [{"name": cube}],
                "width": 500,
                "height": 400,
            },
            view=FakeView(),
        )
        assert mapped["resolution"] == {
            "width": 500,
            "height": 400,
            "source_width": 1000,
            "source_height": 800,
        }
        assert mapped["projected_total"] == 1
        assert mapped["truncated"] is False
        record = mapped["objects"][0]
        assert record["node"]["long_name"].endswith("|visionProbe")
        assert record["screen_bounds"]["top_left"]["min"][0] < 250
        assert record["screen_bounds"]["top_left"]["max"][0] > 250
        assert abs(record["pivot"]["screen_top_left"][0] - 250) <= 1
        assert abs(record["pivot"]["screen_top_left"][1] - 199) <= 1
        assert record["camera_depth"] > 0.0
        assert mapped["projection"]["occlusion_tested"] is False

        cmds.setAttr(f"{cube}.visibility", False)
        hidden = build_scene_map(
            {"nodes": [{"name": cube}]}, view=FakeView()
        )
        assert hidden["objects"] == []
        assert hidden["rejected"]["hidden"] == 1

        included = build_scene_map(
            {"nodes": [{"name": cube}], "include_hidden": True},
            view=FakeView(),
        )
        assert len(included["objects"]) == 1
        assert included["objects"][0]["attribute_visible"] is False

        cmds.setAttr(f"{cube}.visibility", True)
        cube_shape = (cmds.listRelatives(cube, shapes=True, fullPath=True) or [])[0]
        cmds.setAttr(f"{cube_shape}.visibility", False)
        hidden_shape = build_scene_map(
            {"nodes": [{"name": cube}]}, view=FakeView()
        )
        assert hidden_shape["objects"] == []
        assert hidden_shape["rejected"]["hidden"] == 1
        cmds.setAttr(f"{cube_shape}.visibility", True)

        mixed_visible = cmds.curve(
            name="mixedVisibilityProbe",
            degree=1,
            point=[(-1.0, -1.0, 0.0), (1.0, 1.0, 0.0)],
        )
        mixed_hidden_source = cmds.curve(
            name="mixedHiddenSource",
            degree=1,
            point=[(100.0, -1.0, 0.0), (102.0, 1.0, 0.0)],
        )
        mixed_hidden_shape = (
            cmds.listRelatives(
                mixed_hidden_source, shapes=True, fullPath=True
            )
            or []
        )[0]
        mixed_hidden_shape = cmds.parent(
            mixed_hidden_shape, mixed_visible, shape=True, relative=True
        )[0]
        cmds.delete(mixed_hidden_source)
        cmds.xform(
            mixed_visible,
            worldSpace=True,
            translation=[0.0, 0.0, -10.0],
        )
        cmds.setAttr(f"{mixed_hidden_shape}.visibility", False)
        mixed_map = build_scene_map(
            {"nodes": [{"name": mixed_visible}]}, view=FakeView()
        )
        assert len(mixed_map["objects"]) == 1
        assert mixed_map["objects"][0]["world_bounds"]["max"][0] < 2.0
        mixed_with_hidden = build_scene_map(
            {
                "nodes": [{"name": mixed_visible}],
                "include_hidden": True,
            },
            view=FakeView(),
        )
        assert mixed_with_hidden["objects"][0]["world_bounds"]["max"][0] > 100.0

        cmds.instance(cube, name="visionProbeInstance")
        shape_reference = state.node_ref(cube_shape)
        assert shape_reference["instanced"] is True
        assert shape_reference["dag_paths_truncated"] is True
        assert shape_reference["dag_path_limit"] == 1
        assert len(shape_reference["dag_paths"]) == 1

        filtered = build_scene_map(
            {"nodes": [{"name": cube}], "node_types": ["joint"]},
            view=FakeView(),
        )
        assert filtered["objects"] == []
        assert filtered["rejected"]["type"] == 1

        # Type filtering must happen before max_candidates truncation. A mesh
        # listed first cannot consume the one matching-joint candidate slot.
        cmds.setAttr(f"{cube}.visibility", True)
        joint = cmds.createNode("joint", name="visionJoint")
        cmds.xform(joint, worldSpace=True, translation=[0.0, 1.0, -10.0])
        type_first = build_scene_map(
            {
                "nodes": [{"name": cube}, {"name": joint}],
                "node_types": ["joint"],
                "max_candidates": 1,
            },
            view=FakeView(),
        )
        assert len(type_first["objects"]) == 1
        assert type_first["objects"][0]["node"]["long_name"].endswith(
            "|visionJoint"
        )
        assert type_first["rejected"]["type"] == 1

        # Only the bounded candidate window may reach expensive bounds and
        # projection work, even when the caller supplies a larger selector set.
        probes = []
        for index in range(3):
            probe = cmds.polyCube(name=f"boundedProbe{index}")[0]
            cmds.xform(
                probe,
                worldSpace=True,
                translation=[float(index), -1.0, -10.0],
            )
            probes.append(probe)
        original_bounds = cmds.exactWorldBoundingBox
        bounds_calls = 0

        def counted_bounds(*args, **kwargs):
            nonlocal bounds_calls
            bounds_calls += 1
            return original_bounds(*args, **kwargs)

        cmds.exactWorldBoundingBox = counted_bounds
        try:
            bounded = build_scene_map(
                {
                    "nodes": [{"name": probe} for probe in probes],
                    "max_candidates": 1,
                },
                view=FakeView(),
            )
        finally:
            cmds.exactWorldBoundingBox = original_bounds
        assert bounds_calls == 1
        assert bounded["candidate_scanned"] == 1
        assert bounded["candidate_scan_truncated"] is True
        assert bounded["candidate_total_is_lower_bound"] is True

        print(
            "MAYA_MCP_SCENE_MAP_TEST_RESULT="
            + json.dumps(
                {
                    "projection": "passed",
                    "resize": "passed",
                    "visibility": "passed",
                    "instancing_bound": "passed",
                    "candidate_bound": "passed",
                    "type_filter_order": "passed",
                },
                sort_keys=True,
            )
        )
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
