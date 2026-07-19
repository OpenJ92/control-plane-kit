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

## #482 Decision Log: Graph-Pinned Secret Delivery

### Capability

Secret consumption is now a closed sum rather than a special mapping value:

```text
SecretDelivery
  = SecretEnvironmentDelivery(EnvironmentName, SecretReference)
  | SecretFileDelivery(TargetPath, SecretReference, SecretFileMode)
```

The value travels through the canonical pipeline:

```text
DockerImageImplementation.secret_deliveries
  -> MaterializedNode.secret_deliveries
  -> Node.secret_deliveries
  -> GraphDescriptor
  -> StructuralField.SECRET_DELIVERIES
  -> ReconcileNode
  -> ImplementationMaterial
       environment: SecretReferenceMaterialValue
       secret_files: SecretFileMaterial
```

The old `SecretEnvironmentReference` hidden inside implementation metadata was
removed. Literal startup environment remains implementation metadata; all
secret-backed environment and file requirements are explicit first-class node
data.

### Breaking Point: Sum constructors do not share Python ordering

The first focused run attempted `sorted(secret_deliveries)`. Both constructors
were individually ordered dataclasses, but Python correctly refused to compare
`SecretEnvironmentDelivery` with `SecretFileDelivery`.

The domain was not flattened into a tagged mapping. A pure interpreter now
owns canonical order:

```python
def secret_delivery_sort_key(
    value: SecretDelivery,
) -> tuple[str, str, str, str]:
    match value:
        case SecretEnvironmentDelivery(...):
            return ("environment", ...)
        case SecretFileDelivery(...):
            return ("file", ...)
```

Graph and diff descriptors use this interpreter, preserving constructor
identity while making encoding deterministic.

### Breaking Point: Test assumed a nonexistent codec convenience method

The second focused run found one test calling `GraphDescriptorCodec.dumps`,
which does not exist. The application was not changed. The test now renders the
real graph descriptor with deterministic `json.dumps` and retains the exact
assertions that reference identity is present while resolved content is absent.

### Laws

- secret file targets are normalized absolute paths under `/run/secrets`;
- file mode is the closed owner-read-only value `0400`;
- literal and secret environment bindings cannot claim the same name;
- delivery constructors and target identities are unique per node;
- unknown constructors and extra descriptor fields fail closed;
- delivery changes produce explicit graph diff and reconcile work;
- the exact desired graph pins delivery material for execution;
- graph, planning, effects, stores, and projections import no resolver or
  `SecretValue`;
- public configuration artifacts remain unable to target `/run/secrets`;
- no host path, resolved value, digest, or size enters durable graph data.

### Evidence

```text
First focused Docker suite: 68 passed, 1 architecture failure, 3 errors
  - undeclared packaged-server dependency on pure secret algebra
  - cross-constructor dataclass ordering was undefined
Second focused Docker suite: 71 passed, 1 test error
  - nonexistent codec convenience method in new test
Corrected focused Docker proof: 4 passed
Complete Docker/Postgres suite: 826 passed
Assertions weakened: 0
Skips added: 0
Secret resolvers imported by durable layers: 0
```

### Handoff To #483

`SecretFileMaterial` is now exact graph-pinned execution material. Resolve its
`reference_id` only inside the Docker adapter immediately before dispatch.
Create owned ephemeral protected material without placing value-derived data in
names, ownership fingerprints, labels, evidence, exceptions, or command
descriptors. Reuse the configuration-volume ownership protocol where safe, but
keep secret storage and public configuration storage distinct.

## #483 Decision Log: Owned Docker Secret-File Realization

### Capability

The Docker adapter now interprets exact graph-pinned `SecretFileMaterial` into
runtime-only protected files:

```text
SecretFileMaterial(reference, target, 0400)
  -> resolve through process-bootstrap SecretResolver
  -> prove or create an owned ephemeral Docker volume
  -> write through stdin using a networkless hardened helper
  -> verify file existence and mode
  -> mount only the content subpath read-only
  -> start the graph-owned container
```

