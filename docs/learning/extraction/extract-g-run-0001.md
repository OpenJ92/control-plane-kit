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

## EXTRACT.OPERATIONS #837 Postgres Schema Foundation

Status: implemented on `codex/837-postgres-schema-foundation`.

### Law Cards

```text
law: operations schema installation is caller-transactional
expected: install_schema(connection) executes on the supplied connection and
  does not commit
negative: rolling back the caller transaction leaves no created operations
  tables behind
owner: control-plane-kit-operations
```

```text
law: repeated schema installation is idempotent and non-destructive
expected: re-running install_schema preserves existing rows and constraint
  identities
negative: no unconditional DROP TABLE, DROP CONSTRAINT, TRUNCATE, or constraint
  replacement is accepted
owner: control-plane-kit-operations
```

```text
law: durable status and event columns fail closed
expected: workspace lifecycle, execution request status, activity run status,
  activity event kind, and activity event payload shape are constrained by the
  current closed language
negative: unknown strings, activity events without activity_id, run events with
  activity_id, and malformed recovery payloads are rejected by Postgres
owner: control-plane-kit-operations
```

### Dry Run

Frozen lookover:

```text
control_plane_kit/stores/postgres.py
control_plane_kit/stores/unit_of_work.py
control_plane_kit/stores/records.py
tests/test_execution_schema_migration.py
tests/test_operation_postgres_primitives.py
tests/postgres_case.py
```

Finding: the frozen schema contains valuable relational shape and constraints,
but also some in-place compatibility repair from earlier unreleased roadmap
work. The extracted operations package should use the current schema language
directly, not preserve old compatibility machinery. Stores and UnitOfWork remain
out of scope until #838 and later.

### Target-Red Evidence

Added `control-plane-kit-operations/tests/test_postgres_schema.py` before
implementation. The package harness failed for the expected reason:

```text
ModuleNotFoundError: No module named 'psycopg'
```

That red proved the missing behavior was a real Postgres schema/dependency
boundary, not broken collection or a hidden frozen import.

### Implementation

Added a package-local Postgres schema module:

```python
from control_plane_kit_core.operations.lifecycle import ActivityEventKind
from control_plane_kit_core.types import WorkspaceLifecycle

POSTGRES_SCHEMA = _SQL_ENVIRONMENT.from_string(_POSTGRES_SCHEMA_TEMPLATE).render(
    activity_event_kinds=tuple(ActivityEventKind),
    workspace_lifecycles=tuple(WorkspaceLifecycle),
)

def install_schema(connection: PostgresConnection) -> None:
    connection.execute(POSTGRES_SCHEMA)
```

The schema is rendered with strict Jinja2 from closed Python enum values where
core owns the vocabulary. Operations-local closed lists remain private to the
schema module until the corresponding record/service issue extracts them.

Tables introduced:

```text
cpk_workspaces
cpk_graph_versions
cpk_registered_products
cpk_operation_sessions
cpk_operation_actions
cpk_activity_plans
cpk_approval_requests
cpk_approval_decisions
cpk_execution_requests
cpk_activity_runs
cpk_activity_events
```

`control-plane-kit-operations/test.sh` now starts its own disposable
`postgres:16-alpine` container and runs the operations package tests against
that database. This keeps the extracted package self-validating rather than
borrowing the frozen root harness.

### Validation

Focused operations package validation:

```text
./control-plane-kit-operations/test.sh
  8 tests passed
  compileall passed
  installed import smoke passed
```

Full root validation is required before the #837 PR.

### Handoff

#838 is next. It should introduce the UnitOfWork and store-bundle boundary on
top of this schema. It should not add command-service behavior yet. Preserve:

```text
one operator command = one explicit Postgres transaction
stores never commit independently
all stores share the UnitOfWork connection
```

The runtime question remains deliberately outside operations schema work:
Docker, cloud, probe, filesystem, and HTTP effect interpreters belong to the
later runtime/interpreter extraction track.

## EXTRACT.OPERATIONS #838 Postgres UnitOfWork And Store Bundle

Status: implemented on `codex/838-uow-store-bundle`.

### Law Cards

```text
law: one application command owns one explicit Postgres transaction
expected: PostgresUnitOfWork opens one connection, vends stores over that
  connection, and commits only after explicit commit request plus clean exit
negative: stores cannot commit or roll back independently
owner: control-plane-kit-operations
```

```text
law: failed or abandoned command work rolls back completely
expected: uncommitted exit, exceptional exit, exception after commit request,
  and physical commit failure all roll back and close
negative: a partial workspace write cannot escape a failed command
owner: control-plane-kit-operations
```

