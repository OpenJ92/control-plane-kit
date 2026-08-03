# SERVER-SEED Run 0001

Status: #827 closeout for the external OCI seed-product lane.

Parent: #822

Topology:

```text
#823
  -> #824 hello-server
  -> #825 http-active-router
  -> #826 http-multiplexer
  -> #828 postgres-server
  -> #827 closeout
```

## Boundary

The seed lane moves a small set of product processes into
`control-plane-kit-servers` so the later operations milestone has realistic
deployment targets. It does not prove cpk-server can plan, approve, admit,
execute, observe, or advance those products yet.

The split is:

```text
product descriptor
  graph-visible value

OCI image
  process/runtime artifact

published digest
  execution identity

catalogue entry
  discoverable package coordinate
```

Core must not import product implementations. Server products may import core
contracts only to express product descriptors, sockets, protocols,
configuration, verification, and catalogue metadata.

## Frozen Sources Inspected

Reference tag: `pre-server-product-extraction-2026-07-20`

```text
control_plane_kit/servers/hello.py
control_plane_kit/servers/http_active_router.py
control_plane_kit/servers/http_multiplexer.py

tests/test_hello_server_block.py
tests/test_hello_runtime.py
tests/test_http_active_router_server_block.py
tests/test_http_multiplexer_server_block.py
tests/test_server_command_templates.py
```

## Product Selection

The seed set is intentionally small:

```text
hello-server
  ordinary HTTP app shape
  optional named HTTP/Postgres dependencies

http-active-router
  one active upstream
  router/proxy shape

http-multiplexer
  required primary upstream
  optional observer fanout

postgres-server
  data-bearing Postgres provider
  retained data and secret-delivery laws
```

The following frozen products remain out of this lane: weighted balancer, rate
limiter, circuit breaker, retry, timeout, bulkhead, cache, fault injector,
traffic logger, load generator, CoreDNS, service discovery, and auth gateway.

## Law Card: hello-server

Reference tests:

```text
tests/test_hello_server_block.py
tests/test_hello_runtime.py
tests/test_server_command_templates.py
```

Behavioral laws:

- `HELLO_MESSAGE` is startup environment, not hard-coded command source.
- Descriptor/projection views redact the message value.
- The product provides one HTTP socket.
- Each named dependency expands to a paired HTTP requirement and Postgres
  requirement.
- Dependency names must be lowercase slug identifiers matching
  `[a-z][a-z0-9-]*`.
- Duplicate dependency names fail closed.
- Dependency values are socket-derived endpoint bindings, not product source.
- Generated command source includes environment names, not concrete endpoint
  values.
- Generated command source parses as Python syntax.
- HTTP dependency checks are bounded and do not follow redirects.
- Postgres dependency checks require an explicit Postgres URL scheme, not a
  string-prefix shortcut.

Expected successor evidence:

- descriptor tests for provider socket and dependency requirements;
- environment/secret-redaction tests;
- generated command syntax tests;
- published-image smoke proving HTTP response;
- optional dependency smoke proving injected endpoint names are consumed from
  environment;
- negative tests for invalid dependency names and duplicate names.

Old structural assumptions to discard:

- `DeploymentRecipe`/`DockerRuntime` construction inside the frozen package;
- direct imports from `control_plane_kit.servers`;
- `python:3.13-alpine` as the product identity;
- examples that call frozen Docker interpreters directly.

Future operations laws:

- graph compilation wires `HELLO_HTTP_<NAME>_URL` and
  `HELLO_DATABASE_<NAME>_URL`;
- cpk-server deploys `hello-server` through plan/approve/admit/execute;
- teardown and no-op proofs run through durable operations.

## Law Card: http-active-router

Reference tests:

```text
tests/test_http_active_router_server_block.py
tests/test_server_command_templates.py
```

Behavioral laws:

- The product provides one HTTP socket.
- The product requires one active upstream HTTP socket.
- The active upstream URL is injected through `ACTIVE_TARGET_URL`.
- The generated command source includes `ACTIVE_TARGET_URL`.
- Generated command source parses as Python syntax.
- In the pure behavior model, the active target can switch between known
  targets.
- Unknown active targets fail closed.
- Runtime contract names are explicit: `targets` and `active_target`.

Expected successor evidence:

