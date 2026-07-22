from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from maya_mcp_runtime import updater


def _archive(version: str = "0.5.4", target: str = "2027", api: int = 20270100) -> bytes:
    major = target.split(".", 1)[0]
    folder = f"maya-mcp-{version}-maya{target}"
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "package-manifest.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "name": "maya-mcp",
                    "version": version,
                    "maya_target": target,
                    "maya_major_version": major,
                    "maya_api_version": api,
                    "platform": "windows-x64",
                }
            ),
        )
        archive.writestr(
            f"maya-mcp-{major}.mod",
            f"+ MAYAVERSION:{major} PLATFORM:win64 maya-mcp {version} ./{folder}\n",
        )
        archive.writestr(f"{folder}/plug-ins/maya_mcp.mll", b"test-plugin")
        archive.writestr(f"{folder}/bin/maya-mcp-bridge.exe", b"test-bridge")
        archive.writestr(
            f"{folder}/client/Start-MayaMcpBridge.ps1", "# test launcher\n"
        )
        archive.writestr(
            f"{folder}/client/Configure-MayaMcpClients.ps1", "# test configurator\n"
        )
        archive.writestr(
            f"{folder}/scripts/maya_mcp_runtime/__init__.py",
            f'__version__ = "{version}"\n',
        )
    return payload.getvalue()


def _metadata(payload: bytes) -> dict[str, object]:
    return {
        "version": "0.5.4",
        "maya_target": "2027",
        "maya_major_version": "2027",
        "maya_api_version": 20270100,
        "name": "maya-mcp-v0.5.4-maya2027-windows-x64.zip",
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "url": "https://example.invalid/package.zip",
        "release_url": "https://example.invalid/release",
    }


def test_selection() -> None:
    payload = _archive()
    metadata = _metadata(payload)
    asset = {
        "name": metadata["name"],
        "state": "uploaded",
        "size": metadata["size"],
        "digest": f"sha256:{metadata['sha256']}",
        "browser_download_url": metadata["url"],
    }
    release = {
        "tag_name": "v0.5.4",
        "draft": False,
        "prerelease": False,
        "html_url": metadata["release_url"],
        "assets": [asset],
    }
    manifest = {
        "schema_version": 1,
        "name": "maya-mcp",
        "version": "0.5.4",
        "assets": [
            {
                "maya_target": "2027",
                "maya_major_version": "2027",
                "maya_api_version": 20270100,
                "platform": "windows-x64",
                "name": metadata["name"],
                "size": metadata["size"],
                "sha256": metadata["sha256"],
            }
        ],
    }
    selected = updater.select_update(release, manifest, "0.4.1", 20270100)
    assert selected is not None and selected["maya_target"] == "2027"
    assert updater.select_update(release, manifest, "0.5.4", 20270100) is None
    assert updater.select_update(release, manifest, "0.4.1", 20260300) is None


def test_install() -> None:
    payload = _archive()
    with tempfile.TemporaryDirectory() as temporary:
        previous_local_app_data = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = temporary
        modules = Path(temporary) / "modules"
        legacy = modules / "maya-mcp.mod"
        modules.mkdir()
        legacy.write_text("+ maya-mcp 0.4.1 ./maya-mcp-0.4.1\n", encoding="utf-8")
        try:
            result = updater.install_archive_bytes(_metadata(payload), payload, modules)
            installed = Path(result["installed"])
            assert (installed / "plug-ins" / "maya_mcp.mll").read_bytes() == b"test-plugin"
            assert Path(result["bridge"]).read_bytes() == b"test-bridge"
            client_root = Path(temporary) / "MayaMCP" / "client"
            shutil.rmtree(client_root)
            migrated_bridge = updater.register_client_bridge(installed, "0.5.4")
            assert migrated_bridge.read_bytes() == b"test-bridge"
            assert (client_root / "Start-MayaMcpBridge.ps1").is_file()
            assert (client_root / "bridge-installations.json").is_file()
            descriptor = modules / "maya-mcp-2027.mod"
            assert "MAYAVERSION:2027" in descriptor.read_text(encoding="utf-8")
            assert not legacy.exists()

            corrupted = payload[:-1] + bytes([payload[-1] ^ 0xFF])
            try:
                updater.install_archive_bytes(_metadata(payload), corrupted, modules)
            except updater.UpdateError as error:
                assert "SHA-256" in str(error)
            else:
                raise AssertionError("Corrupted package was accepted")
        finally:
            if previous_local_app_data is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = previous_local_app_data


def test_path_traversal() -> None:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("../outside.txt", "bad")
    raw = payload.getvalue()
    metadata = _metadata(raw)
    with tempfile.TemporaryDirectory() as temporary:
        try:
            updater.install_archive_bytes(metadata, raw, temporary)
        except updater.UpdateError as error:
            assert "Unsafe" in str(error)
        else:
            raise AssertionError("Path traversal package was accepted")

    payload = _archive()
    malicious = _metadata(payload)
    malicious["maya_target"] = "../../outside"
    with tempfile.TemporaryDirectory() as temporary:
        try:
            updater.install_archive_bytes(malicious, payload, temporary)
        except updater.UpdateError as error:
            assert "identity" in str(error)
        else:
            raise AssertionError("Malicious target identity was accepted")


if __name__ == "__main__":
    test_selection()
    test_install()
    test_path_traversal()
    print("MAYA_MCP_UPDATER_TEST_RESULT=passed")
