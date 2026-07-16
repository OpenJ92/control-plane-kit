# Roadmap 0008: Activity Execution And Runtime Mutation

Status: Draft
Depends on: Roadmap 0005, Roadmap 0006, Roadmap 0007

## Motivation

Planning explains what should happen. Execution changes the world.

This roadmap is where `control-plane-kit` becomes operationally dangerous, so it
must be built under the data-engineering, security, and approval laws already
defined.

The key distinction:

```text
ActivityPlan
  describes approved intended effects

ActivityRun
  records one execution attempt

ActivityEvent
  records bounded observations during execution

ObservedState
  records what the system saw afterward
```

Execution must not be a hidden side effect of saving a graph. It must be an
approved, claimed, event-emitting workflow.

Execution has two different data-engineering regimes:

```text
ActivityRun / ActivityEvent / ObservedState writes
  -> ordinary Postgres unit-of-work transactions owned by execution services

Docker, block control routes, cloud APIs, filesystem state, and secrets
  -> saga/external-effect steps with durable progress events
```

Sagas do not replace the Postgres unit of work. They coordinate effects that no
single database transaction can protect.

## Goal

Implement the first executable activity pipeline.

This roadmap should provide:

- execution request/claim mechanics,
- activity run lifecycle,
- saga/compensation grammar,
- runtime executor interface,
- dependency-aware multi-node activity planning,
- block control client interface,
- Docker-backed safe activities,
- explicit host-port publication for intentionally exposed local endpoints,
- real health/readiness observation rather than optimistic started-is-healthy
  state,
- control-route-backed safe activities,
- activity events,
- observed-state updates,
- pause/failure/resume shape,
- and at least one live local smoke example.

## Non-Goals

- Do not implement every runtime provider.
- Do not implement full cloud execution.
- Do not promise perfect rollback.
- Do not execute unapproved plans.
- Do not run destructive activities without stronger approval.
- Do not require a graph database.
- Do not hide partial failure.

## Suggested Issue Topology

1. Review and adapt saga grammar.
   - Compare `pottery-factory-saga` with control-plane execution needs.
   - Add generic saga program/activity/interpreter modules.
   - Preserve the law: Saga describes work; Saga does not decide domain truth.

2. Add activity execution request model.
   - Durable request before effects.
   - Idempotency key.
   - Approved plan reference.
   - Actor identity.
   - The request must be written in the same unit of work as the command that
     asks for execution.

3. Add activity run lifecycle.
   - `queued`, `claimed`, `running`, `paused`, `succeeded`, `failed`,
     `compensating`, `compensated`, `partially_failed`, `cancelled`.
   - Guarded status transitions.
   - Prevent duplicate concurrent runs for the same plan unless explicitly
     retrying.
   - Guard status transitions with Postgres transactions and row-level locking
     or equivalent compare-and-set semantics.

4. Add executor interface.
   - Claim approved plan.
   - Compile plan to executable activity sequence/saga.
   - Execute activities.
   - Emit events.
   - Update observed state.
   - Open small explicit transactions for claim/run/event state changes.
   - Keep external effects outside those transactions, with durable events on
     both sides of each effect.

5. Add activity event writer.
   - Step started.
   - Step succeeded.
   - Step failed.
   - Compensation started.
   - Compensation succeeded/failed.
   - Run paused/resumed.
   - Run completed.
   - Each event append must participate in an explicit transaction owned by the
     execution service.

6. Add runtime executor capabilities.
   - Start node.
   - Stop node.
   - Restart node.
   - Wait for health using bounded timeout, interval, and failure policy.
   - Distinguish process/container start from application readiness.
   - Drain node as advisory first.
   - Keep provider interface narrow.

7. Add dependency-aware multi-node activity planning.
   - Distinguish communication edges from explicit startup/readiness
     dependencies.
   - Compile provider-before-consumer ordering where blocks declare it.
   - Wait for required provider readiness before starting dependent consumers.
   - Detect dependency cycles and require an explicit simultaneous/deferred
     policy rather than choosing an arbitrary order.
   - Plan reverse-order compensation or stop where safe.
   - Keep the scheduler generic; it must not know about CPI, Auth, or Postgres
     domain names.

8. Add block control client capabilities.
   - Read block capabilities.
   - Register target.
   - Switch target.
   - Patch runtime variable where supported.
   - Query health/status.
   - Respect control-route auth.

9. Add Docker local runtime executor.
   - Safe start/stop for local demo nodes.
   - Publish declared host ports only when the topology/runtime policy requests
     them; do not expose every provider socket by default.
   - Preserve container-private provider addresses separately from
     host-observed addresses.
   - Probe declared health paths and report unknown/unhealthy instead of marking
     every started container healthy.
   - Retained Postgres handling where needed.
   - No accidental deletion of retained data.

10. Add control-route-backed router switch example.
   - Start candidate service.
   - Wait for health.
   - Register target.
   - Switch router.
   - Record events.

