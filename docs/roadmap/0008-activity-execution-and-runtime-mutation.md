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
- block control client interface,
- Docker-backed safe activities,
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
   - Wait for health.
   - Drain node as advisory first.
   - Keep provider interface narrow.

7. Add block control client capabilities.
   - Read block capabilities.
   - Register target.
   - Switch target.
   - Patch runtime variable where supported.
   - Query health/status.
   - Respect control-route auth.

8. Add Docker local runtime executor.
   - Safe start/stop for local demo nodes.
   - Retained Postgres handling where needed.
   - No accidental deletion of retained data.

9. Add control-route-backed router switch example.
   - Start candidate service.
   - Wait for health.
   - Register target.
   - Switch router.
   - Record events.

10. Add failure and compensation behavior.
    - Failed step records partial state.
    - Completed compensatable steps run compensation in reverse completion
      order.
    - Compensation failures are recorded.

11. Add observed-state updates.
    - Record runtime evidence after execution.
    - Mark stale/unknown where appropriate.
    - Do not silently rewrite desired topology from observed state.

12. Add live local smoke example.
    - Use package-provided blocks.
    - Demonstrate a safe switch or replacement.
    - Record activity events.

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

- Approved plan can be claimed once.
- Unapproved plan cannot execute.
- Duplicate execution request is idempotent.
- Executor emits events for each step.
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
blocks are demo-only versus durable protocol. Roadmap 0009 must not create a
Hub-specific executor or a special control-plane-instance node path.