```text
law: UnitOfWork lifecycle is closed
expected: stores are available only inside an active unfinished UnitOfWork;
  repeated commit requests and late rollback fail closed
negative: finished UnitOfWork cannot vend stores for late writes
owner: control-plane-kit-operations
```

### Dry Run

Frozen lookover:

```text
control_plane_kit/stores/unit_of_work.py
control_plane_kit/stores/protocols.py
control_plane_kit/stores/postgres.py PostgresStoreBundle
tests/test_unit_of_work.py
tests/postgres_case.py
```

Finding: the frozen UnitOfWork lifecycle can be ported directly, but the frozen
domain stores should not move in #838. The extracted bundle is intentionally
minimal so #832/#839 can add RegisteredProduct and workspace/graph stores
without #838 preempting their ownership.

### Target-Red Evidence

Added `control-plane-kit-operations/tests/test_unit_of_work.py` before
implementation. The focused run failed for the intended missing module:

```text
ModuleNotFoundError:
  No module named 'control_plane_kit_operations.postgres.unit_of_work'
```

### Implementation

Added:

```text
control_plane_kit_operations.postgres.stores.PostgresStoreBundle
control_plane_kit_operations.postgres.unit_of_work.PostgresUnitOfWork
control_plane_kit_operations.postgres.unit_of_work.UnitOfWorkStateError
```

The bundle currently exposes only the shared connection:

```python
@dataclass(frozen=True)
class PostgresStoreBundle:
    connection: PostgresConnection
```

This is a temporary but deliberate #838 shape: it proves connection sharing and
transaction ownership without pretending the RegisteredProduct or graph stores
already exist.

### Validation

Focused operations package validation:

```text
./control-plane-kit-operations/test.sh
  16 tests passed
  compileall passed
  installed import smoke passed
```

Full root validation is required before the #838 PR.

Full root validation:

```text
./test.sh
  extracted core validation passed
  operations package validation passed
  packaging smoke passed
  1219 root Docker/Postgres tests passed
```

### Handoff

#832 is next in the corrected topology. It should add durable RegisteredProduct
admission/store behavior on top of the #837 schema and #838 UnitOfWork. Keep
the same data-engineering law:

```text
application command service owns commit/rollback
RegisteredProductStore writes through the UnitOfWork connection
product descriptor admission is durable authority, not core language truth
```

Do not add workspace/graph stores before #839 and do not introduce runtime
interpreters.

## EXTRACT.OPERATIONS #832 RegisteredProduct Admission

Status: in progress on `codex/832-registered-product-admission`.

### Law Cards

```text
law: registration is durable operations truth
expected: workspace x ProductReference x descriptor document x source evidence
  becomes RegisteredProduct through an application service and UnitOfWork
negative: core does not gain registered workspace truth
owner: control-plane-kit-operations
```

```text
law: descriptor source evidence is closed and secret-free
expected: inline upload, remote descriptor URL, and catalogue URL evidence
  round-trip through a strict codec
negative: local host paths, URL credentials, query strings, fragments, and
  unknown source variants fail closed
owner: control-plane-kit-operations
```

```text
law: duplicate exact descriptor import is idempotent
expected: duplicate (workspace_id, descriptor_sha256) returns the first durable
  admission row
negative: second import does not overwrite source, imported_by, or imported_at
owner: control-plane-kit-operations
```

```text
law: same product identity with a different descriptor digest requires explicit
  replacement policy
expected: active conflicting identity/digest fails with ProductRegistrationConflict
negative: product identity is not last-write-wins
owner: control-plane-kit-operations
```

### Frozen Lookover

Reviewed the extracted product language and frozen catalogue/server references:

```text
control-plane-kit-core/src/control_plane_kit_core/products.py
control-plane-kit-core/tests/test_product_reference.py
control_plane_kit/servers/catalog.py
control_plane_kit/products/servers/catalog.py
```

Finding: the pure core language already has the right pinning object:

```python
ProductReference.from_document(document)
```

#832 should not invent another reference. It should persist the workspace
admission act around that reference.

### Target-Red Evidence

The first focused operations run failed on the intended missing boundary:

```text
ModuleNotFoundError:
  No module named 'control_plane_kit_operations.products'
```

### Implementation Shape

Added operations-side values:

```text
RegisteredProduct
RegisteredProductStatus
ImportProductDescriptorCommand
InlineDescriptorSource
RemoteDescriptorSource
CatalogueDescriptorSource
DescriptorSourceCodec
ProductRegistrationService
```

Added a Postgres store under the #838 bundle:

```text
PostgresUnitOfWork.stores.registered_products
```

The service owns commit:

```text
ProductRegistrationService.import_descriptor(command)
  -> open PostgresUnitOfWork
    -> registered_products.register(...)
      -> unit_of_work.commit()
```

The store never commits independently.

