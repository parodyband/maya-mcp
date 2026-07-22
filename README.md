# Maya MCP

A native Maya 2026.3 and Maya 2027 plug-in that gives MCP clients a typed,
authenticated, and vision-aware interface to Maya.

Version 0.5 is a working development preview. It includes the native transport,
Maya main-thread dispatch, scene and rigging tools, native VP2 depth readback,
vision grounding, resources, prompts, security controls, and isolated batch and
interactive test harnesses. GitHub-hosted release metadata now drives exact-API,
SHA-256-verified updates.

## What works

- MCP 2025-11-25 over Streamable HTTP at http://127.0.0.1:7001/mcp
- A native stdio bridge that discovers the active Maya process on every launch
- Automatic user-scope setup for Codex and Claude Code
- A one-click Claude Desktop MCP Bundle (`.mcpb`)
- Cryptographic bearer tokens, strict loopback binding, and Origin validation
- Session initialization, ping, tools, resources, prompts, and session deletion
- A bounded C++ worker pool with removable Maya-timer and Qt-playback dispatch
- Strict JSON Schema inputs and structured MCP outputs
- Canonical node references combining scene epoch, UUID, reference context,
  and DAG paths
- Transactional node edits with step aliases, revision guards, dry-run
  validation, and Maya undo chunks
- Typed custom controls, IK handles, pole vectors, production constraints,
  rig attributes, utility connections, and driven keys
- Viewport capture as real MCP ImageContent
- Optional bounded native Viewport 2.0 depth readback with exact format metadata
- Conservative screen-space scene maps with canonical node identity
- Viewport world-to-screen projection and pixel picking
- Geometry, materials, animation, joint chains, controls, and skin binding
- Non-serializing rig previews with bounded lifetime, strict ownership, and
  preflighted one-chunk acceptance when Maya undo is enabled
- A Maya MCP menu for server status and one-click, per-session Python/MEL approval
- Daily update checks plus one-click, side-by-side installation for the exact
  Maya API version currently running

The connected MCP host supplies the vision model. `maya.viewport.capture`
returns the color image, camera matrices, projected joints, and optional native
experimental renderer-native depth. `maya.viewport.scene_map` and
`maya.viewport.pick` ground pixels back to
canonical Maya nodes. A stable object-ID render pass remains future work.

## Install a release

You do not need Visual Studio, CMake, or an Autodesk devkit to install a release.

