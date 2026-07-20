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

## Migration State

The canonical declaration and catalog values now live in
`control_plane_kit.products.servers`. Concrete declarations still assembled in
`control_plane_kit.servers.catalog` move one representative at a time:

```text
webhook delivery -> #558
embedded FastAPI product -> #559
CoreDNS -> #560
```

This temporary assembly location is not a second product language. It consumes
the canonical `ProductDeclaration` and `ProductCatalog` values. Each migration
must retire its old module home rather than leave a compatibility facade.
