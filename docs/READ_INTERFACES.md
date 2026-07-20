# Control Plane Read Interfaces

Roadmap 0006 makes one control-plane instance visible without making it
mutable.

The implementation shape is:

```text
Postgres-backed stores
  -> InstanceReadService
    -> FastAPI read routes
    -> CLI read commands
    -> MCP-shaped read adapter
```

The law is:

```text
Interfaces expose the model; they do not define the model.
```

Stores own durable truth. Read services compose and redact that truth. Adapters
translate it into a transport or operator surface.

## Source Of Truth

`InstanceReadService` is the shared read boundary for one control-plane
workspace.

It can read:

- workspace summary and graph pointers,
- current graph descriptor,
- desired graph descriptor,
- operator graph projection,
- bounded activity timeline,
- latest observed state,
- and declared control surface.

The service validates workspace existence before activity or observed-state
reads. This prevents an adapter from silently treating a missing workspace as an
empty workspace.

```python
service = InstanceReadService(
    workspace_store=stores.workspace,
    graph_topology_store=stores.graph_topology,
    activity_history_store=stores.activity_history,
    observed_state_store=stores.observed_state,
)

workspace = service.workspace("workspace-a")
operator_graph = service.operator_graph("workspace-a", pointer="current")
timeline = service.activity_timeline("workspace-a", limit=25)
surface = service.control_surface("workspace-a", pointer="current")
```

The service returns descriptor-bearing read models. Callers should serialize
those descriptors instead of reaching into store records.

## Redaction

Graph descriptors and operator projections are redacted at the read-service
layer.

That is intentional. Secret and address redaction must not depend on every
adapter remembering to do the right thing.

```text
adapter
  -> service descriptor
    -> already redacted payload
```

The current read interfaces do not expose secret values. Address-like runtime
metadata is redacted from graph and operator payloads by default.

## FastAPI

The FastAPI adapter is created with `create_instance_read_app`.

```python
from control_plane_kit.servers import create_instance_read_app

app = create_instance_read_app(
    service,
    token="operator-token",
    api_prefix="/__control",
)
```

The current routes are:

```text
GET /health
GET /workspaces/{workspace_id}
GET /workspaces/{workspace_id}/graphs/current
GET /workspaces/{workspace_id}/graphs/desired
GET /workspaces/{workspace_id}/operator-graph?pointer=current
GET /workspaces/{workspace_id}/activity?limit=50
GET /workspaces/{workspace_id}/observed-state
GET /workspaces/{workspace_id}/control-surface?pointer=current
```

When a token is configured, control-plane reads require either:

```text
Authorization: Bearer <token>
```

or:

```text
X-Control-Plane-Token: <token>
```

The route layer is intentionally thin. It maps read-service errors into HTTP
status codes and otherwise returns the service descriptor.

## CLI

The CLI is a read-only HTTP client over the FastAPI read routes.

Configure it with flags:

```bash
control-plane-kit \
  --base-url http://localhost:8010/__control \
  --token operator-token \
  workspace workspace-a
```

or environment variables:

```bash
export CONTROL_PLANE_INSTANCE_URL=http://localhost:8010/__control
export CONTROL_PLANE_TOKEN=operator-token

control-plane-kit current-graph workspace-a
control-plane-kit operator-graph workspace-a --pointer current
control-plane-kit activity workspace-a --limit 25
control-plane-kit control-surface workspace-a
```

The CLI prints JSON. It should remain boring: no mutation, no hidden discovery,
no shelling out to inspect topology.

## MCP-Shaped Adapter

`ReadOnlyMcpAdapter` is the pure MCP-shaped read vocabulary.

It is not a hosted MCP server yet. It is the tool descriptor table and dispatch
object that a runtime-specific MCP server can wrap later.

```python
from control_plane_kit.mcp_read import ReadOnlyMcpAdapter

adapter = ReadOnlyMcpAdapter(service)

tools = adapter.list_tools()
result = adapter.call_tool(
    "get_control_surface",
    {"workspace_id": "workspace-a", "pointer": "current"},
)
```

The current tool names are:

```text
get_workspace
get_current_graph
get_desired_graph
get_operator_graph
get_activity_timeline
get_observed_state
get_control_surface
```

Unknown tool names fail closed. Mutation-like names are not registered.

## Operator Graph

The operator graph is the UI/MCP-facing graph projection.

It contains:

- runtime contexts,
- nodes,
- provider sockets,
- requirement sockets,
- socket connections,
- capability hints,
- dangling requirement markers,
- and redacted implementation metadata.

This is the shape future UI surfaces should use for graph inspection. Do not
teach the UI to inspect raw graph internals when a projection exists.

## Control Surface

The control surface read model answers:

```text
What can the operator ask this graph to do?
```

It does not call control routes. It only describes declared capabilities,
route-set descriptors, providers, requirements, and warnings.

Unknown route-set names become warnings instead of crashing the read surface.
That lets an operator inspect a partially known graph while still seeing that
something needs attention.

## Activity And Observed State

Activity reads are bounded by an explicit limit.

Observed state is separate from graph truth. It can say a node is stale,
unknown, healthy, or unhealthy without changing desired or current topology.

```text
desired graph
  what the operator wants

current graph
  what the instance believes is realized

observed state
  what was most recently seen
```

Roadmap 0007 should use these read descriptors as the operator review lens for
plans and approvals.

## Security And Data Notes

- Read routes are not write routes.
- MCP tools are not mutation tools.
- Secret values are never returned.
- Address values are redacted from graph/operator descriptors by default.
- Activity timelines are bounded.
- Missing workspaces fail at the service boundary.
- Route authorization is enforced when a token is configured.
- Store-local reads remain inside the service/repository boundary; adapters do
  not reach into Postgres directly.

## Test Coverage

The Docker-first suite covers:

- Postgres-backed read store behavior,
- graph redaction,
- operator graph projection,
- capability/control-surface projection,
- stale and missing observed-state behavior,
- bounded activity timelines,
- FastAPI auth and error mapping,
- CLI command/query generation,
- MCP-shaped tool descriptors and fail-closed behavior,
- and adapter consistency over `InstanceReadService`.

## Local Docker Demo

The Roadmap 0006 read API can be tried locally with a small demo server:

```bash
./scripts/read-demo-up.sh
```

It builds the demo image, creates a Docker network, starts Postgres, installs
the control-plane schema, seeds one workspace, and serves the FastAPI read
routes on:

```text
http://localhost:8011
```

If port `8011` is already in use, choose another host port:

```bash
CPK_DEMO_HOST_PORT=8012 ./scripts/read-demo-up.sh
```

The demo workspace is:

```text
demo-workspace
```

The demo token is:

```text
demo-token
```

Try the routes directly:

```bash
curl -H "Authorization: Bearer demo-token" \
  http://localhost:8011/workspaces/demo-workspace

curl -H "Authorization: Bearer demo-token" \
  http://localhost:8011/workspaces/demo-workspace/operator-graph

curl -H "Authorization: Bearer demo-token" \
  http://localhost:8011/workspaces/demo-workspace/control-surface

curl -H "Authorization: Bearer demo-token" \
  "http://localhost:8011/workspaces/demo-workspace/activity?limit=5"
```

Or point the CLI at the live server:

```bash
control-plane-kit \
  --base-url http://localhost:8011 \
  --token demo-token \
  workspace demo-workspace
```

This is not the future hub, not a production instance server, and not a real
MCP host. It is a small live harness for the read model implemented in Roadmap
0006.

Stop and remove the demo containers with:

```bash
./scripts/read-demo-down.sh
```
