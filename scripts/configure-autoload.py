"""Configure the installed Maya MCP plug-in to load automatically."""

from __future__ import annotations

import sys
from pathlib import Path

import maya.standalone


def main() -> None:
    if len(sys.argv) not in {2, 3}:
        raise SystemExit("usage: configure-autoload.py PLUGIN_PATH [MAYA_API_VERSION]")
    plugin = Path(sys.argv[1]).resolve()
    expected_api = int(sys.argv[2]) if len(sys.argv) == 3 else None
    if not plugin.is_file():
        raise SystemExit(f"plug-in does not exist: {plugin}")

    maya.standalone.initialize(name="python")
    try:
        import maya.cmds as cmds

        actual_api = int(cmds.about(apiVersion=True))
        if expected_api is not None and actual_api != expected_api:
            raise SystemExit(
                f"Maya API mismatch: package requires {expected_api}, "
                f"but this Maya reports {actual_api}"
            )
        cmds.loadPlugin(str(plugin), quiet=True)
        cmds.pluginInfo("maya_mcp", edit=True, autoload=True)
        print(f"Configured Maya MCP autoload: {plugin}")
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
