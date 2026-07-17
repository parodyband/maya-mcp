"""Viewport-to-scene grounding for vision-guided Maya workflows."""

from __future__ import annotations

import math
from typing import Any

import maya.api.OpenMaya as om
import maya.cmds as cmds

from . import state
from .tools_viewport import _active_view


_IGNORED_SHAPE_TYPES = {
    "camera",
    "directionalLight",
    "pointLight",
    "spotLight",
    "areaLight",
    "ambientLight",
}


def _attribute_visible(node: str) -> bool:
    """Return visibility from the DAG attribute chain.

    Panel isolation, occlusion, and renderer-specific culling are intentionally
    reported separately because Maya does not expose them through this query.
    """

    def chain_visible(current: str) -> bool:
        while current:
            attribute = f"{current}.visibility"
            if cmds.objExists(attribute):
                try:
                    if not bool(cmds.getAttr(attribute)):
                        return False
                except RuntimeError:
                    return False
            parents = cmds.listRelatives(
                current, parent=True, fullPath=True
            ) or []
            current = parents[0] if parents else ""
        return True

    if not chain_visible(node):
        return False
    try:
        is_shape = bool(cmds.objectType(node, isAType="shape"))
    except RuntimeError:
        return False
    if is_shape:
        return True
    shapes = cmds.listRelatives(
        node, shapes=True, noIntermediate=True, fullPath=True
    ) or []
    drawable_shapes = [
        shape
        for shape in shapes
        if cmds.nodeType(shape) not in _IGNORED_SHAPE_TYPES
    ]
    return not drawable_shapes or any(
        chain_visible(shape) for shape in drawable_shapes
    )


def _candidate_transform(node: str) -> str | None:
    """Normalize a DAG node to the transform represented in the scene map."""

    try:
        is_shape = bool(cmds.objectType(node, isAType="shape"))
    except RuntimeError:
        return None
    if not is_shape:
        return node
    try:
        shape_type = cmds.nodeType(node)
    except RuntimeError:
        return None
    if shape_type in _IGNORED_SHAPE_TYPES:
        return None
    if cmds.objExists(f"{node}.intermediateObject"):
        try:
            if cmds.getAttr(f"{node}.intermediateObject"):
                return None
        except RuntimeError:
            return None
    parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
    return parents[0] if parents else None


def _discover_candidates(
    arguments: dict[str, Any],
    requested_types: set[str],
    max_candidates: int,
) -> tuple[list[tuple[str, list[str]]], int, bool, int]:
    """Discover and type-filter a bounded set before projection work.

    Maya's ls(head=...) bound is used for implicit scene discovery. Explicit
    selectors are cheap to resolve and may be scanned until enough matching
    nodes are found, so a type mismatch cannot consume the candidate budget.
    Bounding happens before visibility, bounds, pivot, and projection queries.
    """

    selectors = arguments.get("nodes") or []
    discovery_limit = max_candidates + 1
    raw_nodes: list[str] = []
    if selectors:
        raw_nodes = [state.resolve_node(selector) for selector in selectors]
    elif requested_types:
        # Filter in Maya's query before truncating the returned candidates.
        for node_type in sorted(requested_types):
            remaining = discovery_limit - len(raw_nodes)
            if remaining <= 0:
                break
            try:
                raw_nodes.extend(
                    cmds.ls(type=node_type, long=True, head=remaining) or []
                )
            except RuntimeError:
                # Preserve the prior unknown-type behavior: no matching records.
                continue
    else:
        # Joints are transforms without drawable child shapes.
        raw_nodes.extend(
            cmds.ls(type="joint", long=True, head=discovery_limit) or []
        )
        remaining = discovery_limit - len(raw_nodes)
        if remaining > 0:
            raw_nodes.extend(
                cmds.ls(
                    type="geometryShape",
                    long=True,
                    noIntermediate=True,
                    head=remaining,
                )
                or []
            )

    candidates: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    rejected_type = 0
    for raw_node in raw_nodes:
        node = _candidate_transform(raw_node)
        if node is None or node in seen:
            continue
        seen.add(node)
        types = _shape_types(node)
        if requested_types and requested_types.isdisjoint(types):
            rejected_type += 1
            continue
        candidates.append((node, types))
        if len(candidates) >= discovery_limit:
            break

    truncated = len(candidates) > max_candidates
    candidate_total = len(candidates)
    return candidates[:max_candidates], candidate_total, truncated, rejected_type


