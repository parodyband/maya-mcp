# Animation review and editing

The development tree adds nine animation operations. They are available through
`maya.tools.describe` and `maya.workflow.run`; the compact profile still exposes
eight entry points. The original `maya.animation.apply` remains compatible.

MCP client setup also installs `maya-animation-principles`. It turns the core
ideas associated with *The Illusion of Life* and *The Animator's Survival Kit*
into practical posing, timing, mechanics, acting, and review decisions. Invoke
it explicitly or let it apply to relevant animation work. Its references and
source notes ship alongside the entrypoint.

| Operation | Purpose |
|---|---|
| `maya.animation.profile` | Create, inspect, export, list, or delete explicit landmark/control mappings. |
| `maya.animation.describe` | Inspect channels, limits, drivers, tangents, infinity settings, and layer-specific curves. |
| `maya.animation.sample` | Retain evaluated world positions, orientations, velocities, accelerations, and declared contacts. |
| `maya.animation.capture` | Collect aligned images and motion, a labeled overview, optional trails and landmark ghosts, and a local HTML player. |
| `maya.animation.artifact` | Page samples, retrieve clean or annotated images, attach notes, list takes, or release evidence. |
| `maya.animation.analyze` | Locate contact drift, penetration, pose steps, explicit reach-limit violations, and loop discontinuities. |
| `maya.animation.edit` | Write distinct keys per channel, edit tangents, delete a range, or retime keys with preflight and undo. |
| `maya.animation.compare` | Compare retained takes at matching times or an explicit frame offset. |
| `maya.animation.layer` | Inspect or adjust candidate-layer mute, solo, and weight. |

Use the running server's schemas as the authority. Rebuild and install the
runtime, then restart Maya and the MCP client to activate new operations. The
source additions do not update an already running Maya process automatically.

For a first review, map the actual controls and choose the shot camera. The
following workflow is a template: replace its Maya names before running it.
Offsets and reach limits use the scene's linear unit. `node` is the evaluated
landmark; `control` identifies where to edit when those nodes differ.

```json
{
  "request_id": "landing-review-001",
  "steps": [
    {
      "id": "rig",
      "tool": "maya.animation.profile",
      "arguments": {
        "action": "create",
        "label": "Character body",
        "mappings": [
          {"id": "pelvis", "node": "pelvis_JNT", "control": "pelvis_CTRL"},
          {"id": "chest", "node": "chest_JNT", "control": "chest_CTRL", "parent": "pelvis"},
          {"id": "left_heel", "node": "leftFoot_JNT", "control": "leftFoot_IK_CTRL"}
        ]
      },
      "select": {"profile_id": "/data/profile_id"}
    },
    {
      "id": "review",
      "tool": "maya.animation.capture",
      "arguments": {
        "profile_id": {"$ref": "rig#/data/profile_id"},
        "time_range": [1, 48],
        "cameras": ["shotCamera"],
        "width": 640,
        "height": 360,
        "trails": true,
        "ghosts": 1,
        "label": "Landing baseline",
        "contacts": [
          {"landmark": "left_heel", "time_range": [24, 36]}
        ],
        "notes": [
          {"kind": "intent", "text": "A heavy landing, followed by a hesitant look up."},
          {"kind": "beat", "time_range": [36, 42], "text": "Hold before the eyes lead the head."}
        ]
      }
    }
  ]
}
```

Workflow references resolve against the original structured step result, even
when `select` reduces the visible result. Recover a tracked workflow using
`maya.request.status` in the same MCP session. Inspect the inner execution result.

The capture result includes an `artifact_id`, sample/image counts, overview
correspondence, units, FPS, missing frames, and a `review_path`. Open the local
HTML player to scrub, step, loop, switch cameras, change speed, toggle clean
images, and jump to acting notes. The images and `manifest.json` are beside it.
Use the artifact's image indices to request larger MCP images:

```json
{"action":"frames","artifact_id":"REPLACE_WITH_TAKE_HANDLE","indices":[12,13],"view":"clean"}
```

`artifact.get` pages numeric samples with `offset` and `limit`, returning
`next_offset`. Its image manifest maps each image index to a frame and camera.
The overview is a selection of captured images; it is not every frame.
Curve descriptions distinguish time inputs from unitless driven-key inputs;
time ranges do not filter the latter. Breakdown flags are returned as booleans.
`image_frames` can select a sparse visual subset while numeric sampling remains
dense. Include breakdowns and between-key frames when inspecting interpolation.

Contact definitions use a landmark, interval, and optional moving support node.
`plane_point` and `plane_normal` describe a plane in support space, or world space
without a support. The default plane passes through the origin with the scene's
up axis as its normal. Sliding is measured relative to the first sampled contact
position transformed with the moving support. Each result reports its actual
`anchor_frame`. Heel and toe mappings can distinguish foot roll from slipping.

