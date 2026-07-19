# Roadmap 0008 Gate G Overnight Run 0002

Date: 2026-07-19

Branch: `roadmap/0008-activity-execution-and-runtime-mutation`

Parent: #402

## Operating Contract

This run resumes Gate G from roadmap commit `0bec6f5`. It begins with #422 and
continues through the remaining HTTP, service, protocol/data, composition, and
acceptance topology under the repository's issue, review, hardening, security,
data-engineering, and Docker-first laws.

Ordinary failures are diagnostic evidence. Each issue section records the
capability, algebra, failures, corrections, downstream consequences, test
evidence, residual risks, and handoff. The run stops only for the explicit
security, secret, transaction, destructive-data, Pottery Factory Docker,
paid-cloud, test-integrity, or genuinely ambiguous architecture conditions in
the execution prompt.

The unrelated untracked `control_plane_kit/.mcp_read.py.swp` file predates this
run and remains untouched.

## Baseline

```text
Roadmap branch head: 0bec6f5
Complete Docker/Postgres suite: 865 tests, OK
Next topological issue: #422
```

## #422: Test-Only HTTP Fault Injection

### Capability

The package now owns an explicitly test-only inline HTTP fault injector. It is
disabled at process start and can be armed only through a dedicated
authenticated control route using a distinct secret reference.

    block = http_fault_injector_block()
    assert block.spec.product is PackageServerProduct.HTTP_FAULT_INJECTOR
    assert block.spec.maturity is ProductMaturity.TEST_ONLY

The product can inject bounded delay, one of four selected 5xx statuses,
connection termination, bounded response truncation, or a reproducible seeded
probabilistic status. It remains a one-target proxy; retry and circuit behavior
come only from graph composition.

### Objects, Morphisms, And Laws

    HttpFault
      = DelayFault
      | StatusFault
      | ConnectionTerminationFault
      | TruncationFault
      | SeededProbabilityFault

    FaultInjectionState
      = DisabledFaultInjection
      | EnabledFaultInjection HttpFault

    PackageServerSpec
      = existing specification
      x PackageServerProduct
      x ProductMaturity

The key interpretation is:

    authenticated closed descriptor
      -> FaultInjectionState
      -> bounded inline HTTP effect
      -> FaultInjectionObservation

Injected-fault identity and target outcome are orthogonal. A delayed request
that receives a natural target 5xx records both DELAY and HTTP_FAILURE. Neither
fact overwrites the other.

Product maturity moved from catalogue-only review data into PackageServerSpec,
its authoritative graph descriptor, and graph validation. The pure production
policy permits only OPERATIONAL package products. Default validation still
permits every closed maturity so test and teaching recipes remain deliberately
representable.

The control language gained one exact route set:

    GET  /__deploy/fault -> control:read
    POST /__deploy/fault -> fault:inject

The generated process requires CPK_FAULT_CONTROL_TOKEN; the ordinary target
address remains runtime material in FAULT_TARGET_URL.

### Breaking Points And Solutions

#### Maturity was not topology truth

ProductMaturity originally existed only beside the package catalogue. A graph
descriptor could retain HTTP_FAULT_INJECTOR but lose TEST_ONLY, making a
production-policy decision depend on a second registry lookup.

The correction made maturity a closed PackageServerSpec field, required it in
the package-server codec, and required catalogue contracts to prove exact
agreement with their block. No metadata inference or compatibility variant was
introduced.

#### The first maturity test constructed an invalid proxy

The first focused run had 65 passing tests and one failure. The test placed the
fault injector alone in a runtime. Canonical validation correctly rejected its
unconnected required target socket.

The test was not weakened. It now constructs a real Hello provider and
SocketConnection, proves default validation accepts the graph, and then proves
the production policy rejects both non-operational package nodes.

#### Production policy initially admitted teaching products

Review found that a method named production() admitted both TEACHING and
OPERATIONAL. The policy was tightened to OPERATIONAL only, and its test now
asserts the exact rejected node identities.

#### A host syntax probe was denied

One exploratory host py_compile attempted to create __pycache__ and was denied
by filesystem policy. It changed no source. Syntax and behavior validation
returned to the Docker image and canonical harness.

### Evidence

    focused fault/catalogue/routes/codec/validation/composition/architecture:
      66 passed

    live generated behavior inside Docker:
      disabled default forwards
      unauthenticated mutation returns 401
      injected status makes zero target calls
      natural target 503 remains distinct from injected status
      response truncation returns exact bounded prefix
      connection termination closes the real client socket
      resetting the same seed reproduces the same selection sequence
      generated process terminates during test cleanup

    complete Docker/Postgres suite before final review:
      873 passed

    complete Docker/Postgres suite after final hardening:
      873 passed

    assertions weakened: 0
    skips added: 0
    production mocks added: 0

Security review:

- fault mutation uses a dedicated strong route scope and distinct opaque secret
  delivery;
- activation defaults to disabled;
- policy contains no URL, body, shell, source, template, or executable field;
- evidence contains no request body, headers, cookies, credentials, target
  address, or secret;
- request, response, control payload, delay, truncation, probability, seed, and
  selected statuses are bounded.

Data and effect review:

- this teaching server adds no store and performs no transaction;
- target HTTP effects occur without a Postgres transaction or lock;
- runtime observations do not rewrite graph truth;
- descriptors retain policy identity and secret references, never values.

### Residual Risk And Handoff

The generated server is HTTP/1 and process-local. It deliberately does not
stream bodies, inject arbitrary payloads, persist activation, or coordinate
fault state across replicas. Reapplying seeded probability resets the sequence;
that behavior is intentional for deterministic tests.

Handoff to #423:

- compose with this product only through sockets and graph position;
- do not reuse fault observations as natural target, retry, circuit, timeout,
  or bulkhead evidence;
- preserve ProductMaturity through any new package product;
- production-oriented scenario validation must reject TEST_ONLY;
- do not weaken the dedicated fault:inject authorization boundary.

## #423 Bounded HTTP Cache

### Capability

The package now has a teaching HTTP cache whose topology identity, cache
policy, sockets, control capabilities, and maturity survive the canonical
graph language. Its runtime entries remain deliberately outside desired graph
truth.

The new product is:

    PackageServerProduct.HTTP_CACHE
      x ProductMaturity.TEACHING
      x HttpCachePolicy
      x RequirementSocket[HTTP](target)
      x ProviderSocket[HTTP](internal)

The key interpretation is:

    bounded HTTP request
      -> secret-safe cache-key digest or explicit bypass
      -> deterministic hit, stale refresh, or target request
      -> bounded CacheObservation

Only GET/200 responses are cacheable. Authorization and Cookie requests
bypass the cache. Set-Cookie, private, no-store, no-cache, wildcard Vary, and
unconfigured Vary responses fail closed. Explicit safe Vary support is a closed
CacheVaryHeader sum type rather than a free-form header escape hatch.

The control language gained one exact authenticated route set:

    GET  /__deploy/cache       -> control:read
    POST /__deploy/cache/purge -> cache:purge

The generated process receives its opaque control token through
CPK_CACHE_CONTROL_TOKEN and its socket-derived target through CACHE_TARGET_URL.
Neither value enters observations.

### Breaking Points And Solutions

No application or test failure occurred during implementation. Focused tests
and the first complete suite were green.

Review concentrated on four boundaries that could otherwise make a teaching
cache unsafe:

- cache keys retain only a SHA-256 digest of bounded method, route, query, and
  explicitly permitted Vary values;
- sensitive request headers cause bypass rather than becoming key material;
- target I/O occurs without holding the cache lock, and this block introduces
  no Postgres transaction;
- purge clears only process-local cache entries and never graph truth, retained
  data, Docker resources, or configuration artifacts.

The stale-while-revalidate name describes cache semantics, but this teaching
implementation refreshes synchronously before returning the retained stale
response. That limitation is explicit and does not advertise asynchronous
production behavior.

### Evidence

    focused cache/catalogue/routes/codec/validation/architecture:
      54 passed

    live generated behavior inside Docker:
      first public request reaches target
      identical second request is served from cache
      private, unknown-Vary, and authorized requests bypass
      unauthenticated state and purge return 401
      authenticated state is bounded and secret-free
      authenticated purge removes all process-local entries
      generated process terminates during cleanup

    complete Docker/Postgres suite:
      879 passed

    assertions weakened: 0
    skips added: 0
    production mocks added: 0

Security review:

- control mutation has a dedicated cache:purge scope and opaque secret
  delivery;
- request bodies, response bodies, URLs, headers, credentials, target address,
  cache keys, and cached values never enter observations;
- request, response, object, capacity, entry count, key material, TTL, and stale
  windows are bounded;
- redirects are disabled for target requests.

Data and effect review:

- cache entries are mutable runtime state, not deployment graph state;
- this teaching server adds no store and performs no transaction;
- target HTTP effects occur without a Postgres transaction or cache lock;
- cached entries are not represented as DataResource or retained data.

### Residual Risk And Handoff

The generated server is HTTP/1, process-local, in-memory, and deliberately not
a shared or production cache. Concurrent equivalent misses can both reach the
target. Stale refresh is synchronous. Cached response headers receive only
minimal hop-by-hop filtering. These are teaching-product limits, not claims
that the block is an operational CDN or reverse-proxy cache.

Handoff to #424:

- authentication and route authorization must be separate closed decisions;
- raw credentials, forwarded identity values, and key material must never enter
  descriptors, observations, logs, or errors;
- trusted identity headers must be stripped from untrusted inbound requests;
- CPK must define gateway contracts without implementing JWT, OIDC, or mTLS
  cryptographic primitives;
- preserve ProductMaturity and dedicated control-route authorization;
- application-domain authorization must remain downstream.

## #424 Authentication And Policy Gateway Contract

### Capability

The package now has a closed authentication-gateway policy language and an
adapter boundary for mature API-key, OIDC/JWT, and mTLS products:

    AuthGatewayPolicy
      x IdentityValidator
      x RequirementSocket[HTTP](target)
      x ProviderSocket[HTTP](internal)

Authentication and route authorization are different values:

    HttpRequest
      -> AuthenticationAccepted AuthenticatedIdentity
       | AuthenticationRejected AuthenticationRejection

    AuthenticatedIdentity x RouteAuthorizationPolicy
      -> AuthorizationDecision

The package does not parse JWTs, discover OIDC keys, validate signatures, or
verify client certificates. Those effects remain behind IdentityValidator.
The included StaticApiKeyValidator and generated API-key gateway are explicitly
TEST_ONLY conformance tools.

Accepted mechanisms, JWT algorithms, route methods, forwarded identity
headers, issuers, audiences, scopes, and resource bounds are typed or bounded.
AuthGatewayPolicy has one exact descriptor decoder; unknown and missing fields
fail closed.

### Breaking Points And Solutions

