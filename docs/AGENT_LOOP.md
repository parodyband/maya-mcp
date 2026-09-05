# Observe, act, and verify Maya

Start with `maya.observe`. It returns a viewport image, current context, scoped
node attributes, a scene map at the image resolution, and an observation ID.
Send an `observe` object with your next workflow or script call to receive fresh
evidence after execution. This avoids a separate verification request.

These capabilities ship in 0.6.0. Install the matching package and restart
Maya and the MCP client to use them. The compact profile has eight tools; the
full profile still exposes every operation directly.

## One action and its evidence

Inspect a known target:

```json
{"nodes":["|character"],"width":960,"height":540}
```

Use the returned `data.observation_id` to guard an edit. This workflow frames
the target without altering selection, then returns the updated image and node
facts in the same response:

```json
{
  "if_observation":"observation:REPLACE_WITH_RETURNED_ID",
  "steps":[{
    "id":"frame",
    "tool":"maya.viewport.frame",
    "arguments":{"nodes":["|character"],"fit_factor":0.8}
  }],
  "observe":{"nodes":["|character"],"width":960,"height":540}
}
```

Omit `if_observation` when there is no prior observation to guard. For a newly
created target, post-action observation can reference the workflow result:

```json
{
  "steps":[{
    "id":"create",
    "tool":"maya.geometry.apply",
    "arguments":{"kind":"cube","name":"agentCube"},
    "select":{}
  }],
  "observe":{"nodes":[{"$ref":"create#/data/transform"}]}
}
```

Execution results stay in the normal envelope. Post-action feedback is in
`data.observation`, with its own `ok` and data. `data.observation_image_indices`
maps feedback images to the outer MCP content array. Image responses are
image-only for host compatibility; metadata stays in `structuredContent`.

A failure to capture or resolve observation targets never reruns completed
edits or changes a successful edit into a failed edit. It returns
`POST_OBSERVATION_FAILED`. Failed workflow steps and scripts that may have
partially mutated the scene also receive requested feedback when possible.
Invalid observation schemas and reference ordering fail before execution.

## Observation scope and coherence

Default observations use selected objects, or at most 40 transforms when the
selection is empty. Specify `nodes`, `max_nodes` (up to 100), and `attributes`
to keep inspection relevant. `image:false` performs structural inspection.
Batch Maya returns structural data and a viewport-unavailable warning when an
image is requested. Color defaults to 960×540, capped at 1024 per dimension.
Optional depth is bounded to 128 pixels on its longest side.

Observation uses synchronous Maya refresh before image readback. It compares
scene epoch, observed revision, context revision, journal cursor, camera matrices,
and viewport size before and after collection. Changes or failed node watches
make `coherence.unchanged_during_observation` false. This is a detected-coherence
check, not snapshot isolation or proof that every renderer and deformer is idle.

Observation IDs belong to the originating MCP client, expire after five minutes,
and have a global 64-record cache. `if_observation` rejects expired, incoherent,
or detectably stale observations before running the edit. New/open and plug-in
unload invalidate observations. Watches cover relevant nodes, shapes, ancestors,
and camera nodes where possible. They do not establish complete evaluated scene
truth; observe again when changing scope or working with unsupported changes.

## Poll changes without dumping the scene

After observing, pass its `data.cursor` to `maya.scene.changes`:

```json
{"cursor":"REPLACE_WITH_RETURNED_CURSOR","limit":200}
```

Read `events` and continue with `next_cursor` while `has_more` is true. Do not
jump to `latest_cursor` when unread pages remain. New/open, overflow, malformed
cursors, or callback gaps set `requires_observation:true` with a reason.
An observation's `since` parameter can include the same change page alongside
the image and current facts.

The journal holds 2,048 events and watches at most 512 nodes. Native Maya API
callbacks report global node additions/removals, selection/time, and watched
attribute/name changes. Watches are additive until reset or node deletion.
Coverage lists the watched nodes and rejected targets. Duplicate UUIDs belonging
to different native objects are reported as a gap rather than silently conflated.

