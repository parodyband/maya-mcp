"""GitHub Releases updater with exact Maya-API and package-integrity checks."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import threading
import time
import urllib.request
import webbrowser
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from . import __version__

_REPOSITORY = "parodyband/maya-mcp"
_LATEST_RELEASE_API = f"https://api.github.com/repos/{_REPOSITORY}/releases/latest"
_MANIFEST_NAME = "release-manifest.json"
_USER_AGENT = f"maya-mcp/{__version__} updater"
_CHECK_INTERVAL_SECONDS = 24 * 60 * 60
_MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024
_MAX_ARCHIVE_FILES = 1024
_NETWORK_TIMEOUT_SECONDS = 5
_SHUTDOWN_JOIN_SECONDS = 6
_TRUE_VALUES = {"1", "true", "yes", "on"}
_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_busy_lock = threading.Lock()
_busy = False
_generation = 0
_workers_lock = threading.Lock()
_workers: set[threading.Thread] = set()


class UpdateError(RuntimeError):
    """A release could not be trusted, selected, downloaded, or installed."""


def _version_key(value: str) -> tuple[int, int, int]:
    if not _VERSION_PATTERN.fullmatch(value):
        raise UpdateError(f"Unsupported release version: {value!r}")
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _metadata_digest(asset: dict[str, Any]) -> str | None:
    digest = asset.get("digest")
    if digest is None:
        return None
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise UpdateError(f"GitHub returned an invalid digest for {asset.get('name', 'asset')}")
    return digest.split(":", 1)[1].lower()


def _download(url: str, maximum: int, expected_size: int | None = None) -> bytes:
    if not url.startswith("https://"):
        raise UpdateError("Updates must be downloaded over HTTPS")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": _USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_NETWORK_TIMEOUT_SECONDS) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > maximum:
                raise UpdateError("Update download exceeds the size limit")
            payload = response.read(maximum + 1)
    except UpdateError:
        raise
    except Exception as error:
        raise UpdateError(f"Could not download update metadata: {error}") from error
    if len(payload) > maximum:
        raise UpdateError("Update download exceeds the size limit")
    if expected_size is not None and len(payload) != expected_size:
        raise UpdateError(
            f"Update size mismatch: expected {expected_size}, received {len(payload)}"
        )
    return payload


def _json_payload(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UpdateError(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise UpdateError(f"{label} must be a JSON object")
    return value


def _release_assets(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("GitHub release has no asset list")
    result: dict[str, dict[str, Any]] = {}
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
            raise UpdateError("GitHub release contains malformed asset metadata")
        result[asset["name"]] = asset
    return result


def _validate_update_identity(update: dict[str, Any]) -> None:
    version = update.get("version")
    target = update.get("maya_target")
    major = update.get("maya_major_version")
    api_version = update.get("maya_api_version")
    supported = {
        "2026.3": ("2026", 20260300),
        "2027": ("2027", 20270100),
    }
    if not isinstance(version, str):
        raise UpdateError("Update version is invalid")
    _version_key(version)
    if target not in supported or supported[target] != (major, api_version):
        raise UpdateError("Update Maya target identity is invalid")
    expected_name = f"maya-mcp-v{version}-maya{target}-windows-x64.zip"
    if update.get("name") != expected_name:
        raise UpdateError("Update package name does not match its target")
    digest = update.get("sha256")
    size = update.get("size")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise UpdateError("Update package SHA-256 is invalid")
    if not isinstance(size, int) or size <= 0 or size > _MAX_DOWNLOAD_BYTES:
        raise UpdateError("Update package size is invalid")


def select_update(
    release: dict[str, Any],
    manifest: dict[str, Any],
    current_version: str,
    maya_api_version: int,
) -> dict[str, Any] | None:
    """Select one exact-API Windows package from validated release metadata."""
    if manifest.get("schema_version") != 1 or manifest.get("name") != "maya-mcp":
        raise UpdateError("Release manifest schema is not supported")
    version = manifest.get("version")
    if not isinstance(version, str):
        raise UpdateError("Release manifest has no version")
    if release.get("tag_name") != f"v{version}":
        raise UpdateError("GitHub tag and release manifest version do not match")
    if release.get("draft") or release.get("prerelease"):
        return None
    if _version_key(version) <= _version_key(current_version):
        return None

    entries = manifest.get("assets")
    if not isinstance(entries, list):
        raise UpdateError("Release manifest has no package list")
    matches = [
        item
        for item in entries
        if isinstance(item, dict)
        and item.get("platform") == "windows-x64"
        and item.get("maya_api_version") == maya_api_version
    ]
    if len(matches) != 1:
        if not matches:
            return None
        raise UpdateError("Release manifest contains duplicate Maya API packages")
    entry = matches[0]
    name = entry.get("name")
    digest = entry.get("sha256")
    if not isinstance(name, str) or not re.fullmatch(r"maya-mcp-v[0-9.]+-maya[0-9.]+-windows-x64\.zip", name):
        raise UpdateError("Release package name is invalid")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise UpdateError("Release package SHA-256 is invalid")
    size = entry.get("size")
    if not isinstance(size, int) or size <= 0 or size > _MAX_DOWNLOAD_BYTES:
        raise UpdateError("Release package size is invalid")

    github_asset = _release_assets(release).get(name)
    if github_asset is None or github_asset.get("state") != "uploaded":
        raise UpdateError(f"GitHub release asset is missing: {name}")
    url = github_asset.get("browser_download_url")
    if not isinstance(url, str):
        raise UpdateError("GitHub release asset has no download URL")
    github_size = github_asset.get("size")
    if github_size != size:
        raise UpdateError("GitHub and release-manifest package sizes do not match")
    github_digest = _metadata_digest(github_asset)
    if github_digest is not None and github_digest != digest.lower():
        raise UpdateError("GitHub and release-manifest package digests do not match")

    target = entry.get("maya_target")
    major = entry.get("maya_major_version")
    if not isinstance(target, str) or major not in {"2026", "2027"}:
        raise UpdateError("Release package Maya target is invalid")
    selected = {
        "version": version,
        "maya_target": target,
        "maya_major_version": major,
        "maya_api_version": maya_api_version,
        "name": name,
        "size": size,
        "sha256": digest.lower(),
        "url": url,
        "release_url": release.get("html_url", ""),
    }
    _validate_update_identity(selected)
    return selected


def query_update(current_version: str, maya_api_version: int) -> dict[str, Any] | None:
    release = _json_payload(
        _download(_LATEST_RELEASE_API, 4 * 1024 * 1024), "GitHub release metadata"
    )
    manifest_asset = _release_assets(release).get(_MANIFEST_NAME)
    if manifest_asset is None or manifest_asset.get("state") != "uploaded":
        raise UpdateError("GitHub release is missing release-manifest.json")
    manifest_url = manifest_asset.get("browser_download_url")
    manifest_size = manifest_asset.get("size")
    if not isinstance(manifest_url, str) or not isinstance(manifest_size, int):
        raise UpdateError("GitHub release manifest metadata is invalid")
    manifest_payload = _download(manifest_url, 1024 * 1024, manifest_size)
    manifest_digest = _metadata_digest(manifest_asset)
    if manifest_digest is not None and _sha256(manifest_payload) != manifest_digest:
        raise UpdateError("GitHub release manifest digest verification failed")
    manifest = _json_payload(manifest_payload, "Release manifest")
    return select_update(release, manifest, current_version, maya_api_version)


def _validated_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if not members or len(members) > _MAX_ARCHIVE_FILES:
        raise UpdateError("Update archive has an invalid file count")
    total_size = 0
    for member in members:
        path = PurePosixPath(member.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise UpdateError(f"Unsafe update archive path: {member.filename}")
        if member.flag_bits & 0x1:
            raise UpdateError("Encrypted update archives are not supported")
        unix_mode = member.external_attr >> 16
        if (unix_mode & 0o170000) == 0o120000:
            raise UpdateError("Update archive may not contain symbolic links")
        total_size += member.file_size
        if total_size > _MAX_DOWNLOAD_BYTES:
            raise UpdateError("Expanded update archive exceeds the size limit")
    return members


def _extract_archive(payload: bytes, destination: Path) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for member in _validated_members(archive):
                relative = PurePosixPath(member.filename.replace("\\", "/"))
                output = destination.joinpath(*relative.parts)
                if member.is_dir():
                    output.mkdir(parents=True, exist_ok=True)
                    continue
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, output.open("wb") as target:
                    shutil.copyfileobj(source, target)
    except UpdateError:
        raise
    except (OSError, zipfile.BadZipFile) as error:
        raise UpdateError(f"Could not unpack update archive: {error}") from error


def _package_metadata(staging: Path, update: dict[str, Any]) -> None:
    folder_name = f"maya-mcp-{update['version']}-maya{update['maya_target']}"
    module_name = f"maya-mcp-{update['maya_major_version']}.mod"
    manifest_path = staging / "package-manifest.json"
    plugin_path = staging / folder_name / "plug-ins" / "maya_mcp.mll"
    runtime_path = staging / folder_name / "scripts" / "maya_mcp_runtime" / "__init__.py"
    bridge_path = staging / folder_name / "bin" / "maya-mcp-bridge.exe"
    launcher_path = staging / folder_name / "client" / "Start-MayaMcpBridge.ps1"
    configurator_path = staging / folder_name / "client" / "Configure-MayaMcpClients.ps1"
    module_path = staging / module_name
    for required in (
        manifest_path,
        plugin_path,
        runtime_path,
        bridge_path,
        launcher_path,
        configurator_path,
        module_path,
    ):
        if not required.is_file():
            raise UpdateError(f"Update archive is missing {required.relative_to(staging)}")
    manifest = _json_payload(manifest_path.read_bytes(), "Package manifest")
    expected = {
        "schema_version": 1,
        "name": "maya-mcp",
        "version": update["version"],
        "maya_target": update["maya_target"],
        "maya_major_version": update["maya_major_version"],
        "maya_api_version": update["maya_api_version"],
        "platform": "windows-x64",
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise UpdateError("Package metadata does not match the selected Maya build")
    module_text = module_path.read_text(encoding="utf-8", errors="strict")
    if (
        f"MAYAVERSION:{update['maya_major_version']}" not in module_text
        or f"./{folder_name}" not in module_text
    ):
        raise UpdateError("Package module descriptor does not match the selected Maya build")


def _atomic_text(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    temporary.write_text(value, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def register_client_bridge(installed: Path, version: str) -> Path:
    """Register this package's versioned bridge behind the stable user launcher."""
    base = os.getenv("LOCALAPPDATA") or tempfile.gettempdir()
    client_root = Path(base) / "MayaMCP" / "client"
    version_root = client_root / "versions" / version
    version_root.mkdir(parents=True, exist_ok=True)

    source_bridge = installed / "bin" / "maya-mcp-bridge.exe"
    source_launcher = installed / "client" / "Start-MayaMcpBridge.ps1"
    source_configurator = installed / "client" / "Configure-MayaMcpClients.ps1"
    bridge_digest = _sha256(source_bridge.read_bytes())
    registered_bridge = version_root / f"maya-mcp-bridge-{bridge_digest[:16]}.exe"
    if not registered_bridge.is_file():
        shutil.copy2(source_bridge, registered_bridge)
    shutil.copy2(source_launcher, client_root / "Start-MayaMcpBridge.ps1")
    shutil.copy2(source_configurator, client_root / "Configure-MayaMcpClients.ps1")

    registry_path = client_root / "bridge-installations.json"
    installations: list[dict[str, Any]] = []
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        if registry.get("schema_version") == 1 and isinstance(
            registry.get("installations"), list
        ):
            installations = [
                item
                for item in registry["installations"]
                if isinstance(item, dict) and item.get("version") != version
            ]
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        pass
    installations.append(
        {
            "version": version,
            "path": str(registered_bridge),
            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    _atomic_text(
        registry_path,
        json.dumps(
            {"schema_version": 1, "installations": installations},
            separators=(",", ":"),
        )
        + "\n",
    )
    return registered_bridge


def install_archive_bytes(
    update: dict[str, Any], payload: bytes, modules_directory: str | os.PathLike[str]
) -> dict[str, str]:
    """Verify and stage an already-downloaded update for the next Maya start."""
    _validate_update_identity(update)
    if len(payload) != update["size"] or _sha256(payload) != update["sha256"]:
        raise UpdateError("Update package SHA-256 verification failed")
    modules = Path(modules_directory).resolve()
    modules.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".maya-mcp-update-", dir=modules))
    try:
        _extract_archive(payload, staging)
        _package_metadata(staging, update)
        folder_name = f"maya-mcp-{update['version']}-maya{update['maya_target']}"
        installed = modules / folder_name
        if installed.exists():
            if installed.is_symlink():
                raise UpdateError("Refusing to replace a symbolic-link install path")
            shutil.rmtree(installed)
        os.replace(staging / folder_name, installed)

        registered_bridge = register_client_bridge(installed, update["version"])

        major = update["maya_major_version"]
        descriptor = modules / f"maya-mcp-{major}.mod"
        module_text = (
            f"+ MAYAVERSION:{major} PLATFORM:win64 maya-mcp {update['version']} ./{folder_name}\n"
            f"MAYA_MCP_VERSION={update['version']}\n"
            f"MAYA_MCP_TARGET={update['maya_target']}\n"
        )
        _atomic_text(descriptor, module_text)

        legacy = modules / "maya-mcp.mod"
        if legacy.is_file():
            legacy_text = legacy.read_text(encoding="utf-8", errors="replace")
            if re.search(r"(?m)^\+\s+maya-mcp\s+", legacy_text):
                legacy.unlink()
        return {
            "installed": str(installed),
            "module": str(descriptor),
            "bridge": str(registered_bridge),
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def install_update(
    update: dict[str, Any], modules_directory: str | os.PathLike[str]
) -> dict[str, str]:
    payload = _download(update["url"], _MAX_DOWNLOAD_BYTES, update["size"])
    return install_archive_bytes(update, payload, modules_directory)


def _state_path() -> Path:
    base = os.getenv("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "MayaMCP" / "update-state.json"


def _auto_check_due() -> bool:
    if os.getenv("MAYA_MCP_DISABLE_UPDATE_CHECK", "").lower() in _TRUE_VALUES:
        return False
    try:
        state = json.loads(_state_path().read_text(encoding="utf-8"))
        return time.time() - float(state.get("last_check", 0)) >= _CHECK_INTERVAL_SECONDS
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return True


def _record_check() -> None:
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_text(path, json.dumps({"last_check": time.time()}, separators=(",", ":")))
    except OSError:
        pass


def _set_busy(value: bool) -> bool:
    global _busy
    with _busy_lock:
        if value and _busy:
            return False
        _busy = value
        return True


def _defer(function: Any) -> None:
    import maya.utils

    maya.utils.executeDeferred(function)


def _start_worker(function: Any, name: str) -> None:
    def run() -> None:
        try:
            function()
        finally:
            with _workers_lock:
                _workers.discard(threading.current_thread())

    worker = threading.Thread(target=run, name=name, daemon=True)
    with _workers_lock:
        _workers.add(worker)
    worker.start()


def _show_error(message: str) -> None:
    import maya.cmds as cmds

    cmds.confirmDialog(title="Maya MCP Update", message=message, button=["OK"])


def _deliver_check(
    generation: int,
    manual: bool,
    update: dict[str, Any] | None,
    error: str | None,
    modules_directory: str,
    maya_api_version: int,
) -> None:
    global _generation
    _set_busy(False)
    if generation != _generation:
        return
    import maya.cmds as cmds

    if error:
        if manual:
            _show_error(f"Could not check for updates.\n\n{error}")
        return
    if update is None:
        if manual:
            _show_error(
                f"No newer compatible release is available for Maya API "
                f"{maya_api_version}.\n\nInstalled version: {__version__}"
            )
        return
    choice = cmds.confirmDialog(
        title="Maya MCP Update Available",
        message=(
            f"Maya MCP {update['version']} is available for Maya {update['maya_target']}.\n\n"
            "Install it side-by-side now? It becomes active after Maya restarts."
        ),
        button=["Install Update", "Later", "View Release"],
        defaultButton="Install Update",
        cancelButton="Later",
        dismissString="Later",
    )
    if choice == "View Release" and update.get("release_url"):
        webbrowser.open(update["release_url"])
    elif choice == "Install Update":
        _begin_install(generation, update, modules_directory)


def _deliver_install(generation: int, result: dict[str, str] | None, error: str | None) -> None:
    _set_busy(False)
    if generation != _generation:
        return
    if error:
        _show_error(f"Could not install the update.\n\n{error}")
        return
    _show_error(
        "The update is installed and verified.\n\n"
        "Close and reopen Maya to load the new plug-in. Your current scene was not changed."
    )


def _begin_install(generation: int, update: dict[str, Any], modules_directory: str) -> None:
    if not _set_busy(True):
        return

    def worker() -> None:
        result: dict[str, str] | None = None
        error: str | None = None
        try:
            result = install_update(update, modules_directory)
        except Exception as exception:
            error = str(exception)
        if generation == _generation:
            _defer(lambda: _deliver_install(generation, result, error))
        else:
            _set_busy(False)

    _start_worker(worker, "MayaMcpUpdateInstall")


def check_for_updates(manual: bool = False) -> None:
    """Check asynchronously and prompt in Maya when an exact build is newer."""
    global _generation
    import maya.cmds as cmds

    if cmds.about(batch=True) or (not manual and not _auto_check_due()):
        return
    if not _set_busy(True):
        if manual:
            cmds.inViewMessage(
                assistMessage="Maya MCP update check is already running",
                position="topCenter",
                fade=True,
            )
        return
    _generation += 1
    generation = _generation
    maya_api_version = int(cmds.about(apiVersion=True))
    user_app = Path(cmds.internalVar(userAppDir=True)).resolve()
    modules_directory = str(user_app.parent / "modules")
    if manual:
        cmds.inViewMessage(
            assistMessage="Checking Maya MCP updates...",
            position="topCenter",
            fade=True,
        )

    def worker() -> None:
        update: dict[str, Any] | None = None
        error: str | None = None
        try:
            _record_check()
            update = query_update(__version__, maya_api_version)
        except Exception as exception:
            error = str(exception)
        if generation == _generation:
            _defer(
                lambda: _deliver_check(
                    generation,
                    manual,
                    update,
                    error,
                    modules_directory,
                    maya_api_version,
                )
            )
        else:
            _set_busy(False)

    _start_worker(worker, "MayaMcpUpdateCheck")


def start_auto_check() -> None:
    check_for_updates(manual=False)


def shutdown() -> None:
    global _generation
    _generation += 1
    _set_busy(False)
    deadline = time.monotonic() + _SHUTDOWN_JOIN_SECONDS
    with _workers_lock:
        workers = list(_workers)
    current = threading.current_thread()
    for worker in workers:
        if worker is current:
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        worker.join(remaining)
