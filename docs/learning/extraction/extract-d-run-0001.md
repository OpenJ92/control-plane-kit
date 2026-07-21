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

## #633 MCP Streamable HTTP Protocol Contract

### Law Card

- Reference identity: `EXTRACT.D.2.mcp-streamable-http-contract`
- Evidence source: frozen `control_plane_kit/mcp_read.py`,
  `tests/test_mcp_read.py`, the EXTRACT.D rollout, #633, and the official MCP
  Streamable HTTP transport documentation.
- Observable law: MCP is a typed protocol and endpoint contract over shared
  control-plane services. It is not an untyped HTTP metadata flag and not a
  hosted server process inside core.
- Expected result: core distinguishes an operator HTTP API from MCP Streamable
  HTTP even though both can use HTTP schemes; the MCP contract describes the
  endpoint path, POST/GET method contract, accepted media, required headers,
  authentication, and origin-validation requirements as deterministic data.
- Negative cases: unknown descriptor keys, missing GET/POST support,
  non-absolute endpoint paths, query/fragment endpoint paths, HTTP protocol
  mistaken for MCP protocol, hosted process terms, stdio state, and session
  cursor leakage into the core descriptor.
- Obsolete assumptions not migrated: frozen `ReadOnlyMcpAdapter` service
  dispatch as the hosted server, MCP runtime imports, FastAPI process wiring,
  and per-transport private projections.
- Future owner: core for the protocol/endpoint contract;
  `control-plane-kit-servers/cpk-server` for the hosted MCP process.

### Objects

```text
ApplicationProtocol
  += mcp-streamable-http

Protocol.MCP_STREAMABLE_HTTP
  = tcp x mcp-streamable-http

McpStreamableHttpContract
  = endpoint_path
  x Protocol.MCP_STREAMABLE_HTTP
  x (POST, GET)
  x accepted content types
  x required request headers
  x named request methods
  x authentication_required
  x origin_validation_required
  x local_bind_policy
  x message_encoding
  x remote_registration
```

### Transformations

```text
McpStreamableHttpContract
  -> closed descriptor
    -> McpStreamableHttpContract
```

and:

```text
Protocol.HTTP != Protocol.MCP_STREAMABLE_HTTP
```

That second law matters. The future descriptor can advertise both
`operator-api` and `operator-mcp` without collapsing them because they share
HTTP URL schemes.

### Implementation Decision

#633 extends the core protocol product rather than attaching MCP as arbitrary
metadata:

```python
Protocol.MCP_STREAMABLE_HTTP = Protocol(
    Transport.TCP,
    ApplicationProtocol.MCP_STREAMABLE_HTTP,
)
```

The endpoint contract is intentionally closed:

```python
McpStreamableHttpContract(
    endpoint_path="/mcp",
    methods=(McpHttpMethod.POST, McpHttpMethod.GET),
    authentication_required=True,
    origin_validation_required=True,
)
```

The descriptor carries current transport obligations but no hosted process:

```python
{
    "kind": "mcp-streamable-http",
    "endpoint_path": "/mcp",
    "methods": ["POST", "GET"],
    "accept_content_types": ["application/json", "text/event-stream"],
    "required_post_headers": [
        "Accept",
        "MCP-Protocol-Version",
        "Mcp-Method",
    ],
    "required_get_headers": ["Accept"],
}
```

Core deliberately avoids durable session state in this contract. The 2025-06-18
MCP transport spec describes optional session headers, while the current draft
changelog removes protocol-level sessions. Because CPK is not yet implementing
the hosted server, the safer core contract records endpoint, security, media,
and header obligations without claiming a session lifecycle.

### Test Evidence

#633 adds `control-plane-kit-core/tests/test_mcp_streamable_http_contract.py`.

The focused red run failed with:

```text
ImportError: cannot import name 'InvalidMcpStreamableHttpContract'
```

That proved the successor test was collected and failed because the contract
language did not exist yet.

The green run passed:

```text
Ran 102 tests in 0.981s
OK
control-plane-kit-core import ok
```

### Review Notes

- Architecture: MCP is now a typed core protocol/contract, but no MCP runtime,
  server host, or process packaging was added.
- Security: the contract requires authentication and Origin validation.
- Data engineering: no store or UnitOfWork implementation changed; MCP remains
  a future adapter over existing services.
- Protocol accuracy: POST and GET, JSON-RPC UTF-8, JSON/SSE Accept behavior,
  and protocol/name headers are represented as data.
- Test integrity: tests distinguish MCP-over-HTTP from generic HTTP and reject
  unknown descriptor shape rather than broadening parsing.

### Handoff To #634

#634 should define the HTTP API route, request, response, auth-scope, and error
contract language. It should reuse the same service-role and UnitOfWork laws
from #631/#632 and must remain distinct from `McpStreamableHttpContract`.
HTTP API routes and MCP tools may share services, but they should not share a
transport descriptor merely because both use HTTP schemes.

