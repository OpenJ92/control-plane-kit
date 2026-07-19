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
