# Architecture review for faster agent work

Reviewed September 5, 2026. Scope included the Python catalog and handlers,
native HTTP and stdio adapters, main-thread dispatch, identity and undo policies,
viewport capture, packaging, and test harnesses.

## Main finding

The existing native/Python split earns its keep. C++ owns concurrency and plug-in
lifetime, while Python provides broad Maya access. Replacing it wholesale would
put tested lifetime invariants at risk without evidence of a corresponding gain.

The strongest immediate opportunity is a deeper workflow module: fewer client
round trips, less initial schema text, and less intermediate data returned to
the model. Detailed typed operations remain useful implementation behind that
smaller interface. Arbitrary Python remains valuable for general Maya work.

This follows the on-demand discovery and local composition patterns described
in [Anthropic's MCP code-execution analysis](https://www.anthropic.com/engineering/code-execution-with-mcp).
The implementation uses bounded declarative steps, while retaining the existing
Python escape hatch. It does not introduce another script language or sandbox.
Structured results and annotations follow the project's pinned
[MCP tools revision](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).

## Implemented in this development tree

| Change | Agent benefit | Implementation and evidence |
|---|---|---|
| Compact discovery plus exact schema lookup | Load specialist schemas only when needed | `catalog.py`, `tools_workflow.py`; native compact HTTP test |
| Ordered workflows with cross-operation references | Create, edit, and verify in one request | `dispatcher.invoke_tool`, `tools_workflow.py`; real Maya mutation, partial-failure and undo tests |
| Selected outputs and bounded retained results | Keep intermediate scene data out of model context | Workflow output projections, truncation markers and limits |
| Query cursors | Traverse results beyond the first page | `tools_core.scene_query`; ordering, continuation and stale-membership tests |
| JSON arguments for Python | Pass data without fragile source interpolation | `tools_domain.script_execute`; quoted/newline data round trip |
| Explicit native undo scope | Avoid undoing unrelated work by counting workflow steps as history entries | Native HTTP tests for grouped undo and a failing step's local rollback |
| Negotiated stdio protocol headers | Older supported clients work after initialization | `stdio_bridge.cpp`; both older protocol revisions tested |
| Matching response budgets and preserved errors | Avoid false transport failures for completed work | Bridge tests for a 9 MiB result and structured HTTP 400 errors |
| Combined observation and post-action feedback | See the result of edits without another inspection round trip | Real GPU image, framing, selection and projection checks |
| Persistent Python namespaces and SDK | Reuse helpers and node handles across cells | Real Maya persistence, ownership, stale-handle and lifecycle checks |
| Scoped change journal | Poll changes and avoid repeated node-count scans | Callback, cursor-gap, undo/redo and scene-reset tests |
| Tracked requests and native status | Recover outcomes without repeating edits | Queued/running duplicate delivery, replay, capacity and session-isolation tests |

The Maya 2027 test measured **17,870 bytes** of compact tool definitions
versus **66,271 bytes** in the complete 25-operation catalog, using compact JSON
serialization. That is approximately a **73% reduction**. These are schema bytes, not token
counts or an end-to-end task-speed benchmark. Small description edits may change
the exact count; `workflow_test.py` prints current measurements.

The tested create → transform → inspect example crosses MCP once instead of
three times after the needed schemas are known. It still performs three Maya
operations. There is no parallel Maya execution or claim that command execution
itself becomes three times faster.

## What was removed from the default interface

Seventeen specialized schemas are absent from initial discovery. Their handlers,
tests, and full-profile direct calls remain available. This avoids substituting
fragile handwritten scripts for working rig, file, material, and geometry tools.
The workflow module centralizes composition at one execution seam; it does not
duplicate those implementations.

Default inspection prompts no longer require depth readback and a whole-scene
summary for every visual question. They request relevant schemas and compose
the necessary observations. Depth is requested when it helps the actual goal.

## Next priorities

1. **Request recovery beyond one live session.** Tracked workflows and scripts
   now have queued/running/completed/failed states and bounded replay. Recovery
   does not survive MCP session closure or server restart. Queued expiry,
   cancellation and persistent recovery remain future work. Active arbitrary
   Maya commands remain unkillable.

2. **Broader scene observation.** Scoped callbacks and a bounded change cursor
   now detect watched attribute/name edits and maintain dependency-node counts
   without a normal-path enumeration. Parenting, components, downstream
   evaluation, references and UFE need broader coverage. Do not build an
   authoritative scene cache on today's approximate revision.

3. **Responsive transport control.** Tracked submissions return immediately and
   status lookup avoids Maya dispatch. Four untracked calls can still occupy all
   HTTP workers. The stdio adapter also processes requests serially. Separate control-message handling
   from waiting for execution while retaining FIFO Maya mutation order. Test
   ping/cancel under load and shutdown during queued work.

4. **End-to-end measurements.** Existing `timing_ms` excludes queue wait,
   signature synchronization, and transport. Measure those phases, response
   bytes, serialization, and whole workflows across scene sizes. Optimize the
   expensive phase demonstrated by evidence before replacing base64 transport
   or the existing 10 ms timer/Qt pumps.

5. **Large data and general scene coverage.** Mesh topology, sparse skin
   weights, USD/UFE hierarchy, and animation curves need bounded resource handles
   and targeted reads. Extend the operation registry by capability without
   growing the default tool profile. Add registered project routines only when
   repeated real workflows justify their lifecycle and versioning complexity.

6. **Independent transport tests.** CMake still requires Maya SDK files before
   declaring the standalone bridge target. An SDK-independent target would make
   protocol tests cheap to run in CI. Extend fake-server tests for restart,
   multi-instance discovery, cancellation, malformed responses, and concurrency.

## Validation and adoption

`scripts/test-plugin.ps1` covers compact workflow discovery over real native HTTP
and runs existing integration checks using the full profile.
`scripts/test-viewport-interactive.ps1` verifies real GPU image capture composed
with scene mapping, plus the existing viewport and rig-preview gates.
`scripts/test-bridge.ps1` exercises the stdio adapter and stable launcher.

Both Maya 2026.3 and 2027 Release packages built successfully. The full standalone
suite passed in Maya 2027. The GPU gate passed on an NVIDIA RTX 4070 Ti SUPER,
including plug-in unload; its isolated Maya process required the harness's
existing cleanup after the normal exit grace period. Maya 2026 runtime behavior
and other GPU configurations were not tested in this review.

Changes are built into the local development package. They do not alter a
currently loaded Maya process or publish a release. Install the development
package using the existing setup flow, restart Maya, then reconnect the client.
Direct specialist-tool clients can set `MAYA_MCP_TOOL_PROFILE=full` before Maya
starts. This development change should be versioned explicitly before release
because it changes the default discovery interface.
