"""Exercise published 0.6.1 updater code against actual release ZIPs, offline.

Run with a distribution directory after packaging. All client homes, module
paths, and subprocess setup are isolated in a temporary directory.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
import maya_mcp_runtime


def main(distribution):
    manifest = json.loads((distribution / "release-manifest.json").read_text(encoding="utf-8"))
    old_source = subprocess.check_output(["git", "show", "v0.6.1:python/maya_mcp_runtime/updater.py"], cwd=ROOT).decode("utf-8")
    old_skill = subprocess.check_output(["git", "show", "v0.6.1:skills/maya-mcp/SKILL.md"], cwd=ROOT)
    legacy = ModuleType("maya_mcp_runtime._legacy_updater")
    legacy.__package__ = "maya_mcp_runtime"
    with patch.object(maya_mcp_runtime, "__version__", "0.6.1"):
        exec(compile(old_source, "v0.6.1/updater.py", "exec"), legacy.__dict__)
    real_run = subprocess.run
    cases = []
    for asset in manifest["assets"]:
        if not asset["name"].endswith(".zip"):
            continue
        payload = (distribution / asset["name"]).read_bytes()
        metadata = {**asset, "version": manifest["version"],
                    "url": f"https://github.com/parodyband/maya-mcp/releases/download/v{manifest['version']}/{asset['name']}"}
        for cohort in ("managed", "registered_without_skills", "customized"):
            with tempfile.TemporaryDirectory(prefix="maya-skill-upgrade-") as temporary:
                base = Path(temporary)
                home = base / "client-home"
                home.mkdir()
                skill_roots = [home / ".agents" / "skills", home / ".claude" / "skills"]
                if cohort == "registered_without_skills":
                    (home / ".codex").mkdir()
                    (home / ".codex" / "config.toml").write_text('[mcp_servers.maya-mcp]\ncommand="powershell.exe"\n', encoding="utf-8")
                    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"maya-mcp": {"command": "powershell.exe"}}}), encoding="utf-8")
                else:
                    for root in skill_roots:
                        folder = root / "maya-mcp"
                        folder.mkdir(parents=True)
                        (folder / "SKILL.md").write_bytes(old_skill + (b"\nPersonal customization\n" if cohort == "customized" else b""))
                        (folder / ".maya-mcp-install.json").write_text(json.dumps({
                            "owner": "maya-mcp", "schema_version": 1, "sha256": hashlib.sha256(old_skill).hexdigest()}), encoding="utf-8")
                invocations = []
                def run_isolated(command, **kwargs):
                    assert command[0] == "powershell.exe" and command[-2:] == ["-SkillsOnly", "-ExistingSkillsOnly"], command
                    completed = real_run([*command, "-AgentHome", str(home)], **kwargs)
                    invocations.append((completed.returncode, completed.stdout[-2000:], completed.stderr[-500:]))
                    return completed
                with patch.dict(os.environ, {"LOCALAPPDATA": str(base / "local"), "CLAUDE_CONFIG_DIR": str(home / ".claude"), "MAYA_MCP_DISABLE_SKILL_SYNC": "0"}), \
                        patch("subprocess.run", side_effect=run_isolated):
                    result = legacy.install_archive_bytes(metadata, payload, base / "modules")
                assert manifest["version"] in Path(result["module"]).read_text(encoding="utf-8")
                with zipfile.ZipFile(distribution / asset["name"]) as archive:
                    prefix = f"maya-mcp-{manifest['version']}-maya{asset['maya_target']}/client/skills/"
                    for name in archive.namelist():
                        if not name.startswith(prefix) or name.endswith("/"):
                            continue
                        relative = name[len(prefix):]
                        for root in skill_roots:
                            target = root / relative
                            if cohort == "customized" and relative.startswith("maya-mcp/"):
                                continue
                            assert target.is_file() and target.read_bytes() == archive.read(name), (cohort, relative, root, invocations)
                if cohort == "customized":
                    for root in skill_roots:
                        assert b"Personal customization" in (root / "maya-mcp" / "SKILL.md").read_bytes()
                cases.append({"maya_target": asset["maya_target"], "customer": cohort, "passed": True})
    assert len(cases) == 6, cases
    print(json.dumps({"legacy_updater": "0.6.1", "release": manifest["version"], "cases": cases}, indent=2))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