- descriptor/socket tests for the HTTP provider and active HTTP requirement;
- command syntax and environment-name tests;
- published-image smoke proving forwarding to an injected upstream;
- negative evidence for missing/invalid target;
- explicit unsupported result for runtime mutation if the seed product does not
  yet expose an authenticated control route.

Old structural assumptions to discard:

- frozen in-memory `HttpActiveRouterServer` class name as public API;
- direct use of frozen `RuntimeContract` constructors by operators;
- graph compilation tests that depend on frozen package layout.

Future operations laws:

- runtime switch acceptance should execute through cpk-server operations, not a
  local imperative helper;
- any mutable route must require the normal authenticated control boundary.

## Law Card: http-multiplexer

Reference tests:

```text
tests/test_http_multiplexer_server_block.py
tests/test_server_command_templates.py
```

Behavioral laws:

- The product provides one HTTP socket.
- The `primary` HTTP requirement is required.
- `observer-a` and `observer-b` HTTP requirements are optional.
- The primary target owns the client response.
- Observers receive copied request data.
- Observer failure is recorded but does not replace the primary response.
- Missing or unknown primary target fails closed.
- Runtime contract names are explicit: `primary_target` and `observers`.
- The generated command source includes `MULTIPLEXER_PRIMARY_URL`,
  `MULTIPLEXER_OBSERVER_A_URL`, and `MULTIPLEXER_OBSERVER_B_URL`.
- Generated command source parses as Python syntax.

Expected successor evidence:

- descriptor/socket tests for required primary and optional observers;
- command syntax and environment-name tests;
- published-image smoke proving the primary response path;
- observer delivery smoke where deterministic and non-flaky;
- negative evidence for missing primary.

Old structural assumptions to discard:

- frozen in-memory `HttpMultiplexerServer` class name as public API;
- graph compilation tests that depend on frozen package layout;
- any assumption that observer delivery is durable event persistence.

Future operations laws:

- fanout topology should be deployable through durable operations after #821;
- observer failures should stay product behavior unless operations adds a
  separate observation contract.

## Law Card: postgres-server

Reference tests:

```text
new-law: no frozen package-owned Postgres server product existed
```

Behavioral laws:

- The product provides Postgres over TCP.
- The default publication mode is private-only.
- Credentials are secret deliveries, never descriptor or catalogue values.
- Retained data volume identity is explicit.
- Generic cleanup must not remove retained data without an explicit destructive
  policy.
- Readiness proves database connection acceptance, not merely process start or
  TCP reachability.
- The product wrapper does not implement cpk-server internal stores,
  migrations, or UnitOfWork.

Expected successor evidence:

- descriptor/socket tests for Postgres provider semantics;
- secret-boundary tests proving credentials do not enter descriptors;
- retained-data classification tests;
- live smoke proving Postgres accepts a connection;
- residue audit that distinguishes retained data from disposable containers and
  networks.

Old structural assumptions to discard:

- treating frozen `DockerPostgresImplementation` as a server product;
- conflating workload database nodes with cpk-server operations storage;
- assuming process start is database readiness.

Future operations laws:

- operations acceptance may deploy application products against this database
  product;
- cpk-server itself still owns an independent operations database.

## Shared Command Rendering Laws

Reference test:

```text
tests/test_server_command_templates.py
```

Laws:

- generated stdlib server commands have shape `("python", "-c", source)`;
- generated source parses as Python syntax;
- generated syntax errors report template name and line without retaining
  sensitive source;
- valid generated source is preserved exactly by validation.

External successor:

These laws belong in `control-plane-kit-servers` shared support only after two
seed products prove the common renderer is shared product infrastructure. Until
then, a product may own a local renderer or use a recorded bootstrap exception.

## Dry-Run Findings

The seed lane is coherent. None of the selected HTTP products require durable
operations to smoke honestly:

```text
hello-server
  request -> response

http-active-router
  request -> active upstream response

http-multiplexer
  request -> primary response + optional observer delivery
```

`postgres-server` is also coherent as a product, but it has stronger retained
data and secret-delivery laws than the HTTP products. It should be treated as a
data product, not as a CPK-owned application server.

The next implementation issues should not claim graph workflow acceptance.
Their closeout evidence is product-level:

```text
descriptor valid
  -> image published
    -> digest-pulled smoke passes
      -> catalogue checksum updated
```

## Handoffs

To #824:

- implement `hello-server` with closed dependency names, redacted message
  descriptors, HTTP provider, paired HTTP/Postgres requirements, and digest
  smoke.

