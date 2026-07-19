# Roadmap 0008 Gate G Overnight Run 0001

Date: 2026-07-19

Branch: `roadmap/0008-activity-execution-and-runtime-mutation`

Parent: #402

## Operating Contract

This run continues Gate G autonomously from the completed #403 capability,
#443 protocol, and #444 configuration foundations. Ordinary test failures,
fixture drift, missing interpreter cases, and coherent algebra extensions are
diagnostic events to resolve and record rather than automatic operator stops.

The run still stops for unresolved authorization, secret-disclosure,
destructive-retention, transaction-ownership, paid-cloud, or genuinely
ambiguous architectural decisions. Tests may be corrected only when their
intended law is preserved or strengthened.

Docker remains the only concrete runtime. Unused Docker resources may be
removed if storage is exhausted, but these running Pottery Factory containers
must remain untouched:

```text
pottery-factory-cloudflare
pottery-factory-auth
pottery-factory-storage
pottery-factory-api
pottery-factory-postgres
```

## Baseline

```text
Roadmap branch head: d599b50
Complete Docker/Postgres suite: 786 tests, OK
Live transport proof: TCP and UDP, OK
Live configuration proof: typed render, read-only mount, replay, cleanup, OK
```

## Issue Reconciliation

The run begins by comparing open issue claims with merged PRs, tests, and the
current package. An issue is closed only when its complete acceptance evidence
already exists.

| Issue | Classification | Evidence and action |
|---|---|---|
| #467 | Stale completed hardening issue | PR #469 merged concurrent exact-artifact convergence, 779-test validation, and live configuration proof. Close with evidence. |
| #340 | Stale completed Gate E issue | PR #342 merged the canonical nullable failure descriptor through read service, FastAPI, MCP, and CLI with 652-test validation. Close with evidence. |
| #241 | Intentional open handoff parent | Roadmap 0008 children #242-#245 are complete, but #246 deliberately remains a Roadmap 0009 prerequisite. Keep open; do not pull CPI lifecycle semantics into Gate G. |
| #1, #5-#7, #9, #61-#63 | Stale completed Roadmap 0004 topology | Roadmap PR #60 and child PRs #64-#71 delivered and reviewed the HTTP teaching-server catalogue. Close each with its exact PR evidence. |
| #8 | Genuine future product work | TCP switching and Postgres pooling remain represented by Gate G #429/#431. Keep open until those products deliberately supersede or complete it. |
| #86-#92, #103 | Stale completed Roadmap 0006 topology | PRs #94, #96-#101, and #104 delivered and hardened the shared read service plus projection, FastAPI, CLI, and MCP adapters. Close each with exact evidence. |

Issues #12, #16, #18, and #19 are not closed merely because later work is
adjacent. Their original acceptance scopes require a separate comparison and
are not prerequisites for #457.

## Breaking Points

### #473: Architecture ownership rejected the new package

The first complete Docker/Postgres run reached 793 tests with 792 passing. The
architecture test rejected the newly discovered `verification` package because
it had no declared dependency rule.

This was not resolved by excluding the package. The dependency algebra was
extended explicitly:

```text
verification -> types
algebra      -> verification
topology     -> verification
```

`verification` remains pure and owns no transport, persistence, workflow, or
runtime dependencies. This preserves the AST policy's exhaustiveness: adding a
new package without declaring its permitted imports continues to fail.

## #473 Decision Log: Closed Verification Contract

### Capability

Blocks can now declare bounded semantic checks as part of `BlockSpec` product
truth. The contract survives compilation and the authoritative graph codec, but
does not execute yet.

```python
BlockSpec(
    role_id="orders-api",
    verification=VerificationContract(
        checks=(
            HttpCheck(
                check_id="can-list-orders",
                provider_socket="public",
                path="/internal/tests/orders",
            ),
        ),
    ),
)
```

The closed language contains HTTP, DNS, Postgres, Redis, broker, object-storage,
and SMTP checks. Every variant has an exact protocol set. `PostgresQueryCheck`
deliberately exposes only the closed `SELECT_ONE` operation; there is no SQL
string. HTTP checks carry a relative path and provider socket identity, never a
URL.

