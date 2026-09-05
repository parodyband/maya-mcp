---
name: maya-mcp
description: Inspect and edit a live Autodesk Maya scene through Maya MCP, including modeling, rigging, animation, materials, and viewport verification. Use for operating Maya; not for ordinary development of the MCP repository.
metadata:
  version: "1.0.0"
---

# Work efficiently in Maya

Use the connected Maya MCP server and the user's existing task authorization.
The host may prefix or normalize tool names; discover its callable names rather
than assuming the names below are literal host identifiers. Inside workflow
steps and SDK calls, use the canonical `maya.*` operation names.

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
