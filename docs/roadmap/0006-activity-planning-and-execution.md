# Roadmap 0006: Activity Planning And Execution

Status: Draft
Depends on: Roadmap 0002, Roadmap 0003, Roadmap 0005

## Motivation

Graph diffing explains what changed. Activity planning explains how to move
from one graph to another. Execution applies that plan through runtime
interpreters and control routes.

This is where the package becomes operational.

## Goal

Implement a typed activity AST and executor:

```text
current graph + desired graph
  -> diff
  -> ActivityPlan
  -> approval
  -> execution
  -> observed runtime state
```

## Non-Goals

- Do not promise perfect rollback.
- Do not automate destructive changes without approval.
- Do not mutate application code.
- Do not treat all graph changes as restarts.

## Suggested Issue Topology

1. Expand activity AST.
2. Add dependency-aware plan validation.
3. Add approval gate interface.
4. Add executor interface.
5. Add Docker executor support for safe activities.
6. Add control-route executor support for variable/target updates.
7. Add blue/green router swap example.
8. Add failure and pause behavior.

## Activity Candidates

```text
StartNode
StopNode
HealthCheck
WaitForHealthy
RegisterTarget
SwitchTarget
DrainTarget
RemoveTarget
SetVariable
RefreshDerivedResource
CreateRuntimeResource
DeleteRuntimeResource
```

## Target Example

```python
current = compile_recipe(current_recipe)
desired = compile_recipe(desired_recipe)

plan = plan_transition(current, desired)
approval.approve(plan)

result = executor.execute(plan)
```

Expected plan for a router-backed backend swap:

```text
StartNode(api-v2)
WaitForHealthy(api-v2)
RegisterTarget(auth-router, api-v2)
SwitchTarget(auth-router, api-v2)
DrainTarget(auth-router, api-v1)
StopNode(api-v1)
```

## Implementation Notes

- Start linear. Add fan-out only when dependencies are explicit.
- Prefer blue/green transitions where graph shape supports them.
- Reload policy determines whether a connection change becomes `SetVariable` or
  restart/drain.
- Every destructive activity should be visible in the plan.
- Execution should emit events for UI/MCP/CLI observers.

## Validation

- Empty graph to target graph produces start activities.
- Backend swap behind router produces start/register/switch/drain/stop.
- Mutable variable change produces `SetVariable`.
- Immutable variable change rejects live mutation or produces restart plan.
- Executor stops before destructive activities without approval.
- `./test.sh`
- `python3 -m compileall control_plane_kit tests`
- `git diff --check`

## Handoff

The UI vertical needs activity plans to be renderable. Keep plan descriptors
stable and human-readable.

