"""Batch-safe coverage for production rig operations in maya.node.apply."""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

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


def _invoke(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    from maya_mcp_runtime.dispatcher import dispatch_base64

    payload = json.dumps(
        {"name": name, "arguments": arguments}, separators=(",", ":")
    ).encode("utf-8")
    response = json.loads(
        dispatch_base64(base64.b64encode(payload).decode("ascii"))
    )
    return response["structuredContent"]


def main() -> None:
    maya.standalone.initialize(name="python")
    try:
        import maya.cmds as cmds
        import maya_mcp_runtime

        from maya_mcp_runtime import state

        assert maya_mcp_runtime.__version__ == "0.5.3"
        state.install_callbacks()
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True)

        cmds.select(clear=True)
        root = cmds.joint(name="typedRigRoot_JNT", position=[0, 8, 0])
        knee = cmds.joint(name="typedRigKnee_JNT", position=[0, 4, 1])
        ankle = cmds.joint(name="typedRigAnkle_JNT", position=[0, 0, 0])
        cmds.joint(root, edit=True, orientJoint="xyz", secondaryAxisOrient="yup", children=True)
        cmds.setAttr(f"{ankle}.jointOrient", 0, 0, 0)
        cmds.select(clear=True)

        result = _invoke(
            "maya.node.apply",
            {
                "label": "Build typed IK leg",
                "operations": [
                    {
                        "id": "pv",
                        "op": "create_control",
                        "name": "typedRigPole_CTRL",
                        "shape": "custom",
                        "points": [
                            [0, 0, 1],
                            [0, 1, 0],
                            [0, 0, -1],
                            [0, -1, 0],
                        ],
                        "closed": True,
                        "size": 1.5,
                        "translate": [0, 4, 6],
                        "color_rgb": [1.0, 0.35, 0.05],
                        "line_width": 2.0,
                    },
                    {
                        "id": "ik",
                        "op": "create_ik_handle",
                        "name": "typedRigLeg_IKH",
                        "start_joint": root,
                        "end_joint": ankle,
                        "solver": "ikRPsolver",
                    },
                    {
                        "id": "pvConstraint",
                        "op": "create_constraint",
                        "name": "typedRigLeg_PVC",
                        "constraint_type": "pole_vector",
                        "drivers": ["$pv"],
                        "driven": "$ik",
                    },
                    {
                        "op": "add_attribute",
                        "node": "$pv",
                        "attribute": "ikFk",
                        "nice_name": "IK / FK",
                        "attribute_type": "enum",
                        "enum_names": ["FK", "IK"],
                        "default_value": 1,
                        "keyable": True,
                    },
                    {
                        "op": "add_attribute",
                        "node": "$pv",
                        "attribute": "footRoll",
                        "nice_name": "Foot Roll",
                        "attribute_type": "double",
                        "min_value": -10.0,
                        "max_value": 10.0,
                        "default_value": 0.0,
                        "keyable": True,
                    },
                    {
                        "id": "ikReverse",
                        "op": "create",
                        "node_type": "reverse",
                        "name": "typedRigIkFk_REV",
                    },
                    {
                        "op": "connect",
                        "source": "$pv.ikFk",
                        "destination": "$ikReverse.inputX",
                    },
                    {
                        "op": "set_driven_keys",
                        "driver_plug": "$pv.footRoll",
                        "driven_plug": "$ik.rotateX",
                        "driven_keys": [
                            {"driver_value": -10, "value": -35},
                            {"driver_value": 0, "value": 0},
                            {"driver_value": 10, "value": 55},
                        ],
                    },
                ],
            },
        )
        assert result["ok"] is True, result
        assert result["undo"] == {
            "available": True,
            "label": "Build typed IK leg",
        }, result
        aliases = result["data"]["aliases"]
        control = aliases["pv"]["long_name"]
        handle = aliases["ik"]["long_name"]
        constraint = aliases["pvConstraint"]["long_name"]
        assert cmds.nodeType(handle) == "ikHandle"
        assert cmds.nodeType(constraint) == "poleVectorConstraint"
        assert cmds.attributeQuery("ikFk", node=control, exists=True)
        assert cmds.attributeQuery("footRoll", node=control, exists=True)
        assert (cmds.addAttr(f"{control}.ikFk", query=True, enumName=True) or "") == "FK:IK"
        assert cmds.listConnections(f"{handle}.rotateX", type="animCurve")
        assert cmds.listConnections(f"{control}.ikFk", destination=True, type="reverse")
        assert cmds.listRelatives(control, shapes=True, type="nurbsCurve")

        cmds.undo()
        for node in (
            "typedRigPole_CTRL",
            "typedRigLeg_IKH",
            "typedRigLeg_PVC",
            "typedRigIkFk_REV",
        ):
            assert not cmds.objExists(node), node
        assert cmds.objExists(knee)

        print(
            "MAYA_MCP_RIG_OPERATIONS_TEST_RESULT="
            + json.dumps(
                {
                    "custom_controls": "passed",
                    "driven_keys": "passed",
                    "ik_handle": "passed",
                    "pole_vector": "passed",
                    "production_attributes": "passed",
                    "single_step_undo": "passed",
                },
                sort_keys=True,
            )
        )
    finally:
        try:
            from maya_mcp_runtime import state

            state.shutdown_callbacks()
        except Exception:
            pass
        maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
