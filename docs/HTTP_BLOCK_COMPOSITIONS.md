# HTTP Block Compositions

Roadmap 0004 adds the first package-provided HTTP server blocks. They are meant
to be small topology objects that can be placed on a graph canvas, connected by
sockets, compiled into a deployment graph, and eventually interpreted by a
runtime.

The core shape remains:

```text
DeployBlock = BlockSpec x RuntimeImplementation x BlockSockets
```

The new HTTP blocks are all `ProxyBlock` values. They are package-provided
servers, but they still obey the same product form as application blocks:

```text
ProxyBlock = BlockSpec x RuntimeImplementation x BlockSockets
```

## Objects

The objects are blocks:

- `hello_server_block`: demo application block.
- `http_proxy_block`: one target, direct forwarding.
- `http_active_router_block`: one active target selected from a target registry.
- `http_weighted_load_balancer_block`: multiple targets selected by weight.
- `http_multiplexer_block`: one primary target plus observer side channels.
- `http_rate_limiter_block`: one target behind a quota gate.

Each block advertises:

- provider sockets: endpoints the block exposes,
- requirement sockets: env-backed endpoint requirements the block needs filled,
- capabilities: operator powers such as health, targets, switching, observers,
  and metrics.

## Morphisms

The morphisms are socket connections:

```python
SocketConnection("app", "internal", "proxy", "target")
```

That says:

```text
provider app.internal -> requirement proxy.target
```

At compile time, the provider endpoint is converted into the consumer's
environment binding. For a proxy, this means:

```text
PROXY_TARGET_URL=http://app:8000
```

Application code and package block code do not need to know who provided the
value. They only receive the value appropriate for their runtime.

## Example: Proxy

```python
from control_plane_kit import DockerRuntime, SocketConnection
from control_plane_kit.servers import hello_server_block, http_proxy_block

DockerRuntime(children=(
    hello_server_block("app", message="Hello through proxy"),
    http_proxy_block("proxy"),
    SocketConnection("app", "internal", "proxy", "target"),
))
```

Law:

```text
request -> proxy -> target -> response
```

The proxy copies method, path/query, headers except `Host`, and body to the
target. The target response is returned to the caller.

## Example: Active Router

```python
DockerRuntime(children=(
    hello_server_block("app-v1", message="Hello from v1"),
    hello_server_block("app-v2", message="Hello from v2"),
    http_active_router_block("router"),
    SocketConnection("app-v1", "internal", "router", "active"),
))
```

Law:

```text
request -> router(active_target) -> active target -> response
```

The first Docker command is hydrated by `ACTIVE_TARGET_URL`. The richer behavior
model uses runtime variables:

```python
class HttpActiveRouterRuntime(RuntimeContract):
    targets = RuntimeMapVariable("targets", required=True)
    active_target = RuntimeValueVariable("active_target", required=True)
```

## Example: Weighted Load Balancer

```python
DockerRuntime(children=(
    hello_server_block("app-a", message="Hello from A"),
    hello_server_block("app-b", message="Hello from B"),
    http_weighted_load_balancer_block("balancer"),
    SocketConnection("app-a", "internal", "balancer", "target-a"),
    SocketConnection("app-b", "internal", "balancer", "target-b"),
))
```

Law:

```text
weights {a: 2, b: 1} => route sequence a, a, b, a, a, b, ...
```

The first implementation uses deterministic weighted cycling rather than random
selection. Determinism keeps examples inspectable and tests stable.

## Example: Multiplexer

```python
DockerRuntime(children=(
    hello_server_block("primary", message="Primary response"),
    hello_server_block("observer", message="Observer response"),
    http_multiplexer_block("multiplexer"),
    SocketConnection("primary", "internal", "multiplexer", "primary"),
    SocketConnection("observer", "internal", "multiplexer", "observer-a"),
))
```

Law:

```text
request -> primary -> response to caller
        -> observer side-channel
```

The primary target owns the response. Observers receive copied request data as a
side effect. Observer failures are fail-open in the demo block.

Security note: multiplexers copy traffic. The demo command copies method,
path/query, headers except `Host`, and body. Do not use this with secrets, PII,
payment data, or production traffic without an explicit filtering and policy
layer.

## Example: Rate Limiter

```python
DockerRuntime(children=(
    hello_server_block("app", message="Allowed response"),
    http_rate_limiter_block("limiter"),
    SocketConnection("app", "internal", "limiter", "target"),
))
```

Law:

```text
remaining > 0 => forward and decrement
remaining = 0 => return 429 Too Many Requests
```

The first rate limiter is a deterministic quota gate. It is not a distributed
abuse-control system. It has no per-client keys, clocked windows, persistence,
or cluster-wide counters.

## Demo Boundary

These server blocks are deliberately small. Their stdlib Docker commands are
for local examples and topology exercises.

The command scripts are rendered from packaged Jinja2 templates under
`control_plane_kit/servers/templates/`. Block modules should pass template
context, not build Python source from raw string lists. This keeps generated
server code reviewable and gives future block implementations a clear precedent.

They are not production replacements for hardened infrastructure such as nginx,
Envoy, HAProxy, Cloudflare, Kubernetes Services, managed load balancers, or
distributed rate-limit systems.

The value is the algebra:

```text
blocks advertise sockets
socket connections compile env bindings
runtime variables represent post-start mutable state
capabilities describe operator controls
runtime interpreters turn graphs into effects
```

This is enough for a UI to let an operator place blocks, connect sockets, and
understand the resulting topology before touching a live runtime.
