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

## #636 HTTP And MCP Service/Projection Parity

### Law Card

- Reference identity: `EXTRACT.D.3.adapter-projection-parity`
- Evidence source: frozen `ReadOnlyMcpAdapter`, frozen instance read FastAPI
  routes, #633 MCP endpoint contract, #634 HTTP route contracts, and #636.
- Observable law: HTTP and MCP adapters differ by transport representation only.
  They expose one canonical service/projection vocabulary and do not own private
  projections or private command names.
- Expected result: every canonical read projection binds to exactly one HTTP
  route id and one MCP tool name; the binding checks the HTTP route's service
  role and response schema.
- Negative cases: duplicate canonical operation ids, duplicate HTTP route ids,
  duplicate MCP tool names, projection schema mismatch, service-role mismatch,
  and route safety mismatch for reads.
- Obsolete assumptions not migrated: FastAPI route table as truth, MCP adapter
  dispatch dictionary as truth, and process-hosted comparison.
- Future owner: core parity contract; hosted adapters prove they implement it
  later.

### Objects

```text
AdapterProjectionBinding
  = operation_id
  x ControlPlaneServiceRole
  x projection_schema
  x http_route_id
  x mcp_tool_name

AdapterParityContract
  = HttpApiContract
  x McpStreamableHttpContract
  x unique AdapterProjectionBinding*
```

### Transformations

```text
HttpApiContract
  x McpStreamableHttpContract
    -> operator_read_projection_parity
      -> AdapterParityContract
        -> closed descriptor
          -> AdapterParityContract
```

### Implementation Decision

#636 adds `control_plane_kit_core.operations.parity`.

The central binding looks like this:

```python
AdapterProjectionBinding(
    operation_id="read.workspace",
    service_role=ControlPlaneServiceRole.READS,
    projection_schema="WorkspaceReadResponse",
    http_route_id="read.workspace",
    mcp_tool_name="get_workspace",
)
```

This is exactly the style we want for adapters:

```text
canonical projection
  -> HTTP route representation
  -> MCP tool representation
```

The parity contract checks the HTTP side against the canonical service/schema:

```python
route = self.http_api.route(binding.http_route_id)
if route.service_role is not binding.service_role:
    raise InvalidAdapterParityContract(...)
if route.response_schema.name != binding.projection_schema:
    raise InvalidAdapterParityContract(...)
```

The first implementation attempt had a Python syntax error because generator
expressions were passed alongside another argument without parentheses. The fix
was purely syntactic:

```python
_reject_duplicates(
    "operation_id",
    (binding.operation_id for binding in self.projections),
)
```

No test assertion changed.

### Test Evidence

#636 adds `control-plane-kit-core/tests/test_adapter_parity_contract.py`.

The focused red run failed with:

```text
ImportError: cannot import name 'AdapterParityContract'
```

The first implementation run failed on a syntax error in the new module. After
the syntax fix, the green run passed:

```text
Ran 121 tests in 0.946s
OK
control-plane-kit-core import ok
```

### Review Notes

- Architecture: parity depends only on pure HTTP/MCP/service contracts.
- Security: no new auth implementation; parity preserves service-role and
  projection equivalence so future auth checks have one vocabulary to protect.
- Data engineering: no stores or UnitOfWork implementation changed.
- Test integrity: no assertion was weakened; the syntax failure was fixed in
  source.
- Design: this is the first explicit “transport adapters are interpreters over
  one operation language” object in extracted core.

### Handoff To #637

#637 should extend this idea from read projection parity into transaction,
idempotency, and approval parity. It can depend on `AdapterParityContract`,
`UnitOfWorkBoundary`, and the HTTP route safety/auth-scope language. It should
not implement command execution or hosted adapters.

## #637 Command Policy Parity

### Law Card

- Reference identity: `EXTRACT.D.3.command-policy-parity`
- Evidence source: frozen command services, Roadmap 0008 UnitOfWork laws, #632
  transaction boundary, #634 HTTP command contract, #636 parity contract, and
  #637.
- Observable law: HTTP and MCP command adapters are two transport
  representations of one canonical operator-command policy. They cannot diverge
  on service role, request/response schema, idempotency, approval requirement,
  or transaction boundary.