Parenting, geometry components, downstream evaluation, and playback attribute
changes are not comprehensively covered. Autodesk specifically documents that
attribute callbacks are not emitted during playback or scrubbing.
[MNodeMessage reference](https://help.autodesk.com/cloudhelp/2026/ENU/MAYA-API-REF/py_ref/class_open_maya_1_1_m_node_message.html)

The journal also maintains a dependency-node count from native add/remove
callbacks. Scene-signature checks use this constant-time count instead of
enumerating every node on every call. Installation and scene replacement rebuild
it once; lost coverage falls back to the conservative enumeration path.

## Persistent Python and the Maya SDK

Call `maya.session` with `{"action":"open"}`. Pass the returned `session_id`
to `maya.script.execute`:

```json
{
  "language":"python",
  "session_id":"REPLACE_WITH_RETURNED_SESSION_ID",
  "source":"cube = maya.node(maya.call('maya.geometry.apply', {'kind':'cube', 'name':'sessionCube'})['transform'])\nresult = cube.name",
  "undo":"chunk",
  "observe":{"nodes":["sessionCube"]}
}
```

In a later call using that Python session:

```json
{
  "language":"python",
  "session_id":"REPLACE_WITH_RETURNED_SESSION_ID",
  "source":"maya.call('maya.node.apply', {'operations':[{'op':'set_transform','node':cube.selector,'translate':[0,3,0]}]})\nresult = cube.name",
  "undo":"chunk",
  "observe":{"nodes":["sessionCube"]}
}
```

`cube` and ordinary helper functions survive between cells. `arguments` and the
cell's `maya` SDK are refreshed each time, and old `result` values are cleared.
The SDK supports:

| Method | Behavior |
|---|---|
| `maya.call(name, args)` | Validate and run a typed operation; return data or raise a tool error |
| `maya.observe(**options)` | Return observation data and emit its viewport image |
| `maya.node(selector)` | Retain a scene-aware canonical handle; `.name` resolves live identity, `.selector` returns a valid selector |
| `maya.keep(value)` | Store finite JSON data and return an opaque result handle |
| `maya.get(handle)` | Retrieve a copy of stored JSON data |
| `maya.release(handle)` | Free stored result data |

Node handles resolve again on access, so rename works and deletion or scene
replacement fails instead of silently selecting a different node. Use the current
cell's SDK; storing an old `maya` object and calling it later raises `STALE_CELL`.
SDK calls cannot recursively execute scripts, workflows, or session management.

Use `maya.session` actions `status`, `reset`, or `close` with `session_id`.
Reset preserves the token and clears its namespace and results. New/open and
unload invalidate every Python session. Sessions belong to their authenticated
MCP client; a different client cannot use the token. There are 16 sessions
globally, with 30-minute idle expiry. Each cell permits 64 SDK calls and at most
8 images/6 MiB encoded image data. Each session stores at most 64 JSON results
and 4 MiB of explicit result data.

Python/MEL keep the existing permission gate and full host privileges. Arbitrary
Python namespace objects are not memory-bounded, sandboxed, or force-cancellable.
Use bounded loops and return selected results. Session isolation prevents
accidental cross-client token use; it is not isolation from trusted arbitrary
Python with full process access.

## Recover a request without repeating edits

Add a unique `request_id` to a native workflow/script call to opt into tracked
execution. It returns immediately with a queued status. Poll
`maya.request.status` with the same ID while keeping the MCP session alive:

```json
{"request_id":"character-controls-001"}
```

Status reports `queued`, `running`, `completed`, or `failed`. A completed status
query has its own `ok:true`; the execution outcome is in
`data.result.isError` and `data.result.structuredContent`. Always inspect that
inner outcome. Recovered images appear in the status tool's outer content.
`result_content: "images_at_top_level"` and `image_content_indices` describe
their relocation from the nested result. Repeating the exact original call
replays the original complete result; it never enqueues the edit again.

Reusing an ID with different arguments returns `REQUEST_ID_CONFLICT`. IDs are
scoped to the live MCP session, with a maximum 128 UTF-8 bytes. Native status
does not dispatch to Maya, so it remains available while a tracked edit runs.
Its empty scene epoch and zero revisions are control metadata, not an observation.

The cache has a global 1,024-request and 128 MiB serialized budget. Pending
requests reserve a maximum result allocation; retrieving a terminal result
shrinks that reservation to its actual size. When capacity is exhausted, new
tracked work is refused before enqueue. Live-session IDs and results are not
silently evicted. Recoverable results are limited to 15 MiB.

Recovery ends when the MCP session closes/expires or the server restarts.
**Closing a session is not cancellation: queued work can still execute after
DELETE.** Retrieve outstanding outcomes before closing. After losing the MCP
session, inspect Maya before deciding whether any edit needs repeating.
Calls without `request_id` retain synchronous behavior and have no replay key.