The graph, plan, effect descriptor, ownership labels, journal, observations,
and errors contain reference identity or non-secret fingerprints only. Secret
bytes exist only in `SecretValue`, the helper process stdin, the owned runtime
volume, and the workload file.

```python
@dataclass(frozen=True)
class DockerSecretMount:
    material: SecretFileMaterial
    volume_name: str
    ownership: DockerOwnership

    def docker_argument(self) -> str:
        return (
            f"type=volume,source={self.volume_name},"
            f"target={self.material.target_path},"
            "volume-subpath=content,readonly"
        )
```

### Breaking Point: Secret-shaped durable evidence failed closed

The first focused regression run failed every ordinary container start. The
new success evidence used a `secret_files` key whose values were only hashed
material identities and dispositions. `BoundedEvidence` correctly rejects the
key itself as secret-shaped.

The evidence guard was not weakened. Secret delivery dispositions were removed
from durable effect evidence because pinned graph truth already owns exact
intent. Success retains ordinary node and ownership evidence; missing and
denied resolution publish only closed redacted failure codes.

### Breaking Point: Immutable reference versus mutable provider bytes

The runtime can verify volume ownership, file existence, and mode without
reading secret bytes back into the control plane. It cannot safely infer that a
provider changed the value behind an unchanged reference. Treating that as
automatic rotation would make desired state depend on hidden mutable provider
state and could silently rewrite a running workload.

The current law is explicit:

```text
same SecretReference + replay
  = reuse exact owned material

new SecretReference
  = graph diff + new ownership fingerprint + explicit reconcile/restart
```

The hardening corpus proves both sides. A changed provider value behind the same
reference does not rewrite the volume. A new reference changes both container
and secret-volume ownership fingerprints.

### Breaking Point: Live fixture command carried value-derived data

The first live proof embedded a digest of its non-sensitive fixture value in
the workload command to validate exact content. Review rejected that precedent.
The final workload command verifies only that protected content arrived; exact
value transport is covered by the focused adapter test, which proves the value
is stdin input and absent from Docker argv.

### Failure And Cleanup Laws

- all environment and file references resolve before any volume mutation;
- missing and denied references are distinct terminal failures;
- partial or unprovable materialization is uncertain and retains the owned
  volume for explicit recovery;
- a definitely failed container start cleans owned secret volumes only after
  proving the container is absent;
- teardown preflights every secret volume before removing the container;
- an ownership conflict preserves both container and volume;
- teardown removes only proven-owned ephemeral secret material;
- no host path enters graph or effect material;
- no transaction or lock spans resolution, filesystem, or Docker effects.

### Evidence

```text
Focused Docker regression suite after protocol propagation: 34 passed
First secret-focused suite: 7 passed
Complete Docker/Postgres suite before final review additions: 833 passed
Final secret-focused hardening suite: 9 passed
Final complete Docker/Postgres suite: 835 passed
Live Docker proof:
  - denied bootstrap created no container or volume
  - delivered file mode was 0400
  - workload mount rejected writes
  - identical replay performed no rewrite
  - cleanup left no owned container, volume, or network
Assertions weakened: 0
Skips added: 0
Secret bytes in Docker argv, ownership, evidence, errors, or logs: 0
```

### Handoff To #458 And #436

Close #458 only after confirming #481 through #483 form one authority,
descriptor, and realization path. Product integrations under #436 may consume
secret-backed credentials through the same reference algebra. They must not
invent product-specific secret stores, value fields, host mounts, or read-secret
routes. Credential rotation remains an explicit graph edit to a new immutable
reference.

## #487 Decision Log: Credentialed Postgres Product Proof

### Capability

The generic secret-file language now supports products that require the mounted
file path to be named by a non-secret environment variable:

```python
SecretFileDelivery(
    "/run/secrets/postgres-password",
    SecretReference("secret://live/postgres/password"),
    path_binding=SecretFilePathBinding("POSTGRES_PASSWORD_FILE"),
)
```