#### Protocol was imported from the wrong standard module

The first focused run produced five import errors because Protocol was imported
from collections.abc. It remains a typing construct. The import moved to typing
without changing the adapter contract.

#### Two tests guessed the wrong boundaries

The second focused run had one error and one failure. A test called a nonexistent
DockerImageImplementation.descriptor() instead of using the authoritative graph
codec. Another classified the intentionally TEST_ONLY gateway as TEACHING.

The tests were corrected, not weakened. Secret-reference evidence now passes
through compile_recipe and GraphDescriptorCodec, and the catalogue test asserts
the exact TEST_ONLY maturity and capability set.

#### Review found credential forwarding and lexical-prefix authorization

The first complete suite passed 885 tests, but security review found two real
application defects:

- the in-memory gateway consumed X-Api-Key but still forwarded it downstream;
- a lexical startswith check allowed /administer to match an /admin policy.

The forwarding boundary now strips Authorization and X-Api-Key after identity
validation and before injecting trusted identity headers. Route matching now
uses exact path segments. New negative tests preserve both laws in the
in-memory and generated interpreters.

### Evidence

    focused gateway/catalogue/templates/codec/validation/architecture:
      61 passed

    live generated behavior inside Docker:
      missing and invalid credentials return 401
      authenticated but unauthorized route returns 403
      forged trusted identity headers are replaced
      consumed API key is not forwarded
      authorized route receives package-issued identity headers
      unauthenticated metrics return 401
      authenticated metrics contain no credentials, identity values,
        target address, or control token
      generated process terminates during cleanup

    complete Docker/Postgres suite before security hardening:
      885 passed

    complete Docker/Postgres suite after security hardening:
      886 passed

    assertions weakened: 0
    skips added: 0
    production mocks added: 0

Security review:

- trusted identity headers are always removed before validation and regenerated
  only after successful authentication and authorization;
- gateway credentials are removed before forwarding;
- identity-provider unavailability is 503, distinct from 401 and 403;
- API-key equality uses constant-time comparison;
- observations retain only counts and closed decisions;
- OIDC/JWT and mTLS cryptographic work remains outside CPK.

Data and effect review:

- this contract adds no store and performs no Postgres transaction;
- downstream HTTP occurs only after authentication and authorization;
- identity, credentials, and runtime metrics do not rewrite graph truth;
- policy descriptors contain allowlists and references, never secret values.

### Residual Risk And Handoff

The package interpreter is not a production identity gateway. Real OIDC/JWT
discovery, key rotation, mTLS trust, API-key storage, and distributed policy
availability require reviewed external product adapters. Application domain
authorization remains downstream even when gateway route policy allows access.

Handoff to #425:

- idempotency identity must include bounded tenant, actor, route, and payload
  fingerprints without retaining credentials or request bodies;
- one execution winner and conflict detection require durable Postgres truth;
- no transaction may span the downstream HTTP effect;
- gateway identity headers may supply actor context only after an explicit
  trust boundary; never infer trust from a header name alone;
- replay must distinguish exact intent, conflicting reuse, in-flight work,
  expired records, and effect-without-result uncertainty.
## #425 Idempotency And Request-Deduplication Gateway

### Capability

The package now has a TEST_ONLY idempotency gateway block with durable,
Postgres-backed one-winner request execution. The gateway can distinguish an
eligible first request, an exact replay, conflicting key reuse, concurrent
in-flight work, capacity exhaustion, and effect-without-result uncertainty.

The server is independently deployable. It advertises one HTTP target
requirement, one Postgres requirement, one HTTP provider, and a secret-reference
identity-attestation contract. Its process entry point composes FastAPI, the
bounded HTTP adapter, and a gateway-owned Postgres UnitOfWork.

### Objects, Morphisms, And Laws

The pure language is:

```text
IdempotencyIdentity
  = tenant fingerprint
  x actor fingerprint
  x route fingerprint
  x payload fingerprint

IdempotencyRecord
  = identity
  x state
  x lease
  x bounded result reference

IdempotencyDecision
  = execute
  | replay
  | conflict
  | in-flight
  | uncertain
  | capacity exhausted
  | ineligible
```

The command interpreter is:

```text
authenticated request
  -> validate route and authority
  -> derive fingerprint-only identity
  -> short transaction: reserve one winner
  -> commit
  -> bounded redirect-free HTTP effect
  -> short transaction: persist terminal result reference
```

An exact replay returns the retained status and safe reference but never stores
or replays a response body. Conflicting reuse cannot retarget prior work. An
effect that may have occurred without a durable result becomes UNCERTAIN and is
never automatically dispatched again.

The gateway owns its own persistence composition:

```python
with IdempotencyGatewayUnitOfWork(connection_factory) as uow:
    decision = uow.records.reserve(identity, policy, now=clock())
    uow.commit()
```

`PostgresUnitOfWork`, `PostgresStoreBundle`, and the CPI-wide schema remain
unchanged. This is a deployable server with a database requirement, not a new
repository inside the control-plane instance transaction.

At the durable boundary, secret and high-cardinality values become digests:

```python
identity = IdempotencyIdentity.from_request(
    tenant_id=trusted_identity.tenant_id,
    actor_id=trusted_identity.actor_id,
    route=route,
    payload=request_body,
)
```

The record contains fingerprints, never the incoming idempotency key,
attestation value, actor value, tenant value, request body, or response body.

### Breakages And Corrections

#### Persistence was initially composed at the wrong level

The first implementation placed the idempotency schema and store inside the
CPI-wide Postgres bundle. That contradicted the product boundary: each deployed
stateful gateway owns its database requirement and transaction lifecycle. The
implementation moved into `control_plane_kit.idempotency_gateway`, with its own
schema, store, UnitOfWork, service, and process composition. Tests now assert
that the generic CPI store bundle has no idempotency member.

#### One missing closed import caused broad loader failure

The first import-closure run found a missing `HttpResponse` import in the
injected FastAPI executor type alias. It manifested as 103 loader errors but had
one root cause. Importing the existing closed message type restored collection;
no assertion, policy, or behavior was weakened.

#### Review found an ingress-bounding gap

The service rejected oversized bodies, but the FastAPI boundary initially read
the complete request before calling it. The boundary now consumes the ASGI body
stream incrementally and fails with 413 as soon as the typed policy limit is
exceeded. The service retains the same check as defense in depth.

#### Review found incomplete route identity

The target adapter forwards the query string, but the first identity derivation
hashed only the path. The same key, actor, payload, and path with a different
query could therefore replay an operation with different target semantics. The
identity now hashes the complete request target while route eligibility remains
an explicit method-and-path policy. A conflict test proves that changing only
the query cannot dispatch a second effect.

### Evidence

```text
complete Docker/Postgres suite after initial composition:
  895 passed

complete Docker/Postgres suite after concurrency and live-process hardening:
  902 passed

complete Docker/Postgres suite before final intent-identity review:
  903 passed

complete Docker/Postgres suite after full request-target identity correction:
  904 passed

live process proof:
  unauthenticated request returned 401
  first authenticated request returned the target's 201 and Location
  exact replay returned 201 and the retained reference with an empty body
  target side-effect count remained exactly one
  durable row contained no request body, response body, key, or attestation

assertions weakened: 0
skips added: 0
production behavior replaced by mocks: 0
```

Concurrency uses independent Postgres connections and proves one effect winner.
Schema reinstallation preserves both rows and constraint identities. Capacity
tests prove uncertain records cannot be evicted to manufacture room. Failure
after the target effect but before result persistence converges to explicit
uncertainty rather than blind replay.

### Review

Architecture:

- the pure idempotency language does not import stores, HTTP, FastAPI, or
  product implementations;
- the gateway package interprets that language and owns its database boundary;
- generic CPI transaction composition remains untouched;
- catalogue identity and graph reconstruction remain explicit.

Security:

- tenant and actor headers are trusted only after secret-reference attestation;
- consumed attestation and idempotency headers are stripped before forwarding;
- redirects are disabled and response size and time are bounded;
- durable identity uses fingerprints rather than secret or personal values;
- safe forwarded response headers exclude connection-framing headers.

Data and effects:

- stores never commit;
- no transaction spans the downstream HTTP effect;
- terminal and uncertain outcomes are durable and closed;
- expiry and finite capacity are explicit policy, not implicit cleanup;
- replay preserves intent identity and cannot change route, actor, or payload.

### Residual Risk And Handoff

The implementation is TEST_ONLY, not a production distributed idempotency
service. It does not retain response bodies, stream large payloads, coordinate
across multiple databases, or recover an uncertain request automatically.
Production use requires reviewed deployment-specific retention, availability,
attestation rotation, and operator recovery policy.

Handoff to #445:

- the load generator must be a separate TEST_ONLY deployable server with its
  own bounded language and no dependency on idempotency persistence;
- trigger, status, and cancellation routes require authentication;
- dispatch must occur outside transactions;
- count, concurrency, rate, duration, response size, and evidence cardinality
  must be bounded;
- idempotency identity should make an exact trigger replay safe without storing
  request or response bodies;
- evidence must remain aggregate and must not retain credentials, cookies,
  arbitrary headers, or target response bodies;
- real Docker evidence must exercise a rate limiter or load balancer and prove
  owned-resource cleanup.

## #445 Bounded Authenticated HTTP Load Generator

### Capability

Gate G now has a TEST_ONLY `ApplicationBlock` that drives bounded HTTP traffic
through a graph-wired requirement socket. Operators can authenticate to its
control provider, trigger one run, read aggregate evidence, cancel future
dispatch, and exactly replay a command identity. The command cannot supply a
target URL, body, credential, cookie, or arbitrary header.

```text
LoadGenerator ApplicationBlock
  requirement target :: HTTP
  provider control :: HTTP

LoadRun
  = RunId
  x GET | HEAD
  x startup-allowed path
  x bounded count
  x bounded concurrency
  x bounded rate
  x bounded duration
  x bounded timeout
```

The control surface is an explicit closed capability:

```text
LOADS
  GET  /__deploy/load-runs/{run_id}
  POST /__deploy/load-runs
  POST /__deploy/load-runs/{run_id}/cancel
```

`LoadGeneratorPolicy` defines hard startup ceilings. `LoadRunCommand` is pure
intent data. `scheduled_offsets_ms()` is a deterministic interpreter from
count, rate, and duration to admitted dispatch offsets. `LoadRunEvidence`
retains only bounded counters for success, rejection, timeout, failure,
cancellation, and deadline exclusion.

### Composition

The implementation remains separated by responsibility:

```text
load_generation.py
  closed policy, command, status, evidence, codec, schedule

servers/http_load_generator.py
  process-local run interpreter and FastAPI boundary

load_generator_server/main.py
  EnvironmentContract, HTTP adapter, and process composition
```

