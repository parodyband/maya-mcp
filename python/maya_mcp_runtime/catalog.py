"""Protocol catalog for Maya MCP tools, resources, and prompts."""

from __future__ import annotations

import json
import copy
import os
from typing import Any

JSON_SCHEMA = "https://json-schema.org/draft/2020-12/schema"


def _object(
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "$schema": JSON_SCHEMA,
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _envelope() -> dict[str, Any]:
    return {
        "$schema": JSON_SCHEMA,
        "type": "object",
        "properties": {
            "schema_version": {"const": "1.0"},
            "ok": {"type": "boolean"},
            "request_id": {"type": "string"},
            "scene_epoch": {"type": "string"},
            "revisions": {"type": "object"},
            "summary": {"type": "string"},
            "data": {},
            "changes": {"type": "array"},
            "warnings": {"type": "array"},
            "undo": {"type": "object"},
            "timing_ms": {"type": "number"},
            "error": {"type": "object"},
        },
        "required": [
            "schema_version",
            "ok",
            "request_id",
            "scene_epoch",
            "revisions",
            "summary",
            "data",
            "changes",
            "warnings",
            "undo",
            "timing_ms",
        ],
        "additionalProperties": False,
    }


def _tool(
    name: str,
    title: str,
    description: str,
    input_schema: dict[str, Any],
    *,
    read_only: bool,
    destructive: bool = False,
    idempotent: bool = False,
    open_world: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": input_schema,
        "outputSchema": _envelope(),
        "annotations": {
            "readOnlyHint": read_only,
            "destructiveHint": destructive,
            "idempotentHint": idempotent,
            "openWorldHint": open_world,
        },
    }


NODE_SELECTOR = {
    "oneOf": [
        {"type": "string", "minLength": 1},
        {
            "type": "object",
            "properties": {
                "node_id": {"type": "string", "minLength": 1},
                "scene_epoch": {
                    "type": "string",
                    "pattern": r"^[0-9a-f]{32}$",
                },
                "uuid": {"type": "string"},
                "reference_node": {
                    "oneOf": [{"type": "string"}, {"type": "null"}]
                },
                "dag_path": {"type": "string", "minLength": 1},
                "name": {"type": "string", "minLength": 1},
                "long_name": {"type": "string", "minLength": 1},
                "type": {"type": "string"},
                "dag_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "dag_paths_truncated": {"type": "boolean"},
                "dag_path_limit": {"type": "integer", "minimum": 1},
                "instanced": {"type": "boolean"},
                "referenced": {"type": "boolean"},
                "locked": {"type": "boolean"},
                "component": {"type": "string"},
            },
            "additionalProperties": False,
            "anyOf": [
                {"required": ["node_id"]},
                {"required": ["dag_path"]},
                {"required": ["long_name"]},
                {"required": ["dag_paths"]},
                {"required": ["name"]},
            ],
        },
    ]
}

# Component-bearing references are accepted only by tools that explicitly
# advertise component semantics. Node-only tools fail closed at runtime too.
COMPONENT_SELECTOR = copy.deepcopy(NODE_SELECTOR)
del NODE_SELECTOR["oneOf"][1]["properties"]["component"]

VECTOR3 = {
    "type": "array",
    "items": {"type": "number"},
    "minItems": 3,
    "maxItems": 3,
}

RGB3 = {
    "type": "array",
    "items": {"type": "number", "minimum": 0, "maximum": 1},
    "minItems": 3,
    "maxItems": 3,
}

RIG_PREVIEW_HANDLE = {
    "type": "object",
    "properties": {
        "preview_id": {
            "type": "string",
            "pattern": r"^rig-preview:[0-9a-f]{32}$",
        },
        "scene_epoch": {"type": "string", "pattern": r"^[0-9a-f]{32}$"},
        "revision": {"type": "integer", "minimum": 1},
    },
    "required": ["preview_id", "scene_epoch", "revision"],
    "additionalProperties": False,
}

RIG_PREVIEW_JOINT = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "minLength": 1, "maxLength": 128},
        "name": {"type": "string", "minLength": 1, "maxLength": 128},
        "position": VECTOR3,
        "parent_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "radius": {"type": "number", "exclusiveMinimum": 0},
    },
    "required": ["id", "position"],
    "additionalProperties": False,
}

