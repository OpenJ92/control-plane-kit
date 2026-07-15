# Roadmap 0010: Visual UI, MCP, And Cross-Language Contracts

Status: Draft
Depends on: Roadmap 0001 through Roadmap 0009

## Motivation

The long-term interface is a graph workbench.

A user should be able to:

- log into the Hub,
- select or wake a control-plane instance,
- inspect a deployment workspace,
- drag blocks into runtime contexts,
- connect sockets,
- validate the graph,
- open an operation session,
- request an activity plan,
- review risks,
- request or grant approval,
- execute approved plans,
- inspect activity history,
- and observe runtime state.

The UI deliberately simplifies a recursive implementation law:

```text
ManagedNode = DeployBlockNode | ControlPlaneInstanceNode
ControlPlaneInstance = control plane over Graph[ManagedNode]
```

The Hub is the same control-plane object configured as the root and normally
admitted to manage child control-plane instances. Ownership, registry,
lifecycle, delegation, and proxying are reusable instance capabilities rather
than Hub-only semantics. An ordinary instance may manage deploy blocks, child
instances, or both when its admission policy permits it. The frontend speaks to
the root, and the root authenticates, selects, and proxies to a chosen child
instance API. The child remains the authority for its own workspace.

Python is first-class, but the topology model should not be Python-only. Other
languages should participate through descriptors, declared sockets, environment
requirements, control-route contracts, and capability payloads.

## Goal

Prepare the package for visual UI, MCP operation, and non-Python block
participation.

This roadmap should provide:

- stable graph/workspace descriptor schemas for UI,
- block/socket descriptor schemas,
- runtime context descriptor schemas,
- capability/control-route descriptor schemas,
- activity session/plan/run/event descriptor schemas,
- JSON descriptor support for non-Python blocks,
- validation for static descriptors,
- read-only and eventually approved mutation MCP shapes,
- UI fixtures,
- and documentation for graph editor authors.

## Non-Goals

- Do not build the polished iPad app inside this package.
- Do not require non-Python servers to expose live control routes immediately.
- Do not make UI-specific fields contaminate core algebra.
- Do not expose MCP mutation tools without approval/session boundaries.
- Do not make every package-provided demo block production-grade.

## Suggested Issue Topology

1. Stabilize graph/workspace descriptor schema.
   - Runtime contexts.
   - Blocks.
   - Sockets.
   - Socket connections.
   - Current/desired graph versions.
   - Observed-state summary.

2. Stabilize block descriptor schema.
   - `BlockSpec`.
   - Runtime implementation metadata.
   - Provider sockets.
   - Requirement sockets.
   - Control routes.
   - Capabilities.

3. Stabilize activity descriptor schemas.
   - Operation session.
   - Operation action.
   - Approval.
   - Activity plan.
   - Activity run.
   - Activity event.
   - Compensation record.

4. Add UI fixture descriptors.
   - Simple local deployment.
   - Router-backed backend swap.
   - Runtime context boxes.
   - Pending approval.
   - Failed/partial activity run.

5. Add static non-Python block descriptors.
   - JSON descriptor format.
   - Required providers and requirements.
   - Environment variable bindings.
   - Optional control-route metadata.

6. Add descriptor validation.
   - Protocol compatibility.
   - Required sockets.
   - Invalid route/capability shape.
   - Secret redaction.

7. Expand MCP read-only adapter.
   - Workspace.
   - Graphs.
   - Capabilities.
   - Activity timeline.
   - Observed state.
   - Validation.

8. Design MCP mutation adapter.
   - Operation session required.
   - Approval required.
   - Dangerous tools separated from read-only tools.
   - May remain disabled until explicitly implemented.

9. Add graph editor documentation.
   - Palette from block catalog.
   - Drag provider socket to requirement socket.
   - Runtime context grouping.
   - Boxed application subgraphs.
   - Activity timeline.
   - Approval workflow.

10. Add cross-language contract documentation.
    - Python-first SDK.
    - Static JSON descriptor path.
    - Future SDK route/control contract.

## UI Mental Model

```text
Hub screen
  visible control-plane instances
  lifecycle controls
  create/wake/select child instance
  authenticated proxy to selected child API

Workspace screen
  runtime context boxes
  blocks
  provider sockets
  requirement sockets
  socket connections
  node inspector
  activity/session timeline
  approval panel
  observed-state panel
```

The editor should present this as a simple navigation hierarchy, not an
infinite recursive canvas. Internally, however, child instances are typed
`ControlPlaneInstance` nodes with provider/control APIs, lifecycle
capabilities, and observable state. This allows the same descriptors and
capability-driven UI machinery to represent both parent-managed instances and
ordinary controllable deployment blocks.

The user action:

```text
drag provider socket -> requirement socket
```

creates a socket connection:

```python
SocketConnection(
    provider_block="postgres",
    provider_socket="internal",
    consumer_block="api",
    requirement_socket="database_url",
)
```

## Non-Python Descriptor Shape

The exact schema may change, but the intent is:

```json
{
  "block_id": "orders-api",
  "display_name": "Orders API",
  "kind": "application",
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
  ],
  "control_routes": [
    {"name": "health", "method": "GET", "path": "/__control/health"}
  ]
}
```

## MCP Safety Model

MCP read tools can arrive before mutation tools.

Mutation tools require:

```text
operation session
base graph version
idempotency key
approval policy
explicit dangerous-tool separation
```

The MCP adapter must not turn "AI can call a tool" into "AI can mutate
production topology without approval."

## Implementation Notes

- Descriptor schemas should be boring and explicit.
- The UI should not need Python reflection.
- Static descriptors are enough for initial non-Python support.
- Live contracts can come later through language SDKs.
- Capability descriptors decide which controls are shown.
- Keep control routes separate from user traffic routes.
- Keep graph descriptors redacted.
- Keep root/child presentation capability- and admissibility-driven rather than
  implementing Hub and Instance as unrelated object hierarchies.
- Any authorized parent instance may proxy authenticated requests to a selected
  child, but it must not duplicate or absorb child workspace semantics.

## Validation

- Descriptor fixtures render all core concepts.
- JSON descriptors validate required fields and protocols.
- Static non-Python descriptors compile into graph nodes.
- Activity plan/run/event descriptors contain enough data for UI timeline
  rendering.
- MCP read tools are bounded and redacted.
- MCP mutation tools are absent or explicitly approval-gated.
- `./test.sh`
- `python3 -m compileall control_plane_kit tests`
- `git diff --check`

## Handoff

After this vertical, the package should be ready for a separate UI project or a
thin demo UI without changing core backend topology. Further roadmap work can
then focus on richer runtimes, cloud providers, graph database adapters, and
production hardening of recursive instance registries and lifecycle providers.