Materialization interprets that typed relation into the literal path binding
`POSTGRES_PASSWORD_FILE=/run/secrets/postgres-password`. Secret bytes remain
available only from the runtime resolver and mounted file. The existing guard
against plaintext values in secret-shaped environment names was not weakened.

```text
SecretFilePathBinding x SecretFileDelivery
  -> pinned SecretFileMaterial
  -> literal path EnvironmentBindingMaterial
  -> Docker secret volume and read-only file mount
```

### Breaking Point: Product bootstrap needs a file-path environment slot

The official Postgres image does not infer its password file. It consumes
`POSTGRES_PASSWORD_FILE`, whose value is a path rather than a credential. A raw
literal would be rejected correctly by the package's secret-shaped environment
guard. The fix was a closed typed relationship on `SecretFileDelivery`, not an
exception to the guard and not a product-specific Docker branch.

The path binding participates in descriptor encoding, sorting, implementation
overlap validation, pinned effect material, and Docker ownership fingerprints.
Changing the binding is therefore a graph-visible change.

### Breaking Point: Descriptor hashing was not structural equality

`Node` previously detected duplicate deliveries by converting one shallow
descriptor level into tuples. The nested path-binding descriptor exposed that
as an unhashable implementation accident. Frozen secret-delivery values already
have structural equality and hashing, so uniqueness now uses the algebraic
values directly:

```python
if len(set(self.secret_deliveries)) != len(self.secret_deliveries):
    raise ValueError("node secret deliveries must be unique")
```

### Breaking Point: The live runner existed before its graph network

The initial product proof sent `WaitForHealthy` directly to the Docker lifecycle
interpreter and correctly received `EffectUnsupported`. It now composes the
existing `CapabilityInterpreterRegistry`: Docker owns lifecycle capabilities,
while `ProbeEffectInterpreter` owns health and performs both process inspection
and real TCP reachability.

The runner container itself starts before the desired graph network exists. The
harness therefore performs one canonical `StartRuntime` bootstrap effect, exits,
then launches a fresh runner attached to the owned network. That runner executes
the complete empty-to-desired plan, including the idempotent runtime activity.
Teardown executes the complete desired-to-empty plan from an unattached runner.

### Breaking Point: Adapter prefix and graph DNS identity diverged

The graph advertises `docker-postgres`, while a nonempty adapter project prefix
would create a differently named container. Canonical live examples use their
unique graph-owned network for isolation and leave the adapter prefix empty, so
the runtime process identity exactly matches the pinned endpoint. The Postgres
proof follows that precedent.

### Breaking Point: Ownership field landed in the adjacent product

The first complete suite found eight configuration-artifact errors. The
secret-file `path_binding` fingerprint had been added to the adjacent
`configuration_artifacts` comprehension instead of `secret_files`, causing
artifact-only nodes to read a field outside their algebra.

The tests were unchanged. The field moved to the secret-file ownership product,
where it now affects both container ownership and individual secret-volume
ownership. All nine configuration ownership tests and all twenty-two secret
contract tests then passed together. This preserved the separation:

```text
ConfigurationArtifact ownership
  = artifact identity x content/source digest x target x mode

SecretFileMaterial ownership
  = reference x target x mode x optional path binding
```

### Live Evidence

```text
denied provider authority
  -> docker.secret-denied
  -> no container or volume

canonical empty graph -> desired Postgres graph
  -> compile, validate, diff, plan, materialize
  -> owned network and container
  -> bounded process + TCP readiness
  -> password-file bootstrap
  -> authenticated psycopg SELECT 1

identical replay
  -> convergent owned resources

desired Postgres graph -> empty graph
  -> owned container, secret volume, and network removed

mounted mode: 0400
write attempt: rejected
plaintext credential in argv/env/descriptors/evidence: none
focused secret/topology/Docker tests: 22 passed
architecture-policy tests: 43 passed
configuration + secret ownership regression tests: 31 passed
complete Docker/Postgres suite: 835 passed
assertions weakened: 0
skips added: 0
```

## #404 Decision Log: Bounded Request Observer

### Capability

