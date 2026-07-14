# Roadmap 0002: Runtime Interpreter Foundation

Status: Draft
Depends on: Roadmap 0001

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

1. Add `RuntimeInterpreter` protocol and `RuntimeState` records.
2. Add Docker activity primitives for network/container lifecycle.
3. Implement Docker interpreter for simple HTTP blocks.
4. Add Postgres container support through existing Postgres implementation
   descriptors.
5. Move hello/router live demo onto the interpreter.
6. Add cleanup policy and idempotent stop behavior.
7. Document unsupported cross-runtime behavior.

## Target API

```python
recipe = hello_router_demo()
graph = compile_recipe(recipe)

interpreter = DockerRuntimeInterpreter(
    project_name="control-plane-kit-demo",
    cleanup_policy=CleanupPolicy.ON_STOP,
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

