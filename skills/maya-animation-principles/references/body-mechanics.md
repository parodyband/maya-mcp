# Body mechanics and spacing

These are practical Maya review heuristics. Adapt them to the character's rig,
anatomy, reference, and style. They are not fixed frame recipes.

## Diagnose timing separately from spacing

Compute a duration as `(end_frame - start_frame) / fps`; the number of sampled
images includes endpoints and is not the number of elapsed frame intervals.
Choose beat durations in seconds or relative rhythm before converting to frames.

When the departure and arrival beats work, inspect intermediate positions and
world-space paths. Adjust breakdowns or tangents to change spacing. When an
accent itself comes too early or late, adjust timing and inspect adjacent beats.
Preserve contacts, audio alignment, and protected poses during either change.

Closely spaced positions imply slower translation; increasingly separated
positions imply acceleration at a fixed sampling interval. Angular movement
also matters: a quiet wrist position can hide a large hand rotation. Evaluate the
rig's final movement instead of treating a smooth control curve as proof.

## Contact and weight

Establish which part supports or drives the body at each phase. Track heel, toe,
palm, grip, or another actual contact marker. An ankle pivot alone cannot explain
foot roll. Declare the support object so moving-platform motion is not mislabeled
as slipping. Check both the visible contact and its sampled displacement.

Convey heaviness through preparation, force, support, compression, and recovery.
Compare the torso's path with the supporting limbs. A pelvis marker is a useful
cue, not a measured center of mass. Dynamic actions can move beyond static support;
avoid imposing static balance tests on running, falling, or airborne poses.

Use the rig's intended controls for stretch and compression. Inspect silhouettes
and deformed meshes for collapsing volume or unintended joint scaling. Stylized
stretch may be deliberate; protect established proportions where the brief needs it.

## Locomotion

For a walk, locate contacts, load acceptance, passing, and release from the actual
reference. Relate pelvis travel to the support transition. Check foot clearance
and heel/toe behavior. Avoid copying a universal cycle length or mirroring every
channel: character proportions, speed, asymmetry, and attitude change the action.

For a run, establish the supported and airborne phases appropriate to the gait.
Check takeoff direction, recovery of the legs, and the next contact. Arms and
torso should respond to the effort; uniform sinusoidal offsets often obscure it.

For a loop, inspect pose, orientation, and velocity across the seam. Declare the
root-motion convention. A repeated endpoint used for comparison should not create
an unintended extra held frame in playback. Check the seam at final playback speed.

## Jumps, landings, and lifting

For a jump, make preparation, push, flight, and landing distinguishable. Trace the
body trajectory and feet relative to the ground. Use reference or the chosen
style to set the flight profile; easing every phase identically can weaken takeoff
and impact. Inspect the frames immediately around support loss and return.

For a landing, establish contact order, compression, rebound if appropriate, and
settle. Recheck contacts while the pelvis and torso continue moving. Offset limbs
and extremities for a motivated response without turning every joint into a spring.

For lifting or pushing, establish the grip, resistance, preparation, and force
transfer. Check the prop against the hands during the full interaction. A hand
following a prop after visible separation can look unconnected even when both
individual trajectories are smooth.

## Curves, arcs, and overlap

Track the point that communicates the action: fingertip, nose, heel, or prop edge
may reveal an arc that the control origin misses. Inspect both position and
orientation from the shot camera, with another view if depth is ambiguous.

Choose a lead and response appropriate to the action. Inspect proximal and distal
parts together. Offset or reshape connected movements where necessary; moving all
curves by the same delay does not produce differentiated overlap.

Before spline polish, confirm breakdowns and contact poses. After interpolation,
inspect between-key frames for overshoot, unintended reversals, knee/elbow changes,
or contact drift. Check layer contributions and switch behavior before editing
the apparent curve. Avoid adding dense keys merely to conceal a rig-space problem.

For each correction, preserve a baseline, change a limited set of controls, and
re-capture the affected interval with some surrounding motion. Review at normal
speed for rhythm and in individual frames for mechanics. Use measurements to
locate issues, with visual and reference judgment determining the intended result.