To #825:

- implement `http-active-router` with HTTP provider, active HTTP requirement,
  injected `ACTIVE_TARGET_URL`, forwarding smoke, and fail-closed missing target
  behavior.

To #826:

- implement `http-multiplexer` with HTTP provider, required primary, optional
  observers, primary-response ownership, deterministic observer evidence where
  possible, and fail-closed missing primary behavior.

To #828:

- implement `postgres-server` as a data-bearing OCI product around official
  Postgres unless a thin wrapper is required; preserve secret, readiness, and
  retained-data boundaries.

To #827:

- collect immutable digests, descriptor hashes, smoke evidence, catalogue
  checksum, and handoffs to #821, #819, #676, and #806.

## Closeout: Seed Catalogue Report

Final server repository PRs:

```text
#824 hello-server          -> control-plane-kit-servers PR #11
#825 http-active-router    -> control-plane-kit-servers PR #12
#826 http-multiplexer      -> control-plane-kit-servers PR #13
#828 postgres-server       -> control-plane-kit-servers PR #14
```

The seed catalogue now has five completed declarations in
`control-plane-kit-servers`: the previously published `cpk-server` baseline plus
the four products from this lane. The final packaged catalogue checksum after
Postgres inclusion is:

```text
0efffbdfe0581b8f47b7cb854480f2b4e79227835b0a0e91f4207fbfe1aa3d7b
```

### hello-server

```text
product id: hello-server
source folder: products/hello_server
image: ghcr.io/openj92/control-plane-kit-servers/hello-server@sha256:0b5d62c2706bdfc5b53b67c7e0a72e36b8af7d13f8b2abf26eaa6e6eb7dda5f0
descriptor sha256: 91da997ce5a17ad0401a9b75b0758dec1d6083fce01f4b9c960c79c3506a7967
catalogue checksum after inclusion: 0b54d37d33bf0a17e0f2073d40f445211fa521c1ed053dea62b8d1f875c2d038
final catalogue checksum: 0efffbdfe0581b8f47b7cb854480f2b4e79227835b0a0e91f4207fbfe1aa3d7b
smoke: sh scripts/hello_server_published_image_smoke.sh
evidence: Hello, seed!
```

Graph-visible sockets:

- provider `internal`: HTTP over TCP.
- dynamic dependency socket families are not encoded in the static descriptor
  yet; operations/product-parameterization must generate those instance-specific
  bindings later.

Configuration and environment contract:

- `HELLO_MESSAGE` is startup environment.
- dependency endpoints are supplied through generated environment names.
- descriptor tests prove process dependency names are closed and unique.

Explicit unsupported behavior:

- no authenticated runtime mutation surface;
- no durable workflow acceptance claim;
- no claim that dynamic dependency sockets are complete in the static core
  descriptor language.

Residual risks:

- operations extraction must decide the exact parameterized product-instance
  language for `k` named dependencies.

### http-active-router

```text
product id: http-active-router
source folder: products/http_active_router
image: ghcr.io/openj92/control-plane-kit-servers/http-active-router@sha256:9edd29c8b62f6413c7acb4009bfa655c065a31a0eac8728ec9d4350122e0a60d
descriptor sha256: 335ad3933dfb7221140e14f21d672709d4bed7e3595a28a9b6203665b7e59cca
catalogue checksum after inclusion: be67942d681cd0272bf4b69ca0f4f39c45ce045895492cfbf4ce66b83dd96f0b
final catalogue checksum: 0efffbdfe0581b8f47b7cb854480f2b4e79227835b0a0e91f4207fbfe1aa3d7b
smoke: sh scripts/http_active_router_published_image_smoke.sh
evidence: active upstream
```

Graph-visible sockets:

- provider `internal`: HTTP over TCP.
- requirement `active`: HTTP over TCP.

Configuration and environment contract:

- `ACTIVE_TARGET_URL` supplies the active upstream.
- the process requires an absolute `http` or `https` URL.

Explicit unsupported behavior:

- no authenticated runtime target switch route in this seed product;
- no graph mutation or activity history.

Residual risks:

- durable operations must express target switching as a graph transition or an
  authenticated control-route mutation, not as an imperative local helper.

### http-multiplexer

