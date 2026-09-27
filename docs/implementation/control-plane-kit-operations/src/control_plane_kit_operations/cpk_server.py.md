Source: [cpk_server.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/cpk_server.py).
Maintain this companion alongside its source.

Operations adapters map authenticated HTTP/MCP protocol values to their owning
application commands. `command.desired-graph.set` and inline
`command.deployment.prepare` forward optional `proposed_graph_id` without
generating or rewriting graph material. Typed commands own validation and
fingerprints; the existing service owns authority, persistence and replay.
Saved preparation with a proposal is malformed rather than silently ignored.
No route, authentication policy, Core wire table or effect surface is added.
Adapter tests prove both HTTP and MCP forwarding; durable behavior belongs to
the PostgreSQL planning and preparation tests.