- Expected result: every canonical operator command binds to one HTTP route id,
  one MCP tool name, one service role, one request schema, one response schema,
  one idempotency policy, and one approval policy.
- Negative cases: duplicate command ids, duplicate route/tool ids, route
  service mismatch, route schema mismatch, read-only route used as a command,
  command service without read-write UnitOfWork participation, command service
  without transaction ownership, destructive command without required
  idempotency, destructive command without current approval, and destructive
  effect policy not `after-commit`.
- Obsolete assumptions not migrated: hosted FastAPI/MCP command dispatch as
  truth, transport-specific command policy, and process code in core.
- Future owner: core parity contract; `control-plane-kit-servers/cpk-server`
  later proves hosted HTTP/MCP adapters implement it.

### Objects

```text
CommandIdempotencyPolicy
  = required
  | best-effort

ApprovalPolicy
  = not-required
  | submits-for-approval
  | decides-approval
  | requires-current-approval

AdapterCommandBinding
  = operation_id
  x ControlPlaneServiceRole
  x request_schema
  x response_schema
  x http_route_id
  x mcp_tool_name
  x CommandIdempotencyPolicy
  x ApprovalPolicy

AdapterCommandParityContract
  = HttpApiContract
  x McpStreamableHttpContract
  x UnitOfWorkBoundary
  x unique AdapterCommandBinding*
```

### Transformations

```text
HttpApiContract
  x McpStreamableHttpContract
  x UnitOfWorkBoundary
    -> operator_command_parity
      -> AdapterCommandParityContract
        -> closed descriptor
          -> AdapterCommandParityContract
```

### Implementation Decision

#637 extends `control_plane_kit_core.operations.parity` instead of creating a
second parity module. Projection parity and command parity are different
objects, but they share the same architectural function:

```text
canonical operation language
  -> HTTP representation
  -> MCP representation
```

The central command binding is:

```python
AdapterCommandBinding(
    operation_id="deployment.execute",
    service_role=ControlPlaneServiceRole.EXECUTION,
    request_schema="ExecuteDeploymentRequest",
    response_schema="ExecutionRunResponse",
    http_route_id="command.deployment.execute",
    mcp_tool_name="execute_deployment",
    idempotency=CommandIdempotencyPolicy.REQUIRED,
    approval=ApprovalPolicy.REQUIRES_CURRENT_APPROVAL,
)
```

The contract checks both the route contract and the UnitOfWork law:

```python
route = self.http_api.route(binding.http_route_id)
if route.service_role is not binding.service_role:
    raise InvalidAdapterParityContract(...)
if route.request_schema.name != binding.request_schema:
    raise InvalidAdapterParityContract(...)

boundary = self.unit_of_work.service(binding.service_role)
if boundary.store_participation is not StoreParticipation.READ_WRITE:
    raise InvalidAdapterParityContract(...)
if not boundary.owns_transaction:
    raise InvalidAdapterParityContract(...)
```

Destructive commands receive the stricter law:

```python
if route.safety is HttpOperationSafety.DESTRUCTIVE:
    if binding.idempotency is not CommandIdempotencyPolicy.REQUIRED:
        raise InvalidAdapterParityContract(...)
    if binding.approval is not ApprovalPolicy.REQUIRES_CURRENT_APPROVAL:
        raise InvalidAdapterParityContract(...)
    if boundary.external_effect_policy is not ExternalEffectPolicy.AFTER_COMMIT:
        raise InvalidAdapterParityContract(...)
```

The first implementation marked `recovery.decide` as destructive. The test run
rejected that because the recovery service boundary does not use runtime
authority or after-commit effects. That was the right signal: deciding recovery
is a durable command, but executing compensation/effects is the destructive
operation. The final route is therefore:

```python
(
    "command.recovery.decide",
    "/workspaces/{workspace_id}/runs/{run_id}/recovery",
    ControlPlaneServiceRole.RECOVERY,
    HttpAuthScope.EXECUTION_RUN,
    HttpOperationSafety.COMMAND,
    "RecoveryDecisionRequest",
    "RecoveryDecisionResponse",
)
```

To support descriptor round-trip of the parity object, #637 also added
`from_descriptor()` methods to the existing service and UnitOfWork boundary
values. This is not a persistence implementation. It is descriptor closure for
the pure contract language.

