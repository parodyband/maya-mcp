from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


TOKEN = "a" * 64
SESSION = "bridge-test-session"
PROTOCOL = "2025-11-25"


class McpHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, object]] = []

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        message = json.loads(body)
        record = {
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "accept": self.headers.get("Accept"),
            "protocol": self.headers.get("MCP-Protocol-Version"),
            "session": self.headers.get("MCP-Session-Id"),
            "message": message,
        }
        type(self).requests.append(record)

        if record["authorization"] != f"Bearer {TOKEN}" or self.path != "/mcp":
            self.send_response(401)
            self.end_headers()
            return

        method = message["method"]
        if method == "initialize":
            response = {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {
                    "protocolVersion": PROTOCOL,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "maya-mcp-test", "version": "test"},
                },
            }
            payload = json.dumps(response, separators=(",", ":")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("MCP-Session-Id", SESSION)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if record["session"] != SESSION:
            self.send_response(404)
            self.end_headers()
            return
        if method == "notifications/initialized":
            self.send_response(202)
            self.end_headers()
            return

        response = {
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {"tools": [{"name": "maya.context.get"}]},
        }
        payload = json.dumps(response, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def run_command(
    command: list[str],
    messages: list[dict[str, object]],
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    input_text = "".join(json.dumps(message, separators=(",", ":")) + "\n" for message in messages)
    return subprocess.run(
        command,
        input=input_text,
        text=True,
        capture_output=True,
        env=environment,
        timeout=15,
        check=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("bridge", type=Path)
    parser.add_argument("--launcher", type=Path)
    args = parser.parse_args()
    bridge = args.bridge.resolve()
    if not bridge.is_file():
        raise AssertionError(f"Bridge executable not found: {bridge}")

    server = ThreadingHTTPServer(("127.0.0.1", 0), McpHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="maya-mcp-bridge-test-") as folder:
            root = Path(folder)
            discovery = root / "current.json"
            discovery.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "pid": os.getpid(),
                        "url": f"http://127.0.0.1:{server.server_port}/mcp",
                        "token": TOKEN,
                        "protocolVersion": PROTOCOL,
                        "pluginVersion": "0.5.3",
                    }
                ),
                encoding="utf-8",
            )
            messages: list[dict[str, object]] = [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": PROTOCOL,
                        "capabilities": {},
                        "clientInfo": {"name": "bridge-test", "version": "1"},
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                {"jsonrpc": "2.0", "id": "tools", "method": "tools/list", "params": {}},
            ]
            result = run_command(
                [str(bridge), "--discovery-file", str(discovery)], messages
            )
            if result.returncode != 0:
                raise AssertionError(f"Bridge failed ({result.returncode}): {result.stderr}")
            output = [json.loads(line) for line in result.stdout.splitlines() if line]
            assert len(output) == 2, output
            assert output[0]["id"] == 1
            assert output[1]["id"] == "tools"
            assert output[1]["result"]["tools"][0]["name"] == "maya.context.get"
            assert not result.stderr, result.stderr

            assert len(McpHandler.requests) == 3
            assert all(item["protocol"] == PROTOCOL for item in McpHandler.requests)
            assert McpHandler.requests[0]["session"] is None
            assert McpHandler.requests[1]["session"] == SESSION
            assert McpHandler.requests[2]["session"] == SESSION
            assert all(item["accept"] == "application/json, text/event-stream" for item in McpHandler.requests)

            unsafe = root / "unsafe.json"
            unsafe.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "pid": os.getpid(),
                        "url": f"http://0.0.0.0:{server.server_port}/mcp",
                        "token": TOKEN,
                        "protocolVersion": PROTOCOL,
                    }
                ),
                encoding="utf-8",
            )
            rejected = run_command(
                [str(bridge), "--discovery-file", str(unsafe)], [messages[0]]
            )
            rejected_output = [json.loads(line) for line in rejected.stdout.splitlines() if line]
            assert rejected.returncode == 0
            assert len(rejected_output) == 1
            assert rejected_output[0]["error"]["code"] == -32000
            assert "approved loopback" in rejected_output[0]["error"]["message"]

            if args.launcher:
                launcher = args.launcher.resolve()
                if not launcher.is_file():
                    raise AssertionError(f"Stable launcher not found: {launcher}")
                powershell = shutil.which("powershell.exe")
                if not powershell:
                    raise AssertionError("powershell.exe is required for the launcher test")
                local_app_data = root / "local-app-data"
                client_root = local_app_data / "MayaMCP" / "client"
                client_root.mkdir(parents=True)
                installed_launcher = client_root / "Start-MayaMcpBridge.ps1"
                shutil.copy2(launcher, installed_launcher)
                (local_app_data / "MayaMCP" / "current.json").write_text(
                    discovery.read_text(encoding="utf-8"), encoding="utf-8"
                )
                (client_root / "bridge-installations.json").write_text(
                    json.dumps(
                        {
                            "schema_version": 1,
                            "installations": [
                                {"version": "0.5.3", "path": str(bridge)}
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                launcher_environment = os.environ.copy()
                launcher_environment["LOCALAPPDATA"] = str(local_app_data)
                McpHandler.requests.clear()
                launched = run_command(
                    [
                        powershell,
                        "-NoLogo",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(installed_launcher),
                    ],
                    messages,
                    launcher_environment,
                )
                if launched.returncode != 0:
                    raise AssertionError(
                        f"Stable launcher failed ({launched.returncode}): {launched.stderr}"
                    )
                launched_output = [
                    json.loads(line) for line in launched.stdout.splitlines() if line
                ]
                assert len(launched_output) == 2, launched.stdout
                assert launched_output[1]["id"] == "tools"
                assert len(McpHandler.requests) == 3

        version = subprocess.run(
            [str(bridge), "--version"],
            text=True,
            capture_output=True,
            timeout=5,
            check=True,
        )
        assert version.stdout.startswith("maya-mcp-bridge ")
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)

    print(
        "MAYA_MCP_BRIDGE_TEST_RESULT=passed "
        f"auth=true session=true loopback=true launcher={str(bool(args.launcher)).lower()}"
    )


if __name__ == "__main__":
    main()
