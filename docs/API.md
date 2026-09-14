# Protocol and Tool API

Maya MCP exposes one Streamable HTTP endpoint and returns standard JSON-RPC 2.0
MCP messages.

## Transport

Default endpoint:

~~~text
POST http://127.0.0.1:7001/mcp
~~~

Required request headers:

~~~http
Authorization: Bearer TOKEN
Accept: application/json, text/event-stream
Content-Type: application/json
~~~

After initialization, also send:

~~~http
MCP-Session-Id: SESSION
MCP-Protocol-Version: 2025-11-25
~~~

The actual URL, token, process ID, and protocol version are in the discovery
file reported by mayaMcpStatus.

## Initialize

~~~json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-11-25",
    "capabilities": {},
    "clientInfo": {
      "name": "my-maya-client",
      "version": "1.0"
    }
  }
}
~~~

Send notifications/initialized with the returned MCP-Session-Id before using
tools, resources, or prompts.

## Canonical node selectors

Tools accept a unique Maya name or an identity-bearing canonical reference.
Reference objects must contain `node_id`, `dag_path`, `long_name`, a non-empty
`dag_paths` entry, or `name`; UUID-only and metadata-only objects are rejected.

~~~json
{
  "node_id": "node:SCENE_EPOCH:OPAQUE_ID"
}
~~~

A full node result includes:

~~~json
{
  "node_id": "node:SCENE_EPOCH:OPAQUE_ID",
  "scene_epoch": "SCENE_EPOCH",
  "uuid": "MAYA_UUID",
  "reference_node": null,
  "name": "L_wrist_JNT",
  "long_name": "|rig|skeleton|L_wrist_JNT",
  "type": "joint",
  "dag_paths": ["|rig|skeleton|L_wrist_JNT"],
  "referenced": false,
  "locked": false
}
~~~

Short names that resolve to multiple nodes produce `TARGET_AMBIGUOUS`. When a
reference includes `node_id` plus UUID, reference-node, type, or path claims,
those claims must agree with the registered identity or the tool returns
`NODE_REFERENCE_CONFLICT`. `maya.selection.set` preserves a returned
`component` suffix. Only component-aware tools such as `maya.selection.set`
and material assignment accept it; node-only tools reject component-bearing
references instead of widening them to an owning node.

## Result envelope

Successful tools return both structuredContent and a serialized text block for
client compatibility:

~~~json
{
  "schema_version": "1.0",
  "ok": true,
  "request_id": "REQUEST_ID",
  "scene_epoch": "SCENE_EPOCH",
  "revisions": {
    "scene_before": 7,
    "scene_after": 8,
    "context": 2
  },
  "summary": "Created a 3-joint chain",
  "data": {},
  "changes": [],
  "warnings": [],
  "undo": {
    "available": true,
    "label": "Create joint chain"
  },
  "timing_ms": 4.2
}
~~~

Recoverable Maya/tool failures use an MCP tool result with isError true.
Protocol-shape and unknown-method failures use JSON-RPC errors.

## Core tools

| Tool | Required fields | Main output |
|---|---|---|
| maya.context.get | none | Maya, scene, units, time, selection, viewport, undo |
| maya.scene.query | none | bounded canonical node records |
| maya.node.apply | operations | ordered operation results and aliases |
| maya.selection.set | mode | canonical active selection |
| maya.history.apply | action | applied undo or redo count |

## Agent workflows

`tools/list` defaults to eight tools: observation, scene query, scoped changes,
operation discovery, workflow execution, scripting, Python sessions, and request
status. Set `MAYA_MCP_TOOL_PROFILE=full` before plug-in startup for all 25 tools.
The profile is fixed when the native server reads its catalog; reconnect clients
after reloading. `maya.tools.describe` always searches the complete registry.
The compact profile is a discovery choice, not an authorization restriction.

Find operation summaries without loading their schemas:

~~~json
{"query":"material"}
~~~

Retrieve exact schemas for known names, up to eight per call:

~~~json
{"names":["maya.geometry.apply","maya.node.apply"]}
~~~

Call `maya.workflow.run` with this input to create, move, and inspect a cube in
one native main-thread dispatch:

~~~json
{
  "steps": [
    {
      "id": "create",
      "tool": "maya.geometry.apply",
      "arguments": {"kind":"cube","name":"workflowCube"},
      "select": {}
    },
    {
      "id": "move",
      "tool": "maya.node.apply",
      "arguments": {
        "operations": [{
          "op":"set_transform",
          "node":{"$ref":"create#/data/transform"},
          "translate":[2,3,4]
        }]
      },
      "select": {}
    },
    {
      "id": "inspect",
      "tool": "maya.scene.query",
      "arguments": {
        "scope":"nodes",
        "nodes":[{"$ref":"create#/data/transform"}],
        "include_attributes":["translate"]
      },
      "select": {"position":"/data/nodes/0/attributes/translate"}
    }
  ]
}
~~~

The final step returns `position: [[2,3,4]]`. Earlier step data stays available
for references even when `select: {}` suppresses it from the response.

Reference objects contain exactly `$ref`, whose value is
`stepId#` followed by a JSON Pointer into that step's structured envelope.
Use `/data/...` for operation data, an empty pointer for the complete envelope,
and `~1` or `~0` for literal slash or tilde in keys. Array indexes are zero-based.
References must name earlier steps; nesting workflows is rejected.

All step schemas, reference ordering, and selection-pointer syntax are checked
before the first step. Resolved values are validated again before each handler
runs. Scene-dependent checks and referenced output existence cannot be fully
validated ahead of execution. Unknown Maya operations never fall through to
arbitrary scripting.

Optional `if_scene_epoch` and `if_scene_revision` guard the workflow's starting
scene. Revisions retain the existing approximate external-edit detection.
Workflows are ordered batches with each handler's existing undo policy, not
atomic transactions. Use `maya.node.apply` for one undo chunk of graph edits.

On failure, `isError` is true and the error code is `WORKFLOW_STOPPED`. Inspect
`data.steps`, `completed`, `failed_step`, and `skipped` before doing more work.
Earlier successful edits remain applied. Each step reports its own undo recording,
revisions, and timing. **Maya groups recorded changes from a native MCP request
into one undo item**, as reported by `data.undo_scope: "native_request"` and
each step's `undo.scope`. Do not issue one history undo per step. Child chunks
still support rollback of a failing operation; this does not make the entire
workflow atomic. Direct Python handler tests have operation-level undo scope.
Top-level undo availability is conservative for workflows containing file,
script, or history operations, which can invalidate the history stack. If output
selection fails after an edit, `OUTPUT_SELECTION_FAILED` is a warning and the
successful edit stays successful. Do not repeat it to repair output selection.

Workflows accept at most 32 steps. Returned data and change records each have
a 512 KiB budget. Suppressed output is marked as truncated. If retained step
results exceed 4 MiB, execution stops before the next step and reports it as
unexecuted. Image content has a separate 6 MiB serialized budget. Each step's
`image_content_indices` identifies its images in the outer MCP content array;
any embedded operation-level indexes remain relative to that original operation.
Like viewport capture, a workflow with images returns image-only `content` for
client compatibility and keeps the complete envelope in `structuredContent`.
If every image is omitted by the budget, visible text contains only a brief
status message; renderer payloads stay in structured content. Workflows that
never produce images also serialize the envelope into text. Error summaries,
messages, and details have separate bounds so large exceptions cannot hide the
completed-step report behind a transport-size error.

Python scripts accept an `arguments` object exposed directly to the source:

~~~json
{
  "language":"python",
  "source":"result = cmds.ls(arguments['pattern'], long=True)[:50]",
  "arguments":{"pattern":"*_CTRL"}
}
~~~

Python/MEL retain their existing permission gate and full host privileges.
Workflow composition does not bypass that gate. Scripts without `session_id`
use a fresh namespace per call. `maya.session` provides persistent, owner-bound
Python namespaces. An optional `observe` object on workflow/script requests
returns post-action feedback, and `if_observation` rejects detectably stale input.
Native `request_id` supports replay within the same live MCP session. There is
no automatic retry or force-cancellation. See [the agent loop contract](AGENT_LOOP.md)
for examples, limits, observation coverage, and request lifecycle.

## Scene query

~~~json
{
  "scope": "subtree",
  "root": "|character|skeleton",
  "node_types": ["joint"],
  "name_glob": "L_*",
  "include_attributes": ["translate", "rotate", "jointOrient"],
  "include_connections": "both",
  "limit": 200
}
~~~

Scopes are scene, selection, subtree, and nodes.

Results are sorted by long name. To continue, resend the identical query with
the returned `next_cursor` as `cursor`. A null cursor marks the last page.
The cursor binds query arguments, scene epoch, observed revision, and ordered
matching node names. A changed query or membership returns `STALE_CURSOR`;
restart without a cursor. This is not a frozen attribute snapshot: unobserved
external attribute edits can still occur between calls.

