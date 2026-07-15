# Roadmap Closeout: 0006 Control Plane Read Interfaces

## Summary

Roadmap 0006 made a control-plane instance inspectable without making it
mutable.

The delivered surface is intentionally layered:

```text
Postgres-backed store contracts
  -> projection read models
    -> InstanceReadService
      -> FastAPI adapter
      -> CLI adapter
      -> MCP-shaped adapter
```

This keeps the package aligned with the central interface law:

```text
Interfaces expose the model; they do not define the model.
```

## Merged Child PRs

- #94: Roadmap 0006.1: Add operator graph projection
- #96: Roadmap 0006.2: Add instance read service and workspace read model
- #97: Roadmap 0006.3: Add activity timeline and observed-state read models
- #98: Roadmap 0006.4: Add capability, contract, and control-route read models
- #99: Roadmap 0006.5: Add FastAPI instance read-only routes
- #100: Roadmap 0006.6: Add CLI read-only commands
- #101: Roadmap 0006.7: Add read-only MCP adapter
- Roadmap 0006.8: Documentation and closeout

## Final Shape

The roadmap delivered these package modules:

```text
control_plane_kit/projections/
  operator_graph.py
  workspace.py
  activity.py
  control_surface.py

control_plane_kit/read_services/
  instance.py

control_plane_kit/servers/
  instance_read.py

control_plane_kit/cli/
  read.py

control_plane_kit/interfaces/
  mcp.py
```

The read adapters all converge on `InstanceReadService`. That is the important
shape: HTTP, CLI, and MCP do not each invent their own query semantics.

## Important Snippets

The service boundary:

```python
read_service = InstanceReadService(
    workspace_store=workspace_store,
    graph_store=graph_store,
    activity_history_store=activity_history_store,
    observed_state_store=observed_state_store,
)

workspace = read_service.workspace("workspace-a")
current_graph = read_service.current_graph("workspace-a")
activity = read_service.activity_timeline("workspace-a", limit=50)
surface = read_service.control_surface("workspace-a")
```

The FastAPI adapter:

```python
app = create_instance_read_app(
    read_service,
    token="operator-read-token",
    api_prefix="/instances",
)
```

The CLI adapter:

```bash
cpk-read workspace workspace-a --database-url "$CPK_DATABASE_URL"
cpk-read control-surface workspace-a --database-url "$CPK_DATABASE_URL"
```

The MCP-shaped adapter:

```python
adapter = ReadOnlyMcpAdapter(read_service)
tools = [tool.descriptor() for tool in adapter.list_tools()]
workspace = adapter.call_tool("get_workspace", {"workspace_id": "workspace-a"})
```

## Validation

- [x] `./test.sh`
- [x] `git diff --check`
- [x] FastAPI routes reject unauthorized reads when a token is configured.
- [x] CLI commands return JSON descriptors through the same read service.
- [x] MCP adapter tools are read-only and reject unknown/mutation-like tools.
- [x] Operator graph projections redact addresses by default.
- [x] Control-surface reads expose declared capabilities and route descriptors
      without calling live control routes.

The final roadmap validation should rerun `./test.sh` from the roadmap branch
after this closeout PR merges.

## Security Result

Roadmap 0006 expanded read surfaces, so the main security concern was
accidental disclosure rather than mutation.

Protective decisions:

- concrete addresses are redacted by default;
- secret values are never exposed;
- FastAPI routes can require a bearer or header token;
- CLI reads require direct database access and do not mutate;
- MCP tools are limited to read projections;
- no adapter calls block control routes;
- logs/events are bounded by explicit limits.

Residual risk:

- a concrete hosted MCP server still needs its own authentication and transport
  policy later;
- direct CLI database access is powerful operationally and should remain a
  local/operator tool, not a remote product surface.

## Data Safety Result

Roadmap 0006 added no write semantics.

The data-engineering result is mostly negative space:

- read services do not commit transactions;
- route handlers do not create unit-of-work boundaries;
- adapters do not install schema or create records;
- observed state is never promoted into graph truth;
- current and desired graph pointers remain workspace-store truth;
- graph descriptors remain graph-topology-store truth.

This follows ADR 0008: store-local transitions belong inside explicit
Postgres-backed unit-of-work boundaries owned by API/application-service code.
Roadmap 0006 only queries those stores.

## Operational Reliability Result

- Health/status: exposed as declared capability/control-surface information,
  not as universal truth.
- Logs/events: activity events are queryable through bounded timeline reads;
  raw log streaming remains future work.
- Failure modes: missing current/desired graphs and unknown tools fail
  explicitly.
- Cleanup: no runtime resources are created by read interfaces.
- Retry/resume: reads are idempotent; execution retry/resume is future roadmap
  work.
- Examples added or updated: read-interface guide and CLI/FastAPI/MCP test
  cases.
- Examples still missing: a live hosted instance-read server example and a
  concrete MCP server wrapper.

## Handoff To Next Roadmap

Roadmap 0007 should build operation sessions, desired graph edits, activity
planning, and approval on top of these read descriptors.

The intended next composition is:

```text
operator proposes graph edit
  -> desired graph snapshot
    -> read projection for review
      -> activity plan preview
        -> approval workflow
```

Roadmap 0007 should not duplicate projection logic. If planning needs a
human-readable view, it should use the read models from Roadmap 0006.
