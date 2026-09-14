"""Preflighted, undoable animation edits with explicit curve/layer ownership."""
from __future__ import annotations

import re

import maya.api.OpenMaya as om
import maya.cmds as cmds

from . import state

TANGENTS = {"auto", "autocustom", "autoease", "automix", "clamped", "flat", "linear", "plateau", "spline", "step", "stepnext", "fixed"}
TANGENT_FLAGS = {"in_tangent": "inTangentType", "out_tangent": "outTangentType",
                 "in_angle": "inAngle", "out_angle": "outAngle", "in_weight": "inWeight", "out_weight": "outWeight",
                 "locked": "lock", "weight_locked": "weightLock"}


def _layer(name):
    node = state.resolve_node(name)
    if cmds.nodeType(node) != "animLayer":
        raise state.ToolError("INVALID_TARGET", "Expected an animation layer")
    return node


def edit(arguments, call):
    from .tools_animation import _curve_records, _key_indices, _range
    state.require_revision(arguments.get("if_scene_revision"))
    if not cmds.undoInfo(query=True, state=True):
        raise state.ToolError("UNDO_DISABLED", "Animation edits require Maya undo")
    action = arguments["action"]
    if action != "retime" and any(key in arguments for key in ("time_scale", "time_offset")):
        raise state.ToolError("INVALID_ARGUMENT", "time_scale and time_offset apply only to retime")
    target_layer, new_layer = arguments.get("layer"), arguments.get("new_layer")
    if target_layer and new_layer:
        raise state.ToolError("INVALID_ARGUMENT", "Choose an existing layer or a new layer")
    if target_layer:
        target_layer = _layer(target_layer)
        if cmds.animLayer(target_layer, query=True, lock=True):
            raise state.ToolError("LOCKED_TARGET", "Destination animation layer is locked")
    time_range = _range(arguments.get("time_range"))
    protected = [_range(value) for value in arguments.get("protected_ranges", [])]
    if action in {"delete_keys", "retime"} and time_range is None:
        raise state.ToolError("INVALID_ARGUMENT", f"{action} requires time_range")
    if new_layer:
        if action != "set_keys" or time_range is None or time_range[0] == time_range[1]:
            raise state.ToolError("INVALID_ARGUMENT", "A candidate layer requires set_keys and a nonzero time_range")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", new_layer) or cmds.objExists(new_layer):
            raise state.ToolError("INVALID_ARGUMENT", "new_layer must be a new, unnamespaced Maya identifier")
        if any(a <= time_range[1] and b >= time_range[0] for a, b in protected):
            raise state.ToolError("PROTECTED_RANGE", "Candidate layer overlaps a protected range")
    plans, seen, work = [], set(), 0
    for item in arguments["edits"]:
        node = state.resolve_node(item["node"])
        plug = node+"."+item["attribute"]
        if not cmds.objExists(plug):
            raise state.ToolError("INVALID_TARGET", f"Missing channel: {plug}")
        selection = om.MSelectionList()
        selection.add(plug)
        canonical = selection.getPlug(0).name()
        if canonical in seen:
            raise state.ToolError("INVALID_ARGUMENT", "Each channel can appear only once in an edit")
        seen.add(canonical)
        if cmds.getAttr(plug, lock=True) or (cmds.lockNode(node, query=True, lock=True) or [False])[0]:
            raise state.ToolError("LOCKED_TARGET", f"Channel is locked: {plug}")
        if cmds.getAttr(plug, type=True) not in {"double", "doubleLinear", "doubleAngle", "float", "long", "short", "byte", "bool", "enum"}:
            raise state.ToolError("INVALID_TARGET", f"Expected a scalar animation channel: {plug}")
        incoming = cmds.listConnections(plug, source=True, destination=False) or []
        if any(not (cmds.nodeType(source).startswith("animCurveT") or cmds.nodeType(source).startswith("animBlendNode")) for source in incoming):
            raise state.ToolError("DRIVEN_CHANNEL", f"Refusing to key a constraint, expression, or driven-key output: {plug}")
        if any(cmds.nodeType(source).startswith("animBlendNode") for source in incoming) and not (target_layer or new_layer):
            raise state.ToolError("AMBIGUOUS_LAYER", "Specify layer when editing a layered channel")
        curves = _curve_records(plug)
        curve = None if new_layer else next((c for c, layer in curves if layer == target_layer), None)
        if not target_layer and not new_layer:
            if len(curves) > 1:
                raise state.ToolError("AMBIGUOUS_CURVE", "Channel has multiple animation sources")
            curve = curves[0][0] if curves else None
        if curve and not cmds.nodeType(curve).startswith("animCurveT"):
            raise state.ToolError("INVALID_TARGET", "Only time-based animation curves are editable")
        if action != "set_keys" and not curve:
            raise state.ToolError("ANIMATION_NOT_FOUND", f"No curve on the requested layer for {plug}")
        if action in {"set_keys", "tangents"} and not item.get("keys"):
            raise state.ToolError("INVALID_ARGUMENT", f"{action} requires keys for every channel")
        if action in {"delete_keys", "retime"} and item.get("keys"):
            raise state.ToolError("INVALID_ARGUMENT", f"{action} uses time_range, not a keys list")
        if action in {"delete_keys", "retime"} and "weighted" in item:
            raise state.ToolError("INVALID_ARGUMENT", "weighted applies to key or tangent edits")
        keys = item.get("keys", [])
        weighted = item.get("weighted", bool((cmds.keyTangent(curve, query=True, weightedTangents=True) or [False])[0]) if curve else False)
        if not weighted and any("in_weight" in key or "out_weight" in key for key in keys):
            raise state.ToolError("INVALID_ARGUMENT", "Tangent weight edits require a weighted curve or weighted:true")
        key_times = [key["time"] for key in keys]
        if len(set(key_times)) != len(key_times):
            raise state.ToolError("INVALID_ARGUMENT", "Duplicate key times are not allowed")
        for key in keys:
            if action == "set_keys" and "value" not in key:
                raise state.ToolError("INVALID_ARGUMENT", "set_keys requires an explicit value for each key")
            if action == "tangents" and ("value" in key or "breakdown" in key or not any(k in key for k in TANGENT_FLAGS)):
                raise state.ToolError("INVALID_ARGUMENT", "tangents requires tangent properties and cannot change key values")
            if time_range and not time_range[0] <= key["time"] <= time_range[1]:
                raise state.ToolError("INVALID_ARGUMENT", "Key time is outside time_range")
            for field in ("in_tangent", "out_tangent"):
                if field in key and (key[field] not in TANGENTS or (field == "in_tangent" and key[field] in {"step", "stepnext"})):
                    raise state.ToolError("INVALID_ARGUMENT", f"Unsupported {field}: {key[field]}")
            if action == "tangents" and not cmds.keyframe(curve, query=True, time=(key["time"], key["time"]), keyframeCount=True):
                raise state.ToolError("ANIMATION_NOT_FOUND", "Tangent edits require existing keys")
        all_times = []
        if curve:
            _key_indices(curve)
            all_times = cmds.keyframe(curve, query=True, timeChange=True) or []
        selected = [t for t in all_times if time_range[0] <= t <= time_range[1]] if action in {"delete_keys", "retime"} else key_times
        destination = list(selected)
        if action == "retime":
            scale, offset = arguments.get("time_scale", 1), arguments.get("time_offset", 0)
            destination = [time_range[0]+(t-time_range[0])*scale+offset for t in selected]
            outside = set(all_times)-set(selected)
            if any(abs(a-b) < 1e-7 for a in destination for b in outside):
                raise state.ToolError("KEY_COLLISION", "Retiming would overwrite a key outside the source range")
            # Preserve curve ordering relative to untouched keys, not just exact times.
            if selected and any(min(selected[0], destination[0]) < t < max(selected[-1], destination[-1]) for t in outside):
                raise state.ToolError("KEY_COLLISION", "Retiming would cross an untouched key")
        if any(a <= t <= b for a,b in protected for t in selected+destination):
            raise state.ToolError("PROTECTED_RANGE", "Edit would change a protected key time")
        if "weighted" in item and protected:
            raise state.ToolError("PROTECTED_RANGE", "Curve-wide weighting changes cannot be combined with protected ranges")
        work += max(len(selected), len(keys))
        if work > 10000:
            raise state.ToolError("WORK_LIMIT_EXCEEDED", "Edit at most 10,000 keys per request")
        plans.append({"plug": plug, "curve": curve, "item": item, "selected": selected, "destination": destination})
    with state.undo_chunk(call, "Edit animation channels"):
        if new_layer:
            target_layer = cmds.animLayer(new_layer, override=True)
            state.mark_mutated(call)
            # Candidate influence is gated to the requested range at frame boundaries.
            for frame, value in ((time_range[0]-1, 0), (time_range[0], 1), (time_range[1], 1), (time_range[1]+1, 0)):
                cmds.setKeyframe(target_layer+".weight", time=frame, value=value, outTangentType="step")
        for plan in plans:
            plug, curve, item = plan["plug"], plan["curve"], plan["item"]
            if action == "set_keys":
                if target_layer:
                    cmds.animLayer(target_layer, edit=True, attribute=plug)
                    state.mark_mutated(call)
                for key in item["keys"]:
                    kwargs = {"time": key["time"], "value": key["value"], "insertBlend": False,
                              "breakdown": key.get("breakdown", False), "noResolve": True}
                    if target_layer:
                        kwargs["animLayer"] = target_layer
                    changed = cmds.setKeyframe(plug, **kwargs)
                    if not changed:
                        raise state.ToolError("ANIMATION_EDIT_FAILED", f"Maya did not key {plug}")
                    state.mark_mutated(call)
                resolved = [c for c, owner in _curve_records(plug) if owner == target_layer] if target_layer else (cmds.keyframe(plug, query=True, name=True) or [])
                if len(resolved) != 1:
                    raise state.ToolError("ANIMATION_EDIT_FAILED", "Maya did not produce the requested curve")
                curve = resolved[0]
            if action in {"set_keys", "tangents"}:
                if "weighted" in item:
                    cmds.keyTangent(curve, edit=True, weightedTangents=item["weighted"])
                    state.mark_mutated(call)
                for key in item["keys"]:
                    fields = {TANGENT_FLAGS[k]: v for k,v in key.items() if k in TANGENT_FLAGS}
                    if fields:
                        cmds.keyTangent(curve, edit=True, time=(key["time"], key["time"]), **fields)
                        state.mark_mutated(call)
            elif action == "delete_keys":
                if plan["selected"]:
                    cmds.cutKey(curve, time=tuple(time_range), clear=True, animation="objects")
                    state.mark_mutated(call)
            elif plan["selected"]:
                old, new = plan["selected"], plan["destination"]
                if len(old) == 1:
                    cmds.keyframe(curve, edit=True, time=(old[0], old[0]), timeChange=new[0], absolute=True)
                else:
                    cmds.scaleKey(curve, time=(old[0], old[-1]), newStartTime=new[0], newEndTime=new[-1], scaleSpecifiedKeys=True, autoSnap=False, animation="objects")
                state.mark_mutated(call)
        call.changes.append({"kind": "animation.edit", "action": action, "plugs": [p["plug"] for p in plans], "layer": target_layer})
    if call.mutation_started:
        state.bump_scene_revision()
    return state.result(call, {"action": action, "channels": len(plans), "keys": work, "layer": target_layer,
                               "value_space": "authored_curve", "protected_scope": "authored key times; interpolation is not frozen"},
                        f"Applied {action} to {len(plans)} animation channels")


def layer(arguments, call):
    node = _layer(arguments["layer"])
    edits = {key: arguments[key] for key in ("mute", "solo", "weight") if key in arguments}
    if "frame" in arguments and "weight" not in arguments:
        raise state.ToolError("INVALID_ARGUMENT", "frame requires weight")
    if edits:
        if not cmds.undoInfo(query=True, state=True):
            raise state.ToolError("UNDO_DISABLED", "Animation layer edits require Maya undo")
        if cmds.animLayer(node, query=True, lock=True):
            raise state.ToolError("LOCKED_TARGET", "Animation layer is locked")
        with state.undo_chunk(call, "Adjust animation candidate"):
            for key, value in edits.items():
                if key == "weight" and "frame" in arguments:
                    cmds.setKeyframe(node+".weight", time=arguments["frame"], value=value)
                else:
                    cmds.animLayer(node, edit=True, **{key: value})
                state.mark_mutated(call)
            call.changes.append({"kind": "animation.layer", "layer": node, **edits})
        state.bump_scene_revision()
    return state.result(call, {"layer": node, **{key: cmds.animLayer(node, query=True, **{key: True}) for key in ("mute", "solo", "weight", "lock")}}, "Inspected animation candidate")
