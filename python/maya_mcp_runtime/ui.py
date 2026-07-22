"""Small, local-only Maya UI for server and session capability controls."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import maya.cmds as cmds

_MENU = "mayaMcpMenu"
_SCRIPT_ITEM = "mayaMcpAllowScriptsMenuItem"
_TRUE_VALUES = {"1", "true", "yes", "on"}


def script_execution_enabled() -> bool:
    return os.getenv("MAYA_MCP_ALLOW_UNSAFE_CODE", "").lower() in _TRUE_VALUES


def _set_script_execution(enabled: bool) -> None:
    os.environ["MAYA_MCP_ALLOW_UNSAFE_CODE"] = "1" if enabled else "0"
    if cmds.menuItem(_SCRIPT_ITEM, exists=True):
        cmds.menuItem(_SCRIPT_ITEM, edit=True, checkBox=enabled)
    state = "enabled" if enabled else "disabled"
    cmds.inViewMessage(
        assistMessage=f"Maya MCP Python/MEL automation {state} for this session",
        position="topCenter",
        fade=True,
    )
    cmds.warning(
        "Maya MCP: Python/MEL automation is "
        f"{state} for this Maya session. It runs with your full user privileges."
    )


def _toggle_script_execution(*args: Any) -> None:
    enabled = bool(args[0]) if args else not script_execution_enabled()
    _set_script_execution(enabled)


def _show_status(*_: Any) -> None:
    status = json.loads(cmds.mayaMcpStatus())
    message = (
        f"Maya MCP {status.get('version', '?')}\n"
        f"Build: Maya {status.get('mayaTarget', '?')} "
        f"(API {status.get('mayaApiVersion', '?')})\n"
        f"Running: {status.get('running', False)}\n"
        f"Endpoint: {status.get('endpoint', '')}\n"
        "Python/MEL: "
        + ("allowed this session" if script_execution_enabled() else "blocked")
    )
    cmds.confirmDialog(title="Maya MCP Status", message=message, button=["OK"])


def _start_server(*_: Any) -> None:
    cmds.mayaMcpStart()
    _show_status()


def _stop_server(*_: Any) -> None:
    cmds.mayaMcpStop()
    cmds.inViewMessage(
        assistMessage="Maya MCP server stopped",
        position="topCenter",
        fade=True,
    )


def _check_for_updates(*_: Any) -> None:
    from . import updater

    updater.check_for_updates(manual=True)


def _register_client_bridge() -> Path:
    from . import __version__, updater

    package_root = Path(__file__).resolve().parents[2]
    return updater.register_client_bridge(package_root, __version__)


def _configure_clients(*_: Any) -> None:
    package_root = Path(__file__).resolve().parents[2]
    configurator = package_root / "client" / "Configure-MayaMcpClients.ps1"
    local_base = Path(os.getenv("LOCALAPPDATA") or os.getenv("TEMP") or str(Path.home()))
    launcher = local_base / "MayaMCP" / "client" / "Start-MayaMcpBridge.ps1"
    try:
        _register_client_bridge()
    except (OSError, ValueError) as error:
        cmds.confirmDialog(
            title="Configure Maya MCP Clients",
            message=f"Could not register the client bridge:\n{error}",
            button=["OK"],
            icon="critical",
        )
        return
    if not configurator.is_file() or not launcher.is_file():
        cmds.confirmDialog(
            title="Configure Maya MCP Clients",
            message="Client setup files are missing. Run the latest Maya MCP installer again.",
            button=["OK"],
            icon="critical",
        )
        return

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(configurator),
                "-LauncherPath",
                str(launcher),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=flags,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        cmds.confirmDialog(
            title="Configure Maya MCP Clients",
            message=f"Could not start client configuration:\n{error}",
            button=["OK"],
            icon="critical",
        )
        return
    details = (completed.stdout + "\n" + completed.stderr).strip()
    if len(details) > 2000:
        details = details[-2000:]
    if completed.returncode == 0:
        message = details or "Maya MCP client configuration is complete."
        message += "\n\nRestart Codex or Claude Code if it is already open."
        icon = "information"
    else:
        message = details or "Client configuration failed."
        icon = "critical"
    cmds.confirmDialog(
        title="Configure Maya MCP Clients",
        message=message,
        button=["OK"],
        icon=icon,
    )


def install_menu() -> None:
    if cmds.about(batch=True):
        return
    try:
        _register_client_bridge()
    except (OSError, ValueError) as error:
        cmds.warning(f"Maya MCP: could not register the client bridge: {error}")
    remove_menu()
    menu = cmds.menu(_MENU, label="Maya MCP", parent="MayaWindow", tearOff=True)
    cmds.menuItem(label="Server Status...", parent=menu, command=_show_status)
    cmds.menuItem(divider=True, parent=menu)
    cmds.menuItem(
        _SCRIPT_ITEM,
        label="Allow Python/MEL Automation This Session",
        annotation=(
            "Allow the authenticated local MCP client to execute Python or MEL "
            "with your full Maya user privileges until Maya closes"
        ),
        parent=menu,
        checkBox=script_execution_enabled(),
        command=_toggle_script_execution,
    )
    cmds.menuItem(divider=True, parent=menu)
    cmds.menuItem(label="Start / Refresh Server", parent=menu, command=_start_server)
    cmds.menuItem(label="Stop Server", parent=menu, command=_stop_server)
    cmds.menuItem(divider=True, parent=menu)
    cmds.menuItem(label="Configure AI Clients...", parent=menu, command=_configure_clients)
    cmds.menuItem(label="Check for Updates...", parent=menu, command=_check_for_updates)
    if os.getenv("MAYA_MCP_DISABLE_UPDATE_CHECK", "").lower() not in _TRUE_VALUES:
        from . import updater

        updater.start_auto_check()


def remove_menu() -> None:
    updater_module = sys.modules.get("maya_mcp_runtime.updater")
    if updater_module is not None:
        updater_module.shutdown()
    if cmds.about(batch=True):
        return
    if cmds.menu(_MENU, exists=True):
        cmds.deleteUI(_MENU, menu=True)
