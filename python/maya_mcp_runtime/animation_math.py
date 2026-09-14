"""Pure motion calculations on explicit, sampled scene data."""
from __future__ import annotations

import math


def distance(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def angle(a, b):
    # q and -q represent the same orientation. Normalize defensively.
    norms = math.sqrt(sum(x*x for x in a) * sum(x*x for x in b))
    if norms <= 1e-15:
        raise ValueError("Cannot compare a zero quaternion")
    cosine = abs(sum(x*y for x, y in zip(a, b))) / norms
    return math.degrees(2 * math.acos(min(1.0, cosine)))


def derivatives(samples):
    """Nonuniform three-point differences, one-sided at endpoints; no smoothing."""
    if not samples:
        return
    for role in samples[0]["points"]:
        for index, current in enumerate(samples):
            def differentiate(field):
                if len(samples) == 1:
                    return [0.0, 0.0, 0.0]
                left, right = max(0, index - 1), min(len(samples) - 1, index + 1)
                if left == index or right == index:
                    dt = samples[right]["seconds"] - samples[left]["seconds"]
                    return [(b-a)/dt for a, b in zip(samples[left]["points"][role][field], samples[right]["points"][role][field])]
                h0 = current["seconds"] - samples[left]["seconds"]
                h1 = samples[right]["seconds"] - current["seconds"]
                weights = (-h1/(h0*(h0+h1)), (h1-h0)/(h0*h1), h0/(h1*(h0+h1)))
                return [sum(w * samples[i]["points"][role][field][axis] for w, i in zip(weights, (left, index, right))) for axis in range(3)]
            current["points"][role]["velocity"] = differentiate("position")
        # All velocities must exist before the second pass.
        for index, current in enumerate(samples):
            left, right = max(0, index-1), min(len(samples)-1, index+1)
            dt = samples[right]["seconds"] - samples[left]["seconds"]
            current["points"][role]["acceleration"] = ([0.0]*3 if dt == 0 else [
                (b-a)/dt for a, b in zip(samples[left]["points"][role]["velocity"], samples[right]["points"][role]["velocity"])])
            current["points"][role]["speed"] = distance(current["points"][role]["velocity"], [0]*3)


def diagnose(data, options):
    samples = data["samples"]
    findings = []
    def emit(kind, role, start, end, value, threshold, unit):
        if len(findings) < 200:
            findings.append({"kind": kind, "landmark": role, "time_range": [start, end],
                             "value": value, "threshold": threshold, "unit": unit,
                             "interpretation": "review_candidate"})
    unit = data["units"]["linear"]
    for mapping in data["mappings"]:
        if "reach_root" in mapping:
            role = mapping["id"]
            worst = max(samples, key=lambda s: distance(s["points"][role]["position"], s["points"][mapping["reach_root"]]["position"]))
            value = distance(worst["points"][role]["position"], worst["points"][mapping["reach_root"]]["position"])
            if value > mapping["max_reach"]:
                emit("reach_exceeded", role, worst["frame"], worst["frame"], value, mapping["max_reach"], unit)
    for definition in data["contacts"]:
        index, role = definition["id"], definition["landmark"]
        active = [(s, s["contacts"][index]) for s in samples if index in s["contacts"]]
        if not active:
            continue
        worst = max(active, key=lambda pair: pair[1]["drift"])
        tolerance = options.get("slip_tolerance", 0.5)
        if worst[1]["drift"] > tolerance:
            emit("contact_drift", role, active[0][0]["frame"], worst[0]["frame"], worst[1]["drift"], tolerance, unit)
        worst = min(active, key=lambda pair: pair[1]["height"])
        tolerance = options.get("penetration_tolerance", 0.1)
        if worst[1]["height"] < -tolerance:
            emit("contact_penetration", role, worst[0]["frame"], worst[0]["frame"], -worst[1]["height"], tolerance, unit)
    if options.get("stage", "polish") != "blocking":
        for previous, current in zip(samples, samples[1:]):
            for role, point in current["points"].items():
                prior = previous["points"][role]
                for kind, value, threshold, units in (
                    ("position_step", distance(prior["position"], point["position"]), options.get("jump_distance", 10), unit),
                    ("orientation_step", angle(prior["quaternion"], point["quaternion"]), options.get("jump_degrees", 90), "degrees"),
                ):
                    if value > threshold:
                        emit(kind, role, previous["frame"], current["frame"], value, threshold, units)
    if options.get("loop") and len(samples) > 1:
        first, last = samples[0], samples[-1]
        root_delta = [0, 0, 0]
        if options.get("root_motion"):
            root = options["root_landmark"]
            root_delta = [b-a for a,b in zip(first["points"][root]["position"], last["points"][root]["position"])]
        for role, point in first["points"].items():
            end = last["points"][role]
            checks = [("loop_orientation", angle(point["quaternion"], end["quaternion"]), options.get("loop_orientation_tolerance", 1), "degrees"),
                      ("loop_velocity", distance(point["velocity"], end["velocity"]), options.get("loop_velocity_tolerance", 1), unit+"/second")]
            adjusted_end = [value-delta for value, delta in zip(end["position"], root_delta)]
            checks.append(("loop_position", distance(point["position"], adjusted_end), options.get("loop_position_tolerance", 0.1), unit))
            for kind, value, threshold, units in checks:
                if value > threshold:
                    emit(kind, role, first["frame"], last["frame"], value, threshold, units)
    return findings
