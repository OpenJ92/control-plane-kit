# Roadmap 0004: Package Server Blocks

Status: Draft
Depends on: Roadmap 0003

## Motivation

The package-provided servers are the examples users will copy. If they use
hardcoded special cases, users will copy hardcoded special cases. If they use
contracts, users will understand the contract model.

## Goal

Provide a small library of controllable server blocks:

- hello server,
- proxy,
- active router,
- weighted load balancer,
- request logger/multiplexer,
- rate limiter.

Each server should have:

- a block factory,
- provider/requirement sockets,
- a contract,
- application traffic routes,
- control routes,
- tests,
- a small example.

## Non-Goals

- Do not build production-grade nginx/HAProxy replacements.
- Do not optimize for high throughput yet.
- Do not make the control plane part of the traffic path.
- Do not make every server inherit from a giant generic proxy class.

## Suggested Issue Topology

1. Convert hello server to `EnvironmentContract`.
2. Convert active router to runtime contract variables.
3. Add proxy server.
4. Add weighted balancer.
5. Add request logger/multiplexer.
6. Add rate limiter.
7. Add examples composing multiple blocks around one application.
8. Add documentation explaining traffic routes versus control routes.

## Target Shapes

Hello:

```python
class HelloEnvironment(EnvironmentContract):
    world = TextVariable("HELLO_WORLD", mutable=True)


@app.get("/hello")
def hello() -> dict:
    return {"message": f"Hello, {env.get('world')}!"}
```

Active router:

```python
class ActiveRouterRuntime(RuntimeContract):
    targets = RuntimeMap("targets", mutable=True)
    active_target = RuntimeValue("active_target", mutable=True)
```

Weighted balancer:

```python
class WeightedBalancerRuntime(RuntimeContract):
    targets = RuntimeMap("targets", mutable=True)
    weights = RuntimeMap("weights", mutable=True)
```

## Implementation Notes

- Keep each server in an obvious module or folder.
- Keep traffic routes and control routes separate.
- Control routes should use the same auth/redaction machinery as application
  contracts.
- Concrete modules are better than an unreadable universal proxy abstraction.
- Shared helpers are fine after two or more servers genuinely need them.

## Validation

- Each server has descriptor tests.
- Each server has traffic behavior tests.
- Each server has control route tests.
- Mutable servers prove runtime changes affect later requests.
- Request logger/multiplexer proves observers can receive copied request data
  while the application target still receives traffic.
- `./test.sh`
- `python3 -m compileall control_plane_kit tests`
- `git diff --check`

## Handoff

The control-plane interface vertical should consume these servers through
contracts and capabilities. Leave descriptors stable enough for MCP/UI work.

