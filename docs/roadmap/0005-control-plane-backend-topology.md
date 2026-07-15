# Roadmap 0005: Control Plane Backend Topology

Status: Draft
Depends on: Roadmap 0001 through Roadmap 0004, ADR 0004 through ADR 0008,
Design Discussion 0003

## Motivation

The package has a usable algebra for blocks, sockets, runtime contexts,
contracts, and package-provided server blocks.  The next step is operational.
Operational work requires durable state, authorization, activity history,
lifecycle management, and runtime mutation.

That is where the package can become confused if the backend topology is not
made explicit first.

The core boundary is:

```text
desired topology
  !=
observed running system
```

The package needs a backend structure that preserves that distinction.

The agreed high-level shape is:

```text
Hub
  grants access and coordinates control-plane instance lifecycle

ControlPlaneInstance
  owns one deployment workspace and orchestrates topology/activity work

Deployed application graph
  contains the actual servers, databases, blocks, runtimes, and control routes
```

The most important repository law for this roadmap is the Pottery Factory
ownership law:

```text
Separate truth ownership from workflow intent.
```

Source-of-truth modules own durable facts and valid mutations. Workflow/session
modules own grouped operator intent. Effect modules perform approved effects
through narrow capability boundaries. Interface adapters expose the model; they
do not define it.

## Goal

Define and scaffold the backend topology for control-plane work without
implementing full runtime mutation yet.

This roadmap should leave the package with:

- clear Hub / ControlPlaneInstance / deployed graph boundaries,
- explicit module taxonomy and package folders,
- source-of-truth store protocols,
- workflow/session service boundaries,
- policy/authorization service boundaries,
- planner/effect/projection boundary definitions,
- initial instance-owned persistence contracts,
- explicit lifecycle states and retention rules,
- and enough type shape for later read APIs, planning, execution, and UI work.

The desired construction order is:

```text
truth ownership
  -> workflow/session ownership
    -> policy/authorization
      -> planning/interpreting
        -> saga/compensation grammar
          -> execution/capability calls
            -> projections
              -> interfaces
```

This roadmap mostly covers the first three layers and establishes the seams for
the rest.

## Non-Goals

- Do not build the full Hub server in this vertical.
- Do not build full runtime execution in this vertical.
- Do not expose mutation routes in this vertical.
- Do not build the MCP adapter in this vertical.
- Do not require Neo4j, Memgraph, or another graph database yet.
- Do not make API routes define core semantics.
- Do not hide durable mutations inside a generic apply function.
- Do not collapse activity history, graph topology, and observed state into one
  vague store object.
- Do not perform a broad algebra package relocation in this vertical.  Roadmap
  0005 is about backend topology boundaries; algebra/package reshaping should
  remain a separate issue if it becomes necessary.

## Suggested Issue Topology

1. Add backend topology design references to roadmap docs.
   - Link Design Discussion 0003 and ADR 0008 from the relevant roadmap
     documents.
   - Make Roadmap 0005 the explicit entry point for backend topology work.

2. Create source-of-truth package structure.
   - Add modules or packages for stores/repositories.
   - Define protocols for workspace, graph topology, activity history,
     observed state, instance registry, and secret references.
   - Keep implementations minimal or in-memory at first.

3. Define `WorkspaceStore` and `GraphTopologyStore` contracts.
   - `WorkspaceStore` owns workspace identity, lifecycle, current graph pointer,
     desired graph pointer, and graph-version metadata.
   - `GraphTopologyStore` owns graph-shaped topology payloads behind an adapter.
   - First implementation may store graph descriptors/blobs, but the interface
     must be graph-shaped enough for future Neo4j/Memgraph adapters.

4. Define `ActivityHistoryStore` contracts.
   - Operation sessions.
   - Operation actions.
   - Approval records.
   - Activity plan records.
   - Activity run records.
   - Activity event records.
   - Compensation records where needed.

5. Define `ObservedStateStore` contracts.
   - Latest observed state per workspace/node.
   - Historical observations where useful.
   - Bounded health/status snapshots.
   - No secret values.

