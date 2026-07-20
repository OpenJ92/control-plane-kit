# Deployment Application Program

`control_plane_kit.application.deploy` is the intentional application entrance
for moving one deployment graph to another. A long-lived `DeploymentProgram`
composes the package's canonical planning, approval, admission, lifecycle,
execution, advancement, and durable-context services. A short-lived `Deploy`
binds that program to one graph pair. Neither object replaces durable records or
transaction boundaries.

## Object Lifecycles

Create the capability composition once when the control-plane server starts:

```python
from control_plane_kit.application.deploy import (
    DeploymentProgram,
    DeploymentProgramServices,
)

program = DeploymentProgram(
    DeploymentProgramServices(
        planning=planning,
        approvals=approval_command_service,
        admission=execution_admission_service,
        lifecycle=run_lifecycle_service,
        coordinator=execution_coordinator,
        advancement=current_graph_advancement_service,
        contexts=deployment_plan_context_query_service,
    )
)
```

The three lifecycles are distinct:

```text
DeploymentProgram
  long-lived capability composition; no operator workflow state

Deploy
  short-lived program specialized to current graph x desired graph

plan, approval, execution request, run, event, and observation records
  durable Postgres facts that survive process restarts and long delays
```

Planning binds a graph pair only for the current request:

```python
prepared = program.between(current, desired).plan(plan_command)
```

Later requests reconstruct from durable identity. The Python object from the
planning request is neither retained nor required:

```python
approved = program.for_plan(plan_id).approve(
    approval_request_id,
    approval_grant,
)

result = program.for_plan(plan_id).run(
    approval_request_id,
    execution_grant,
)
```

`for_plan()` returns an ephemeral handle containing only `plan_id`. Every
`approve()` or `run()` invocation opens one read-only Postgres UnitOfWork,
reconstructs the canonical graph, session, plan, and approval evidence, closes
that transaction, and then delegates mutation to the existing command service.
It does not cache a snapshot across requests.

The approval request remains explicit because one plan may have more than one
historical request. The server must never guess which authority decision an
execution uses.

The future CPI HTTP boundary maps directly onto this API:

```text
POST /workspaces/{workspace_id}/plans
POST /plans/{plan_id}/approvals/{approval_request_id}
POST /plans/{plan_id}/executions
```

Notification is a downstream adapter over durable `ApprovalRequested` truth.
Failed email or push delivery cannot erase or become the approval request.

## One Transition Language

Every deployment operation remains a pair of graph values:

```python
from control_plane_kit import DeploymentGraph

empty = DeploymentGraph("empty")

initial = program.between(empty, desired)
update = program.between(current, desired)
teardown = program.between(current, empty)
no_op = program.between(current, current)
```

`Deploy.transition` interprets those pairs as one closed sum type:

```text
DeploymentTransition
  = InitialDeployment
  | UpdateDeployment
  | TeardownDeployment
  | NoOpDeployment
```

There is no separate “transition” executor. Initial deployment is a transition
from an empty graph. Teardown is a transition to an empty graph. No-op is an
identical graph pair and produces durable planning evidence without fabricating
approval, admission, runtime effects, or advancement.

## Composition

The public stages are callable objects parameterized by canonical application
command services:

```python
from control_plane_kit.application.deploy import (
    Admit,
    Advance,
    Approve,
    Claim,
    Deploy,
    Execute,
    ExecuteApprovedDeployment,
    Plan,
    PlanningServices,
    PrepareDeployment,
)

planning = PlanningServices(
    operation_command_service,
    desired_graph_command_service,
    activity_planning_command_service,
    approval_command_service,
)

deploy = Deploy(
    current,
    desired,
    PrepareDeployment(Plan(planning)),
    Approve(approval_command_service),
    ExecuteApprovedDeployment(
        Admit(execution_admission_service),
        Claim(run_lifecycle_service),
        Execute(execution_coordinator),
        Advance(current_graph_advancement_service),
    ),
)
```

The class instances hold dependencies. Their calls transform typed values:

```text
Plan     : DeploymentPlanRequest -> DeploymentPreparationResult
Approve  : ApprovalSuspension x ApprovalGrant -> ApprovedDeployment
Admit    : ApprovedDeployment x AdmissionGrant -> AdmittedDeployment
Claim    : AdmittedDeployment x ClaimGrant -> ClaimedDeployment
Execute  : ClaimedDeployment x ExecutionLimits -> DeploymentExecutionResult
Advance  : ExecutedDeployment x AdvancementGrant -> AdvancedDeployment
```

`Deploy` composes those morphisms without importing stores, SQL, HTTP clients,
Docker clients, or effect adapters.

## Prepare And Suspend

Preparation records operator intent and stops before an authority decision:

```python
from control_plane_kit.application.deploy import (
    ApprovalSuspension,
    DeploymentPlanRequest,
    DeploymentReviewBlocked,
    NoDeploymentChanges,
)

prepared = deploy(
    DeploymentPlanRequest(
        transition=deploy.transition,
        workspace_id="workspace-a",
        current_graph_id="graph-current",
        expected_desired_graph_id="graph-current",
        actor_id="operator-a",
        title="Deploy desired topology",
        approval_comment="Review the compiled activity plan.",
        idempotency_prefix="deployment-42",
    )
)

match prepared:
    case ApprovalSuspension():
        pass  # present the plan and required scope to an approver
    case NoDeploymentChanges():
        pass  # planning truth exists; no execution exists
    case DeploymentReviewBlocked():
        pass  # diagnostics require a graph or policy correction
```