## #634 HTTP API Route Contract Language

### Law Card

- Reference identity: `EXTRACT.D.2.http-api-contract`
- Evidence source: frozen `control_plane_kit/servers/instance_read.py`,
  `tests/test_instance_read_fastapi.py`, #634, #631 service roles, and #632
  UnitOfWork boundaries.
- Observable law: HTTP routes are transport contracts over application services.
  They do not own domain semantics, stores, projection logic, or process
  hosting.
- Expected result: core can name route identity, method, path template, target
  service role, auth scope, safety classification, bounded request schema,
  bounded response schema, and bounded error shape as deterministic data.
- Negative cases: invalid route identifiers, query/fragment paths, duplicate
  route identities, duplicate method/path pairs, read-only routes using non-GET
  methods, destructive routes without destructive authorization scope, success
  statuses in error contracts, and descriptors with unknown keys.
- Obsolete assumptions not migrated: FastAPI route decorators, TestClient
  behavior, token-header implementation, read service construction, and HTTP
  process packaging.
- Future owner: core for route contracts; `cpk-server` for hosted HTTP process
  implementation.

### Objects

```text
HttpMethod
  = GET | POST | PUT | PATCH | DELETE

HttpAuthScope
  = read
  | plan:write
  | approval:decide
  | execution:run
  | admin

HttpOperationSafety
  = read-only
  | command
  | destructive

HttpSchemaRef
  = name x max_bytes

HttpErrorContract
  = sorted 4xx/5xx statuses x HttpSchemaRef

HttpApiRouteContract
  = route_id
  x HttpMethod
  x path_template
  x ControlPlaneServiceRole
  x HttpAuthScope
  x HttpOperationSafety
  x request_schema
  x response_schema
  x errors

HttpApiContract
  = unique route_id
  x unique (method, path_template)
  x deterministic route ordering
```

### Transformations

```text
tuple[HttpApiRouteContract]
  -> HttpApiContract
    -> closed descriptor
      -> HttpApiContract
```

The frozen read adapter contributes the first route catalogue:

```python
HttpApiContract(operator_read_http_routes())
```

Those routes all map to:

```text
service_role = reads
auth_scope   = read
safety       = read-only
method       = GET
```

### Implementation Decision

#634 adds `control_plane_kit_core.operations.http`. The module is intentionally
framework-free. The old FastAPI routes become route contracts:

```python
HttpApiRouteContract(
    route_id="planning.create-plan",
    method=HttpMethod.POST,
    path_template="/workspaces/{workspace_id}/plans",
    service_role=ControlPlaneServiceRole.PLANNING,
    auth_scope=HttpAuthScope.PLAN_WRITE,
    safety=HttpOperationSafety.COMMAND,
    request_schema=HttpSchemaRef("PlanTransitionRequest", max_bytes=65536),
    response_schema=HttpSchemaRef("PlanPreparedResponse", max_bytes=65536),
)
```

That object does not execute the plan. It says which service the future process
adapter must call and what safety/auth/shape contract it must enforce.

The frozen read inventory is now available without importing a web framework:

```python
operator_read_http_routes()
```

The `HttpApiContract` sorts routes deterministically and rejects duplicate route
identity or duplicate method/path pairs. This gives #636/#637/#638 a stable
surface for parity checks against MCP.

### Test Evidence

#634 adds `control-plane-kit-core/tests/test_http_api_contract.py`.

The focused red run failed with:

```text
ImportError: cannot import name 'HttpApiContract'
```

That proved the successor test was collected and failed because the HTTP
contract language did not exist yet.

The green run passed:

```text
Ran 109 tests in 0.795s
OK
control-plane-kit-core import ok
```

### Review Notes

- Architecture: no FastAPI, httpx, process, Docker, or product package is
  imported.
- Security: every route carries auth scope and safety classification; destructive
  routes require execution or admin scope.
- Data engineering: route contracts point to service roles rather than stores or
  transactions directly, preserving the UnitOfWork boundary from #632.
- Test integrity: the frozen read route inventory is preserved as a contract,
  but obsolete FastAPI implementation details were not copied.
- MCP parity: HTTP routes and MCP contracts remain distinct typed values.

### Handoff To #635

#635 should add readiness, liveness, shutdown, observation, verification, and
retained-data contracts. It should reuse `HttpApiContract`,
`McpStreamableHttpContract`, `DeploymentProgramBoundary`, and
`UnitOfWorkBoundary` as inputs where useful, but it must not build the hosted
process or product descriptor.

## #635 Readiness, Liveness, Verification, Observation, And Shutdown Contracts

### Law Card