### Objects, Morphisms, And Laws

```text
VerificationCheck
  = HttpCheck
  | DnsResolveCheck
  | PostgresQueryCheck
  | RedisCheck
  | BrokerRoundTripCheck
  | ObjectStorageRoundTripCheck
  | SmtpAcceptanceCheck

VerificationContract -> exact descriptor -> VerificationContract
VerificationCheck    -> accepted provider protocols
```

- policies bound timeout, attempts, and evidence bytes;
- contract check identities are unique and bounded;
- descriptor keys and variants are exact and fail closed;
- `BlockSpec` rejects untyped contract values at construction;
- process, transport, application-health, and readiness probes remain unchanged;
- no interpreter, transport, store, or external effect was introduced.

### Evidence

```text
Focused Docker tests: 27 passed before review strengthening
Complete Docker/Postgres suite after AST ownership correction: 793 passed
Final complete Docker/Postgres suite: 795 passed
Test skips added: 0
Assertions weakened: 0
```

### Handoff To #474

Validate every check against the provider socket on its compiled node, preserve
contract changes as graph differences, and materialize endpoint-bearing check
requests only from the exact graph pinned by the approved plan. Adapters must
not import stores or choose current graph truth.

### #474: Review found a displaced pre-existing assertion

The first focused implementation inserted the new graph-diff test immediately
before the final assertion of the preceding no-change test. That assertion then
appeared inside the new test and failed because its local value did not exist.

The correction did not delete the assertion. Review restored it to its original
test and added a separate planning assertion for the new behavior:

```text
graph == graph
  -> empty diff
  -> summary == "no changes"

verification contract changes
  -> BLOCK_SPECIFICATION change
  -> exactly one ReconcileNode for the owner
```

This is why diff review remains part of the issue loop even after a green test
run: a misplaced assertion can make one test fail while quietly weakening an
adjacent test if the correction is made mechanically.

### #474: Architecture ownership rejected an undeclared effect dependency

The focused architecture policy reported that `effects.material` imported the
new pure `verification` language without declaring that dependency. The policy
was extended explicitly:

```text
effects -> verification
```

The rule was not bypassed and the package scan was not narrowed. Materialization
may interpret a verification contract into immutable endpoint-bearing effect
material; verification still cannot import effects, stores, runtimes, or
adapters.

## #474 Decision Log: Graph-Pinned Verification Material

### Capability

Verification contracts now participate in the complete pure transition from
desired topology to executable material:

```text
BlockSpec.verification
  -> graph validation
  -> BLOCK_SPECIFICATION graph diff
  -> ReconcileNode activity
  -> pinned NodeMaterial
  -> tuple[VerificationCheckMaterial, ...]
```

Each check is paired with the endpoint for its declared provider socket only
after that endpoint has been materialized from the exact desired graph pinned
by the approved plan:

```python
checks = materialize_verification_contract(node_material)

VerificationCheckMaterial(
    node_id="api",
    check=HttpCheck(
        check_id="api-semantic-check",
        provider_socket="internal",
        path="/internal/tests/dependencies",
    ),
    endpoint=EndpointMaterial(
        socket_name="internal",
        protocol=Protocol.HTTP,
        ...,
    ),
)
```

### Laws

- a check must reference a provider socket on the same node;
- the provider protocol must belong to the check variant's closed protocol set;
- contract changes remain explicit graph and plan data;
- endpoint selection occurs only from `NodeMaterial.endpoints`;
- arbitrary URLs and current-graph lookup never enter the materializer;
- adapters do not import stores or choose topology truth;
- semantic verification remains distinct from process, transport, health, and
  readiness probes;
- empty contracts preserve existing deployment behavior.

### Evidence

```text
Focused Docker suite after corrections: 45 passed
Complete Docker/Postgres suite: 799 passed
Test skips added: 0
Assertions weakened: 0
Pre-existing assertions restored during review: 1
```

