# Maya MCP domain language

- **Operation**: A schema-described Maya capability with one handler and its
  existing permission, result, and undo policies. All operations live in the
  complete Python registry.
- **Tool profile**: The subset of operation schemas advertised to MCP clients
  at server startup. Compact reduces discovery size; full supports direct calls
  to every operation. Profiles do not authorize or disable capabilities.
- **Workflow**: A bounded sequence of operation steps executed in one native
  main-thread dispatch. Each step has an ID and may reference earlier results.
  Workflows stop on failure and preserve each operation's undo policy.
- **Canonical node reference**: A scene-epoch-scoped node identity, combined
  with Maya UUID, reference context, and DAG paths.
- **Scene revision**: An observed scene-change counter combining scoped callback
  events with a fallback signature. It is not a complete DG change journal.
- **Scene epoch**: The identity of the currently open scene, renewed on new/open.
- **Query cursor**: A continuation bound to query arguments, epoch, observed
  revision, and ordered matching node membership. It is not an attribute snapshot.
- **Observation**: Bounded scene facts, viewport image and projection metadata
  gathered together, with an expiring client-bound token for detecting observed
  changes before an edit. It does not freeze the scene.
- **Change cursor**: A continuation in the bounded, scoped event journal. Gaps
  require a fresh observation.
- **Python session**: A client- and scene-bound persistent namespace with an
  SDK and bounded JSON result handles. Arbitrary Python is still fully privileged.
- **Tracked request**: A workflow or script identified within one MCP session;
  duplicate submissions replay its state or result without repeating execution.
