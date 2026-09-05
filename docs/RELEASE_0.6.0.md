# Maya MCP 0.6.0

This release reduces agent round trips by combining scene observation, batched
edits, and visual verification, with a companion skill for Codex and Claude Code.

## Changes

- Eight default tools with specialist schemas available on demand; set
  `MAYA_MCP_TOOL_PROFILE=full` before launching Maya for all 25 direct tools.
- Ordered workflows with result references, selected outputs, and explicit
  partial-failure reports.
- Combined viewport and scene observation, stale-observation guards, framing
  without selection changes, and post-action images.
- Persistent Python sessions with checked node handles and bounded JSON results.
- Scoped change cursors and callback-maintained node counts.
- Session-scoped request IDs, immediate acknowledgements, and result recovery
  without repeating completed edits.
- Companion-skill installation, managed updates, and repair for Codex and Claude
  Code. Local skill edits are preserved; `-SkipSkills` allows connection-only setup.
- Scene-query pagination, structured script arguments, negotiated bridge protocol
  headers, aligned response limits, and preserved transport errors.

## Upgrade

Use **Maya MCP > Check for Updates** or download the ZIP matching Maya 2026.3 or
2027. Restart Maya after installation, then reconnect the AI client. Existing
users can run **Configure AI Clients** to install the new companion skill.
Claude Desktop uses the separate MCP Bundle; it does not receive a Claude Code
skill through the Windows installer.

Workflows can retain edits completed before a failure. Request recovery lasts
only within the same live MCP session and does not cancel queued work. Scene
change coverage is scoped rather than a complete evaluated-scene snapshot.
See [the agent-loop guide](AGENT_LOOP.md) for examples and exact limits.

## Validation

Release builds target Maya 2026.3 and 2027. Runtime integration and GPU validation
use Maya 2027 on an NVIDIA RTX 4070 Ti SUPER. Maya 2026 runtime and other GPUs
remain untested. Gates cover workflows, observation, sessions, request recovery,
undo, rig operations, previews, bridge behavior, client configuration, and both
release installer paths.
