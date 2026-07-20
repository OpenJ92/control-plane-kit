# ADR 0009: Package Boundary Topology

## Status

Accepted for Roadmap 0008 package consolidation.

## Context

`control-plane-kit` now contains several related languages and their
interpretations:

- deployment topology and planning;
- discovery, webhook, idempotency, and load-generation languages;
- durable workflows, stores, execution history, recovery, and read models;
- Docker, HTTP, probe, verification, and configuration interpreters;
- package-owned deployable servers;
- CLI, MCP, FastAPI, and process bootstrap surfaces.

The code remains substantially values-first, but its physical package layout
does not reveal that structure. Pure deployment values are adjacent to stores,
FastAPI applications, Docker effects, and process composition. A permissive
package-edge allowlist also accepts cycles once both directions are listed.

The purpose of consolidation is not to make the repository look larger or more
formal. It is to make the existing algebras claimable and to make accidental
dependency reversal executable as a test failure.

## Decision

Keep one repository and one Python distribution. Organize it as a directed
acyclic package graph with six ownership kinds:

```text
core
  deployment description, topology, validation, comparison, and pure planning

domains
  independent closed command/result languages

operations
  durable control-plane truth, workflows, scheduling, recovery, and reads

interpreters
  rendering, transport, Docker, probes, verification, and external effects

products
  graph-visible declarations of package-owned deployable capabilities

entrypoints
  explicit composition and runnable process boundaries
```

The dependency direction is a DAG rather than a strict stack:

```text
domains      -> core
operations   -> core + accepted domains
interpreters -> core + explicit operational protocols
products     -> core + accepted domains + interpreter specification values
entrypoints  -> explicit composition of required outer packages
```

Those arrows describe possible dependency families, not universal permission.
The checked-in module inventory declares the exact intended destination and
the architecture policy declares the exact legal edges.

## The Kernel Test

The core language is exactly what is required to express this effect-free
pipeline:

```text
DeploymentRecipe
  -> DeploymentGraph
  -> ValidatedGraph
  -> GraphDiff
  -> ActivityPlan
```

A module belongs in core only if that pipeline requires its meaning without
persistence, networking, processes, external effects, or optional server
dependencies.

This excludes immutable code that is pure but operational. Saga state,
execution events, scheduling, recovery, and operator projections are pure data
or pure transformations, but they describe execution after planning. They
belong to operations, not the deployment kernel.

The kernel includes pure specification values needed by graph truth:

- block and socket algebra;
- capability and control-route descriptors;
- runtime implementation specifications;
- public, socket-derived, and secret-reference delivery descriptors;
- immutable configuration artifacts;
- verification contracts, but not probe clients;
- topology, validation, descriptors/codecs, and diff;
- activity-plan values, codec, and compiler, but not recovery orchestration.

Codecs remain adjacent to the language they interpret. There is no global
descriptor package.

## Domain Admission

`domains` is not a miscellaneous pure-code folder. A language qualifies when
it has independent identities, bounded commands or inputs, closed results or
events, validation and transition laws, codecs when durable, and meaning that
does not depend on a particular product or process.

The current admitted languages are:

```text
domains.discovery
domains.webhook
domains.idempotency
domains.load_generation
```

The executable admission matrix and exact source-to-destination manifest live
in `docs/architecture/domain-language-admission.md`. Admission is deliberately
independent of deployability: all four behaviors also retain uniform
graph-visible declarations under `products.servers`.

Saga and execution languages remain operational because their meaning is the
control plane's durable execution process. A proxy, multiplexer, retry server,
or load balancer does not earn a domain merely because it has typed product
configuration. It may become one later if an independent command/result
language emerges.

## Uniform Server-Product Exterior

Every package-owned deployable server has one graph-visible exterior:

```text
control_plane_kit.products.servers.<product>
```

This is what a parent deployment graph can select, connect, configure, verify,
realize, observe, and control. Its conceptual product form is:

```text
ServerProductDeclaration
  = ProductIdentity
  x BlockSpec
  x RuntimeImplementationSpec
  x ProviderSockets
  x RequirementSockets
  x DeclaredCapabilities
  x ConfigurationRequirements
  x VerificationContract
  x LifecyclePolicy
```

The internal depth of the implementation does not change its status as a
server product. Webhook delivery spans all five outer concerns:

```text
products.servers.webhook_delivery  graph-visible exterior
domains.webhook                    closed webhook language
operations.webhook                 durable workflow and stores
interpreters.webhook_http          external HTTP effect
entrypoints.webhook_server         runnable process composition
```

A multiplexer can be a valid server product with no independent domain and no
durable store. CoreDNS can be a server product that interprets pure discovery
values into DNS configuration without importing the discovery registry.

Therefore:

```text
products.servers = what the parent graph can see and run
domains / operations / interpreters / entrypoints
                 = what a server may be made from internally
```

This does not classify every graph node as a server. Runtime environments,
retained data, managed databases, and external endpoints keep their actual
graph roles. Evidence may later justify sibling product categories.

The future self-hosted server follows the same law:

```text
products.servers.control_plane_instance
  -> opaque deployable exterior visible to its parent

entrypoints.control_plane_instance
  -> process that owns its own database, graph truth, and activity history
```

Recursive spawning does not make the parent authoritative for the child's
workspace. The parent owns only its graph-visible child product, public endpoint
contract, authentication relationship, and lifecycle evidence.

## Product and Process Import Law

Importing a product declaration or catalog entry must not import:

- FastAPI or a runnable app;
- a Postgres store or UnitOfWork;
- an HTTP or Docker client;
- process environment loading;
- process bootstrap.

Entrypoints are the composition roots that may import a product declaration,
its operations, and its concrete interpreters. Products are values;
entrypoints are processes.

The current `servers` modules frequently mix these roles and are therefore
classified by their destination product exterior with `split-and-move` status.
The move issues must extract process and effect code; they must not carry the
mixed module unchanged into a prettier directory.

## Discovery to CoreDNS

Discovery does not know CoreDNS exists. The CoreDNS product owns the pure
projection:

```text
DiscoveryRecord
  -> CoreDnsConfiguration
    -> ConfigurationArtifact
```

That is an intentional product interpretation edge:

```text
products.servers.coredns -> domains.discovery
```

It is not permission for CoreDNS to import discovery registry stores,
UnitOfWork, service processes, or mutable registry truth. A/AAAA projection
preserves addresses and deliberately loses endpoint ports; SRV or another
explicit port-bearing contract is future work rather than hidden inference.

## Operations Vocabulary

Use `operations`, not `runtime`, for durable control-plane behavior. Runtime is
already a graph concept denoting Docker, ECS, EC2, and similar execution
substrates.

Use CPK for the package or system. Write `ControlPlaneInstance` explicitly for
the deployable server product unless a later vocabulary ADR introduces another
abbreviation.

## Current Findings Recorded Before Movement

The exhaustive inventory records these deliberate migration inputs:

1. `planning.recovery` imports policies while policy services import planning,
   creating a conceptual cycle hidden by the old allowlist.
2. `servers` combines graph product declarations with FastAPI/Jinja process
   implementations and catalog process imports.
3. `webhook` combines a domain language, durable operations, an HTTP
   interpreter, and application composition behind one package entrance.
4. Shared HTTP message values live under `servers`, causing the idempotency
   operation to import the broad server package.
5. CoreDNS is coherent behaviorally but physically colocated with mixed server
   modules. Its descriptor, TCP/UDP, immutable-artifact, projection, ownership,
   and live DNS behavior are relocation invariants.
6. The root package is already lightweight after settling and must remain so.
7. `contracts.py` combines typed declarations with process-environment access,
   locks, candidate preparation, derived-resource lifecycle, and live mutation.
   It is operational as written, not part of the deployment-to-plan kernel.
   Its current root re-export is a migration input for #551/#553 rather than a
   reason to broaden core.

These findings are not compatibility obligations. The package is unreleased;
canonical imports replace old homes and obsolete homes are removed.

## Machine-Readable Inventory

`docs/architecture/package-module-inventory.json` is the migration source of
truth. Every current Python module has exactly one record containing:

- current module and source file;
- owner and destination;
- role and motivation;
- direct internal and optional external dependencies;
- semantic roles;
- canonical exports and known package consumers;
- migration prerequisites and movement status;
- tests protecting current semantics;
- server-product domain qualification and forbidden imports when applicable.

Tests compare the manifest with the filesystem and reject missing, duplicate,
or unknown ownership. Every consolidation issue must update it as modules move.

## Migration Discipline

1. Characterize behavior before movement.
2. Establish the target DAG as executable architecture policy.
3. Move the primitive core dependency floor.
4. Move topology and pure planning without redesign.
5. Move admitted domain languages without their operations.
6. Move interpreters and strict rendering.
7. Establish products, catalog, and entrypoint foundations.
8. Prove the split on webhook, embedded FastAPI servers, and CoreDNS.
9. Remove temporary migration allowances and old homes.
10. Run base-wheel, complete Docker/Postgres, live product, and test-integrity
    validation before product development resumes.

Structural PRs are behavior preserving. A semantic change discovered during a
move becomes a separately justified issue rather than being hidden in the
relocation.

## Consequences

- The deployment algebra becomes physically legible and base-importable.
- Independent languages remain reusable without importing their products.
- Durable truth has a named owner separate from runtime substrates.
- Product catalog imports remain graph data rather than process startup.
- Self-hosting remains coherent: a parent sees a child
  `ControlPlaneInstance` as an opaque server product and does not own the
  child's graph truth or activity history.
- Some migration PRs will be mechanically large, so topology order,
  architecture tests, and exact behavior characterization are mandatory.

## Consolidation Closeout Interpretation

The representative consolidation closes with two distinct public entrances:

```text
control_plane_kit.core
  = minimal effect-free deployment kernel

control_plane_kit
  = lightweight pure facade over core and selected operational value languages
```

The facade may expose immutable saga, scheduling, execution, effect-request,
and contract values. It may not import domains, product catalogs, concrete
interpreters, stores, network clients, FastAPI applications, environment
bootstrap, or process entrypoints. This does not make those operational
languages members of core.

The observed package graph must be acyclic without exceptions. The temporary
`PackageMigrationAllowance` review value was removed at closeout; future cycles
are architecture failures, not debt that can be named and tolerated.

The inventory remains honest about deferred physical movement. `move` and
`split-and-move` mean the existing module has a canonical ownership destination
but has not been relocated. They do not authorize a parallel implementation.
The representative moves retire old homes immediately; new products begin in
their canonical package.

## Non-Goals

- separate repositories or distributions;
- compatibility import wrappers;
- redesigning working graph or runtime behavior;
- calling all pure data core;
- giving every product an artificial domain package;
- moving codecs away from their language;
- product feature development during consolidation.
