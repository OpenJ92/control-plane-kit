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
