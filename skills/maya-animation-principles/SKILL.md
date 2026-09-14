---
name: maya-animation-principles
description: Applies classical animation principles to character body mechanics, acting, timing, spacing, posing, and polish in Maya. Use when creating, revising, or critiquing character animation through Maya MCP. Does not apply to developing the MCP repository or building a rig without animation work.
metadata:
  version: "1.0.0"
---

# Animate with intention and readable mechanics

Translate the user's performance goal into visible choices. Respect the chosen
style, existing poses, shot camera, frame rate, contacts, and approved timing.
Scale the work to the request: a local arc correction does not require rebuilding
the shot or running a twelve-principle audit.

This is an original operational guide using the classical framework associated
with Frank Thomas and Ollie Johnston's *The Illusion of Life*, and the timing,
spacing, and movement emphasis of Richard Williams' *The Animator's Survival Kit*.
It contains no book pages, illustrations, quotations, or chapter reconstruction.
See [sources and scope](references/sources.md) for attribution and further study.

## Choose the right change

1. State the action or thought the audience should read: for example, a character
   reaches confidently, notices danger, and withdraws without wanting to show fear.
   Infer reasonable details from the shot; clarify only decisions that materially
   change the performance. Keep reference delivery and the user's direction primary.
2. Inspect the current animation, controls, shot camera, frame range, and FPS.
   Identify the relevant story poses, contact changes, accents, and holds. For new
   action, establish these before adding fine motion. Retain approved work.
3. Choose the most consequential issue and the principle that explains it.
   Distinguish timing (when a beat occurs) from spacing (positions between beats).
   If arrival times already work, refine spacing without automatically retiming.
4. Make a scoped candidate, review the evaluated result across frames, and compare
   it with the previous version. Fix readability and mechanics before incidental
   detail. Stop when the requested change reads clearly and introduces no observed
   regressions; avoid endless smoothing or unsolicited variations.

## Twelve principles as decisions

| Principle | Decision to make |
|---|---|
| Squash and stretch | Convey force through deformation; preserve perceived volume and rig integrity. |
| Anticipation | Prepare attention and effort at the scale the action needs. |
| Staging | Give each beat a clear focus in the shot camera. |
| Straight ahead / pose to pose | Anchor story and contacts; explore connective motion where discovery helps. |
| Follow through / overlap | Let connected parts respond with motivated differences in timing. |
| Slow in / slow out | Shape spacing around accents, arrivals, departures, and holds. |
| Arcs | Make tracked body-part paths support the gesture. |
| Secondary action | Reinforce the main idea without competing for attention. |
| Timing | Choose durations and rhythm for intent, effort, and material. |
| Exaggeration | Strengthen the intended idea to suit the selected style. |
| Solid drawing | In 3D, check form, weight, silhouette, and deformation. |
| Appeal | Make personality and pose design engaging and readable. |

Use these as related choices, not twelve effects to apply to every shot.
Preserve sharp impacts, deliberate stillness, stepped animation, and stylized
violations of physical motion when they serve the brief.

## Use the evidence Maya can provide

- Discover the running `maya.animation.*` schemas. The companion `maya-mcp` skill
  covers operation, identity, undo, and request-recovery details when available.
- Use `profile` and `describe` to connect evaluated landmarks to editable controls.
  Inspect IK/FK, space switches, layers, and driven channels before choosing edits.
- Use `capture` for a readable sequence and `sample` for motion measurements.
  Read the overview, then request `artifact` with `action:"frames"` around contacts,
  fast changes, breakdowns, or facial beats. A movie path alone is not observation.
- Keep the shot camera as the primary judgment view. Add a diagnostic camera or
  larger hand/face image when needed. Inspect clean frames as well as overlays.
- Declare contact intervals and supports before interpreting slip diagnostics.
  Treat `analyze` findings as measured review candidates. Intentional impacts and
  blocking can legitimately trigger numerical thresholds.
- Use `edit` or a candidate layer for changes and `compare` under matching review
  conditions. Protected key ranges do not freeze interpolated contacts; re-sample.
- Record useful acting beats with `artifact` and `action:"notes"`. Report a
  concrete frame interval, visible problem, proposed adjustment, and verification.
  Separate observed evidence from interpretation or unobserved assumptions.

For walks, runs, jumps, landings, lifting, contact, arcs, or curve cleanup, read
[body mechanics and spacing](references/body-mechanics.md).
For acting, gaze, dialogue, gesture, holds, staging, and performance polish, read
[acting and shot review](references/acting-and-review.md).
Load the reference relevant to the task; ordinary local fixes rarely need both.