### Handoff To #475

Interpret the closed `VerificationCheckMaterial` variants through registered
capability adapters. Adapters receive graph-pinned endpoint material and bounded
policy only. They must not receive stores, graph selectors, arbitrary commands,
arbitrary SQL, or arbitrary URLs. Unsupported product checks remain explicit
unsupported outcomes rather than optimistic success.

### #475: Adapter dry run found missing graph identity

`VerificationCheckMaterial` initially carried the node, check, and endpoint but
not the identity of the pinned graph. That was sufficient to prove endpoint
selection but insufficient to correlate durable evidence without consulting
some later mutable graph pointer.

The material language was corrected in place:

```text
VerificationCheckMaterial
  = node_id
  x graph_id
  x VerificationCheck
  x EndpointMaterial
```

`materialize_verification_contract` now accepts the complete
`MaterializedEffectRequest`, obtains `material_graph_id` from that already
pinned value, and rejects non-node material. No adapter receives a graph store
or graph selector.

### #475: AST ownership rejected undeclared transports

The new concrete adapter passed all semantic tests, but architecture policy
rejected its direct `httpx` and `socket` imports. The exact module was added as
an owner of those transports:

```text
httpx  -> control_plane_kit.adapters.verification
socket -> control_plane_kit.adapters.verification
```

Transport ownership was not granted to the package generally. HTTP, Redis, and
future transports therefore remain statically discoverable and reviewable.

### #475: Review prevented a second observation model

The first result name was `VerificationObservation`. Although it was only an
adapter completion value, that name competed with the canonical operational
observation model and contradicted the Gate G prohibition on parallel
observation languages.

It was renamed before merge:

```text
VerificationResult
  = VerificationCompleted
  | VerificationUnsupported

VerificationCompleted
  -> later interpreted by #476 into the canonical observation store
```

No verification-specific store, projection, or observation repository was
introduced.

## #475 Decision Log: Closed Verification Dispatch And Adapters

### Capability

The package now has a typed immutable registry over the complete verification
capability set:

```python
registry = VerificationInterpreterRegistry(
    {
        VerificationCapability.HTTP: HttpVerificationInterpreter(http_policy),
        VerificationCapability.REDIS: RedisVerificationInterpreter(redis_policy),
    }
)

result = registry.execute(check_material)
```

Missing interpreters return `VerificationUnsupported`. Registered interpreters
must advertise their exact capability, and results must reproduce the exact
`node_id x graph_id x check_id` identity and capability of their input.

The deterministic `StaticVerificationInterpreter` provides effect-free fixture
behavior without replacing application services or persistence with mocks.

### Representative Concrete Interpreters

```text
HttpVerificationInterpreter
  -> authorized graph-pinned endpoint
  -> GET relative contract path
  -> no redirects
  -> bounded response consumption
  -> status and byte-count evidence only

RedisVerificationInterpreter
  -> authorized graph-pinned endpoint
  -> exact RESP PING command
  -> bounded response consumption
  -> exact PONG comparison
  -> byte-count evidence only
```

HTTP never retains a response body. Redis never accepts arbitrary commands.
Neither interpreter imports stores, selects current topology, commits data, or
creates an external-effect transaction boundary.

### Objects, Morphisms, And Laws

```text
VerificationCheck -> VerificationCapability
VerificationCheckMaterial -> VerificationIdentity
Registry x VerificationCheckMaterial -> VerificationResult

VerificationEvidence
  = HttpVerificationEvidence(status_code, response_bytes)
  | RedisVerificationEvidence(response_bytes)
```

- the seven check variants map exhaustively to seven closed capabilities;
- unsupported capability is distinct from attempted failure;
- interpreter capability and result identity lies fail closed;
- all attempts and evidence sizes remain bounded by contract policy;
- address policy is applied before transport;
- public redirects cannot retarget verification;
- secret references may be resolved only at the transport authorization
  boundary and secret values never enter results;
- results are completion values, not a competing durable observation model.

### Evidence