The package now owns a terminal HTTP observer suitable for the copied branch of
an `HTTP_MULTIPLEXER` topology:

```text
RequestObserverBlock
  = PackageServerSpec(REQUEST_OBSERVER)
  x DockerImageImplementation
  x ProviderSocket(HTTP)
  x SecretEnvironmentDelivery(control token)
```

The data route accepts bounded copied traffic and retains only:

```python
@dataclass(frozen=True)
class RequestObservation:
    count: int
    latest_correlation_id: str | None
```

Bodies, paths, query values, authorization headers, cookies, arbitrary headers,
and caller-provided correlation identities are discarded. The package generates
the retained identity from the monotonic in-process observation count.

Operational evidence is available through the authenticated closed route set:

```text
CapabilityName.METRICS_READABLE
  -> ControlRouteSetName.METRICS
    -> GET /__deploy/metrics
```

`/health` remains an unauthenticated readiness probe. The metrics route accepts
the opaque control token only at runtime; the graph retains its secret reference
and never its value.

### Objects, Morphisms, And Laws

```text
HttpRequest
  -> bounded request-observer interpreter
    -> HttpResponse(202, generated correlation identity)
    -> RequestObservation(count, latest identity)

PackageServerProduct.REQUEST_OBSERVER
  -> package-server capability contract
    -> executable health probe + authenticated metrics route
```

- request bodies are bounded to 1 through 1,048,576 configured bytes;
- an oversized request returns `413` and does not change observation state;
- unauthorized metrics access returns `401`;
- advertised capabilities exactly equal the executable capability evidence;
- the generated command crosses the existing strict Jinja2/Python syntax
  boundary;
- observer state is explicitly ephemeral process state, not graph or durable
  operational truth;
- no request-controlled value enters the retained observation descriptor.

### Breaking Point: Expected HTTP errors were not closed

The first focused run passed but emitted `ResourceWarning` for the expected
`401` and `413` responses. The tests now close both `HTTPError` response objects
while preserving the exact status assertions. No application behavior changed.

### Breaking Point: Readiness helper assumed application health

The first multiplexer integration fixture used the observer `/health` helper for
a temporary Python file server. That server has no `/health` contract, so the
fixture incorrectly treated an application-level `404` as failure to start.

The correction uses a TCP listening check for the opaque primary and retains an
HTTP health check for the package-owned observer. This explicitly preserves the
distinction between transport reachability and application readiness.

### Breaking Point: Primary response was decoded as observer JSON

The multiplexer successfully returned the primary file server response, but the
test reused a JSON-only control-route helper and failed while decoding HTML.
The primary assertion now reads the response as opaque bytes and checks only its
HTTP status. Metrics remain decoded through the JSON control-route helper.

### Review Finding: Generic probe assertion weakened exact paths

The first catalogue update changed the existing teaching-product assertion from
an exact `"/"` probe to merely non-null because the observer uses `/health`.
Test-integrity review rejected that weakening. The final test contains a closed
product-to-path table, preserves every prior exact `"/"` assertion, requires the
observer's exact `/health` path, and fails when a new teaching product lacks an
explicit expected path.

### Evidence

```text
focused Docker suite: 29 passed
live generated multiplexer path:
  client -> multiplexer -> opaque primary response
                       `-> request observer -> authenticated count == 1
complete Docker/Postgres suite: 840 passed
assertions weakened: 0
skips added: 0
request-controlled values retained: 0
```

### Handoff To #413

Use this observer as the terminal copied-traffic target while hardening the
multiplexer itself. Keep observer failure off the primary response path, bound
both copied requests and observer response handling, and preserve graph position
as the distinction between terminal observation and the later inline logger in
`#415`.

## #413 Decision Log: Typed HTTP Circuit Breaker

### Capability

The package catalogue now contains a Docker-backed teaching circuit breaker:

```text
HttpCircuitBreakerBlock
  = ProxyBlock
  x RequirementSocket(target, HTTP)
  x ProviderSocket(internal, HTTP)
  x CircuitBreakerPolicy
  x opaque control-token reference
```

