# Updates and releases

Maya MCP publishes one Windows ZIP per supported Maya API. The source revision is
the same; only the Autodesk headers and import libraries differ.

## Compatibility matrix

| Release asset label | Autodesk devkit | `MAYA_API_VERSION` | Maya module condition |
|---|---|---:|---|
| `maya2026.3` | Maya 2026.3 Update | `20260300` | `MAYAVERSION:2026` |
| `maya2027` | Maya 2027.1 Update | `20270100` | `MAYAVERSION:2027` |

The updater requires an exact API match. It does not guess that a binary built
for one Maya update is compatible with another.

## Prepare the SDKs

Place the Autodesk devkit ZIPs in `Downloads`, then run:

~~~powershell
.\scripts\setup-devkits.ps1
~~~

The script validates the API macros and extracts only `include\maya` and `lib` to
`%LOCALAPPDATA%\MayaMCP\devkits`. Rerunning it is safe and does no work when both
SDKs are complete.

## Build and validate

~~~powershell
.\scripts\build.ps1 -MayaVersion All -Configuration Release
.\scripts\test-plugin.ps1 -MayaVersion 2027
.\scripts\test-viewport-interactive.ps1 -MayaVersion 2027
.\scripts\package-release.ps1
~~~

The 2026.3 binary can compile and link using only its devkit. Run the two Maya
runtime gates on a machine with Maya 2026.3 before expanding the formal runtime
support claim beyond build compatibility.

`package-release.ps1` writes install-ready artifacts under `dist\vVERSION`:

- one ZIP for Maya 2026.3;
- one ZIP for Maya 2027;
- `release-manifest.json`, containing target, API version, byte size, and SHA-256.

Each ZIP includes a versioned module folder, a Maya-qualified `.mod` file, package
metadata, a double-click `Install-MayaMcp.cmd`, and its PowerShell implementation.
The installer uses Autodesk's per-user module layout, needs no administrator
access, and configures autoload when the matching Maya installation is present.
Extracting both ZIPs into the same Maya module directory is safe.

After packaging, exercise the public ZIP contents and both installer launchers:

~~~powershell
.\scripts\test-release-installer.ps1
~~~

## Publish

Commit and push the release source first. The publishing script refuses a dirty
tree or an unpushed commit.

~~~powershell
# First public release only
.\scripts\publish-release.ps1 -MakePublic

# Later releases
.\scripts\publish-release.ps1
~~~

The script rebuilds both targets and creates `vVERSION` with both ZIPs and the
release manifest through GitHub CLI.

## Updater trust and lifecycle

The Maya-side updater:

1. reads the latest non-draft, non-prerelease GitHub release;
2. verifies the release tag matches the manifest version;
3. selects exactly one `windows-x64` asset matching `cmds.about(apiVersion=True)`;
4. compares the manifest hash with GitHub's asset digest when present;
5. downloads with bounded size and timeouts;
6. rejects encrypted files, symbolic links, path traversal, and oversized archives;
7. verifies the ZIP SHA-256 and its internal package metadata;
8. installs to a new versioned folder and atomically replaces only that Maya
   version's module descriptor.

The updater never overwrites the DLL loaded by Maya. A restart activates the new
descriptor. Older version folders remain available for rollback.

Set `MAYA_MCP_DISABLE_UPDATE_CHECK=1` before launching Maya to disable the daily
check. The manual **Check for Updates...** menu action remains available.

## Roll back

Close Maya and edit the matching descriptor in `Documents\maya\modules`:

- `maya-mcp-2026.mod` for Maya 2026;
- `maya-mcp-2027.mod` for Maya 2027.

Point its final path field at an older installed folder, then reopen Maya. No
scene files or Maya preferences need to be changed.