11. Add failure and compensation behavior.
    - Failed step records partial state.
    - Completed compensatable steps run compensation in reverse completion
      order.
    - Compensation failures are recorded.

12. Add observed-state updates.
    - Record runtime evidence after execution.
    - Mark stale/unknown where appropriate.
    - Do not silently rewrite desired topology from observed state.

13. Add live local smoke example.
    - Use package-provided blocks.
    - Demonstrate a safe switch or replacement.
   - Record activity events.
   - Exercise an intentionally host-published endpoint with real HTTP health
     evidence so Roadmap 0009 can reuse the same mechanism for CPI.

## Target Execution Flow

```text
approved ActivityPlan
  -> execution request
  -> executor claims request
  -> ActivityRun opened
  -> activities interpreted as saga where useful
  -> runtime/control-route calls
  -> ActivityEvent*
  -> ObservedState update
  -> ActivityRun closed
```

## Saga Shape

The saga module should remain generic:

```python
program = then(
    step(start_api_v2),
    step(wait_for_api_v2),
    step(register_router_target),
    step(switch_router_target),
)

result = await interpret(program)
```

Activity-specific compensation is supplied by adapters:

```text
start api-v2
  compensation: stop api-v2 if still unused

switch router to api-v2
  compensation: switch router back to prior target if healthy
```

Some activities may be non-compensatable. They must say so.

## Implementation Notes

- Follow ADR 0008 strictly.
- Import desired-state graph concepts from `control_plane_kit.topology` and
  activity-plan concepts from `control_plane_kit.planning`.
- Do not place execution, runtime mutation, workflow persistence, or store code
  inside either pure algebra package.
- Store-local execution state uses normal Postgres unit-of-work transactions.
- Saga steps only wrap external effects that cannot be made ACID by Postgres.
- Execution claims need locking or guarded status transitions.
- Effects happen after durable execution request.
- Every external effect emits an event.
- Event payloads are bounded and redacted.
- Runtime providers and block clients are capability boundaries.
- Destructive activities must require stronger approval.
- Local package server blocks are teaching/demo blocks unless explicitly marked
  production-grade.

## Validation

The reusable planning corpus in `examples/scenarios/` is the acceptance basis
for execution. Roadmap 0008 must preserve each scenario's typed operations,
dependency order, risk, and readiness while extending it with expected runtime
evidence:

```text
PlanningScenario
  + ActivityRun expectation
  + ActivityEvent partial order
  + ObservedState expectation
  + compensation/failure expectation
```

Blocked scenarios must remain non-executable and must not acquire approval or
run records merely because an executor exists.

The database scenario is initially limited to switching between endpoints that
the desired graph already treats as provisioned. Data copy, replication
catch-up, schema migration, consistency verification, and old-database
retirement are separate external effects. Roadmap 0008 must not infer those
effects from an ordinary Postgres socket change.

The first executable boundary should therefore be:

```text
operator or provider establishes migration readiness
  -> durable, typed readiness evidence
    -> approved endpoint cutover
      -> bounded health/consistency observation
```

A future database-migration capability may produce and execute the readiness
evidence itself. Until that capability exists, migration remains explicitly
user/provider-managed and database replacement plans must fail closed rather
than treating a newly started empty database as a valid target.

- Approved plan can be claimed once.
- Unapproved plan cannot execute.
- Duplicate execution request is idempotent.
- Executor emits events for each step.
- Multi-node plans honor declared readiness dependencies and reject unresolved
  cycles.
- Docker start does not imply health; declared health checks produce observed
  healthy, unhealthy, timeout, or unknown state.
- Host ports are published only by explicit policy and remain distinct from
  Docker-private endpoint addresses.
- Socket-derived environment assignments reach the started container without
  appearing unredacted in activity descriptors or events.
- Failure records partial state.
- Compensation runs in reverse completion order where possible.
- Compensation failure is visible.
- Observed state updates are recorded separately from graph truth.
- Live local smoke example passes when Docker is available.
- `./test.sh`
- `python3 -m compileall control_plane_kit tests`
- `git diff --check`

## Handoff

Roadmap 0009 will package the control-plane instance server as an ordinary
`ApplicationBlock` and use this executor to make its lifecycle ordinary
approved activity work. The handoff must include stable
activity-run/event descriptors, idempotent execution requests, saga boundaries,
runtime provisioning capabilities, capability-route expectations, and which
blocks are demo-only versus durable protocol.

The handoff must specifically prove that Roadmap 0009 can:

```text
start Postgres
wait for Postgres readiness
start an ApplicationBlock with socket-derived environment
publish one explicitly requested host endpoint
wait on the application's declared health path
record the private and public observations separately
retain Postgres across application replacement where policy requires
```

Roadmap 0009 must not repair those generic Docker/runtime behaviors with a CPI
startup script, a Hub-specific executor, or a special control-plane-instance
node path.
