# Architecture

Maya MCP keeps network work away from Maya's UI thread while preserving ordered,
main-thread access to Maya APIs.

## Runtime flow

~~~mermaid
flowchart LR
    Client["MCP client"] -->|"HTTP POST + bearer token"| HTTP["C++ Streamable HTTP"]
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
| PythonBridge | Base64 JSON boundary into Maya's bundled Python runtime |
| maya_mcp_runtime | Schemas, identities, undo policy, and typed Maya operations |

The C++ and Python boundary is deliberate. C++ owns lifetime, concurrency,
transport, and security. Python provides fast access to Maya commands for broad
coverage without compiling one native command per Maya feature.

High-volume mesh data, Viewport 2.0 render passes, native undo commands, and UFE
support belong in C++ as the project matures.

## Main-thread dispatcher

Maya APIs are generally not thread-safe. Transport workers enqueue callables
into a 256-item FIFO. A Maya timer callback drains up to 32 items or five
milliseconds of work per tick.

The timer callback is preferable to one idle task per request:

- request order remains FIFO;
- the callback has a removable Maya callback ID;
- queued promises can be failed during stop;
- no raw task pointer can outlive the plug-in DLL.

The manual mayaMcpPump command drains the same queue. Tests use it because Maya
standalone has no interactive event loop.

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

1. Reject new work.
2. Pause the dispatcher and fail queued promises.
3. Stop the HTTP listener.
4. Join the listener and worker pool.
5. Delete discovery owned by this Maya process.
6. Remove the Maya timer callback.
7. Deregister Maya commands in reverse order.
8. Destroy all owned state before DLL unload.

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

Clients should send node_id back when possible. DAG-instance-specific work
should also send dag_path once those tools expose per-instance operations.

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

## Viewport and vision

maya.viewport.capture returns:

- PNG or JPEG image content;
- active camera identity;
- model-view and projection matrices;
- clip planes;
- viewport size;
- scene time and units;
- current selection;
- projected joint locations.

maya.viewport.project grounds world landmarks into pixels and pixels into world
rays. maya.viewport.pick maps a pixel to Maya nodes or components while
restoring the user's selection.

The next vision layer will use Viewport 2.0 render targets for depth, object-ID,
normal, wireframe, and rig-overlay passes.

## Adding a tool

1. Add a strict input schema and description in catalog.py.
2. Implement a handler in the relevant tools module.
3. Add it to that module's handler map.
4. Return through state.result so output remains schema-compatible.
5. Add a Maya standalone test or an interactive viewport test.
6. Document undo, destructiveness, and any file or host access.

See the official [MCP transport specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
and [MCP tools specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).