### Data-Engineering Finding

The first green attempt exposed an important descriptor-storage boundary.
`ProductDescriptorDocument.content_digest` is over exact canonical descriptor
bytes. Postgres `jsonb` intentionally normalizes object representation and
therefore cannot be the sole authoritative storage for exact descriptor bytes.

The schema now stores both:

```text
descriptor_document jsonb   # inspectable/queryable descriptor material
descriptor_content text     # exact canonical product.cpk.json content
```

Rows reconstruct `ProductDescriptorDocument` from `descriptor_content`; JSONB
remains projection material. This preserves exact digest identity instead of
teaching core to accept non-canonical JSONB output.

### Architecture-Policy Finding

The operations package boundary originally forbade `psycopg` everywhere, but
#837 already made Postgres an operations dependency. #832 refined the rule:
`psycopg` is allowed only below the `postgres/` adapter package. Docker,
FastAPI, HTTP clients, server products, MCP process code, and runtime
interpreters remain forbidden.

### Focused Validation

```text
./control-plane-kit-operations/test.sh
  22 tests passed
  compileall passed
  installed import smoke passed
```

Full validation:

```text
git diff --check
./test.sh
  extracted core validation passed
  operations package validation passed
  packaging smoke passed
  1219 root Docker/Postgres tests passed
```

### Handoff

#839 can now build workspace and graph stores on top of:

```text
#837 schema
#838 UnitOfWork
#832 RegisteredProductStore
```

Graph authoring should resolve product references against active
RegisteredProduct rows in the same workspace. It should not use mutable remote
URLs or OCI image references as product contract truth.

## EXTRACT.OPERATIONS #839 Workspace And Graph Stores

Status: in progress on `codex/839-workspace-graph-stores`.

### Law Cards

```text
law: workspace truth owns graph pointers
expected: WorkspaceRecord stores lifecycle, current_graph_id, desired_graph_id,
  and metadata in cpk_workspaces
negative: observed state and product registration do not rewrite workspace
  graph pointers
owner: control-plane-kit-operations
```

```text
law: graph versions are immutable workspace-scoped descriptor values
expected: GraphVersionRecord persists extracted-core GraphDescriptorCodec
  mappings and reconstructs typed graph identity through the same codec
negative: graph persistence must not erase external product/block identity
owner: control-plane-kit-operations
```

```text
law: current graph advancement primitive is compare-and-set
expected: compare_and_set_current_graph changes the pointer only when the
  caller's expected graph id is current
negative: stale pointer writes return None without last-write-wins behavior
owner: control-plane-kit-operations
```

```text
law: workspace and graph writes share one UnitOfWork connection
expected: uncommitted workspace plus graph writes roll back together
negative: neither store commits independently
owner: control-plane-kit-operations
```

### Frozen Lookover

Reviewed:

```text
control_plane_kit/stores/records.py
control_plane_kit/stores/protocols.py
control_plane_kit/stores/postgres.py
tests/test_stores.py
tests/test_current_graph_advancement.py
tests/test_desired_graph_command_service.py
```

Finding: the frozen workspace/graph store shape ports cleanly into operations:

```text
WorkspaceStore
GraphTopologyStore
```

#839 should not port desired-graph command services or current-graph
advancement services. Those stay in later command-service issues.

### Target-Red Evidence

The first focused run failed on the intended missing records module:

```text
ModuleNotFoundError:
  No module named 'control_plane_kit_operations.records'
```

### Placement Finding

Core product instantiation currently materializes graph nodes from
`ProductDescriptorDocument`/catalogue values. The #832 registered-product
resolution law belongs to #840:

```text
desired graph authoring
  -> ProductReference
    -> active RegisteredProduct in same workspace
      -> graph version save / planning
```

#839 therefore stores and reconstructs graph descriptors without importing
server packages or adding graph-authoring policy.

### Implementation Shape

Added:

```text
WorkspaceRecord
GraphVersionRecord
PostgresWorkspaceStore
PostgresGraphTopologyStore
PostgresUnitOfWork.stores.workspaces
PostgresUnitOfWork.stores.graphs
```

Focused validation:

```text
./control-plane-kit-operations/test.sh
  26 tests passed
  compileall passed
  installed import smoke passed
```

Full validation:

```text
git diff --check
./test.sh
  extracted core validation passed
  operations package validation passed
  packaging smoke passed
  1219 root Docker/Postgres tests passed
```

### Handoff

#840 should integrate RegisteredProduct with graph authoring/planning. It should
build on #839's graph persistence rather than embedding product descriptors
directly in a new command model. The command-level law should be:

```text
operator desired graph input
  -> resolve ProductReference against active RegisteredProduct rows
    -> save GraphVersionRecord
      -> set desired/current pointer through WorkspaceStore
```

