# Architecture

Maya MCP keeps network work away from Maya's UI thread while preserving ordered,
main-thread access to Maya APIs.

## Runtime flow

~~~mermaid
flowchart LR
    Client["Codex or Claude"] -->|"stdio"| Stdio["Native discovery bridge"]
    Stdio -->|"HTTP POST + bearer token"| HTTP["C++ Streamable HTTP"]
    HTTP --> Protocol["JSON-RPC and MCP router"]
    Protocol --> Queue["Bounded FIFO"]
    Queue -->|"Maya timer callback"| Bridge["C++ Python bridge"]
    Bridge --> Tools["Typed Maya runtime"]
    Tools --> Maya["Maya DAG, DG, viewport, rig, files"]
    Maya --> Result["Structured data and image content"]
    Result --> Client
~~~

The HTTP layer never calls Maya APIs. It parses, authenticates, validates the
MCP lifecycle, then waits for a queued main-thread task.

## Non-negotiable invariants

1. Every Maya API, command, Python, and MEL call runs on Maya's main thread.
2. Network listeners and workers stop and join before the plug-in DLL unloads.
3. Every callback registered by the plug-in is removed during unload.
4. Potentially large inputs are bounded before Maya sees them.
5. Typed tools are the default. Arbitrary code is a separately enabled fallback.
6. Ambiguous short node names are errors.
7. Viewport images use MCP ImageContent rather than base64 inside text.

## Native components

| Component | Responsibility |
|---|---|
| src/plugin.cpp | Maya command registration, startup, shutdown, and ownership |
| MainThreadDispatcher | Bounded FIFO drained by a removable Maya timer callback |
| McpServer | Loopback HTTP, authentication, sessions, and MCP routing |
| maya-mcp-bridge | Stdio framing, active-process discovery, and HTTP session forwarding |
| PythonBridge | Base64 JSON boundary into Maya's bundled Python runtime |
| Vp2CaptureCommand | Bounded VP2 render-target capture with retained callback ownership |
| maya_mcp_runtime | Schemas, identities, undo policy, and typed Maya operations |

The C++ and Python boundary is deliberate. C++ owns lifetime, concurrency,
transport, and security. Python provides fast access to Maya commands for broad
coverage without compiling one native command per Maya feature.

Release packages are emitted per Maya API. The updater selects an exact API
match, verifies GitHub and manifest digests, and atomically advances only the
matching Maya-version module descriptor for the next process start.

Codex and Claude Code launch a stable PowerShell entry point over stdio. It reads
the active discovery version, chooses the matching side-by-side native bridge,
and inherits the client's standard streams. The native bridge accepts only a
`127.0.0.1` MCP URL, reads the per-process token without printing it, and forwards
the negotiated MCP session header. Claude Desktop bundles the same bridge as a
Windows MCP Bundle.

Depth render-target capture already lives in C++. High-volume mesh data,
additional Viewport 2.0 passes, native undo commands, and UFE support will move
into C++ as the project matures.

## Main-thread dispatcher

Maya APIs are generally not thread-safe. Transport workers enqueue callables
into a 256-item FIFO. A removable Maya timer callback and an interactive Qt
heartbeat both drain up to 32 items or five milliseconds of work per tick.
The Qt heartbeat keeps requests moving while Maya playback suppresses timer
callbacks. Reentrancy protection makes the two pump sources safe to overlap.

The timer callback is preferable to one idle task per request:

- request order remains FIFO;
- the callback has a removable Maya callback ID;
- queued promises can be failed during stop;
- no raw task pointer can outlive the plug-in DLL.

The manual mayaMcpPump command drains the same queue. Tests use it because Maya
standalone has no interactive event loop.

Native tool calls enter Python with Maya undo recording explicitly enabled;
read-only MCP resources do not. This distinction is required because Maya's
native Python execution API otherwise defaults to an undo-disabled context.

Long Maya operations still block Maya. A timeout cannot safely kill Python,
MEL, skinning, or many Maya commands once they begin.

## Plug-in lifecycle

Startup order:

1. Install the main-thread dispatcher.
2. Import and validate the Python tool catalog.
3. Construct the server.
4. Register Maya commands.
5. Bind loopback, write discovery, and start listening.

Shutdown order:

1. Refuse unload while a dispatched tool or VP2 capture is executing.
2. Prove no VP2 render notification remains registered.
3. Deregister Maya commands in reverse order.
4. Reject new work, stop HTTP, and join the listener and worker pool.
5. Delete discovery owned by this Maya process.
6. Remove the Maya timer callback.
7. Stop the Qt heartbeat, run Python lifecycle cleanup, and destroy owned state.
8. Allow the DLL to unload.

Registration and teardown are transactional around DLL safety. If a command or
callback cannot be removed, removed commands are restored where possible and
Maya is told to keep the plug-in loaded. A failed VP2-notification removal keeps
its heap-owned callback state alive for a later cleanup retry.

## MCP protocol

The server targets the current stable MCP revision, 2025-11-25.

Implemented:

- initialize and notifications/initialized
- cryptographic MCP session IDs
- MCP-Protocol-Version validation
- ping
- tools/list and tools/call
- resources/list and resources/read
- prompts/list and prompts/get
- DELETE session termination
- direct application/json responses

GET on the MCP endpoint returns 405. Server-Sent Events, progress, resource
subscriptions, and server-to-client notifications are not advertised yet.

## Node identity

A Maya UUID alone is insufficient for remote identity. Referencing the same
source more than once can repeat source UUIDs, and one DAG node can have
multiple instance paths.

Each result therefore includes:

- an opaque node_id scoped to scene_epoch;
- Maya UUID;
- reference node context;
- long node name;
- every known DAG path;
- node type, referenced state, and lock state.

Clients should send node_id back when possible. Stable claims supplied beside a
node_id are checked for contradictions. DAG-instance-specific work should also
send dag_path once those tools expose per-instance operations.

scene_epoch changes after Maya creates or opens a scene, including user-driven
file operations. scene_revision increments for MCP mutations and for observed
out-of-band changes to the scene signature. context_revision tracks selection
and time changes through Maya event callbacks.

Native DG and per-node change observation will replace the remaining
scene-signature fallback before 1.0.

## Undo and transactions

maya.node.apply resolves operations in order and supports step aliases such as
$newRoot. Normal edits run inside one named Maya undo chunk. Any Python
exception closes and attempts to undo the chunk.

Undo chunks are useful but weaker than a native MPxCommand with explicit
undoIt and redoIt. A native transaction command is planned for operations that
need guaranteed rollback.

File operations and arbitrary scripts do not claim atomic rollback.

Rig previews use a separate transient lifecycle. Direct Maya API modifiers avoid
globally toggling undo; exact owned nodes are tagged by UUID and marked
`doNotWrite`. Preview handles carry scene epoch and preview revision. Strict
cleanup resolves every owned UUID, rejects foreign descendants, verifies no
survivors, and retains failed records for retry. Active previews and aggregate
owned nodes have fixed caps.

Accept requires Maya undo, preflights all names and targets, then creates
permanent rig nodes in one normal undo chunk. It is intentionally described as
preflighted and undo-chunked, not as a native atomic transaction.

## Viewport and vision

maya.viewport.capture returns:

- PNG or JPEG image content;
- optional bounded native VP2 depth data with exact render-target metadata;
- active camera identity;
- model-view and projection matrices;
- clip planes;
- viewport size;
- scene time and units;
- current selection;
- projected joint locations.

Color uses Maya's compatibility `M3dView` readback with fixed dimension and
encoded-response budgets. Optional depth invokes the
internal `mayaMcpVp2Capture` command. That command installs a scoped VP2
end-render notification, refreshes the active view, copies the current depth
target, downsamples it under fixed dimension and payload budgets, then removes
the notification before returning. Failed removal retains both callback metadata
and client data, and plug-in unload is refused until cleanup succeeds.

maya.viewport.project grounds world landmarks into pixels and pixels into world
rays. maya.viewport.pick maps a pixel to Maya nodes or components while
restoring the user's selection.

maya.viewport.scene_map projects conservative object bounds and pivots while
retaining canonical node identity. It complements the image and depth channel;
it does not claim occlusion, instance, shared-snapshot, or segmentation truth.

The next native vision layer will add stable object-ID, normal, wireframe, and
rig-overlay passes. Object-ID requests currently return an explicit unsupported
result rather than an unstable mapping.

## Adding a tool

1. Add a strict input schema and description in catalog.py.
2. Implement a handler in the relevant tools module.
3. Add it to that module's handler map.
4. Return through state.result so output remains schema-compatible.
5. Add a Maya standalone test or an interactive viewport test.
6. Document undo, destructiveness, and any file or host access.

See the official [MCP transport specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
and [MCP tools specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).