## Transactional node operations

Supported operation names:

- create
- duplicate
- rename
- delete
- parent
- set_transform
- set_attribute
- add_attribute
- connect
- disconnect
- create_control
- create_ik_handle
- create_constraint
- set_driven_keys

An operation ID creates a step alias. Later operations reference it with a
dollar-prefixed string:

~~~json
{
  "label": "Build control root",
  "if_scene_revision": 12,
  "operations": [
    {
      "id": "root",
      "op": "create",
      "node_type": "transform",
      "name": "rigControls_GRP"
    },
    {
      "op": "set_transform",
      "node": "$root",
      "translate": [0, 10, 0],
      "space": "world"
    },
    {
      "op": "add_attribute",
      "node": "$root",
      "attribute": "rigVersion",
      "attribute_type": "string",
      "value": "1.0"
    }
  ]
}
~~~

Set validate_only to true to resolve and validate without editing.

Rig operations share the same transaction and aliases. This request creates a
custom pole control, RP IK handle, pole-vector constraint, an animator
attribute, and foot-roll driven keys in one undo step:

~~~json
{
  "label": "Build left leg IK",
  "operations": [
    {
      "id": "pole",
      "op": "create_control",
      "name": "L_legPole_CTRL",
      "shape": "diamond",
      "size": 2.0,
      "translate": [4, 8, 6],
      "color_rgb": [0.2, 0.5, 1.0]
    },
    {
      "id": "legIk",
      "op": "create_ik_handle",
      "name": "L_leg_IKH",
      "start_joint": "L_hip_JNT",
      "end_joint": "L_ankle_JNT",
      "solver": "ikRPsolver"
    },
    {
      "op": "create_constraint",
      "constraint_type": "pole_vector",
      "drivers": ["$pole"],
      "driven": "$legIk",
      "name": "L_leg_PVC"
    },
    {
      "op": "add_attribute",
      "node": "$pole",
      "attribute": "footRoll",
      "attribute_type": "double",
      "min_value": -10,
      "max_value": 10,
      "default_value": 0,
      "keyable": true
    },
    {
      "op": "set_driven_keys",
      "driver_plug": "$pole.footRoll",
      "driven_plug": "$legIk.rotateX",
      "driven_keys": [
        {"driver_value": -10, "value": -35},
        {"driver_value": 0, "value": 0},
        {"driver_value": 10, "value": 55}
      ]
    }
  ]
}
~~~

`create_constraint` supports parent, orient, point, scale, aim, and pole-vector
constraints. Utility nodes for IK/FK blending can be created with `create` and
wired with `connect`; enum, numeric limits, defaults, keyability, channel-box
visibility, and locking are supported by `add_attribute`.

## Content tools

| Tool | Actions |
|---|---|
| maya.geometry.apply | cube, sphere, cylinder, cone, plane, torus, curve |
| maya.material.apply | create_assign, assign, inspect |
| maya.animation.apply | set_keys, delete_keys, inspect |
| maya.animation.profile | create, get, export, list, delete |
| maya.animation.describe | channel, tangent, infinity, layer and editability inspection |
| maya.animation.sample | bounded evaluated motion and declared contacts |
| maya.animation.capture | aligned images, frame sheets, trails, landmark ghosts, review player |
| maya.animation.artifact | get, frames, notes, list, release |
| maya.animation.analyze | contact, pose-step, reach and loop diagnostics |
| maya.animation.edit | per-channel set_keys, tangents, delete_keys, retime |
| maya.animation.compare | retained take comparison with explicit frame alignment |
| maya.animation.layer | candidate mute, solo and weight |
| maya.file.apply | query, save, save_as, open, import, reference, export_selection |

See [Animation review and editing](ANIMATION.md) for examples, bounds, reference
timing, candidate layers, and evaluation limitations.

All transforms are explicit arrays of three numbers. Times are numeric Maya
time values under the current scene time unit.

## Rigging tools

### Create a joint chain

~~~json
{
  "action": "create_chain",
  "joints": [
    {"name": "shoulder_JNT", "position": [0, 10, 0]},
    {"name": "elbow_JNT", "position": [4, 7, 0]},
    {"name": "wrist_JNT", "position": [8, 5, 0]}
  ],
  "primary_axis": "xyz",
  "secondary_axis": "yup",
  "orient": true
}
~~~

Use action inspect with root to read a skeleton.

### Create controls

