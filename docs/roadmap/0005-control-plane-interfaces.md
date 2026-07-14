# Roadmap 0005: Control Plane Interfaces

Status: Draft
Depends on: Roadmap 0003, Roadmap 0004

## Motivation

The package needs multiple interfaces over the same semantic model:

- Python API,
- CLI,
- HTTP control plane,
- MCP adapter,
- future visual UI.

These interfaces should not invent separate topology models.

## Goal

Create control-plane-facing interfaces that can inspect graph topology,
contracts, runtime state, capabilities, health, and events.

The MCP adapter should be read-only first.

## Non-Goals

- Do not expose mutation tools until auth, approval, and activity planning are
  ready.
- Do not let MCP shell out to discover topology.
- Do not make MCP Docker-specific.
- Do not build the full UI in this vertical.

## Suggested Issue Topology

1. Define control plane service interface.
2. Add graph/contract/status query API.
3. Add FastAPI control-plane server skeleton.
4. Add CLI read-only commands.
5. Add MCP read-only adapter.
6. Add bounded logs/events query model.
7. Add documentation and examples.

## Target MCP Tools

```python
get_graph()
list_nodes()
get_node(node_id)
list_runtime_contexts()
list_socket_connections()
validate_graph(candidate_graph)
list_contracts()
get_contract(node_id)
list_capabilities(node_id)
get_health(node_id=None)
get_recent_events(limit=100)
```

## Target Control-Plane Client

```python
client = ControlPlaneClient(base_url="https://control.example.internal")

graph = client.get_graph()
contracts = client.list_contracts()
errors = client.validate_graph(candidate_graph)
```

## Implementation Notes

- MCP, CLI, and UI should all talk to the same service interface.
- Mutation-capable APIs should be designed but disabled or absent at first.
- Logs and events must be bounded.
- Secret values must never appear.
- Health is a capability, not a universal property.

## Validation

- Query APIs return graph descriptors.
- Contract APIs redact secrets.
- MCP tools return bounded descriptors.
- Invalid node IDs produce structured errors.
- Mutation tools are absent or rejected.
- `./test.sh`
- `python3 -m compileall control_plane_kit tests`
- `git diff --check`

## Handoff

Activity planning will add mutations. Record exactly where approval boundaries
should attach.

