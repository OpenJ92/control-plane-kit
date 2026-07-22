# EXTRACT.G Run 0001

Status: #667 reduced bootstrap review in progress.

Parent: #601

Topology:

```text
#820 complete
  -> #667 reduced bootstrap review
    -> #668 final reduced bootstrap closeout
      -> mandatory operator stop before #831/#832/#821
```

## Scope Boundary

EXTRACT.G closes the extracted bootstrap around the architecture that now
exists. It does not implement durable operations, product registration, Hello
workflow acceptance, recursive cpk-server deployment, or interpreter/runtime
extraction.

Current baseline:

```text
EXTRACT.E
  extracted core release-candidate complete

EXTRACT.F
  control-plane-kit-servers exists
  cpk-server is a published OCI-backed server product

SERVER-SEED
  hello-server, http-active-router, http-multiplexer, and postgres-server
  are published/catalogued/smoked product artifacts
```

The next executable architecture after EXTRACT.G is no longer old Hello
acceptance. The correct head of the operations path is:

```text
#831 pure ProductReference language
  -> #832 durable RegisteredProduct admission/store
    -> #821 durable cpk-server operations
```

## Frozen Reference Lookover

Reference tag:

```text
pre-server-product-extraction-2026-07-20
```

Files inspected for #667 law extraction:

```text
tests/test_package_server_catalog.py
tests/test_architecture_dependencies.py
tests/test_mcp_read.py
tests/test_operation_command_service.py
tests/test_operation_postgres_primitives.py
examples/read_interface_demo_server.py
control_plane_kit/products/servers/catalog.py
control_plane_kit/stores/unit_of_work.py
control_plane_kit/stores/postgres.py
control_plane_kit/workflows/command_service.py
control_plane_kit/workflows/planning.py
control_plane_kit/workflows/execution_admission.py
control_plane_kit/workflows/execution_coordinator.py
control_plane_kit/workflows/run_lifecycle.py
```

One guessed frozen path, `tests/test_cpk_server_process.py`, did not exist.
That confirms the process proof was distributed across read/MCP/demo/control
tests rather than one process-local test module.

## Extracted Law Cards

### Product Catalogue Identity

Reference:

```text
tests/test_package_server_catalog.py
```

Behavioral laws:

- product catalogue identity is closed and deterministic;
- every closed product appears exactly once;
- catalogue declarations carry executable evidence, not free-form claims;
- capability claims without evidence fail closed;
- product identity round-trips through graph descriptors;
- unknown persisted product identity fails closed;
- free-form metadata cannot override typed product identity.

Obsolete structure discarded:

- frozen `PackageServerProduct` enum as core-owned product universe;
- package-owned server imports from the root package;
- built-in knowledge of Hello, CoreDNS, webhook, or cpk-server inside core.

Current extracted reading:

```text
pure ProductDescriptorDocument/ProductCatalogue values
  remain core language

durable workspace admission of a product
  belongs to operations
```

This is why #831 precedes #832.

### Architecture Dependency Rings

Reference:

```text
tests/test_architecture_dependencies.py
```

Behavioral laws:

- core stays dependency-light and does not import runtime/server surfaces;
- stores/workflows are not imported by pure product/graph language;
- effect materialization/interpreters do not select store truth themselves;
- transport clients are owned by adapter/interpreter surfaces;
- static Docker environment mappings and environment metadata escape hatches
  are rejected.

Current extracted reading:

```text
control-plane-kit-core
  pure language/contracts

control-plane-kit-operations
  durable stores, UoW, command services

control-plane-kit-servers/cpk-server
  process wrapper and endpoint hosting
```

Operations should be a sibling distribution in `OpenJ92/control-plane-kit`, not
a new repository.

### HTTP/MCP Read Boundary

References:

```text
tests/test_mcp_read.py
examples/read_interface_demo_server.py
```

Behavioral laws:

- MCP/read tools are deterministic and bounded;
- mutation-like tools fail closed when exposed as read-only;
- read routes delegate to read services;
- graph/control-surface reads redact endpoint values;
- HTTP and MCP should remain adapters over the same services, not independent
  truth owners.

Current extracted reading:

EXTRACT.F proved the cpk-server HTTP/MCP wrapper shape around shared service
contracts. EXTRACT.G only reviews that boundary. Durable read services and
Postgres-backed projections still belong to #821.

### Durable Command And Store Laws

References:

```text
tests/test_operation_command_service.py
tests/test_operation_postgres_primitives.py
```

Behavioral laws:

- one command commits a coherent set of records atomically;
- idempotent replay returns original state;
- conflicting intent fails explicitly;
- concurrent writers converge through database constraints;
- late write failure rolls back all earlier writes in the same command;
- session/action ordinals and terminal transitions are concurrency-safe;
- stores expose primitives but command services own transaction boundaries.

Current extracted reading:

These laws are not EXTRACT.G implementation work. They are the first real
content of #821, and #832 will add another store-like surface for admitted
products:

```text
RegisteredProductStore
  workspace-scoped
  descriptor-digest-pinned
  source-evidence-backed
  UnitOfWork-owned
```

### Runtime And Product Smoke Evidence

References:

```text
gate-f-live-test.sh
gate-d-live-test.sh
generated-hello-live-test.sh
SERVER-SEED smokes in control-plane-kit-servers
```

Behavioral laws:

- published OCI digest is execution identity;
- local image tags are development evidence only;
- product-level smoke is not durable workflow acceptance;
- process start is not application readiness;
- database readiness must prove database acceptance, not merely TCP reachability;
- retained data must remain distinct from disposable compute.

Current extracted reading:

SERVER-SEED product artifacts are ready as future operations targets. They do
not prove cpk-server can plan, approve, admit, execute, observe, or advance
those products.

## #667 Review Findings

The reduced bootstrap is coherent with the extracted architecture.

Established capabilities:

```text
core release-candidate language
  -> server package catalogue values
    -> published cpk-server OCI product
      -> published seed product catalogue
```

What is proven:

- core extraction is complete for required core parity;
- cpk-server has published OCI image and descriptor evidence;
- server catalogue can publish completed product declarations without importing
  product process code;
- seed products are digest-pinned and smoke-tested as product artifacts;
- stale Hello workflow acceptance is deferred to #819;
- interpreter/runtime laws remain visible under #806;
- operations implementation is explicitly deferred to #821.

What is not proven:

- cpk-server cannot yet import/register/select product descriptors durably;
- no `RegisteredProductStore` exists in extracted operations;
- no extracted Postgres UnitOfWork/store implementation exists yet;
- no extracted graph workflow can run
  `plan -> approve -> admit -> claim -> execute -> advance`;
- no recursive child cpk-server deployment is proven;
- no cloud/runtime interpreter extraction is complete.

## Product Registration Boundary

The post-SERVER-SEED boundary is now explicit:

```text
descriptor source
  InlineDescriptor | RemoteDescriptorUrl | CatalogueUrl

pure reference
  ProductIdentity x descriptor_sha256

durable admission
  workspace x ProductReference x descriptor_document x source_evidence
```

Core should own only the pure side:

```text
ProductDescriptorDocument
ProductIdentity
ProductCatalogue
ProductDescriptorDigest
ProductReference / CataloguedProductReference
descriptor validation and codec laws
```

Operations should own durable truth:

```text
RegisteredProduct
RegisteredProductStore
ImportProductDescriptor command
source evidence
workspace ownership
imported_by / imported_at
trust, replacement, disable, and revocation policy
```

URLs, uploads, and remote catalogues are acquisition paths. Graph planning
must consume pinned descriptor identity/digest.

## Current Coordinates

cpk-server baseline:

```text
image: ghcr.io/openj92/control-plane-kit-servers/cpk-server@sha256:5bdd63738f8d2ea211e02681fbb80760cb581c6435f1c7dd854bceba0b949416
descriptor sha256: 8f78690897a0e47bf697aaadc4fcd37ac2a9aeaf243a815ad0252e9245a3658e
```

SERVER-SEED catalogue checksum:

```text
0efffbdfe0581b8f47b7cb854480f2b4e79227835b0a0e91f4207fbfe1aa3d7b
```

Seed product coordinates:

```text
hello-server
  ghcr.io/openj92/control-plane-kit-servers/hello-server@sha256:0b5d62c2706bdfc5b53b67c7e0a72e36b8af7d13f8b2abf26eaa6e6eb7dda5f0

http-active-router
  ghcr.io/openj92/control-plane-kit-servers/http-active-router@sha256:9edd29c8b62f6413c7acb4009bfa655c065a31a0eac8728ec9d4350122e0a60d

http-multiplexer
  ghcr.io/openj92/control-plane-kit-servers/http-multiplexer@sha256:2b6466d87c7642691c4ce2ee52022450d7b7cf1055f1f25a1449adbb5c8131ec

postgres-server
  docker.io/library/postgres@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777
```

## #667 Decision

Proceed to #668. No implementation issue is required inside EXTRACT.G.

#668 should publish the final reduced bootstrap closeout and stop before #831,
#832, or #821 begins.
