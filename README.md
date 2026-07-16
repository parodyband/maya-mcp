# Maya MCP

A native Maya 2027 plug-in that gives MCP clients a typed, authenticated, and
vision-aware interface to Maya.

Version 0.2 is a working development preview. It includes the native transport,
Maya main-thread dispatch, scene and rigging tools, viewport image output,
resources, prompts, security controls, and an end-to-end standalone test.

## What works

- MCP 2025-11-25 over Streamable HTTP at http://127.0.0.1:7001/mcp
- Cryptographic bearer tokens, strict loopback binding, and Origin validation
- Session initialization, ping, tools, resources, prompts, and session deletion
- A bounded C++ worker pool and removable Maya main-thread timer dispatcher
- Strict JSON Schema inputs and structured MCP outputs
- Canonical node references combining scene epoch, UUID, reference context,
  and DAG paths
- Transactional node edits with step aliases, revision guards, dry-run
  validation, and Maya undo chunks
- Viewport capture as real MCP ImageContent
- Viewport world-to-screen projection and pixel picking
- Geometry, materials, animation, joint chains, controls, and skin binding
- Guarded Python and MEL execution when a typed tool cannot express an operation

The connected MCP host supplies the vision model. maya.viewport.capture returns
a viewport image plus camera matrices, joint projections, selection, time,
units, and scene revisions.

## Prerequisites

- Autodesk Maya 2027 at C:\Program Files\Autodesk\Maya2027
- Maya 2027.1 Update devkit under vendor\devkitBase
- Visual Studio 2022 17.8.3 or newer with **Desktop development with C++**
- CMake 3.25 or newer
- Git, used by CMake to fetch two pinned header-only dependencies

The SDK setup defines:

~~~text
DEVKIT_LOCATION=D:\Maya-MCP\vendor\devkitBase
MAYA_LOCATION=C:\Program Files\Autodesk\Maya2027
~~~

Open a new terminal to read those user environment variables.

## Quick start

Build the Release package:

~~~powershell
.\scripts\build.ps1
~~~

Run the full standalone integration test:

~~~powershell
.\scripts\test-plugin.ps1
~~~

Expected result:

~~~text
MAYA_MCP_TEST_RESULT={"protocol":"2025-11-25","resources":4,"rigging_pipeline":"passed","security_checks":"passed","tools":16,"typed_mutation":"passed","version":"0.2.0"}
~~~

Install the module for your Maya user:

~~~powershell
.\scripts\install-module.ps1
~~~

Restart Maya. Open **Windows > Settings/Preferences > Plug-in Manager**, then
load maya_mcp.mll. The server starts with the plug-in.

Verify it from Maya's Script Editor:

~~~python
import json
import maya.cmds as cmds

print(json.dumps(json.loads(cmds.mayaMcpStatus()), indent=2))
~~~

The packaged plug-in is:

~~~text
build\maya2027-mcp-vs2022\package\maya-mcp\plug-ins\maya_mcp.mll
~~~

## Connect an MCP client

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

Reveal the token only when configuring a trusted client:

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
- maya.node.apply — transactional create, edit, connect, and delete operations
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
- maya.rig.skin — bind, unbind, or inspect skinClusters

### Viewport and vision

- maya.viewport.capture — image plus camera and semantic metadata
- maya.viewport.project — world-to-screen points and screen-to-world rays
- maya.viewport.pick — pixel-to-node/component picking with selection restore

### Escape hatch

- maya.script.execute — Python or MEL with captured output and code hash

All normal edit tools use named Maya undo chunks. Python and MEL cannot be
honestly sandboxed or force-cancelled inside Maya.

## Enable Python and MEL execution

Script execution is disabled by default. To enable it for a trusted local
session, set the flag before Maya loads the plug-in:

~~~powershell
$env:MAYA_MCP_ALLOW_UNSAFE_CODE = '1'
& 'C:\Program Files\Autodesk\Maya2027\bin\maya.exe'
~~~

Loading the plug-in still does not make remote network access available. The
server remains bound to 127.0.0.1 and requires its bearer token.

## Maya commands

~~~python
cmds.mayaMcpStatus()  # JSON status
cmds.mayaMcpStop()    # stop accepting requests
cmds.mayaMcpStart()   # restart transport and write new discovery
cmds.mayaMcpPump()    # manually drain queued work; mainly for batch tests
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
- [Roadmap and known limits](docs/ROADMAP.md)

The devkit and generated builds are excluded from Git. CMake fetches
cpp-httplib v0.50.1 and nlohmann/json v3.12.0 into the ignored build tree.
