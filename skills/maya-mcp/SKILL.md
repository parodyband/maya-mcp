---
name: maya-mcp
description: Operates Maya MCP correctly and efficiently using scoped observation, schema discovery, batched workflows, persistent Python sessions, verified edits, and request recovery. Use when inspecting or editing a live Maya scene, including modeling, rigging, animation, and materials; not for developing the MCP repository.
metadata:
  version: "1.1.0"
---

# Work efficiently in Maya

Use the connected Maya MCP server and the user's existing task authorization.
The host may prefix or normalize tool names; discover its callable names rather
than assuming the names below are literal host identifiers. Inside workflow
steps and SDK calls, use the canonical `maya.*` operation names.

## Pick the smallest sufficient operation

| Need | Preferred route |
|---|---|
| See the scene and relevant facts | One scoped `maya.observe`; use `image:false` for facts alone. |
| Learn an unfamiliar operation | `maya.tools.describe` for that operation; reuse its schema during the session. |
| Execute a known sequence | One `maya.workflow.run`, with `$ref` dependencies and `select` to limit output. |
| Make graph edits with local rollback | A `maya.node.apply` step; a whole workflow is not atomic. |
| Repeat custom calculations | A persistent Python session with structured arguments and retained result handles. |
| Inspect an action across time | `maya.animation.capture` or `sample`; retrieve selected evidence rather than one call per frame. |
| Recover a slow or interrupted submission | `maya.request.status` with the original request ID and MCP session. |

The compact tool list is a discovery subset, not a capability limit. Specialist
operations remain callable through workflows. Batch steps whose inputs are
already known; pause the batch where a new observation must inform the next edit.

## Choose the shortest useful loop

- Start visual work with `maya.observe`, scoped to relevant nodes. It combines
  an image, node facts, context, and scene mapping. Use `image:false` when only
  structural facts matter. Avoid enumerating the whole scene for a local edit.
- Load missing operation schemas with `maya.tools.describe`. The running
  server's schemas are authoritative. If the server lacks these agent-loop
  tools, use its advertised tools and report the capability mismatch; do not
  invent arguments or repeatedly retry unsupported operations.
- Batch a known sequence in `maya.workflow.run`. Pass earlier results using
  `{"$ref":"stepId#/data/field"}` and project only useful step outputs with
  `select`. Fetch exact schemas before constructing unfamiliar operations.
- Include `observe` in the workflow or script arguments to get fresh evidence
  after edits. It can reference nodes created by an earlier workflow step.
  Inspect the returned image and relevant attributes before the next decision.
- Use `if_observation` with a returned observation ID when the edit depends on
  that observed state. On a stale/expired response, observe and reconsider the
  edit. This guard detects scoped changes; it does not freeze Maya or detect
  every component, parenting, deformer, or playback change.
- Prefer canonical node references returned by Maya. Ambiguous short names
  are errors. Frame targets with `maya.viewport.frame` when needed; it preserves
  selection. Depth is optional and should serve a concrete grounding need.

## Review and edit animation across time

- For character posing, timing, body mechanics, acting, or polish, use the bundled
  `maya-animation-principles` companion when available. It supplies artistic
  decision criteria; the running operation schemas remain authoritative.
- Discover `maya.animation.*` schemas. Use `profile` to map explicit evaluated
  landmarks to controls; use `describe` for channel limits, drivers, tangents,
  layers, and editability. Do not infer rig semantics from names alone.
- Use `capture` for a frame sequence with aligned world-space samples. Review
  its overview, then call `maya.animation.artifact` with `action:"frames"` for
  selected images or `action:"get"` for sample pages.
  Keep cameras and framing stable. Use additional authored cameras for ambiguity.
- Declare heel/toe/hand contacts and moving supports. `analyze` returns measured
  candidates; a large pose step may be an intentional accent. Sample between keys.
- Use `edit` for distinct keys per channel, scoped retiming, and tangent changes.
  Layered channels require an explicit destination; `new_layer` creates a
  candidate with a declared range. Protected key ranges do not freeze poses.
- Capture after editing and use `compare` with matching times and cameras.
  Attach acting beats and reference offsets using `maya.animation.artifact`
  with `action:"notes"`. Notes are
  guidance, not an automatic quality score or enforced pose constraint.
- Inspect `complete` and `missing_frames`: capture may stop at its time budget.
  Use shorter ranges or sparse `image_frames` with dense numeric sampling.
  Sequential evaluation requires validated simulation/cache assumptions.
- Retained artifacts are scoped to a client and scene, expire after an hour,
  and disappear on scene replacement or unload. Release unused takes; copy the
  local review directory before release when persistent evidence is needed.

## Reuse Python for general work

For repeated algorithms, loops, or functionality beyond typed operations, open
`maya.session` with `{"action":"open"}`. Pass its `session_id` to
`maya.script.execute` with `language:"python"`. Helpers and variables persist
in that namespace. Pass input data in `arguments`; assign JSON output to `result`.
The session response describes the SDK:

- `maya.call(name, args)` runs a typed operation and returns its data.
- `maya.observe(**kwargs)` returns facts and emits viewport images.
- `maya.node(selector)` creates a checked handle; use `.name` or `.selector`
  after renames instead of keeping a stale path string.
- `maya.keep(value)`, `maya.get(handle)`, and `maya.release(handle)` retain bounded
  JSON results without sending all intermediate data back to the model.

Use the current cell's `maya` object; do not retain SDK objects between cells.
Sessions expire and reset on scene replacement. Rebuild helpers when a session
is lost. Cells have bounded SDK calls and output; split large work at meaningful
checkpoints. Python remains full-privilege code under the existing execution
gate. If disabled, use supported typed operations or explain what is unavailable.

## Recover outcomes without repeating edits

For long workflows or scripts, set a unique `request_id` for the logical action.
Tracked submissions acknowledge immediately. Query `maya.request.status` until
terminal, allowing time between polls. A queued or running acknowledgement is
not evidence that the edit completed.

Repeat an identical submission with the same ID only to recover that action;
changed arguments need a new ID. IDs and cached outcomes belong to one live MCP
session. After session loss or server restart, inspect scene state before
resubmitting an action whose outcome is unknown. Closing a session does not
cancel work already queued.

Status query success is separate from execution success. Inspect the recovered
`data.result.isError` and `data.result.structuredContent.ok`. Recovered images
may be at the outer content array; follow the returned image indices.
Workflows stop at failure and can retain completed edits. Inspect completed,
failed, and skipped steps before deciding what remains. A post-observation
failure does not undo a successful edit or justify repeating it.

## Keep observations small

Use `maya.scene.changes` with the observation's cursor between decisions when
an image is unnecessary. Continue with `next_cursor` while paginating. If
`requires_observation` is true, refresh the relevant observation. The journal
has bounded retention and watches selected nodes, not the complete evaluated
scene. Use scene-query cursors for large result sets and refresh stale queries.

Images live in MCP image content; structured facts live in `structuredContent`.
Forward native images through the host's image-display mechanism when necessary.
Respect truncation markers, coherence warnings, and per-operation undo reports.
Scale verification to the user's task rather than capturing after every command.
Use output selection, pages, or retained handles when a result is truncated; do
not interpret omitted data as an empty scene. Request depth only for a specific
grounding question and keep its binary payload out of normal text output.