The product-local closed language is:

```text
CircuitBreakerState
  = Closed | Open | HalfOpen

CircuitBreakerMethodPolicy
  = SafeOnly | AllMethods

CircuitBreakerPolicy
  = failure threshold
  x recovery timeout
  x half-open trial budget
  x upstream timeout
  x request byte bound
  x response byte bound
  x method policy
```

Policy values are rendered into validated Jinja2 source and therefore remain
part of immutable Docker implementation identity. The target is supplied by the
typed socket connection through `CIRCUIT_TARGET_URL`; it is not free metadata.

The closed operational surface is:

```text
CircuitStateReadable -> GET  /__deploy/circuit
CircuitResettable    -> POST /__deploy/circuit/reset
```

Both routes require the opaque control token. The product deliberately omits a
force-open mutation: authenticated reset is sufficient for this scaffold and
does not enlarge operator authority without a demonstrated workflow.

### State Laws

- closed requests contact the target and count only failures;
- reaching the typed threshold transitions to open;
- open fails fast without contacting the target;
- recovery timeout transitions to half-open;
- half-open reserves no more than the typed trial budget;
- a successful trial closes and a failed trial reopens;
- `SAFE_ONLY` rejects unsafe methods before reading or forwarding bodies;
- `ALL_METHODS` is a typed explicit opt-in;
- request, response, timeout, threshold, and trial values are bounded;
- locks protect state transitions but never span an upstream HTTP effect;
- circuit observations retain state, counters, and package-generated transition
  identity only;
- health remains distinct from open/closed circuit state;
- reset changes ephemeral process state and never graph truth.

### Breaking Point: Policy default preceded its validator

The first focused import failed because function defaults construct
`CircuitBreakerPolicy()` while the module loads, but the pure `_bounded`
validator was defined later. The validator moved above the policy definition.
No policy value, bound, or test changed.

### Breaking Point: Unsafe-method and body-bound assertions overlapped

The first live fixture expected an oversized `POST` to return `413`. The
`SAFE_ONLY` policy correctly rejects `POST` as `405` before reading its body.
The test now proves those independent laws independently:

```text
unsafe POST -> 405 -> zero target calls
oversized permitted GET -> 413 -> zero target calls
```

This preserves fail-early handling of unsafe methods rather than reordering the
application merely to satisfy the fixture.

### Security And Data Findings

- unauthorized circuit reads and resets return `401`;
- unauthorized reset leaves the open state unchanged;
- target addresses and control credentials are absent from state evidence;
- redirects are not followed;
- upstream time and response bytes are bounded;
- server logs are suppressed and bounded errors do not echo target failures;
- the control token remains an opaque `SecretReference` in graph data;
- there is no store, transaction, durable cursor, or second observation model.

### Evidence

```text
focused Docker suite: 55 passed
live generated state path:
  closed -> two target failures -> open
  open -> fail fast with unchanged target call count
  timeout -> half-open -> successful target call -> closed
  open -> unauthorized reset rejected -> authorized reset -> closed
complete Docker/Postgres suite: 845 passed
assertions weakened: 0
skips added: 0
```

### Handoff To #414

The retry proxy must remain a separate network block and policy language. It may
retry only operations allowed by a closed method/idempotency policy, must bound
attempts, delay, total deadline, request bytes, and response bytes, and must not
reuse circuit state or silently compose itself into this product.

## #414 Decision Log: Bounded HTTP Retry

### Capability

The package catalogue now contains a Docker-backed teaching retry proxy:

```text
HttpRetryBlock
  = ProxyBlock
  x RequirementSocket(target, HTTP)
  x ProviderSocket(internal, HTTP)
  x RetryPolicy
  x opaque control-token reference
```

Its closed policy language is:

```text
RetryMethodPolicy = SafeOnly | IdempotencyKey
RetryStatusPolicy = GatewayErrors | ServerErrors

RetryPolicy
  = finite attempts
  x per-attempt timeout
  x total deadline
  x bounded fixed backoff
  x request/response byte bounds
  x method policy
  x status policy
```

