Source: [test_cpk_server_adapters.py](../../../../control-plane-kit-operations/tests/test_cpk_server_adapters.py).
Maintain this companion alongside its source.

The existing suite owns Operations route-to-command adaptation and policy
boundaries, with recording ports or real services as appropriate. #1875 adds
HTTP and MCP propagation of proposed_graph_id through desired-graph.set and
inline deployment.prepare. The same submitted descriptor must reach the typed
command unchanged; neither adapter synthesizes configuration or authority.

Recording command ports prove this adapter boundary only. Real PostgreSQL
persistence, replay, collisions and plans are covered by planning_commands and
deployment_program_preparation. Servers237 must separately exercise its actual
client/host and shared receiver; this is not a network or deployment proof.
The new targets are unvalidated until ordinary owning-gate evidence is recorded.