The process composition reads `LOAD_TARGET_URL` through
`LoadGeneratorEnvironment`. The run command never sees that address. The HTTP
adapter receives no headers and no body, follows no redirects, bounds response
bytes, and translates transport timeout into the closed timeout outcome.

The block is rejected by production graph validation because its
`PackageServerSpec` maturity is TEST_ONLY. Process startup independently fails
unless `CPK_TEST_ONLY=1` is present.

### Breakages And Corrections

#### Live readiness initially had insufficient diagnostics

The first focused run passed 28 of 29 tests but reported only that the packaged
process did not become ready within the original two-second polling window. The
test now gives bounded startup time, fails immediately if the process exits,
and includes captured stderr in that failure. This did not weaken the health
assertion. It made process-start evidence reviewable. The live proof then passed
and closes every captured pipe during cleanup.

#### Process configuration was in the server interpreter

The first complete suite passed 911 tests and failed the architecture policy on
four direct environment reads in `servers/http_load_generator.py`. Moving those
reads into a whitelist would have obscured ownership. Instead, process
composition moved to `load_generator_server/main.py`, where an
`EnvironmentContract` gathers and vends the target, secret token, test marker,
and optional port. The server interpreter no longer owns process configuration.

#### Self-targeting was representable

The dry run confirmed that a `SocketConnection` could connect a node's provider
back to its own requirement. Rather than special-case the load generator, graph
validation gained the general `SELF_CONNECTION` law. Recursive deployment
between distinct CPI nodes remains representable; one server cannot satisfy its
own dependency through its own endpoint.

#### Runtime deadlines needed operational evidence

The pure schedule already excludes offsets outside declared duration, but a
slow runtime could fall behind that schedule. The interpreter now checks the
actual monotonic deadline before each dispatch and records the additional
undispatched work as `deadline_skipped`. Cancellation and deadline evidence stay
distinct.

#### Fake-clock review exposed scheduler ordering

The first deterministic fake-clock test observed dispatch at `0, 1, 1` instead
of `0, 0.5, 1`. The interpreter was waiting for the next rate offset before
waiting for a concurrency slot. The scheduler now obtains capacity first and
then waits for that dispatch's rate offset. This makes saturation delay work
truthfully without advancing the schedule ahead of unavailable capacity.

### Evidence

```text
focused algebra/server/catalogue/control/architecture suite:
  39 passed

complete Docker/Postgres suite after scheduler hardening:
  913 passed

live weighted-balancer proof:
  six generated requests succeeded
  traffic reached both configured targets

live rate-limiter proof:
  five generated requests produced two successes and three 429 rejections

live control proof:
  unauthenticated trigger returned 401
  authenticated trigger returned 202
  status converged from running to a closed terminal state
  aggregate evidence contained no target URL or control token
  generator, limiter, balancer, and target processes were cleaned up

assertions weakened: 0
skips added: 0
production behavior replaced by mocks: 0
```

The atomic tests additionally prove exact command replay, conflicting run-id
reuse, one-active-run capacity, cancellation before future dispatch, bounded
concurrency, deterministic fake-clock scheduling, policy rejection before any
target effect, descriptor closure, graph-codec reconstruction, and production
admission refusal.

### Review

Architecture:

- pure load data imports no server, adapter, topology, or store layer;
- the ApplicationBlock uses the existing block product form and graph compiler;
- target selection is entirely socket-driven;
- environment and transport access remain in their declared composition and
  adapter owners;
- the feature creates no competing execution, observation, or persistence
  model.

Security and operations:

- every mutable/read run route requires constant-time bearer-token validation;
- startup policy, not a request, declares allowed non-control paths;
- commands are limited to GET and HEAD and cannot carry bodies or headers;
- one active run prevents multiplying configured concurrency through overlap;
- count, concurrency, rate, duration, timeout, response bytes, command bytes,
  and retained-run count are all finite;
- cancellation and lifespan shutdown stop future dispatch and join workers;
- response payloads and headers are discarded before aggregate evidence.

Data and effects:

- this test fixture has no store and no transaction;
- process-local records are explicitly ephemeral and bounded;
- network effects are executed only by the HTTP adapter;
- aggregate observations never rewrite desired graph truth.

### Residual Risk And Handoff

The load generator is intentionally not a benchmarking system. It does not
promise precise high-frequency timing, distributed coordination, durable run
history, percentile latency statistics, arbitrary request construction, or
production admission. Cancellation cannot revoke an HTTP request already in
flight; each such request is bounded by its timeout, while future dispatch
stops immediately.

Handoff to #437:

- compose the existing HTTP products and this load generator through ordinary
  recipes and socket connections;
- use aggregate load evidence to prove rate limits, balancing, bulkhead
  rejection, retry/circuit behavior, and cancellation without inventing a new
  scenario runner;
- keep representative live acceptance distinct from contract-only product
  coverage;
- preserve TEST_ONLY admission and owned-process cleanup;
- do not reinterpret generated traffic as application-level correctness.

## #437 HTTP Policy And Resilience Family Acceptance

### Capability

The complete closed HTTP product family now appears in one ordinary deployment
expression:

```text
load generator -> managed entry router
                    |-> active router -> proxy -> auth -> rate limiter
                    |                              -> weighted balancer
                    |                                   |-> logger -> retry
                    |                                   |             -> circuit
                    |                                   |                 -> cache
                    |                                   |                     -> Hello A
                    |                                   `-> timeout -> bulkhead
                    |                                                -> fault injector
                    |                                                    -> multiplexer
                    |                                                         |-> Hello B
                    |                                                         `-> observer
                    `-> Hello green

idempotency gateway -> Hello target
                    -> Postgres
```

The exact authoring expression is
`examples/http_policy_family.py:http_policy_family_recipe`. It is composed
only from `DeploymentRecipe`, `DockerRuntime`, deployable blocks, and
`SocketConnection` values. It creates no combined gateway product and no
acceptance-only graph model.

### Objects And Transformations

```text
DeploymentRecipe
  -> compile_recipe
    -> validated heterogeneous DeploymentGraph
      -> GraphDescriptorCodec
        -> exact canonical descriptor

EmptyGraph x HTTPFamilyGraph
  -> diff_graphs
    -> compile_activity_plan
      -> dependency-ordered ActivityPlan
```

The entry router owns a package verification contract:

```python
VerificationContract(
    (
        HttpCheck(
            check_id="entry-can-reach-application",
            provider_socket="internal",
            path="/probe",
        ),
    )
)
```

Environment-bound socket edges make provider health a predecessor of consumer
startup. The managed router's runtime-controlled active socket remains a
separate explicit edge interpreted after process startup.

### Representative Live Proof

One focused test starts real generated server processes for:

```text
load generator
  -> rate limiter
    -> weighted balancer
      |-> traffic logger -> retry -> circuit breaker -> target A
      `-> timeout -> bulkhead -> disabled fault injector
                              -> multiplexer
                                  |-> target B
                                  `-> request observer
```

Twelve generated requests produce eight successful responses and four explicit
rate-limit rejections. Both balancer branches receive real HTTP traffic.
Observer count equals branch-B terminal deliveries, and logger count equals
branch-A terminal deliveries. Aggregate evidence contains neither control
tokens nor target addresses.

This is representative server-composition evidence. It intentionally does not
replace the later Postgres-backed `DeploymentProgram` proof in #407 or static
Docker realization in #408.

### Breakpoints And Resolutions

1. The first graph validation failed because the managed router's
   runtime-controlled `active` requirement was not connected. The expression
   now declares that edge explicitly; the requirement was not made optional.
2. The first logger read asked for 100 entries while the product's default
   page bound is 50. The acceptance client now obeys the public bound; the
   server bound was not raised.
3. The first startup-order assertion treated runtime-control edges like startup
   environment edges. The law now distinguishes the two typed bindings:
   environment consumers depend on provider health, while runtime control is a
   post-start mutation.
4. Direct Python equality failed after codec reconstruction because canonical
   decoding sorts runtime children while authoring preserves source insertion
   order. The durable law is exact canonical descriptor round trip:
   `encode(decode(encode(graph))) == encode(graph)`.
5. Docker BuildKit lost a parent extraction snapshot while exporting a rebuilt
   test image. Only build cache was pruned. Running containers, images, volumes,
   and Pottery Factory processes were not removed.

### Evidence

```text
focused HTTP family acceptance: 4 passed
complete Docker/Postgres suite:   917 passed
assertions weakened:              0
skips added:                      0
mocked application behavior:      0
```

The suite also retains every atomic HTTP product proof: timeout, retry,
circuit, bulkhead, rate limit, cache, authentication, idempotency, logging,
observer delivery, fault injection, load generation, router mutation,
descriptor closure, capability truth, and control-route authentication.

### Review And Handoff

- Algebra: every product retains exact `PackageServerProduct`, implementation,
  sockets, maturity, verification, and descriptor identity.
- Security: test-only products fail production validation; mutable controls are
  authenticated; the live assertion rejects token and address retention.
- Data: the acceptance addition introduces no store and no transaction.
- Effects: live processes are bounded, joined, and exercised only through
  public HTTP surfaces.
- Test integrity: no skip, mock, or weakened assertion was introduced.

Handoff to #440, #441, and #405:

- reuse `http_policy_family_recipe` as a source expression rather than copying
  its node list into another graph model;
- named recipes may choose coherent subsets but must expand to visible ordinary
  blocks and socket connections;
- #405 should add typed invalid heterogeneous variants;
- #407 must carry this graph through the existing Postgres-backed
  `DeploymentProgram`;
- #408 owns canonical Docker materialization and resource cleanup.

## #501 Closed Service-Discovery Language And Block Contract

### Capability

Service discovery now has a closed pure language before persistence or HTTP
behavior is introduced:

```text
DiscoveryRegistration
  = DiscoveryIdentity
  x Endpoint
  x DiscoveryRegistrationMode
  x DiscoveryLease

DiscoveryCommand
  = RegisterDiscoveryInstance
  | HeartbeatDiscoveryInstance
  | DeregisterDiscoveryInstance
  | ResolveDiscoveryService
  | ExpireDiscoveryLeases
