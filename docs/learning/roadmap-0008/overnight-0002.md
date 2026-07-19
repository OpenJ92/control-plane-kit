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

- retry behavior must remain distinct from cache hit/miss/stale evidence;
- retries must not run while a Postgres transaction or cache lock is held;
- retries must define request replayability and bounded attempt policy rather
  than infer safety from free-form methods;
- preserve ProductMaturity and dedicated control-route authorization;
- never retry an uncertain non-idempotent effect by default.
