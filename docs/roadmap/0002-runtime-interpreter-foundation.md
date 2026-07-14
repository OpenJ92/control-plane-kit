# Roadmap 0002: Runtime Interpreter Foundation

Status: In progress
Depends on: Roadmap 0001
Parent issue: OpenJ92/control-plane-kit#17
Roadmap branch: `roadmap/0002-runtime-interpreter-foundation`

## Motivation

The package can describe topology as values. The next step is proving that a
compiled graph can be interpreted into a live system without making Docker the
core model.

Docker is the first runtime because it is local, visible, and testable. It is
not the architecture.

## Goal

Introduce a first-class runtime interpreter layer:

```text
DeploymentRecipe
  -> compile_recipe
  -> DeploymentGraph
  -> RuntimeInterpreter
  -> RuntimeState
```

The first implementation should support a small Docker deployment containing a
hello server, an active router, and optionally Postgres.

## Non-Goals

- Do not build Kubernetes, ECS, EC2, RDS, or Cloudflare interpreters yet.
- Do not make Docker fields part of `DeploymentGraph`.
- Do not implement full graph migration in this vertical.
- Do not solve secrets or live variable mutation here.

## Suggested Issue Topology

1. #32: Define runtime interpreter protocol and state records.
2. #37: Add Docker planning activities without Docker side effects.
3. #33: Implement Docker runtime executor lifecycle.
4. #34: Run hello HTTP blocks through Docker interpreter.
5. #35: Add Postgres and router runtime examples.
6. #36: Document cleanup policy and unsupported runtime boundaries.

## Target API

```python
recipe = hello_router_demo()
graph = compile_recipe(recipe)

interpreter = DockerRuntimeInterpreter(
    project_name="control-plane-kit-demo",
    cleanup_policy=CleanupPolicy.REMOVE_ON_STOP,
)

state = interpreter.up(graph, runtime_id="local-docker")

try:
    assert state.node("hello-earth").healthy
finally:
    interpreter.down(state)
```

## Implementation Notes

- Runtime contexts in the graph determine which blocks an interpreter owns.
- Container names are runtime state, not graph identity.
- Host health checks must use host-published ports, not Docker private DNS.
- Docker cleanup must be explicit and reliable.
- Unsupported graph features should fail before partial startup when possible.
- Cross-runtime edges should remain in the graph even when Docker cannot realize
  them yet.


## Completed Runtime Shape

Roadmap 0002 produced the first runtime interpreter pipeline:

```text
DeploymentRecipe
  -> compile_recipe
  -> DeploymentGraph
  -> DockerRuntimeInterpreter.plan_start
  -> RuntimePlan
  -> DockerRuntimeInterpreter.up
  -> RuntimeState
  -> DockerRuntimeInterpreter.down
```

The Docker interpreter keeps Docker-specific resource identity in activity and
state values. The graph remains a pure description of blocks, sockets,
connections, and runtime contexts.

Cleanup policy is explicit:

- `CleanupPolicy.REMOVE_ON_STOP` stops/removes owned containers and removes the
  owned Docker network.
- `CleanupPolicy.PRESERVE_ON_STOP` stops containers but does not remove
  containers or the network.

Unsupported boundaries are explicit:

- Cross-runtime edges are graph-valid but Docker realization rejects them before
  startup.
- Host port publishing and host health checks are not implemented yet.
- Live Docker smoke tests are optional; ordinary validation uses fake clients.
- Environment values are redacted in Docker activity descriptors but still
  delivered to the Docker client for execution.

## Validation

- Unit tests for interpreter planning without Docker effects.
- Integration tests for a tiny Docker hello deployment.
- Integration tests for hello -> router -> hello.
- Cleanup test proving containers/networks are removed or preserved according
  to policy.
- Failure test for unsupported runtime combinations.
- `./test.sh`
- `python3 -m compileall control_plane_kit tests`
- `git diff --check`

## Handoff

The `EnvironmentContract` vertical needs the runtime interpreter to inject
bootstrap environment values. Record the exact shape of environment assignment
in `RuntimeState`.

