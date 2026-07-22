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

## #668 Final Reduced Bootstrap Closeout

Status: complete pending operator stop.

EXTRACT.G closes the bootstrap extraction. The system now has:

```text
control-plane-kit-core
  pure deployment/product/descriptor/operation contract language

control-plane-kit-servers
  cpk-server product wrapper
  reusable seed server products
  immutable descriptor/catalogue artifacts

SERVER-SEED catalogue
  realistic future operations targets
```

The extracted system still does not have durable cpk-server operations. That is
intentional and must remain explicit.

### Capability Now Established

The bootstrap can now claim:

```text
frozen reference laws
  -> extracted core release candidate
    -> external server-product repository
      -> published cpk-server OCI baseline
        -> seed product catalogue artifacts
          -> operations-ready handoff topology
```

It cannot yet claim:

```text
operator imports product
  -> graph references admitted product
    -> plan
      -> approve
        -> admit
          -> execute
            -> advance current graph
```

That belongs to #831, #832, and #821.

### Objects And Transformations Proven

Objects:

```text
ProductDescriptorDocument
ProductCatalogue
ContainerServerProduct descriptor
published OCI image digest
server catalogue declaration
cpk-server process wrapper descriptor
SERVER-SEED product coordinates
```

Transformations:

```text
core product descriptor
  -> server package declaration
    -> packaged catalogue checksum

server product source
  -> OCI image
    -> immutable digest
      -> descriptor digest
        -> catalogue declaration

frozen behavioral law
  -> extracted-core or server-product ownership
    -> deferred operations/interpreter handoff where not implemented
```

Laws:

- core remains product-independent and server-import-free;
- server catalogue imports values, not product processes or stores;
- published OCI digest is execution identity;
- local image builds are development evidence only;
- descriptor data, not image URLs alone, is graph-visible product contract;
- product-artifact smoke is not workflow acceptance;
- operations admission requires durable workspace truth;
- one operator command will remain one explicit Postgres transaction once
  operations exists.

### Final Coordinates

cpk-server:

```text
image: ghcr.io/openj92/control-plane-kit-servers/cpk-server@sha256:5bdd63738f8d2ea211e02681fbb80760cb581c6435f1c7dd854bceba0b949416
descriptor sha256: 8f78690897a0e47bf697aaadc4fcd37ac2a9aeaf243a815ad0252e9245a3658e
```

SERVER-SEED final catalogue checksum:

```text
0efffbdfe0581b8f47b7cb854480f2b4e79227835b0a0e91f4207fbfe1aa3d7b
```

SERVER-SEED products:

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

### Issue Ledger

Closed:

```text
#599 EXTRACT.E core release-candidate closeout
#600 EXTRACT.F cpk-server/server repository foundation
#820 EXTRACT.G topology refresh
#822 SERVER-SEED parent
#827 SERVER-SEED closeout
#667 EXTRACT.G reduced bootstrap review
```

Deferred / next:

```text
#831 ProductReference pure core language
#832 RegisteredProduct durable operations admission/store
#821 Durable cpk-server operations and Postgres workflow implementation
#819 Hello workflow acceptance after operations
#676 Recursive child cpk-server deployment
#806 Interpreter/runtime extraction
```

EXTRACT.G does not close #594 or merge the roadmap to a terminal release. It
closes only the reduced bootstrap acceptance segment and stops for operator
approval before operations begins.

### Operations Placement

Operations should be implemented as a sibling distribution in the existing
`OpenJ92/control-plane-kit` repository:

```text
control-plane-kit/
  control-plane-kit-core/
  control-plane-kit-operations/
```

Do not create a new GitHub repository for operations unless a later ADR changes
this decision.

Dependency direction:

```text
control-plane-kit-core
  <- control-plane-kit-operations
    <- control-plane-kit-servers/cpk-server
```

Rationale:

- operations and core are tightly versioned;
- operations consumes core contracts constantly;
- core contract changes will often need operations changes in the same PR;
- cpk-server depends on both;
- a separate repository would create version friction before operations
  stabilizes.

### Product Admission Handoff

The first operations work should begin with the registration split:

```text
#831 ProductReference
  pure language for identity + descriptor digest

#832 RegisteredProduct
  durable workspace admission, source evidence, store, trust policy

#821 operations
  graph/current topology, sessions, approvals, admission, execution, reads
```

Data-engineering law:

```text
one operator command = one explicit Postgres transaction
```

Application command services own commit/rollback. Stores never commit
independently.

Descriptor acquisition law:

```text
InlineDescriptor | RemoteDescriptorUrl | CatalogueUrl
  acquisition path

ProductReference(identity, descriptor_sha256)
  graph-visible pure reference

RegisteredProduct(workspace, reference, descriptor, source_evidence)
  durable operational truth
```

### Review Findings

Architecture:

- coherent;
- core/server/operations boundaries are explicit;
- no hard-coded SERVER-SEED product knowledge is required by core.

Security:

- product descriptor truth excludes secret values;
- `postgres-server` uses a secret delivery slot rather than a descriptor value;
- remote descriptor/catalogue URL import remains future work and must receive
  SSRF, redirect, size, checksum, and trust-policy review.

Data engineering:

- durable registration likely needs a new Postgres-backed store;
- descriptor admission must be workspace-scoped and digest-pinned;
- replacement/revocation policy remains unresolved and belongs to #832.

Docker ownership:

- no cpk-server runtime changed during EXTRACT.G;
- no image republish was required;
- SERVER-SEED evidence already includes published/live smokes and residue audit.

Test integrity:

- frozen laws were inspected and extracted as review context;
- no assertions were weakened;
- stale Hello workflow laws remain deferred, not erased.

### Residual Risks

- #831 must avoid making product registration durable inside core.
- #832 must avoid accepting image URLs as complete product contracts.
- #821 must avoid sharing parent/child cpk-server database truth.
- Remote descriptor acquisition will require security review before network
  fetches are accepted.
- Hello dynamic dependency sockets still need a product-parameterization
  decision before generated Hello workflow acceptance.

### Next Recommended Milestone

Begin operations only after explicit operator approval.

Recommended next prompt should start with:

```text
Execute the operations extraction topology under AGENTS.md.

Begin with #831, then #832, then the refreshed #821 child topology.
Implement operations as control-plane-kit-operations beside
control-plane-kit-core inside OpenJ92/control-plane-kit.

Do not create a new operations repository.
```

Mandatory stop:

```text
Do not begin #831, #832, #821, #819, #676, or #806 from EXTRACT.G.
```

## EXTRACT.OPERATIONS #831 ProductReference

Status: implemented on `codex/831-product-reference-language`.

### Law Cards

Frozen reference:

```text
tests/test_package_server_catalog.py
control_plane_kit/products/servers/catalog.py
```

Surviving law:

```text
product identity + executable descriptor evidence
  -> deterministic graph/planning reference
```

Discarded structural assumptions:

- package-owned server enum as the product universe;
- root imports of product server modules;
- catalogue lookup by importing Python product code;
- product-specific names inside core.

Current extracted expression:

```python
ProductReference(
    identity=ProductIdentity("cpk-servers", "hello-server", 1),
    descriptor_sha256=ProductDescriptorDigest("...64 hex characters..."),
)
```

`ProductDescriptorDigest` uses the raw SHA-256 hex already produced by
`ProductDescriptorDocument.content_digest`. This intentionally differs from OCI
image digests, which remain `sha256:<hex>` image references.

### Boundary Decision

Core now owns only the pure reference:

```text
ProductDescriptorDocument
  -> ProductReference
```

Operations will own durable admission:

```text
ProductReference
  + workspace_id
  + descriptor_document
  + source_evidence
  + imported_by/imported_at
  + trust/replacement policy
  -> RegisteredProduct
```