~~~json
{
  "action": "create",
  "targets": [
    {"node_id": "node:SCENE_EPOCH:SHOULDER"}
  ],
  "shape": "circle",
  "size": 2.0,
  "color": 17,
  "constraint": "parent",
  "maintain_offset": true
}
~~~

Shapes are circle, square, and cube. Constraints are none, parent, orient, and
point.

### Preview rig placement

`maya.rig.preview` supports `create`, `update`, `query`, `list`, `accept`, and
`cancel`. Create and update return an epoch- and revision-bound handle. Always
send the complete latest handle to later actions.

| Action | Required input | Effect |
|---|---|---|
| `create` | `joints` and/or `controls` | Create a bounded, non-serializing preview |
| `update` | latest `handle` plus changed fields | Replace the preview and issue a new handle revision |
| `query` | latest `handle` | Return markers and the full canonical acceptance `spec` |
| `list` | none | Return at most the 16 active previews and their specs |
| `accept` | latest `handle`; undo enabled | Preflight and commit permanent outputs in one named undo chunk |
| `cancel` | latest `handle` | Verify ownership and remove only preview-owned nodes |

~~~json
{
  "action": "create",
  "name": "Arm Preview",
  "joints": [
    {"id": "shoulder", "name": "L_shoulder_JNT", "position": [2, 12, 0]},
    {"id": "elbow", "name": "L_elbow_JNT", "position": [6, 10, 0], "parent_id": "shoulder"}
  ],
  "controls": [
    {"id": "elbowControl", "target_joint_id": "elbow", "shape": "circle"}
  ]
}
~~~

Scalar update fields merge with the previous spec. Supplying `joints` or
`controls` replaces that entire collection. Query and list return the full spec,
including parent, orientation, axes, colors, names, targets, constraints, and
offset settings, so a client can verify exactly what accept will create.

Preview code uses direct Maya API modifiers and never disables the user's undo
queue or forcibly resets the dirty flag. Owned nodes have exact UUID-backed tags
and are marked `doNotWrite`, so normal preview operations remain selection-,
undo-, and save-neutral. Because the preview uses live DAG/DG nodes, Maya may
honestly mark a clean scene dirty; the implementation never resets that flag.
If a third-party callback performs a real edit synchronously, its dirty/undo
effects are left visible.

Previews do not expire on a timer. They remain until `cancel`, scene new/open,
or plug-in unload, subject to 16 active previews and 8,192 total owned nodes.
Strict cleanup refuses missing, tampered, or externally parented ownership and
keeps the handle retryable.

Acceptance requires Maya undo to be enabled, preflights every output and target,
creates root-namespace names exactly as planned, and commits permanent nodes in
one named undo chunk. This is a normal Maya undo chunk, not a native atomic
`MPxCommand`; rollback after an unexpected Maya failure is best effort. Use
`if_scene_revision` when the plan depends on an earlier scene read.

See [Vision-guided rigging](VISION_RIGGING.md) for the full review-and-accept
workflow.

### Bind skin

~~~json
{
  "action": "bind",
  "geometry": [{"node_id": "node:SCENE_EPOCH:MESH"}],
  "influences": [
    {"node_id": "node:SCENE_EPOCH:SHOULDER"},
    {"node_id": "node:SCENE_EPOCH:ELBOW"},
    {"node_id": "node:SCENE_EPOCH:WRIST"}
  ],
  "max_influences": 3,
  "dropoff_rate": 4,
  "normalize": true
}
~~~

Actions are bind, unbind, and inspect.

## Viewport tools

`maya.viewport.capture` accepts optional `width`, `height`, `format`,
`include_joint_projections`, `include_depth`, and `depth_max_dimension`. The
response contains one color MCP image:

~~~json
{
  "type": "image",
  "data": "BASE64_IMAGE",
  "mimeType": "image/png",
  "annotations": {
    "audience": ["assistant", "user"],
    "priority": 1.0
  }
}
~~~

The structured result contains camera matrices and semantic metadata, not a
second copy of the color image. Color dimensions are capped at 2,048 by 2,048,
and encoded color output is capped at 8,388,608 base64 characters. When
`include_depth` is true,
`data.native_capture.passes.depth` contains a bounded base64 native render
target plus exact format, dimension, pitch, stride, and byte-count metadata.
Depth is capped at a 1024-pixel maximum dimension and 4,194,304 encoded
characters. Depth payload bytes are not duplicated into the text fallback.
Values are the renderer-native, normally non-linear hardware depth buffer, not
world-unit distances. Renderer-native row zero has not been normalized across
backends, so depth is experimental and not yet guaranteed pixel-correlated with
color. Do not use it for reconstruction without validating the backend's row
origin.

