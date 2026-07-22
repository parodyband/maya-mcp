"""Batch-safe contract coverage for transient rig placement previews."""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
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


def _assert_packaged_runtime() -> None:
    import maya_mcp_runtime

    runtime_path = Path(maya_mcp_runtime.__file__).resolve()
    assert PACKAGE_SCRIPTS in runtime_path.parents, runtime_path
    assert maya_mcp_runtime.__version__ == "0.5.2"


def _invoke(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    from maya_mcp_runtime.dispatcher import dispatch_base64

    payload = json.dumps(
        {"name": name, "arguments": arguments},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    response = json.loads(
        dispatch_base64(base64.b64encode(payload).decode("ascii"))
    )
    return response["structuredContent"]


def _assert_error(
    response: dict[str, Any], code: str
) -> dict[str, Any]:
    assert response["ok"] is False, response
    assert response["error"]["code"] == code, response
    return response


def _undo_state(cmds: Any) -> tuple[str, str]:
    return (
        cmds.undoInfo(query=True, undoName=True) or "",
        cmds.undoInfo(query=True, redoName=True) or "",
    )


def _can_be_written(node: str) -> bool:
    import maya.api.OpenMaya as om

    selection = om.MSelectionList()
    selection.add(node)
    dependency = selection.getDependNode(0)
    return bool(om.MFnDependencyNode(dependency).canBeWritten())


def main() -> None:
    maya.standalone.initialize(name="python")
    try:
        _assert_packaged_runtime()
        import maya.cmds as cmds

        from maya_mcp_runtime import state
        from maya_mcp_runtime.catalog import CATALOG
        from maya_mcp_runtime.dispatcher import HANDLERS

        tool_names = {tool["name"] for tool in CATALOG["tools"]}
        assert "maya.rig.preview" in tool_names
        assert "maya.viewport.scene_map" in tool_names
        assert "maya.rig.preview" in HANDLERS
        assert "maya.viewport.scene_map" in HANDLERS

        state.install_callbacks()
        cmds.file(new=True, force=True)
        cmds.undoInfo(state=True)
        selection_sentinel = cmds.createNode(
            "transform", name="previewSelectionSentinel"
        )
        cmds.select(selection_sentinel, replace=True)
        cmds.file(modified=False)

        clean_dirty = bool(cmds.file(query=True, modified=True))
        clean_undo = _undo_state(cmds)
        clean_selection = cmds.ls(selection=True, long=True) or []
        create = _invoke(
            "maya.rig.preview",
            {
                "action": "create",
                "name": "Two Joint Preview",
                "joints": [
                    {
                        "id": "root",
                        "name": "previewAcceptRoot_JNT",
                        "position": [0.0, 0.0, 0.0],
                        "radius": 0.6,
                    },
                    {
                        "id": "tip",
                        "name": "previewAcceptTip_JNT",
                        "position": [4.0, 0.0, 0.0],
                        "parent_id": "root",
                        "radius": 0.4,
                    },
                ],
                "controls": [
                    {
                        "id": "rootControl",
                        "name": "previewAcceptRoot_CTRL",
                        "offset_name": "previewAcceptRoot_ZERO",
                        "constraint_name": "previewAcceptRoot_PAR_CON",
                        "target_joint_id": "root",
                        "shape": "circle",
                        "size": 1.5,
                        "color": 18,
                        "constraint": "parent",
                        "maintain_offset": False,
                    },
                    {
                        "id": "tipControl",
                        "name": "previewAcceptTip_CTRL",
                        "offset_name": "previewAcceptTip_ZERO",
                        "target_joint_id": "tip",
                        "parent_id": "rootControl",
                        "shape": "square",
                        "size": 1.0,
                        "color": 17,
                    },
                ],
            },
        )
        assert create["ok"] is True, create
        assert create["undo"]["available"] is False
        assert create["revisions"]["scene_before"] == create["revisions"]["scene_after"]
        # Direct API preview edits do not enter Maya undo and do not falsify
        # the file state. The transient doNotWrite nodes still make Maya's
        # live scene honestly dirty until the user saves or resets it.
        assert clean_dirty is False
        assert bool(cmds.file(query=True, modified=True)) is True
        assert _undo_state(cmds) == clean_undo
        assert (cmds.ls(selection=True, long=True) or []) == clean_selection
        handle_v1 = create["data"]["handle"]
        preview_root_v1 = create["data"]["grouping"]["root"]["long_name"]
        assert cmds.objExists(preview_root_v1)
        exposed_preview_refs = [
            *[
                reference
                for reference in create["data"]["grouping"].values()
                if reference is not None
            ],
            *[
                reference
                for marker in create["data"]["joint_markers"]
                for reference in (marker["marker"], marker["bone"])
                if reference is not None
            ],
            *[
                marker["marker"]
                for marker in create["data"]["control_markers"]
                if marker["marker"] is not None
            ],
        ]
        assert len(exposed_preview_refs) >= 4
        for reference in exposed_preview_refs:
            assert not _can_be_written(reference["long_name"]), reference

        query = _invoke(
            "maya.rig.preview", {"action": "query", "handle": handle_v1}
        )
        assert query["ok"] is True
        assert query["data"]["spec"] == create["data"]["spec"]
        assert (
            query["revisions"]["scene_before"]
            == create["revisions"]["scene_after"]
        )
        listed = _invoke("maya.rig.preview", {"action": "list"})
        assert listed["ok"] is True
        assert listed["data"]["count"] == 1
        assert listed["data"]["previews"][0]["spec"] == create["data"]["spec"]

        before_update_dirty = bool(cmds.file(query=True, modified=True))
        before_update_undo = _undo_state(cmds)
        update = _invoke(
            "maya.rig.preview",
            {
                "action": "update",
                "handle": handle_v1,
                "joint_color": [0.2, 1.0, 0.3],
                "control_color": [0.8, 0.2, 1.0],
            },
        )
        assert update["ok"] is True, update
        handle_v2 = update["data"]["handle"]
        assert handle_v2["preview_id"] == handle_v1["preview_id"]
        assert handle_v2["revision"] == handle_v1["revision"] + 1
        assert bool(cmds.file(query=True, modified=True)) == before_update_dirty
        assert _undo_state(cmds) == before_update_undo
        assert (cmds.ls(selection=True, long=True) or []) == clean_selection
        assert not cmds.objExists(preview_root_v1)
        preview_root_v2 = update["data"]["grouping"]["root"]["long_name"]
        assert cmds.objExists(preview_root_v2)
        _assert_error(
            _invoke(
                "maya.rig.preview",
                {"action": "query", "handle": handle_v1},
            ),
            "PREVIEW_REVISION_CONFLICT",
        )

        # A conflict on the second requested output proves acceptance checks all
        # names before creating the otherwise valid first joint.
        collision = cmds.createNode("transform", name="previewAcceptTip_JNT")
        before_failed_accept_undo = _undo_state(cmds)
        before_failed_accept_selection = (
            cmds.ls(selection=True, long=True) or []
        )
        failed_accept = _assert_error(
            _invoke(
                "maya.rig.preview",
                {"action": "accept", "handle": handle_v2},
            ),
            "OUTPUT_NAME_CONFLICT",
        )
        assert failed_accept["undo"]["available"] is False
        assert _undo_state(cmds) == before_failed_accept_undo
        assert cmds.objExists(preview_root_v2)
        assert not cmds.objExists("previewAcceptRoot_JNT")
        assert (
            cmds.ls(selection=True, long=True) or []
        ) == before_failed_accept_selection
        still_active = _invoke(
            "maya.rig.preview", {"action": "query", "handle": handle_v2}
        )
        assert still_active["ok"] is True
        cmds.delete(collision)
        guard_query = _invoke(
            "maya.rig.preview", {"action": "query", "handle": handle_v2}
        )
        assert guard_query["ok"] is True
        before_accept_selection = cmds.ls(selection=True, long=True) or []

        accepted = _invoke(
            "maya.rig.preview",
            {
                "action": "accept",
                "handle": handle_v2,
                "if_scene_revision": guard_query["revisions"]["scene_after"],
            },
        )
        assert accepted["ok"] is True, accepted
        assert accepted["data"]["status"] == "accepted"
        assert accepted["undo"] == {
            "available": True,
            "label": "Accept rig preview",
        }
        assert accepted["data"]["output"]["counts"] == {
            "joints": 2,
            "controls": 2,
        }
        assert (
            cmds.ls(selection=True, long=True) or []
        ) == before_accept_selection
        assert not cmds.objExists(preview_root_v2)
        for joint in accepted["data"]["output"]["joints"]:
            assert cmds.objExists(joint["node"]["long_name"])
        for control in accepted["data"]["output"]["controls"]:
            assert cmds.objExists(control["control"]["long_name"])
            assert cmds.objExists(control["offset_group"]["long_name"])
        _assert_error(
            _invoke(
                "maya.rig.preview",
                {"action": "query", "handle": handle_v2},
            ),
            "PREVIEW_NOT_FOUND",
        )

        cmds.undo()
        for name in (
            "previewAcceptRoot_JNT",
            "previewAcceptTip_JNT",
            "previewAcceptRoot_CTRL",
            "previewAcceptTip_CTRL",
            "previewAcceptRoot_ZERO",
            "previewAcceptTip_ZERO",
            "previewAcceptRoot_PAR_CON",
        ):
            assert not cmds.objExists(name), name

        # cmds.curve initially chooses generic curveShape names for square and
        # cube controls. Acceptance must rename both generated shapes exactly,
        # and the complete result must remain one Maya undo step.
        polygonal_control_preview = _invoke(
            "maya.rig.preview",
            {
                "action": "create",
                "controls": [
                    {
                        "id": "squareAccept",
                        "name": "squareAccept_CTRL",
                        "offset_name": "squareAccept_ZERO",
                        "position": [0.0, 0.0, 0.0],
                        "shape": "square",
                    },
                    {
                        "id": "cubeAccept",
                        "name": "cubeAccept_CTRL",
                        "offset_name": "cubeAccept_ZERO",
                        "position": [2.0, 0.0, 0.0],
                        "shape": "cube",
                    },
                ],
            },
        )
        assert polygonal_control_preview["ok"] is True
        polygonal_accept = _invoke(
            "maya.rig.preview",
            {
                "action": "accept",
                "handle": polygonal_control_preview["data"]["handle"],
            },
        )
        assert polygonal_accept["ok"] is True, polygonal_accept
        for name in (
            "squareAccept_CTRL",
            "squareAccept_CTRLShape",
            "squareAccept_ZERO",
            "cubeAccept_CTRL",
            "cubeAccept_CTRLShape",
            "cubeAccept_ZERO",
        ):
            assert cmds.objExists(name), name
        cmds.undo()
        for name in (
            "squareAccept_CTRL",
            "squareAccept_CTRLShape",
            "squareAccept_ZERO",
            "cubeAccept_CTRL",
            "cubeAccept_CTRLShape",
            "cubeAccept_ZERO",
        ):
            assert not cmds.objExists(name), name

        # Generated curve-shape names participate in the same preflight as
        # requested DG outputs. A constraint may not silently steal a shape
        # name and receive Maya's automatic numeric suffix.
        shape_collision_preview = _invoke(
            "maya.rig.preview",
            {
                "action": "create",
                "joints": [
                    {
                        "id": "shapeCollisionJoint",
                        "name": "shapeCollision_JNT",
                        "position": [0.0, 0.0, 0.0],
                    }
                ],
                "controls": [
                    {
                        "id": "shapeCollisionControl",
                        "name": "shapeCollision_CTRL",
                        "constraint_name": "shapeCollision_CTRLShape",
                        "target_joint_id": "shapeCollisionJoint",
                        "constraint": "parent",
                    }
                ],
            },
        )
        assert shape_collision_preview["ok"] is True
        shape_collision_handle = shape_collision_preview["data"]["handle"]
        shape_collision_root = shape_collision_preview["data"]["grouping"][
            "root"
        ]["long_name"]
        _assert_error(
            _invoke(
                "maya.rig.preview",
                {"action": "accept", "handle": shape_collision_handle},
            ),
            "OUTPUT_NAME_CONFLICT",
        )
        assert cmds.objExists(shape_collision_root)
        assert not cmds.objExists(":shapeCollision_JNT")
        assert not cmds.objExists(":shapeCollision_CTRL")
        assert _invoke(
            "maya.rig.preview",
            {"action": "cancel", "handle": shape_collision_handle},
        )["ok"] is True

        # Joint/control arrays are canonical replacement fields on update, not
        # partial collections merged by id. Every view exposes the exact spec
        # that a later accept would consume.
        collection_preview = _invoke(
            "maya.rig.preview",
            {
                "action": "create",
                "joints": [
                    {"id": "oldA", "position": [0.0, 0.0, 0.0]},
                    {
                        "id": "oldB",
                        "position": [1.0, 0.0, 0.0],
                        "parent_id": "oldA",
                    },
                ],
                "controls": [
                    {
                        "id": "oldControl",
                        "target_joint_id": "oldA",
                    }
                ],
            },
        )
        assert collection_preview["ok"] is True
        collection_update = _invoke(
            "maya.rig.preview",
            {
                "action": "update",
                "handle": collection_preview["data"]["handle"],
                "joints": [
                    {"id": "newOnly", "position": [2.0, 0.0, 0.0]}
                ],
                "controls": [
                    {
                        "id": "newControlOnly",
                        "position": [2.0, 1.0, 0.0],
                    }
                ],
            },
        )
        assert collection_update["ok"] is True, collection_update
        replacement_spec = collection_update["data"]["spec"]
        assert [item["id"] for item in replacement_spec["joints"]] == [
            "newOnly"
        ]
        assert [item["id"] for item in replacement_spec["controls"]] == [
            "newControlOnly"
        ]
        collection_query = _invoke(
            "maya.rig.preview",
            {
                "action": "query",
                "handle": collection_update["data"]["handle"],
            },
        )
        assert collection_query["data"]["spec"] == replacement_spec
        collection_list = _invoke("maya.rig.preview", {"action": "list"})
        listed_collection = next(
            item
            for item in collection_list["data"]["previews"]
            if item["handle"]["preview_id"]
            == collection_update["data"]["handle"]["preview_id"]
        )
        assert listed_collection["spec"] == replacement_spec
        assert _invoke(
            "maya.rig.preview",
            {
                "action": "cancel",
                "handle": collection_update["data"]["handle"],
            },
        )["ok"] is True

        before_cancel_dirty = bool(cmds.file(query=True, modified=True))
        before_cancel_undo = _undo_state(cmds)
        before_cancel_selection = cmds.ls(selection=True, long=True) or []
        cancellable = _invoke(
            "maya.rig.preview",
            {
                "action": "create",
                "name": "Cancellable Preview",
                "controls": [
                    {
                        "id": "worldControl",
                        "position": [1.0, 2.0, 3.0],
                        "rotation": [0.0, 30.0, 0.0],
                        "shape": "cube",
                    }
                ],
            },
        )
        assert cancellable["ok"] is True
        cancel_handle = cancellable["data"]["handle"]
        cancel_root = cancellable["data"]["grouping"]["root"]["long_name"]
        assert cmds.objExists(cancel_root)
        cancelled = _invoke(
            "maya.rig.preview",
            {"action": "cancel", "handle": cancel_handle},
        )
        assert cancelled["ok"] is True
        assert cancelled["data"]["status"] == "cancelled"
        assert not cmds.objExists(cancel_root)
        assert bool(cmds.file(query=True, modified=True)) == before_cancel_dirty
        assert _undo_state(cmds) == before_cancel_undo
        assert (
            cmds.ls(selection=True, long=True) or []
        ) == before_cancel_selection

        cleanup_failure = _invoke(
            "maya.rig.preview",
            {
                "action": "create",
                "joints": [
                    {
                        "id": "cleanupJoint",
                        "name": "previewCleanupFailure_JNT",
                        "position": [0.0, 2.0, 0.0],
                    }
                ],
            },
        )
        assert cleanup_failure["ok"] is True
        cleanup_handle = cleanup_failure["data"]["handle"]
        cleanup_root = cleanup_failure["data"]["grouping"]["root"]["long_name"]

        from maya_mcp_runtime import tools_rig_preview

        original_destroy = tools_rig_preview._destroy_preview

        def injected_cleanup_failure(
            record: dict[str, Any], *, strict: bool
        ) -> bool:
            raise RuntimeError("injected post-commit cleanup failure")

        tools_rig_preview._destroy_preview = injected_cleanup_failure
        try:
            accepted_with_warning = _invoke(
                "maya.rig.preview",
                {"action": "accept", "handle": cleanup_handle},
            )
        finally:
            tools_rig_preview._destroy_preview = original_destroy

        assert accepted_with_warning["ok"] is True, accepted_with_warning
        assert (
            accepted_with_warning["data"]["status"]
            == "accepted_preview_cleanup_failed"
        )
        assert accepted_with_warning["undo"]["available"] is True
        assert {
            warning["code"]
            for warning in accepted_with_warning["warnings"]
        } == {"PREVIEW_CLEANUP_FAILED"}
        assert cmds.objExists("previewCleanupFailure_JNT")
        assert cmds.objExists(cleanup_root)
        cleanup_query = _invoke(
            "maya.rig.preview",
            {"action": "query", "handle": cleanup_handle},
        )
        assert cleanup_query["ok"] is True
        assert cleanup_query["data"]["status"] == "accepted_cleanup_failed"
        _assert_error(
            _invoke(
                "maya.rig.preview",
                {"action": "accept", "handle": cleanup_handle},
            ),
            "PREVIEW_NOT_ACTIVE",
        )
        cleanup_retry = _invoke(
            "maya.rig.preview",
            {"action": "cancel", "handle": cleanup_handle},
        )
        assert cleanup_retry["ok"] is True
        assert cleanup_retry["data"]["status"] == "cleanup_completed"
        assert not cmds.objExists(cleanup_root)
        assert cmds.objExists("previewCleanupFailure_JNT")
        cmds.undo()
        assert not cmds.objExists("previewCleanupFailure_JNT")

        with tempfile.TemporaryDirectory() as directory:
            serialization_preview = _invoke(
                "maya.rig.preview",
                {
                    "action": "create",
                    "joints": [
                        {
                            "id": "serializationJoint",
                            "position": [0.0, 3.0, 0.0],
                        }
                    ],
                },
            )
            assert serialization_preview["ok"] is True
            serialization_handle = serialization_preview["data"]["handle"]
            serialization_root = serialization_preview["data"]["grouping"][
                "root"
            ]["long_name"]
            assert cmds.objExists(serialization_root)
            scene_path = Path(directory) / "preview_serialization_probe.ma"
            cmds.file(rename=str(scene_path))
            cmds.file(save=True, type="mayaAscii", force=True)
            saved_text = scene_path.read_text(encoding="utf-8", errors="replace")
            assert "MAYA_MCP_RIG_PREVIEW_" not in saved_text
            assert "mayaMcpRigPreview" not in saved_text
            assert cmds.objExists(serialization_root)
            serialization_cancel = _invoke(
                "maya.rig.preview",
                {"action": "cancel", "handle": serialization_handle},
            )
            assert serialization_cancel["ok"] is True

        from maya_mcp_runtime import tools_rig_preview
        import maya.api.OpenMaya as om

        # A synchronous third-party callback may mutate the scene while an API
        # node is being created. Its node must never be adopted/tagged, its undo
        # command and dirty state must remain visible, and strict cleanup must
        # refuse to delete it as an arbitrary descendant.
        cmds.file(modified=False)
        callback_undo = _undo_state(cmds)
        callback_state: dict[str, Any] = {
            "armed": True,
            "external": None,
        }

        def create_external_descendant(
            node: Any, _client_data: Any
        ) -> None:
            if not callback_state["armed"]:
                return
            callback_state["armed"] = False
            parent = om.MFnDagNode(node).fullPathName()
            callback_state["external"] = cmds.createNode(
                "transform",
                name="previewCallbackExternal",
                parent=parent,
            )

        callback_id = om.MDGMessage.addNodeAddedCallback(
            create_external_descendant, "transform"
        )
        try:
            callback_preview = _invoke(
                "maya.rig.preview",
                {
                    "action": "create",
                    "controls": [
                        {
                            "id": "callbackControl",
                            "position": [0.0, 0.0, 0.0],
                        }
                    ],
                },
            )
        finally:
            om.MMessage.removeCallback(callback_id)
        assert callback_preview["ok"] is True, callback_preview
        callback_handle = callback_preview["data"]["handle"]
        callback_root = callback_preview["data"]["grouping"]["root"][
            "long_name"
        ]
        external = str(callback_state["external"])
        external_uuid = (cmds.ls(external, uuid=True) or [""])[0]
        callback_record = tools_rig_preview._PREVIEWS[
            callback_handle["preview_id"]
        ]
        assert external_uuid
        assert external_uuid not in {
            entry["uuid"] for entry in callback_record["owned_nodes"]
        }
        assert not cmds.attributeQuery(
            "mayaMcpRigPreview", node=external, exists=True
        )
        assert bool(cmds.file(query=True, modified=True)) is True
        assert _undo_state(cmds) != callback_undo
        callback_query = _invoke(
            "maya.rig.preview",
            {"action": "query", "handle": callback_handle},
        )
        assert callback_query["ok"] is True
        assert (
            callback_query["revisions"]["scene_before"]
            > callback_preview["revisions"]["scene_after"]
        )
        callback_cancel = _assert_error(
            _invoke(
                "maya.rig.preview",
                {"action": "cancel", "handle": callback_handle},
            ),
            "PREVIEW_TAMPERED",
        )
        assert callback_cancel["ok"] is False
        assert cmds.objExists(external)
        assert cmds.objExists(callback_root)
        assert callback_handle["preview_id"] in tools_rig_preview._PREVIEWS
        external = (cmds.parent(external, world=True) or [external])[0]
        assert _invoke(
            "maya.rig.preview",
            {"action": "cancel", "handle": callback_handle},
        )["ok"] is True
        assert cmds.objExists(external)
        cmds.delete(external)

        # A surviving UUID with changed ownership metadata is damage, not an
        # absent node. User and lifecycle cleanup both retain its record.
        tamper_preview = _invoke(
            "maya.rig.preview",
            {
                "action": "create",
                "controls": [
                    {
                        "id": "tamperControl",
                        "position": [0.0, 1.0, 0.0],
                    }
                ],
            },
        )
        assert tamper_preview["ok"] is True
        tamper_handle = tamper_preview["data"]["handle"]
        tamper_root = tamper_preview["data"]["grouping"]["root"]["long_name"]
        original_preview_id = str(
            cmds.getAttr(f"{tamper_root}.mayaMcpPreviewId")
        )
        cmds.setAttr(
            f"{tamper_root}.mayaMcpPreviewId",
            "tampered-preview-id",
            type="string",
        )
        _assert_error(
            _invoke(
                "maya.rig.preview",
                {"action": "cancel", "handle": tamper_handle},
            ),
            "PREVIEW_TAMPERED",
        )
        assert tamper_handle["preview_id"] in tools_rig_preview._PREVIEWS
        assert cmds.objExists(tamper_root)
        tools_rig_preview._cleanup_previews("test_tamper")
        assert tamper_handle["preview_id"] in tools_rig_preview._PREVIEWS
        assert cmds.objExists(tamper_root)
        cmds.setAttr(
            f"{tamper_root}.mayaMcpPreviewId",
            original_preview_id,
            type="string",
        )
        assert _invoke(
            "maya.rig.preview",
            {"action": "cancel", "handle": tamper_handle},
        )["ok"] is True

        # If construction itself fails and rollback cannot complete, every
        # surviving UUID is retained under a queryable/cancellable cleanup
        # handle instead of becoming an untracked scene orphan.
        original_preview_rgb = tools_rig_preview._set_api_rgb_override
        original_preview_rollback = tools_rig_preview._rollback_owned_entries

        def injected_build_failure(
            _node: Any, _color: list[float]
        ) -> None:
            raise RuntimeError("injected preview build failure")

        tools_rig_preview._set_api_rgb_override = injected_build_failure
        tools_rig_preview._rollback_owned_entries = lambda _entries: False
        try:
            build_failure = _assert_error(
                _invoke(
                    "maya.rig.preview",
                    {
                        "action": "create",
                        "controls": [
                            {
                                "id": "failedBuildControl",
                                "position": [0.0, 0.0, 0.0],
                            }
                        ],
                    },
                ),
                "PREVIEW_BUILD_ROLLBACK_FAILED",
            )
        finally:
            tools_rig_preview._set_api_rgb_override = original_preview_rgb
            tools_rig_preview._rollback_owned_entries = (
                original_preview_rollback
            )
        build_cleanup_handle = build_failure["error"]["details"][
            "cleanup_handle"
        ]
        assert build_cleanup_handle["preview_id"] in tools_rig_preview._PREVIEWS
        build_cleanup_query = _invoke(
            "maya.rig.preview",
            {"action": "query", "handle": build_cleanup_handle},
        )
        assert build_cleanup_query["ok"] is True
        assert build_cleanup_query["data"]["status"] == "build_cleanup_failed"
        assert _invoke(
            "maya.rig.preview",
            {"action": "cancel", "handle": build_cleanup_handle},
        )["ok"] is True

        # The same retention guarantee applies when update preserves the old
        # preview but cleanup of the uncommitted replacement also fails.
        failed_update_source = _invoke(
            "maya.rig.preview",
            {
                "action": "create",
                "controls": [
                    {
                        "id": "failedUpdateSource",
                        "position": [0.0, 0.0, 0.0],
                    }
                ],
            },
        )
        assert failed_update_source["ok"] is True
        failed_update_handle = failed_update_source["data"]["handle"]
        failed_update_record = tools_rig_preview._PREVIEWS[
            failed_update_handle["preview_id"]
        ]
        original_preview_destroy = tools_rig_preview._destroy_preview

        def injected_update_cleanup_failure(
            candidate: dict[str, Any], *, strict: bool
        ) -> bool:
            if candidate is failed_update_record:
                raise RuntimeError("injected original cleanup failure")
            return False

        tools_rig_preview._destroy_preview = (
            injected_update_cleanup_failure
        )
        try:
            failed_update = _assert_error(
                _invoke(
                    "maya.rig.preview",
                    {
                        "action": "update",
                        "handle": failed_update_handle,
                        "control_color": [0.1, 0.2, 0.3],
                    },
                ),
                "PREVIEW_UPDATE_ROLLBACK_FAILED",
            )
        finally:
            tools_rig_preview._destroy_preview = original_preview_destroy
        update_cleanup_handle = failed_update["error"]["details"][
            "cleanup_handle"
        ]
        assert update_cleanup_handle["preview_id"] in tools_rig_preview._PREVIEWS
        assert failed_update_handle["preview_id"] in tools_rig_preview._PREVIEWS
        assert (
            _invoke(
                "maya.rig.preview",
                {"action": "query", "handle": update_cleanup_handle},
            )["data"]["status"]
            == "update_cleanup_failed"
        )
        assert _invoke(
            "maya.rig.preview",
            {"action": "cancel", "handle": update_cleanup_handle},
        )["ok"] is True
        assert _invoke(
            "maya.rig.preview",
            {"action": "cancel", "handle": failed_update_handle},
        )["ok"] is True

        # Cleanup is UUID-complete, not root-only. If an owned marker is
        # detached from the preview hierarchy it is still deleted and verified.
        detached_preview = _invoke(
            "maya.rig.preview",
            {
                "action": "create",
                "controls": [
                    {
                        "id": "detachedControl",
                        "position": [1.0, 1.0, 1.0],
                    }
                ],
            },
        )
        assert detached_preview["ok"] is True
        detached_handle = detached_preview["data"]["handle"]
        detached_root = detached_preview["data"]["grouping"]["root"][
            "long_name"
        ]
        detached_marker = detached_preview["data"]["control_markers"][0][
            "marker"
        ]["long_name"]
        detached_marker = (
            cmds.parent(detached_marker, world=True) or [detached_marker]
        )[0]
        assert _invoke(
            "maya.rig.preview",
            {"action": "cancel", "handle": detached_handle},
        )["ok"] is True
        assert not cmds.objExists(detached_root)
        assert not cmds.objExists(detached_marker)

        # Accept is a permanent transaction and therefore refuses before
        # preflight or mutation if Maya cannot record an undo chunk.
        undo_disabled_preview = _invoke(
            "maya.rig.preview",
            {
                "action": "create",
                "joints": [
                    {
                        "id": "undoDisabled",
                        "name": "undoDisabledAccept_JNT",
                        "position": [0.0, 0.0, 0.0],
                    }
                ],
            },
        )
        assert undo_disabled_preview["ok"] is True
        undo_disabled_handle = undo_disabled_preview["data"]["handle"]
        undo_disabled_root = undo_disabled_preview["data"]["grouping"]["root"][
            "long_name"
        ]
        cmds.undoInfo(stateWithoutFlush=False)
        try:
            _assert_error(
                _invoke(
                    "maya.rig.preview",
                    {"action": "accept", "handle": undo_disabled_handle},
                ),
                "UNDO_DISABLED",
            )
            assert not cmds.objExists(":undoDisabledAccept_JNT")
            assert cmds.objExists(undo_disabled_root)
        finally:
            cmds.undoInfo(stateWithoutFlush=True)
        assert _invoke(
            "maya.rig.preview",
            {"action": "cancel", "handle": undo_disabled_handle},
        )["ok"] is True

        # Unqualified output names are always created in Maya's root namespace,
        # while the caller's active namespace is restored exactly.
        namespace = "rigPreviewActiveNamespace"
        if not cmds.namespace(exists=namespace):
            cmds.namespace(add=namespace)
        previous_namespace = str(
            cmds.namespaceInfo(currentNamespace=True, absoluteName=True)
        )
        cmds.namespace(set=f":{namespace}")
        try:
            namespace_preview = _invoke(
                "maya.rig.preview",
                {
                    "action": "create",
                    "joints": [
                        {
                            "id": "rootNamespace",
                            "name": "rootNamespacePreview_JNT",
                            "position": [0.0, 0.0, 0.0],
                        }
                    ],
                },
            )
            assert namespace_preview["ok"] is True
            namespace_accepted = _invoke(
                "maya.rig.preview",
                {
                    "action": "accept",
                    "handle": namespace_preview["data"]["handle"],
                },
            )
            assert namespace_accepted["ok"] is True, namespace_accepted
            assert (
                cmds.namespaceInfo(currentNamespace=True, absoluteName=True)
                == f":{namespace}"
            )
            namespace_joint = namespace_accepted["data"]["output"]["joints"][
                0
            ]["node"]
            assert namespace_joint["long_name"] == "|rootNamespacePreview_JNT"
            assert cmds.objExists(":rootNamespacePreview_JNT")
            assert not cmds.objExists(
                f":{namespace}:rootNamespacePreview_JNT"
            )
            cmds.undo()
            assert not cmds.objExists(":rootNamespacePreview_JNT")
        finally:
            cmds.namespace(set=previous_namespace)
        cmds.namespace(removeNamespace=namespace)

        # Non-expiring records and list output are bounded. The node cap is
        # checked before even a single preview node is allocated.
        bounded_handles: list[dict[str, Any]] = []
        for index in range(tools_rig_preview._MAX_ACTIVE_PREVIEWS):
            bounded = _invoke(
                "maya.rig.preview",
                {
                    "action": "create",
                    "controls": [
                        {
                            "id": f"bounded{index}",
                            "position": [float(index), 0.0, 0.0],
                        }
                    ],
                },
            )
            assert bounded["ok"] is True, bounded
            bounded_handles.append(bounded["data"]["handle"])
        bounded_list = _invoke("maya.rig.preview", {"action": "list"})
        assert bounded_list["ok"] is True
        assert (
            bounded_list["data"]["count"]
            == tools_rig_preview._MAX_ACTIVE_PREVIEWS
        )
        assert (
            len(bounded_list["data"]["previews"])
            == tools_rig_preview._MAX_ACTIVE_PREVIEWS
        )
        _assert_error(
            _invoke(
                "maya.rig.preview",
                {
                    "action": "create",
                    "controls": [
                        {
                            "id": "overActiveLimit",
                            "position": [0.0, 0.0, 0.0],
                        }
                    ],
                },
            ),
            "PREVIEW_LIMIT_EXCEEDED",
        )
        for handle in bounded_handles:
            assert _invoke(
                "maya.rig.preview",
                {"action": "cancel", "handle": handle},
            )["ok"] is True

        original_node_limit = tools_rig_preview._MAX_TOTAL_OWNED_NODES
        tools_rig_preview._MAX_TOTAL_OWNED_NODES = 6
        try:
            _assert_error(
                _invoke(
                    "maya.rig.preview",
                    {
                        "action": "create",
                        "controls": [
                            {
                                "id": "overNodeLimit",
                                "position": [0.0, 0.0, 0.0],
                            }
                        ],
                    },
                ),
                "PREVIEW_NODE_LIMIT_EXCEEDED",
            )
            assert _invoke(
                "maya.rig.preview", {"action": "list"}
            )["data"]["count"] == 0
        finally:
            tools_rig_preview._MAX_TOTAL_OWNED_NODES = original_node_limit

        epoch_preview = _invoke(
            "maya.rig.preview",
            {
                "action": "create",
                "joints": [
                    {"id": "epochJoint", "position": [0.0, 1.0, 0.0]}
                ],
            },
        )
        assert epoch_preview["ok"] is True
        epoch_handle = epoch_preview["data"]["handle"]
        cmds.file(new=True, force=True)
        assert state.scene_epoch() != epoch_handle["scene_epoch"]
        _assert_error(
            _invoke(
                "maya.rig.preview",
                {"action": "query", "handle": epoch_handle},
            ),
            "SCENE_EPOCH_MISMATCH",
        )

        print(
            "MAYA_MCP_RIG_PREVIEW_TEST_RESULT="
            + json.dumps(
                {
                    "accept_atomicity": "passed",
                    "callback_isolation": "passed",
                    "cleanup_integrity": "passed",
                    "cleanup_retention": "passed",
                    "epoch_handles": "passed",
                    "limits": "passed",
                    "namespace": "passed",
                    "serialization": "passed",
                    "spec_roundtrip": "passed",
                    "transient_edits": "passed",
                    "undo_disabled": "passed",
                    "undo": "passed",
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
