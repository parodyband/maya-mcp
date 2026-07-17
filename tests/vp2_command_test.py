"""Batch-safe contract checks for the native VP2 capture command."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import maya.standalone


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = (
    ROOT
    / "build"
    / "maya2027-mcp-vs2022"
    / "package"
    / "maya-mcp"
    / "plug-ins"
    / "maya_mcp.mll"
)
SCRIPTS = PLUGIN.parents[1] / 'scripts'


def invoke(request: dict[str, object]) -> dict[str, object]:
    import maya.cmds as cmds

    result = cmds.mayaMcpVp2Capture(
        request=json.dumps(request, separators=(",", ":"))
    )
    payload = json.loads(result)
    assert isinstance(payload, dict)
    return payload


def main() -> None:
    maya.standalone.initialize(name="python")
    loaded = False
    try:
        import maya.cmds as cmds

        assert PLUGIN.is_file(), f"Build the Release plug-in first: {PLUGIN}"
        sys.path.insert(0, str(SCRIPTS))
        cmds.loadPlugin(str(PLUGIN), quiet=True)
        loaded = True

        unavailable = invoke({})
        assert unavailable["ok"] is False
        assert unavailable["error"]["code"] == "VIEWPORT_UNAVAILABLE"
        assert unavailable["capabilities"]["depth"]["supported"] is True
        assert unavailable["limits"]["hard_max_dimension"] == 1024
        assert unavailable["limits"]["base64_budget_chars"] == 4 * 1024 * 1024

        object_id = invoke(
            {"depth": False, "color": False, "object_id": True}
        )
        assert object_id["ok"] is False
        assert object_id["error"]["code"] == "UNSUPPORTED_PASS"

        for request in (
            {"unknown": True},
            {"max_dimension": 1025},
            {"depth": False, "color": False, "object_id": False},
            {"depth": "yes"},
        ):
            invalid = invoke(request)
            assert invalid["ok"] is False, invalid
            assert invalid["error"]["code"] == "INVALID_ARGUMENT", invalid

        print(
            "MAYA_MCP_VP2_COMMAND_TEST_RESULT="
            + json.dumps(
                {
                    "batch_safety": "passed",
                    "limits": "passed",
                    "object_id_contract": "passed",
                    "strict_parser": "passed",
                },
                sort_keys=True,
            )
        )
    finally:
        if loaded:
            import maya.cmds as cmds

            cmds.unloadPlugin(PLUGIN.stem, force=True)
        maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
