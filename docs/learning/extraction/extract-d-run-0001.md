# EXTRACT.D Core Services And cpk-server Boundary - Run 0001

## Scope

EXTRACT.D defines the extracted core control-plane application service surface
and the exact handoff contract for the future
`control-plane-kit-servers/cpk-server` product.

It does not package `cpk-server` inside core.

```text
control-plane-kit-core
  owns generic control-plane application services
  owns HTTP/MCP contract language
  owns service-level parity laws

control-plane-kit-servers/cpk-server
  owns FastAPI/MCP process composition
  owns Dockerfile and OCI image
  owns product descriptor
  owns live process publication evidence
```

## D.0 Result

#630 confirms that the refreshed D.0-D.5 topology is coherent and executable.
The remaining children each have one phase owner and no remaining child
requires core to build the canonical `cpk-server` process, Dockerfile, OCI
image, hosted MCP process, FastAPI app, or product descriptor.

## Phase Topology

```text
D.0 Topology Refresh
  #630

D.1 Core Application Service Composition
  #631 -> #632

D.2 Core HTTP/MCP Contract Language
  #633 -> #634 -> #635

D.3 Core Parity Laws
  #632 + #634 -> #636 -> #637 -> #638

D.4 cpk-server Product Handoff Contract
  #636 + #637 + #638 -> #639 -> #640 -> #641

D.5 Mandatory Stop And Closeout
  #641 -> #642
```

## Child Classification

| Issue | Classification | Why |
| --- | --- | --- |
| #631 | Core application service composition | Frozen workflows show generic planning, approval, admission, lifecycle, execution, recovery, observation, and read-service boundaries. These are reusable control-plane semantics. |
| #632 | Core application service composition | Frozen stores and command services prove that UnitOfWork, store participation, workers, runtime authority, and transaction laws must be explicit before transport contracts use them. |
| #633 | Core HTTP/MCP contract language | MCP Streamable HTTP belongs in the typed protocol/endpoint language, but the hosted MCP server is process packaging. |
| #634 | Core HTTP/MCP contract language | HTTP routes, request/response shapes, auth scopes, and errors are contracts over services, not FastAPI process ownership. |
| #635 | Core HTTP/MCP contract language | Readiness, liveness, verification, observation, shutdown, and retained-data behavior are contracts the future process must satisfy. |
| #636 | Core parity law | HTTP and MCP must share command vocabulary and projections before either transport wrapper is implemented. |
| #637 | Core parity law | Transaction, idempotency, and approval behavior must be service laws, not transport-specific behavior. |
| #638 | Core parity law | Authorization, destructive-command classification, and activity history must be identical across transports. |
| #639 | cpk-server handoff contract | Process entrypoint composition belongs to `control-plane-kit-servers/cpk-server`; core only defines what must be composed. |
| #640 | cpk-server handoff contract | Environment, secret, configuration, and descriptor obligations are the product wrapper contract. |
| #641 | cpk-server handoff contract | OCI image, publication, health, live smoke, cleanup, and retained-data evidence belong to the process wrapper. |
| #642 | Mandatory stop and closeout | Close EXTRACT.D and stop before process packaging starts. |

## Frozen Source Classification

| Frozen source | Future owner | Classification |
| --- | --- | --- |
| `control_plane_kit/workflows/*.py` | core | Generic application command services and service commands. |
| `control_plane_kit/stores/*.py` | core | Durable records, store protocols, Postgres UnitOfWork, and transaction boundaries. |
| `control_plane_kit/read_services/*.py` | core | Canonical redacted read projections. |
| `control_plane_kit/execution/*.py` | core | Typed execution values, records, observations, recovery, and codecs. |
| `control_plane_kit/mcp_read.py` | core contract vocabulary | Transport-neutral MCP-shaped tool vocabulary; not a hosted MCP server. |
| `control_plane_kit/servers/instance_read.py` | cpk-server handoff input | FastAPI read adapter pattern; useful as frozen evidence, but process adapter implementation belongs to `cpk-server`. |
| `control_plane_kit/servers/_fastapi.py` | cpk-server handoff input | Optional FastAPI import boundary; do not put it in core. |
| `control_plane_kit/servers/block_control.py` | cpk-server/product helper handoff input | Control-route adapter evidence; process hosting belongs outside core. |
| `control_plane_kit/servers/catalog.py` and product servers | control-plane-kit-servers | Product catalogue and reusable server products. |
| Docker runtimes/effects/live demos | later interpreters or cpk-server/server packages | Not part of D core service implementation except as handoff obligations. |