Do not let unresolved product identities, mutable catalogue URLs, or OCI image
references bypass the registered-product admission boundary.

## EXTRACT.OPERATIONS #840 Registered Product Graph Authoring

Status: in progress on `codex/840-registered-product-graph-authoring`.

### Law Cards

```text
law: desired graph authoring resolves registered products by workspace
expected: every product-instantiated graph node yields ProductReference and
  must match an active RegisteredProduct in the same workspace
negative: registration in another workspace does not authorize this workspace
owner: control-plane-kit-operations
```

```text
law: descriptor digest is graph truth
expected: product_identity plus product_descriptor_digest pins the exact
  admitted product descriptor before graph save or planning
negative: same product identity with a different descriptor digest is rejected
  rather than silently selecting a newer descriptor
owner: control-plane-kit-operations
```

```text
law: desired graph publication is one transaction
expected: graph version save and workspace desired pointer update commit
  together through PostgresUnitOfWork
negative: stale pointer rejection leaves no orphan graph version
owner: control-plane-kit-operations
```

```text
law: selectable products are an operations read view over active registration
expected: GraphAuthoringService.selectable_products returns secret-free active
  ProductReference/display fields for a workspace
negative: revoked products are not selectable
owner: control-plane-kit-operations
```

### Frozen Lookover

Reviewed:

```text
tests/test_desired_graph_command_service.py
control-plane-kit-core/tests/test_product_instantiation.py
control-plane-kit-operations/src/control_plane_kit_operations/products.py
control-plane-kit-operations/src/control_plane_kit_operations/postgres/product_store.py
control-plane-kit-operations/src/control_plane_kit_operations/postgres/graph_store.py
```

Finding: core product instantiation already writes the pure, secret-free
metadata needed for registration enforcement:

```text
node.metadata["product_identity"]
node.metadata["product_descriptor_digest"]
```

That lets operations enforce product admission without importing
`control-plane-kit-servers`, looking up mutable catalogue URLs, or treating OCI
image references as contract truth.

### Target-Red Evidence

The first focused run failed only because the new service module did not exist:

```text
ModuleNotFoundError:
  No module named 'control_plane_kit_operations.graph_authoring'
```

### Implementation Shape

Added:

```text
SetDesiredGraphCommand
SetDesiredGraphResult
SelectableProduct
GraphAuthoringService
product_references_in_graph(graph)
```

The command path is:

```text
SetDesiredGraphCommand
  -> extract ProductReference values from graph node metadata
  -> lock workspace row
  -> compare expected desired pointer
  -> require active RegisteredProduct rows in same workspace
  -> save GraphVersionRecord
  -> set workspace.desired_graph_id
  -> commit once through UnitOfWork
```

Focused validation:

```text
./control-plane-kit-operations/test.sh
  33 tests passed
  compileall passed
  installed import smoke passed
```

Full validation:

```text
git diff --check
./test.sh
  extracted core validation passed
  operations package validation passed
  packaging smoke passed
  1219 root Docker/Postgres tests passed
```

### Handoff

#841 can build operation sessions and command history on top of this boundary.
It should not reimplement product registration checks in a parallel model.
When #841/#842 introduce action records and planning, the desired-graph command
should call or preserve this path so registered-product admission remains the
single graph-authoring gate.

## EXTRACT.OPERATIONS #841 Operation Sessions And Command History

Status: in progress on `codex/841-operation-sessions-history`.

### Law Cards

```text
law: operation session start is one transaction
expected: session row and initial START_OPERATION_SESSION action row commit
  together through PostgresUnitOfWork
negative: late action insert failure rolls back the session row
owner: control-plane-kit-operations
```

```text
law: operation command idempotency is exact intent replay
expected: same scoped key plus same internal intent returns original durable
  session/action evidence
negative: same key plus changed title, actor, action type, or non-secret
  payload value conflicts
owner: control-plane-kit-operations
```

```text
law: public descriptors redact but fingerprints distinguish intent
expected: command descriptors omit operator-supplied values where required,
  while internal fingerprints include validated non-secret intent data
negative: redacted descriptor shape must not collapse distinct command intent
owner: control-plane-kit-operations
```

```text
law: operation history uses core command identities
expected: cpk_operation_actions.action_type is OperatorCommandKind, matching
  the extracted core command contract map
negative: the old frozen OperationActionKind enum is not preserved as a
  competing vocabulary
owner: control-plane-kit-operations
```

```text
law: terminal sessions reject new operator actions
expected: close/cancel transition open sessions once and records terminal
  action evidence
negative: closed or cancelled sessions cannot accept later RecordOperationAction
owner: control-plane-kit-operations
```

### Frozen Lookover