RIG_PREVIEW_CONTROL = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "minLength": 1, "maxLength": 128},
        "name": {"type": "string", "minLength": 1, "maxLength": 128},
        "offset_name": {"type": "string", "minLength": 1, "maxLength": 128},
        "constraint_name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
        },
        "target": NODE_SELECTOR,
        "target_joint_id": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
        },
        "position": VECTOR3,
        "rotation": VECTOR3,
        "parent_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "shape": {
            "type": "string",
            "enum": ["circle", "square", "cube"],
            "default": "circle",
        },
        "size": {"type": "number", "exclusiveMinimum": 0, "default": 1},
        "color": {"type": "integer", "minimum": 0, "maximum": 31},
        "constraint": {
            "type": "string",
            "enum": ["none", "parent", "orient", "point"],
            "default": "none",
        },
        "maintain_offset": {"type": "boolean", "default": True},
    },
    "required": ["id"],
    "additionalProperties": False,
}


OBSERVATION_INPUT = _object({
    "image": {"type": "boolean", "default": True},
    "width": {"type": "integer", "minimum": 64, "maximum": 1024, "default": 960},
    "height": {"type": "integer", "minimum": 64, "maximum": 1024, "default": 540},
    "nodes": {"type": "array", "items": NODE_SELECTOR, "maxItems": 100},
    "attributes": {"type": "array", "items": {"type": "string", "maxLength": 128}, "maxItems": 32},
    "max_nodes": {"type": "integer", "minimum": 1, "maximum": 100, "default": 40},
    "include_depth": {"type": "boolean", "default": False},
    "since": {"type": "string", "maxLength": 2048},
})
REQUEST_ID = {"type": "string", "minLength": 1, "maxLength": 128,
              "description": "Native session-scoped replay key. Returns queued immediately; poll maya.request.status. Never reuse with different inputs."}