## Law Cards

### D.1 Service Composition Law

- Evidence source: frozen `workflows/*`, `stores/*`, `read_services/*`,
  `execution/*`, and Roadmap 0008 learning.
- Observable law: entrypoints compose services; they do not own graph, activity,
  execution, observation, recovery, or read truth.
- Negative cases: a route function opening its own store, a tool function
  bypassing approval, a worker committing independently, a process global
  becoming durable truth.
- Future owner: core.

### D.2 Contract Language Law

- Evidence source: frozen `servers/instance_read.py`, `mcp_read.py`, protocol
  extraction, and EXTRACT.C product descriptor language.
- Observable law: HTTP and MCP are typed contracts over shared services before
  they are hosted processes.
- Negative cases: free-form metadata protocols, FastAPI imports in core, hosted
  MCP runtime imports in core, route-only command semantics.
- Future owner: core for values/contracts; `cpk-server` for process hosting.

### D.3 Parity Law

- Evidence source: frozen `ReadOnlyMcpAdapter`, read service descriptors, route
  adapter shape, command services, and UnitOfWork tests.
- Observable law: equivalent HTTP and MCP requests reach the same services,
  produce the same durable intent/result, and expose equivalent redacted
  projections.
- Negative cases: duplicate tool table, duplicate projection table, transport
  bypass of idempotency, approval, authorization, or activity history.
- Future owner: core.

### D.4 Handoff Law

- Evidence source: rollout server-product split and EXTRACT.C
  `ContainerServerProduct` descriptor language.
- Observable law: the `cpk-server` wrapper is an ordinary external server
  product that imports core and satisfies core contracts.
- Negative cases: core importing `cpk-server`, core owning canonical Dockerfile,
  core owning canonical product descriptor, automatic self-registration,
  recursive proxying as a requirement.
- Future owner: `control-plane-kit-servers/cpk-server`.

## Stale Assumptions Not Migrated

- "Core-owned CPI image" is stale. Core owns services and contracts.
- "Core self descriptor" is stale. The canonical descriptor belongs to the
  future `cpk-server` product package.
- A FastAPI app in the frozen reference is adapter evidence, not a core module.
- A hosted MCP server is process packaging, not core protocol language.
- The server product catalogue must not be imported by core.
- Direct navigation uses the child `cpk-server` public endpoint; recursive
  proxying is not required.

## Security And Data Notes

- Every mutating command must require authorization and the same approval laws
  regardless of transport.
- One operator command owns one explicit transaction.
- Stores never commit independently.
- HTTP/MCP/Docker/filesystem/health/cloud effects never occur inside an open
  transaction.
- Tokens, secret values, private endpoints, and unbounded payloads stay out of
  descriptors, events, logs, errors, and product data.

## Test Evidence

#630 adds a core-local topology guardrail:

```text
control-plane-kit-core/docs/EXTRACT_D_TOPOLOGY.md
  -> control-plane-kit-core/tests/test_extract_d_topology.py
```

The focused red run failed because the topology document did not exist. That
proved the new tests were collected and failing for missing behavior, not for a
broken test harness or frozen implementation import.

## Handoff To #631

#631 may now inventory and design the generic control-plane application service
composition without worrying that it must build the server process. It should
start from frozen `workflows/*`, `stores/*`, `read_services/*`, and
`execution/*`, then design the target service boundary in core.