6. Define `InstanceRegistryStore` contracts.
   - Hub-visible control-plane instance records.
   - Owner/grant references.
   - Lifecycle status.
   - Endpoint/wake metadata.
   - Retention metadata.

7. Define workflow/session services.
   - `OperationSessionService`.
   - `OperationActionService`.
   - `ApprovalWorkflowService`.
   - `ActivityRunService`.
   - These services should record intent and workflow state, not own graph or
     runtime truth.

8. Define policy/authorization services.
   - `HubAccessPolicy`.
   - `InstanceAccessPolicy`.
   - `ApprovalPolicy`.
   - `DestructiveActivityPolicy`.
   - Keep policy mostly pure and testable.

9. Define control-plane lifecycle states and retention laws.
   - Suggested states:
     `created`, `running`, `paused`, `stopped`, `archived`,
     `deconstructed`, `deleted`, `failed`.
   - Specify what state is retained in each lifecycle.
   - `deleted` must be explicitly destructive.

10. Add local durable persistence direction docs.
    - Hub will eventually use Postgres.
    - Local instance persistence should prefer Postgres-backed relational
      adapters over SQLite for durable work.
    - In-memory stores remain valid for tests.
    - Graph topology remains adapter-backed and graph-database-ready.

11. Add module service/adaptor law to `AGENTS.md` or an equivalent design
    reference.
    - If a boundary might become a microservice boundary later, make the service
      contract visible now.

12. Add tests for ownership boundaries.
    - Workflow services cannot mutate graph truth directly.
    - Policy modules return decisions rather than performing effects.
    - Store protocols can be satisfied by in-memory test implementations.

## Target Package Shape

The exact names may change, but this is the target shape:

```text
control_plane_kit/stores/
  graph.py
  workspace.py
  activity_history.py
  observed_state.py
  instance_registry.py
  secrets.py

control_plane_kit/workflows/
  operation_sessions.py
  operation_actions.py
  approvals.py
  activity_runs.py

control_plane_kit/policies/
  hub_access.py
  instance_access.py
  approval.py
  destructive_activity.py

control_plane_kit/planning/
  validation.py
  diff.py
  activity_planner.py
  recovery_planner.py

control_plane_kit/saga/
  activity.py
  program.py
  interpreter.py

control_plane_kit/effects/
  runtime_executor.py
  block_control_client.py
  runtime_provider.py
  secret_writer.py

control_plane_kit/projections/
  operator_graph.py
  workspace_read_model.py
  capabilities.py
  activity_timeline.py

control_plane_kit/interfaces/
  hub_fastapi.py
  instance_fastapi.py
  cli.py
  mcp.py
```

This roadmap does not need to fill every module.  It should establish the
ownership taxonomy and the earliest truth/workflow/policy contracts.

## Implementation Notes

- Prefer protocols and small dataclasses before concrete databases.
- Keep in-memory implementations for tests.
- Do not bake JSON descriptor storage into the public graph-store API.
- Do not model graph topology as normalized Postgres tables in the first pass.
- Do use proper relational normalization for session/action/approval/run/event
  data.
- Treat `ControlPlaneInstance` as an orchestration/API boundary over imported
  modules, not as the place where every rule accumulates.
- Treat the Hub as light: access, registry, lifecycle, and session granting.
- Treat the Instance as heavy: workspace graph/activity/state authority.
- Keep source-of-truth modules separate from workflow/session modules even when
  they share one physical Postgres database.

## Validation

- Store protocols have in-memory implementations.
- Workflow services can record sessions/actions without mutating graph truth.
- Policy services can approve/reject operations without executing effects.
- Lifecycle states and retention behavior are documented and represented.
- Examples or tests demonstrate one instance workspace with current/desired
  graph metadata.
- Documentation references ADR 0008 for data engineering requirements.
- `./test.sh`
- `python3 -m compileall control_plane_kit tests`
- `git diff --check`

## Handoff

Roadmap 0006 should build read/query interfaces over these boundaries. Do not
start read routes until the ownership taxonomy is represented clearly enough
that routes cannot become accidental domain logic.
