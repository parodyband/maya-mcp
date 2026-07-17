# Security Model

Maya MCP can change scenes, read files, and optionally execute arbitrary code
with the same operating-system privileges as Maya. Treat the endpoint like a
local developer shell with a typed API in front of it.

## Default trust boundary

The supported deployment is one trusted user, one local Maya process, and a
trusted MCP client on the same Windows account.

Remote or LAN binding is not implemented. Do not proxy this endpoint to another
machine.

## Controls already enforced

- The listener binds only to 127.0.0.1.
- Every MCP request requires Authorization: Bearer TOKEN.
- Tokens use 256 bits from Windows BCryptGenRandom unless explicitly supplied.
- Session IDs use 192 random bits and are not treated as authentication.
- Browser Origin values must resolve to localhost, 127.0.0.1, or ::1.
- HTTP request payloads are capped at 8 MiB.
- Every serialized tool or resource result has a final 16 MiB native-bridge
  ceiling; typed tools apply tighter field and work limits before invoking Maya.
- The HTTP worker pool has four workers and a 64-request queue.
- The Maya dispatcher has a 256-request queue.
- Sessions expire after two idle hours and are capped at 128 per Maya process.
- Tool arguments pass strict schemas before execution.
- Query result counts, projection arrays, script size, capture response size,
  and script output are bounded.
- Color capture is capped at 2,048 pixels per axis and 8 MiB of base64; native
  depth is capped at 1,024 pixels on its largest sampled axis and 4 MiB base64.
- Rig previews are capped at 16 active records and 8,192 owned nodes.
- Tokens are not returned by the Maya status command.
- Discovery files live below the user's LocalAppData directory.
- Python and MEL are disabled by default and can be approved from Maya's local
  UI for only the current process.

These controls follow MCP's requirements to validate Origin, bind local servers
to loopback, and authenticate connections.

## Bearer-token handling

Each server start generates a new token unless MAYA_MCP_TOKEN is set.

Discovery files contain the token because a separate MCP client must learn it.
They rely on the current Windows user's LocalAppData permissions. Any process
already running as the same user can usually control Maya by other means, so
this does not create a stronger cross-process boundary.

Recommended practice:

- Use the generated per-run token.
- Reveal it only to a trusted MCP client.
- Never commit current.json or server-PID.json.
- Stop the server when it is not needed.
- Use a different Windows account for untrusted software.

## Python and MEL

maya.script.execute is full host code execution. It can:

- read, modify, or delete user files;
- start processes or access the network;
- load native libraries;
- modify Maya preferences and plug-ins;
- disable or corrupt Maya's undo stack.

AST inspection would be advisory, not a sandbox. The only honest control is to
leave the tool disabled or grant it to a trusted client.

For a trusted client, enable it without restarting from:

**Maya MCP > Allow Python/MEL Automation This Session**

The checkbox changes only the current Maya process and shows a full-privilege
warning. Headless sessions may instead opt in before launch:

~~~powershell
$env:MAYA_MCP_ALLOW_UNSAFE_CODE = '1'
& 'C:\Program Files\Autodesk\Maya2027\bin\maya.exe'
~~~

The response records a SHA-256 hash of executed source and caps captured output.
A complete append-only audit log is planned.

## Tool risk classes

| Class | Examples | Default behavior |
|---|---|---|
| Read | context, query, viewport capture | Enabled |
| Transient scene write | rig preview create/update/cancel | Direct API, bounded, ownership-checked, and non-serializing |
| Scene write | node apply, geometry, animation, rig | Enabled and undo-chunked |
| Destructive | delete, unbind, open scene | Requires explicit action fields |
| File access | save, import, reference, export | Explicit paths and actions |
| Host execution | Python and MEL | Disabled unless explicitly approved in Maya or by environment opt-in |

MCP annotations describe risk to clients, but server-side checks remain the
authority.

Rig-preview cleanup resolves every UUID, verifies ownership tags, rejects
foreign descendants, and verifies deletion before dropping tracking. Preview
nodes are marked `doNotWrite`, so they are excluded from Maya scene
serialization. The implementation does not globally disable undo or hide a
third-party callback's dirty state. Acceptance separately preflights permanent
output names and targets, requires undo to be enabled, and uses one normal Maya
undo chunk.

Viewport color and depth can reveal scene content. They use the same bearer
permission as other tools and never write captures to a user-selected path.
Interactive test evidence is written only below the ignored `build` tree.

## Known security limits

- There is one bearer-token permission level. Fine-grained read, write, file,
  viewport, and host-execution scopes are planned.
- Discovery files use inherited Windows ACLs rather than an explicit ACL.
- There is no append-only audit log yet.
- File operations are not restricted to configured project roots yet.
- The transport is plain HTTP because it is loopback-only.
- Cancellation does not interrupt a Maya operation after it starts.

## If a token is exposed

1. Run mayaMcpStop from Maya's Script Editor.
2. Remove MAYA_MCP_TOKEN if it contained the exposed value.
3. Run mayaMcpStart to generate a new token.
4. Reconfigure the trusted client from the new discovery file.
5. Review the Maya scene and undo queue before saving.

See MCP's [security best practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices)
and [Streamable HTTP security requirements](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).