TOOLS = [
    _tool(
        "maya.context.get",
        "Get Maya Context",
        "Return Maya readiness, scene identity and revisions, units, timeline, "
        "selection, active camera, workspace, renderer, and undo state.",
        _object(),
        read_only=True,
        idempotent=True,
    ),
    _tool(
        "maya.scene.query",
        "Query Maya Scene",
        "Query DAG or dependency nodes with strict filtering and bounded results. "
        "Returns canonical node references; ambiguous short names are rejected.",
        _object(
            {
                "scope": {
                    "type": "string",
                    "enum": ["scene", "selection", "subtree", "nodes"],
                    "default": "scene",
                },
                "nodes": {"type": "array", "items": NODE_SELECTOR},
                "root": NODE_SELECTOR,
                "node_types": {"type": "array", "items": {"type": "string"}},
                "name_glob": {"type": "string"},
                "include_shapes": {"type": "boolean", "default": True},
                "include_attributes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 64,
                },
                "include_connections": {
                    "type": "string",
                    "enum": ["none", "incoming", "outgoing", "both"],
                    "default": "none",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "default": 200,
                },
                "cursor": {"type": "string", "maxLength": 2048},
            }
        ),
        read_only=True,
        idempotent=True,
    ),
    _tool(
        "maya.node.apply",
        "Apply Maya Node Operations",
        "Atomically create, duplicate, rename, delete, parent, transform, set "
        "attributes, connect nodes, and assemble production rigs with custom "
        "controls, IK handles, pole vectors, constraints, and driven keys. "
        "Ordered steps can reference an earlier result with '$stepId'. Use "
        "validate_only to resolve targets without edits.",
        _object(
            {
                "operations": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 200,
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_]*$"},
                            "op": {
                                "type": "string",
                                "enum": [
                                    "create",
                                    "duplicate",
                                    "rename",
                                    "delete",
                                    "parent",
                                    "set_transform",
                                    "set_attribute",
                                    "add_attribute",
                                    "connect",
                                    "disconnect",
                                    "create_control",
                                    "create_ik_handle",
                                    "create_constraint",
                                    "set_driven_keys",
                                ],
                            },
                            "node": NODE_SELECTOR,
                            "node_type": {"type": "string"},
                            "name": {"type": "string"},
                            "parent": NODE_SELECTOR,
                            "attribute": {"type": "string"},
                            "value": {},
                            "attribute_type": {"type": "string"},
                            "source": {"type": "string"},
                            "destination": {"type": "string"},
                            "translate": VECTOR3,
                            "rotate": VECTOR3,
                            "scale": VECTOR3,
                            "matrix": {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 16,
                                "maxItems": 16,
                            },
                            "space": {
                                "type": "string",
                                "enum": ["world", "object"],
                                "default": "world",
                            },
                            "force": {"type": "boolean", "default": False},
                            "shape": {
                                "type": "string",
                                "enum": [
                                    "circle",
                                    "square",
                                    "cube",
                                    "diamond",
                                    "arrow",
                                    "custom",
                                ],
                                "default": "circle",
                            },
                            "size": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "maximum": 1000000,
                                "default": 1,
                            },
                            "points": {
                                "type": "array",
                                "items": VECTOR3,
                                "minItems": 2,
                                "maxItems": 1000,
                            },
                            "degree": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 7,
                                "default": 1,
                            },
                            "closed": {"type": "boolean", "default": False},
                            "normal": VECTOR3,
                            "color": {
                                "type": "integer",
                                "minimum": 0,
                                "maximum": 31,
                            },
                            "color_rgb": RGB3,
                            "line_width": {
                                "type": "number",
                                "minimum": 1,
                                "maximum": 10,
                            },
                            "start_joint": NODE_SELECTOR,
                            "end_joint": NODE_SELECTOR,
                            "solver": {
                                "type": "string",
                                "enum": [
                                    "ikRPsolver",
                                    "ikSCsolver",
                                    "ikSplineSolver",
                                    "ikSpringSolver",
                                ],
                                "default": "ikRPsolver",
                            },
                            "curve": NODE_SELECTOR,
                            "create_curve": {
                                "type": "boolean",
                                "default": True,
                            },
                            "drivers": {
                                "type": "array",
                                "items": NODE_SELECTOR,
                                "minItems": 1,
                                "maxItems": 16,
                            },
                            "driven": NODE_SELECTOR,
                            "constraint_type": {
                                "type": "string",
                                "enum": [
                                    "parent",
                                    "orient",
                                    "point",
                                    "scale",
                                    "aim",
                                    "pole_vector",
                                ],
                            },
                            "maintain_offset": {
                                "type": "boolean",
                                "default": True,
                            },
                            "aim_vector": VECTOR3,
                            "up_vector": VECTOR3,
                            "world_up_type": {
                                "type": "string",
                                "enum": [
                                    "scene",
                                    "object",
                                    "objectrotation",
                                    "vector",
                                    "none",
                                ],
                            },
                            "world_up_object": NODE_SELECTOR,
                            "skip_translate": {
                                "type": "array",
                                "items": {"type": "string", "enum": ["x", "y", "z"]},
                                "uniqueItems": True,
                                "maxItems": 3,
                            },
                            "skip_rotate": {
                                "type": "array",
                                "items": {"type": "string", "enum": ["x", "y", "z"]},
                                "uniqueItems": True,
                                "maxItems": 3,
                            },
                            "driver_plug": {"type": "string", "minLength": 3},
                            "driven_plug": {"type": "string", "minLength": 3},
                            "driven_keys": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 256,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "driver_value": {"type": "number"},
                                        "value": {"type": "number"},
                                        "in_tangent": {"type": "string"},
                                        "out_tangent": {"type": "string"},
                                    },
                                    "required": ["driver_value", "value"],
                                    "additionalProperties": False,
                                },
                            },
                            "nice_name": {"type": "string", "maxLength": 128},
                            "enum_names": {
                                "type": "array",
                                "items": {"type": "string", "maxLength": 64},
                                "minItems": 1,
                                "maxItems": 128,
                            },
                            "min_value": {"type": "number"},
                            "max_value": {"type": "number"},
                            "default_value": {},
                            "keyable": {"type": "boolean"},
                            "channel_box": {"type": "boolean"},
                            "locked": {"type": "boolean"},
                        },
                        "required": ["op"],
                        "additionalProperties": False,
                    },
                },
                "label": {"type": "string", "maxLength": 120},
                "validate_only": {"type": "boolean", "default": False},
                "if_scene_revision": {"type": "integer", "minimum": 0},
            },
            ["operations"],
        ),
        read_only=False,
        destructive=True,
    ),
    _tool(
        "maya.selection.set",
        "Set Maya Selection",
        "Replace, add, remove, toggle, or clear the object/component selection.",
        _object(
            {
                "items": {
                    "type": "array",
                    "items": COMPONENT_SELECTOR,
                    "maxItems": 500,
                },
                "mode": {
                    "type": "string",
                    "enum": ["replace", "add", "remove", "toggle", "clear"],
                    "default": "replace",
                },
            },
            ["mode"],
        ),
        read_only=False,
        idempotent=False,
    ),
    _tool(
        "maya.history.apply",
        "Undo or Redo",
        "Apply one or more Maya undo or redo steps and report the resulting state.",
        _object(
            {
                "action": {"type": "string", "enum": ["undo", "redo"]},
                "steps": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 1,
                },
            },
            ["action"],
        ),
        read_only=False,
        destructive=True,
    ),
    _tool(
        "maya.geometry.apply",
        "Create Geometry",
        "Create a polygon primitive or NURBS curve with explicit dimensions, "
        "transform, naming, and construction-history policy.",
        _object(
            {
                "kind": {
                    "type": "string",
                    "enum": ["cube", "sphere", "cylinder", "cone", "plane", "torus", "curve"],
                },
                "name": {"type": "string", "maxLength": 128},
                "position": VECTOR3,
                "rotation": VECTOR3,
                "scale": VECTOR3,
                "dimensions": {
                    "type": "object",
                    "properties": {
                        "width": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000000},
                        "height": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000000},
                        "depth": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000000},
                        "radius": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000000},
                        "section_radius": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000000},
                        "subdivisions_x": {"type": "integer", "minimum": 1, "maximum": 512},
                        "subdivisions_y": {"type": "integer", "minimum": 1, "maximum": 512},
                    },
                    "additionalProperties": False,
                },
                "points": {"type": "array", "items": VECTOR3, "minItems": 2, "maxItems": 10000},
                "degree": {"type": "integer", "minimum": 1, "maximum": 7, "default": 1},
                "construction_history": {"type": "boolean", "default": True},
            },
            ["kind"],
        ),
        read_only=False,
    ),
    _tool(
        "maya.material.apply",
        "Create or Assign Material",
        "Create an Arnold standard surface or Maya surface shader, set common "
        "PBR values, and assign it to objects or polygon faces.",
        _object(
            {
                "action": {"type": "string", "enum": ["create_assign", "assign", "inspect"]},
                "material": NODE_SELECTOR,
                "targets": {"type": "array", "items": COMPONENT_SELECTOR, "maxItems": 1000},
                "name": {"type": "string"},
                "shader_type": {
                    "type": "string",
                    "enum": ["standardSurface", "aiStandardSurface", "lambert", "blinn"],
                    "default": "standardSurface",
                },
                "base_color": {
                    "type": "array",
                    "items": {"type": "number", "minimum": 0, "maximum": 1},
                    "minItems": 3,
                    "maxItems": 3,
                },
                "metalness": {"type": "number", "minimum": 0, "maximum": 1},
                "roughness": {"type": "number", "minimum": 0, "maximum": 1},
            },
            ["action"],
        ),
        read_only=False,
    ),
    _tool(
        "maya.animation.apply",
        "Apply Animation Keys",
        "Set or delete keys on explicit node attributes with frame values and "
        "optional tangent types.",
        _object(
            {
                "action": {"type": "string", "enum": ["set_keys", "delete_keys", "inspect"]},
                "targets": {"type": "array", "items": NODE_SELECTOR, "maxItems": 250},
                "attributes": {"type": "array", "items": {"type": "string", "maxLength": 256}, "maxItems": 64},
                "keys": {
                    "type": "array",
                    "maxItems": 2000,
                    "items": {
                        "type": "object",
                        "properties": {
                            "time": {"type": "number"},
                            "value": {"type": "number"},
                            "in_tangent": {"type": "string"},
                            "out_tangent": {"type": "string"},
                        },
                        "required": ["time"],
                        "additionalProperties": False,
                    },
                },
                "time_range": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 2,
                    "maxItems": 2,
                },
            },
            ["action", "targets"],
        ),
        read_only=False,
    ),
    _tool(
        "maya.rig.skeleton",
        "Create or Inspect Skeleton",
        "Create and orient a named joint chain from world-space landmarks, or "
        "inspect an existing skeleton hierarchy and joint orientation.",
        _object(
            {
                "action": {"type": "string", "enum": ["create_chain", "inspect"]},
                "root": NODE_SELECTOR,
                "joints": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 500,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "position": VECTOR3,
                            "radius": {"type": "number", "exclusiveMinimum": 0},
                        },
                        "required": ["position"],
                        "additionalProperties": False,
                    },
                },
                "parent": NODE_SELECTOR,
                "primary_axis": {"type": "string", "enum": ["xyz", "xzy", "yxz", "yzx", "zxy", "zyx"], "default": "xyz"},
                "secondary_axis": {"type": "string", "enum": ["xup", "xdown", "yup", "ydown", "zup", "zdown"], "default": "yup"},
                "orient": {"type": "boolean", "default": True},
            },
            ["action"],
        ),
        read_only=False,
    ),
    _tool(
        "maya.rig.controls",
        "Create or Inspect Rig Controls",
        "Create curve controls with offset groups, colors, hierarchy matching, "
        "and optional parent/orient/point constraints.",
        _object(
            {
                "action": {"type": "string", "enum": ["create", "inspect"]},
                "targets": {"type": "array", "items": NODE_SELECTOR, "maxItems": 500},
                "shape": {"type": "string", "enum": ["circle", "square", "cube"], "default": "circle"},
                "size": {"type": "number", "exclusiveMinimum": 0, "default": 1},
                "color": {"type": "integer", "minimum": 0, "maximum": 31},
                "suffix": {"type": "string", "default": "_CTRL"},
                "constraint": {"type": "string", "enum": ["none", "parent", "orient", "point"], "default": "none"},
                "maintain_offset": {"type": "boolean", "default": True},
                "parent_hierarchy": {"type": "boolean", "default": False},
            },
            ["action"],
        ),
        read_only=False,
    ),
    _tool(
        "maya.rig.preview",
        "Preview and Accept Rig Placement",
        "Create, revise, inspect, list, accept, or cancel vision-friendly joint "
        "and control placement previews. Preview nodes are doNotWrite and stay "
        "outside Maya undo, but live nodes may mark the scene dirty. Accept "
        "preflights names and commits one named undo chunk.",
        _object(
            {
                "action": {
                    "type": "string",
                    "enum": [
                        "create",
                        "update",
                        "query",
                        "list",
                        "accept",
                        "cancel",
                    ],
                },
                "handle": RIG_PREVIEW_HANDLE,
                "name": {"type": "string", "minLength": 1, "maxLength": 128},
                "joints": {
                    "type": "array",
                    "items": RIG_PREVIEW_JOINT,
                    "maxItems": 500,
                },
                "controls": {
                    "type": "array",
                    "items": RIG_PREVIEW_CONTROL,
                    "maxItems": 500,
                },
                "parent": NODE_SELECTOR,
                "orient": {"type": "boolean", "default": True},
                "primary_axis": {
                    "type": "string",
                    "enum": ["xyz", "xzy", "yxz", "yzx", "zxy", "zyx"],
                    "default": "xyz",
                },
                "secondary_axis": {
                    "type": "string",
                    "enum": [
                        "xup",
                        "xdown",
                        "yup",
                        "ydown",
                        "zup",
                        "zdown",
                    ],
                    "default": "yup",
                },
                "joint_color": RGB3,
                "bone_color": RGB3,
                "control_color": RGB3,
                "if_scene_revision": {
                    "type": "integer",
                    "minimum": 0,
                },
            },
            ["action"],
        ),
        read_only=False,
    ),
    _tool(
        "maya.rig.skin",
        "Bind or Inspect Skin",
        "Bind geometry to joint influences with explicit weighting options, or "
        "inspect skinCluster influences and vertex counts.",
        _object(
            {
                "action": {"type": "string", "enum": ["bind", "inspect", "unbind"]},
                "geometry": {"type": "array", "items": NODE_SELECTOR, "maxItems": 100},
                "influences": {"type": "array", "items": NODE_SELECTOR, "maxItems": 500},
                "max_influences": {"type": "integer", "minimum": 1, "maximum": 32, "default": 4},
                "dropoff_rate": {"type": "number", "minimum": 0.1, "maximum": 10, "default": 4},
                "normalize": {"type": "boolean", "default": True},
            },
            ["action", "geometry"],
        ),
        read_only=False,
        destructive=True,
    ),
    _tool(
        "maya.viewport.capture",
        "Capture Maya Viewport",
        "Capture the active Viewport 2.0 color image as MCP ImageContent and "
        "return camera, matrices, resolution, time, selection, and projected "
        "joints. Optional experimental VP2 depth is bounded raw data with "
        "renderer-native row metadata and is not yet pixel-correlated to color; "
        "object-ID capture is not yet supported. Width and height are capped at "
        "2048 pixels and encoded color output at 8 MiB.",
        _object(
            {
                "width": {"type": "integer", "minimum": 64, "maximum": 2048},
                "height": {"type": "integer", "minimum": 64, "maximum": 2048},
                "format": {"type": "string", "enum": ["png", "jpg"], "default": "png"},
                "include_joint_projections": {"type": "boolean", "default": True},
                "include_depth": {"type": "boolean", "default": False},
                "depth_max_dimension": {
                    "type": "integer",
                    "minimum": 64,
                    "maximum": 1024,
                    "default": 512,
                },
            }
        ),
        read_only=True,
        idempotent=True,
    ),
    _tool(
        "maya.viewport.scene_map",
        "Map Scene Objects into the Viewport",
        "Return conservative projected world-AABB boxes, canonical Maya node "
        "references, and pivots for viewport grounding. This does not test "
        "occlusion or produce segmentation masks.",
        _object(
            {
                "width": {"type": "integer", "minimum": 64, "maximum": 4096},
                "height": {"type": "integer", "minimum": 64, "maximum": 4096},
                "nodes": {
                    "type": "array",
                    "items": NODE_SELECTOR,
                    "maxItems": 500,
                },
                "node_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 32,
                },
                "include_hidden": {"type": "boolean", "default": False},
                "max_nodes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "default": 250,
                },
                "max_candidates": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5000,
                    "default": 1000,
                },
            }
        ),
        read_only=True,
        idempotent=True,
    ),
    _tool(
        "maya.viewport.project",
        "Project World Points",
        "Project world-space points or node pivots into active-viewport pixel "
        "coordinates, or return world rays for screen pixels.",
        _object(
            {
                "world_points": {
                    "type": "array",
                    "items": VECTOR3,
                    "maxItems": 1000,
                },
                "nodes": {
                    "type": "array",
                    "items": NODE_SELECTOR,
                    "maxItems": 500,
                },
                "screen_points": {
                    "type": "array",
                    "maxItems": 1000,
                    "items": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 2,
                        "maxItems": 2,
                    },
                },
            }
        ),
        read_only=True,
        idempotent=True,
    ),
    _tool(
        "maya.viewport.pick",
        "Pick Viewport Pixel",
        "Pick the Maya node or component under a viewport pixel and return the "
        "corresponding world ray. Maya selection is restored, but selection "
        "callbacks run during the temporary pick.",
        _object(
            {
                "x": {"type": "integer", "minimum": 0},
                "y": {"type": "integer", "minimum": 0},
                "radius": {"type": "integer", "minimum": 0, "maximum": 64, "default": 2},
            },
            ["x", "y"],
        ),
        read_only=False,
    ),
    _tool(
        "maya.file.apply",
        "Apply Scene File Operation",
        "Query, save, save-as, open, import, reference, or export selection. "
        "Open/new require an explicit dirty-scene policy.",
        _object(
            {
                "action": {"type": "string", "enum": ["query", "save", "save_as", "open", "import", "reference", "export_selection"]},
                "path": {"type": "string"},
                "file_type": {"type": "string"},
                "namespace": {"type": "string"},
                "dirty_policy": {"type": "string", "enum": ["error", "save", "discard"], "default": "error"},
                "force": {"type": "boolean", "default": False},
            },
            ["action"],
        ),
        read_only=False,
        destructive=True,
        open_world=True,
    ),
    _tool(
        "maya.script.execute",
        "Execute Python or MEL",
        "Unsafe escape hatch for capabilities not yet represented by typed tools. "
        "Runs with the user's full Maya privileges, is not sandboxed or safely "
        "interruptible. It is enabled by default for the authenticated local "
        "client and can be disabled from Maya's Maya MCP menu or with "
        "MAYA_MCP_ALLOW_UNSAFE_CODE=0.",
        _object(
            {
                "language": {"type": "string", "enum": ["python", "mel"]},
                "source": {"type": "string", "minLength": 1, "maxLength": 1000000},
                "return_expression": {"type": "string"},
                "arguments": {"type": "object", "description": "JSON values exposed as arguments in Python; avoids interpolating data into source."},
                "session_id": {"type": "string", "minLength": 1, "maxLength": 128},
                "request_id": REQUEST_ID,
                "observe": OBSERVATION_INPUT,
                "if_observation": {"type": "string", "maxLength": 128},
                "undo": {"type": "string", "enum": ["none", "chunk"], "default": "none"},
                "label": {"type": "string", "maxLength": 120},
            },
            ["language", "source"],
        ),
        read_only=False,
        destructive=True,
        open_world=True,
    ),
]


