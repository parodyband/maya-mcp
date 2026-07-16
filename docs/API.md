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

Tools accept a unique Maya name, a canonical reference, or a node_id:

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

Short names that resolve to multiple nodes produce TARGET_AMBIGUOUS.

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

### Scene query

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

### Transactional node operations

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

## Content tools

| Tool | Actions |
|---|---|
| maya.geometry.apply | cube, sphere, cylinder, cone, plane, torus, curve |
| maya.material.apply | create_assign, assign, inspect |
| maya.animation.apply | set_keys, delete_keys, inspect |
| maya.file.apply | query, save, save_as, open, import, reference, export_selection |

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

maya.viewport.capture accepts optional width, height, format, and
include_joint_projections. The response contains MCP image content:

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

The structured result contains the camera matrices and semantic metadata, not a
second copy of the image.

maya.viewport.project accepts world_points, nodes, and screen_points. It
returns screen pixels or world rays.

maya.viewport.pick accepts x, y, and radius. Coordinates use a bottom-left
origin, matching Maya's viewport API.

Viewport tools require interactive Maya.

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

The tool is disabled unless MAYA_MCP_ALLOW_UNSAFE_CODE is enabled. Successful
results include stdout, stderr, a JSON-safe result, and SHA-256 source hash.

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

- INVALID_ARGUMENT
- TARGET_NOT_FOUND
- TARGET_AMBIGUOUS
- STALE_NODE_ID
- SCENE_EPOCH_MISMATCH
- REVISION_CONFLICT
- PLUG_LOCKED
- CONNECTION_CONFLICT
- DIRTY_SCENE
- VIEWPORT_UNAVAILABLE
- VIEWPORT_CAPTURE_FAILED
- CAPABILITY_DISABLED
- SCRIPT_ERROR