```text
Focused Docker suite: 33 passed
Complete Docker/Postgres suite: 806 passed
Test skips added: 0
Assertions weakened: 0
Transport ownership exceptions added: 0
Exact transport owners declared: 2
```

### Handoff To #476

Adapt `VerificationCompleted` and `VerificationUnsupported` into the existing
canonical observed-state and operator-read surfaces. Preserve graph, node, and
check identity; expose only typed bounded evidence; keep unsupported distinct
from failed; and do not introduce a verification store, mutable latest-result
cache, or parallel projection model. API, CLI, and MCP should consume the shared
read service rather than decode verification results independently.

### #476: Architecture policy rejected an undeclared workflow dependency

The first complete Docker/Postgres run reached 811 tests with 810 passing. The
new verification command service imported the pure `verification` language,
but the `workflows` dependency rule did not permit that package.

The implementation was not moved to evade the policy. The workflow layer is
the application transaction boundary that interprets a graph-pinned
`VerificationResult` into canonical observed-state truth, so the dependency
algebra was extended explicitly:

```text
workflows -> verification
```

The reverse dependency remains forbidden: `verification` still depends only on
`types` and cannot import workflows, stores, effects, or adapters. This keeps
the pure language independent while making its one application interpreter
statically visible.

### #476: Review found missing durable intent before verification dispatch

The first green implementation used a short read-only preflight transaction,
executed the adapter, and then persisted a terminal observation. Although no
transaction crossed the effect, a process crash after dispatch and before the
result transaction would have left no durable evidence that verification was
attempted.

The command was corrected to use the existing observation store as an
append-only intent/result journal for this operation:

```text
short transaction: persist starting/unknown verification intent
  -> commit
    -> bounded verification adapter
      -> short transaction: persist exact terminal result
```

Both rows retain the same `workspace x graph x node x check` identity. The
intent is immutable and remains visible if the effect loses its result; the
terminal observation becomes latest only after it commits. No verification
store, mutable cursor, or graph write was introduced.

### #476: Mixed ISO timestamp precision reversed causal observation order

The complete suite after durable-intent hardening reached 813 tests with two
failures. Domain time placed the terminal result one microsecond after intent,
but `cpk_observations.observed_at` is intentionally stored and ordered as text.
Python encoded the exact-second intent as `12:00:00Z` and the result as
`12:00:00.000001Z`; lexical ordering therefore selected the intent because `Z`
sorts after `.`.

The tests were not changed. The command boundary now renders both timestamps in
one canonical UTC form with fixed microsecond precision:

```text
2026-07-19T12:00:00.000000Z
2026-07-19T12:00:00.000001Z
```

This makes the existing text ordering agree with causal time for all rows
created by the command and removes observation-ID ordering from the semantic
result.

## #476 Decision Log: Canonical Verification Observations

### Capability

Package-owned semantic checks now pass through one application workflow and
the existing observed-state system:

```text
ExecuteVerification
  -> authorize verification:execute
  -> verify workspace owns pinned graph
  -> persist immutable starting intent
  -> commit
  -> VerificationInterpreterRegistry.execute(material)
  -> persist immutable terminal observation
  -> commit
  -> InstanceReadService.observed_state
       -> FastAPI
       -> MCP
       -> CLI
```

The observation algebra gained one exact layer and its closed outcomes:

```text
ProbeKind.SEMANTIC_VERIFICATION

ProbeOutcome
  = VERIFIED
  | VERIFICATION_FAILED
  | TIMED_OUT
  | MALFORMED
  | REJECTED
  | UNSUPPORTED
  | UNKNOWN
```

`UNKNOWN` is used only for durable pre-dispatch intent. Unsupported capability
is not collapsed into attempted failure, and semantic success is not collapsed
into application health.

### Data And Security Laws