Safe methods may use the configured retry budget. Unsafe methods receive one
attempt unless the graph selected `IdempotencyKey` and the request supplies a
bounded key. The key is forwarded for application idempotency but never enters
the retry observation.

### Objects, Morphisms, And Laws

```text
HttpRequest
  -> method/idempotency interpretation
    -> finite sequence of bounded target attempts
      -> HttpResponse x RetryObservation

graph position:
  retry -> circuit -> target
  circuit -> retry -> target
```

Composition order has executable meaning. With retry outside a one-failure
circuit, the first target failure opens the circuit and the next retry receives
the circuit's fail-fast response. With retry inside the circuit, transient
failure may recover before the outer circuit observes the final response.

- attempts are bounded from 1 through 10;
- each attempt and the whole request have independent deadlines;
- fixed backoff is bounded and cannot exceed remaining total time;
- request and response bodies are bounded;
- redirects are not followed;
- gateway-only and all-server-error retry sets are closed values;
- target addresses, bodies, credentials, cookies, and idempotency keys do not
  enter observations;
- retries change ephemeral process evidence, never graph truth;
- the authenticated metrics route exposes bounded counters and a
  package-generated request identity only.

`Retry-After` is deliberately unsupported in this scaffold. Consequently no
untrusted response header can expand the typed deadline or backoff policy.

### Breaking Point: Retry decisions were counted before dispatch

The first generated server incremented `retry_count` when a response was
classified retryable. If the total deadline expired before the next attempt,
the observation claimed a retry that never occurred. The counter now advances
at the start of each actual attempt after the first. The live test compares the
counter delta to the target's actual call delta.

### Breaking Point: Timeout disconnect was silently discarded

The live target fixture expects clients to disconnect when an attempt timeout
expires. Its first implementation swallowed `BrokenPipeError` with a pass-only
handler. The architecture test-integrity policy rejected that hidden evidence.
The fixture now records each disconnected write under its lock. Retry behavior
and assertions were not weakened.

### Harness Finding

An ad hoc focused command used the package Docker stage, which intentionally
does not copy `tests/`, and therefore produced import errors without executing
tests. Validation returned to the canonical `./test.sh` test stage. This was a
harness invocation error, not an application or test failure.

### Evidence

```text
focused retry/circuit/catalogue/template suite: 24 passed
live generated retry path:
  transient 503 -> 200
  deterministic three-attempt exhaustion
  unsafe POST -> one attempt
  keyed POST -> bounded retries without retained key
  oversized request -> 413 and zero target calls
  timed-out attempts -> bounded calls and exact retry evidence
complete Docker/Postgres suite: 851 passed
assertions weakened: 0
skips added: 0
```

### Residual Risk

This remains a teaching proxy with process-local counters and fixed backoff. It
does not implement distributed retry budgets, jitter, `Retry-After`, streaming,
or durable observations. Those omissions are explicit and do not enlarge the
advertised capability contract.

### Handoff To #415

The inline logger should reuse the same one-target proxy topology without
sharing retry policy or counters. Inbound versus outbound logging is graph
position. Keep forwarding independent from bounded evidence retention, use a
closed path-redaction policy, and expose evidence only through authenticated
control routes.

## #415 Decision Log: Inline HTTP Traffic Logger

### Capability

The package catalogue now contains one traffic logger whose direction is graph
position rather than product identity:

```text
HttpTrafficLoggerBlock
  = ProxyBlock
  x RequirementSocket(target, HTTP)
  x ProviderSocket(internal, HTTP)
  x TrafficEvidencePolicy
  x opaque control-token reference

client -> logger -> application    -- inbound
application -> logger -> service   -- outbound
```

The evidence language is closed:

```text
TrafficPathPolicy = Redacted | StableHash
TrafficMethod = Get | Head | Options | Post | Put | Patch | Delete | Other
TrafficStatusClass
  = Informational | Success | Redirection | ClientError | ServerError

TrafficEvidence
  = sequence
  x generated correlation identity
  x closed method
  x closed status class
  x bounded duration
  x bounded request/response byte counts
  x optional path digest
```