```

The package-owned block is ordinary deployment algebra:

```python
ApplicationBlock(
    PackageServerSpec(product=PackageServerProduct.SERVICE_DISCOVERY),
    PlanOnlyImplementation("service-discovery-contract"),
    BlockSockets(
        requirements=(
            RequirementSocket(
                "database",
                Protocol.POSTGRES,
                ("DISCOVERY_DATABASE_URL",),
            ),
        ),
        providers=(ProviderSocket("internal", Protocol.HTTP),),
    ),
)
```

It is deliberately plan-only and advertises no executable capability yet.
#503 must replace the implementation and add capability evidence only after the
authenticated FastAPI routes exist.

### Laws

- registration identity always names workspace, service, and instance;
- registration mode is either control-plane managed or self-registered;
- leases use timezone-aware typed timestamps and expire strictly after issue;
- registry endpoints reuse the canonical typed `Endpoint` and `Protocol`;
- process-local and unresolved secret-reference addresses cannot become
  resolvable registry truth;
- command descriptors reject unknown variants and fields;
- authority keeps workspace, scopes, and optional self-instance identity
  explicit;
- the block has an explicit Postgres requirement but no persistence owner in
  the control-plane store.

### Breakpoints And Resolutions

1. Adding `SERVICE_DISCOVERY` enlarged the package product sum. The existing
   #437 assertion had equated the HTTP policy family with every package product.
   It now explicitly excludes the service-infrastructure product. This narrows
   the assertion to its stated domain rather than weakening product coverage;
   #438 owns service-family acceptance.
2. The architecture dependency corpus rejected the new `discovery` package
   root because it had no declared dependency rule. It now has the narrow rule
   `discovery -> topology + types`. No store, workflow, adapter, server, or
   process dependency was whitelisted.
3. Capability and discovery route-set values were added before runtime
   realization, but the block does not advertise them. This preserves the law
   that catalogue capability claims require executable evidence.

### Evidence

```text
focused discovery, catalogue, graph, route, and HTTP regression tests: 38 passed
architecture policy corpus:                                         43 passed
complete Docker/Postgres suite:                                    923 passed
assertions weakened:                                                  0
skips added:                                                          0
```

### Handoff To #502

- create `control_plane_kit.discovery_registry` as the server-owned application
  and persistence package;
- give it a narrow `DiscoveryStore`, `DiscoveryUnitOfWork`, normalized Postgres
  schema, and command service;
- do not modify or import the control-plane `PostgresUnitOfWork`;
- use the existing command and authority values rather than introducing store
  DTOs or open status strings;
- stores may execute and return rows but never commit;
- explicit observed timestamps drive lease expiry;
- preserve graph truth independently from registry lease truth.

## #502 Durable Service-Discovery Registry

### Capability

The service-discovery block now has its own transactional Postgres boundary:

```text
DiscoveryRegistryService
  -> DiscoveryUnitOfWork
    -> PostgresDiscoveryStore
      -> current lease projection
      x immutable command ledger
```

The current projection is normalized by
`(workspace_id, service_id, instance_id)`. The command ledger separately owns
command identity, intent fingerprint, actor identity, bounded result snapshot,
and recording time. Exact replay returns the original durable result. Reusing a
command id with changed intent fails without mutating the projection.

This is deliberately not part of the control-plane persistence module. The
registry has a dedicated `PostgresDiscoveryUnitOfWork`; neither the existing
`PostgresUnitOfWork` nor its stores were modified or imported.

### Transaction And Lease Laws

```text
one discovery command
  = one explicit registry-owned Postgres transaction
```

- the application service owns commit;
- the store never commits;
- command and identity advisory locks serialize competing writers;
- projection mutation and immutable command evidence commit together;
- a late command-ledger failure rolls back an earlier projection mutation;
- an active unexpired identity has one registration winner;
- an expired identity may be registered again but cannot be revived by a late
  heartbeat or deregistration;
- expiry changes status and revision without deleting prior lease truth;
- resolution returns only active leases whose expiry is strictly after the
  explicit observation time.

The database independently closes registration status, registration mode,
endpoint scope, command variant, lease ordering, revision, and the valid
transport/application protocol product. Direct SQL cannot bypass the pure
protocol algebra with unknown strings.

### Security And Descriptor Boundaries

Registry endpoint values reuse the canonical typed `Endpoint`, but their
literal addresses are additionally bounded to 2,048 bytes, single-line, and
credential-free. Workspace authority is checked before writes. Control-plane
registrations require management scope; self-registration requires both the
self-registration scope and an exact subject/instance match. Authority
descriptors reject duplicate and unknown scopes.

Result descriptors remain closed JSON-shaped durable values. They reconstruct
typed `DiscoveryResult` and `DiscoveryRegistrationRecord` values rather than
leaking database rows into the application language.

### Breakpoints And Resolutions

1. The initial projection rule treated every active row as permanently
   exclusive, including rows whose leases had already expired. Registration
   now compares the existing expiry to the command's recorded time, allowing a
   new lease while preserving the row's increasing revision.
2. Heartbeat and deregistration initially relied only on exact expiry equality.
   Both now reject commands recorded at or after that expiry, so a stale client
   cannot revive or rewrite an expired lease.
3. Pure protocol construction closed valid combinations, but direct SQL could
   still write arbitrary transport/application strings. A named Postgres check
   constraint now mirrors the closed product and survives schema
   reinstallation.
4. The first complete suite run passed all discovery tests but two existing
   live HTTP child processes missed their five-second readiness windows while
   remaining alive. Both tests passed unchanged in the same image when run
   alone, and the complete suite then passed unchanged on rerun. No timeout,
   assertion, skip, or application behavior was relaxed. This is retained as a
   test-harness pressure signal for later hardening if it recurs.

### Evidence

```text
complete Docker/Postgres suite:                   933 passed
focused unchanged live HTTP rerun:                  2 passed
independent-connection registration winner proof:   present
late-ledger rollback proof:                          present
schema reinstall row/constraint preservation:       present
assertions weakened:                                      0
skips added:                                             0
```

### Handoff To #503

- mount the existing `DiscoveryRegistryService` behind authenticated FastAPI
  routes; do not move transaction ownership into route handlers or stores;
- bootstrap a dedicated registry database connection from the block's declared
  `DISCOVERY_DATABASE_URL` requirement;
- decode the existing closed command and authority descriptors at the HTTP
  boundary;
- advertise service-discovery capabilities only after route behavior is live
  and tested;
- keep tokens, database credentials, endpoint credentials, and command bodies
  out of durable results and errors;
- keep network effects outside the registry transaction.

## #503 Authenticated Discovery HTTP And Docker Boundary

### Capability

The package-owned discovery block is now a real Docker application:

```text
service_discovery_block
  = PackageServerSpec
  x DockerImageImplementation
  x (Postgres requirement + HTTP provider)
```

Its process composition is deliberately separate from the generic server
catalogue:

```text
discovery_server.main
  -> ServiceDiscoveryEnvironment
  -> PostgresDiscoveryUnitOfWork
  -> DiscoveryRegistryService
  -> create_service_discovery_app
```

Authenticated routes expose register, heartbeat, deregister, resolve, and
bounded expiry commands. Every route decodes or constructs the existing closed
command language and delegates to the canonical service. No route owns SQL or
commit behavior.

### Authentication And Scope Boundary

A constant-time opaque identity attestation authenticates the trusted upstream
identity boundary. Bounded headers are then decoded into the existing
`DiscoveryAuthority` product:

```text
attestation
  x actor
  x workspace
  x closed discovery scopes
  x optional self-instance identity
    -> DiscoveryAuthority
```

The registry service remains authoritative for workspace, management,
resolution, and self-registration scope checks. Missing attestation returns
401; coherent but under-scoped authority returns 403; missing registration and
conflicting intent remain distinct 404 and 409 outcomes. Error responses do
not echo tokens, database URLs, endpoint bodies, or internal exception text.

Mutation bodies are streamed through a 16 KiB bound before JSON and closed
descriptor decoding. Resolve limits remain closed at 100; expiry limits remain
closed at 1,000.

### Health And Readiness

```text
/health       = HTTP process is serving
/health/ready = registry database accepts a bounded query
```

Readiness failure returns 503 and cannot be inferred from process startup. The
probe opens and closes its own short database connection; it does not enter a
registry command transaction or claim a semantic registration result.

### Breakpoints And Resolutions

1. The initial FastAPI adapter was placed beside the package block in
   `servers/service_discovery.py`. Architecture policy correctly showed that
   this made the generic server catalogue depend on discovery persistence. The
   adapter moved to `discovery_server/app.py`; `servers` again owns only the
   block contract.
2. Postponed annotations caused FastAPI to interpret the locally imported
   `Request` class as a query parameter, producing 422 before the service. The
   adapter module now evaluates its route annotations where the optional
   FastAPI `Request` class is in lexical scope. Request bounds and service
   assertions were not weakened.
3. The first route implementation omitted the already-typed expiry command,
   although #504 requires live authenticated expiry. A bounded management-only
   expiry route was added to the same discovery route set; no new command,
   service, or persistence model was introduced.
4. The exact route-set corpus rejected the new expiry path until its closed
   expected set was extended. The assertion remains exact.
5. One complete run saw an unrelated bulkhead child process miss its readiness
   window. The test passed unchanged in isolation, and the full suite passed
   unchanged on rerun. No timeout or assertion was relaxed.

### Evidence

```text
complete Docker/Postgres suite:                   940 passed
focused architecture ownership proof:              1 passed
unchanged bulkhead live rerun:                       1 passed
authenticated real-Postgres FastAPI cases:           7 passed
assertions weakened:                                      0
skips added:                                             0
```

### Handoff To #504

- run the packaged Docker server with a real Postgres requirement and injected
  opaque attestation;
- prove register, resolve, heartbeat, expiry, and deregistration through the
  public authenticated routes;
- add adversarial cross-workspace, self-registration, stale-boundary,
  pagination, response-size, and redaction proofs;
- preserve the trusted-attestation versus typed-authority distinction;
- preserve `servers` as block declaration only and `discovery_server` as the
  application composition root;
- prove cleanup removes only graph-owned ephemeral containers and networks;
- do not create another registry service, store, ledger, or UoW.

## #504 Discovery Concurrency, Security, And Live Acceptance

### Capability

The package-owned discovery product now has adversarial and live acceptance
evidence around the complete server boundary:

```text
authenticated command
  -> closed DiscoveryAuthority
  -> DiscoveryRegistryService
  -> PostgresDiscoveryUnitOfWork
  -> current lease projection + immutable command ledger
```

Independent Postgres connections prove one-winner registration and heartbeat,
heartbeat-versus-deregistration serialization, and heartbeat-versus-expiry
serialization at the exact lease boundary. Late heartbeat cannot revive either
an expired or deregistered registration. Resolution remains deterministic by
instance identity and is bounded to 100 records.

The real Docker proof starts a dedicated Postgres container and the packaged
FastAPI discovery server, then sends authenticated HTTP commands through the
same public routes used by a control plane:

```text
unauthenticated register -> 401
register hello-a
resolve hello-a
heartbeat hello-a
deregister hello-a
resolve []
register hello-b
expire hello-b
resolve []
```

Cleanup removes only containers and the network carrying the exact
`io.control-plane-kit.test=discovery-live` ownership label, then verifies that
none remain.

### Closed Self Identity

The hardening pass found that self-registration authority originally bound
only an instance identifier. That allowed an attested instance to reuse its
instance ID under another service name. Self identity is now one product:

```python
DiscoveryAuthority(
    actor_id="orders-a",
    workspace_id="workspace-a",
    scopes=frozenset((DiscoveryScope.REGISTER_SELF,)),
    subject_service_id="orders",
    subject_instance_id="orders-a",
)
```

Service and instance subject values must be present together. The application
service compares both values to the registration identity. The FastAPI
boundary reconstructs them from separate trusted headers after constant-time
attestation; an incorrect service fails before any registry or command-ledger
write.

### One Protocol Scheme Law

Discovery initially introduced a second mapping from `Protocol` to safe URL
schemes. Review found that probes already owned the same closed relation. The
relation now belongs to the protocol algebra itself:

```python
class Protocol:
    def endpoint_schemes(self) -> frozenset[str]:
        return _ENDPOINT_SCHEMES[self]