1. Close Maya.
2. Download the matching ZIP from [GitHub Releases](https://github.com/parodyband/maya-mcp/releases/latest):
   `maya2026.3` for Maya 2026.3 or `maya2027` for Maya 2027.1.
3. Open the ZIP and double-click **Install-MayaMcp.cmd**.
4. Wait for the green installed message, press any key, and open Maya.
5. Restart Codex or Claude Code if it was already open.

That is the entire install. It does not require administrator access and does not
change the machine-wide PowerShell policy. The installer copies only the matching
Maya package into your user modules folder, enables autoload, and configures any
detected Codex or Claude Code installation. If Windows opens only the launcher
from inside the ZIP, the launcher downloads and verifies the exact complete
package automatically. Choosing **Extract All** first is optional and avoids that
second download.

For Claude Desktop, double-click **Install-MayaMcp-Claude-Desktop.mcpb** in the
same package and approve the extension. [Anthropic's current extension flow](https://support.anthropic.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop)
keeps Claude Desktop setup separate from Claude Code setup.

For terminal-based or managed installs, the equivalent command is:

~~~powershell
powershell -ExecutionPolicy Bypass -File .\Install-MayaMcp.ps1
~~~

Older versions stay in place as a rollback. Maya 2026 and 2027 can coexist
because each uses a version-qualified module descriptor.

## Automatic updates

Maya MCP checks GitHub Releases at most once every 24 hours. It selects a package
only when its `MAYA_API_VERSION` exactly matches the running Maya process. Choose
**Maya MCP > Check for Updates...** to check immediately.

An accepted update is downloaded over HTTPS, verified against both the release
manifest and GitHub's SHA-256 asset digest, and installed side-by-side. The loaded
DLL is never overwritten. Close and reopen Maya to activate the staged version.
The update also registers the matching stdio bridge. Codex and Claude Code keep
using the same stable launcher, so their MCP configuration does not change.
Packages installed by an older updater self-register this bridge the first time
the new plug-in starts.

Disable automatic checks while keeping the manual menu command available:

~~~powershell
$env:MAYA_MCP_DISABLE_UPDATE_CHECK = '1'
& 'C:\Program Files\Autodesk\Maya2027\bin\maya.exe'
~~~

## Developer prerequisites

- Autodesk Maya 2027 for the full local runtime and viewport test gates
- Maya 2026.3 Update and Maya 2027.1 Update C++ devkit ZIPs in `Downloads`
- Visual Studio 2022 17.8.3 or newer with **Desktop development with C++**
- CMake 3.25 or newer
- Git, used by CMake to fetch two pinned header-only dependencies

Install a lean copy of both SDKs. The script extracts only the Maya headers and
import libraries; Autodesk's large examples and bundled dependency SDKs are not
needed for this plug-in.

~~~powershell
.\scripts\setup-devkits.ps1
~~~

The ignored SDK store is `%LOCALAPPDATA%\MayaMCP\devkits`. Autodesk devkit files
are never committed or included in a GitHub release.

## Quick start

One command builds, installs, and configures the Maya 2027 plug-in to autoload:

~~~powershell
.\scripts\setup.ps1
~~~

For Maya 2026.3 on a machine where it is installed:

~~~powershell
.\scripts\setup.ps1 -MayaVersion 2026.3
~~~

Open Maya. The server starts automatically and the **Maya MCP** menu shows its
status. Normal rigging does not require Python/MEL approval.

## Developer build and tests

Build both Release packages:

~~~powershell
.\scripts\build.ps1
~~~

Build only one target:

~~~powershell
.\scripts\build.ps1 -MayaVersion 2026.3
.\scripts\build.ps1 -MayaVersion 2027
~~~

Run the full standalone integration test:

~~~powershell
.\scripts\test-plugin.ps1
~~~

Expected result:

~~~text
MAYA_MCP_TEST_RESULT={"protocol":"2025-11-25","resources":4,"rigging_pipeline":"passed","security_checks":"passed","tools":18,"typed_mutation":"passed","version":"0.5.3"}
~~~

Validate the real GPU viewport in a separate, isolated Maya process:

~~~powershell
.\scripts\test-viewport-interactive.ps1
~~~

Evidence is written below `build\viewport-validation\`. The launcher never
attaches to or closes a Maya process that it did not start.

Create the two public release ZIPs, the Claude Desktop MCP Bundle, and
`release-manifest.json`:

~~~powershell
.\scripts\package-release.ps1
.\scripts\test-release-installer.ps1
~~~

Manual developer install for one Maya version:

~~~powershell
.\scripts\install-module.ps1 -MayaVersion 2027
~~~

Open **Windows > Settings/Preferences > Plug-in Manager**, then load
maya_mcp.mll. `setup.ps1` performs this installation and configures autoload.

Verify it from Maya's Script Editor:

~~~python
import json
import maya.cmds as cmds

print(json.dumps(json.loads(cmds.mayaMcpStatus()), indent=2))
~~~

The configured build packages are outside the repository working tree:

~~~text
%LOCALAPPDATA%\MayaMCP\build\maya2026.3-mcp-vs2022\package
%LOCALAPPDATA%\MayaMCP\build\maya2027-mcp-vs2022\package
~~~

## Connect an MCP client

The release installer configures detected Codex and Claude Code clients for your
Windows user. It creates one permanent `maya-mcp` entry that launches a local
stdio bridge. The bridge reads the active Maya process, port, and token at
connection time, so Maya restarts and plug-in updates do not require editing the
client configuration.

To add a client installed after Maya MCP, or repair its configuration, choose:

**Maya MCP > Configure AI Clients...**

Restart the AI client after configuration. Open Maya before starting a session
that needs Maya tools.

Claude Desktop uses the separate **Install-MayaMcp-Claude-Desktop.mcpb** file.
Install it from Claude Desktop's extension settings if double-click is not
registered on your machine.

### Advanced direct HTTP connection

The plug-in writes per-process discovery data to:

~~~text
%LOCALAPPDATA%\MayaMCP\server-PID.json
~~~

It also updates %LOCALAPPDATA%\MayaMCP\current.json for the most recently
started Maya instance.

Inspect the connection without printing the full token:

~~~powershell
.\scripts\show-connection.ps1
~~~

Reveal the token only when configuring a trusted client that cannot launch the
included stdio bridge:

~~~powershell
.\scripts\show-connection.ps1 -RevealToken -AsJson
~~~

Configure a Streamable HTTP client with:

- URL: the discovery file's url
- Header: Authorization: Bearer TOKEN
- Protocol version: 2025-11-25

The default port is 7001. If it is occupied, the plug-in chooses an available
loopback port and records it in discovery.

You can make the port or token predictable before launching Maya:

~~~powershell
$env:MAYA_MCP_PORT = '7001'
$env:MAYA_MCP_TOKEN = 'replace-with-a-long-random-secret'
& 'C:\Program Files\Autodesk\Maya2027\bin\maya.exe'
~~~

Do not commit tokens or place them in shared project files.

## Tool surface

### Core

- maya.context.get — scene, units, time, selection, viewport, renderer, undo
- maya.scene.query — bounded DAG/DG queries with canonical references
- maya.node.apply — transactional graph edits plus controls, IK, pole vectors,
  constraints, rig attributes, utility connections, and driven keys
- maya.selection.set — replace, add, remove, toggle, or clear selection
- maya.history.apply — apply Maya undo or redo steps

### Content and animation

- maya.geometry.apply — polygon primitives and NURBS curves
- maya.material.apply — create, inspect, and assign common PBR materials
- maya.animation.apply — inspect, set, or delete animation keys
- maya.file.apply — query, save, open, import, reference, or export

### Rigging

- maya.rig.skeleton — create and orient chains or inspect joint hierarchy
- maya.rig.controls — curve controls, offsets, colors, and constraints
- maya.rig.preview — revise ghost joints and controls, then preflight and accept
  them in one undo chunk
- maya.rig.skin — bind, unbind, or inspect skinClusters

### Viewport and vision

- maya.viewport.capture — color image, semantic metadata, and optional VP2 depth
- maya.viewport.scene_map — projected boxes and pivots with canonical node refs
- maya.viewport.project — world-to-screen points and screen-to-world rays
- maya.viewport.pick — pixel-to-node/component picking with selection restore

### Escape hatch

- maya.script.execute — Python or MEL with captured output and code hash

All normal edit tools use named Maya undo chunks. Python and MEL cannot be
honestly sandboxed or force-cancelled inside Maya.

## Python and MEL fallback

Script execution remains disabled by default. When a typed operation genuinely
cannot express the task, click:

**Maya MCP > Allow Python/MEL Automation This Session**

The approval applies immediately and expires when Maya closes. It does not
require a restart. Headless automation can still opt in before launch:

~~~powershell
$env:MAYA_MCP_ALLOW_UNSAFE_CODE = '1'
& 'C:\Program Files\Autodesk\Maya2027\bin\maya.exe'
~~~

The MCP server remains bound to 127.0.0.1 and requires its bearer token.
Approved Python or MEL still has your full user privileges, including file,
process, and network access.

## Maya commands

~~~python
cmds.mayaMcpStatus()  # JSON status
cmds.mayaMcpStop()    # stop accepting requests
cmds.mayaMcpStart()   # restart transport and write new discovery
cmds.mayaMcpPump()    # manually drain queued work; mainly for batch tests

# Expert/native diagnostic command. MCP clients normally use viewport.capture.
json.loads(cmds.mayaMcpVp2Capture(request='{"depth":true}'))
~~~

## Resources and prompts

Read-only resources:

- maya://context
- maya://scene/summary
- maya://selection
- maya://timeline

Workflow prompts:

- maya.viewport.inspect
- maya.rig.from_landmarks
- maya.scene.audit

## Design and safety

- [Architecture](docs/ARCHITECTURE.md)
- [Security model](docs/SECURITY.md)
- [Protocol and tool API](docs/API.md)
- [Vision-guided rigging workflow](docs/VISION_RIGGING.md)
- [Roadmap and known limits](docs/ROADMAP.md)
- [Updates and releases](docs/UPDATES_AND_RELEASES.md)

The devkit and generated builds are excluded from Git. CMake fetches
cpp-httplib v0.50.1 and nlohmann/json v3.12.0 into the ignored build tree.
