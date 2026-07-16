# Activity Sessions And Planning

Roadmap 0007 turns topology editing into an inspectable, durable workflow:

```text
OperationSession
  -> OperationAction*
  -> desired GraphVersion
  -> validated GraphDiff
  -> ActivityPlan
  -> ApprovalRequest
```

This pipeline records and plans operator intent. It does not execute runtime
effects. Containers, cloud resources, control routes, and other external
systems are outside this boundary until Roadmap 0008.

## Command Services

The transport-neutral application boundary consists of four command services:

```python
services = PlanningWorkflowServices(
    operations=OperationCommandService(unit_of_work_factory, clock=clock),
    desired_graphs=DesiredGraphCommandService(unit_of_work_factory, clock=clock),
    plans=ActivityPlanningCommandService(unit_of_work_factory, clock=clock),
    approvals=ApprovalCommandService(unit_of_work_factory, clock=clock),
)
```

Each `execute(command)` call owns one `PostgresUnitOfWork`:

```text
one operator command
  = one explicit Postgres transaction
```

The complete planning workflow contains several commands and therefore several
transactions. The durable result of one command is the validated input to the
next. Stores share the command's one connection and never commit on their own.

Database ownership remains per deployed control-plane server. A composed
control-plane instance injects its own UnitOfWork factory; command services do
not select from a global collection of instance databases.

## Complete Example

The capstone example composes the services without importing FastAPI, MCP, a
runtime interpreter, or a Postgres driver:

```python
result = plan_backend_swap(
    services,
    workspace_id="workspace-a",
    actor_id="operator",
    current_graph_id="graph-current",
    expected_desired_graph_id="graph-current",
    desired_graph=router_graph("api-v2"),
    idempotency_prefix="backend-swap",
)
```

Its implementation is deliberately direct:

```python
session = services.operations.execute(StartOperationSession(...))
desired = services.desired_graphs.execute(SetDesiredGraph(...))
plan = services.plans.execute(RequestActivityPlan(...))
approval = services.approvals.execute(RequestPlanApproval(...))
```

The result contains durable evidence from each command and says explicitly that
no runtime effects occurred:

```python
{
    "session": {...},
    "desired_graph": {...},
    "plan": {...},
    "approval": {"state": "pending", ...},
    "runtime_effects_executed": False,
}
```

See `examples/backend_swap_planning.py` and
`tests/test_backend_swap_planning_example.py`.

## Stale State And Retries

Desired graph and planning commands include the graph pointers the operator
observed. If the workspace pointers changed before the command obtained its
lock, the command rejects without publishing partial facts:

```python
SetDesiredGraph(
    expected_desired_graph_id="graph-the-operator-saw",
    ...,
)

RequestActivityPlan(
    expected_current_graph_id="current-the-operator-saw",
    expected_desired_graph_id="desired-the-operator-saw",
    ...,
)
```

Every command also carries a scoped `IdempotencyKey`. Repeating the same intent
returns the original durable result. Reusing the key for different intent fails
as an explicit idempotency conflict. The capstone test replays the entire
four-command workflow and proves that it still contains one session, graph
version, plan, approval request, and four ordered action records.

Callers should treat stale state and conflicting retries as explicit conflicts,
not silently retry them with new expectations:

```python
try:
    desired = desired_graphs.execute(command)
except StaleDesiredGraph:
    # Reload workspace truth and ask the operator to review the new graph.
    ...
except DesiredGraphIdempotencyConflict:
    # The key already names different intent; do not invent another meaning.
    ...
```

## Typed Planning Pipeline

The pure planning path is:

```text
stored graph descriptor
  -> GraphDescriptorCodec.decode
  -> validate_graph
  -> ValidatedGraph
  -> diff_graphs
  -> GraphDiff[StructuralChange]
  -> compile_activity_plan
  -> ActivityPlan[PlannedActivity]
```

`GraphDiff` and `ActivityPlan` are closed typed values, not dictionaries tagged
with ad hoc strings. Their codecs are persistence boundaries. Unknown or lossy
variants fail closed.

The important law is:

```text
compile_activity_plan(diff_graphs(current, desired))
  is pure, deterministic, and has no store or runtime effects
```

## Approval

Approval requests and decisions are different immutable facts. Requesting
approval does not approve a plan. A policy derives required scope from the
canonical plan:

```text
ordinary plan       -> plan:approve
destructive plan    -> plan:approve-destructive
```

The requesting actor needs `plan:request`. The deciding actor needs the scope
recorded on the request. A destructive plan cannot be approved using the weaker
ordinary scope.

A minimal destructive plan makes that policy visible:

```python
plan = ActivityPlan((
    PlannedActivity(
        activity_id=ActivityId("stop-api-v1"),
        operation=StopNode(NodeTarget("api-v1")),
        risk=RiskLevel.HIGH,
        impact=ActivityImpact.DESTRUCTIVE,
    ),
))

# ApprovalPolicy.requirement_for(plan).required_scope
# == "plan:approve-destructive"
```

Roadmap 0007 records approval state but does not expose an execution command.
Roadmap 0008 must verify the persisted decision and scope before claiming a
plan.

## Recovery Is Planning, Not Undo

Recovery compiles a fresh canonical plan toward a prior graph snapshot. It does
not claim that external effects can be reversed perfectly:

```text
current observed topology + target historical topology
  -> fresh validation
  -> fresh diff
  -> fresh ActivityPlan
  -> explicit recovery limitations
```

Recovery candidates name limitations such as absent runtime evidence,
non-restorable data, and the need for fresh approval. Roadmap 0008 must preserve
those limitations when deciding whether and how a recovery plan can execute.

## Read And Transport Boundaries

`InstanceReadService` owns projections and redaction. FastAPI, CLI, and MCP are
thin interpretations of the same descriptors:

```text
Postgres stores
  -> InstanceReadService
    -> FastAPI JSON
    -> CLI JSON
    -> MCP-shaped JSON
```

Focused reads expose open sessions, session detail, plan detail with risk and
recovery, and pending approvals. They are bounded, workspace-scoped, redacted,
and non-mutating. Cross-adapter tests require exact payload agreement.

## Stable Handoff

Roadmap 0008 inherits:

- canonical typed `ActivityPlan` values and descriptors;
- explicit activity dependencies, risk, and impact;
- persisted approval requests and decisions;
- idempotent command evidence;
- operation sessions and ordered actions;
- recovery candidates with limitations;
- and focused read projections.

Roadmap 0009 may compose these services behind a control-plane-instance API.
Routes should map transport requests to the existing typed commands and map
typed errors to HTTP responses. They must not recreate transaction, planning,
approval, descriptor, or redaction semantics in route handlers.

The intended error families are also transport-neutral:

```text
InvalidOperationCommand / malformed descriptor
  -> invalid client input

*WorkspaceNotFound / *TargetNotFound / *SessionNotFound
  -> missing resource

StaleDesiredGraph / *StateConflict / *IdempotencyConflict
  -> current-state conflict

ApprovalAuthorizationDenied
  -> authorization denied
```

Roadmap 0009 may map these families to HTTP 400/422, 404, 409, and 403
respectively, while preserving typed Python errors below the transport. The
exact route schema belongs to that roadmap; the durable semantics do not.