```

Both discovery construction and probe construction use this method. A
discovery endpoint must contain a host and explicit port, use a scheme admitted
by its exact transport/application product, contain no credentials, query, or
fragment, remain non-local, and fit the existing 2,048-byte bound. This avoids
parallel address-policy models and makes future product integrations inherit
one protocol truth.

### Bounded HTTP Projection

Resolve and expiry pages now share a maximum of 100 records. The FastAPI
adapter also checks the exact compact JSON projection against a 512 KiB
response bound. The maximal-page test uses 100 near-maximum endpoint values
through the real Postgres service and proves the emitted response remains under
that bound. Invalid credential-bearing endpoints return only the generic
closed 400 response; endpoint material and attestation do not appear.

### Breakpoints And Resolutions

1. The dry run exposed incomplete self identity. Adding another route-only
   check would have left durable authority ambiguous, so service identity was
   added to the closed `DiscoveryAuthority` descriptor and enforced by the
   canonical service.
2. Discovery endpoint validation first duplicated the probe scheme table and
   disagreed on DNS and OTLP gRPC spellings. The scheme relation moved to
   `Protocol`, and the existing probe helper became a compatibility interpreter
   over that method.
3. A heartbeat-versus-expiry race can reject the losing heartbeat as either a
   missing active registration or a stale precondition, depending on lock
   acquisition. Both are canonical fail-closed outcomes; the test asserts that
   only one lease mutation wins and that the final row matches that winner.
4. The first complete implementation suite passed 950 tests. After the
   protocol-scheme consolidation and an added exhaustive protocol law, the
   complete suite passed 951 tests. No assertions or timeouts were relaxed.

### Evidence

```text
complete Docker/Postgres suite:                    951 passed
real Docker/Postgres discovery lifecycle:          passed
unauthenticated live mutation:                      401
owned containers/networks after cleanup:            0
maximum resolution page:                 100 records
assertions weakened:                                  0
skips added:                                         0
```

### Handoff To #430 And #438

- treat the discovery registry as authoritative lease projection, never graph
  truth;
- reuse `Protocol.endpoint_schemes()` and exact typed endpoint products;
- derive CoreDNS configuration from bounded registry results rather than
  inventing another registry;
- keep health, DNS projection, and discoverability as distinct evidence;
- compose service acceptance through the packaged discovery server and its
  existing authenticated routes;
- do not introduce another discovery service, store, ledger, or UnitOfWork.

## #427 OpenTelemetry Collector Integration

### Capability

Control Plane Kit now models the official OpenTelemetry Collector as a typed,
operational package server rather than implementing another telemetry process:

```text
OpenTelemetryCollectorConfiguration
  = Receivers
  x Processors
  x Exporters
  x Pipelines
  x HealthExtension

opentelemetry_collector_block
  = PackageServerSpec
  x DockerImageImplementation
  x RoleSockets
```

The default product accepts exact OTLP/gRPC and OTLP/HTTP connections, exposes
an HTTP health socket, redacts selected request attributes, batches all three
telemetry signals, and writes to the Collector debug exporter. Optional remote
OTLP/HTTP exporters are graph connections: their endpoints are requirement
sockets, while credential headers are opaque `SecretReference` deliveries.

Product configuration follows the configuration-artifact law established by
#444:

```text
typed Collector configuration
  -> strict packaged Jinja2 template
  -> bounded secret-free ConfigurationArtifact
  -> graph-pinned read-only Docker mount
```

The official image is pinned to
`otel/opentelemetry-collector-contrib:0.156.0`. The generated artifact is
mounted at `/etc/cpk/opentelemetry-collector.yaml`, and the image receives the
exact `--config` argument for that package-owned path.

### Objects, Morphisms, And Laws

The closed product vocabulary includes `TelemetrySignal`, typed OTLP receiver
protocols, memory limiting, batching, attribute redaction, probabilistic trace
sampling, debug and OTLP/HTTP exporters, exporter headers, health, and signal
pipelines. Collector references preserve official component identity such as
`attributes/redaction` and `otlphttp/archive`; arbitrary component-type strings
cannot enter configuration.

Construction enforces these laws at the pure boundary:

- every pipeline references declared receiver, processor, and exporter
  components;
- probabilistic sampling belongs only to traces;
- provider socket names and container ports are globally unique;
- remote exporter requirement sockets and all injected environment names are
  globally unique;
- headers, component collections, identities, ports, queues, retry windows,
  and rendered content are bounded;
- generated configuration contains environment placeholders, never secret
  values or secret reference identifiers;
- graph codec reconstruction preserves product, socket, image, secret
  delivery, and exact artifact identity;
- changing rendered configuration is visible to graph diff;
- telemetry remains observational evidence and never rewrites desired graph
  truth.

### Breakpoints And Resolutions

1. The first product draft allowed arbitrary Collector `component_id` values.
   The official image rejected `redaction` because Collector component names
   retain their implementation type. Fixed components now own canonical IDs,
   and repeatable OTLP/HTTP exporters derive `otlphttp/<instance>` identities.
   This removed the invalid state rather than special-casing the live fixture.
2. Docker Desktop rejected mounting a volume-subpath file over the image's
   existing `/etc/otelcol-contrib/config.yaml`. The artifact moved to the
   package-owned `/etc/cpk/opentelemetry-collector.yaml` path, selected through
   the Collector's official `--config` argument. The mount remains immutable
   and graph-pinned.
3. An inline shell-quoted OTLP JSON body was fragile. Structured OTLP payload
   construction moved into the Python live fixture; the shell now only
   orchestrates isolated Docker resources.
4. Architecture policy rejected the new server module's direct configuration
   and verification imports because those composition dependencies were not
   declared. The `servers` dependency rule now explicitly admits the existing
   pure configuration output and verification languages. The policy was not
   bypassed or weakened.
5. Final review found that per-receiver uniqueness did not prevent collisions
   with the health extension, and per-exporter checks did not prevent
   cross-exporter environment collisions. Global socket, port, requirement,
   and environment checks plus a header bound were added at configuration
   construction.

### Live Evidence

The dedicated live proof derives startup and teardown from the canonical
recipe, graph, diff, ActivityPlan, effect request, materialization, and Docker
interpreter pipeline. It proves:

```text
graph-pinned Collector startup
  -> idempotent start replay
  -> HTTP health
  -> real OTLP/HTTP JSON trace
  -> official debug exporter logs contain cpk-live-span
  -> graph-derived teardown
  -> zero owned container, network, or configuration volume remains
```

Validation at closeout:

```text
complete Docker/Postgres suite:                    961 passed
focused Collector and descriptor cases:             10 passed
real official-image OTLP live proof:                    passed
owned resources after cleanup:                               0
assertions weakened:                                          0
skips added:                                                 0
```

### Residual Risk And Handoff

The integration proves OTLP/HTTP trace delivery through the official Collector
and exact OTLP/gRPC topology, but it does not yet send a live gRPC payload or
exercise a remote authenticated exporter. Product telemetry is intentionally
not retained by CPK as graph truth.

Handoff requirements:

- #438 may compose this block into service-observability acceptance, using its
  existing exact sockets and package-owned verification contract;
- future remote-exporter acceptance must bind the OTLP/HTTP requirement socket
  and resolve header secrets only at execution;
- do not introduce another collector, telemetry store, protocol model,
  configuration interpreter, or observation truth;
- preserve the official-image boundary and graph-derived ownership cleanup.

## #510 Closed Webhook-Delivery Algebra

### Capability

Control Plane Kit now has a pure, closed language for durable webhook delivery
before either persistence or outbound HTTP exists:

```text
WebhookDeliveryIntent
  = CommandIdentity
  x DeliveryIdentity
  x BoundedEndpoint
  x BoundedPayload
  x RetryPolicy
  x EnqueueTime
  x OptionalSecretReference

tuple[WebhookEvent, ...]
  -> replay_webhook_events
    -> WebhookDeliveryState
```

The language distinguishes enqueue intent, attempt start, attempt result,
scheduled retry, dead-letter admission, and operator-required uncertainty as
immutable events. `WebhookDeliveryState` is a reconstructible projection, not
stored workflow truth or a mutable delivery cursor.

### Objects, Morphisms, And Laws

The closed products include HTTP/HTTPS endpoints, bounded JSON, CloudEvents
JSON, and opaque payloads, HMAC-SHA256 signing references, bounded exponential
backoff, delivery status, attempt outcome, and least-privilege webhook scopes.

The primary morphisms are:

```python
state = evolve_webhook_delivery(state, event)
state = replay_webhook_events(events)
descriptor = webhook_event_descriptor(event)
event = webhook_event_from_descriptor(descriptor)
```

Construction and replay enforce these laws:

- payloads are nonempty, bounded to one MiB, hidden from representations, and
  protected by a verified SHA-256 content digest;
- declared JSON is parsed at construction, and CloudEvents JSON is an object;
- signing values never enter the language; only `SecretReference` identity is
  durable;
- endpoint URLs exclude credentials, query strings, fragments, whitespace,
  backslashes, invalid ports, and non-HTTP schemes;
- attempts and timestamps are ordered and bounded by the retry policy;
- retry availability is derived exactly from prior completion and policy;
- terminal failure, retryable failure, and effect uncertainty remain distinct;
- uncertainty can move only to explicit operator-required state;
- every descriptor has an exact field set and unknown variants fail closed;
- replaying identical event history reconstructs identical state.

### Review Findings And Decisions

1. The initial implementation was one flat module. It moved to the
   `control_plane_kit.webhook` package so persistence, services, adapters, and
   server composition can gain adjacent homes without entering the pure
   language module.
2. The language deliberately depends only on the existing secrets algebra for
   `SecretReference`. Architecture policy now declares and enforces that exact
   dependency.
3. Payload bytes are present in the durable intent because later dispatch and
   crash recovery need exact pinned material. They are absent from `repr` and
   must remain absent from logs, errors, graph descriptors, and operator
   evidence downstream.
4. Pure endpoint validation establishes closed URL shape, not DNS resolution or
   SSRF safety. Final outbound resolution, redirect, timeout, and address policy
   belong to #513 at the effect boundary.
5. A truncated command rendering appeared to show a duplicate descriptor key.
   Numbered source inspection disproved it, so no test or production behavior
   was changed to accommodate a tooling artifact.

### Evidence

```text
focused webhook and architecture tests:             18 passed
complete Docker/Postgres suite:                     973 passed
assertions weakened:                                  0
skips added:                                         0
external effects introduced:                         0
persistence introduced:                              0
```

### Handoff To #511

- persist the canonical intent and immutable event descriptors rather than a
  second mutable state model;
- give webhook delivery its own application-owned Postgres schema and
  UnitOfWork, never the CPI database transaction;
- keep stores free of commit and rollback ownership;
- preserve payload confidentiality while retaining exact recovery material;
- use optimistic journal/version preconditions and one-winner claim semantics;
- do not introduce another event vocabulary, projection, recovery cursor, or
  compatibility schema.

## #517 Webhook Claim And Lease Hardening

The #511 persistence dry run found that #510's issue contract required claim
facts, but the merged pure language moved directly from enqueue to attempt
start. Encoding worker ownership only as Postgres columns would have created a
second operational language below the algebra. #517 therefore blocks #511 and
adds the missing distinction in place:

```text
queued or retry-ready
  -> WebhookClaimed(WebhookClaim)
    -> WebhookAttemptStarted(claim_id)
      -> external effect
        -> WebhookAttemptFinished(claim_id, outcome)

