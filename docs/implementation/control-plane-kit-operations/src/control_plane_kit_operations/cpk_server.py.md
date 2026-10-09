Source: [cpk_server.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/cpk_server.py).
Maintain this companion alongside its source.

Operations adapters map authenticated HTTP/MCP protocol values to their owning
application commands. `command.desired-graph.set` and inline
`command.deployment.prepare` forward optional `proposed_graph_id` without
generating or rewriting graph material. Typed commands own validation and
fingerprints; the existing service owns authority, persistence and replay.
Saved preparation with a proposal is malformed rather than silently ignored.
Adapter tests prove both HTTP and MCP forwarding; durable behavior belongs to
the PostgreSQL planning and preparation tests.

The workload verifier read adds a closed HTTP path / MCP argument mapping for
workspace_id and purposes. Its bounded CSV is decoded to existing purpose enum
values without hiding duplicates; the projection owns allowed-family semantics.
Workspace-scoped DELEGATION_KEY_READ is checked before opening the existing
read UoW. No command or effect surface is added.

The receiver-authoring-context route requires both workspace-read and
delegation-key-read before UoW construction, then validates its closed logical
arguments. Its dedicated service owns one read-only repeatable-read snapshot;
the ordinary read UoW is not also entered. HTTP-shaped path plus optional
expected/pending-draft values and MCP-shaped complete arguments share the same
query/service/descriptor. Database and decode failures map to bounded fresh
application errors. This mapping does not implement Server query decoding or
authorize dependency adoption; those remain Servers #238.