#631 must not import or implement FastAPI, hosted MCP, Dockerfile, OCI image,
product descriptor, or `cpk-server`.

## #631 Core Application Service Composition

### Law Card

- Reference identity: `EXTRACT.D.1.service-composition-boundary`
- Evidence source: frozen `control_plane_kit/workflows/*`,
  `control_plane_kit/stores/*`, `control_plane_kit/read_services/*`,
  `control_plane_kit/execution/*`, #631, and #630.
- Observable law: a `DeploymentProgram` is composed from generic service roles;
  entrypoints bind transports to those roles but do not own graph, activity,
  execution, recovery, observation, authorization, or read truth.
- Expected result: every required generic role is present exactly once,
  descriptors are deterministic, and the boundary is importable without
  process or product packages.
- Negative cases: missing roles, duplicate roles, process-packaging names such
  as FastAPI or Dockerfile, and service parameters that smuggle process
  packaging into the core boundary.
- Obsolete assumptions not migrated: frozen FastAPI route modules as core
  modules, hosted MCP server as core, Docker/image publication as core.
- Future owner: core.

### Objects

```text
ControlPlaneServiceRole
  = planning
  | approval
  | admission
  | lifecycle
  | execution
  | recovery
  | observation
  | reads
  | authorization

ApplicationServiceBinding
  = role
  x service_name
  x parameters

DeploymentProgramBoundary
  = exactly-one ApplicationServiceBinding per ControlPlaneServiceRole
```

### Transformations

```text
tuple[ApplicationServiceBinding]
  -> DeploymentProgramBoundary
    -> deterministic descriptor
```

### Implementation Decision

#631 introduces `control_plane_kit_core.operations` as a pure boundary package.
It does not implement the workflow services themselves yet. The boundary exists
so later issues can attach UnitOfWork, stores, worker authority, HTTP contracts,
and MCP contracts to a closed set of roles instead of inventing service names
inside routes or tools.

The boundary deliberately rejects obvious process-packaging terms in service
names and parameters:

```python
_FORBIDDEN_PROCESS_TERMS = (
    "cpi",
    "cpk-server",
    "dockerfile",
    "fastapi",
    "mcp-server",
    "oci-image",
    "product-descriptor",
    "uvicorn",
)
```

This is not a general security sanitizer. It is an architecture guardrail: core
service composition must not become a hiding place for the server process.

The first implementation draft included a product-name literal in this guard.
The existing package-boundary test rejected that, correctly: core must not learn
package-owned product identities even as strings inside defensive code. The
final guard only names generic process-packaging terms.

### Test Evidence

#631 adds `control-plane-kit-core/tests/test_deployment_program_boundary.py`.

The focused red run failed with:

```text
ModuleNotFoundError: No module named 'control_plane_kit_core.operations'
```

That proved the successor test was collected and failed because the service
composition boundary was missing.

The green run must continue to prove:

- exact role coverage;
- duplicate/missing role rejection;
- deterministic descriptor order;
- generic naming, with no CPI, product, FastAPI, or Dockerfile leakage;
- no process/product imports from the operations package.

### Handoff To #632

#632 should now define the UnitOfWork, store, worker, runtime-authority, and
transaction boundaries against this closed service-role surface. It should not
add Postgres implementations yet unless the issue evidence requires it; the
first step is to state which roles participate in one operator command and
where commit/rollback ownership lives.

## #632 UnitOfWork And Transaction Boundary Contract

### Law Card

- Reference identity: `EXTRACT.D.1.unit-of-work-boundary`
- Evidence source: frozen `control_plane_kit/stores/unit_of_work.py`,
  frozen UnitOfWork tests, Roadmap 0008 data-engineering laws, #632, and the
  #631 service-role boundary.