Reviewed:

```text
control_plane_kit/stores/records.py
control_plane_kit/stores/postgres.py
control_plane_kit/workflows/commands.py
control_plane_kit/workflows/command_service.py
tests/test_operation_commands.py
tests/test_operation_command_service.py
tests/test_operation_postgres_primitives.py
tests/test_workflows.py
```

Finding: #841 should port the durable session/action shape and command-service
transaction laws, but align the action vocabulary with extracted core
`OperatorCommandKind`. The old frozen `OperationActionKind` is superseded by
the core command contract map.

### Target-Red And Fix Evidence

The first focused run after implementation exposed an important boundary bug:

```text
OperationIdempotencyConflict not raised
```

Cause:

```text
fingerprint(command.descriptor())
```

The descriptor intentionally redacts operator values, so two different payload
values had the same redacted shape.

Fix:

```text
public descriptor: redacted operator-facing shape
internal fingerprint: validated non-secret intent shape
```

This matches the frozen service precedent without leaking secrets.

### Implementation Shape

Added:

```text
OperationSessionStatus
OperationSessionRecord
OperationActionRecord
PostgresActivityHistoryStore
IdempotencyKey
StartOperationSession
CloseOperationSession
CancelOperationSession
RecordOperationAction
OperationCommandService
OperationCommandResult
```

The command path is:

```text
OperationCommandService.execute(command)
  -> compute internal intent fingerprint
  -> open PostgresUnitOfWork
  -> read/lock workspace or session truth
  -> idempotency replay/conflict check
  -> write session/action rows
  -> commit once through UnitOfWork
```

Focused validation:

```text
./control-plane-kit-operations/test.sh
  42 tests passed
  compileall passed
  installed import smoke passed
```

Full validation after the record-boundary test hardening:

```text
git diff --check
./test.sh
  extracted core validation passed
  operations package validation passed
  packaging smoke passed
  1219 root Docker/Postgres tests passed
```

### Handoff

#842 should compose desired graph authoring with operation-session/action
history instead of introducing a parallel desired graph workflow. The intended
shape is:

```text
StartOperationSession
  -> SetDesiredGraphCommand through GraphAuthoringService
  -> OperationActionRecord(action_type=OperatorCommandKind.SET_DESIRED_GRAPH)
```

All participating writes must share the same UnitOfWork so desired graph save,
workspace pointer update, and action history evidence commit or roll back
together.

## EXTRACT.OPERATIONS #842 Planning Command Service

Status: in progress on `codex/842-planning-command-service`.

### Law Cards

```text
law: desired graph command composes graph truth and action history
expected: one command writes graph version, workspace.desired_graph_id, and
  SET_DESIRED_GRAPH action evidence in one PostgresUnitOfWork
negative: late action failure rolls back graph insert and workspace pointer
owner: control-plane-kit-operations
```

```text
law: planning pins the graph pointers observed by the operator
expected: RequestActivityPlan loads the exact current and desired graph ids from
  workspace truth and rejects stale pointer observations before writing a plan
negative: missing, cross-workspace, stale, or malformed graph truth writes no
  plan and no planning action
owner: control-plane-kit-operations
```

```text
law: operations planning uses the extracted pure graph pipeline
expected: durable graph descriptors decode to typed graphs, validate to
  ValidatedGraph, diff through diff_graphs, and compile to ActivityPlan
negative: operations must not invent a parallel diff, plan, or graph model
owner: control-plane-kit-operations
```

```text
law: planning-stage idempotency is exact replay
expected: same session key plus identical intent returns original graph/plan and
  action evidence
negative: same key plus changed actor, graph descriptor, or graph pointer intent
  conflicts explicitly
owner: control-plane-kit-operations
```

### Frozen Lookover

Reviewed:

```text
control_plane_kit/workflows/graph_edits.py
control_plane_kit/workflows/planning.py
control_plane_kit/stores/records.py
control_plane_kit/stores/postgres.py
tests/test_desired_graph_commands.py
tests/test_activity_planning_command_service.py
tests/test_operation_command_service.py
```

Finding: #842 should preserve the frozen command-service laws but update the
vocabulary and pipeline to the extracted package boundaries:

```text
OperatorCommandKind.SET_DESIRED_GRAPH
OperatorCommandKind.REQUEST_ACTIVITY_PLAN
ValidatedGraph -> GraphDiff -> ActivityPlan
```

### Target-Red And Fix Evidence

First focused implementation exposed a pure-core API mismatch:

```text
TypeError: diff_graphs requires two ValidatedGraph values
```

Fix:

```text
decode graph descriptor
  -> validate_graph(...)
  -> require_valid()
  -> diff_graphs(validated_current, validated_desired)
```

