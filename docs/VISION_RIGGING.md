# Vision-guided rigging

Use viewport evidence to plan a rig, inspect a non-serializing preview, and
commit the result in one named Maya undo chunk when undo is enabled.

## Recommended workflow

1. Read `maya://context` and `maya://scene/summary`.
2. Capture the active viewport with `maya.viewport.capture`.
3. Call `maya.viewport.scene_map` to correlate pixels with canonical nodes.
4. Create a `maya.rig.preview` from proposed joint and control positions.
5. Capture the viewport again and revise the preview with its latest handle.
6. Accept only after the placement and output names pass review.

The MCP host provides the vision model. The plug-in provides pixels, depth,
camera data, scene identity, and reversible Maya operations.

## Capture color and depth

~~~json
{
  "name": "maya.viewport.capture",
  "arguments": {
    "width": 1280,
    "height": 720,
    "format": "png",
    "include_depth": true,
    "depth_max_dimension": 512
  }
}
~~~

Color is returned as MCP `ImageContent`, with each axis capped at 2,048 pixels
and encoded output capped at 8,388,608 base64 characters. Optional depth is returned at
`data.native_capture.passes.depth` as a bounded base64 render-target payload.
Its metadata includes:

- source and sampled dimensions;
- raster format and pixel stride;
- source and packed row pitches;
- native byte order and renderer-native row order;
- exact byte count and base64 length.

The depth values are the native, usually non-linear hardware depth buffer. Do
not treat them as world-unit distances. Row zero retains renderer-native order,
so depth is experimental and is not yet guaranteed pixel-correlated with the
color image. Do not reconstruct positions until the active backend's row origin
has been validated; only then use the returned projection matrix and clip
planes. The payload is capped at 4,194,304 characters and 1024 pixels on its
largest sampled dimension.

## Ground pixels to Maya nodes

`maya.viewport.scene_map` projects conservative world-space bounding boxes and
pivots into the emitted image coordinate system.

~~~json
{
  "name": "maya.viewport.scene_map",
  "arguments": {
    "width": 1280,
    "height": 720,
    "node_types": ["mesh", "joint", "nurbsCurve"],
    "max_nodes": 250,
    "max_candidates": 1000
  }
}
~~~

Every record carries a canonical node reference. Boxes are available in both
top-left and bottom-left pixel coordinates, plus normalized top-left
coordinates. The map does not test occlusion or panel isolate state. Confirm
overlapping or ambiguous objects with `maya.viewport.pick`. Bounds crossing the
near plane may include only projectable corners; DAG instances are not separate
records; and capture plus scene-map are separate snapshots.

Maya does not expose a stable object-ID render target through the active VP2
capture path used here. Requests for the native object-ID pass return
`UNSUPPORTED_PASS`; they never return guessed identities.

## Create a rig preview

~~~json
{
  "name": "maya.rig.preview",
  "arguments": {
    "action": "create",
    "name": "Left Arm",
    "joints": [
      {
        "id": "shoulder",
        "name": "L_shoulder_JNT",
        "position": [2.1, 12.4, 0.2],
        "radius": 0.6
      },
      {
        "id": "elbow",
        "name": "L_elbow_JNT",
        "position": [5.8, 10.7, -0.1],
        "parent_id": "shoulder"
      },
      {
        "id": "wrist",
        "name": "L_wrist_JNT",
        "position": [8.7, 9.9, 0.0],
        "parent_id": "elbow"
      }
    ],
    "controls": [
      {
        "id": "wristControl",
        "name": "L_wrist_CTRL",
        "offset_name": "L_wrist_ZERO",
        "target_joint_id": "wrist",
        "shape": "circle",
        "size": 1.2,
        "color": 18,
        "constraint": "orient",
        "maintain_offset": false
      }
    ]
  }
}
~~~

The response contains a handle:

~~~json
{
  "preview_id": "rig-preview:0123456789abcdef0123456789abcdef",
  "scene_epoch": "0123456789abcdef0123456789abcdef",
  "revision": 1
}
~~~

Keep the whole handle. An update returns a new revision; older revisions are
rejected with `PREVIEW_REVISION_CONFLICT`. Handles from another scene are
rejected with `SCENE_EPOCH_MISMATCH`.

Preview nodes use a reference display layer, carry UUID-backed ownership tags,
and are marked `doNotWrite`. Creation uses direct Maya API modifiers without
globally disabling undo or forcibly resetting dirty state. Normal preview work
does not enter the undo queue, change selection, dirty the scene, or serialize
into Maya files; real edits performed by synchronous third-party callbacks are
left visible.

Query and list return the full canonical `spec` used by acceptance, not only
marker summaries. Previews have no timer expiry. They persist until cancel,
scene new/open, or plug-in unload, with hard limits of 16 active previews and
8,192 total owned nodes.

## Revise and accept

Update only the scalar fields that changed. Supplying `joints` or `controls`
replaces that entire collection:

~~~json
{
  "name": "maya.rig.preview",
  "arguments": {
    "action": "update",
    "handle": {
      "preview_id": "rig-preview:0123456789abcdef0123456789abcdef",
      "scene_epoch": "0123456789abcdef0123456789abcdef",
      "revision": 1
    },
    "joint_color": [0.2, 1.0, 0.3]
  }
}
~~~

Accept the latest handle. Include `if_scene_revision` when the plan depends on
an earlier scene read:

~~~json
{
  "name": "maya.rig.preview",
  "arguments": {
    "action": "accept",
    "handle": {
      "preview_id": "rig-preview:0123456789abcdef0123456789abcdef",
      "scene_epoch": "0123456789abcdef0123456789abcdef",
      "revision": 2
    },
    "if_scene_revision": 42
  }
}
~~~

Acceptance preflights every output name and target before creating the first
permanent node. A conflict leaves the preview active and creates nothing.
Successful acceptance creates the joints, controls, offset groups, and optional
constraints in one named undo chunk, with names resolved from Maya's root
namespace exactly as preflighted. Acceptance returns `UNDO_DISABLED` before
mutation if Maya undo is off. A normal undo chunk provides best-effort rollback;
native `MPxCommand` transaction semantics are planned for a later slice.

If the permanent rig commits but preview cleanup unexpectedly fails, the call
still reports success with `PREVIEW_CLEANUP_FAILED`. The handle remains
available for cleanup with `action: "cancel"`; a second accept is rejected.

## Validate the real viewport

After a Release build, run:

~~~powershell
.\scripts\test-viewport-interactive.ps1
~~~

The launcher starts one isolated Maya process and writes captures, native depth
evidence, and a JSON result below `build\viewport-validation\`. It does not
attach to or terminate pre-existing Maya sessions.