### Test Evidence

#637 adds `control-plane-kit-core/tests/test_command_parity_contract.py`.

The focused red run failed with:

```text
ImportError: cannot import name 'ApprovalPolicy'
```

After implementation, the first green attempt failed structurally:

```text
InvalidAdapterParityContract:
'recovery.decide' external effects must occur after commit
```

That failure led to the `recovery.decide` safety correction above. The final
green run passed:

```text
Ran 126 tests in 0.813s
OK
control-plane-kit-core import ok
```

### Review Notes

- Architecture: command parity remains pure data over HTTP/MCP/UoW contracts.
- Security: destructive commands require current approval and required
  idempotency regardless of transport.
- Data engineering: command services must participate read-write and own the
  operator-command transaction.
- External effects: destructive effect policy must be after-commit; no hosted
  adapter or effect interpreter was introduced.
- Test integrity: failures caused source corrections; no assertion was weakened.
- Design: `recovery.decide` is intentionally a command, not a destructive
  runtime effect.

### Handoff To #638

#638 can now layer authorization, destructive-operation classification, and
activity-history parity over:

```text
AdapterParityContract
AdapterCommandParityContract
HttpAuthScope
HttpOperationSafety
ApprovalPolicy
CommandIdempotencyPolicy
UnitOfWorkBoundary
```

#638 should keep the same rule: core proves the shared policy vocabulary; the
future `cpk-server` process implements adapters against that vocabulary.

## #638 Authorization, Safety, And Activity-History Parity

### Law Card

- Reference identity: `EXTRACT.D.3.authorization-history-parity`
- Evidence source: frozen authorization behavior, bounded error laws, command
  services, activity-history requirements, #636 projection parity, #637 command
  parity, and #638.
- Observable law: HTTP and MCP expose the same authorization scope, safety
  classification, activity-history requirement, and error disclosure policy for
  every shared operation.
- Expected result: every projection and command operation has exactly one
  security binding; read projections are read scoped/read-only; accepted and
  rejected commands require durable activity evidence; errors are bounded and
  redacted.
- Negative cases: missing operation coverage, read projection with command/admin
  scope, read projection claiming command history, command using read scope,
  command safety mismatch, command without activity history, duplicate
  operation ids, and transport-private error disclosure.
- Obsolete assumptions not migrated: route-local authorization rules,
  MCP-private safety rules, hosted adapter code as the source of error policy,
  and unbounded transport errors.
- Future owner: core parity contract; hosted adapters prove implementation
  later in `control-plane-kit-servers/cpk-server`.

### Objects

```text
ActivityHistoryPolicy
  = not-recorded
  | record-accepted-and-rejected-commands

ErrorDisclosurePolicy
  = bounded-redacted
  | transport-private

AdapterOperationSecurityBinding
  = operation_id
  x ControlPlaneServiceRole
  x http_route_id
  x mcp_name
  x HttpAuthScope
  x HttpOperationSafety
  x ActivityHistoryPolicy
  x ErrorDisclosurePolicy

AdapterOperationSecurityParityContract
  = AdapterParityContract
  x AdapterCommandParityContract
  x unique AdapterOperationSecurityBinding*
```

### Transformations

```text
AdapterParityContract
  x AdapterCommandParityContract
    -> operator_adapter_security_parity
      -> AdapterOperationSecurityParityContract
        -> closed descriptor
          -> AdapterOperationSecurityParityContract
```

### Implementation Decision

#638 layers security/history law over the already-closed projection and command
parity contracts:

```python
AdapterOperationSecurityBinding(
    operation_id="deployment.execute",
    service_role=ControlPlaneServiceRole.EXECUTION,
    http_route_id="command.deployment.execute",
    mcp_name="execute_deployment",
    auth_scope=HttpAuthScope.EXECUTION_RUN,
    safety=HttpOperationSafety.DESTRUCTIVE,
    activity_history=(
        ActivityHistoryPolicy.RECORD_ACCEPTED_AND_REJECTED_COMMANDS
    ),
    error_disclosure=ErrorDisclosurePolicy.BOUNDED_REDACTED,
)
```

The contract checks reads and commands differently:

```python
if operation.auth_scope is not HttpAuthScope.READ:
    raise InvalidAdapterParityContract(...)
if operation.safety is not HttpOperationSafety.READ_ONLY:
    raise InvalidAdapterParityContract(...)
if operation.activity_history is not ActivityHistoryPolicy.NOT_RECORDED:
    raise InvalidAdapterParityContract(...)
```

and:

```python
if operation.auth_scope is HttpAuthScope.READ:
    raise InvalidAdapterParityContract(...)
if operation.safety is not route.safety:
    raise InvalidAdapterParityContract(...)
if operation.activity_history is not (
    ActivityHistoryPolicy.RECORD_ACCEPTED_AND_REJECTED_COMMANDS
):
    raise InvalidAdapterParityContract(...)
```

This preserves the key law:

```text
transport choice
  must not change auth scope
  must not change safety classification
  must not skip activity evidence
  must not change error disclosure
```

The first #638 green attempt failed a test that rejected the substring
`localhost` anywhere in the full descriptor. That assertion was too broad:
`McpStreamableHttpContract.local_bind_policy == "localhost-only"` is an
intentional safety policy, not an internal endpoint leak. The assertion now
rejects secret/private material such as token/secret/password/private_url while
preserving descriptor closure and bounded-redacted error policy.

### Test Evidence

#638 adds `control-plane-kit-core/tests/test_authorization_history_parity_contract.py`.

The focused red run failed with:

```text
ImportError: cannot import name 'ActivityHistoryPolicy'
```

After implementation, one overbroad test assertion failed:

```text
AssertionError: 'localhost' unexpectedly found ...
```

The correction narrowed the assertion to actual secret/private material. The
final green run passed:

```text
Ran 131 tests in 0.920s
OK
control-plane-kit-core import ok
```

### Review Notes

- Architecture: the security/history layer depends only on pure parity, HTTP,
  MCP, and UnitOfWork contracts.
- Security: read operations remain read-scoped; commands cannot use read scope;
  errors must be bounded and redacted.
- Data engineering: accepted and rejected commands require activity-history
  evidence.
- Test integrity: the `localhost-only` correction removed a false positive
  without weakening secret redaction or parity assertions.
- Design: #638 completes D.3 without implementing hosted adapters or process
  code.

### Handoff To #639

D.3 is now complete. #639 can begin the `cpk-server` handoff contract phase over
these core values:

```text
DeploymentProgramBoundary
UnitOfWorkBoundary
HttpApiContract
McpStreamableHttpContract
ControlPlaneProcessContract
AdapterParityContract
AdapterCommandParityContract
AdapterOperationSecurityParityContract
```

#639 should describe how the future `control-plane-kit-servers/cpk-server`
process composes entrypoints against these contracts. It must not move process
implementation into core.

## #639 cpk-server Entrypoint Handoff Contract

### Law Card

- Reference identity: `EXTRACT.D.4.cpk-server-entrypoint-handoff`
- Evidence source: frozen FastAPI/MCP adapter shape, #635 process contract,
  D.3 parity contracts, clarified server-product split, and #639.
- Observable law: the future `control-plane-kit-servers/cpk-server` process
  imports core, composes one deployment program, exposes HTTP and MCP adapters
  over the same contracts, and never stores workflow truth in process globals.
- Expected result: a pure handoff contract names the external implementation
  package, import direction, process contract, program boundary, UnitOfWork
  boundary, projection parity, command parity, and security parity.
- Negative cases: wrong implementation owner, wrong import direction, process
  globals owning truth, process HTTP contract missing command routes, process
  MCP contract diverging from parity, UnitOfWork describing a different program,
  or security parity built over different parity objects.
- Obsolete assumptions not migrated: core-owned FastAPI process, core-owned
  hosted MCP server, core-owned Dockerfile, core-owned OCI image, and core-owned
  canonical cpk-server product descriptor.
- Future owner: core owns the handoff contract; `control-plane-kit-servers`
  later owns the implementation.

### Objects

```text
EntrypointCompositionPolicy
  = one-deployment-program

ProcessStatePolicy
  = process-globals-are-not-truth
  | process-globals-own-truth

CpkServerEntrypointHandoffContract
  = ControlPlaneProcessContract
  x DeploymentProgramBoundary
  x UnitOfWorkBoundary
  x AdapterParityContract
  x AdapterCommandParityContract
  x AdapterOperationSecurityParityContract
  x implementation_package
  x import_direction
  x EntrypointCompositionPolicy
  x ProcessStatePolicy
```