This keeps operations on the extracted core graph pipeline instead of passing
raw graphs as the frozen package did.

The next focused run exposed an over-specific scenario expectation:

```text
expected ReconcileNode
```

The #842 fixture is initial product-node introduction, not the frozen router
switch. The strengthened expectation now asserts the actual compiler output:

```text
ReconcileRuntime -> StartNode -> WaitForHealthy
```

and checks that the health wait depends on the produced start-node activity id.

### Implementation Shape

Added:

```text
ActivityPlanStatus
ActivityPlanRecord
PostgresActivityHistoryStore.add_plan/get_plan/plans_for_session
DesiredGraphCommandService
ActivityPlanningCommandService
SetDesiredGraph
RequestActivityPlan
DesiredGraphEditResult
ActivityPlanningResult
```

The desired graph command path is:

```text
SetDesiredGraph
  -> open PostgresUnitOfWork
  -> verify open operation session
  -> set_desired_graph_in_unit_of_work(...)
  -> OperationActionRecord(SET_DESIRED_GRAPH)
  -> one commit
```

The planning command path is:

```text
RequestActivityPlan
  -> open PostgresUnitOfWork
  -> lock workspace truth
  -> verify open operation session
  -> load pinned current/desired graph records
  -> decode/validate/diff/compile through core
  -> ActivityPlanRecord
  -> OperationActionRecord(REQUEST_ACTIVITY_PLAN)
  -> one commit
```

Focused validation:

```text
./control-plane-kit-operations/test.sh
  50 tests passed
  compileall passed
  installed import smoke passed
```

Full validation:

```text
git diff --check
./test.sh
  extracted core validation passed
  operations package validation passed
  packaging smoke passed
  1219 root Docker/Postgres tests passed
```

### Handoff

#843 should build approval request and decision truth on top of the persisted
ActivityPlanRecord and action history. It must not create a second plan model,
second approval queue, or server route layer. The next service should consume
`ActivityPlanningResult.plan_record.plan_id` and persist approval facts through
the same UnitOfWork law.

## #843 Approval Command Service

### Frozen Lookover

Inspected:

```text
control_plane_kit/workflows/approvals.py
tests/test_approval_command_service.py
control_plane_kit/stores/postgres.py
```

The frozen implementation already had the right operational shape:

```text
RequestPlanApproval
  -> ApprovalRequestRecord
  -> OperationActionRecord(APPROVAL_REQUESTED)

DecidePlanApproval
  -> ApprovalDecisionRecord
  -> OperationActionRecord(APPROVAL_DECIDED)
```

The extracted package keeps that law while translating vocabulary to the core
contract language:

```text
RequestApproval
  -> ApprovalRequestRecord
  -> OperationActionRecord(REQUEST_APPROVAL)

DecideApproval
  -> ApprovalDecisionRecord
  -> OperationActionRecord(DECIDE_APPROVAL)
```

### Law Cards

```text
approval.request.pending-risk-evidence
approval.decision.distinct-durable-fact
approval.authority.fails-closed
approval.destructive.requires-stronger-scope
approval.idempotency.exact-replay
approval.idempotency.changed-intent-conflict
approval.second-decision.rejected
approval.late-action-failure.rolls-back-request
approval.late-action-failure.rolls-back-decision
approval.concurrent-identical-request.one-winner
approval.concurrent-competing-decision.one-winner
approval.concurrent-close-and-request.serialized
approval.sql.closed-scope-and-risk
```

### Implementation Shape

Added:

```text
ApprovalDecisionKind
ApprovalRequestRecord
ApprovalDecisionRecord
PostgresActivityHistoryStore approval methods
ApprovalCommandService
RequestApproval
DecideApproval
ApprovalRequestResult
ApprovalDecisionResult
```

The request path is:

```text
RequestApproval
  -> open PostgresUnitOfWork
  -> verify operation session is open
  -> load persisted ActivityPlanRecord
  -> derive ApprovalRequirement from core ApprovalPolicy
  -> persist ApprovalRequestRecord
  -> persist OperationActionRecord(REQUEST_APPROVAL)
  -> one commit
```

The decision path is:

```text
DecideApproval
  -> open PostgresUnitOfWork
  -> verify operation session and request ownership
  -> verify no prior decision
  -> check typed PolicyScope authority
  -> persist ApprovalDecisionRecord
  -> persist OperationActionRecord(DECIDE_APPROVAL)
  -> one commit
```

Approval remains durable evidence rather than a mutable flag on a plan.

### Data-Engineering Finding

The first implementation made `PolicyScope` and `RiskLevel` closed at the
Python record boundary. Review found that raw SQL could still insert unknown
approval scope or risk strings into fresh schemas. The fix tightened the
Postgres schema:

```text
cpk_approval_requests.required_scope IN PolicyScope
cpk_approval_requests.max_risk IN RiskLevel
cpk_approval_decisions.scope IN PolicyScope
```

and added schema tests proving those strings fail closed. Stores still do not
commit; schema installation remains caller-transactional.

### Validation

Focused validation:

```text
git diff --check
./control-plane-kit-operations/test.sh
  59 tests passed
  compileall passed
  installed import smoke passed
```

Full validation:

```text
./test.sh
  extracted core validation passed
  operations package validation passed
  packaging smoke passed
  1219 root Docker/Postgres tests passed
```

### Handoff

#844 can treat approval request and decision truth as durable operations data.
Admission should consume the persisted plan plus the matching approval request
and approved decision. It must reject stale plan/graph truth, rejected decisions,
missing authority, and cross-session or cross-workspace approval evidence. It
should not create another approval store, queue, mutable plan flag, or route
layer.

## #844 Execution Admission

### Frozen Lookover

Inspected:

```text
tests/test_execution_admission.py
tests/test_run_lifecycle.py
tests/test_execution_concurrency.py
control_plane_kit/execution/values.py
control_plane_kit/workflows/run_lifecycle.py
control_plane_kit/stores/postgres.py
```

The dry run found that the original #844 issue contained two separable
surfaces:

```text
execution admission
  approved ActivityPlan -> durable execution request

run lifecycle
  execution request -> claim/open run -> lifecycle events/transitions
```

Admission is now #844. Run lifecycle moved to #862 and must complete before
#845 coordinator work.

### Law Cards

```text
execution-admission.approved-current-plan.admitted-atomically
execution-admission.no-effect-dependency
execution-admission.exact-idempotency-replay
execution-admission.changed-intent-conflict
execution-admission.concurrent-identical-request.one-winner
execution-admission.late-action-failure.rolls-back-request
execution-admission.requires-plan-execute-scope
execution-admission.rejected-approval-denied
execution-admission.empty-plan-not-executable
execution-admission.forged-approval-risk-denied
execution-admission.foreign-workspace-denied
execution-admission.stale-graph-pointers-denied
execution-admission.review-blocker-denied
execution-admission.database-cutover.requires-reference-evidence
execution-admission.readiness-evidence.reference-only
execution-admission.sql.lifecycle-action-kind-closed
```

### Implementation Shape

Added:

```text
PolicyScope.PLAN_EXECUTE
ExecutionIdempotency
ExecutionRequestIdentity
ExecutionRequestRecord
ClaimIdentity
PostgresExecutionStore
RequestPlanExecution
ExternalReadinessAttestation
ExecutionAdmissionCommandService
ExecutionAdmissionResult
```

The command path is:

```text
RequestPlanExecution
  -> open PostgresUnitOfWork
  -> lock execution admission idempotency
  -> load workspace/session/plan/approval truth
  -> require PolicyScope.PLAN_EXECUTE
  -> require matching approved approval decision
  -> require current workspace graph pointers still match the plan
  -> require reference-only external readiness for database endpoint cutovers
  -> persist ExecutionRequestRecord(QUEUED)
  -> persist OperationActionRecord(ADMIT_EXECUTION)
  -> one commit
```

The old frozen `OperationActionKind.EXECUTION_REQUESTED` did not move into
extracted core. Core already owns the lifecycle operation language, so operation
actions now accept the closed union:

```python
OperatorCommandKind | LifecycleOperationKind
```

and Postgres enforces the same closed union for `cpk_operation_actions`.

### Data-Engineering Notes

Execution admission uses a scoped advisory transaction lock before the execution
request row exists:

```text
execution-admission:{workspace_id}:{idempotency_key}
```

That gives identical concurrent submissions deterministic replay instead of a
race into the unique index. The durable request insert and operation-action
insert live in one `PostgresUnitOfWork`; a late action failure rolls back the
request.

External readiness evidence is intentionally reference-only:

```text
ExternalReadinessAttestation(activity_id, evidence_ref)
```

URLs, bearer values, and unbounded strings fail at the command boundary. The
payload records only bounded references such as
`migration-check/2026-07-22/a`.

### Focused Validation

```text
./control-plane-kit-operations/test.sh
  70 tests passed
  compileall passed
  installed import smoke passed
```

### Handoff

#862 must implement the lifecycle half split out of #844:

```text
ExecutionRequestRecord(QUEUED)
  -> one-winner claim
  -> ActivityRunRecord
  -> ActivityEventRecord
  -> worker-owned lifecycle transitions
```

#862 should reuse the frozen relational shape for claim/open, events, and
write-once settlement unless the extracted package boundary requires a
documented difference. #845 must wait for #862.

