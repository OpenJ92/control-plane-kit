# Design

`control-plane-kit` separates four concerns.

## 1. Core Graph

The core graph is pure data:

- `Node`
- `Endpoint`
- `Edge`
- `DeploymentGraph`

It should not know about Docker, Kubernetes, Cloudflare, AWS, Postgres, or any
particular application.  It should remain cheap to instantiate in tests and easy
to serialize.

## 2. Activity Planning

The planner compares two graphs and emits an activity plan.

The first planner is intentionally conservative:

1. start new nodes,
2. verify startable/checkable nodes,
3. add edges,
4. switch mutable edges,
5. remove old edges,
6. stop removed nodes.

Future planners can produce non-linear activity DAGs for fanout, parallel
startup, approval gates, database verification, and rollback branches.

## 3. Proxy Algebra

Proxy-like infrastructure is modeled as:

```text
ProxyNode = NetworkProtocol x ProxyBehavior x ProxyImplementation
```

That means:

- HTTP active-target routing and Postgres active-target routing can share the
  same behavior shape.
- HAProxy can implement both HTTP and TCP/Postgres forms.
- PgBouncer can implement Postgres connection pooling.
- A cloud load balancer can implement the same behavior later without changing
  the graph language.

This is meant to avoid a class hierarchy such as:

```text
HttpRouter
HttpLoadBalancer
TcpRouter
TcpLoadBalancer
PostgresRouter
PostgresPool
...
```

Convenience constructors are fine later, but they should build the compositional
core under the hood.

## 4. Runtime Interpreters

Runtimes are interpreters for activity plans.  The dry-run runtime is effect
free.  A Docker runtime, Kubernetes runtime, Cloudflare runtime, or AWS runtime
should live behind the same conceptual boundary.

The runtime owns effects.  The graph owns intent.

## Future UI Direction

An operator UI should be capability-driven.  A node with `switch-target` can
show a switch control.  A node with `logs` can show logs.  A node with `health`
can show health.  This keeps the UI from becoming a pile of one-off screens for
every deployment lego.

## Things This Kit Should Not Pretend

- TCP switching is not SQL understanding.
- A database topology switch is not a complete data migration.
- Health checks are not business validation.
- A graph diff is not automatically safe to execute.
- Runtime credentials need a real secret-provider story before production use.