### Transformations

```text
process contract
  x program boundary
  x transaction boundary
  x parity contracts
    -> canonical_cpk_server_entrypoint_handoff
      -> CpkServerEntrypointHandoffContract
        -> closed descriptor
          -> CpkServerEntrypointHandoffContract
```

### Implementation Decision

#639 adds `control_plane_kit_core.operations.handoff` rather than growing the
parity module again. The central contract is:

```python
CpkServerEntrypointHandoffContract(
    process=process,
    program=program,
    unit_of_work=unit_of_work,
    projection_parity=projection_parity,
    command_parity=command_parity,
    security_parity=security_parity,
    implementation_package="control-plane-kit-servers/cpk-server",
    import_direction="cpk-server-imports-core",
)
```

The handoff explicitly rejects process-global truth:

```python
if self.state_policy is not ProcessStatePolicy.PROCESS_GLOBALS_ARE_NOT_TRUTH:
    raise InvalidCpkServerHandoffContract(
        "process globals must not own workflow truth"
    )
```

and verifies that the process contract and parity contracts are the same
surface:

```python
if self.process.http_api != self.projection_parity.http_api:
    raise InvalidCpkServerHandoffContract(...)
if self.process.http_api != self.command_parity.http_api:
    raise InvalidCpkServerHandoffContract(...)
if self.security_parity.command_parity != self.command_parity:
    raise InvalidCpkServerHandoffContract(...)
```

This is the precise middle ground we wanted:

```text
core
  describes what cpk-server must compose

control-plane-kit-servers/cpk-server
  implements FastAPI, hosted MCP, Dockerfile, OCI image, and product descriptor
```

### Test Evidence

#639 adds `control-plane-kit-core/tests/test_cpk_server_entrypoint_handoff.py`
and `control_plane_kit_core.operations.handoff`.

The focused red run failed with:

```text
ImportError: cannot import name 'CpkServerEntrypointHandoffContract'
```

After implementation, the green run passed:

```text
Ran 135 tests in 0.963s
OK
control-plane-kit-core import ok
```

### Review Notes

- Architecture: the handoff module depends on process/service/UoW/parity
  contracts only; it does not import server packages.
- Security: HTTP/MCP parity and security parity must be the exact contracts the
  process advertises.
- Data engineering: the handoff preserves one DeploymentProgram and one
  UnitOfWork boundary.
- Process ownership: implementation package is fixed to
  `control-plane-kit-servers/cpk-server`.
- Test integrity: red evidence was a missing contract; no assertion was
  weakened.

### Handoff To #640

#640 can define environment, secret, configuration, and descriptor obligations
for the future `cpk-server` product wrapper. It should build on:

```text
CpkServerEntrypointHandoffContract
ControlPlaneProcessContract
ContainerServerProduct / product descriptor language
ConfigurationArtifact language
SecretEnvironmentDelivery / secret exclusion laws
```

#640 must still not create the cpk-server Dockerfile, OCI image, or product
descriptor inside core.

## #640 cpk-server Material Handoff Contract

### Law Card

- Reference identity: `EXTRACT.D.4.cpk-server-material-handoff`
- Evidence source: environment binding language, secret delivery language,
  configuration artifact language, product descriptor language, #639 handoff,
  and #640.
- Observable law: the future `cpk-server` product has explicit runtime
  material requirements, but no database URI, runtime token, private endpoint,
  secret value, Dockerfile, OCI image, or concrete product descriptor is baked
  into core.
- Expected result: a pure material handoff names required environment variables,
  required secret-delivered environment variables, required configuration
  targets, product identity, descriptor filename, descriptor fields, admission
  policy, runtime lookup policy, and no self-registration policy.
- Negative cases: missing required secret delivery, missing required
  configuration artifact, private endpoint in public environment, wrong product
  identity, wrong descriptor filename, auto-trusted self-registration, and
  non-runtime lookup policy.
- Obsolete assumptions not migrated: core-owned
  `control-plane-instance.product.cpk.json`, core-owned image build inputs, and
  descriptor self-admission.
- Future owner: core owns the handoff contract; `control-plane-kit-servers`
  later supplies actual product material.