claimed before attempt start
  -> WebhookClaimReleased(abandoned | expired)
    -> prior readiness reconstructed
```

`WebhookClaim` owns delivery identity, claim identity, worker identity, attempt
number, claim time, and bounded lease expiry. `WebhookDeliveryState` projects
the active claim from immutable history. Attempt intent and result both carry
the exact claim identity, so a stale or foreign worker result cannot compose
with the active history.

The executable laws now prove:

- only queued or retry-ready work can be claimed;
- one claim targets exactly the next bounded attempt;
- retry work cannot be claimed before policy availability;
- claim time and lease fit wholly inside the delivery deadline;
- attempt start requires the exact active, unexpired claim;
- release is possible only before attempt start;
- expired release cannot precede the lease boundary;
- release and reclaim preserve immutable prior history;
- unknown claim descriptors and release reasons fail closed;
- identical event replay reconstructs identical claim state;
- effect intent without a result remains in-flight and cannot become a fresh
  retry merely because a lease expires.

Evidence:

```text
focused webhook and architecture tests:             19 passed
complete Docker/Postgres suite:                     974 passed
assertions weakened:                                  0
skips added:                                         0
SQL or external effects introduced:                   0
```

Handoff to #511: persist these exact claim events and reconstructible active
claim. SQL may enforce one-winner appends and projection versions, but it must
not invent another claim status, lease language, or mutable dispatch cursor.

## #511 Webhook-Owned Postgres And UnitOfWork

### Capability

Webhook delivery now has a dedicated application persistence boundary over one
Postgres connection:

```text
PostgresWebhookUnitOfWork
  = IntentStore
  x JournalStore
  x ProjectionStore
  x CommandStore
  x one caller-owned transaction
```

The intent relation retains exact dispatch material, including bounded payload
bytes and only the signing `SecretReference`. The event relation is the
immutable canonical journal. The current projection is an indexed,
reconstructible derivative carrying its exact journal version. The command
ledger gives later services durable exact-replay identity.

### Transaction And Reconstruction Laws

```python
with PostgresWebhookUnitOfWork(connect) as work:
    work.intents.add(intent)
    work.journal.append(identity, expected_ordinal=1, event=enqueued)
    work.projections.add(initial_state, journal_version=1)
    work.commands.add(...)
    work.commit()
```

`commit()` requests commit; it does not expose transaction ownership to a
store. On normal exit without that request, exceptions, or commit failure, the
UnitOfWork rolls back the shared connection. The existing architecture policy
admits this exact application UnitOfWork module and continues rejecting commit
calls from stores and unrelated modules.

Executable laws prove:

- all four repositories participate in one atomic transaction;
- schema installation executes inside the caller's transaction and never
  commits or rolls back itself;
- reinstalling the schema preserves rows and named constraint identity;
- journal append and projection replacement reject stale versions;
- delivery and command advisory locks serialize independent connections;
- concurrent enqueue and claim decisions have exactly one winner;
- the stored projection equals replay of the canonical event journal;
- unknown event/status values and malformed relational state fail closed;
- payload bytes and signing values remain absent from diagnostics and command
  results; only the signing reference is durable;
- no HTTP, secret-resolution, Docker, or other external effect exists in this
  persistence issue.

### Breakpoints And Resolutions

1. Architecture analysis correctly rejected the new UnitOfWork's commit call.
   The exact `control_plane_kit.webhook.unit_of_work` module was added to the
   closed owner set and an executable policy test proves that store commits
   remain rejected. No package-prefix exemption was added.
2. The complete suite exposed an unrelated live-fixture race. Two sequential
   released-port probes could select the same port, and the one-second startup
   budget was too narrow under container load. The fixture now reserves both
   dynamic ports simultaneously and uses an explicit five-second monotonic
   readiness deadline. Its end-to-end forwarding and observer assertions are
   unchanged.
3. The Postgres relations deliberately duplicate exact immutable event
   descriptors alongside normalized intent columns. The event journal remains
   independently replayable canonical history; the projection is explicitly
   derivative and is checked against replay in real-Postgres tests.
4. Webhook persistence does not import CPI stores or CPI
   `PostgresUnitOfWork`. It is owned by the webhook application so a packaged
   webhook service can receive its own Postgres requirement socket.

### Evidence And Handoff

```text
focused real-Postgres webhook cases:                 9 passed
focused live multiplexer regression:                 1 passed
complete Docker/Postgres suite:                    983 passed
assertions weakened:                                 0
skips added:                                         0
external effects introduced:                        0
```

Handoff to #512:

- compose command services over these four stores and this UnitOfWork;
- preserve one command as one short explicit transaction;
- commit durable claim/attempt intent before outbound delivery;
- resolve secrets and perform HTTP only after that transaction closes;
- record result and replay-equivalent projection in a second short transaction;
- never treat claim lease expiry after attempt start as permission for blind
  replay;
- introduce no second journal, projection, recovery cursor, or CPI transaction.

## #512 Durable Webhook Dispatch And Recovery Service

### Capability

The webhook application now composes its pure event language and application
Postgres boundary into one closed command interpreter:

```text
WebhookCommand
  = EnqueueWebhook
  | ClaimWebhook
  | ReleaseWebhookClaim
  | DispatchWebhook
  | RecoverWebhook

WebhookDeliveryService
  : WebhookCommand -> WebhookCommandResult
```

The service accepts a typed `WebhookOutboundDelivery` capability. It does not
know HTTP clients, DNS, SSRF policy, redirects, signing values, FastAPI, or
Docker. Those remain the next interpreter boundary in #513.

### Split-Transaction Interpretation

Dispatch has the required visible composition:

```text
transaction 1
  authorize + lock command + lock delivery
  append WebhookAttemptStarted
  replace replay-equivalent projection
  record exact command identity
  commit

WebhookOutboundDelivery.deliver(WebhookOutboundRequest)

transaction 2
  lock delivery
  append WebhookAttemptFinished
  append retry, dead-letter, or operator-required event when required
  replace replay-equivalent projection
  commit
```

The outbound request carries exact endpoint, bounded payload, signing
reference, claim identity, and attempt number as typed immutable data. A fake
capability test asserts every UnitOfWork connection is already closed when the
effect begins.

### Recovery Laws

The durable state distinguishes two crash windows:

```text
claimed, no attempt-start
  -> expired claim release
  -> safe re-claim

attempt-started, no result
  -> no automatic effect replay
  -> after lease expiry, immutable uncertain result
  -> operator-required
```

Exact replay of a dispatch command consults the command ledger and current
canonical journal projection; it never invokes the outbound capability again.
Recovery uses a fresh service instance over Postgres and requires no mutable
cursor or process memory.

Other executable laws prove:

- workspace and least-privilege scopes are checked before mutation;
- command-id replay converges while changed intent conflicts;
- competing service claims across independent connections have one winner;
- a worker may abandon only its own unexpired pre-dispatch claim;
- an expired claim cannot be relabelled as voluntary abandonment;
- retry time is derived exactly from bounded exponential backoff;
- retry work cannot be claimed before availability;
- retry exhaustion includes the case where the next backoff no longer fits
  inside the delivery deadline;
- terminal failure, dead letter, uncertainty, operator-required, and delivered
  remain distinct journal-derived states;
- caught adapter exceptions become bounded uncertainty facts without retaining
  exception text, payload content, or secret values.

### Breakpoints And Resolutions

1. A deadline-exhaustion fixture initially used a thirty-second claim lease
   inside a four-second delivery deadline. The pure algebra correctly rejected
   that impossible claim. The fixture now uses a two-second lease and reaches
   the intended law: a five-second retry backoff cannot fit before deadline.
2. The original dead-letter transition recognized only terminal failure,
   attempt-count exhaustion, or a clock already at deadline. The service exposed
   the missing mathematical case where the next exact backoff lies beyond the
   deadline. The pure transition law was extended to admit dead letter for that
   derived condition; no string flag or service-only exception was introduced.
3. Review found that an unknown runtime command object could fall through the
   pattern match and return `None`. The interpreter now fails closed with a
   type error after exhausting the closed command variants.
4. Review also separated claim abandonment from expiry. Voluntary release is
   legal only before lease expiry; after expiry the explicit recovery command
   records `WebhookClaimReleaseReason.EXPIRED`.

### Evidence And Provisional Boundary

```text
focused webhook, Postgres, service, architecture:    39 passed
complete Docker/Postgres suite:                     994 passed
assertions weakened:                                  0
skips added:                                          0
real outbound HTTP introduced:                        0
secret values resolved:                               0
```

The service is operationally complete over a typed fake capability, but review
still requires a focused crash-window hardening pass for result-transaction
failure, concurrent late result versus recovery, and command-ledger rollback.
That pass must complete before #513 receives the handoff.

## #521 Webhook Crash And Concurrency Hardening

The focused hardening pass exercised the remaining service boundaries with real
Postgres failures and independent connections. It required no production-code
change: the #512 split-transaction program already satisfied the proposed laws.

### Real Failure Proofs

Test-only Postgres triggers fail the projection update after a successful typed
effect and fail command-ledger insertion after earlier enqueue writes. These
prove:

```text
effect succeeds
  -> result event insert
  -> injected projection failure
  -> complete result transaction rollback
  -> durable state remains attempt-started / in-flight

enqueue writes intent + event + projection
  -> injected command-ledger failure
  -> complete command transaction rollback
  -> no partial durable fact remains
