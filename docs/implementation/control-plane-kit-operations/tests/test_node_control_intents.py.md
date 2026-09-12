Source: [control-plane-kit-operations/tests/test_node_control_intents.py](../../../../control-plane-kit-operations/tests/test_node_control_intents.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These 19 tests exercise
[node-control intent authorization](../src/control_plane_kit_operations/node_control_intents.py.md)
through actual PostgreSQL transactions, with constructed principals, graphs and
unsigned command values. They also test supporting secret/key locking and generated
key admission. All methods belong to one class whose setup requires the test
database, including the public-shape and static-source tests. None signs a grant,
resolves a private key, invokes a provider or relays a command. This documentation
records full source review, not executable passing evidence from an authoring run.

Setup requires CPK_OPERATIONS_TEST_DATABASE_URL, opens an autocommit connection,
installs schema and resets by truncating cpk_workspaces CASCADE. It seeds graph
truth and authority, then constructs a tracker and deterministic ID factory. Setup
failure attempts truncate/close; teardown truncates with close in finally. These
are destructive disposable-database fixtures, not read-only production probes.
The ID factory offers attempt-a followed by the two JTIs; replay variants replace
both clocks and the factory with ForbiddenCall objects that raise if invoked.

TrackingUnitOfWork wraps the actual
[PostgresUnitOfWork](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py),
counting entries, active wrappers and commit requests. Its active counter decreases
in __exit__ after the inner exit; the success assertion active=0 demonstrates that
this wrapper exited before return. A commit count alone is not an independent
database commit hook, although subsequent reads use the real committed database.
The tracker is not a general failure-injection or connection-leak test harness.

The context helper constructs an AuthenticatedPrincipal with one workspace grant
and obtains its TrustedCommandContext. There is no credential verifier or live
authentication step in the fixture. Commands request read-state or scalar apply
on router.control/routing, with fixed request/idempotency IDs and a version-4
precondition for apply. The normal clocks return epoch 100 and a fixed canonical
wall timestamp; lifetime is 60. The test file does not exhaust malformed lifetime,
clock or ID-factory behavior.

Graph setup uses real stores in a UoW to save an authored graph deliberately
without runtime truth and a distinct realized projection with gateway/router,
HTTP sockets, a control surface, edge and declared runtime. It sets that projection
as current; a second workspace supports mismatch tests. This demonstrates that
fresh authorization reads accepted realized truth, not the empty authored graph.
It does not deploy the graph or observe a process. The target declares scalar
read/apply contracts, and its gateway edge names the selected gateway as consumer.
No fixture tests an edge consumed by a different node or multiple gateways. The
actual owner's all-edges provider/socket membership predicate therefore must not
be described as tested selected-gateway connectivity.

Authority setup uses real provider/reference registration services for one provider
and two signing references with family-specific allowed intents. Provider admission
also allows the probe-signing intent used by the supporting generation test. It
directly inserts active transit/workload keys with registration IDs derived by the
actual identity function and fingerprints from synthetic public material. Active
and verify-only helper inserts follow the same pattern. No private/public key
correspondence, real key generation or full key activation workflow is exercised
by these fixture inserts.

Public-shape assertions require the Core audience helper to yield the expected
workload audience and inspect exact dataclass field names for both deferred
families and the preparation. A separate test prepares a real intent, then swaps
the grant families in the deferred constructors and requires rejection. These
checks do not exhaust subclass, all-field or direct aggregate-constructor attacks;
the root contract getter establishes name presence, not independent export identity.

The main success test requires one commit request, inactive tracker, replayed=False,
expected lineage/runtime/attempt/actor, request correspondence, grant targets,
variables, operations/codecs, IDs, issue/expiry times and workload audience. It
requires distinct family key/authorization IDs and deferred grants equal to the
attempt's grants. Two durable secret-use rows must have the correct intents/actor,
distinct deterministic correlations and authorization IDs matching the result;
there must be one attempt row. It computes expected correlations with the real
helper. Repr checks exclude secret://, endpoint and credential substrings for this
fixture, not all payload or operational-data disclosure. The row query does not
independently assert every stored authorization provenance field.

The scope matrix removes each of four required scopes for read and apply, requiring
denial before UoW entry and bounded errors without cause/context. It also substitutes
read for apply and vice versa, and tries three unrelated scopes in place of execute.
These are positive required-scope products, not a prohibition on additional scopes.
Another test successfully prepares both operations and confirms read grants have
no command codec. No live variable state is read or changed by either operation.

Graph negatives cover context/request workspace mismatch, stale authored graph
revision, missing gateway and missing variable, with no authorized secret-use rows.
Additional graph fixtures reject gateway protocol, runtime, missing edge and
surface mismatch through the service; missing socket and map-codec requests are
also rejected with zero attempt/authorization rows. Three preliminary cases fail
while constructing or describing an invalid Core graph/contract, before service
invocation. They are Core admission evidence, not three additional service rejection
paths. Realized-graph mutation deliberately rewrites its descriptor and matching
digest through SQL to exercise the current declaration checks.

The replay test first prepares an intent, clears both current workspace pointers
and revokes keys, then invokes a service whose clocks/ID factory must not run.
It requires replayed=True, the identical attempt and deferred requests, a second
commit request and still two secret-use rows. This proves those retained replay
cases rather than fresh authority: it does not instrument every graph/key read,
and the actual attempt store still joins retained relational witnesses. Selected
caller scopes remain required before lookup by owner source. Changed actor,
gateway or request idempotency key must conflict with no new authorizations and
without clock/ID refresh.

Revoking only the workload reference causes the second authorization to fail;
after the service raises, both authorization and attempt tables must be empty.
This protects rollback of the preceding transit authorization, not every database
row or every possible commit failure. assert_no_intent_rows similarly checks only
these two tables. It does not require fixture authority mutations themselves to
be rolled back, since they were committed before the attempted service call.

Eight authority mutation cases cover missing/ambiguous transit selection, purpose
substitution, reused public material or private reference, wrong reference intent,
inactive provider and cross-paired references. Each must produce bounded,
chain-free conflict with no intent rows. These are selected asymmetric cases,
not every lifecycle/status/issuer permutation for each family. A separate corrupt
public-PEM canary must not appear in the resulting error; a Core-valid actor string
outside persistence grammar must fail boundedly before UoW entry. These local
error checks do not prove every database or injected callback exception is scrubbed.

The two-worker test synchronizes service starts with a barrier, gives each worker
its own UoW factory and distinct candidate IDs, then requires one fresh result and
one replay with equal retained attempts/deferred requests. Exactly one attempt and
two authorization rows may remain. It is concurrent end-to-end preparation against
PostgreSQL, stronger than a store-only lock probe, but only one controlled race.
There is no scheduler-wide stress, process crash, external execution or at-most-once
delivery claim. The barrier does not force a specific interleaving inside execute.

The secret-row lock test calls the actual authorization helper directly inside a
UoW. Separate connections with 250ms lock timeouts must fail updates to the selected
reference and provider while the helper's transaction remains open. A deliberate
RuntimeError aborts that transaction; zero authorizations remain and the two updates
then execute successfully. It tests reference/provider lock retention and rollback
for one family, not the complete intent service through successful commit or locks
held during signing. Post-release updates do not assert changed values or rowcounts.

The supporting-source test searches key-store source for LIMIT 2 and shared/exclusive
advisory-lock strings, checks helper/store method presence and compares textual
positions of lock_purpose_for_lifecycle and secret_references.register in generated
admission source. These are static guardrails, not timing measurements or observed
SQL order. The actual
[key store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/delegation_signing_key_store.py)
and [generation service](../src/control_plane_kit_operations/delegation_key_generation.py.md)
were read to substantiate the described ordering.

The lifecycle test holds shared active selection for the transit purpose, then
requires separate register, activate, retire and revoke calls to time out. Verify-
only candidates support activation/retirement; each failed contender is rolled
back. After releasing the holder, registration alone is retried successfully and
its inserted row is deleted. Activation, retirement and revocation are not all
rerun to completion. The opposite direction then holds an exclusive purpose lock
and requires selection to time out; after release, selection succeeds with the
original transit key. This tests both lock directions for one purpose through
participating store APIs, not arbitrary direct SQL or global deadlock freedom.

The generated-key test prepares a real reference-only generation grant but manually
constructs its evidence: version ID/number, public key and replay flag. No provider
or generator is invoked. With active selection holding the shared transit-purpose
lock, timed admit_generated must fail with LockNotAvailable. After rollback there
must be zero references for the candidate path; retry after release must return
that reference and the expected key ID. Zero retained rows alone would not prove
that reference registration was never attempted before failure, since rollback
could erase it. The actual admission source and separate textual order assertion
establish purpose-lock-before-reference order. The real preparation contract uses
probe-signing custody intent even when this candidate key's purpose is transit;
this supporting test does not subsequently use the generated reference for
node-control signing or establish its suitability for that later family use.

The final AST test rejects framework/effect module names and their submodules,
product/metadata imports and selected resolution/product/metadata identifiers or
attributes. It also excludes signer/dispatcher/resolver/relay/client constructor
parameters. This is a static boundary check, not instrumentation of all imported
functions or injected callbacks. Database calls and authorization inserts are
intentional operations effects; the excluded boundary is provider/signing/relay
execution. No test here creates delivery results or structured completion history.

Read depth: full 1642-line test, all helpers/fixtures and 19 test methods, plus
retained full 637-line owner, attempt/store and UoW context. Newly completed sections
cover lifecycle lock directions, generated admission and verify-only fixtures.
Actual key lifecycle/locking and generation prepare/admit paths were refreshed,
with retained identity, graph and secret-use dependencies. These notes changed no
source/pins, ran no executable validation or database setup, accessed no credentials/
private keys and performed no provider/runtime action or publication. Documentation
adds no security surface and does not claim this suite ran or passed during review.
