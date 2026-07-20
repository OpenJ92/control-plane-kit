# Package Ownership Boundaries

Control Plane Kit uses dependency direction to preserve values-plus-interpreters
design as the package grows:

```text
core
  <- domains
  <- operations
  <- interpreters
  <- products
  <- entrypoints
```

This is a dependency partial order, not a requirement that every vertical have
equal depth. A package-owned server always has a graph-visible product exterior.
It may additionally have a domain language, durable operations, interpreters,
and a runnable entrypoint.

## Ownership Laws

```text
Core owns the deployment graph and pure planning language.
Domains own independent closed command/result languages.
Operations own durable control-plane truth and transaction boundaries.
Interpreters perform representation or external effects.
Products are graph-visible declarations.
Entrypoints compose dependencies and run processes.
```

A product declaration has the conceptual shape:

```text
ProductDeclaration
  = BlockSpec
  x RuntimeImplementationSpec
  x RoleSockets
  x DeclaredCapabilities
  x VerificationContract
```

The current Python value stores these components through its typed `DeployBlock`
and exact executable-capability evidence. `ProductCatalog` is an immutable,
identity-unique collection of those declarations.

```python
from control_plane_kit.products.servers import (
    ProductCatalog,
    ProductDeclaration,
)
```

Importing this declaration language does not import FastAPI, HTTP clients,
Postgres drivers, Docker clients, stores, UnitOfWork, environment bootstrap, or
runnable applications.

An entrypoint instead owns process composition:

```text
Entrypoint
  = explicit dependencies
  x process configuration
  x application construction
  x startup / shutdown ownership
```

The read-only CLI is the first canonical example at
`control_plane_kit.entrypoints.cli`. It may read process environment and perform
network I/O; neither power belongs to a product declaration.

## Uniform Server Exterior

Webhook delivery, discovery, idempotency, load generation, CoreDNS, proxies,
routers, multiplexers, balancers, and future ControlPlaneInstance packaging are
all server products from the parent graph's perspective. Their interiors vary:

```text
simple product
  = declaration + interpreter/entrypoint

substantial product
  = declaration + domain + operations + interpreters + entrypoint
```

The graph observes identity, sockets, capabilities, verification, configuration,
and runtime implementation. It does not observe internal stores or application
composition.

## Consolidation State

The consolidation proof is complete for the dependency floor and representative
verticals:

```text
core topology and planning       canonical physical homes
admitted domain languages        canonical physical homes
strict configuration rendering  canonical interpreter home
webhook delivery                 product + domain + operations + interpreter + entrypoint
auth gateway                     product declaration separated from generated process
CoreDNS                          product-owned projection and templates
```

The package graph is unconditionally acyclic. There is no migration allowance
that can excuse a cycle.

The inventory remains both an ownership map and a relocation backlog. A
`movement` value of `move` or `split-and-move` records an intentionally deferred
physical relocation; it does not claim that a second canonical implementation
exists. The broader operational packages and teaching-server family remain in
their existing homes because moving every file for visual symmetry was not a
goal of this vertical. New products, beginning with PgBouncer, use the canonical
`control_plane_kit.products.servers` exterior directly.

When a deferred relocation is undertaken, the old home must be retired in the
same change. The package is unreleased, so compatibility facades are not kept.