```

After result-transaction failure, exact dispatch replay returns the in-flight
state without calling the capability. Once the lease expires, a fresh service
instance records uncertainty and operator-required evidence.

### Concurrency Proof

A blocking typed outbound capability allows recovery to run while dispatch is
outside every transaction. At lease expiry, recovery and the late result contend
on the same delivery lock:

```text
recovery wins
  -> uncertain + operator-required commits
  -> late success is rejected and cannot overwrite history

result wins
  -> delivered commits
  -> later recovery is rejected and cannot overwrite history
```

Both outcomes are one-winner, append-only, and replay-equivalent. The test uses
real independent Postgres connections and no application mock.

### Additional Laws

- a malformed capability result cannot publish a false attempt result;
- malformed-result rollback leaves the attempt in-flight and exact replay does
  not invoke the capability again;
- dispatch exactly at lease expiry is rejected before the effect;
- recovery strictly before lease expiry is rejected;
- failed command-ledger insertion rolls back intent, journal, projection, and
  ledger together, after which the same command can execute normally;
- test failure injection is confined to test-owned triggers and leaves the
  application schema unchanged after cleanup.

Evidence:

```text
focused service and hardening suite:                 17 passed
complete Docker/Postgres suite:                    1000 passed
production lines changed by hardening:                0
assertions weakened:                                  0
skips added:                                          0
```

#512 is ready to close after this hardening PR merges. #513 inherits a stable
typed outbound capability and must implement DNS/address policy, SSRF defense,
redirect and response bounds, final-boundary secret resolution and signing, and
authenticated FastAPI routes without changing these workflow semantics.

## #513 Secure Webhook HTTP And Authenticated Server Boundaries

### Capability

The webhook application now has two side-effect adapters around the existing
service rather than a second workflow:

```text
WebhookDeliveryService
  -> WebhookOutboundDelivery
       -> HttpWebhookDelivery

authenticated FastAPI route
  -> typed WebhookCommand
       -> WebhookDeliveryService
```

`HttpWebhookDelivery` interprets the existing immutable
`WebhookOutboundRequest`. Its address authority is an exact closed product:

```python
WebhookAddressPolicy(
    grants=(
        WebhookEndpointGrant(
            endpoint_id="orders",
            url="https://hooks.example.test/orders",
            scope=WebhookEndpointScope.PUBLIC,
        ),
    )
)
```

An endpoint identity is insufficient by itself: both identity and exact URL
must match process-bootstrap policy. Public DNS is resolved and pinned for the
same request; the transport connects to the selected global address while
retaining the authorized hostname for `Host` and TLS SNI. Host-local and
runtime-private addresses require their corresponding explicit typed grant.
Loopback, link-local, metadata, unspecified, multicast, reserved, and public
literal addresses cannot be smuggled through the runtime-private constructor.

The outbound interpreter is redirect-free and streams a bounded response. It
keeps transport meanings closed:

```text
2xx                         -> succeeded
408 | 425 | 429 | 5xx       -> retryable failure
3xx                         -> terminal redirect rejection
other 4xx                   -> terminal rejection
connect failure             -> retryable failure
read/write/pool timeout      -> uncertain
other post-dispatch loss     -> uncertain
```

When signing is requested, the durable graph and journal retain only
`SecretReference`. The adapter resolves `SecretValue` after endpoint
authorization and immediately computes deterministic HMAC-SHA256 over the
exact payload bytes. The secret value is absent from requests' durable
descriptors, command results, events, API responses, exception text, and object
representations.

### FastAPI Boundary

The package now exposes authenticated enqueue, claim, release, dispatch,
recovery, and read routes. The boundary:

- validates a bounded identity-attestation header;
- constructs `WebhookAuthority` from bounded subject, workspace, and closed
  scope headers;
- rejects oversized bodies before JSON decoding;
- rejects unknown command fields;
- constructs the already-canonical typed command variants;
- delegates every mutation to `WebhookDeliveryService`;
- imports neither Postgres stores nor SQL;
- contains no commit call;
- returns one canonical redacted state descriptor shared with future MCP and
  CLI adapters.

The application service gained a read capability over the existing UoW. It
acquires the delivery journal's existing transaction-scoped lock before reading
the journal and projection, preventing a concurrent append from creating a
mixed-version two-statement read. It does not request commit.

### Breakpoints And Resolutions

1. The first full run correctly rejected `httpx` in the webhook package because
   transport ownership had not been declared. The exact
   `control_plane_kit.webhook.http` module is now an explicit HTTP transport
   owner. The policy remains closed; the package was not granted a prefix-wide
   exemption.
2. The first FastAPI integration run returned `422` before reaching every route.
   The adapter lazily imports optional FastAPI symbols inside its app factory,
   while postponed annotations had turned the local `Request` type into an
   unresolved string. Removing postponed annotation evaluation binds route
   parameters to the actual FastAPI `Request` class. Authentication and body
   assertions were not changed.
3. Review found operator-state rendering in the HTTP adapter. That would permit
   future MCP projection drift. The pure `webhook_delivery_descriptor`
   interpreter now lives beside the application service and excludes endpoint,
   payload, and signing-reference material. HTTP only bounds and transports
   that canonical descriptor.
4. Review also tightened scope separation: a runtime-private grant cannot be
   used for loopback, metadata, public, unspecified, multicast, reserved, or
   link-local literal addresses. A separate host-local grant is required for
   loopback tests and development.

### Evidence And Handoff

```text
focused HTTP, FastAPI, and architecture cases:      17 passed
complete Docker/Postgres suite:                   1011 passed
assertions weakened:                                 0
skips added:                                         0
new durable stores or projections:                   0
transactions spanning HTTP:                          0
```

Handoff to #514:

- package this exact application service and HTTP interpreter as an
  `ApplicationBlock` with its own Postgres requirement socket;
- provide identity-attestation and signing material through opaque secret
  references and runtime-only resolution;
- realize endpoint grants from explicit bootstrap configuration, not arbitrary
  request data;
- prove a real Docker sender-to-receiver request, deterministic signature,
  authentication failure, readiness, restart reconstruction, and cleanup;
- do not add another workflow, journal, projection, UoW, or outbound result
  language.

## #514 Packaged Webhook ApplicationBlock And Live Deployment

### Capability

The durable webhook application is now a package product with the same product
form as every deployable application:

```python
ApplicationBlock(
    PackageServerSpec(
        product=PackageServerProduct.WEBHOOK_DELIVERY,
        maturity=ProductMaturity.OPERATIONAL,
        capabilities=(CapabilityName.HEALTH_CHECKABLE,),
        verification=VerificationContract((
            HttpCheck(
                check_id="webhook-readiness",
                provider_socket="internal",
                path="/health/ready",
            ),
        )),
    ),
    DockerImageImplementation(
        image=image,
        command=("python", "-m", "control_plane_kit.webhook_server.main"),
        environment={
            "CPK_WEBHOOK_ENDPOINT_POLICY": bounded_policy_json,
            "CPK_WEBHOOK_SIGNING_REFERENCE": signing_reference.reference_id,
        },
        secret_deliveries=(identity_delivery, signing_delivery),
    ),
    BlockSockets(
        requirements=(
            RequirementSocket(
                "database",
                Protocol.POSTGRES,
                ("WEBHOOK_DATABASE_URL",),
            ),
        ),
        providers=(ProviderSocket("internal", Protocol.HTTP),),
    ),
)
```

The graph supplies the database URL only through the Postgres socket
connection. Bootstrap configuration carries a deterministic, bounded,
secret-free endpoint policy plus opaque secret references. Secret values are
resolved only by the Docker interpreter into the process environment.

The packaged process composition root constructs its own:

```text
PostgresWebhookUnitOfWork
  + WebhookDeliveryService
  + HttpWebhookDelivery
  + SystemWebhookPublicAddressResolver
  -> authenticated FastAPI application
```

It does not reuse or import the control-plane instance stores or UnitOfWork.
The graph's `postgresql+psycopg` endpoint remains canonical topology identity;
the process composition root locally interprets it as the direct psycopg DSN.
This keeps driver spelling out of the protocol algebra.

### Canonical Live Proof

The live scenario constructs this graph:

```text
DockerRuntime("webhook-live-runtime")
  |-- ephemeral Postgres DataBlock
  |-- controlled signed receiver ApplicationBlock
  `-- webhook-delivery ApplicationBlock

postgres.internal -> webhook-delivery.database
```

Every graph-owned runtime mutation passes through `DeploymentProgram`:

```text
EmptyGraph
  -> plan -> approve -> admit -> claim -> execute -> advance
  -> desired webhook graph
  -> authenticated enqueue, claim, dispatch, and read
  -> plan -> approve -> admit -> claim -> execute -> advance
  -> EmptyGraph
```

The harness directly creates only its external control Postgres, control
network, and controller process. It attaches that controller to the realized
runtime network so it can continue the deliberately suspended deployment.
Containers and networks represented by the deployment graph are created and
removed only by the canonical coordinator and Docker interpreter.

Live evidence proves:

- an unauthenticated enqueue returns `401`;
- an allowed runtime-private endpoint receives the exact payload;
- the receiver validates the deterministic HMAC signature;
- exact enqueue replay returns the original durable delivery without a second
  intent;
- the authenticated read route reconstructs delivered state from Postgres;
- an endpoint absent from bootstrap authority becomes the closed
  `dead-letter` / `terminal-failure` state;
- teardown removes all proven-owned graph containers and the runtime network;
- the ephemeral topology leaves no owned volume behind.

### Breakpoints And Resolutions

1. The live example initially imported store records from the package root.
   Those records are intentionally store-local. Importing them from
   `control_plane_kit.stores` preserved the public package boundary.
2. The receiver signing value initially appeared as literal implementation
   environment. Effect materialization correctly rejected it. The receiver now
   declares `SecretEnvironmentDelivery`, and both sender and receiver values
   enter only through the live secret resolver.
3. The denied endpoint assertion initially expected a generic failed state.
   The canonical webhook algebra distinguishes retry exhaustion from terminal
   policy rejection; the correct state is `dead-letter` with
   `terminal-failure`. The test now asserts that exact product.
4. Adding `WEBHOOK_DELIVERY` extended the closed `PackageServerProduct` sum.
   The exhaustive HTTP policy-family test correctly failed until webhook
   delivery was explicitly classified as a non-policy application service.
   The scenario's policy-product assertion remains exhaustive.
5. Public DNS resolution belongs to the outbound transport adapter. The exact
   `control_plane_kit.webhook.http` module is the declared socket owner; the
   process composition root only selects that capability.

### Review And Evidence

```text
focused block, catalogue, and architecture tests:     22 passed
complete Docker/Postgres suite:                    1017 passed
real DeploymentProgram webhook proof:                 passed
assertions weakened:                                      0
skips added:                                              0
new workflow/store/journal/projection models:             0
transactions spanning outbound HTTP:                     0
graph-owned resources remaining after teardown:          0
```