`maya.viewport.scene_map` returns conservative projected world-AABB boxes,
pivots, and canonical node references. It is bounded by `max_nodes` and
`max_candidates`, with type filtering applied before expensive projection work.
It does not test occlusion, panel isolate state, or return a segmentation mask.
Near-plane-crossing bounds can be conservative only for the projectable
corners, DAG instances are not separate records, and capture/map calls are
separate snapshots. Confirm ambiguous evidence with picking.

maya.viewport.project accepts bounded `world_points`, `nodes`, and
`screen_points` arrays. It returns screen pixels or world rays.

maya.viewport.pick accepts x, y, and radius. Coordinates use a bottom-left
origin, matching Maya's viewport API. The original selection is restored, but
Maya temporarily changes active selection while picking, so `SelectionChanged`
callbacks run; the tool is intentionally not annotated read-only.

Viewport tools require interactive Maya. Native object-ID capture explicitly
returns `UNSUPPORTED_PASS`; use scene maps plus picking until a stable ID pass
and canonical legend are implemented.

## Script escape hatch

~~~json
{
  "language": "python",
  "source": "result = cmds.ls(selection=True, long=True)",
  "return_expression": "result",
  "undo": "none",
  "label": "Optional undo label"
}
~~~

The tool is enabled by default for the authenticated local client. Clear **Maya
MCP > Allow Python/MEL Automation This Session** or set
`MAYA_MCP_ALLOW_UNSAFE_CODE=0` before launch to disable it. Successful results
include stdout, stderr, a JSON-safe result, and SHA-256 source hash.

## Resources

| URI | Content |
|---|---|
| maya://context | scene, selection, time, units, viewport |
| maya://scene/summary | node counts, node types, roots, references |
| maya://selection | canonical selection |
| maya://timeline | ranges, time unit, animation-curve count |

## Prompts

| Name | Purpose |
|---|---|
| maya.viewport.inspect | correlate visual evidence with scene structure |
| maya.rig.from_landmarks | inspect, place, verify, control, and skin a rig |
| maya.scene.audit | non-destructive structural review |

## Common tool error codes

| Code | Meaning | Recovery |
|---|---|---|
| `INVALID_ARGUMENT` | Input failed semantic validation | Correct the named field and retry |
| `TARGET_NOT_FOUND` / `TARGET_AMBIGUOUS` | Selector resolved to zero or multiple nodes | Send a canonical `node_id` reference |
| `STALE_NODE_ID` / `SCENE_EPOCH_MISMATCH` | Identity or preview belongs to stale scene state | Re-read context and nodes, then re-plan |
| `NODE_REFERENCE_CONFLICT` | Visible identity claims contradict `node_id` | Use an unmodified canonical reference |
| `REVISION_CONFLICT` / `PREVIEW_REVISION_CONFLICT` | Scene or preview changed after planning | Query again and use the newest revision |
| `PREVIEW_NOT_FOUND` / `PREVIEW_NOT_ACTIVE` | Preview was cleaned or already accepted | List previews or create a new one |
| `PREVIEW_DAMAGED` / `PREVIEW_TAMPERED` | Owned nodes are missing or ownership is unsafe | Inspect details; repair only the named preview, then retry cancel |
| `PREVIEW_LIMIT_EXCEEDED` / `PREVIEW_NODE_LIMIT_EXCEEDED` | Retained preview budget is full | Cancel unused previews or reduce the spec |
| `UNDO_DISABLED` | Accept cannot guarantee a rollback path | Enable Maya undo before accepting |
| `VIEWPORT_UNAVAILABLE` | No interactive viewport is available | Run in interactive Maya and activate a model panel |
| `VIEWPORT_CAPTURE_TOO_LARGE` | Encoded color exceeded the response budget | Reduce width/height or use JPEG |
| `NATIVE_VIEWPORT_CAPTURE_FAILED` / `CAPTURE_FAILED` | VP2 depth contract or renderer operation failed | Retry in a standard VP2 view; inspect error details |
| `UNSUPPORTED_PASS` | Stable object-ID capture is not implemented | Use scene map plus pixel picking |
| `CAPABILITY_DISABLED` | Python/MEL escape hatch is off | Prefer typed tools or remove the explicit opt-out for a trusted session |
