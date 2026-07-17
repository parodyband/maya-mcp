"""Configure the installed Maya MCP plug-in to load automatically."""

from __future__ import annotations

import sys
from pathlib import Path

import maya.standalone


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: configure-autoload.py PLUGIN_PATH")
    plugin = Path(sys.argv[1]).resolve()
    if not plugin.is_file():
        raise SystemExit(f"plug-in does not exist: {plugin}")

    maya.standalone.initialize(name="python")
    try:
        import maya.cmds as cmds

        cmds.loadPlugin(str(plugin), quiet=True)
        cmds.pluginInfo("maya_mcp", edit=True, autoload=True)
        print(f"Configured Maya MCP autoload: {plugin}")
    finally:
        maya.standalone.uninitialize()


if __name__ == "__main__":
    main()
