# Control Plane Read Interfaces

Status: Introduced by Roadmap 0006

This document describes the read-only surfaces for one control-plane instance.

The core law is:

```text
Read interfaces expose stored model state.
They do not define state, mutate state, or interpret runtime effects.
```

The current read surfaces are:

- projection functions for internal callers,
- `InstanceReadService` for application services and adapters,
- FastAPI read routes for HTTP clients,
- CLI commands for operators,
- and an MCP-shaped adapter for future agent clients.

All of them sit over the same store-backed read models.

## Source Of Truth

Roadmap 0006 reads from stores introduced by Roadmap 0005:

```text
WorkspaceStore
  workspace identity, lifecycle, current graph pointer, desired graph pointer

GraphTopologyStore
  graph version descriptors

ActivityHistoryStore
  sessions, plans, runs, events

ObservedStateStore
  latest observed runtime/node facts
```

Read services do not commit transactions. They query existing state and project
it into bounded, JSON-ready descriptors. API/use-case code remains the owner of
transaction boundaries.

## Operator Graph Projection

The operator graph projection turns a compiled deployment graph into a UI/MCP
shape:

```python
from control_plane_kit import compile_recipe, project_operator_graph_descriptor
from examples.http_block_compositions import active_router_recipe

graph = compile_recipe(active_router_recipe())
descriptor = project_operator_graph_descriptor(graph)
```

The descriptor includes:

- runtime contexts,
- role/block nodes,
- provider sockets,
- requirement sockets,
- socket connections,
- capabilities,
- and redacted endpoint summaries.

Concrete addresses are redacted by default. They are available only through an
explicit opt-in:

```python
descriptor = project_operator_graph_descriptor(graph, include_addresses=True)
```

The default is intentionally safe for logs, GitHub comments, MCP responses, and
operator UI payloads.

## Instance Read Service

`InstanceReadService` is the main boundary for read adapters.

```python
from control_plane_kit import InstanceReadService

read_service = InstanceReadService(
    workspace_store=workspace_store,
    graph_store=graph_store,
    activity_history_store=activity_history_store,
    observed_state_store=observed_state_store,
)

workspace = read_service.workspace("workspace-a")
current_graph = read_service.current_graph("workspace-a")
desired_graph = read_service.desired_graph("workspace-a")
activity = read_service.activity_timeline("workspace-a", limit=50)
observed = read_service.observed_state("workspace-a", limit=100)
control_surface = read_service.control_surface("workspace-a")
```

The service composes read models. It does not create graph versions, approve
plans, execute activities, or contact live block control routes.

## FastAPI Read Routes

The FastAPI adapter exposes one instance-oriented route set:

```python
from control_plane_kit import create_instance_read_app

app = create_instance_read_app(
    read_service,
    token="operator-read-token",
    api_prefix="/instances",
)
```

Routes:

```text
GET /instances/{workspace_id}/workspace
GET /instances/{workspace_id}/graphs/current
GET /instances/{workspace_id}/graphs/desired
GET /instances/{workspace_id}/activity
GET /instances/{workspace_id}/observed-state
GET /instances/{workspace_id}/control-surface
```

When a token is configured, requests must include one of:

```text
Authorization: Bearer <token>
X-Control-Plane-Token: <token>
```

The route layer is deliberately thin. It delegates to `InstanceReadService` and
returns descriptors.

## CLI Read Commands

The CLI adapter is for local operator inspection:

```bash
cpk-read workspace workspace-a --database-url "$CPK_DATABASE_URL"
cpk-read current-graph workspace-a --database-url "$CPK_DATABASE_URL"
cpk-read desired-graph workspace-a --database-url "$CPK_DATABASE_URL"
cpk-read activity workspace-a --database-url "$CPK_DATABASE_URL" --limit 20
cpk-read observed-state workspace-a --database-url "$CPK_DATABASE_URL" --limit 20
cpk-read control-surface workspace-a --database-url "$CPK_DATABASE_URL"
```

`--database-url` can be omitted when `CPK_DATABASE_URL` is set.

The CLI does not install schema, create workspaces, or mutate any store. It is a
read-only adapter over the same `InstanceReadService`.

## MCP-Shaped Adapter

Roadmap 0006 adds the stable MCP vocabulary, not a hosted MCP server process.

```python
from control_plane_kit import ReadOnlyMcpAdapter

adapter = ReadOnlyMcpAdapter(read_service)
tools = [tool.descriptor() for tool in adapter.list_tools()]
workspace = adapter.call_tool("get_workspace", {"workspace_id": "workspace-a"})
```

Tools:

```text
get_workspace
get_current_graph
get_desired_graph
get_activity_timeline
get_observed_state
get_control_surface
```

There are no mutation tools. A future concrete MCP server should wrap this
adapter rather than redefining the read vocabulary.

## Control Surface Read Model

The control-surface projection summarizes what nodes say they can do:

```python
surface = read_service.control_surface("workspace-a")
descriptor = surface.descriptor()
```

The descriptor includes:

- declared capabilities,
- expanded control-route sets,
- provider sockets,
- requirement sockets,
- and whether requirements are fulfilled by graph edges.

This is a model read, not a live probe. It does not call block routes.

## Activity And Observed State

Activity timeline reads are bounded:

```python
timeline = read_service.activity_timeline("workspace-a", limit=25)
```

Observed state reads latest facts per subject:

```python
state = read_service.observed_state("workspace-a", limit=100)
```

Observed state is not promoted into desired or current topology. If a node is
stale, failed, or unknown, that is operator information, not graph truth.

## Security Notes

- Addresses are redacted by default.
- Secret values are never exposed.
- FastAPI read routes can require bearer or header token authentication.
- CLI reads require direct database access and perform no mutation.
- MCP tools are read-only and do not invoke live control routes.
- Logs/events are bounded by limits.

## Data Engineering Notes

- Read models do not own transaction boundaries.
- Store-local mutations remain behind API/application-service unit-of-work
  code.
- Read adapters do not create implicit writes for convenience.
- Cross-boundary execution remains future activity/saga work.

## Handoff

Roadmap 0007 can use these read models as the operator lens for desired graph
edits, activity planning, approval, and plan review. It should not duplicate
read projection code inside planning or execution modules.
