# Gate G Package Consolidation Closeout

## Result

The package-consolidation vertical establishes a legible deployment kernel,
closed domain-language homes, durable-operation ownership, interpreter
boundaries, graph-visible server products, and explicit process entrypoints
inside one repository and one distribution.

The final ownership vocabulary is:

```text
core         owns the deployment language
domains      own independent closed languages
operations   own durable control-plane truth
interpreters perform representation and external effects
products     are graph-visible deployable values
entrypoints  compose dependencies and run processes
```

The observed package graph contains 55 package nodes and 415 source-backed
dependency edges. It has zero cycles. Cycle allowances no longer exist in the
architecture policy language.

## Public Package Map

```text
control_plane_kit/
  core/
    algebra.py
    capabilities.py
    configuration.py
    control_routes.py
    environment.py
    implementations.py
    lifecycle.py
    planning/
    secrets.py
    topology/
    types.py
    verification.py

  domains/
    discovery/
    idempotency/
    load_generation/
    webhook/

  operations/
    planning/
    webhook/

  interpreters/
    configuration_rendering.py
    webhook_http.py

  products/servers/
    catalog.py
    coredns.py
    http_auth_gateway.py
    webhook_delivery.py
    support/
    templates/

  entrypoints/
    cli.py
    webhook_server/
```

This is not a claim that every module has already moved for visual symmetry.
The exhaustive inventory records 160 Python modules and distinguishes:

```text
remain          52
moved            2
move            78
split-and-move  28
```

`move` and `split-and-move` are explicit deferred physical work. They identify
canonical ownership without creating a second implementation. Completed
representative moves removed their old homes immediately.

## Kernel And Facade

The smallest claimable kernel is `control_plane_kit.core`. Its controlling
pipeline is:

```python
graph = compile_recipe(recipe)
validated = validate_graph(graph)
changes = diff_graphs(current, validated)
plan = compile_activity_plan(changes)
```

Equationally:

```text
DeploymentRecipe
  -> DeploymentGraph
  -> ValidatedGraph
  -> GraphDiff
  -> ActivityPlan
```

`import control_plane_kit` remains a convenience facade rather than the kernel
itself. It may expose the core pipeline and the immutable values of exactly five
pure operational packages:

```text
operations.contracts
operations.effects
operations.execution
operations.saga
operations.scheduling
```

It does not import domains, product catalogs, stores, concrete interpreters,
network clients, FastAPI applications, environment bootstrap, or process
entrypoints. The clean base-wheel proof enforces that boundary.

## Representative Product Shapes

### Webhook Delivery

```text
products.servers.webhook_delivery  graph-visible exterior
domains.webhook                    closed command/result language
operations.webhook                 durable intent, journal, projection, UoW
interpreters.webhook_http          bounded external HTTP effect
entrypoints.webhook_server         FastAPI/process composition
```

This proves that a substantial internal application remains one ordinary
server product to its parent graph.

### Auth Gateway

```text
products.servers.http_auth_gateway
  = typed test-only policy
  x opaque secret references
  x generated process command
  x graph-visible sockets and verification
```

The package gateway remains a test fixture, not a claim that CPK owns a user's
authentication application. Its declaration no longer imports a runnable
FastAPI app or optional server dependencies.

### Proxy Family

Routers, multiplexers, balancers, rate limiters, and other teaching products
remain physically under the transitional `servers` package. The inventory
assigns their uniform `products.servers` exterior and forbids product imports of
stores, UnitOfWork, runtime clients, and process bootstrap. They have no second
canonical implementation.

### CoreDNS

```text
DiscoveryRegistrationRecord*
  -> CoreDnsConfiguration
  -> ConfigurationArtifact*
  -> ApplicationBlock
  -> DeploymentGraph
  -> GraphDiff
```

CoreDNS owns the product-specific discovery projection. Discovery has no reverse
dependency. A/AAAA projection intentionally preserves addresses and loses
ports; SRV remains a future typed extension.

## Self-Hosting Boundary

The package topology supports recursive spawning without recursive ownership:

```text
parent ControlPlaneInstance graph truth
  contains ChildControlPlaneInstanceBlock and its lifecycle evidence

child ControlPlaneInstance private truth
  contains its own graph, approvals, history, observations, and stores
```

The future child CPI is a server product to its parent and an independent
control plane internally. The parent does not import or mutate the child's
stores.

## Validation Evidence

```text
Focused architecture/inventory/structure suite: 34 tests, OK
Installed base, HTTP, Postgres, and server wheel matrix: OK
Complete Docker/Postgres suite: 1112 tests in 174.218 seconds, OK

CoreDNS live:
  official image, DNS TCP/UDP, health/readiness, read-only artifacts,
  replay, ownership, and cleanup, OK

Webhook live:
  unauthorized rejection, persistence, restart, exact replay, signature,
  durable history, allowlist, DeploymentProgram, and cleanup, OK

Heterogeneous service infrastructure live:
  discovery lifecycle, OTLP trace, signed webhook, verification evidence,
  redaction, DeploymentProgram, and cleanup, OK

Owned container/network/volume residue after proofs: zero
```

No assertions were weakened, no skips were added, and no application behavior
was replaced with mocks. The closeout changes only architecture policy,
inventory interpretation, and documentation.

## Deviations And Residual Risk

The original target tree was intentionally not applied as one complete physical
rewrite. Doing so would have mixed broad import churn with product work and
violated the vertical's own non-goal of relocation for symmetry. The remaining
movement records are therefore a reviewable backlog.

The largest residual physical boundary is the transitional `servers` family and
its global catalog assembly. It is acyclic and product-classified, but not yet
as immediately legible as the representative canonical products. Existing
operational packages such as `effects`, `execution`, `stores`, and `workflows`
have the same physical-deferment caveat. New code must not expand those
transitional roots merely for convenience.

The root facade intentionally exports selected pure operational values. Its
exact owner set is architecture-tested so this convenience cannot silently grow
to include stores, workflows, products, or processes.

## PgBouncer Handoff

PgBouncer issue #431 must begin directly with the settled shape:

```text
products.servers.pgbouncer
  typed PackageServerProduct identity
  exact Postgres provider/requirement sockets
  typed secret-free configuration values
  strict ConfigurationTemplate rendering
  immutable read-only ConfigurationArtifact values
  opaque SecretReference delivery
  exact pinned Docker image and ownership policy
  truthful health/readiness/verification
```

Product code must not import stores, UnitOfWork, current-graph readers, Docker
clients, network clients, or process entrypoints. Database endpoint cutover does
not imply schema or data migration. Secret content is resolved only at the
external-effect boundary. Any runnable package process belongs in an explicit
entrypoint, and the old home is retired in the same change if an existing module
is moved.
