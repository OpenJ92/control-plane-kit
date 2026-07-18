# Roadmap 0008 Closeout Review

## Result

Roadmap 0008 is implementation-complete on its roadmap branch and ready for
operator review in draft PR #228. The roadmap PR remains intentionally
unmerged.

The completed program accepts two topology values and carries their typed
transition through planning, approval, admission, claim, execution, recovery,
and guarded advancement:

```python
deploy = Deploy(
    current,
    desired,
    PrepareDeployment(Plan(planning)),
    Approve(approval),
    ExecuteApprovedDeployment(
        Admit(admission),
        Claim(lifecycle),
        Execute(coordinator),
        Advance(advancement),
    ),
)

prepared = deploy(request)
approved = deploy.approve(prepared, approval_grant)
result = deploy.execute_approved(approved, execution_grant)
```

Approval and recovery remain explicit suspension points. The composition never
fabricates grants or hides operator-required uncertainty.

## Objects

The central durable and pure objects are:

```text
DeploymentRecipe and DeployBlock
DeploymentGraph and ValidatedGraph
GraphDiff and ActivityPlan
ApprovalRequest and ApprovalDecision
ExecutionRequest, AdmittedRun, ActivityRun, and ActivityEvent
SagaProgram, SagaState, and ExecutionSchedule
EffectRequest and MaterializedEffectRequest
ObservedState and recovery projections
DeploymentTransition, suspensions, continuations, and terminal outcomes
Deploy
```

`Deploy` is not a replacement for these values. It is a parameterized program
that composes their existing interpreters and command services.

## Morphisms

The operational path is now legible as a composition:

```text
compile_recipe
  : DeploymentRecipe -> DeploymentGraph

validate_graph
  : DeploymentGraph -> ValidatedGraph

diff_graphs
  : CurrentGraph x DesiredGraph -> GraphDiff

compile_activity_plan
  : GraphDiff -> ActivityPlan

Plan
  : DeploymentPlanRequest -> ApprovalSuspension | NoOpDeployment

Approve
  : ApprovalSuspension x ApprovalGrant -> ApprovedDeployment

Admit
  : ApprovedDeployment x ExecutionGrant -> AdmittedDeployment

Claim
  : AdmittedDeployment x WorkerIdentity -> ClaimedDeployment

Execute
  : ClaimedDeployment -> ExecutionOutcome | RecoverySuspension

Advance
  : SuccessfulExecution -> CompletedDeployment
```

Inside execution, the pure and effectful boundaries remain separate:

```text
ActivityEvent* -> project_activity_journal -> SagaState
ActivityPlan x SagaState -> derive_schedule -> ready typed effects
EffectRequest x pinned graph -> MaterializedEffectRequest
MaterializedEffectRequest -> bounded adapter effect
effect result -> ActivityEvent + observation + projection
```

## Executable Laws

The suite makes the following laws executable:

- the four graph-pair transition forms are exhaustive and typed;
- no-op creates no approval, admission, run, effect, or advancement record;
- every resumed deployment value belongs to the same graph pair as `Deploy`;
- unapproved and review-blocked plans cannot execute;
- one operator command owns one explicit Postgres UnitOfWork;
- stores never commit independently;
- no transaction or lock spans an external effect;
- admitted work remains pinned to its approved desired graph;
- current graph advances only after successful evidence-backed execution;
- observations never rewrite desired topology;
- effect uncertainty pauses and never blindly replays;
- compensation reconstructs from one append-only journal in reverse durable
  completion order;
- original and compensation failures remain independently visible;
- HTTP control mutation fails closed without authorization;
- secrets do not enter durable descriptors, events, logs, or errors;
- Docker mutation proves ownership and respects retained-data policy;
- host publication is explicit and private-only is the default;
- process existence, reachability, health, and readiness remain distinct.

## Validation Evidence

```text
./test.sh
  707 passed
  0 skipped

planning corpus
  11 typed graph-pair scenarios

execution corpus
  17 real-Postgres cases through Deploy

./gate-f-live-test.sh
  explicit loopback publication passed
  unauthorized mutation returned 401 and preserved the blue target
  authenticated Deploy update changed Hello, blue! to Hello, green!
  cleanup removed only label-proven owned resources
```

The acceptance corpus, live boundary matrix, and cross-module review contain
the detailed evidence.

## Deviations And Provisional Boundaries

The roadmap changed shape as its laws became concrete:

- Gate F became a topological chain of small application-composition issues
  rather than one facade implementation.
- Cross-module review discovered that resumed values were not bound to the
  parameterized graph pair. Issue #379 fixed that without adding a duplicate
  deployment identifier.
- Initial Docker bootstrap and final teardown use a split harness because the
  controller cannot join a network before that network exists. Ordinary update
  execution uses canonical `Deploy`; the helper is not a second update
  language.
- The planning-only `examples.scenarios.workflow` fixture remains for older
  examples. It does not execute effects and is not the public deployment path.
- Local live names and ports are fixed proof inputs, not a multi-tenant
  allocator.
- Database endpoint cutover is implemented; database migration is not.
- Cloud and mixed-runtime interpreters remain future work.
- CPI packaging and parent-to-child spawning remain Roadmap 0009.

These limitations are visible and do not weaken the generic execution algebra.

## Security And Data Engineering Review

`Deploy` is a higher-order program over separately transactional command
services. It is deliberately not one database transaction:

```text
short transaction: record durable intent
  -> commit
    -> bounded external effect
      -> short transaction: record result, event, observation, and projection
```

Earlier durable facts survive later failure. Authorization is explicit at
approval, admission, worker claim, recovery, and control-route boundaries.
Effect material is derived from pinned graph truth, and secret references are
resolved only at the adapter boundary without entering durable evidence.

## Roadmap 0009 Handoff

Roadmap 0009 may package the control-plane instance as an ordinary
`ApplicationBlock` and deploy it through:

```python
from control_plane_kit.application.deploy import DeploymentProgram
```

It must reuse the generic graph, planning, execution, Docker, probe,
observation, retention, and recovery machinery. It must not add a CPI-specific
executor, startup script, transaction manager, or `started == healthy`
shortcut. Guarded instance lifecycle issue #246 remains a prerequisite before
destructive CPI lifecycle commands are exposed.