No acquisition path is part of `ProductReference`. URL, upload, and remote
catalogue details remain future operations source evidence.

### Evidence

Focused target-red evidence:

```text
./control-plane-kit-core/test.sh control-plane-kit-core/tests/test_product_reference.py

ImportError: cannot import name 'ProductDescriptorDigest'
```

Focused green evidence after implementation:

```text
./control-plane-kit-core/test.sh \
  control-plane-kit-core/tests/test_product_reference.py \
  control-plane-kit-core/tests/test_product_catalog.py \
  control-plane-kit-core/tests/test_product_descriptor.py \
  control-plane-kit-core/tests/test_external_product_fixture.py

379 tests passed; compileall passed; import check passed.
```

Broader validation:

```text
git diff --check
./control-plane-kit-core/test.sh
./test.sh

Full repository suite: 1214 tests passed.
```

### Handoff To #832

#832 should persist `RegisteredProduct` in operations, not core. It should use
`ProductReference` as the pure pinned identity of admitted descriptor truth and
add workspace ownership, source evidence, importer identity, timestamps, trust
policy, and replacement/revocation behavior under Postgres UnitOfWork control.

## EXTRACT.OPERATIONS #835 Topology Refresh

Status: implemented on `codex/835-operations-topology-refresh`.

### Dry-Run Finding

The original operations prompt ordered:

```text
#831 -> #832 -> #835 -> #836 -> #837 -> #838
```

That ordering is not coherent after #831. #832 is explicitly durable
operations work. It needs `control-plane-kit-operations`, an idempotent
Postgres schema foundation, and the UnitOfWork/store-bundle boundary before it
can introduce `RegisteredProductStore`.

Amended canonical order:

```text
#831
  -> #835
    -> #836 -> #837 -> #838
      -> #832
        -> #839 -> #840 -> #841 -> #842 -> #843 -> #844
          -> #845 -> #846 -> #847 -> #848 -> #849 -> #850
```

This correction was recorded on #821 and #832.

### Machine-Readable Artifact

Added:

```text
artifacts/extraction/operations-topology-refresh.json
```

The artifact records:

- the amended issue order;
- the reason #832 moved after #836/#837/#838;
- per-issue owners, outputs, and dependencies;
- frozen-law inventory by data-engineering and service boundary;
- the DeploymentProgram north star:

```text
Deploy:
  plan -> approve -> admit -> claim -> execute -> advance

initial deployment = Deploy(EmptyGraph, desired)
update             = Deploy(current, desired)
teardown           = Deploy(current, EmptyGraph)
no-op              = Deploy(graph, graph)
```

### Frozen Law Inventory

The #835 artifact keeps these law groups visible for later issues:

```text
postgres_schema_and_stores
unit_of_work
command_services
read_services_and_adapters
acceptance_and_runtime_boundary
```

Important frozen references:

```text
control_plane_kit/stores/postgres.py
control_plane_kit/stores/unit_of_work.py
control_plane_kit/stores/protocols.py
control_plane_kit/stores/records.py
control_plane_kit/workflows/*.py
tests/test_operation_postgres_primitives.py
tests/test_operation_command_service.py
tests/test_execution_store.py
tests/test_execution_schema_migration.py
tests/test_execution_concurrency.py
tests/test_recovery_concurrency.py
tests/postgres_case.py
```

### Target-Red Evidence

Focused target-red was the missing artifact:

```text
tests/test_operations_extraction_topology.py
  -> artifacts/extraction/operations-topology-refresh.json absent
```

This proves the new guard is about missing topology evidence, not broken
collection or imports.

### Handoff

#836 is next. It should create the sibling `control-plane-kit-operations`
distribution with no stores or services beyond minimal scaffolding. Keep
operations inside this repository, beside `control-plane-kit-core`; do not
create a new repository. The package boundary must prove:

```text
control-plane-kit-core
  <- control-plane-kit-operations
    <- control-plane-kit-servers/cpk-server
```

## EXTRACT.OPERATIONS #836 Operations Package Foundation

Status: implemented on `codex/836-operations-package-foundation`.

### Law Cards

```text
law: operations distribution exists as a sibling of core
expected: control-plane-kit-operations has pyproject metadata, import package,
  and Docker-first unittest harness
negative: a new GitHub repository, hidden package under core, or process-owned
  package is not accepted
owner: control-plane-kit-operations
```

```text
law: operations depends inward on core and not outward on products/processes
expected: operations may import control_plane_kit_core contract values
negative: operations must not import frozen control_plane_kit,
  control_plane_kit_servers, FastAPI, MCP, Docker, httpx, uvicorn, or psycopg
  in the foundation issue
owner: control-plane-kit-operations
```

```text
law: root validation exercises the operations package harness
expected: ./test.sh runs ./control-plane-kit-operations/test.sh after the core
  harness and before the frozen root Docker/Postgres suite
negative: operations must not become an untested sibling directory
owner: system acceptance
```

### Dry Run

Frozen lookover:

```text
control_plane_kit/stores/unit_of_work.py
control_plane_kit/stores/protocols.py
control_plane_kit/workflows/*.py
tests/test_unit_of_work.py
tests/test_operation_postgres_primitives.py
tests/test_service_infrastructure_program.py
docs/DEPLOY_PROGRAM.md
```

Finding: #836 should not port any store, schema, UnitOfWork, command service, or
adapter behavior. The frozen code confirms those are real durable semantics, so
they belong to #837 and later. #836 only creates the package shell that can
receive them.

### Target-Red Evidence

Added `tests/test_operations_package_foundation.py` before implementation. The
root Docker suite failed with three expected errors:

```text
control-plane-kit-operations/pyproject.toml missing
control-plane-kit-operations/src/control_plane_kit_operations/__init__.py missing
root test image did not expose test.sh for the harness-wiring guard
```

That red proved the missing behavior was package-boundary evidence, not broken
collection or an accidental frozen import.

### Implementation

Added the sibling distribution:

```text
control-plane-kit-operations/
  AGENTS.md
  README.md
  pyproject.toml
  test.sh
  src/control_plane_kit_operations/
    __init__.py
    foundation.py
  tests/
    test_package_boundary.py
    test_scaffold.py
```

The package exposes only an inspectable boundary value for now:

```python
OPERATIONS_PACKAGE_BOUNDARY = OperationsPackageBoundary(
    distribution="control-plane-kit-operations",
    import_package="control_plane_kit_operations",
    depends_on=("control-plane-kit-core",),
    deployment_spine=tuple(DeploymentProgramStage),
    future_owners=(
        "DeploymentProgram",
        "Deploy",
        "Postgres schema",
        "PostgresUnitOfWork",
        "store bundle",
        "command services",
        "read projections",
        "RegisteredProduct",
    ),
    excluded_owners=(
        "core pure language",
        "cpk-server process",
        "HTTP framework adapters",
        "MCP process adapter",
        "Docker runtime interpreter",
        "package-owned server products",
    ),
)
```

`psycopg` is intentionally not a dependency yet. It enters when #837/#838 add
the real Postgres schema and UnitOfWork/store bundle. This keeps the foundation
import-light while preserving the data-engineering direction.

### Validation

Focused operations package validation:

```text
./control-plane-kit-operations/test.sh
  4 tests passed
  compileall passed
  installed import smoke passed
```

Full root validation is required before the #836 PR.

### Handoff

#837 is next. It should add the operations Postgres schema foundation using the
frozen explicit-SQL/Jinja2 precedent. Keep schema installation caller
transactional, idempotent, and non-destructive. Do not introduce stores beyond
what the schema installer needs to prove migration/install policy.
