"""Run with mayapy; exercises native callbacks without loading the plug-in."""
from __future__ import annotations

import os
from pathlib import Path
import sys

import maya.standalone

package = os.environ.get("MAYA_MCP_TEST_PACKAGE")
sys.path.insert(0, str(Path(package) / "maya-mcp" / "scripts") if package else
                str(Path(__file__).resolve().parents[1] / "python"))


def main() -> None:
    maya.standalone.initialize(name="python")
    try:
        import maya.cmds as cmds
        from maya_mcp_runtime import change_journal as journal, state

        cmds.file(new=True, force=True)
        assert journal.node_count() is None
        state.install_callbacks()
        journal.install()
        assert journal.node_count() == len(cmds.ls(dependencyNodes=True))
        cmds.undoInfo(state=True)
        count_cube = cmds.polyCube(name="countCube")[0]
        assert journal.node_count() == len(cmds.ls(dependencyNodes=True))
        cmds.delete(count_cube)
        assert journal.node_count() == len(cmds.ls(dependencyNodes=True))
        cmds.undo()
        assert journal.node_count() == len(cmds.ls(dependencyNodes=True))
        cmds.redo()
        assert journal.node_count() == len(cmds.ls(dependencyNodes=True))
        cmds.file(new=True, force=True)
        assert journal.node_count() == len(cmds.ls(dependencyNodes=True))
        installed = len(journal._callbacks)
        journal.install()
        assert len(journal._callbacks) == installed == 4
        baseline = journal.cursor()
        cube = cmds.polyCube(name="journalCube")[0]
        assert any(e["kind"] == "node_added" for e in journal.read(baseline)["events"])
        watched = journal.watch([cube, "doesNotExist"])
        assert len(watched["watched_nodes"]) == 1 and len(watched["rejected_nodes"]) == 1
        node_id = watched["watched_nodes"][0]["uuid"]
        assert watched["complete_scene_tracking"] is False
        journal.consume_pending_revision()
        cmds.undoInfo(state=False)
        cmds.setAttr(cube + ".translateX", 1)
        journal.consume_pending_revision()
        signature = state._capture_scene_signature()
        baseline = journal.cursor()
        cmds.setAttr(cube + ".translateX", 7)
        assert signature == state._capture_scene_signature(), "Test requires unchanged fallback signature"
        events = journal.read(baseline)["events"]
        assert any(e["kind"] == "attribute_changed" and e["uuid"] == node_id for e in events), events
        assert journal.consume_pending_revision() and not journal.consume_pending_revision()
        baseline = journal.cursor()
        cmds.select(clear=True)
        cmds.currentTime(12)
        context_events = journal.read(baseline)["events"]
        assert any(e["kind"] == "selection_changed" for e in context_events), context_events
        assert any(e["kind"] == "time_changed" for e in context_events), context_events
        assert not journal.consume_pending_revision()
        baseline = journal.cursor()
        renamed = cmds.rename(cube, "renamedJournalCube")
        assert any(e["kind"] == "node_renamed" and e["name"] == renamed for e in journal.read(baseline)["events"])
        baseline = journal.cursor()
        cmds.delete(renamed)
        assert any(e["kind"] == "node_removed" and e["uuid"] == node_id for e in journal.read(baseline)["events"])
        assert not journal.watch([])["watched_nodes"]

        from unittest.mock import patch
        targets = [cmds.createNode("transform", name=f"watchBound{index}") for index in range(3)]
        with patch.object(journal, "MAX_WATCHED_NODES", 2):
            bounded = journal.watch(targets)
            assert len(bounded["watched_nodes"]) == 2
            assert bounded["rejected_nodes"] == [{"node": targets[-1], "reason": "watch_limit"}]
        journal.reset("test_watch_cleanup")
        baseline = journal.cursor()
        cmds.setAttr(targets[0] + ".translateX", 10)
        assert journal.cursor() == baseline, "reset must remove node callbacks"

        baseline = journal.cursor()
        for index in range(journal.MAX_EVENTS + 3):
            journal._append("test_event", scene=False, value=index)
        assert len(journal._events) == journal.MAX_EVENTS
        overflow = journal.read(baseline)
        assert overflow["requires_observation"] and overflow["reason"] == "journal_overflow"
        baseline = journal.cursor()
        for index in range(3):
            journal._append("test_event", scene=False, value=index)
        page = journal.read(baseline, limit=2)
        assert len(page["events"]) == 2 and page["has_more"]
        tail = journal.read(page["next_cursor"], limit=2)
        assert len(tail["events"]) == 1 and not tail["has_more"]
        assert journal.read("invalid")["requires_observation"]
        assert journal.read()["requires_observation"]
        baseline = journal.cursor()
        journal.reset("test")
        assert journal.read(baseline)["reason"] == "scene_or_journal_reset"
        baseline = journal.cursor()
        state.reset_scene_epoch()
        assert journal.read(baseline)["reason"] == "scene_or_journal_reset"

        import maya.api.OpenMaya as om
        first = cmds.createNode("transform", name="duplicateUuidFirst")
        second = cmds.createNode("transform", name="duplicateUuidSecond")
        selection = om.MSelectionList()
        selection.add(first)
        selection.add(second)
        first_fn = om.MFnDependencyNode(selection.getDependNode(0))
        # Maya rejects directly assigning an existing UUID. Simulate the UUID
        # collision at the lookup boundary while preserving distinct native
        # MObjects; this exercises the defensive identity comparison.
        from unittest.mock import Mock
        native_factory = om.MFnDependencyNode
        def duplicate_identity(node):
            result = Mock(wraps=native_factory(node))
            result.uuid.return_value = first_fn.uuid()
            return result
        baseline = journal.cursor()
        with patch.object(om, "MFnDependencyNode", side_effect=duplicate_identity):
            duplicates = journal.watch([first, second])
        assert duplicates["rejected_nodes"] == [{"node": second, "reason": "duplicate_uuid"}]
        assert journal.read(baseline)["requires_observation"]
        assert journal.node_count() is None, "Coverage gaps must force count fallback"
        journal.reset("duplicate_test_cleanup")
        assert journal.node_count() == len(cmds.ls(dependencyNodes=True))
        cube = cmds.polyCube()[0]
        journal.watch([cube])
        journal.shutdown()
        assert not journal._callbacks and not journal._watched and not journal._events
        assert journal.node_count() is None
        baseline = journal.cursor()
        cmds.setAttr(cube + ".translateX", 99)
        assert journal.cursor() == baseline
        print("Change journal native Maya contracts passed")
    finally:
        from maya_mcp_runtime import change_journal
        change_journal.shutdown()
        state.shutdown_callbacks()
        maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
