"""End-to-end Maya standalone smoke test for the native MCP transport."""

from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import maya.standalone

maya.standalone.initialize(name="python")

import maya.cmds as cmds


def fail(message: str) -> None:
    raise RuntimeError(message)


cmds.loadPlugin("maya_mcp")
import maya_mcp_runtime

plugin_path = Path(
    cmds.pluginInfo("maya_mcp", query=True, path=True)
).resolve()
packaged_scripts = (plugin_path.parent.parent / "scripts").resolve()
runtime_path = Path(maya_mcp_runtime.__file__).resolve()
if packaged_scripts not in runtime_path.parents:
    fail(
        "Maya MCP imported runtime code outside the built package: "
        f"{runtime_path} (expected below {packaged_scripts})"
    )
status = json.loads(cmds.mayaMcpStatus())
if not status["running"]:
    fail(f"Maya MCP did not start: {status}")
if status["version"] != "0.5.1":
    fail(f"Unexpected Maya MCP version: {status['version']}")

with open(status["discoveryFile"], "r", encoding="utf-8") as stream:
    discovery = json.load(stream)

endpoint = discovery["url"]
token = discovery["token"]
session_id: str | None = None
if len(token) != 64:
    fail("Discovery token is not a 256-bit hexadecimal secret")


def expect_http_error(
    expected_status: int,
    *,
    authorization: str | None,
    origin: str | None,
    method: str = "POST",
) -> None:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if authorization:
        headers["Authorization"] = authorization
    if origin:
        headers["Origin"] = origin
    request = urllib.request.Request(
        endpoint,
        data=b"{}" if method == "POST" else None,
        headers=headers,
        method=method,
    )
    try:
        urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as error:
        if error.code != expected_status:
            fail(f"Expected HTTP {expected_status}, received {error.code}")
    else:
        fail(f"Expected HTTP {expected_status}, request unexpectedly succeeded")


expect_http_error(401, authorization=None, origin="http://localhost")
expect_http_error(
    401,
    authorization="Bearer incorrect",
    origin="http://localhost",
)
expect_http_error(
    403,
    authorization=f"Bearer {token}",
    origin="https://attacker.example",
)
expect_http_error(
    405,
    authorization=f"Bearer {token}",
    origin="http://localhost",
    method="GET",
)


def post(payload: dict, *, session: str | None = None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Origin": "http://localhost",
    }
    if session:
        headers["MCP-Session-Id"] = session
        headers["MCP-Protocol-Version"] = "2025-11-25"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        parsed = json.loads(body) if body else None
        return response.status, response.headers, parsed


http_status, headers, initialized = post(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "maya-mcp-smoke", "version": "1.0"},
        },
    }
)
if http_status != 200 or initialized["result"]["protocolVersion"] != "2025-11-25":
    fail(f"Initialize failed: {initialized}")
session_id = headers.get("MCP-Session-Id")
if not session_id:
    fail("Initialize response did not include MCP-Session-Id")

post(
    {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    },
    session=session_id,
)

_, _, tools_response = post(
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    session=session_id,
)
tool_names = {tool["name"] for tool in tools_response["result"]["tools"]}
required_tools = {
    "maya.context.get",
    "maya.scene.query",
    "maya.node.apply",
    "maya.viewport.capture",
    "maya.viewport.scene_map",
    "maya.rig.preview",
    "maya.rig.skeleton",
    "maya.script.execute",
}
if not required_tools.issubset(tool_names):
    fail(f"Tool catalog is missing entries: {required_tools - tool_names}")