```text
product id: http-multiplexer
source folder: products/http_multiplexer
image: ghcr.io/openj92/control-plane-kit-servers/http-multiplexer@sha256:2b6466d87c7642691c4ce2ee52022450d7b7cf1055f1f25a1449adbb5c8131ec
descriptor sha256: 22dc8ea50627b6b1c412bec89199436fb04ff95860570818006df7b70e23ba96
catalogue checksum after inclusion: 173c82a96649b2489c81fe79e33d33a106e6adbdee544115971c9aa56412f912
final catalogue checksum: 0efffbdfe0581b8f47b7cb854480f2b4e79227835b0a0e91f4207fbfe1aa3d7b
smoke: sh scripts/http_multiplexer_published_image_smoke.sh
evidence: primary response and observer received copy
```

Graph-visible sockets:

- provider `internal`: HTTP over TCP.
- required requirement `primary`: HTTP over TCP.
- optional requirements `observer-a` and `observer-b`: HTTP over TCP.

Configuration and environment contract:

- `MULTIPLEXER_PRIMARY_URL` is required.
- `MULTIPLEXER_OBSERVER_A_URL` and `MULTIPLEXER_OBSERVER_B_URL` are optional.
- primary response owns the client response; observers are fail-open copies.

Explicit unsupported behavior:

- observer delivery is product behavior, not durable event persistence;
- no authenticated runtime observer mutation.

Residual risks:

- operations acceptance must prove fanout topology through the deployment
  program rather than direct smoke scripts.

### postgres-server

```text
product id: postgres-server
source folder: products/postgres_server
image: docker.io/library/postgres@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777
descriptor sha256: f6637870f6148ae9134148ea150ab8fb130249186468cb6dc8de2f98a2f44885
catalogue checksum after inclusion: 0efffbdfe0581b8f47b7cb854480f2b4e79227835b0a0e91f4207fbfe1aa3d7b
smoke: sh scripts/postgres_server_image_smoke.sh
evidence: select 1
```

Graph-visible sockets:

- provider `postgres`: Postgres over TCP.
- no requirements.
- private-only by default; no host publication is part of the descriptor or
  smoke.

Configuration and environment contract:

- public bootstrap environment: `POSTGRES_DB=cpk`, `POSTGRES_USER=cpk`.
- secret delivery slot: `POSTGRES_PASSWORD` from
  `secret://control-plane-kit/postgres/password`.
- retained data resource: `postgres-data`.
- readiness: `PostgresQueryCheck` with `select-one`.

Explicit unsupported behavior:

- no CPK schemas;
- no migrations;
- no stores;
- no UnitOfWork implementation;
- no automatic retained-data deletion.

Residual risks:

- operations extraction must keep workload database nodes distinct from the
  cpk-server operations database.
- future interpreters must enforce retained-data cleanup laws before teardown
  can touch database volumes.

## Closeout Validation

Server repository validation:

```text
git diff --check
./test.sh
sh scripts/hello_server_published_image_smoke.sh
sh scripts/http_active_router_published_image_smoke.sh
sh scripts/http_multiplexer_published_image_smoke.sh
sh scripts/postgres_server_image_smoke.sh
sh scripts/docker_residue_audit.sh
```

Observed evidence:

```text
./test.sh: passed
hello-server: Hello, seed!
http-active-router: active upstream
http-multiplexer: primary response and observer received copy
postgres-server: select 1
Docker residue audit: passed
```

No local-only image acceptance remains in this lane. GHCR products are smoked by
immutable digest. `postgres-server` is the intentional exception: it uses the
official Docker Hub image by immutable digest and is recorded as
`external-oci`, not as a package-owned GHCR image.

## Closeout Handoffs

To #821:

- operations extraction can use the published seed catalogue for realistic
  targets, but the seed smokes are product-level acceptance only.
- canonical future acceptance should deploy:

```text
postgres-server
  <- hello-server
    <- http-active-router
      <- http-multiplexer
```

or another small topology using the same catalogue entries.

To #819:

- `hello-server` transfer is complete as a product descriptor, OCI image, and
  published-image smoke.
- Hello workflow acceptance remains deferred until durable operations exist.

To #676:

- recursive cpk-server work should reuse the same product-catalogue convention:
  descriptor value plus immutable OCI digest. Do not invent a privileged
  self-registration lane.

To #806:

- interpreter/runtime extraction must consume these descriptors through the
  generic product catalogue boundary.
- Docker interpreters need support for both package-owned GHCR images and
  external digest-pinned OCI images.
- retained-data enforcement is now required by `postgres-server`.