Evidence lives in a fixed-capacity ring with an explicit eviction count.
Operator reads are authenticated and paginated. There is deliberately no reset
mutation and no durable log store in this teaching implementation.

### Data-Minimization Laws

- bodies, query strings, authorization, cookies, arbitrary headers, target
  URLs, credentials, and caller correlation values never enter evidence;
- raw paths are never retained;
- the default path policy is complete redaction;
- stable hashing is an explicit graph-time policy and produces only a fixed
  SHA-256 digest of the path without its query;
- unknown or oversized methods collapse to the closed `Other` evidence value;
- capacity, page size, request/response bytes, and upstream time are bounded;
- forwarding returns the target response independently of evidence retrieval;
- health, successful forwarding, and evidence availability remain distinct.

### Breaking Point: Auth preceded pagination validation

The first full run expected malformed pagination to return `400` but sent no
control credential. The server correctly returned `401` before inspecting the
query. The negative test now authenticates and preserves the exact `400`
assertion. No authorization ordering or application behavior changed.

### Review Finding: Structured evidence is not line logs

The first catalogue pass advertised `LOG_READABLE` and served paginated traffic
records from `/__deploy/logs`. Review found that the canonical log protocol is
an exact `block_id + lines` descriptor interpreted by the control HTTP client.
Treating structured evidence as that capability would either lie about the
route contract or stringify and erase the traffic algebra.

The correction introduced a distinct closed capability and route set:

```text
TrafficEvidenceReadable
  -> ControlRouteSetName.TRAFFIC_EVIDENCE
    -> GET /__deploy/traffic-evidence
```

Ordinary line logs remain unchanged. The package product now advertises exactly
health plus structured traffic evidence.

### Harness Findings

Two focused invocations initially selected the wrong import root. They executed
zero relevant tests and were not counted as evidence. The corrected Docker
invocations use `/app/tests` for flat test modules and `/app` for modules that
import the `tests.architecture` namespace. The canonical full harness remains
authoritative.

### Evidence

```text
focused logger/product suite: 30 passed
focused architecture/capability suite: 26 passed
live generated logger path:
  request method/path/query/headers/body -> target unchanged
  target status/body -> caller unchanged
  three requests into capacity two -> oldest evicted, count == 1
  unauthenticated evidence read -> 401
  authenticated bounded page -> structured redacted evidence
  oversized request -> 413 and zero target calls
complete Docker/Postgres suite: 857 passed
assertions weakened: 0
skips added: 0
```

### Residual Risk

The evidence ring is process-local and disappears on restart. Stable path
digests can reveal membership for guessable paths and are therefore opt-in, not
the default. The scaffold does not stream bodies, preserve trailers, implement
HTTP/2, or provide durable distributed telemetry; none is advertised.

### Handoff To #420

The timeout/deadline proxy must remain a separate product even though this
logger already bounds its own target attempt. `#420` owns explicit timeout
outcomes and cancellation semantics; it must not absorb logging or traffic
evidence. Composition order should remain visible in graph topology.

## #420 Decision Log: HTTP Timeout And Deadline Proxy

### Capability

The package catalogue now contains a one-attempt timeout proxy:

```text
HttpTimeoutBlock
  = ProxyBlock
  x RequirementSocket(target, HTTP)
  x ProviderSocket(internal, HTTP)
  x HttpTimeoutPolicy
  x opaque control-token reference

HttpTimeoutPolicy
  = upstream attempt timeout
  x total request deadline
  x request byte bound
  x response byte bound
```

Its closed outcome language distinguishes:

```text
Forwarded
RequestRejected
ResponseRejected
UpstreamTimeout
UpstreamDisconnected
TotalDeadlineExceeded
ClientDisconnected
```

The generated server makes exactly one target attempt. It never imports retry
policy, traffic logging, circuit state, or durable workflow state. Authenticated
metrics retain counters, the latest closed outcome, bounded duration, and a
package-generated request identity only.

