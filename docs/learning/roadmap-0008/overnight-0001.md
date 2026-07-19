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
