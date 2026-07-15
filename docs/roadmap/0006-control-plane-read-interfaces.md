# Roadmap 0006: Control Plane Read Interfaces

Status: Implemented on Roadmap PR 0006 v2
Depends on: Roadmap 0005

## Motivation

Once the backend topology exists, the package needs safe ways to inspect it.

Read interfaces are the first operational boundary because they let API, CLI,
MCP, and future UI clients observe graph topology, workspace state, contracts,
capabilities, activity history, lifecycle state, and bounded logs/events without
mutating anything.

This roadmap should make the control plane visible before it becomes mutable.

The central law:

```text
Interfaces expose the model; they do not define the model.
```

The first meaningful server should be the `ControlPlaneInstance` read API. The
Hub can begin later as a light registry/read shell.

## Goal

Create read/query interfaces over one control-plane instance.

This roadmap should provide:

- instance read service interfaces,
- workspace read models,
- graph projection payloads,
- capability summaries,
- contract summaries,
- activity timeline projections,
- observed-state summaries,
- bounded log/event query models,
- FastAPI read-only instance routes,
- CLI read-only commands,
- and a read-only MCP adapter if the service interface is stable enough.

The route layer must stay thin. It should call read-model services and should
not own graph, workflow, activity, or policy truth.

## Non-Goals

- Do not expose graph mutation routes.
- Do not expose activity execution routes.
- Do not build full Hub lifecycle management yet.
- Do not make MCP mutation tools.
- Do not let MCP shell out to discover topology.
- Do not make read models Docker-specific.
- Do not expose secret values.
- Do not expose unbounded logs or raw request/response bodies.

## Suggested Issue Topology

1. Define instance read service boundary.
   - Read current graph metadata.
   - Read desired graph metadata.
   - Read workspace summary.
   - Read activity timeline.
   - Read observed state.
   - Read capabilities.
   - Read block/control-route descriptors.

2. Add operator graph projection.
   - Project internal graph topology into UI/MCP-friendly nodes and edges.
   - Include runtime contexts, blocks, sockets, socket connections, and
     capability hints.
   - Redact secrets and implementation-only metadata.

3. Add workspace read model.
   - Combine graph projection, lifecycle state, block catalog, capability
     summary, observed-state summary, and recent activity timeline.
   - Keep it bounded and deterministic for tests.

4. Add activity timeline read model.
   - Operation sessions.
   - Operation actions.
   - Approval records.
   - Activity plans.
   - Activity runs.
   - Activity events.
   - Compensation records.
   - Keep raw logs separate.

5. Add capability/control-route read model.
   - Generic health/capability routes.
   - Block-specific route descriptors.
   - Required scopes for control routes.
   - Mutation routes are described but not callable through this roadmap.

6. Add observed-state read model.
   - Latest node health/status.
   - Runtime state summaries.
   - Stale/unknown markers.
   - No silent promotion of observed state into desired/current topology.

7. Add FastAPI instance read-only server.
   - `/health` can remain public or minimally scoped for runtime checks.
   - Instance protocol routes require authorization.
   - Routes delegate to read services.

8. Add CLI read-only commands.
   - Inspect workspace.
   - Inspect graph.
   - List sessions/actions/events.
   - List capabilities.
   - Validate graph descriptors where useful.

9. Add MCP read-only adapter.
   - Only if the read service boundary is stable.
   - MCP tools must not perform mutation.
   - MCP responses must be bounded and redacted.

10. Add documentation and examples.
    - Show one instance workspace query.
    - Show current/desired graph read.
    - Show capability read.
    - Show activity timeline read.

## Target Instance Read API

The exact routes may change, but the target shape is:

```text
GET /health

GET /control/workspace
GET /control/workspace/current-graph
GET /control/workspace/desired-graph
GET /control/workspace/operator-graph
GET /control/workspace/capabilities
GET /control/workspace/contracts
GET /control/workspace/observed-state
GET /control/workspace/activity-timeline
GET /control/workspace/events?limit=100
GET /control/blocks
GET /control/control-routes
```

The corresponding service should be usable without HTTP:

```python
read_model = InstanceReadService(...)

workspace = read_model.workspace()
operator_graph = read_model.operator_graph()
timeline = read_model.activity_timeline(limit=100)
```

## Target MCP Tools

Read-only first:

```python
get_workspace()
get_current_graph()
get_desired_graph()
get_operator_graph()
list_nodes()
get_node(node_id)
list_runtime_contexts()
list_socket_connections()
list_capabilities(node_id=None)
get_observed_state(node_id=None)
get_activity_timeline(limit=100)
get_recent_events(limit=100)
validate_graph(candidate_graph)
```

Mutation tools remain absent.

## Implementation Notes

- Keep API adapters thin.
- Keep read models bounded.
- Redact secrets at the read-model layer, not only at the route layer.
- Health is a capability, not universal truth.
- Observed state can be stale. Represent staleness explicitly.
- The Hub read API can be added after the instance read API if the instance
  model proves coherent.
- Do not require a live Docker deployment for all read-model tests; use stores
  and fixtures.

## Validation

- Read services return deterministic payloads from Postgres-backed test stores.
- FastAPI routes delegate to read services.
- Unauthorized reads are rejected when auth is configured.
- Secret values do not appear in graph, event, contract, or MCP payloads.
- Activity timeline is bounded.
- Observed-state payloads can represent unknown/stale state.
- MCP tools, if implemented, are read-only.
- `./test.sh`
- `python3 -m compileall control_plane_kit tests`
- `git diff --check`

## Handoff

Roadmap 0007 will add operation sessions, desired graph edits, planning, and
approval. Keep read-model descriptors stable enough that plans and approval
workflows can refer to them without reworking all query routes.

Implementation notes and examples now live in
[Control Plane Read Interfaces](../READ_INTERFACES.md). The second execution
learning document is
[Roadmap 0006 Run 0002 Learning](../learning/roadmap-0006/run-0002.md).