### Objects

```text
CpkServerMaterialHandoffContract
  = CpkServerEntrypointHandoffContract
  x ProductIdentity
  x PublicStaticEnvironmentBinding*
  x required_environment_name*
  x SecretDelivery*
  x required_secret_environment_name*
  x ConfigurationArtifact*
  x required_configuration_target*
  x descriptor_filename
  x descriptor_admission_policy
  x self_registration_policy
  x runtime_lookup_policy
  x required_product_descriptor_field*
```

### Transformations

```text
entrypoint handoff
  x product identity
  x env/secret/config requirements
    -> canonical_cpk_server_material_handoff
      -> CpkServerMaterialHandoffContract
        -> closed descriptor
          -> CpkServerMaterialHandoffContract
```

### Implementation Decision

#640 extends `control_plane_kit_core.operations.handoff`. It composes existing
languages rather than creating a second environment or secret vocabulary.

The canonical shape is:

```python
CpkServerMaterialHandoffContract(
    entrypoint=entrypoint,
    product_identity=ProductIdentity("control-plane-kit", "cpk-server", 1),
    public_environment=(
        PublicStaticEnvironmentBinding("CPK_MODE", "server"),
    ),
    required_environment_names=("CPK_PUBLIC_BASE_URL",),
    secret_deliveries=(
        SecretEnvironmentDelivery(
            "CPK_DATABASE_URL",
            SecretReference("secret://runtime/cpk/database-url"),
        ),
        SecretEnvironmentDelivery(
            "CPK_RUNTIME_AUTH_TOKEN",
            SecretReference("secret://runtime/cpk/runtime-auth"),
        ),
    ),
    required_secret_environment_names=(
        "CPK_DATABASE_URL",
        "CPK_RUNTIME_AUTH_TOKEN",
    ),
    configuration_artifacts=(
        ConfigurationArtifact(
            artifact_id="cpk-server-config",
            target_path="/etc/cpk/server.json",
            media_type=ConfigurationMediaType.JSON,
            content='{"mode":"server"}',
        ),
    ),
)
```

The material handoff checks that required secret names are actually delivered:

```python
delivered_secret_names = {
    getattr(delivery, "environment_name", None)
    for delivery in self.secret_deliveries
}
if not set(self.required_secret_environment_names) <= delivered_secret_names:
    raise InvalidCpkServerHandoffContract(...)
```

and rejects obvious private endpoint values in public static environment:

```python
if any(marker in normalized for marker in (
    "postgres://",
    "postgresql://",
    "private.",
    "internal.",
    "127.0.0.1",
    "0.0.0.0",
)):
    raise InvalidCpkServerHandoffContract(...)
```

This preserves the distinction:

```text
secret reference
  is durable descriptor data

secret value / private endpoint
  is runtime material
```

### Test Evidence

#640 extends `control-plane-kit-core/tests/test_cpk_server_entrypoint_handoff.py`.

The focused red run failed with:

```text
ImportError: cannot import name 'CpkServerMaterialHandoffContract'
```

After implementation, the green run passed:

```text
Ran 140 tests in 0.963s
OK
control-plane-kit-core import ok
```

### Review Notes

- Architecture: material handoff composes existing environment/secret/config
  and product identity languages.
- Security: secret references are allowed; secret values/private endpoints are
  rejected from public environment.
- Data engineering: missing runtime material is modeled as failed readiness
  obligations for the future product.
- Product model: descriptor admission is ordinary external product data and is
  not auto-trusted.
- Test integrity: no assertion was weakened.

### Handoff To #641

#641 can now define OCI, publication, health, live smoke, cleanup, and retained
data obligations for `control-plane-kit-servers/cpk-server`. It should build on:

```text
CpkServerEntrypointHandoffContract
CpkServerMaterialHandoffContract
OciImageReference / OciPlatform
ProductRuntimeContract
ControlPlaneProcessContract
ShutdownContract
```

#641 must still not create the actual Dockerfile, OCI image, or product
descriptor in core.

## #641 cpk-server Publication Handoff Contract

### Law Card

- Reference identity: `EXTRACT.D.4.cpk-server-publication-handoff`
- Evidence source: OCI image reference language, product descriptor language,
  process health contracts, shutdown/retained-data laws, #640 material handoff,
  and #641.