- `VerificationAuthority` uses the closed `VerificationScope.EXECUTE` value;
- workspace and graph ownership fail before adapter dispatch;
- intent and result are immutable rows in the canonical observation store;
- no transaction or lock spans HTTP, Redis, or another verification adapter;
- adapter loss leaves visible starting/unknown evidence and no terminal lie;
- graph pointers are never written by verification;
- graph changes make old verification stale only through read-time projection;
- evidence contains result identity, capability, outcome, attempt count, and
  bounded typed evidence, never endpoint addresses or response bodies;
- conditional schema changes preserve rows and stop changing constraint
  identities after the current language is installed;
- API, MCP, and CLI perform no independent verification decoding.

### Representative Live Proof

`./verification-live-test.sh` creates only labeled, issue-owned Docker
resources. It starts a real HTTP fixture and Postgres, executes the concrete
HTTP verifier through `VerificationCommandService`, confirms the durable
`starting -> verified` history, reads the shared projection, proves the target
address is absent from that projection, and removes every owned resource.

```text
Live verification passed: HTTP 200
  -> durable starting/verified observations
  -> shared redacted projection
```

### Evidence

```text
Complete Docker/Postgres suite: 813 passed
Live Docker HTTP/Postgres proof: passed
Test skips added: 0
Assertions weakened: 0
New stores or projections: 0
Graph writes from verification: 0
```

### Handoff

Issue #457 can close because its pure language, graph propagation, pinned
material, dispatch, representative adapters, canonical persistence, and shared
operator reads now exist. Product integration work must register additional
bounded interpreters against this language; it must not introduce product
verification stores or decode result descriptors independently.

## #481 Decision Log: Secret Bootstrap Authority

### Breaking Point: Secret identity existed twice

The HTTP control adapter owned `CredentialReference`, `SecretValue`, and a
resolver protocol while Docker owned a separate string-to-string
`DockerSecretResolver`. That duplication allowed an authenticated HTTP effect
and a container environment effect to disagree about reference validity,
resolution failure, and redaction.

The duplicate values were replaced with one pure package language:

```text
SecretReference
  = secret://SecretProviderId/Path+

SecretResolution
  = SecretResolved(SecretValue)
  | SecretMissing
  | SecretDenied

SecretResolver
  = SecretProviderAuthority
  x (SecretReference -> SecretResolution)
```

`SecretValue` has no revealing representation. Releasing its text requires an
explicit call at a bounded runtime transport boundary. HTTP interprets it into
an authorization header; Docker interprets it into the environment map passed
to the Docker client. Neither interpretation produces durable data.

### Breaking Point: Path punctuation admitted traversal semantics

The first full suite showed that the segment grammar admitted `.` and `..`
because periods are otherwise valid inside provider keys. The test was kept and
construction was tightened so traversal segments are rejected independently of
the general segment character grammar.

### Breaking Point: The architecture algebra rejected an undeclared root

The AST architecture suite discovered the new `secrets` package root and
failed because it had no declared dependency rule. The package was not folded
into an existing adapter to evade the policy. It is declared as a pure root
with no package dependencies, and only these roots may import it:

```text
adapters
docker_runtime
implementations
```

Stores, topology, effects, planning, workflows, projections, API, MCP, and CLI
do not gain resolution authority.

### Capability And Laws

- all durable references are provider-qualified;
- bootstrap authority is process configuration and cannot be obtained from a
  deployment graph;
- allowed provider path prefixes are immutable typed authority;
- local development uses the same resolver protocol as external providers;
- missing and denied are distinct closed results;
- malformed references fail at construction;
- local resolver representations and errors never reveal configured values;
- no secret-value store or read-secret route was introduced.

### Evidence

```text
First full Docker/Postgres suite: 817 passed, 2 failed
  - undeclared pure package root
  - traversal segment admitted by URI grammar
Final full Docker/Postgres suite: 819 passed
Assertions weakened: 0
Skips added: 0
Durable secret-value fields added: 0
Secret read routes added: 0
```

### Handoff To #482

Use `SecretReference` as the one opaque identity when introducing the closed
environment-versus-file delivery algebra. Graph and pinned material may retain
only reference identity and delivery coordinates. They must never import a
resolver, carry `SecretValue`, or derive bootstrap authority.
