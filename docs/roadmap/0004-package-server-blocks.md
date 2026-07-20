# Roadmap 0004: Package Server Blocks

Status: In progress
Depends on: Roadmap 0003
Parent issue: OpenJ92/control-plane-kit#1
Roadmap branch: `roadmap/0004-package-server-blocks`

## Motivation

The package-provided servers are the examples users will copy. If they use
hardcoded special cases, users will copy hardcoded special cases. If they use
contracts, users will understand the contract model.

Roadmap 0003 delivered the missing substrate: `EnvironmentContract`,
`RuntimeContract`, redacted descriptors, derived resources, and contract-backed
hello/router examples. Roadmap 0004 turns that substrate into reusable server
blocks that can be placed in a deployment graph around ordinary application
code.

## Goal

Provide a small library of controllable server blocks:

- hello server block,
- HTTP proxy block,
- active router block,
- weighted load balancer block,
- HTTP multiplexer block,
- rate limiter block.

Each server should have:

- a block factory,
- provider/requirement sockets,
- an environment or runtime contract,
- application traffic routes,
- control routes when the block is controllable,
- tests,
- and a small example.

## Non-Goals

- Do not build production-grade nginx/HAProxy replacements.
- Do not optimize for high throughput yet.
- Do not make the control plane part of the application traffic path.
- Do not make every server inherit from a giant generic proxy class.
- Do not implement TCP/Postgres blocks in this roadmap; keep those as a later
  protocol-specific roadmap after HTTP server blocks are coherent.

## Inherited Work From Roadmap 0003

The original draft listed these first steps:

1. Convert hello server to `EnvironmentContract`.
2. Convert active router to runtime contract variables.

Those are now inherited from Roadmap 0003:

```python
class HelloEnvironment(EnvironmentContract):
    message = TextVariable("message", metadata={"env": "HELLO_MESSAGE"})
```

```python
class RouterRuntimeState(RuntimeContract):
    active_target = RuntimeValueVariable("active_target", required=True)
    targets = RuntimeMapVariable("targets", required=True)
```

Roadmap 0004 should keep those patterns and promote them into reusable package
server blocks.

## Suggested Issue Topology

1. Add package server module layout and hello block factory.
2. Add HTTP proxy server block.
3. Add HTTP active router server block.
4. Add weighted HTTP load balancer block.
5. Add standalone HTTP multiplexer block.
6. Add HTTP rate limiter block.
7. Add block composition examples and traffic/control-route documentation.

## Target Shapes

Hello:

```python
class HelloEnvironment(EnvironmentContract):
    message = TextVariable("message", metadata={"env": "HELLO_MESSAGE"})

block = hello_server_block("hello", message="Hello, world!")
```

Proxy:

```python
class ProxyRuntime(RuntimeContract):
    target = RuntimeValueVariable("target", required=True)

block = http_proxy_block("proxy")
```

Active router:

```python
class ActiveRouterRuntime(RuntimeContract):
    targets = RuntimeMapVariable("targets", required=True)
    active_target = RuntimeValueVariable("active_target", required=True)
```

Weighted balancer:

```python
class WeightedBalancerRuntime(RuntimeContract):
    targets = RuntimeMapVariable("targets", required=True)
    weights = RuntimeMapVariable("weights", required=True)
```

Multiplexer:

```python
class MultiplexerRuntime(RuntimeContract):
    target = RuntimeValueVariable("target", required=True)
    observers = RuntimeMapVariable("observers", required=False)
```

The multiplexer is its own server. Its application traffic law is:

```text
one incoming request
  -> forwarded to exactly one primary target
  -> copied to zero or more observer targets
  -> response returned from the primary target
```

Rate limiter:

```python
class RateLimiterRuntime(RuntimeContract):
    target = RuntimeValueVariable("target", required=True)
    max_requests = RuntimeValueVariable("max_requests", required=True)
```

## Implementation Notes

- Keep each server in an obvious module under `control_plane_kit/servers/`.
- Keep traffic routes and control routes separate.
- Control routes use the route/capability/contract machinery already present.
- Concrete modules are better than an unreadable universal proxy abstraction.
- Shared helpers are fine after two or more servers genuinely need them.
- Use in-memory/test clients first; do not require FastAPI to run the core test
  suite unless the optional server dependency is installed.

## Security

Roadmap 0004 introduces application traffic forwarding. Every child PR must
state:

- which headers/body/path/query data are forwarded,
- whether request bodies are copied to observers,
- whether logs are bounded and redacted,
- what mutation routes exist,
- whether mutation requires the control-route token,
- and what assumptions remain development-only.

Do not rely on Docker private networking as the only security argument.

## Validation

- Each server has descriptor tests.
- Each server has traffic behavior tests.
- Each server has control state tests when mutable.
- Mutable servers prove runtime changes affect later requests.
- Multiplexer proves observers can receive copied request data while the
  primary target still receives traffic.
- Rate limiter proves allowed and rejected traffic paths.
- `./test.sh`
- `python3 -m compileall control_plane_kit tests examples`
- `git diff --check`

## Handoff

The control-plane interface vertical should consume these servers through
contracts and capabilities. Leave descriptors stable enough for MCP/UI work,
and document any server behavior that is intentionally toy-grade rather than
production-grade.