- Observable law: the future `cpk-server` product must publish an immutable
  digest-pinned OCI image, run non-root, avoid runtime package installation,
  expose only explicit publication, prove live HTTP/MCP behavior through the
  public product contract, clean owned resources, and preserve retained data.
- Expected result: a pure publication handoff names image, non-root policy,
  mutable runtime install policy, filesystem policy, publication policy, live
  smoke obligations, cleanup evidence, and descriptor digest policy.
- Negative cases: root image, mutable runtime installs, `latest` tag, missing
  HTTP/MCP/cleanup smoke obligation, retained-data deletion, and descriptor not
  referencing the immutable published digest.
- Obsolete assumptions not migrated: core-owned Dockerfile, core-owned image
  build, core-owned publication, and fake live proof in core.
- Future owner: core owns the handoff contract; `control-plane-kit-servers`
  owns the actual image, product descriptor, live smoke, and cleanup evidence.

### Objects

```text
PublicationPolicy
  = private-by-default-public-endpoint-explicit

CpkServerPublicationHandoffContract
  = CpkServerMaterialHandoffContract
  x OciImageReference
  x runs_as_non_root
  x mutable_runtime_install_policy
  x filesystem_policy
  x PublicationPolicy
  x live_smoke_obligation*
  x cleanup_evidence_policy
  x descriptor_digest_policy
```

### Transformations

```text
material handoff
  x digest-pinned OCI image reference
    -> canonical_cpk_server_publication_handoff
      -> CpkServerPublicationHandoffContract
        -> closed descriptor
          -> CpkServerPublicationHandoffContract
```

### Implementation Decision

#641 extends `control_plane_kit_core.operations.handoff` with a publication
contract rather than adding Docker or image-building code.

The canonical shape is:

```python
CpkServerPublicationHandoffContract(
    material=material,
    image=OciImageReference(
        registry="ghcr.io",
        repository="openj92/control-plane-kit-servers/cpk-server",
        digest="sha256:" + "a" * 64,
        tag="0.1.0",
        platforms=(OciPlatform("linux", "amd64"),),
    ),
    runs_as_non_root=True,
    mutable_runtime_install_policy="forbidden",
    filesystem_policy="least-privilege-read-only-root",
)
```

The live smoke is a contract over the future product boundary:

```python
live_smoke_obligations = (
    "http-readiness",
    "http-read-route",
    "mcp-tool-call",
    "shutdown-cleanup",
)
```

and the retained-data law is checked against the process shutdown contract:

```python
shutdown = self.material.entrypoint.process.shutdown
if shutdown.retained_data_policy != "preserve-retained-data":
    raise InvalidCpkServerHandoffContract(...)
```

This is again the same split:

```text
core
  states the proof obligations

control-plane-kit-servers/cpk-server
  supplies the image, descriptor, live smoke, and cleanup evidence
```

### Test Evidence

#641 extends `control-plane-kit-core/tests/test_cpk_server_entrypoint_handoff.py`.

The focused red run failed with:

```text
ImportError: cannot import name 'CpkServerPublicationHandoffContract'
```

After implementation, the green run passed:

```text
Ran 144 tests in 0.955s
OK
control-plane-kit-core import ok
```

### Review Notes

- Architecture: publication handoff depends on material handoff and
  `OciImageReference`; it does not build or publish images.
- Security: image must run non-root; publication is private by default with
  explicit public endpoint.
- Operations: live proof must include HTTP readiness, HTTP read route, MCP tool
  call, and shutdown cleanup.
- Retained data: cleanup evidence must preserve retained data.
- Test integrity: no assertion was weakened.

### Handoff To #642

D.4 is now complete. #642 can perform the mandatory EXTRACT.D closeout over:

```text
DeploymentProgramBoundary
UnitOfWorkBoundary
McpStreamableHttpContract
HttpApiContract
ControlPlaneProcessContract
AdapterParityContract
AdapterCommandParityContract
AdapterOperationSecurityParityContract
CpkServerEntrypointHandoffContract
CpkServerMaterialHandoffContract
CpkServerPublicationHandoffContract
```

#642 should run the full validation required by the milestone, update learning,
open the roadmap PR if required, and stop before any cpk-server process
implementation begins.

