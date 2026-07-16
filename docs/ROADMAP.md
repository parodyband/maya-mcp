# Roadmap and Known Limits

Version 0.2 is the first complete vertical slice. It is useful now, but it is
not the claimed end state.

## Verified in 0.2

- Maya 2027.1 SDK and Visual Studio 2022 Release build
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
- Skin binding and inspection
- Transactional cleanup

The automated test runs through mayapy and unloads the plug-in cleanly.

## Interactive test still required

mayapy has no 3D viewport. Before calling viewport capture production-ready,
run an interactive Maya test that verifies:

1. PNG and JPEG capture at native and resized resolutions.
2. Perspective and orthographic cameras.
3. HiDPI coordinate mapping.
4. Pixel picking in shaded, wireframe, and component modes.
5. Selection preservation.
6. Camera matrices and joint projection alignment.
7. Capture while playback, isolate select, and custom render overrides are active.
8. Transaction rollback while work is dispatched by Maya's timer callback.

## 0.3 — visual grounding and scene truth

- Native Viewport 2.0 color and depth render targets
- Lossless object-ID pass with canonical node legend
- Normal, wireframe, selected-only, joint-axis, and skin-weight passes
- Temporary non-scene rig overlays and labels
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
- Interactive Maya test harness

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
- Arbitrary scripts cannot be force-stopped or sandboxed.
- File tools are not yet restricted to configured roots.
- One token grants every enabled tool.
- Discovery tracks the most recently started instance in current.json.
- Viewport capture currently uses Maya's compatibility color-buffer API.
- Depth, segmentation, visual diff, ghost previews, events, UFE, and USD are planned.
