# Roadmap 0007: Visual UI And Cross-Language Contracts

Status: Draft
Depends on: Roadmap 0001 through Roadmap 0006

## Motivation

The long-term interface is a graph workbench. A user should be able to drag
blocks into runtime contexts, connect sockets, validate the graph, inspect
health, and approve activity plans.

Python is first-class, but the topology model should not be Python-only.

## Goal

Prepare descriptors for visual UI and non-Python participation:

- graph descriptors contain enough information to draw nodes and edges,
- block descriptors contain provider and requirement sockets,
- runtime descriptors contain child grouping,
- contract descriptors contain variables and redacted status,
- capability descriptors determine UI controls,
- activity plans can be rendered step-by-step,
- non-Python services can participate through JSON descriptors.

## Non-Goals

- Do not build a polished iPad app inside this package.
- Do not require non-Python servers to expose live control routes immediately.
- Do not make UI-specific fields contaminate the core algebra.

## Suggested Issue Topology

1. Define stable graph descriptor schema.
2. Define stable block/socket descriptor schema.
3. Define stable contract descriptor schema.
4. Define stable activity plan descriptor schema.
5. Add JSON contract descriptor support for non-Python services.
6. Add static validation for JSON descriptors.
7. Add UI fixture descriptors.
8. Add documentation for graph editor authors.

## UI Mental Model

```text
Workspace
  runtime context boxes
  blocks
  provider sockets
  requirement sockets
  socket connections
  node inspector
  activity timeline
```

The user action:

```text
drag provider socket -> requirement socket
```

creates:

```python
SocketConnection(
    provider_role="postgres",
    provider_socket="internal",
    consumer_role="api",
    requirement_socket="database_url",
)
```

## Non-Python Descriptor Shape

```json
{
  "role_id": "orders-api",
  "display_name": "Orders API",
  "providers": [
    {"name": "internal", "protocol": "http", "port": 8080}
  ],
  "requirements": [
    {
      "name": "database_url",
      "protocol": "postgres",
      "env_vars": ["DATABASE_URL"]
    },
    {
      "name": "payments",
      "protocol": "http",
      "env_vars": ["PAYMENTS_BASE_URL"]
    }
  ]
}
```

## Implementation Notes

- Descriptor schemas should be boring and explicit.
- The UI should not need Python reflection.
- Static descriptors are enough for initial non-Python support.
- Live contracts can come later through language SDKs.
- Capability descriptors decide which controls are shown.

## Validation

- Descriptor fixtures render all core concepts.
- JSON descriptors validate required fields and protocols.
- Static non-Python descriptors compile into graph nodes.
- Activity plan descriptors contain enough data for UI timeline rendering.
- `./test.sh`
- `python3 -m compileall control_plane_kit tests`
- `git diff --check`

## Handoff

After this vertical, the package should be ready for a separate UI project or a
thin demo UI without changing core algebra.

