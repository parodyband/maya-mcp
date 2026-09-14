# Maya MCP 0.7.0

Maya MCP 0.7.0 adds animation review across frames, precise animation editing,
and two companion skills that install for existing customers during updates.

## Animation tools

- Capture aligned images and evaluated motion from up to three cameras, with
  labeled frame sheets, clean images, motion trails, landmark ghosts, and a local
  review player.
- Map rig landmarks to controls, inspect animation curves and layers, and sample
  world-space positions, orientations, velocities, and declared contacts.
- Locate contact drift, penetration, pose steps, explicit reach-limit violations,
  and loop discontinuities with frame-linked evidence.
- Edit distinct keys per channel, adjust tangents, retime ranges, and compare
  candidate layers with undo and collision preflight.
- Retain takes, retrieve selected frames, and attach acting notes or reference
  audio/video offsets.

The complete registry now contains 34 operations. Compact discovery still uses
eight entry points. See [the animation guide](ANIMATION.md) for usage and limits.

## Skills included in installation and updates

- **Maya MCP Operations** (`maya-mcp`): efficient tool selection, observation,
  batching, Python sessions, verification, undo, and recovery.
- **Maya Animation Principles** (`maya-animation-principles`): an original
  practical guide to the twelve principles, timing, spacing, body mechanics,
  acting, and polish, with attribution to *The Illusion of Life* and
  *The Animator's Survival Kit*.

The installer copies complete skill folders, including references and UI metadata,
for Codex and Claude Code. Existing managed copies migrate to per-file receipts.
Customers with an existing Maya MCP registration also receive the skills when
upgrading from installations without skill receipts. Startup synchronization
covers older updaters that did not run the skill-installation step.

Customized or unowned skill files are preserved. Missing managed files are
repaired, and unrelated client configuration is retained. Unconfigured clients
are not enrolled by background updates. Set `MAYA_MCP_DISABLE_SKILL_SYNC=1` to
disable automatic skill synchronization; `-SkipSkills` skips the installer step.

## Updating

In Maya, choose **Maya MCP > Check for Updates > Install Update**, then close and
reopen Maya. Restart Codex or Claude Code so it discovers the skills. The update
stages beside the existing installation and preserves the current scene.

Windows ZIPs are supplied for the exact Maya 2026.3 API (`20260300`) and Maya 2027.1
API (`20270100`). Claude Desktop uses the separate MCP Bundle for its connection;
the companion skill installer targets Codex and Claude Code.

## Validation and limits

Both API targets build and package. Runtime and GPU validation use Maya 2027 and
an NVIDIA RTX 4070 Ti SUPER. Maya 2026.3 runtime behavior still needs validation on
a matching installation. Release checks cover client setup, extracted and
direct-from-ZIP installation, and the published 0.6.1 updater installing the new
skills for managed, previously skill-less, and customized customer installations.

Animation evidence is bounded and reports incomplete captures. Contact checks use
declared planes and landmark proxies; diagnostics are review candidates rather
than an artistic quality score. Landmark ghosts are not full-mesh ghosts.
Arbitrary simulations are not guaranteed to reproduce or restore internal state.
Browser interaction testing of the local player was blocked by local-file browser
policy; JavaScript syntax and real captured images were checked.