## #642 EXTRACT.D Closeout

EXTRACT.D closes with the core/server boundary intact:

```text
control-plane-kit-core
  owns generic service contracts
  owns UnitOfWork and transaction laws
  owns HTTP/MCP contract language
  owns adapter parity contracts
  owns cpk-server handoff contracts

control-plane-kit-servers/cpk-server
  owns FastAPI process composition
  owns hosted MCP Streamable HTTP process composition
  owns Dockerfile and OCI image
  owns product descriptor
  owns live process publication evidence
```

### Capability Now Established

Core can describe the application service composition needed by a future
`DeploymentProgram` wrapper without importing runtime infrastructure. It can
also describe the transport contracts and handoff obligations a future
`cpk-server` product must satisfy.

The closeout object set is:

```text
DeploymentProgramBoundary
UnitOfWorkBoundary
McpStreamableHttpContract
HttpApiContract
ControlPlaneProcessContract
AdapterParityContract
AdapterCommandParityContract
AdapterOperationSecurityParityContract
CpkServerEntrypointHandoffContract
CpkServerMaterialHandoffContract
CpkServerPublicationHandoffContract
```

### Important Laws

- `cpk-server` imports core; core never imports `cpk-server`.
- Core may expose handoff contracts, but not process packaging.
- HTTP and MCP must delegate to the same services.
- MCP must not bypass authorization, approval, idempotency, UnitOfWork,
  activity history, or destructive-operation policy.
- One operator command owns one explicit transaction.
- Stores never commit independently.
- External effects occur after commit.
- Secret values, private endpoints, and unbounded payloads do not enter
  descriptors, events, logs, errors, or product data.

### Curated Snippet

The publication handoff is deliberately a contract over future server-package
work:

```python
CpkServerPublicationHandoffContract(
    material=material_handoff,
    image=pinned_oci_image_reference,
    runs_as_non_root=True,
    mutable_runtime_install_policy="forbidden",
    filesystem_policy="least-privilege-read-only-root",
    publication_policy=PublicationPolicy.PRIVATE_BY_DEFAULT_PUBLIC_ENDPOINT_EXPLICIT,
    live_smoke_obligations=(
        "http-readiness",
        "http-read-route",
        "mcp-tool-call",
        "shutdown-cleanup",
    ),
    cleanup_evidence_policy="owned-resources-cleaned-retained-data-preserved",
)
```

This is not an image build, Dockerfile, FastAPI app, or hosted MCP server.

### EXTRACT.E Rewrite Warning

EXTRACT.E must be refreshed before execution. Its existing parent and child
issues still contain stale wording from the older plan where core produced a
CPI image and self descriptor.

The updated interpretation is:

```text
core wheel
  + parity manifest
  + architecture/security/data/test-integrity evidence
  + exact cpk-server handoff contract

cpk-server image and descriptor remain external
  + implemented later in control-plane-kit-servers/cpk-server
```

Concretely:

- #643 remains valid as required-core law reconciliation.
- #644 should become contract/demo parity for core-owned public interfaces, not
  live Docker/Postgres/FastAPI/MCP process reproduction.
- #645 remains valid as architecture, packaging, import, and public-language
  review.
- #646 should review core security/data/supply-chain/test integrity for
  contracts and wheel packaging, while handing live image/process findings to
  the server milestone.
- #647 must be retitled or rewritten. Core may publish a pinned wheel and
  manifest evidence, but not a CPI image, cpk-server OCI image, or self
  descriptor.
- #648 remains the mandatory operator stop before server-product migration.

### Review Findings

- Architecture: no process module, Dockerfile, cpk-server product descriptor, or
  hosted MCP/FastAPI implementation was added to core.
- Security: private runtime endpoints and secret values remain runtime material
  or opaque secret references, not descriptor data.
- Data engineering: Postgres is represented here as UnitOfWork law and store
  participation contracts. Concrete Postgres interpreters are not part of
  extracted core.
- Test integrity: closeout adds guardrail tests rather than weakening existing
  assertions.

### Handoff

EXTRACT.E should perform a D.0-style confidence pass before implementation. It
should rewrite the milestone as core release-candidate parity plus
server-product handoff readiness, then stop for operator approval before any
server repository process packaging begins.
