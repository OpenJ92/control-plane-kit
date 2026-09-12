Source: [control-plane-kit-operations/tests/test_current_graph_advancement.py](../../../../control-plane-kit-operations/tests/test_current_graph_advancement.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 1,456-line file has three pure contract tests and nineteen PostgreSQL tests
for advancing a workspace's current authored/realized graph lineage. It checks
run-ID admission, exact fence shape, successful history and replay, selected stale
authority/lineage/evidence, row-lock ordering, projection-only rotation, rollback
and competing advancements. It imports the real advancement service and records
directly, with no optional-module fallback. Its unittest.main guard runs the suite.

The pure helpers use fixed workspace/request/plan/graph/projection coordinates,
worker-a with EXECUTION_OPERATE, fence generation one and key advance-a. The result
helper builds congruent CURRENT_GRAPH_ADVANCED event/action evidence, with a
synthetic a-times-64 projection digest. It first attempts RunId on the supplied
input; after a ValueError it uses run-a only for nested event/action construction
and still passes the original invalid value into the result. That keeps nested
fixture construction from preempting the intended result run-ID admission check.

The invalid-run matrix has 45 cases: an object, bool, str subclass, empty/space
text, four forbidden leading punctuation characters, slash/internal space, each
control code zero through 31 plus 127, and length 201. Three accepted examples
cover one character, permitted punctuation after the first character, and length
200. assert_invalid_run initially catches Exception but then requires the exact
InvalidOperationCommand type, no cause/context, combined str/repr length at most
256 and absence of supplied canaries. Inputs with no canary still receive the
other assertions.

Both the command and direct/replayed result constructors receive that matrix;
valid results also expose the same run ID through descriptor(). The method named
before_unit_of_work only constructs commands: it does not instrument a unit-of-work
factory. Fence testing uses dataclasses.replace with object(), an empty
ExecutionLeaseFence subclass and a different worker's fence. Each must raise
InvalidOperationCommand without cause/context and with bounded repr. It does not
test every malformed nested authority, generation or other command field.

The database class requires CPK_OPERATIONS_TEST_DATABASE_URL, opens an autocommit
connection, installs schema, truncates cpk_workspaces CASCADE and immediately seeds
base truth in setUp. tearDown closes that connection; it does not truncate again.
reset_truth explicitly truncates and reseeds for subcases. unit_of_work creates
a fresh psycopg connection. service injects a fixed 2026-07-22T13:05:00Z clock and
a local Sequence that pops IDs without recording calls. Labels such as unused-event
are ordinary values, not allocation-failing sentinels.

seed_truth writes one workspace, two empty authored DeploymentGraph values named
current/desired, current/desired pointers, one open session, a single StartNode(api)
activity plan, an approved low-risk approval and a CLAIMED execution request. The
actual workspace store supplies identity realized projections when projection IDs
are omitted; their IDs/digests and the incremented desired revision are read back
and retained for commands. Plan rows pin both authored and realized coordinates.
The request uses a synthetic fingerprint and fixed claim dates. One unit of work
groups this seed; the earlier truncation is separately committed.

seed_succeeded_run directly stores a SUCCEEDED run with settlement time and five
events: opened, started, step started, selected step result and run succeeded. The
step result defaults to STEP_SUCCEEDED but can be replaced; both step events use
the supplied activity ID. Event evidence is only {"seed": "test"}. No effect-attempt,
intent, outcome or provider observation is seeded. The run is not transitioned by
a lifecycle service, and fixed event times are not a real runtime chronology.
The empty graph does not contain a deployed api node merely because the plan names
one. These fixtures establish durable journal test inputs, not provider truth.

The actual
[advancement owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/advancement.py)
requires EXECUTION_OPERATE, a matching CLAIMED request fence, pinned authored and
realized lineage/revision, owned graph/projection rows and complete successful
journal evidence for fresh advancement. Its worker check does not compare lease
expiry with a clock or reread the approval decision. The seeded lease expires
before the fixed advancement time; this suite therefore must not be described
as proving active-unexpired-lease or fresh-approval validation.

The owner's completeness check requires a settled SUCCEEDED run, one latest
RUN_SUCCEEDED event, absence of specified failing/compensating history, a coherent
projected saga with successful schedule and exact activity-success counts. The
[journal adapter](../../../../control-plane-kit-operations/src/control_plane_kit_operations/activity_journal.py)
maps relevant event kinds/coordinates into Core journal values without projecting
failure payloads or provider evidence. This explains why these seeded journal
rows exercise advancement without full effect-outcome records. The test's three
negative step kinds are narrower than the owner's entire completeness predicate.

The main success test advances, closes the operation session through the actual
OperationCommandService, then replays with the same command through a new service.
It checks persisted current authored/projection pointers, target digest, exact
transition evidence, event/action kinds and request/generation action fields.
claim_generation must be absent from event evidence. Replay must be marked replayed
and return equal event/action values; the full ordered run history must contain
exactly the original five kinds plus one CURRENT_GRAPH_ADVANCED. This tests replay
after session closure, not process restart or exhaustive write/allocation freedom.

Three fence subcases use a foreign worker, a larger supplied generation or a stale
supplied generation after direct stored-generation replacement. Each requires
Denied, unchanged current authored graph and no advancement event/action. A separate
case admits once, replaces generation with two and requires the old same-key replay
to be denied, preserving desired current graph, one advancement event/action and
the accepted action. These are claim-ownership checks, not lease-expiry tests.

Two first-execution lock tests hold either request-a or workspace-a in a separate
transaction. The worker connection publishes its backend PID through a queue.
wait_until_blocked_by repeatedly queries pg_blocking_pids for up to five seconds
without sleeping, requiring the specific blocker PID. While the request is held,
a separate NOWAIT probe must lock run-a; while the workspace is held, it must lock
both request-a and run-a. After blocker commit, the worker must finish within five
seconds and report graph-desired. These checks establish relative acquisition
order under those held rows, not absence of initial unlocked locator reads.

The replay lock test first holds the request and submits a changed-generation
same-key command. It must return IdempotencyConflict within five seconds despite
that lock. This directly establishes early rejection without waiting for the held
request; it does not independently probe whether that rejected path locked the
run. The exact-replay phase again observes request blocking and successfully probes
the run NOWAIT. Its ID factory explicitly fails on allocation, unlike ordinary
Sequence labels, and the released result must be replayed. Its clock is a fixed
lambda rather than a fail-on-call probe.

Those lock tests release and close their blockers in finally inside the executor
context. Worker futures/queues have bounded waits and the blocker poll has a
deadline, but the fixture sets no database statement/lock timeouts; executor exit
still waits for workers. The actual service uses initial unlocked run/request
locators, then action-key locking. Fresh execution locks session, workspace,
request and run; exact replay checks intent before locking request then run.
The tests do not measure every SQL lock or all cross-service deadlock possibilities.

Replay action-evidence testing mutates fourteen payload cases independently from
the accepted payload: workspace, plan, missing/foreign request, run, authored and
realized source/destination, digest, boolean revision, missing/changed generation
and event ID. Each requires CurrentGraphAdvancementError without cause/context,
repr length at most 256 and no canary text; the original payload is restored after
each passing case. Actor drift and event-evidence workspace drift are then checked
for rejection/no chaining/canary absence, without the same length assertion.
There is no before/after snapshot in these mutation checks.

Another replay case abandons request-a and clears its claim, creates compatible
claimed request-b and reassigns run-a. Replay must fail without cause/context and
preserve advancement_truth. Because that helper omits request and run linkage,
its equality does not mean the deliberate reassignment was undone. It observes
only current graph/projection and global advancement event/action counts in one
SQL query; it is not a complete row/payload snapshot.

A malformed persisted action replaces the payload with a JSON list containing a
canary; replay must raise the advancement error base with no chaining, combined
str/repr length at most 256 and no canary. Three persisted-event cases use malformed
evidence, malformed failure details or a structurally valid but forbidden failure.
Each requires the same bounded, unchained, canary-free error. These are explicit
redaction assertions for these error paths, not a universal claim about every
dependency exception or arbitrary secret value.

Closing the session before a fresh advance must raise Conflict matching open
session; current authored graph stays unchanged and no advancement event is found.
By contrast, the earlier exact replay after closure succeeds. Three bad step-result
subcases seed uncertain, unsupported or failed history while keeping the directly
seeded run marked succeeded. Each must raise Incomplete, leave current authored
graph unchanged and leave session actions empty. They do not independently assert
unchanged realized pointers or full event history.

The scope/worker/stale-current test runs three rejections against one success seed
and checks current authored graph afterward. The realized-lineage test separately
changes expected current projection, desired projection or desired revision; all
must conflict, with current authored/projection unchanged and no advancement event.
Changed same-key worker intent after success must conflict with idempotency and
leave one advancement event. Missing workspace/run/plan cases require NotFound,
no cause/context, bounded repr and no canary; they contain no durable snapshot
assertion. None of these groups is an exhaustive command-field matrix.

The stable-authored scenario creates graph-stable and three DELEGATION_VERIFIER
projection records keyed a, a-plus-b and b. Their graphs are separately named empty
DeploymentGraph values, not generated verifier payloads. _seed_execution writes
suffix-specific session, pinned plan, approval, claimed request, settled succeeded
run and five synthetic events through the supplied stores; it has no local commit
and hardcodes start-api in history rather than deriving events from arbitrary plans.

The first actual advancement changes projection-a to projection-a-plus-b while both
authored IDs stay graph-stable. A later transaction uses desired-projection CAS to
select projection-b at the current desired revision and seeds another run; a second
actual advancement accepts that lineage. Final readback checks current/desired
authored IDs remain graph-stable, current projection is b, authored descriptor is
unchanged and lacks delegation_verifier_projection text, and the result carries
the stored b digest. It does not perform live key rotation, verifier overlap or
runtime validation, nor assert every intermediate pointer/event/revision field.

The selected actual
[graph/workspace store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/graph_store.py)
CAS checks current and desired authored/projection coordinates and desired revision
before replacing current pointers. Desired-projection CAS keeps authored identity
and increments revision. The selected
[projection record](../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
validates canonical graph representation and computes its digest from workspace,
authored source, projection kind/key and descriptor. Test digest comparisons use
those actual stored values, not an independent known-hash oracle.

The late-action failure test commits a preexisting action ID, then reuses it for
advancement. PostgreSQL UniqueViolation must roll back the previously attempted
pointer update and advancement event: the assertions check original current
authored graph and zero advancement events. They do not compare current realized
pointer, all actions or every table, and do not inject commit failure. The actual
[unit of work](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
groups pointer CAS, event and action and rolls back exceptional exit.

The final concurrency test submits two different-key commands via executor.map
with two workers. A service Conflict becomes the string conflict; other exceptions
propagate. It requires one non-conflict return, graph-desired as current and one
advancement event. There is no start barrier, forced overlapping transaction,
timeout, same-key concurrency case or full action/projection count assertion in
this test. A sequential schedule can satisfy it. This differs from the explicit
held-row lock probes above; no failure was observed during documentation review.

Read depth: the complete 1,456-line file, all 22 tests and all local helpers were
read, recovering initially truncated output in bounded segments. The complete
918-line advancement owner and journal adapter were read. Selected actual workspace
pointer/CAS, realized projection save/get, projection record/identity/digest and
retained unit-of-work contracts were checked. No full graph-store/records/Core
saga review is claimed. Validation was documentation-only: local links, whitespace
and frozen-source comparison. No application imports, tests, database/provider
calls, credential access, source/inventory edits or publication were performed.