TOOLS.extend([
    _tool(
        "maya.tools.describe",
        "Discover Maya Operations",
        "List available operations by query, or fetch exact input schemas by names. "
        "Call unlisted operations through maya.workflow.run. Empty input lists compact summaries.",
        _object({
            "query": {"type": "string", "maxLength": 200},
            "names": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8},
        }),
        read_only=True,
        idempotent=True,
    ),
    _tool(
        "maya.workflow.run",
        "Run a Maya Workflow",
        "Run up to 32 ordered tool steps in one main-thread dispatch. Discover schemas "
        "with maya.tools.describe. Arguments may reference earlier structured results: "
        '{"$ref":"create#/data/transform"}. Stops on first failure; completed steps remain '
        "applied. Maya groups recorded edits into one request-level undo item. "
        "This is not an atomic transaction. "
        "Use select to return only needed JSON Pointer fields. Capture images remain MCP images.",
        _object({
            "steps": {
                "type": "array", "minItems": 1, "maxItems": 32,
                "items": _object({
                    "id": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_]{0,63}$"},
                    "tool": {"type": "string"},
                    "arguments": {"type": "object"},
                    "select": {"type": "object", "maxProperties": 32,
                               "additionalProperties": {"type": "string", "maxLength": 512},
                               "description": "Output name to JSON Pointer into the step's structured result. Empty object suppresses data."},
                }, ["id", "tool"]),
            },
            "if_scene_epoch": {"type": "string"},
            "if_scene_revision": {"type": "integer", "minimum": 0},
            "request_id": REQUEST_ID,
            "observe": {**OBSERVATION_INPUT, "description": "Post-action observation. May reference earlier workflow steps using $ref. Returned even after a step fails; failure to observe never reruns edits."},
            "if_observation": {"type": "string", "maxLength": 128},
        }, ["steps"]),
        read_only=False,
        destructive=True,
        open_world=True,
    ),
])