- Reference identity: `EXTRACT.D.2.process-operational-contract`
- Evidence source: Roadmap 0008 health/readiness/observation laws,
  `SERVER_PRODUCT_ROLLOUT.md`, existing core `VerificationContract`,
  `ResourceLifecycle`, #635, and the #633/#634 endpoint contracts.
- Observable law: process start is not readiness; readiness depends on explicit
  dependencies; liveness reveals no sensitive state; observations append truth
  without rewriting desired graph topology; shutdown preserves retained data.
- Expected result: core can describe liveness, readiness, dependency readiness,
  semantic verification, observation projection, and shutdown/retained-data
  policy as deterministic handoff data for the future `cpk-server` process.
- Negative cases: readiness using the liveness endpoint, public readiness,
  duplicate dependency kinds, sensitive evidence keys, missing HTTP/MCP contracts
  when those dependencies are required, shutdown that deletes retained data, and
  shutdown that fails to record an observation.
- Obsolete assumptions not migrated: Docker process startup, live health loops,
  runtime cleanup, retained-volume deletion, hosted route handlers, and concrete
  observation stores.
- Future owner: core for the contract; `control-plane-kit-servers/cpk-server`
  and operations/interpreters for runtime proof.

### Objects

```text
ProcessEndpointKind
  = liveness
  | readiness

DependencyReadinessKind
  = store
  | runtime-authority
  | worker
  | http-api
  | mcp-streamable-http
  | observation

HttpStatusProbeContract
  = kind
  x path
  x public
  x reveals_sensitive_state
  x expected_statuses
  x maximum_response_bytes

ReadinessDependency
  = kind
  x evidence_key
  x required

ObservationHandoffContract
  = append-only projection
  x never-rewrite-desired-graph
  x maximum_evidence_bytes

ShutdownContract
  = graceful_timeout_seconds
  x preserve-retained-data
  x records_observation

ControlPlaneProcessContract
  = liveness
  x readiness
  x dependencies
  x verification
  x observation
  x shutdown
  x optional HttpApiContract
  x optional McpStreamableHttpContract
```

### Transformations

```text
ControlPlaneProcessContract
  -> closed descriptor
    -> ControlPlaneProcessContract
```

and:

```text
process exists
  != liveness
  != readiness
  != semantic verification
```

### Implementation Decision

#635 adds `control_plane_kit_core.operations.process`. The central shape is:

```python
ControlPlaneProcessContract(
    dependencies=(
        ReadinessDependency(DependencyReadinessKind.STORE),
        ReadinessDependency(DependencyReadinessKind.RUNTIME_AUTHORITY),
        ReadinessDependency(DependencyReadinessKind.WORKER),
        ReadinessDependency(DependencyReadinessKind.HTTP_API),
        ReadinessDependency(DependencyReadinessKind.MCP_STREAMABLE_HTTP),
        ReadinessDependency(DependencyReadinessKind.OBSERVATION),
    ),
    http_api=HttpApiContract(operator_read_http_routes()),
    mcp=McpStreamableHttpContract(),
)
```

The liveness/readiness distinction is executable:

```python
HttpStatusProbeContract.liveness()
HttpStatusProbeContract.readiness()
```

and readiness refuses to claim HTTP or MCP availability without the corresponding
contract object.

Shutdown is a law, not a cleanup implementation:

```python
ShutdownContract(
    graceful_timeout_seconds=30.0,
    retained_data_policy="preserve-retained-data",
    records_observation=True,
)
```

This keeps retained-data preservation visible to `cpk-server` without putting
Docker cleanup behavior in core.

### Test Evidence

#635 adds `control-plane-kit-core/tests/test_process_operational_contract.py`.

The focused red run failed with:

```text
ImportError: cannot import name 'ControlPlaneProcessContract'
```

That proved the successor test was collected and failed because the operational
handoff contract did not exist yet.

The green run passed:

```text
Ran 116 tests in 0.789s
OK
control-plane-kit-core import ok
```

### Review Notes

- Architecture: the module composes existing verification, HTTP, and MCP
  contracts; it does not import process or runtime packages.
- Security: public liveness cannot reveal sensitive state; dependency evidence
  keys reject secret/token/password vocabulary.
- Data engineering: observations are append-only and explicitly forbidden from
  rewriting desired graph truth.
- Retained data: shutdown preserves retained data and records an observation.
- Test integrity: tests strengthen health/readiness distinctions and do not
  accept process startup as readiness.

### Handoff To #636

D.2 is now complete. #636 can define shared HTTP/MCP service vocabulary and
projection parity over:

```text
DeploymentProgramBoundary
UnitOfWorkBoundary
HttpApiContract
McpStreamableHttpContract
ControlPlaneProcessContract
```

#636 should prove that HTTP and MCP delegate to the same application services
and projections without implementing either hosted adapter.
