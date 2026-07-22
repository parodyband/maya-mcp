# Roadmap and Known Limits

Version 0.5 is a complete development slice. It is useful now, but it is not
the claimed end state.

## Verified in 0.5

- Maya 2026.3 and Maya 2027.1 SDK Visual Studio 2022 Release builds
- MCP 2025-11-25 initialization and authenticated HTTP transport
- Plug-in start, status, stop, unload, and discovery cleanup
- Tools, resources, and prompts discovery
- Main-thread tool dispatch from concurrent HTTP workers
- Strict schema rejection
- Typed transform creation and editing
- Canonical node-ID round trips
- Geometry creation
- Three-joint creation and orientation
- Control creation
- Custom control shapes, RP/SC/spline IK handles, pole vectors, parent/orient/
  point/scale/aim constraints, rig attributes, utility connections, and driven keys
- One-click, per-session Python/MEL approval from the Maya MCP menu
- Exact-API Maya 2026.3 and 2027 release builds with a verified, restart-safe updater
- Skin binding and inspection
- Transactional cleanup
- Bounded native VP2 depth command and strict batch/error contracts
- Conservative viewport scene maps with canonical node references
- Non-serializing rig preview create/update/query/list/accept/cancel lifecycle
- Preflighted rig acceptance in one undo chunk, undo-disabled refusal, and
  cleanup-failure honesty

The automated gate loads the absolute packaged plug-in and packaged Python
runtime through mayapy, requires version 0.5 and all 18 tools, then unloads the
plug-in cleanly.

## Interactive viewport validation

mayapy has no 3D viewport. Run the isolated interactive harness after a Release
build:

~~~powershell
.\scripts\test-viewport-interactive.ps1 `
    -MayaLocation 'C:\Program Files\Autodesk\Maya2027' `
    -TimeoutSeconds 240
~~~

The launcher starts a separate Maya process with isolated preferences and MCP
discovery data. A unique launch guard must match before the test resets a scene
or closes Maya. On timeout, the launcher terminates only the child process it
started; it never searches for Maya processes by name or ID. Evidence is written
to `build/viewport-validation/TIMESTAMP-PID-RUN_ID/`.

The harness verifies PNG/JPEG capture, perspective and orthographic cameras,
pixel dimensions, device-pixel ratio evidence, joint scaling, shaded/wireframe/
component picking, selection preservation, projection, normalized rays,
playback, isolate select, timer dispatch, authenticated transport, and unload.
The default v0.5 harness requires `mayaMcpVp2Capture`, scene-map and rig-preview
tools, direct native VP2 depth, bounded base64 metadata, the MCP
`include_depth` path, and the explicit `UNSUPPORTED_PASS` response for object-ID
capture. A stale or legacy package fails rather than silently skipping the new
slice.

Production sign-off still requires:

1. Windows display scaling at 100%, 150%, and 200%.
2. NVIDIA, AMD, and supported integrated GPU configurations.
3. Studio custom render overrides and color-management configurations.
4. Transaction rollback while work is dispatched by Maya's timer callback.
5. Native depth validation across supported GPU backends and scene complexity.
6. Object-ID validation after a stable identity render target is exposed.

## Next — production visual grounding and scene truth

- Production-validate native Viewport 2.0 depth across supported GPU backends
- Lossless object-ID pass with canonical node legend
- Normal, wireframe, selected-only, joint-axis, and skin-weight passes
- Native non-scene rig overlays and labels
- Multi-view landmark triangulation and mesh-surface grounding
- Native MDGMessage, MModelMessage, MUiMessage, and UFE observers
- Exact scene and context revisions with coalesced event records
- Resource subscriptions and cursor-based event polling
- Maya USD and UFE selection, hierarchy, transforms, and attributes

## 0.4 — transactions and production operations

- Native MPxCommand transactions with explicit undoIt and redoIt
- Plan, preview, commit, and expiring plan tokens
- Idempotency keys and full JSON Pointer references between transaction steps
- Rig recipes for limbs, spines, digits, necks, eyes, and tails
- Sparse skin-weight resources and editing
- Geometry, material, animation, deformer, and rig diagnostics
- Project extension manifests with registered schema-described routines
- File-root allowlists and fine-grained authorization scopes
- Append-only audit log with redaction

## 0.5 — long work and compatibility

- SSE streams and server-to-client notifications
- Progress and cooperative cancellation
- MCP task support where stable clients implement it
- Job compatibility tools for clients without tasks
- Optional stdio adapter using a restricted local IPC channel
- Multi-Maya instance broker and explicit instance selection
- Official MCP conformance runner in CI
- Interactive viewport harness in CI on a licensed GPU runner

## 1.0 criteria

- No known plug-in unload or callback lifetime hazards
- Exact protocol conformance for every advertised capability
- Native viewport color, depth, and object-ID capture
- UFE and Maya USD coverage
- Reliable native undo for typed scene mutations
- Permission scopes and file-root policy
- Complete audit and diagnostics
- Stress tests for large scenes, concurrent clients, cancellation, and shutdown
- Versioned API compatibility policy

## Current limitations

- Streamable HTTP returns direct JSON only; GET SSE is 405.
- Cancellation notifications are accepted but do not interrupt active Maya work.
- Revisions use a scene-signature fallback for out-of-band user edits.
- Normal typed mutations use undo chunks, not native undo command objects.
- Rig-preview accept requires undo to be enabled; previews do not expire on a
  timer and remain until cancel, new/open, or unload under fixed count/node caps.
- Arbitrary scripts cannot be force-stopped or sandboxed.
- File tools are not yet restricted to configured roots.
- One token grants every enabled tool.
- Discovery tracks the most recently started instance in current.json.
- Color images still use Maya's compatibility color-buffer API. Optional depth
  is bounded raw Viewport 2.0 render-target data with explicit format metadata.
- Object-ID, visual diff, events, UFE, and USD are planned. Ghost rig previews
  currently use transient `doNotWrite` DAG nodes and still need broad studio
  validation before a native non-scene overlay replaces them.
