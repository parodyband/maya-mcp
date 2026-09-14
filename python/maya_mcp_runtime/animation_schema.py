"""Animation operation schemas; intentionally independent of Maya imports."""
from __future__ import annotations


def definitions(tool, obj, node, vector):
    text = {"type": "string", "minLength": 1, "maxLength": 256}
    handle = {"type": "string", "minLength": 1, "maxLength": 128}
    frame_range = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
    strings = {"type": "array", "items": text, "maxItems": 32}
    mapping = obj({
        "id": {**text, "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,63}$"},
        "node": node, "control": node, "offset": vector, "parent": text,
        "mirror": text, "channels": strings, "description": {"type": "string", "maxLength": 2000},
        "reach_root": text, "max_reach": {"type": "number", "exclusiveMinimum": 0},
    }, ["id", "node"])
    mappings = {"type": "array", "items": mapping, "minItems": 1, "maxItems": 48}
    contact = obj({
        "landmark": text, "time_range": frame_range, "support": node,
        "plane_point": vector, "plane_normal": vector,
    }, ["landmark", "time_range"])
    note = obj({
        "text": {"type": "string", "minLength": 1, "maxLength": 4000},
        "time_range": frame_range, "landmark": text,
        "kind": {"type": "string", "enum": ["intent", "reference", "beat", "feedback", "protect"]},
        "reference": {"type": "string", "maxLength": 2048},
        "reference_offset_seconds": {"type": "number"},
        "reference_fps": {"type": "number", "exclusiveMinimum": 0},
        "image_xy": {"type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1}, "minItems": 2, "maxItems": 2},
    }, ["text"])
    sampling = {
        "profile_id": handle, "landmarks": mappings,
        "time_range": frame_range,
        "frames": {"type": "array", "items": {"type": "number"}, "minItems": 1, "maxItems": 240},
        "step": {"type": "number", "minimum": 0.01, "maximum": 1000, "default": 1},
        "evaluation": {"type": "string", "enum": ["direct", "sequential"], "default": "direct"},
        "preroll": {"type": "number", "minimum": 0, "maximum": 1000, "default": 0},
        "contacts": {"type": "array", "items": contact, "maxItems": 24},
        "label": {"type": "string", "maxLength": 256},
        "notes": {"type": "array", "items": note, "maxItems": 100},
        "max_seconds": {"type": "number", "minimum": 1, "maximum": 60, "default": 20},
    }
    key = obj({
        "time": {"type": "number"}, "value": {"type": "number"},
        "in_tangent": text, "out_tangent": text,
        "in_angle": {"type": "number"}, "out_angle": {"type": "number"},
        "in_weight": {"type": "number", "minimum": 0}, "out_weight": {"type": "number", "minimum": 0},
        "breakdown": {"type": "boolean"}, "locked": {"type": "boolean"},
        "weight_locked": {"type": "boolean"},
    }, ["time"])
    edit = obj({
        "node": node, "attribute": text,
        "keys": {"type": "array", "items": key, "minItems": 1, "maxItems": 2000},
        "weighted": {"type": "boolean"},
    }, ["node", "attribute"])
    return [
        tool("maya.animation.profile", "Map Animation Controls",
             "Create, retrieve, list or delete an owner- and scene-bound explicit rig map. Roles reference evaluated landmarks and editable controls; names are never guessed.",
             obj({"action": {"type": "string", "enum": ["create", "get", "export", "list", "delete"]},
                  "profile_id": handle, "label": text, "mappings": mappings}, ["action"]), read_only=False),
        tool("maya.animation.describe", "Inspect Animation Curves",
             "Inspect rig controls, channel editability, curve tangents/infinity and animation layers. Bounded per-curve key pages; inspect evaluated motion with sample.",
             obj({"profile_id": handle, "targets": {"type": "array", "items": node, "minItems": 1, "maxItems": 48},
                  "attributes": strings, "time_range": frame_range,
                  "key_offset": {"type": "integer", "minimum": 0, "maximum": 100000},
                  "key_limit": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 64}}, []), read_only=True),
        tool("maya.animation.sample", "Sample Evaluated Motion",
             "Sample explicit landmarks across up to 240 frames, with world orientations, derived velocities, and declared surface-relative contacts. Retains an immutable artifact; restores time and playback. Use a tracked workflow for request recovery.",
             obj(sampling), read_only=True),
        tool("maya.animation.capture", "Capture Animation Review",
             "Capture aligned motion and images from explicit cameras. Returns a labeled sheet, artifact handle, and local HTML player. Optional trails and pose ghosts are drawn into images without creating Maya nodes. Retrieve individual frames with animation.artifact.",
             obj({**sampling,
                  "cameras": {"type": "array", "items": node, "minItems": 1, "maxItems": 3},
                  "width": {"type": "integer", "minimum": 128, "maximum": 1280, "default": 640},
                  "height": {"type": "integer", "minimum": 128, "maximum": 1280, "default": 360},
                  "image_frames": {"type": "array", "items": {"type": "number"}, "minItems": 1, "maxItems": 240},
                  "sheet_frames": {"type": "integer", "minimum": 1, "maximum": 12, "default": 12},
                  "trails": {"type": "boolean", "default": False},
                  "ghosts": {"type": "integer", "minimum": 0, "maximum": 3, "default": 0},
                  "clean_view": {"type": "boolean", "default": True}}), read_only=True),
        tool("maya.animation.artifact", "Read Animation Evidence",
             "List, page, annotate or release this client's retained animation evidence. Frame reads return MCP images. Notes support acting beats, reference/audio offsets and normalized image annotations. Artifacts expire after one hour or scene replacement.",
             obj({"action": {"type": "string", "enum": ["list", "get", "frames", "notes", "release"]},
                  "artifact_id": handle, "offset": {"type": "integer", "minimum": 0},
                  "limit": {"type": "integer", "minimum": 1, "maximum": 48, "default": 12},
                  "indices": {"type": "array", "items": {"type": "integer", "minimum": 0}, "minItems": 1, "maxItems": 4},
                  "view": {"type": "string", "enum": ["annotated", "clean"], "default": "annotated"},
                  "notes": {"type": "array", "items": note, "minItems": 1, "maxItems": 100}}, ["action"]), read_only=False),
        tool("maya.animation.analyze", "Diagnose Motion",
             "Analyze retained evidence for declared contact drift/penetration, sampled positional/orientation jumps and loop discontinuities. Returns measured facts with frame intervals; thresholds are in recorded units. Blocking disables pop heuristics. No universal quality score.",
             obj({"artifact_id": handle, "stage": {"type": "string", "enum": ["blocking", "polish"], "default": "polish"},
                  "slip_tolerance": {"type": "number", "minimum": 0, "default": 0.5},
                  "penetration_tolerance": {"type": "number", "minimum": 0, "default": 0.1},
                  "jump_distance": {"type": "number", "exclusiveMinimum": 0, "default": 10},
                  "jump_degrees": {"type": "number", "exclusiveMinimum": 0, "maximum": 180, "default": 90},
                  "loop": {"type": "boolean", "default": False}, "root_motion": {"type": "boolean", "default": False},
                  "root_landmark": text,
                  "loop_orientation_tolerance": {"type": "number", "minimum": 0, "maximum": 180, "default": 1},
                  "loop_position_tolerance": {"type": "number", "minimum": 0, "default": 0.1},
                  "loop_velocity_tolerance": {"type": "number", "minimum": 0, "default": 1}}, ["artifact_id"]), read_only=True),
        tool("maya.animation.edit", "Edit Animation Channels",
             "Preflight and apply distinct keys per channel, tangent edits, scoped delete or retime in one undo chunk. Supports explicit existing layers or a new candidate layer. Refuses locked/driven/ambiguous channels and retime collisions. Preserves protected ranges.",
             obj({"action": {"type": "string", "enum": ["set_keys", "tangents", "delete_keys", "retime"]},
                  "edits": {"type": "array", "items": edit, "minItems": 1, "maxItems": 96},
                  "layer": text, "new_layer": text, "time_range": frame_range,
                  "time_scale": {"type": "number", "exclusiveMinimum": 0, "maximum": 100},
                  "time_offset": {"type": "number"},
                  "protected_ranges": {"type": "array", "items": frame_range, "maxItems": 24},
                  "if_scene_revision": {"type": "integer", "minimum": 0}}, ["action", "edits"]), read_only=False),
        tool("maya.animation.compare", "Compare Animation Takes",
             "Compare two retained takes without re-evaluation. Requires identical landmark identities, units and time basis. Reports motion deltas and camera-compatible paired images; optional explicit time offset supports beat alignment.",
             obj({"before": handle, "after": handle, "frame_offset": {"type": "number", "default": 0},
                  "landmarks": strings, "image_pairs": {"type": "integer", "minimum": 0, "maximum": 4, "default": 2}}, ["before", "after"]), read_only=True),
        tool("maya.animation.layer", "Manage Animation Candidates",
             "Inspect or change mute, solo and weight on an explicit animation layer, with optional frame-specific weight keys. All scene edits are undoable.",
             obj({"layer": text, "mute": {"type": "boolean"}, "solo": {"type": "boolean"},
                  "weight": {"type": "number", "minimum": 0, "maximum": 1},
                  "frame": {"type": "number"}}, ["layer"]), read_only=False),
    ]