Preparation may span several separately durable operator commands: session
start, desired graph edit, plan request, and approval request. Each command
service owns one short Postgres UnitOfWork. A later failure does not erase
earlier operator history.

## Explicit Approval

The program never invents an approver or scope:

```python
from control_plane_kit.application.deploy import ApprovalGrant
from control_plane_kit.workflows import IdempotencyKey

approved = deploy.approve(
    prepared,
    ApprovalGrant(
        actor_id="approver-a",
        actor_scopes=(prepared.approval_request.request.required_scope,),
        idempotency_key=IdempotencyKey("deployment-42:approve"),
        comment="Reviewed and approved.",
    ),
)
```

`Deploy` verifies that the suspension belongs to its parameterized graph pair.
The approval command service verifies current plan identity and authorization.

## Execute Approved Work

Execution requires separate operator, worker, lease, timeout, and idempotency
inputs:

```python
from control_plane_kit.application.deploy import (
    AdmissionGrant,
    AdvancementGrant,
    ClaimGrant,
    DeploymentExecutionGrant,
    ExecutionLimits,
)
from control_plane_kit.effects import TimeoutPolicy
from control_plane_kit.workflows import ExecutionWorkerAuthority, IdempotencyKey

worker = ExecutionWorkerAuthority("worker-a", ("execution:operate",))

result = deploy.execute_approved(
    approved,
    DeploymentExecutionGrant(
        admission=AdmissionGrant(
            "operator-a",
            ("plan:execute",),
            IdempotencyKey("deployment-42:admit"),
        ),
        claim=ClaimGrant(
            worker,
            "2026-07-18T18:00:00Z",
            IdempotencyKey("deployment-42:claim"),
            IdempotencyKey("deployment-42:start"),
        ),
        advancement=AdvancementGrant(
            IdempotencyKey("deployment-42:advance")
        ),
        limits=ExecutionLimits(
            timeout=TimeoutPolicy(total_seconds=30, interval_seconds=2),
            max_effects=100,
        ),
    ),
)
```

The result is closed:

```text
DeploymentProgramResult
  = AdvancedDeployment
  | ExecutionContinuation
  | RecoverySuspension
```

`AdvancedDeployment` means terminal execution evidence passed guarded
current-graph compare-and-set. `ExecutionContinuation` means bounded progress
may continue. `RecoverySuspension` means operator evidence or a recovery
decision is required; it does not mean “retry.”

## Continuation And Recovery

Bounded progress resumes explicitly:

```python
continued = deploy.resume_execution(
    continuation,
    limits=ExecutionLimits(max_effects=100),
    advancement=AdvancementGrant(
        IdempotencyKey("deployment-42:advance")
    ),
)
```

Recovery is a separate operator workflow. The operator reads the canonical
recovery projection and records one typed decision through the lifecycle
command service. Only then is the original suspension passed back:

```python
# recovery_service.execute(typed_recovery_command)

resumed = deploy.resume_recovered(
    recovery_suspension,
    limits=ExecutionLimits(max_effects=100),
    advancement=AdvancementGrant(
        IdempotencyKey("deployment-42:advance")
    ),
)
```

The coordinator reconstructs state from the canonical event journal. Uncertain
effects never replay blindly. Compensation remains reverse durable completion
order and preserves the original forward failure independently.

## Runtime Effect Boundary

`Deploy` does not call Docker or HTTP directly. `Execute` asks the canonical
coordinator for bounded progress. The coordinator preserves this law:

```text
short transaction: record durable intent
  -> commit
    -> bounded external effect
      -> short transaction: record result, event, observation, projection
```

Materialization uses the exact graph versions pinned by admission. Later graph
drift cannot retarget work. Advancement succeeds only after complete terminal
evidence and a current-graph compare-and-set.

## Live Proof

Run the complete local operator proof without Docker Compose:

```bash
./gate-f-live-test.sh
```

It proves explicit loopback publication, a real Postgres-backed graph
transition, explicit approval/admission/claim, authenticated router control,
the same HTTP route changing from blue to green, a 401 for unauthorized
mutation, and ownership-aware cleanup.

## Scope Boundaries

- Database endpoint cutover changes a connection target. It does not migrate
  schema or data. Migration remains an explicit external/operator workflow.
- The live bootstrap helper exists because a controller cannot join a Docker
  network before the initial effect creates that network. It is a harness
  boundary, not a second update language.
- `examples.scenarios.workflow` is a Roadmap 0007 planning-only fixture. It does
  not execute effects and is not the deployment application API.
- The older `DockerRuntimeInterpreter.up/down` surface remains a focused
  low-level runtime example. New control-plane workflows should use
  `DeploymentProgram`.
- CPI packaging, CPI lifecycle, public endpoint advertisement, and parent-child
  instance spawning remain Roadmap 0009 work.

## Canonical Import

Use this package boundary:

```python
from control_plane_kit.application.deploy import DeploymentProgram
```

The root `control_plane_kit` package intentionally does not flatten the
application program into the topology algebra's already-large export surface.