- Observable law: every operator command owns exactly one explicit transaction
  boundary; participating services share that boundary; stores never commit
  independently; external effects are forbidden inside the transaction.
- Expected result: the core package can describe service store participation,
  transaction ownership, worker use, runtime-authority use, and effect timing as
  pure values without importing or implementing a database adapter.
- Negative cases: missing service transaction rules, duplicate rules, read-write
  services without transaction ownership, external effects inside transactions,
  and worker/runtime authority on read or authorization services.
- Obsolete assumptions not migrated: concrete connection pools, SQL adapters,
  host process configuration, server request handlers, and database-specific
  import requirements.
- Future owner: core for the contract; `control-plane-kit-operations` or
  `cpk-server` composition for concrete UnitOfWork construction.

### Objects

```text
StoreParticipation
  = none
  | read-only
  | read-write

ExternalEffectPolicy
  = forbidden
  | after-commit
  | inside-transaction

ServiceTransactionBoundary
  = ControlPlaneServiceRole
  x StoreParticipation
  x owns_transaction
  x ExternalEffectPolicy
  x uses_worker
  x uses_runtime_authority

UnitOfWorkBoundary
  = DeploymentProgramBoundary
  x exactly-one ServiceTransactionBoundary per ControlPlaneServiceRole
```

`inside-transaction` exists as a closed value so descriptors and tests can fail
closed at the construction boundary. It is never accepted as a valid runtime
law.

### Transformations

```text
DeploymentProgramBoundary
  x tuple[ServiceTransactionBoundary]
    -> UnitOfWorkBoundary
      -> deterministic descriptor
```

### Implementation Decision

#632 adds `control_plane_kit_core.operations.transactions`. This is a pure
contract module, not a database implementation. The important rule is now
visible in code:

```python
if (
    self.store_participation is StoreParticipation.READ_WRITE
    and not self.owns_transaction
):
    raise InvalidUnitOfWorkBoundary(
        "read-write services must own the operator-command transaction"
    )
```

The external-effect law is also made executable:

```python
if self.external_effect_policy is ExternalEffectPolicy.INSIDE_TRANSACTION:
    raise InvalidUnitOfWorkBoundary(
        "external effects must not run inside a transaction"
    )
```

The execution role can say it needs a worker and runtime authority, but only
with an after-commit effect policy:

```python
ServiceTransactionBoundary(
    ControlPlaneServiceRole.EXECUTION,
    StoreParticipation.READ_WRITE,
    owns_transaction=True,
    external_effect_policy=ExternalEffectPolicy.AFTER_COMMIT,
    uses_worker=True,
    uses_runtime_authority=True,
)
```

That keeps the shape aligned with Roadmap 0008:

```text
short transaction
  -> commit
    -> bounded external effect
      -> short transaction
```

### Test Evidence

#632 adds `control-plane-kit-core/tests/test_unit_of_work_boundary.py`.

The focused red run failed with:

```text
ImportError: cannot import name 'ExternalEffectPolicy'
```

That proved the successor test was collected and failed because the UnitOfWork
boundary language did not exist yet.

The green run passed:

```text
Ran 97 tests in 0.910s
OK
control-plane-kit-core import ok
```

### Review Notes

- Architecture: the module depends only on the existing pure service-role
  boundary.
- Data engineering: the Postgres UnitOfWork law is preserved as a contract but
  no concrete adapter is introduced.
- Transactionality: every read-write service must own an operator-command
  transaction; stores remain descriptor participants, not independent committers.
- Security: runtime authority is not allowed on read or authorization services
  and may only appear with after-commit effects.
- Test integrity: the new tests strengthen the boundary and do not weaken
  existing assertions.

### Handoff To #633

#633 may now define MCP Streamable HTTP endpoint contracts against a service
surface that already names both service roles and transaction/effect laws. It
must not implement a hosted MCP process in core. MCP should be a typed contract
language that a future `control-plane-kit-servers/cpk-server` entrypoint
implements.