def _shape_types(node: str) -> list[str]:
    result: set[str] = set()
    try:
        result.add(cmds.nodeType(node))
    except RuntimeError:
        return []
    for shape in cmds.listRelatives(node, shapes=True, fullPath=True) or []:
        try:
            result.add(cmds.nodeType(shape))
        except RuntimeError:
            continue
    return sorted(result)


def _world_bounds(node: str, include_hidden: bool) -> list[float]:
    """Return visible-shape bounds unless hidden geometry was requested."""

    if include_hidden:
        return [float(value) for value in cmds.exactWorldBoundingBox(node)]
    shapes = cmds.listRelatives(
        node, shapes=True, noIntermediate=True, fullPath=True
    ) or []
    drawable_shapes = [
        shape
        for shape in shapes
        if cmds.nodeType(shape) not in _IGNORED_SHAPE_TYPES
    ]
    if not drawable_shapes:
        return [float(value) for value in cmds.exactWorldBoundingBox(node)]
    visible_bounds = [
        [float(value) for value in cmds.exactWorldBoundingBox(shape)]
        for shape in drawable_shapes
        if _attribute_visible(shape)
    ]
    if not visible_bounds:
        raise RuntimeError("No visible drawable shape bounds")
    return [
        min(bounds[index] for bounds in visible_bounds)
        for index in range(3)
    ] + [
        max(bounds[index] for bounds in visible_bounds)
        for index in range(3, 6)
    ]


def _bbox_corners(bounds: list[float]) -> list[list[float]]:
    minimum = bounds[:3]
    maximum = bounds[3:]
    return [
        [x, y, z]
        for x in (minimum[0], maximum[0])
        for y in (minimum[1], maximum[1])
        for z in (minimum[2], maximum[2])
    ]


def _project_point(
    view: Any,
    model_view: om.MMatrix,
    point: list[float],
    scale_x: float,
    scale_y: float,
) -> dict[str, Any]:
    camera_point = om.MPoint(*point) * model_view
    projected = view.worldToView(om.MPoint(*point))
    if len(projected) == 3:
        x, y, visible = projected
    else:
        x, y = projected
        visible = 0 <= x < view.portWidth() and 0 <= y < view.portHeight()
    return {
        "x": float(x) * scale_x,
        "y": float(y) * scale_y,
        "front": float(camera_point.z) < 0.0,
        "inside": bool(visible),
        "camera_depth": max(0.0, -float(camera_point.z)),
    }


def _screen_bounds(
    projected: list[dict[str, Any]], width: int, height: int
) -> dict[str, Any] | None:
    front = [point for point in projected if point["front"]]
    if not front:
        return None
    raw_min_x = min(point["x"] for point in front)
    raw_max_x = max(point["x"] for point in front)
    raw_min_y = min(point["y"] for point in front)
    raw_max_y = max(point["y"] for point in front)
    if raw_max_x < 0 or raw_max_y < 0 or raw_min_x >= width or raw_min_y >= height:
        return None

    min_x = max(0.0, min(float(width - 1), raw_min_x))
    max_x = max(0.0, min(float(width - 1), raw_max_x))
    min_y = max(0.0, min(float(height - 1), raw_min_y))
    max_y = max(0.0, min(float(height - 1), raw_max_y))
    pixel_width = max(0.0, max_x - min_x)
    pixel_height = max(0.0, max_y - min_y)
    top = float(height - 1) - max_y
    bottom = float(height - 1) - min_y
    area = pixel_width * pixel_height
    return {
        "bottom_left": {
            "min": [int(round(min_x)), int(round(min_y))],
            "max": [int(round(max_x)), int(round(max_y))],
        },
        "top_left": {
            "min": [int(round(min_x)), int(round(top))],
            "max": [int(round(max_x)), int(round(bottom))],
        },
        "normalized_top_left": {
            "min": [min_x / width, top / height],
            "max": [max_x / width, bottom / height],
        },
        "coverage": area / float(width * height),
        "clipped_to_image": (
            raw_min_x < 0
            or raw_min_y < 0
            or raw_max_x >= width
            or raw_max_y >= height
        ),
    }


