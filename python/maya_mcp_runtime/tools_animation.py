"""Evaluated animation review, explicit rig semantics, and reversible editing."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import copy
import json
import math
from pathlib import Path
import shutil
import tempfile
import time

import maya.api.OpenMaya as om
import maya.cmds as cmds

from . import animation_store as store, state
from .animation_math import angle, derivatives, diagnose, distance


def _range(value):
    if value is not None and value[0] > value[1]:
        raise state.ToolError("INVALID_ARGUMENT", "Time ranges must be ascending")
    return value


def _resolve_mappings(mappings):
    result, ids = [], set()
    for mapping in mappings:
        role = mapping["id"]
        if role in ids:
            raise state.ToolError("INVALID_ARGUMENT", "Landmark IDs must be unique")
        ids.add(role)
        node = state.resolve_node(mapping["node"])
        if not cmds.objectType(node, isAType="transform"):
            raise state.ToolError("INVALID_TARGET", "Landmarks must refer to transform or joint nodes")
        control = state.resolve_node(mapping.get("control", mapping["node"]))
        result.append({**mapping, "node": state.node_ref(node), "control": state.node_ref(control),
                       "offset": mapping.get("offset", [0, 0, 0]), "provenance": "explicit"})
    for item in result:
        if ("reach_root" in item) != ("max_reach" in item):
            raise state.ToolError("INVALID_ARGUMENT", "reach_root and max_reach must be supplied together")
        for field in ("parent", "mirror", "reach_root"):
            if item.get(field) and (item[field] not in ids or item[field] == item["id"]):
                raise state.ToolError("INVALID_ARGUMENT", f"{field} must name another mapped landmark")
    return result


def _mappings(arguments):
    if "landmarks" in arguments and "profile_id" in arguments:
        raise state.ToolError("INVALID_ARGUMENT", "Choose landmarks or profile_id")
    if "profile_id" in arguments:
        profile = store.get(arguments["profile_id"], "profile")["data"]
        factor = profile["centimeters_per_unit"]/om.MDistance(1, om.MDistance.uiUnit()).asCentimeters()
        definitions = copy.deepcopy(profile["mappings"])
        for item in definitions:
            item.pop("provenance", None)
            item["offset"] = [value*factor for value in item["offset"]]
            if "max_reach" in item:
                item["max_reach"] *= factor
        return _resolve_mappings(definitions)
    if not arguments.get("landmarks"):
        raise state.ToolError("INVALID_ARGUMENT", "Supply explicit landmarks or a rig profile_id")
    return _resolve_mappings(arguments["landmarks"])


def profile(arguments, call):
    action = arguments["action"]
    if action == "create":
        if not arguments.get("mappings"):
            raise state.ToolError("INVALID_ARGUMENT", "create requires mappings")
        data = {"label": arguments.get("label", "Rig profile"), "mappings": _resolve_mappings(arguments["mappings"]),
                "linear_unit": cmds.currentUnit(query=True, linear=True),
                "centimeters_per_unit": om.MDistance(1, om.MDistance.uiUnit()).asCentimeters()}
        token = store.put("profile", data)
        data = {"profile_id": token, **data}
    elif action == "list":
        data = {"profiles": [{"profile_id": item["artifact_id"], **{k:v for k,v in item.items() if k != "artifact_id"}}
                             for item in store.listing("profile")]}
    else:
        token = arguments.get("profile_id", "")
        record = store.get(token, "profile")
        data = {"profile_id": token, **copy.deepcopy(record["data"])}
        if action == "delete":
            store.release(token)
            data = {"profile_id": token, "deleted": True}
        elif action == "export":
            portable = _mappings({"profile_id": token})
            for item in portable:
                item.pop("provenance", None)
                for key in ("node", "control"):
                    item[key] = item[key]["long_name"]
            data = {"linear_unit": cmds.currentUnit(query=True, linear=True), "create_arguments": {
                "action": "create", "label": record["data"]["label"], "mappings": portable}}
    return state.result(call, data, f"Animation profile {action}")


def _layers():
    return [{"name": layer, **{key: cmds.animLayer(layer, query=True, **{key: True})
             for key in ("mute", "solo", "weight", "lock", "override", "parent")}}
            for layer in (cmds.ls(type="animLayer") or [])[:64]]


def _curve_records(plug):
    """Resolve per-layer sources explicitly; do not attribute a blended output to one curve."""
    result = []
    for layer in cmds.ls(type="animLayer") or []:
        curve = cmds.animLayer(layer, query=True, findCurveForPlug=plug)
        if curve:
            result.extend((item, layer) for item in (curve if isinstance(curve, list) else [curve]))
    if not result:
        result = [(curve, None) for curve in (cmds.keyframe(plug, query=True, name=True) or [])]
    return list(dict.fromkeys(result))


def _attribute_limits(node, attribute):
    try:
        return {"minimum": cmds.attributeQuery(attribute, node=node, minimum=True) if cmds.attributeQuery(attribute, node=node, minExists=True) else None,
                "maximum": cmds.attributeQuery(attribute, node=node, maximum=True) if cmds.attributeQuery(attribute, node=node, maxExists=True) else None,
                "default": cmds.attributeQuery(attribute, node=node, listDefault=True)}
    except RuntimeError:
        return {"available": False}


def _key_indices(curve, time_range=None):
    count = cmds.keyframe(curve, query=True, keyframeCount=True) or 0
    if count > 100000:
        raise state.ToolError("WORK_LIMIT_EXCEEDED", "Curve exceeds 100,000 keys", {"curve": curve})
    return cmds.keyframe(curve, query=True, indexValue=True, **({"time": tuple(time_range)} if time_range else {})) or []


def _curve_info(curve, layer, time_range, offset, limit):
    time_based = cmds.nodeType(curve).startswith("animCurveT")
    indices = _key_indices(curve, time_range if time_based else None)
    selected = indices[offset:offset+limit]
    keys = []
    for index in selected:
        def query(command, flag):
            value = command(curve, query=True, index=(index, index), **{flag: True})
            return value[0] if isinstance(value, (list, tuple)) and value else value
        keys.append({"index": index, ("time" if time_based else "input"): query(cmds.keyframe, "timeChange" if time_based else "floatChange"),
                     "value": query(cmds.keyframe, "valueChange"),
                     "breakdown": bool(cmds.keyframe(curve, query=True, index=(index, index), breakdown=True)),
                     **{key: query(cmds.keyTangent, flag) for key, flag in (
                         ("in_tangent", "inTangentType"), ("out_tangent", "outTangentType"),
                         ("in_angle", "inAngle"), ("out_angle", "outAngle"),
                         ("in_weight", "inWeight"), ("out_weight", "outWeight"),
                         ("locked", "lock"), ("weight_locked", "weightLock"))}})
    return {"node": state.node_ref(curve), "layer": layer, "keys": keys,
            "input_domain": "time" if time_based else "unitless", "time_range_applied": time_based and time_range is not None,
            "total_keys_in_range": len(indices), "next_offset": offset+len(keys) if offset+len(keys) < len(indices) else None,
            "weighted": bool((cmds.keyTangent(curve, query=True, weightedTangents=True) or [False])[0]),
            "pre_infinity": cmds.getAttr(curve+".preInfinity"), "post_infinity": cmds.getAttr(curve+".postInfinity")}


def describe(arguments, call):
    _range(arguments.get("time_range"))
    if "profile_id" in arguments and "targets" in arguments:
        raise state.ToolError("INVALID_ARGUMENT", "Choose profile_id or targets")
    mappings = _mappings(arguments) if "profile_id" in arguments else []
    targets = [item["control"] for item in mappings] or arguments.get("targets", [])
    if not targets:
        raise state.ToolError("INVALID_ARGUMENT", "Supply targets or profile_id")
    records, budget = [], 2000
    plugs_seen = set()
    for target in targets:
        node = state.resolve_node(target)
        attributes = arguments.get("attributes") or (cmds.listAttr(node, keyable=True) or [])[:32]
        for attribute in attributes:
            plug = node+"."+attribute
            if plug in plugs_seen:
                continue
            plugs_seen.add(plug)
            if len(plugs_seen) > 96:
                raise state.ToolError("WORK_LIMIT_EXCEEDED", "Inspect at most 96 channels per request")
            if not cmds.objExists(plug):
                raise state.ToolError("INVALID_TARGET", f"Missing channel: {plug}")
            curves = []
            for curve, layer in _curve_records(plug):
                info = _curve_info(curve, layer, arguments.get("time_range"), arguments.get("key_offset", 0), min(budget, arguments.get("key_limit", 64)))
                budget -= len(info["keys"])
                curves.append(info)
            records.append({"node": state.node_ref(node), "attribute": attribute,
                            "value": state.json_safe(cmds.getAttr(plug)), "locked": cmds.getAttr(plug, lock=True),
                            "keyable": cmds.getAttr(plug, keyable=True), "settable": cmds.getAttr(plug, settable=True),
                            "limits": _attribute_limits(node, attribute),
                            "incoming": (cmds.listConnections(plug, source=True, destination=False, plugs=True) or [])[:16],
                            "rotate_order": cmds.getAttr(node+".rotateOrder") if cmds.objExists(node+".rotateOrder") else None,
                            "curves": curves})
    return state.result(call, {"channels": records, "layers": _layers(), "mappings": mappings,
                               "units": {"linear": cmds.currentUnit(query=True, linear=True), "angular": cmds.currentUnit(query=True, angle=True), "time": cmds.currentUnit(query=True, time=True)},
                               "keys_returned": 2000-budget, "key_budget": 2000}, f"Inspected {len(records)} animation channels")


def _frames(arguments):
    if "frames" in arguments and "time_range" in arguments:
        raise state.ToolError("INVALID_ARGUMENT", "Choose explicit frames or time_range")
    if "frames" in arguments:
        frames = sorted(set(arguments["frames"]))
    elif "time_range" in arguments:
        start, end = _range(arguments["time_range"])
        step = arguments.get("step", 1)
        count = int(math.floor((end-start)/step+1e-9))+1
        if count > 240:
            raise state.ToolError("WORK_LIMIT_EXCEEDED", "Sample at most 240 frames per request; split the shot or increase step")
        frames = [start+i*step for i in range(count)]
        if abs(frames[-1]-end) > 1e-7:
            frames.append(end)
    else:
        raise state.ToolError("INVALID_ARGUMENT", "Supply frames or time_range")
    if len(frames) > 240:
        raise state.ToolError("WORK_LIMIT_EXCEEDED", "At most 240 frames are supported")
    return frames


@contextmanager
def _evaluation_context(call, capture=False, clean=True):
    frame = cmds.currentTime(query=True)
    playing = bool(cmds.play(query=True, state=True))
    forward = bool(cmds.play(query=True, forward=True)) if playing else True
    auto = bool(cmds.autoKeyframe(query=True, state=True))
    view, camera, panel, settings = None, None, None, {}
    if capture:
        from .tools_viewport import _active_view
        view = _active_view()
        camera = view.getCamera()
        focus = cmds.getPanel(withFocus=True)
        if focus and cmds.getPanel(typeOf=focus) == "modelPanel":
            panel = focus
        if clean and panel:
            for flag in ("nurbsCurves", "joints", "locators", "grid", "selectionHiliteDisplay"):
                settings[flag] = cmds.modelEditor(panel, query=True, **{flag: True})
    try:
        if playing:
            cmds.play(state=False)
        cmds.autoKeyframe(state=False)
        for flag in settings:
            cmds.modelEditor(panel, edit=True, **{flag: False})
        yield view
    finally:
        errors = []
        actions = [lambda: cmds.currentTime(frame, edit=True, update=True), lambda: cmds.autoKeyframe(state=auto)]
        if view is not None:
            actions.append(lambda: view.setCamera(camera))
        actions.extend(lambda flag=flag, value=value: cmds.modelEditor(panel, edit=True, **{flag: value}) for flag, value in settings.items())
        if playing:
            actions.append(lambda: cmds.play(forward=forward))
        for action in actions:
            try:
                action()
            except Exception as error:
                errors.append(str(error))
        if errors:
            raise state.ToolError("ANIMATION_RESTORE_FAILED", "Could not fully restore Maya review context", {"errors": errors})


def _dag(node):
    selection = om.MSelectionList()
    selection.add(node)
    return selection.getDagPath(0)


def _camera(node):
    name = state.resolve_node(node)
    if cmds.nodeType(name) != "camera":
        shapes = cmds.listRelatives(name, shapes=True, fullPath=True, type="camera") or []
        if len(shapes) != 1:
            raise state.ToolError("INVALID_TARGET", f"Expected an unambiguous camera: {name}")
        name = shapes[0]
    return _dag(name)


def _qt():
    try:
        from PySide6 import QtCore, QtGui
    except ImportError:
        from PySide2 import QtCore, QtGui
    return QtCore, QtGui


def _capture(view, path, width, height):
    from .tools_viewport import _camera_metadata
    cmds.refresh(force=True)
    pixels = om.MImage()
    view.readColorBuffer(pixels, True)
    pixels.writeToFile(str(path), "png")
    core, gui = _qt()
    original = gui.QImage(str(path))
    if original.isNull():
        raise state.ToolError("CAPTURE_FAILED", "Maya returned an unreadable image")
    scaled = original.scaled(width, height, core.Qt.KeepAspectRatio, core.Qt.SmoothTransformation)
    canvas = gui.QImage(width, height+28, gui.QImage.Format_RGB32)
    canvas.fill(gui.QColor("#181c22"))
    painter = gui.QPainter(canvas)
    x, y = (width-scaled.width())//2, (height-scaled.height())//2
    painter.drawImage(x, y, scaled)
    painter.end()
    if not canvas.save(str(path), "PNG"):
        raise state.ToolError("CAPTURE_FAILED", "Could not save captured image")
    return {"camera": _camera_metadata(view), "width": width, "height": height+28,
            "image_rect": [x, y, scaled.width(), scaled.height()],
            "world_to_centimeters": om.MDistance(1, om.MDistance.uiUnit()).asCentimeters()}


def _sample(arguments, call, capture):
    started = time.perf_counter()
    frames = _frames(arguments)
    if arguments.get("preroll", 0) and arguments.get("evaluation", "direct") != "sequential":
        raise state.ToolError("INVALID_ARGUMENT", "preroll requires sequential evaluation")
    mappings = _mappings(arguments)
    if len(frames)*len(mappings) > 12000:
        raise state.ToolError("WORK_LIMIT_EXCEEDED", "Limit frame × landmark samples to 12,000")
    names = {m["id"]: state.resolve_node(m["node"]) for m in mappings}
    contacts = []
    up = cmds.upAxis(query=True, axis=True)
    for i, contact in enumerate(arguments.get("contacts", [])):
        start, end = _range(contact["time_range"])
        if contact["landmark"] not in names or start < frames[0] or end > frames[-1]:
            raise state.ToolError("INVALID_ARGUMENT", "Contacts must reference a mapped landmark and lie inside the sampled range")
        normal = contact.get("plane_normal", [0, 1, 0] if up == "y" else [0, 0, 1])
        if distance(normal, [0]*3) < 1e-10:
            raise state.ToolError("INVALID_ARGUMENT", "Contact plane normal cannot be zero")
        contacts.append({**contact, "id": str(i), "plane_normal": normal,
                         "plane_point": contact.get("plane_point", [0, 0, 0]),
                         "support": state.node_ref(state.resolve_node(contact["support"])) if "support" in contact else None})
    image_frames = set(arguments.get("image_frames", frames)) if capture else set()
    if image_frames-set(frames):
        raise state.ToolError("INVALID_ARGUMENT", "image_frames must be included in sampled frames")
    cameras = [_camera(selector) for selector in arguments.get("cameras", [])] if capture else []
    if capture and len(image_frames)*max(1, len(cameras)) > 240:
        raise state.ToolError("WORK_LIMIT_EXCEEDED", "Capture at most 240 images across all cameras")
    seconds_per_frame = om.MTime(1, om.MTime.uiUnit()).asUnits(om.MTime.kSeconds)
    centimeters_per_unit = om.MDistance(1, om.MDistance.uiUnit()).asCentimeters()
    data = {"label": arguments.get("label", "Animation take"), "scene_epoch": state.scene_epoch(),
            "source_revision": state.scene_revision(), "units": {"linear": cmds.currentUnit(query=True, linear=True),
            "angular": cmds.currentUnit(query=True, angle=True), "time": cmds.currentUnit(query=True, time=True), "up_axis": up},
            "fps": 1/seconds_per_frame, "mappings": mappings, "contacts": contacts,
            "requested_frames": frames, "samples": [], "images": [], "notes": arguments.get("notes", []),
            "evaluation": arguments.get("evaluation", "direct"), "complete": True,
            "derivatives": "Three-point nonuniform position derivative; secant acceleration; one-sided endpoints; no smoothing",
            "coverage": {"snapshot_isolation": False, "contact_geometry": "explicit planes and landmark proxies",
                         "simulation_state_restored": False}, "capture_settings": {key: arguments.get(key, default) for key, default in
                (("width", 640), ("height", 360), ("clean_view", True), ("trails", False), ("ghosts", 0))}}
    directory = tempfile.mkdtemp(prefix="maya-mcp-animation-") if capture else None
    anchors, capture_seconds, sample_seconds = {}, 0.0, 0.0
    try:
        with _evaluation_context(call, capture, arguments.get("clean_view", True)) as view:
            if capture and not cameras:
                cameras = [view.getCamera()]
            previous = frames[0]-arguments.get("preroll", 0)-1
            for frame in frames:
                if time.perf_counter()-started > arguments.get("max_seconds", 20):
                    data["complete"] = False
                    call.warnings.append({"code": "ANIMATION_TIME_LIMIT", "message": "Review stopped between frames; retrieve completed samples and review a shorter range"})
                    break
                if state.scene_epoch() != data["scene_epoch"]:
                    raise state.ToolError("REVISION_CONFLICT", "Scene changed during animation evaluation")
                clock = time.perf_counter()
                if data["evaluation"] == "sequential":
                    if frame-previous > 2000:
                        raise state.ToolError("WORK_LIMIT_EXCEEDED", "Sequential evaluation gap exceeds 2,000 frames")
                    intermediate = previous+1
                    while intermediate < frame-1e-8:
                        cmds.currentTime(intermediate, edit=True, update=True)
                        intermediate += 1
                cmds.currentTime(frame, edit=True, update=True)
                previous = frame
                sample_seconds += time.perf_counter()-clock
                if frame in image_frames:
                    for camera in cameras:
                        clock = time.perf_counter()
                        view.setCamera(camera)
                        path = Path(directory)/f"frame-{len(data['images']):04d}.png"
                        info = _capture(view, path, arguments.get("width", 640), arguments.get("height", 360))
                        data["images"].append({"frame": frame, "seconds": frame*seconds_per_frame, "file": path.name, **info})
                        capture_seconds += time.perf_counter()-clock
                        if sum(p.stat().st_size for p in Path(directory).iterdir()) > 48*1024*1024:
                            raise state.ToolError("ARTIFACT_LIMIT", "Capture exceeds 48 MiB; reduce frames or resolution")
                clock = time.perf_counter()
                points = {}
                for mapping in mappings:
                    matrix = om.MMatrix(cmds.xform(names[mapping["id"]], query=True, worldSpace=True, matrix=True))
                    position = om.MPoint(*(v*centimeters_per_unit for v in mapping["offset"]))*matrix
                    quaternion = om.MTransformationMatrix(matrix).rotation(asQuaternion=True)
                    points[mapping["id"]] = {"position": [v/centimeters_per_unit for v in (position.x, position.y, position.z)],
                                               "quaternion": [quaternion.x, quaternion.y, quaternion.z, quaternion.w]}
                sample = {"frame": frame, "seconds": frame*seconds_per_frame, "points": points, "contacts": {}}
                for contact in contacts:
                    if contact["time_range"][0] <= frame <= contact["time_range"][1]:
                        matrix = om.MMatrix(cmds.xform(state.resolve_node(contact["support"]), query=True, worldSpace=True, matrix=True)) if contact["support"] else om.MMatrix()
                        if abs(matrix.det4x4()) < 1e-12:
                            raise state.ToolError("INVALID_TARGET", "Contact support has a singular world transform")
                        inverse = matrix.inverse()
                        position = om.MPoint(*(v*centimeters_per_unit for v in points[contact["landmark"]]["position"]))
                        anchor = anchors.setdefault(contact["id"], {"position": position*inverse, "frame": frame})
                        expected = anchor["position"]*matrix
                        normal = (om.MVector(*contact["plane_normal"])*inverse.transpose()).normal()
                        plane = om.MPoint(*(v*centimeters_per_unit for v in contact["plane_point"]))*matrix
                        delta = position-expected
                        tangent = delta-normal*(delta*normal)
                        sample["contacts"][contact["id"]] = {"drift": tangent.length()/centimeters_per_unit, "height": ((position-plane)*normal)/centimeters_per_unit,
                                                                  "anchor_frame": anchor["frame"]}
                if abs(cmds.currentTime(query=True)-frame) > 1e-7:
                    raise state.ToolError("ANIMATION_TIME_CHANGED", "A callback changed time during frame evaluation")
                data["samples"].append(sample)
                sample_seconds += time.perf_counter()-clock
        derivatives(data["samples"])
        data["missing_frames"] = frames[len(data["samples"]):]
        data["timing_seconds"] = {"evaluation_and_sampling": sample_seconds, "capture": capture_seconds,
                                    "total": time.perf_counter()-started}
        if not data["samples"]:
            raise state.ToolError("ANIMATION_EMPTY", "No frames completed")
        images = []
        if capture:
            _decorate(data, directory, arguments)
            if data["images"]:
                sheet = Path(directory)/"overview.png"
                overview = _overview(data["images"], arguments.get("sheet_frames", 12))
                _sheet([Path(directory)/item["file"] for item in overview], sheet,
                       labels=[f"Frame {item['frame']:g} | {item['camera']['node']['name'][:23]}" for item in overview])
                data["overview"] = [{"image_index": data["images"].index(item), "frame": item["frame"], "camera": item["camera"]["node"]} for item in overview]
                images = [_image(sheet)]
            data["review_path"] = str(Path(directory)/"review.html")
            _write_player(data, directory)
        token = store.put("take", data, directory)
        data["timing_seconds"]["total"] = time.perf_counter()-started
        response = state.result(call, _summary(token, data), f"Reviewed {len(data['samples'])} frames and {len(mappings)} landmarks", image_content=images)
        if images:
            response["content"] = images
        return response
    except Exception:
        if directory:
            shutil.rmtree(directory)
        raise


def sample(arguments, call):
    return _sample(arguments, call, False)


def capture(arguments, call):
    return _sample(arguments, call, True)


def _image(path):
    encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    if len(encoded) > 5*1024*1024:
        raise state.ToolError("IMAGE_OUTPUT_LIMIT", "Image exceeds the response budget")
    return {"type": "image", "mimeType": "image/png", "data": encoded}


def _overview(items, count):
    if len(items) <= count:
        return items
    return [items[round(i*(len(items)-1)/max(1, count-1))] for i in range(count)]


def _sheet(paths, destination, columns=4, labels=None):
    core, gui = _qt()
    columns = min(columns, len(paths))
    rows = math.ceil(len(paths)/columns)
    canvas = gui.QImage(columns*320, rows*220, gui.QImage.Format_RGB32)
    canvas.fill(gui.QColor("#11151b"))
    painter = gui.QPainter(canvas)
    font = painter.font()
    font.setPixelSize(14)
    painter.setFont(font)
    painter.setPen(gui.QColor("#edf2f7"))
    for i, path in enumerate(paths):
        original = gui.QImage(str(path))
        image = original.copy(0, 0, original.width(), original.height()-28).scaled(320, 192, core.Qt.KeepAspectRatio, core.Qt.SmoothTransformation)
        x, y = (i % columns)*320, (i//columns)*220
        painter.drawImage(x+(320-image.width())//2, y+(192-image.height())//2, image)
        painter.drawText(x+8, y+212, labels[i] if labels else Path(path).stem)
    painter.end()
    if not canvas.save(str(destination), "PNG"):
        raise state.ToolError("CAPTURE_FAILED", "Could not save the review sheet")


def _project(position, info):
    matrix = om.MMatrix(info["camera"]["model_view_matrix"]["values"])*om.MMatrix(info["camera"]["projection_matrix"]["values"])
    point = om.MPoint(*(v*info.get("world_to_centimeters", 1) for v in position))*matrix
    if point.w <= 1e-8:
        return None
    x, y, width, height = info["image_rect"]
    return [x+(point.x/point.w+1)*width/2, y+(1-point.y/point.w)*height/2]


def _decorate(data, directory, options):
    core, gui = _qt()
    samples = data["samples"]
    for info in data["images"]:
        path = Path(directory)/info["file"]
        canvas = gui.QImage(str(path))
        clean = canvas.copy() if options.get("trails") or options.get("ghosts") else None
        painter = gui.QPainter(canvas)
        painter.setRenderHint(gui.QPainter.Antialiasing)
        painter.setClipRect(0, 0, info["width"], info["height"]-28)
        if options.get("trails"):
            for role in samples[0]["points"]:
                painter.setPen(gui.QPen(gui.QColor(80, 210, 235, 155), 1.5))
                previous = None
                for sample in samples:
                    point = _project(sample["points"][role]["position"], info)
                    if point and previous:
                        painter.drawLine(core.QPointF(*previous), core.QPointF(*point))
                    previous = point
        ghosts = options.get("ghosts", 0)
        if ghosts:
            index = next(i for i, sample in enumerate(samples) if sample["frame"] == info["frame"])
            for ghost_index in range(max(0, index-ghosts), min(len(samples), index+ghosts+1)):
                if ghost_index == index:
                    continue
                sample = samples[ghost_index]
                painter.setPen(gui.QPen(gui.QColor("#eead61" if ghost_index < index else "#84d3a5"), 2))
                for mapping in data["mappings"]:
                    point = _project(sample["points"][mapping["id"]]["position"], info)
                    if not point:
                        continue
                    painter.drawEllipse(core.QPointF(*point), 3, 3)
                    if mapping.get("parent"):
                        parent = _project(sample["points"][mapping["parent"]]["position"], info)
                        if parent:
                            painter.drawLine(core.QPointF(*point), core.QPointF(*parent))
        painter.setClipping(False)
        painter.setPen(gui.QColor("#f3f5f7"))
        font = painter.font()
        font.setPixelSize(13)
        painter.setFont(font)
        camera = info["camera"]["node"]["name"].rsplit("|", 1)[-1]
        label = f"{data['label'][:40]} | frame {info['frame']:g} | {info['seconds']:.3f}s | {camera}"
        painter.drawText(8, info["height"]-9, label)
        painter.end()
        if not canvas.save(str(path), "PNG"):
            raise state.ToolError("CAPTURE_FAILED", "Could not save labeled review image")
        if clean is not None:
            painter = gui.QPainter(clean)
            painter.setFont(font)
            painter.setPen(gui.QColor("#f3f5f7"))
            painter.drawText(8, info["height"]-9, label)
            painter.end()
            info["clean_file"] = path.stem+"-clean.png"
            if not clean.save(str(Path(directory)/info["clean_file"]), "PNG"):
                raise state.ToolError("CAPTURE_FAILED", "Could not save clean review image")


def _write_player(data, directory):
    # A local, self-contained viewer; authored strings enter the DOM as text.
    payload = json.dumps({key: data[key] for key in ("label", "fps", "images", "notes", "complete")}, allow_nan=False).replace("<", "\\u003c")
    page = Path(__file__).with_name("animation_player.html").read_text(encoding="utf-8")
    Path(directory, "review.html").write_text(page.replace("PAYLOAD", payload), encoding="utf-8")
    Path(directory, "manifest.json").write_text(json.dumps(data, indent=2, allow_nan=False), encoding="utf-8")


def _summary(token, data):
    return {"artifact_id": token, "label": data["label"], "complete": data["complete"],
            "sample_count": len(data["samples"]), "image_count": len(data["images"]), "fps": data["fps"],
            "units": data["units"], "landmarks": [{"id": m["id"], "node": m["node"], "control": m["control"]} for m in data["mappings"]],
            "sampled_frames": [s["frame"] for s in data["samples"]], "missing_frames": data["missing_frames"],
            "review_path": data.get("review_path"), "timing_seconds": data["timing_seconds"],
            "overview": data.get("overview", []),
            "coverage": data["coverage"], "notes": data["notes"], "expires_after_seconds": store.TTL}


def artifact(arguments, call):
    action = arguments["action"]
    if action == "list":
        return state.result(call, {"artifacts": store.listing("take")}, "Listed animation takes")
    token = arguments.get("artifact_id", "")
    record = store.get(token, "take")
    data = record["data"]
    images = []
    if action == "release":
        store.release(token)
        output = {"artifact_id": token, "released": True}
    elif action == "notes":
        if not arguments.get("notes"):
            raise state.ToolError("INVALID_ARGUMENT", "notes requires at least one note")
        for note in arguments["notes"]:
            _range(note.get("time_range"))
        original, original_bytes = record["data"], record["bytes"]
        try:
            notes = store.add_notes(token, arguments["notes"])
            if record["directory"]:
                _write_player(record["data"], record["directory"])
            store.recount(token)
        except Exception:
            record["data"], record["bytes"] = original, original_bytes
            if record["directory"]:
                _write_player(original, record["directory"])
            raise
        output = {"artifact_id": token, "notes": notes}
    elif action == "frames":
        indices = arguments.get("indices", [0])
        if any(index >= len(data["images"]) for index in indices):
            raise state.ToolError("INVALID_ARGUMENT", "Image index is outside this take")
        selected = [data["images"][i] for i in indices]
        image_paths = [item.get("clean_file", item["file"]) if arguments.get("view") == "clean" else item["file"] for item in selected]
        images = [_image(Path(record["directory"])/path) for path in image_paths]
        if sum(len(item["data"]) for item in images) > 6*1024*1024:
            raise state.ToolError("IMAGE_OUTPUT_LIMIT", "Request fewer frames at once")
        output = {"artifact_id": token, "images": selected, "view": arguments.get("view", "annotated"), "image_content_indices": list(range(len(images)))}
    else:
        offset, limit = arguments.get("offset", 0), arguments.get("limit", 12)
        output = {**_summary(token, data), "samples": data["samples"][offset:offset+limit],
                  "next_offset": offset+limit if offset+limit < len(data["samples"]) else None,
                  "images": [{"index": i, **item} for i, item in enumerate(data["images"])],
                  "contacts": data["contacts"], "derivatives": data["derivatives"]}
    response = state.result(call, output, f"Animation artifact {action}", image_content=images)
    if images:
        response["content"] = images
    return response


def analyze(arguments, call):
    data = store.get(arguments["artifact_id"], "take")["data"]
    if arguments.get("root_motion") and arguments.get("root_landmark") not in {m["id"] for m in data["mappings"]}:
        raise state.ToolError("INVALID_ARGUMENT", "root_motion requires an explicit mapped root_landmark")
    findings = diagnose(data, arguments)
    by_role = {m["id"]: m for m in data["mappings"]}
    for finding in findings:
        finding["control"] = by_role[finding["landmark"]]["control"]
        finding["image_indices"] = [i for i, image in enumerate(data["images"]) if finding["time_range"][0] <= image["frame"] <= finding["time_range"][1]][:12]
    return state.result(call, {"artifact_id": arguments["artifact_id"], "findings": findings,
                               "finding_limit": 200, "limit_reached": len(findings) == 200,
                               "complete_source": data["complete"], "sampled_frames": [s["frame"] for s in data["samples"]],
                               "limitations": ["Between-sample motion is unobserved", "Contact checks require declared planes and intervals",
                                               "Pose steps can be intentional; thresholds are not an acting-quality score",
                                               "Reach checks use explicitly supplied limits; no automatic IK inference",
                                               "No whole-mesh collision or physical center of mass"]},
                        f"Found {len(findings)} motion review candidates")


def compare(arguments, call):
    before_record = store.get(arguments["before"], "take")
    after_record = store.get(arguments["after"], "take")
    before, after = before_record["data"], after_record["data"]
    if before["units"] != after["units"] or before["fps"] != after["fps"]:
        raise state.ToolError("INCOMPATIBLE_TAKES", "Comparison requires matching units and FPS")
    mappings = [{m["id"]: m for m in data["mappings"]} for data in (before, after)]
    roles = arguments.get("landmarks", list(mappings[0]))
    for role in roles:
        if any(role not in mapping for mapping in mappings) or any(
            mappings[0][role][key] != mappings[1][role][key] for key in ("offset",)) or mappings[0][role]["node"]["node_id"] != mappings[1][role]["node"]["node_id"]:
            raise state.ToolError("INCOMPATIBLE_TAKES", "Comparison requires the same landmark identities and offsets")
    lookup = {round(sample["frame"], 8): sample for sample in after["samples"]}
    pairs = [(sample, lookup[round(sample["frame"]+arguments.get("frame_offset", 0), 8)]) for sample in before["samples"]
             if round(sample["frame"]+arguments.get("frame_offset", 0), 8) in lookup]
    if not pairs:
        raise state.ToolError("INCOMPATIBLE_TAKES", "No sampled frames align at this offset")
    changes = []
    for role in roles:
        values = [(a["frame"], distance(a["points"][role]["position"], b["points"][role]["position"]),
                   angle(a["points"][role]["quaternion"], b["points"][role]["quaternion"])) for a, b in pairs]
        worst = max(values, key=lambda value: value[1])
        changes.append({"landmark": role, "max_position_delta": worst[1], "worst_frame": worst[0],
                        "rms_position_delta": math.sqrt(sum(v[1]**2 for v in values)/len(values)),
                        "max_orientation_delta_degrees": max(v[2] for v in values)})
    image_pairs = []
    for a in before["images"]:
        for b in after["images"]:
            if abs(b["frame"]-a["frame"]-arguments.get("frame_offset", 0)) < 1e-7 and a["camera"]["node"]["node_id"] == b["camera"]["node"]["node_id"]:
                consistent = all(a[field] == b[field] for field in ("width", "height", "image_rect")) and before["capture_settings"] == after["capture_settings"]
                for matrix in ("model_view_matrix", "projection_matrix"):
                    consistent = consistent and all(abs(x-y) < 1e-7 for x, y in zip(a["camera"][matrix]["values"], b["camera"][matrix]["values"]))
                if consistent:
                    image_pairs.append((a, b))
    images = []
    selected = _overview(image_pairs, arguments.get("image_pairs", 2)) if arguments.get("image_pairs", 2) else []
    for a, b in selected:
        # Comparison output is temporary; it does not mutate either retained take.
        with tempfile.TemporaryDirectory(prefix="maya-mcp-compare-") as directory:
            path = Path(directory)/"pair.png"
            _sheet([Path(before_record["directory"])/a["file"], Path(after_record["directory"])/b["file"]], path, columns=2,
                   labels=[f"Before | frame {a['frame']:g}", f"After | frame {b['frame']:g}"])
            images.append(_image(path))
    output = {"before": arguments["before"], "after": arguments["after"], "frame_offset": arguments.get("frame_offset", 0),
              "paired_frames": len(pairs), "unpaired_before_frames": len(before["samples"])-len(pairs), "changes": changes,
              "compatible_image_pairs": len(image_pairs), "returned_image_pairs": [{"before_frame": a["frame"], "after_frame": b["frame"]} for a,b in selected],
              "units": before["units"], "complete_sources": before["complete"] and after["complete"]}
    if before["images"] and after["images"] and not image_pairs:
        call.warnings.append({"code": "VISUAL_COMPARISON_UNAVAILABLE", "message": "No frames had matching cameras, matrices, dimensions and capture settings"})
    response = state.result(call, output, f"Compared {len(pairs)} aligned frames", image_content=images)
    if images:
        response["content"] = images
    return response


from .animation_edit import edit, layer

ANIMATION_HANDLERS = {
    "maya.animation.profile": profile, "maya.animation.describe": describe,
    "maya.animation.sample": sample, "maya.animation.capture": capture,
    "maya.animation.artifact": artifact, "maya.animation.analyze": analyze,
    "maya.animation.edit": edit, "maya.animation.compare": compare, "maya.animation.layer": layer,
}