_, _, resources_response = post(
    {"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}},
    session=session_id,
)
resource_uris = {
    resource["uri"] for resource in resources_response["result"]["resources"]
}
if "maya://context" not in resource_uris:
    fail("Context resource is missing")


def call_tool(
    request_id: int,
    name: str,
    arguments: dict,
    *,
    expect_error_code: str | None = None,
):
    result: dict = {}
    error: list[BaseException] = []

    def worker() -> None:
        try:
            _, _, response = post(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
                session=session_id,
            )
            result["response"] = response
        except BaseException as exception:
            error.append(exception)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30.0
    while thread.is_alive() and time.monotonic() < deadline:
        cmds.mayaMcpPump()
        time.sleep(0.005)
    thread.join(timeout=1.0)
    if thread.is_alive():
        fail(f"Timed out calling {name}")
    if error:
        raise error[0]
    response = result["response"]
    if "error" in response:
        fail(f"{name} returned a JSON-RPC error: {response}")
    tool_result = response["result"]
    if expect_error_code:
        if not tool_result.get("isError"):
            fail(f"{name} should have failed with {expect_error_code}: {tool_result}")
        actual_code = tool_result["structuredContent"]["error"]["code"]
        if actual_code != expect_error_code:
            fail(f"{name} failed with {actual_code}, expected {expect_error_code}")
        return tool_result["structuredContent"]
    if tool_result.get("isError"):
        fail(f"{name} failed: {tool_result}")
    return tool_result["structuredContent"]


context_result = call_tool(10, "maya.context.get", {})
if not context_result["ok"] or context_result["data"]["maya"]["batch"] is not True:
    fail(f"Context tool returned invalid data: {context_result}")
call_tool(
    21,
    "maya.context.get",
    {"unexpected": True},
    expect_error_code="INVALID_ARGUMENT",
)
call_tool(
    22,
    "maya.viewport.capture",
    {},
    expect_error_code="VIEWPORT_UNAVAILABLE",
)
call_tool(
    23,
    "maya.script.execute",
    {"language": "python", "source": "result = 1"},
    expect_error_code="CAPABILITY_DISABLED",
)
os.environ["MAYA_MCP_ALLOW_UNSAFE_CODE"] = "1"
call_tool(
    24,
    "maya.script.execute",
    {
        "language": "python",
        "source": (
            "try:\n"
            "    cmds.mayaMcpStop()\n"
            "except RuntimeError as error:\n"
            "    result = str(error)\n"
        ),
    },
)

failed_script = call_tool(
    27,
    "maya.script.execute",
    {
        "language": "python",
        "undo": "chunk",
        "label": "Maya MCP failing script probe",
        "source": (
            "cmds.createNode('transform', name='partialScriptMutation')\n"
            "raise RuntimeError('intentional script failure')\n"
        ),
    },
    expect_error_code="SCRIPT_ERROR",
)
if not cmds.objExists("partialScriptMutation"):
    fail("The failing-script probe did not exercise a partial scene mutation")
if (
    failed_script["revisions"]["scene_after"]
    <= failed_script["revisions"]["scene_before"]
):
    fail("A failing script did not advance the conservative scene revision")
if not any(
    warning["code"] == "PARTIAL_SCRIPT_MUTATION_POSSIBLE"
    for warning in failed_script["warnings"]
):
    fail("A failing script did not report possible partial mutation")
# The native bridge must opt into Maya undo even when a batch test manually
# pumps the queue through a non-undoable MPxCommand.
if failed_script["undo"] != {
    "available": True,
    "label": "Maya MCP failing script probe",
}:
    fail(f"The manually pumped script hid its recovery chunk: {failed_script}")
cmds.undo()
if cmds.objExists("partialScriptMutation"):
    fail("The manually pumped failing-script chunk did not undo its mutation")

# Native tool dispatch explicitly opts into Maya undo recording and must
# preserve a real recovery chunk for a failing script.
from maya_mcp_runtime.dispatcher import dispatch_base64

direct_payload = {
    "name": "maya.script.execute",
    "arguments": {
        "language": "python",
        "undo": "chunk",
        "label": "Maya MCP direct failing script probe",
        "source": (
            "cmds.createNode('transform', name='directPartialScriptMutation')\n"
            "raise RuntimeError('intentional direct script failure')\n"
        ),
    },
}
direct_encoded = base64.b64encode(
    json.dumps(direct_payload).encode("utf-8")
).decode("ascii")
direct_failure = json.loads(dispatch_base64(direct_encoded))["structuredContent"]
if direct_failure["error"]["code"] != "SCRIPT_ERROR":
    fail(f"Direct failing script returned the wrong error: {direct_failure}")
if direct_failure["undo"] != {
    "available": True,
    "label": "Maya MCP direct failing script probe",
}:
    fail(f"Direct failing script hid its recovery chunk: {direct_failure}")
cmds.undo()
if cmds.objExists("directPartialScriptMutation"):
    fail("The direct failing-script recovery chunk did not undo its mutation")

bounded_script = call_tool(
    28,
    "maya.script.execute",
    {
        "language": "python",
        "source": "print('x' * 400000)\nresult = list(range(10000))",
    },
)
if len(bounded_script["data"]["stdout"]) > 262144:
    fail("Script stdout exceeded its retained-output budget")
if not bounded_script["data"]["output_truncated"]:
    fail("Script stdout truncation was not reported")
if not bounded_script["data"]["result_truncated"]:
    fail("Large script result truncation was not reported")
os.environ.pop("MAYA_MCP_ALLOW_UNSAFE_CODE", None)
if not json.loads(cmds.mayaMcpStatus())["running"]:
    fail("A nested script call was able to stop the MCP server")

create_result = call_tool(
    11,
    "maya.node.apply",
    {
        "label": "Maya MCP smoke create",
        "operations": [
            {
                "id": "probe",
                "op": "create",
                "node_type": "transform",
                "name": "mayaMcpSmokeProbeOriginal",
            },
            {"op": "rename", "node": "$probe", "name": "mayaMcpSmokeProbe"},
            {
                "op": "set_transform",
                "node": "$probe",
                "translate": [1.0, 2.0, 3.0],
                "space": "world",
            },
        ],
    },
)
probe = create_result["data"]["aliases"]["probe"]
if not cmds.objExists(probe["long_name"]):
    fail("Node transaction did not create its probe")

query_result = call_tool(
    12,
    "maya.scene.query",
    {
        "scope": "nodes",
        "nodes": [probe],
        "include_attributes": ["translate"],
    },
)
if query_result["data"]["count"] != 1:
    fail(f"Scene query did not resolve node_id: {query_result}")
call_tool(
    25,
    "maya.animation.apply",
    {
        "action": "set_keys",
        "targets": [probe],
        "attributes": ["translateX"],
        "keys": [],
    },
    expect_error_code="INVALID_ARGUMENT",
)
if not cmds.objExists(probe["long_name"]):
    fail("A failed empty animation transaction undid the prior user edit")

call_tool(
    13,
    "maya.node.apply",
    {
        "label": "Maya MCP smoke cleanup",
        "operations": [{"op": "delete", "node": {"node_id": probe["node_id"]}}],
    },
)
if cmds.objExists("mayaMcpSmokeProbe"):
    fail("Node transaction cleanup failed")
cmds.createNode("transform", name="mayaMcpSmokeProbe")
call_tool(
    26,
    "maya.scene.query",
    {"scope": "nodes", "nodes": [probe]},
    expect_error_code="STALE_NODE_ID",
)
cmds.delete("mayaMcpSmokeProbe")

mesh_result = call_tool(
    14,
    "maya.geometry.apply",
    {
        "kind": "cube",
        "name": "mayaMcpSmokeMesh",
        "dimensions": {"width": 2.0, "height": 4.0, "depth": 1.0},
    },
)
mesh = mesh_result["data"]["transform"]
mesh_component = dict(mesh)
mesh_component["component"] = f"{mesh['long_name']}.f[0:5]"
call_tool(
    60,
    "maya.node.apply",
    {"operations": [{"op": "delete", "node": mesh_component}]},
    expect_error_code="INVALID_ARGUMENT",
)
if not cmds.objExists(mesh["long_name"]):
    fail("A node-only operation widened a face selector and deleted its mesh")
component_selection = call_tool(
    61,
    "maya.selection.set",
    {"mode": "replace", "items": [mesh_component]},
)
if component_selection["data"]["returned"] != 1:
    fail(f"Compact component selection was not preserved: {component_selection}")
if not component_selection["data"]["selection"][0].get("component"):
    fail("Component selection did not return a canonical component reference")
material_result = call_tool(
    62,
    "maya.material.apply",
    {
        "action": "create_assign",
        "name": "mayaMcpSmokeMaterial",
        "shader_type": "lambert",
        "targets": [mesh_component],
        "base_color": [0.2, 0.4, 0.8],
    },
)
assigned_targets = material_result["changes"][-1]["targets"]
if not assigned_targets[0].get("component"):
    fail("Material assignment widened its component result to the whole mesh")
call_tool(63, "maya.selection.set", {"mode": "clear"})
skeleton_result = call_tool(
    15,
    "maya.rig.skeleton",
    {
        "action": "create_chain",
        "joints": [
            {"name": "mayaMcpSmoke_root_JNT", "position": [0.0, -2.0, 0.0]},
            {"name": "mayaMcpSmoke_mid_JNT", "position": [0.0, 0.0, 0.0]},
            {"name": "mayaMcpSmoke_end_JNT", "position": [0.0, 2.0, 0.0]},
        ],
        "primary_axis": "xyz",
        "secondary_axis": "yup",
        "orient": True,
    },
)
joints = skeleton_result["data"]["joints"]
controls_result = call_tool(
    16,
    "maya.rig.controls",
    {
        "action": "create",
        "targets": [{"node_id": joints[0]["node_id"]}],
        "shape": "circle",
        "size": 1.5,
        "color": 17,
        "constraint": "none",
    },
)
control_group = controls_result["data"]["controls"][0]["offset_group"]
skin_result = call_tool(
    17,
    "maya.rig.skin",
    {
        "action": "bind",
        "geometry": [{"node_id": mesh["node_id"]}],
        "influences": [{"node_id": joint["node_id"]} for joint in joints],
        "max_influences": 3,
        "normalize": True,
    },
)
if not skin_result["data"]["clusters"]:
    fail("Rig skin tool did not create a skinCluster")
skin_inspect = call_tool(
    18,
    "maya.rig.skin",
    {"action": "inspect", "geometry": [{"node_id": mesh["node_id"]}]},
)
if not skin_inspect["data"]["geometry"][0]["skin_clusters"]:
    fail("Rig skin inspection did not find the created skinCluster")
call_tool(
    19,
    "maya.node.apply",
    {
        "label": "Maya MCP domain cleanup",
        "operations": [
            {"op": "delete", "node": {"node_id": mesh["node_id"]}},
            {"op": "delete", "node": {"node_id": joints[0]["node_id"]}},
            {"op": "delete", "node": {"node_id": control_group["node_id"]}},
            {
                "op": "delete",
                "node": {
                    "node_id": material_result["data"]["shading_groups"][0][
                        "node_id"
                    ]
                },
            },
            {
                "op": "delete",
                "node": {
                    "node_id": material_result["data"]["material"]["node_id"]
                },
            },
        ],
    },
)

_, _, prompt_response = post(
    {
        "jsonrpc": "2.0",
        "id": 30,
        "method": "prompts/get",
        "params": {
            "name": "maya.rig.from_landmarks",
            "arguments": {"goal": "a test arm"},
        },
    },
    session=session_id,
)
if "a test arm" not in prompt_response["result"]["messages"][0]["content"]["text"]:
    fail("Prompt argument substitution failed")

epoch_result = call_tool(
    31,
    "maya.node.apply",
    {
        "operations": [
            {
                "id": "epoch_probe",
                "op": "create",
                "node_type": "transform",
                "name": "mayaMcpEpochProbe",
            }
        ]
    },
)
old_epoch_ref = epoch_result["data"]["aliases"]["epoch_probe"]
old_epoch = old_epoch_ref["scene_epoch"]
cmds.file(new=True, force=True)
new_context = call_tool(32, "maya.context.get", {})
if new_context["scene_epoch"] == old_epoch:
    fail("Scene epoch did not change after creating a new Maya scene")
call_tool(
    33,
    "maya.scene.query",
    {"scope": "nodes", "nodes": [old_epoch_ref]},
    expect_error_code="SCENE_EPOCH_MISMATCH",
)

print(
    "MAYA_MCP_TEST_RESULT="
    + json.dumps(
        {
            "version": status["version"],
            "protocol": initialized["result"]["protocolVersion"],
            "tools": len(tool_names),
            "resources": len(resource_uris),
            "typed_mutation": "passed",
            "rigging_pipeline": "passed",
            "security_checks": "passed",
        },
        sort_keys=True,
    )
)

cmds.unloadPlugin("maya_mcp")
maya.standalone.uninitialize()