def build_scene_map(
    arguments: dict[str, Any], *, view: Any | None = None
) -> dict[str, Any]:
    """Build conservative 2D grounding records for drawable DAG objects."""

    view = view or _active_view()
    source_width = int(view.portWidth())
    source_height = int(view.portHeight())
    width = int(arguments.get("width", source_width))
    height = int(arguments.get("height", source_height))
    scale_x = float(width) / float(source_width)
    scale_y = float(height) / float(source_height)
    model_view = view.modelViewMatrix()
    requested_types = set(arguments.get("node_types") or [])
    include_hidden = bool(arguments.get("include_hidden", False))
    max_nodes = int(arguments.get("max_nodes", 250))

    max_candidates = int(arguments.get('max_candidates', 1000))
    (
        candidates,
        candidate_total,
        candidate_scan_truncated,
        rejected_type,
    ) = _discover_candidates(arguments, requested_types, max_candidates)
    records: list[dict[str, Any]] = []
    rejected = {
        "hidden": 0,
        "type": rejected_type,
        "offscreen": 0,
        "invalid_bounds": 0,
    }
    for node, types in candidates:
        visible_by_attributes = _attribute_visible(node)
        if not include_hidden and not visible_by_attributes:
            rejected["hidden"] += 1
            continue
        try:
            bounds = _world_bounds(node, include_hidden)
        except RuntimeError:
            rejected["invalid_bounds"] += 1
            continue
        if len(bounds) != 6 or not all(math.isfinite(value) for value in bounds):
            rejected["invalid_bounds"] += 1
            continue

        corners = _bbox_corners(bounds)
        projected = [
            _project_point(view, model_view, point, scale_x, scale_y)
            for point in corners
        ]
        screen = _screen_bounds(projected, width, height)
        if screen is None:
            rejected["offscreen"] += 1
            continue
        pivot = [
            float(value)
            for value in cmds.xform(node, query=True, worldSpace=True, rotatePivot=True)
        ]
        projected_pivot = _project_point(
            view, model_view, pivot, scale_x, scale_y
        )
        pivot_y_top = float(height - 1) - projected_pivot["y"]
        depths = [point["camera_depth"] for point in projected if point["front"]]
        records.append(
            {
                "node": state.node_ref(node),
                "types": types,
                "attribute_visible": visible_by_attributes,
                "world_bounds": {"min": bounds[:3], "max": bounds[3:]},
                "screen_bounds": screen,
                "pivot": {
                    "world": pivot,
                    "screen_bottom_left": [
                        int(round(projected_pivot["x"])),
                        int(round(projected_pivot["y"])),
                    ],
                    "screen_top_left": [
                        int(round(projected_pivot["x"])),
                        int(round(pivot_y_top)),
                    ],
                    "inside_view": projected_pivot["inside"],
                },
                "camera_depth": min(depths) if depths else None,
            }
        )

    records.sort(
        key=lambda item: (
            item["camera_depth"] is None,
            item["camera_depth"] or float("inf"),
            item["node"]["long_name"],
        )
    )
    total_projected = len(records)
    truncated = total_projected > max_nodes
    return {
        "resolution": {
            "width": width,
            "height": height,
            "source_width": source_width,
            "source_height": source_height,
        },
        "coordinate_system": {
            "pixel_boxes": "top-left and bottom-left",
            "normalized_boxes": "top-left, [0,1]",
            "world_up_axis": cmds.upAxis(query=True, axis=True),
            "linear_unit": cmds.currentUnit(query=True, linear=True),
        },
        "projection": {
            "kind": "conservative_world_aabb",
            "occlusion_tested": False,
            "panel_isolation_tested": False,
            "note": (
                "Boxes are projected candidates, not a segmentation mask. "
                "Use viewport picking or an object-ID channel to confirm overlap."
            ),
        },
        "objects": records[:max_nodes],
        'candidate_total': candidate_total,
        'candidate_total_is_lower_bound': candidate_scan_truncated,
        'candidate_scanned': len(candidates),
        'candidate_scan_truncated': candidate_scan_truncated,
        "projected_total": total_projected,
        "truncated": truncated,
        "rejected": rejected,
    }


def viewport_scene_map(
    arguments: dict[str, Any], call: state.CallState
) -> dict[str, Any]:
    try:
        data = build_scene_map(arguments)
        return state.result(
            call,
            data,
            f"Projected {len(data['objects'])} Maya object(s) into the viewport",
        )
    except state.ToolError:
        raise
    except Exception as error:
        raise state.ToolError(
            "VIEWPORT_SCENE_MAP_FAILED",
            str(error),
            {"type": type(error).__name__},
        ) from error


VISION_HANDLERS = {"maya.viewport.scene_map": viewport_scene_map}