TOOLS.extend([
    _tool("maya.observe", "Observe Maya", "Return context, scoped node facts, viewport image and scene map in one dispatch. Includes an observation ID and bounded change cursor; flags detectable changes during capture. Scoped observers are not a complete scene snapshot.",
          OBSERVATION_INPUT, read_only=True, idempotent=True),
    _tool("maya.scene.changes", "Read Maya Changes", "Poll bounded scoped change hints since a cursor. Overflow, reset or lost coverage requires a fresh observation. Attribute coverage is limited to watched nodes, excluding evaluated/playback changes.",
          _object({"cursor": {"type": "string", "maxLength": 2048}, "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 200}}), read_only=True, idempotent=True),
    _tool("maya.session", "Manage Python Session", "Open, inspect, reset or close a persistent Python namespace owned by this MCP client. Pass session_id to script.execute. Scene replacement invalidates sessions and handles. Python retains full Maya privileges.",
          _object({"action": {"type": "string", "enum": ["open", "status", "reset", "close"]}, "session_id": {"type": "string", "minLength": 1, "maxLength": 128}}, ["action"]), read_only=False),
    _tool("maya.request.status", "Recover Maya Request", "Read native queued/running/completed/failed status for a request_id in this MCP session. Completed results are bounded and may expire; never resubmit an expired ID as new work.",
          _object({"request_id": REQUEST_ID}, ["request_id"]), read_only=True, idempotent=True),
    _tool("maya.viewport.frame", "Frame Maya Nodes", "Fit the active camera to explicit nodes without changing selection. Returns camera identity; use workflow observe for the refreshed image.",
          _object({"nodes": {"type": "array", "items": NODE_SELECTOR, "minItems": 1, "maxItems": 100}, "fit_factor": {"type": "number", "minimum": 0.1, "maximum": 1, "default": 0.8}}, ["nodes"]), read_only=False),
])


from .animation_schema import definitions as animation_definitions

TOOLS.extend(animation_definitions(_tool, _object, NODE_SELECTOR, VECTOR3))


RESOURCES = [
    {"uri": "maya://context", "name": "Maya Context", "description": "Current scene, time, selection, units, and viewport context.", "mimeType": "application/json"},
    {"uri": "maya://scene/summary", "name": "Scene Summary", "description": "Bounded DAG, node-type, reference, and scene-state summary.", "mimeType": "application/json"},
    {"uri": "maya://selection", "name": "Selection", "description": "Canonical references for the active Maya selection.", "mimeType": "application/json"},
    {"uri": "maya://timeline", "name": "Timeline", "description": "Playback range, animation range, current time, and key summary.", "mimeType": "application/json"},
]


PROMPTS = [
    {
        "name": "maya.viewport.inspect",
        "title": "Inspect the Maya Viewport",
        "description": "Visually inspect the current viewport and correlate it with scene structure.",
        "arguments": [{"name": "goal", "description": "What to inspect or diagnose.", "required": True}],
        "_message": "Inspect Maya for this goal: {{goal}}. Discover scene_map and pick schemas with maya.tools.describe. Combine context, viewport.capture and viewport.scene_map in maya.workflow.run, using the same image resolution. Request depth only when needed for the goal. Ground color with projected boxes and canonical nodes; treat depth row orientation as experimental, confirm ambiguous overlaps with a pick step, and report uncertainty before making edits.",
    },
    {
        "name": "maya.rig.from_landmarks",
        "title": "Build a Rig from Visual Landmarks",
        "description": "Plan and build a joint/control setup from visible landmarks.",
        "arguments": [{"name": "goal", "description": "Rig type and desired behavior.", "required": True}],
        "_message": "Build this rig: {{goal}}. Discover rig and viewport schemas with maya.tools.describe and invoke them as maya.workflow.run steps. Inspect the mesh and existing rig, capture useful views, and ground landmarks with scene_map and pick. Request depth only when needed. Create the proposed joints and controls with maya.rig.preview, review and update the transient preview visually, then call accept with the latest handle and scene revision only after the layout is approved. Skin only after acceptance; keep permanent edits undoable and use explicit names.",
    },
    {
        "name": "maya.scene.audit",
        "title": "Audit the Maya Scene",
        "description": "Perform a structured, non-destructive scene review.",
        "arguments": [{"name": "focus", "description": "Optional audit focus.", "required": False}],
        "_message": "Audit the current Maya scene with focus: {{focus}}. Use read-only queries, identify concrete evidence and canonical targets, and propose fixes without applying them.",
    },
]


CATALOG = {
    "instructions": (
        "Start with maya.observe for a viewport and scoped scene facts. Request observe on "
        "workflow.run or script.execute to receive post-action verification in the same request. "
        "Use if_observation to reject detectably stale input. Open maya.session for persistent "
        "Python variables and the maya.call/observe/node/keep/get SDK. Poll scene.changes for "
        "scoped change hints; refresh observation after coverage gaps. Assign request_id to "
        "workflow/script calls for native queued execution and recover via maya.request.status; "
        "keep the same MCP session. Expired results do not authorize repeating mutations. "
        "Discover operation schemas with maya.tools.describe; "
        "all operations, including those absent from tools/list, are available as steps in "
        "maya.workflow.run. Combine related reads, edits and verification in one workflow; "
        "pass canonical nodes between steps with $ref and select only needed outputs. "
        "Workflows stop on error and retain completed edits; each edit has its own undo policy. "
        "Use maya.node.apply inside a step for transactional graph edits. For general Maya "
        "Python/MEL work, use maya.script.execute with JSON arguments and a bounded result. "
        "Verify edits structurally and visually. Never blindly retry a timed-out mutation."
    ),
    "tools": TOOLS,
    "resources": RESOURCES,
    "prompts": PROMPTS,
}


def catalog_json() -> str:
    # Keep one complete registry internally. The compact startup profile changes
    # discovery only; workflow dispatch still validates against every schema.
    profile = os.getenv("MAYA_MCP_TOOL_PROFILE", "compact").lower()
    if profile not in {"compact", "full"}:
        raise ValueError("MAYA_MCP_TOOL_PROFILE must be compact or full")
    compact_names = {
        "maya.observe", "maya.scene.query", "maya.scene.changes", "maya.session",
        "maya.tools.describe", "maya.workflow.run", "maya.script.execute", "maya.request.status",
    }
    catalog = {**CATALOG, "tools": [
        tool for tool in TOOLS if profile == "full" or tool["name"] in compact_names
    ]}
    return json.dumps(catalog, ensure_ascii=True, separators=(",", ":"))
