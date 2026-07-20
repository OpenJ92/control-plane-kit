# Mathematical Design Preference

Status: Draft
Last updated: 2026-07-14

## Purpose

This document records a design taste for `control-plane-kit`.

The package should prefer discovering and preserving the mathematical structure
of the problem before committing to operational machinery.

Plainly:

```text
First define the algebra.
Then write interpreters.
```

This note is lower priority than security, data safety, and correctness. It is
not permission to over-abstract. It is a lens for explaining and evaluating the
core design.

## Core Questions

When a change affects architecture, ask:

```text
What are the objects?
What are the morphisms or transformations?
What are the laws?
What are the valid compositions?
What are the invariants?
What are the interpreters?
What observations prove an interpreter respected the structure?
```

These questions are especially useful when a PR changes:

- graph shape,
- block shape,
- socket semantics,
- descriptors,
- control contracts,
- activity plans,
- runtime interpreters,
- validation,
- or public examples.

## Objects

Objects are the things the package can name and reason about.

Current examples:

```text
BlockSpec
ApplicationBlock
DataBlock
ProxyBlock
RuntimeContext
ProviderSocket
RequirementSocket
SocketConnection
DeploymentRecipe
DeploymentGraph
ControlVariable
EnvironmentContract
ActivityPlan
RuntimeState
```

An object should be inspectable. If an important concept cannot be represented
as data, future interpreters, validators, UIs, and MCP adapters cannot reason
about it.

## Morphisms And Transformations

Transformations move from one representation to another.

Current and intended examples:

```text
DeploymentRecipe -> DeploymentGraph
DeploymentGraph -> descriptor
DeploymentGraph x DeploymentGraph -> GraphDiff
GraphDiff -> ActivityPlan
ActivityPlan -> RuntimeState
EnvironmentContract -> contract descriptor
SocketConnection -> environment assignment
```

Good transformations are:

- explicit,
- testable,
- inspectable,
- and preferably pure until an interpreter boundary.

## Laws

Laws are the rules that must hold for the structure to be meaningful.

Examples:

```text
Provider protocol must match requirement protocol.
Graph compilation should not perform runtime effects.
Runtime interpreters must not change topology meaning.
Secrets are never emitted in descriptors.
Activity plans are inspectable before execution.
Data mutation requires validation and verification.
Mutation routes require auth unless explicitly read-only/development-only.
```

When laws are implicit, they are easy to break. Prefer tests that read like law
checks.

## Valid Composition

Composition is how small pieces become larger systems.

Examples:

```text
ProviderSocket(HTTP) can satisfy RequirementSocket(HTTP).
ProviderSocket(POSTGRES) cannot satisfy RequirementSocket(HTTP).
RuntimeContext can contain blocks and socket connections.
ActivityPlan can sequence activities when dependencies are satisfied.
ControlVariable can generate a requirement socket when it is env-backed.
```

A major purpose of the compiler and validators is to reject invalid
composition before runtime effects occur.

## Invariants

Invariants are facts that should remain true across representations.

Examples:

```text
Node identity survives graph descriptor round trips.
Runtime context membership survives compilation.
Socket connection endpoints remain named.
Secret values remain redacted.
Activity plans preserve the intended target graph.
Runtime interpreters report observed state without rewriting desired topology.
```

When a PR changes a representation, it should state which invariants it
preserves or introduces.

## Interpreters As Realizations

Interpreters realize the abstract structure in a concrete domain.

Examples:

```text
Docker interpreter:
  DeploymentGraph -> containers, networks, env vars

Descriptor interpreter:
  DeploymentGraph -> JSON-friendly descriptors

UI interpreter:
  DeploymentGraph -> visual nodes, edges, sockets, warnings

MCP interpreter:
  control plane state -> tools/resources for AI agents

Activity executor:
  ActivityPlan -> runtime changes
```

Interpreters should be boring. The interesting structure should already be
visible in the data they interpret.

## Practical Coding Consequences

Prefer:

```text
BlockSpec x RuntimeImplementation x BlockSockets
```

over:

```text
DockerFastApiRouterWithPostgresAndHealthCheckRole
```

Prefer:

```text
DeploymentRecipe -> DeploymentGraph -> ActivityPlan -> Executor
```

over:

```text
function that discovers topology while starting containers
```

Prefer:

```text
descriptor, validation result, plan, event
```

over:

```text
hidden state in a long-lived object
```

Prefer product values and small interpreters over subclass trees that mix
orthogonal axes.

## When Not To Overdo It

Not every helper function needs a new algebra.

Do not invent objects, laws, or interpreters when:

- the behavior is local and obvious,
- there is only one concrete example,
- the abstraction would make examples harder to read,
- or the name is more impressive than the behavior.

The goal is not to decorate code with mathematical language. The goal is to keep
the core system understandable, compositional, and interpretable.

## PR Explanation Hook

When a PR changes architecture, the PR decision log should include a short
mathematical design note:

```text
Mathematical design note

- Objects:
  ...
- Transformations:
  ...
- Laws/invariants:
  ...
- Valid compositions:
  ...
- Interpreter boundary:
  ...
```

This section is optional for trivial PRs. It is expected when a PR changes
public algebra, graph shape, descriptors, contracts, interpreters, validators,
or activity planning.