## #862 Run Lifecycle Claims And Events

### Split Objective

#862 completed the lifecycle half split out of #844. Admission now creates a
durable queued execution request; lifecycle claims that request, opens an
activity run, and records append-only run-level events.

The implemented extracted path is:

```text
ExecutionRequestRecord(QUEUED)
  -> ClaimAndOpenActivityRun
    -> ExecutionRequestRecord(CLAIMED, ClaimIdentity)
    -> ActivityRunRecord(CLAIMED)
    -> ActivityEventRecord(RUN_OPENED)
    -> OperationActionRecord(CLAIM_RUN)
```

and then:

```text
StartActivityRun    -> RUN_STARTED  -> ActivityRunStatus.RUNNING
PauseActivityRun    -> RUN_PAUSED   -> ActivityRunStatus.PAUSED
ResumeActivityRun   -> RUN_RESUMED  -> ActivityRunStatus.RUNNING
CompleteActivityRun -> RUN_SUCCEEDED -> ActivityRunStatus.SUCCEEDED
FailActivityRun     -> RUN_FAILED   -> ActivityRunStatus.FAILED
CancelActivityRun   -> RUN_CANCELLED -> ActivityRunStatus.CANCELLED
```

No coordinator, runtime adapter, recovery cursor, effect language, or effect
dispatch moved into operations lifecycle.

### Law Cards

```text
run-lifecycle.records.closed-status-timing
run-lifecycle.records.closed-event-scope
run-lifecycle.evidence.bounded-json-only
run-lifecycle.evidence.secret-shaped-keys-denied
run-lifecycle.claim.one-winner
run-lifecycle.claim.identical-replay
run-lifecycle.claim.changed-intent-conflict
run-lifecycle.claim.requires-execution-operate-scope
run-lifecycle.claim.competing-worker-conflict
run-lifecycle.transition.worker-owned
run-lifecycle.transition.compare-and-set-status
run-lifecycle.transition.append-only-ordinal-events
run-lifecycle.transition.late-action-failure-rolls-back
run-lifecycle.transition.terminal-settlement-write-once
```

### Implementation Shape

Added durable value objects:

```python
@dataclass(frozen=True)
class ActivityRunRecord:
    run_id: str
    plan_id: str
    admission: AdmittedRun
    retry: RetryIdentity
    status: ActivityRunStatus
    created_at: str
    started_at: str | None = None
    settled_at: str | None = None
    metadata: BoundedEvidence = field(default_factory=BoundedEvidence)
```

```python
@dataclass(frozen=True)
class ActivityEventRecord:
    event_id: str
    run_id: str
    ordinal: int
    kind: ActivityEventKind
    occurred_at: str
    activity_id: str | None = None
    evidence: BoundedEvidence = field(default_factory=BoundedEvidence)
    failure: FailureEvidence | None = None
```

`BoundedEvidence` is the durable JSON boundary. Internal immutable tuples remain
possible in domain code, but the persisted operator evidence language accepts
only deterministic JSON-shaped values. Secret-shaped keys such as `token`,
`password`, `secret`, `credential`, and `private_key` fail closed.

Added service and authority:

```python
program = RunLifecycleCommandService(
    unit_of_work_factory,
    clock=clock,
    id_factory=id_factory,
)

authority = ExecutionWorkerAuthority(
    worker_id="worker-a",
    scopes=(PolicyScope.EXECUTION_OPERATE,),
)
```

The lifecycle service owns the transaction boundary. Stores do not commit.

### Data-Engineering Notes

`PostgresExecutionStore.claim_request()` locks the request row with
`FOR UPDATE`, then transitions only `queued -> claimed`. A request already
claimed by another worker returns no row to the service, producing an explicit
conflict rather than overwriting ownership.

Run transitions use SQL compare-and-set:

```sql
UPDATE cpk_activity_runs
SET status = %s,
    started_at = COALESCE(%s, started_at),
    settled_at = COALESCE(settled_at, %s)
WHERE run_id = %s
  AND status = %s
  AND settled_at IS NULL
```

That preserves the important write-once settlement law at the database
boundary. If action persistence fails after a run update and event insert, the
owning `PostgresUnitOfWork` rolls the full command back.

### Focused Validation

```text
./control-plane-kit-operations/test.sh
  78 tests passed
  compileall passed
  installed import smoke passed
```

### Handoff

#845 can now assume:

```text
admitted request
  -> claimed request
  -> opened run
  -> started/paused/resumed/completed/failed/cancelled run events
```

#845 must not bypass `RunLifecycleCommandService` when it needs run ownership
or lifecycle truth. Runtime/effect execution remains outside #862; the
coordinator should consume the claimed/started lifecycle boundary and later
append step events through the same store/journal shape.