`analyze` thresholds use the artifact's recorded linear units. Position-step
thresholds are displacement between samples, not speed. `stage:"blocking"`
disables pose-step heuristics. Loop checks compare position, orientation, and
velocity; `root_motion:true` requires a `root_landmark` and removes that root's
translation difference from position comparisons. It does not remove turning.
A mapping with both `reach_root` and `max_reach` enables an explicit reach check.
These are review candidates with evidence, not automatic judgments of acting.

For editing, specify each channel separately. Values are authored curve values,
including when targeting an animation layer. This example creates an override
candidate affecting its named channels during frames 24–36:

```json
{
  "action": "set_keys",
  "new_layer": "LandingCandidate",
  "time_range": [24, 36],
  "edits": [
    {
      "node": "pelvis_CTRL",
      "attribute": "translateY",
      "keys": [
        {"time": 24, "value": 8, "out_tangent": "auto"},
        {"time": 28, "value": 5, "in_tangent": "auto", "out_tangent": "auto"},
        {"time": 36, "value": 8, "in_tangent": "auto"}
      ]
    }
  ]
}
```

Candidate-layer weight uses stepped boundary keys, returning to zero at the next
frame after the range. Existing layered channels require an explicit `layer`.
Locked targets, driven outputs, missing channels, and malformed edits fail
preflight. All supported scene edits require undo and use one undo chunk.

`retime` maps each selected time to
`range_start + (time - range_start) * time_scale + time_offset`. It refuses
collisions and crossings with untouched keys. `protected_ranges` protects authored
key times, including retime destinations; it does not freeze interpolated poses.
Re-sample contacts after editing. Tangent changes can affect neighboring segments.

Capture the candidate with the same frames, cameras, dimensions, and settings.
`compare` returns positional/orientation deltas and compatible paired images.
Camera matrix or capture-setting differences suppress misleading visual pairs.
An explicit `frame_offset` aligns an after-take frame to a before-take frame for
beat comparisons. Unmatched frames and incomplete sources are reported.

Attach notes with `artifact.notes`. A note can include a frame range, landmark,
kind (`intent`, `reference`, `beat`, `feedback`, or `protect`), reference path/URL,
and normalized `image_xy` coordinates measured from the image's top left. Protect
notes are guidance; only an edit's `protected_ranges` enforces key protection.
Image notes are shown on every selected view at those normalized coordinates.
Reference audio/video uses browser-supported formats. Its offset means
`source_seconds = scene_seconds - reference_offset_seconds`. Media is not
downloaded, transcribed, or automatically analyzed by this runtime.

Profiles retain canonical identities through renames. Their offsets and reach
limits retain physical size when the scene's linear unit changes. `profile.export`
returns portable `create_arguments` with current names and a separate linear-unit
field; recreate it in that unit after checking names in the destination scene.
Mirroring metadata records role relationships; it does not infer mirror axes or
perform automatic rig-specific IK/FK/space matching.

Reviews are bounded to 48 landmarks, 240 sampled times, 240 images across all
cameras, and 12,000 landmark samples. Curve inspection returns at most 2,000 keys
across 96 channels per request; use key offsets and smaller channel scopes.
The store holds at most 32 records and 128 MiB, with a 64 MiB per-record limit.
Handles belong to one client and scene, expire one hour after creation, and are
released on scene replacement or plugin unload. Copy an entire review directory
before releasing its artifact if you want to retain it outside Maya.

Sampling restores time, auto-key, and playback. Capture also restores its camera
and changed display flags. It does not change selection or create overlay nodes.
`clean_view` hides helper drawings when the focused panel is a model panel.
Landmark ghosts are projected points and parent links, not ghosted mesh geometry;
they do not resolve occlusion. Camera framing remains as authored.

`evaluation:"sequential"` advances through intervening frames, with optional
preroll. This does not guarantee reproducibility or restore the internal state of
arbitrary simulations. Use validated caches for stateful work. The default direct
mode is appropriate for ordinary keyed rigs. Image capture requires interactive
Maya; sampling and analysis work in standalone Maya.

`max_seconds` stops collection between frames and returns explicit partial
evidence. It cannot interrupt a Maya command already executing. Capture/sample
timings exclude native queue wait and transport. The scene is not frozen against
arbitrary callback-driven changes. Ground planes are proxies; full mesh collision,
physical balance, universal rig matching, and automated performance scoring are
outside these operations' claims.

Run `scripts/test-plugin.ps1` for the standalone regression suite, including
`tests/animation_test.py`. The interactive viewport harness also exercises native
HTTP animation capture, two camera views, clean/annotated images, notes, frame
retrieval, comparisons, and cleanup. Both launchers accept `-PackageRoot` for an
isolated development package. The current implementation was validated with
Maya 2027; the 2026 runtime requires its own compatibility run.