### Laws

- upstream timeout and total request deadline are independent finite values;
- timeout never implies retry;
- redirect following is disabled;
- request and response bytes are bounded;
- client disconnect is recorded only when reading from or delivering to the
  caller fails;
- upstream timeout and disconnect remain distinct from client disconnect;
- target addresses, headers, credentials, bodies, and error text do not enter
  evidence;
- no lock spans the upstream HTTP effect;
- health, forwarding success, and timeout observations remain distinct.

### Breaking Point: Public timeout policy name collision

The first full suite reported ten probe errors. They had one cause: the new
server exported a generic `TimeoutPolicy` from the package root and shadowed the
existing execution/probe algebra's `TimeoutPolicy`. Existing callers therefore
constructed the new HTTP product policy where effects required the canonical
effect timeout.

The package is unreleased, so no alias or compatibility model was retained. The
server value is now explicitly product-owned:

```text
effects.TimeoutPolicy     -- existing effect/probe retry timing
servers.HttpTimeoutPolicy -- HTTP timeout product configuration
```

Focused tests import both public surfaces and prove the established probe
language remains intact.

### Evidence

```text
initial focused timeout/architecture suite: 23 passed
post-collision timeout/probe/architecture suite: 39 passed
live generated proxy path:
  success -> forwarded
  oversized request -> 413 and zero target calls
  slow target -> 504 upstream-timeout
  raw caller socket close -> client-disconnected
  unauthenticated metrics -> 401
complete Docker/Postgres suite: 861 passed
assertions weakened: 0
skips added: 0
```

### Residual Risk

The pure in-memory interpreter cannot preempt an arbitrary synchronous Python
callback; it classifies elapsed time after the callback returns. The generated
HTTP implementation enforces its network timeout during the effect. The
teaching server remains HTTP/1 and non-streaming and does not implement
cooperative cancellation propagation to arbitrary upstream applications.

### Handoff To #421

The next HTTP product must consume this timeout block only through graph
composition. Do not copy its timeout evidence into another product or infer
retry behavior from a timeout result. Preserve product-qualified public names
when a generic execution-algebra name already exists.

## #421 Decision Log: Ticketed HTTP Bulkhead

### Capability

```text
HttpBulkheadBlock
  = ProxyBlock
  x RequirementSocket(target, HTTP)
  x ProviderSocket(internal, HTTP)
  x HttpBulkheadPolicy

HttpBulkheadPolicy
  = maximum in-flight
  x bounded queue capacity
  x queue timeout
  x request/response byte bounds
```

The admission state uses monotonic tickets rather than an unstructured
semaphore wait. Cancelled or timed-out tickets are retired explicitly so later
callers cannot remain blocked behind an abandoned position.

### Laws

- no more than the configured number of target effects execute concurrently;
- queue overflow rejects immediately and queue expiry is a distinct outcome;
- queued admission follows ticket order;
- permits release in `finally` after success, target failure, and response
  rejection;
- permit count cannot become negative;
- the condition lock never spans the target effect;
- no request value, target address, or secret enters observations;
- process state is authenticated and observable but never graph truth;
- bulkhead admission does not imply rate limiting, timeout recovery, or retry.

### Evidence

```text
focused bulkhead/catalogue/architecture suite: 23 passed
live generated concurrency proof:
  first request -> one in flight
  second request -> one ticket waiting
  third request -> immediate 503 rejection
  release -> first and second both complete
  final in_flight == 0 and waiting == 0
  unauthenticated metrics -> 401
complete Docker/Postgres suite: 865 passed
assertions weakened: 0
skips added: 0
```

### Residual Risk

Client disconnect while waiting cannot be observed reliably by the stdlib HTTP
server before response delivery. Such a caller may retain its ticket until
admission or queue timeout; the queue remains finite and permits still recover.
The implementation is process-local and is not a distributed concurrency
budget.

### Handoff To #422

Fault injection is test-only behavior and must not reuse bulkhead outcomes.
Its activation authority, product maturity, and policy must make accidental
production use structurally visible.