The bootstrap/read helpers in the live harness use autocommit for fixture
setup and read-only recovery of plan identity. The packaged webhook server does
not: application commands own `PostgresWebhookUnitOfWork`, stores never commit,
and outbound HTTP remains between short transactions.

Handoff to #515:

- repeat the security, data-engineering, crash-window, ownership, retained-data,
  projection, and test-integrity audits over the packaged process and live
  topology;
- verify restart reconstruction and stable idempotency explicitly at the
  process boundary;
- preserve the one canonical webhook workflow, journal, projection, UoW, and
  outbound result language;
- close the durable webhook parent only after the focused hardening and final
  live proof remain green.

## #525 Graph-Pinned Docker Node Reconciliation

### Breakpoint

The webhook restart proof changed only the opaque identity-secret reference in
the desired graph. The existing pure pipeline correctly produced
`ReconcileNode`, but Docker did not advertise or interpret
`NODE_RECONCILIATION`. Treating reconciliation as another start would have
discarded the base ownership proof and attempted to create desired state over
an existing container name.

The missing execution product is a plan-pinned transition, not one node:

```python
@dataclass(frozen=True)
class ReconcileNodeMaterial:
    before: NodeMaterial
    after: NodeMaterial
```

Forward materialization selects `base -> desired`; compensation selects
`desired -> base`. Both sides come from the exact graph pair already pinned by
the approved plan. The durable descriptor retains topology, references, and
ownership inputs but never resolved secret values.

### Docker Interpretation

The Docker interpreter now advertises `NODE_RECONCILIATION` and maps the pure
product to one typed command:

```text
ReconcileNodeMaterial
  -> ReconcileDockerNodeEffect(before removal, after start)
  -> resolve desired secrets
  -> prove base container and ephemeral material ownership
  -> preserve/prove retained data volumes
  -> remove only the proven-owned base resources
  -> realize and prove desired ownership and publication
```

All safe preconditions occur before removal. Once removal begins, a failure to
realize or prove the desired postcondition becomes
`docker.postcondition-unknown`, not an ordinary retryable failure. Exact replay
converges when desired ownership already exists. Foreign, stale, or ambiguous
ownership fails before mutation.

No transaction or lock spans Docker. No webhook-specific branch entered the
generic runtime interpreter.

### Review Findings

The first complete suite exposed two useful test-integrity findings:

1. The configuration-artifact test still reached through reconciliation as if
   material were one node. It now proves both current and desired artifacts on
   the closed `before x after` product.
2. The heterogeneous HTTP policy-family live test passed in isolation but its
   load-generator process occasionally needed more than five seconds to start
   under the complete 1,000-test run. The same listening postcondition and all
   behavioral assertions remain; only the bounded startup allowance increased
   to ten seconds.

Neither correction weakened application behavior, added a skip, or accepted a
different semantic result.

### Evidence

```text
focused reconciliation/Docker tests:                         35 passed
complete Docker/Postgres suite:                            1023 passed
real webhook restart/reconciliation proof:                    passed
assertions weakened:                                             0
skips added:                                                     0
secret values in descriptors/events/evidence:                    0
transactions spanning Docker:                                   0
graph-owned resources remaining after teardown:                 0
Pottery Factory containers changed:                              0
```

The live proof now exercises:

```text
persist queued webhook
  -> desired graph changes one opaque secret reference
  -> plan -> approve -> admit -> claim -> reconcile -> advance
  -> replacement process reconstructs exact replay from Postgres
  -> signed delivery succeeds
  -> ungranted endpoint dead-letters
  -> graph teardown proves cleanup
```

Handoff back to #515:

- treat restart reconstruction and stable idempotency as proven at the real
  process boundary;
- retain the existing webhook UoW, journal, projection, and outbound result
  language;
- perform the final security, data-engineering, architecture, retained-data,
  and test-integrity audit before closing the webhook parent.

## #515 Durable Webhook Final Hardening And Closeout

### Result

The complete webhook vertical is coherent. The final review found no production
change that would improve the established model without duplicating or
weakening it. This issue therefore closes through review evidence rather than
inventing another abstraction.

### Objects And Boundaries

The application owns one closed workflow language:

```text
WebhookDeliveryIntent
  -> WebhookEvent journal
  -> replay_webhook_events
  -> WebhookDeliveryState projection
```

Postgres retains four conceptually separate relations:

```text
cpk_webhook_intents       original bounded delivery truth
cpk_webhook_events        immutable ordered history
cpk_webhook_projections   rebuildable current state
cpk_webhook_commands      exact command/idempotency ledger
```

All four stores share one `PostgresWebhookUnitOfWork` connection. Stores never
commit. Application commands request commit, and exceptional or unrequested
exit rolls the complete relation set back.

The dispatch boundary retains the external-effect law literally:

```python
with self._unit_of_work_factory() as work:
    # lock, append WebhookAttemptStarted, record command intent
    work.commit()

outbound_result = self._outbound.deliver(request)

with self._unit_of_work_factory() as work:
    # re-lock, prove claim ownership, append result, replace projection
    work.commit()
```

No database transaction spans outbound HTTP. A crash or adapter loss after the
attempt-start commit leaves durable in-flight evidence; expiry recovery records
an uncertain attempt plus operator-required evidence and never resends
automatically.

### Security Review

- the FastAPI boundary requires a bounded constant-time identity attestation;
- actor, workspace, and closed scopes are reconstructed from bounded headers;
- every service command rechecks workspace and scope authority;
- endpoint grants are exact, bounded, and bootstrap-configured;
- runtime-private and public destinations follow separate address laws;
- public DNS is resolved and pinned for the request;
- metadata, loopback, redirect, rebinding, credential-bearing, and ungranted
  destinations fail closed;
- signing values are resolved only at the outbound effect boundary;
- payloads are bounded and digest-checked;
- response headers and bodies are bounded;
- projections expose identity, status, counts, timing, and closed outcomes but
  not endpoint URLs, payload bodies, or signing references;
- transport errors publish closed failure codes rather than exception text.

### Data And Concurrency Review

- enqueue and claim races use independent Postgres connections and one winner;
- journal ordinals and projection versions reject stale writers;
- the command ledger makes exact replay stable across process restart;
- changed intent under the same command identity conflicts;
- claim ownership and lease boundaries are validated by pure state evolution;
- effect-result and expiry-recovery races cannot both publish terminal truth;
- original payload bytes are stored once as application truth and verified
  against their digest when reconstructed;
- schema installation is caller-transactional, idempotent, and preserves rows
  and named constraints;
- no CPI store, CPI UnitOfWork, mutable delivery cursor, or second recovery
  journal entered the application.

### Test-Integrity Review

The review covered the pure algebra, codecs, Postgres stores, application UoW,
service commands, crash windows, secure HTTP adapter, FastAPI boundary,
ApplicationBlock packaging, graph reconciliation, and live process restart.

```text
complete Docker/Postgres suite:                            1023 passed
real webhook deploy/restart/deliver/teardown proof:           passed
assertions weakened:                                             0
skips added:                                                     0
fixture-only replacement of application behavior:               0
parallel webhook models introduced:                              0
```

### Residual Scope

Webhook delivery is now operational as a package-owned ApplicationBlock. It
does not yet claim a general worker scheduler, hosted secret provider, or
production cloud runtime. Those are composition/deployment concerns, not gaps
to fill by expanding the webhook algebra.

Handoff to #438:

- compose webhook delivery with service discovery, OpenTelemetry, and the
  package verification contract;
- retain the webhook application's explicit Postgres requirement and
  application-owned UoW;
- prove credentials and payloads remain absent from cross-product acceptance
  evidence;
- run representative operations through `DeploymentProgram` and verify final
  cleanup.

## #528 Heterogeneous Service Acceptance Recipe

### Result

The service-infrastructure acceptance graph is now an ordinary algebraic
construction. It does not introduce a combined service-stack product or a
second acceptance model:

```python
DeploymentRecipe(
    "service-infrastructure",
    DockerRuntime(
        runtime_id="service-infrastructure",
        children=(
            discovery_postgres,
            webhook_postgres,
            webhook_receiver,
            service_discovery_block(...),
            opentelemetry_collector_block(...),
            webhook_delivery_block(...),
            SocketConnection(
                "discovery-postgres", "internal",
                "service-discovery", "database",
            ),
            SocketConnection(
                "webhook-postgres", "internal",
                "webhook-delivery", "database",
            ),
        ),
    ),
)
```

### Objects, Morphisms, And Laws

The objects are independently identified package and application blocks:

```text
Ephemeral Postgres A -> Service Discovery
Ephemeral Postgres B -> Webhook Delivery
OpenTelemetry Collector
Controlled Webhook Receiver
```

The two socket connections are the only topology morphisms. They carry exact
Postgres protocol identity and compile to the application-owned environment
assignments `DISCOVERY_DATABASE_URL` and `WEBHOOK_DATABASE_URL`.

The recipe demonstrates these laws:

- each stateful application owns an explicit and independent database
  requirement;
- provider health precedes consumer startup in the compiled `ActivityPlan`;
- a missing requirement remains an explicit graph-validation finding;
- an OTLP provider cannot satisfy a Postgres requirement;
- product identity, protocol identity, and opaque secret references survive
  graph descriptor round-trip;
- webhook destination registration remains dynamic application truth rather
  than a false socket edge;
- no secret value enters the recipe, descriptor, plan, or test evidence.

### Review And Test Integrity

The first focused run exposed one test-only diagnostic mismatch. The pure
compiler correctly rejected an OTLP-to-Postgres edge with:

```text
consumer webhook-delivery.database expects postgres,
connection provides otlp-http
```

The test was tightened to assert those exact closed identities instead of the
generic word `protocol`. No application behavior changed.

```text
focused acceptance tests:                                  4 passed
complete Docker/Postgres suite:                         1027 passed
assertions weakened:                                          0
skips added:                                                  0
parallel graph or product models introduced:                  0
```

### Handoff To #529 And #532

- run this exact graph through the existing Postgres-backed
  `DeploymentProgram`; do not recreate planning, approval, admission, claim,
  execution, advancement, or read models;
- preserve two separate application database requirements and one shared
  operator-command transaction boundary;
- the graph intentionally advertises canonical SQLAlchemy-style Postgres URLs
  as `postgresql+psycopg://...`;
- the webhook process already interprets that graph URL into a direct psycopg
  DSN at its driver boundary;
- the discovery process currently passes the graph URL directly to psycopg,
  while its standalone live test masks the mismatch with `postgresql://`;
- implement that narrow driver-boundary interpretation in #532 before the live
  heterogeneous proof in #530;
- do not change the graph protocol, socket language, or canonical topology URL
  to accommodate one database driver.
