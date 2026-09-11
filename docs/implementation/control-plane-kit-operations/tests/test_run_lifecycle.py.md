Source: [control-plane-kit-operations/tests/test_run_lifecycle.py](../../../../control-plane-kit-operations/tests/test_run_lifecycle.py).
Maintain this document alongside its source file. Recheck database time, claim
generation, lock order, replay reconstruction, rollback and settlement semantics
when the tests or their lifecycle, record and store owners change.

The 1,572-line file contains four database-free record-law tests and 33 PostgreSQL
composition tests, including all local helpers. The composition tests use the
real RunLifecycleCommandService and PostgreSQL stores, with deterministic identity
factories and injected transition clocks. They have no runtime interpreter or
provider effect dependency. No tests, application imports, database connections,
Docker actions or provider actions were executed while authoring this companion.

## Fixture truth and transaction ownership

RunLifecycleTests requires CPK_OPERATIONS_TEST_DATABASE_URL. Setup opens an
autocommit connection, installs the schema, truncates cpk_workspaces CASCADE and
seeds the workspace, graphs, session, plan, approval records and queued execution
request. Teardown closes the connection; the next setup performs the truncation.
This is destructive isolated-test setup, not a production initialization recipe.

Most seed facts are direct SQL. The imported
[graph-lineage fixture](graph_lineage_fixture.py.md) stores empty authored graphs
and their identity projections in a separate committed unit of work. The plan
has those projection IDs and an empty JSON payload; there is no real application
activity schedule. Synthetic approval rows make the request admissible to the
tested lifecycle path without executing the ordinary preparation/approval flow.
Consequently, successful lifecycle completion here is not proof that a deployment
plan's activities ran or that its providers report success.

Sequence pops supplied IDs and has no fallback; accidental extra allocation
exhausts the list. service supplies that factory and a fixed clock, normally
2026-07-22T13:00:00Z. Claim tests deliberately also use a 1900 clock to show that
claim time is database-owned. authority defaults to worker-a with EXECUTION_OPERATE;
fence defaults to worker-a/generation 1. Both claim-command helpers request a
600-second lease by default; target_claim_command additionally checks that the
duration type is exposed by the lifecycle module.

Each service call constructs a real
[PostgresUnitOfWork](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py).
Its commit method requests a commit; successful context exit performs the physical
commit, and exceptional exit rolls back and closes the connection. Claim updates,
run/event inserts and operation-action inserts therefore share one command
transaction. Rollback tests fail actual SQL writes inside that boundary. They do
not simulate a lost connection during physical commit or ambiguous commit success.

_insert_run bypasses application record construction with direct SQL and supplies
start/settlement timestamps only for its CANCELLED fixture case. _seed_second_request
adds a cancelled request in the same workspace/session/plan for collision and
foreign-evidence cases. These helpers manufacture test state; they are not retry,
cancellation or admission workflows. _run_record constructs a typed claimed run
for store round trips. _count permits only the three named run/event/action tables;
its counts are not a snapshot of all Operations truth.

## Record laws and the canonical event contract

The timing/scope method rejects a claimed run with started_at, a running run
without started_at, a succeeded run without settled_at, a run event carrying an
activity ID and a step event lacking one. Placeholder strings such as created
and started demonstrate presence constraints; this method does not prove timestamp
parsing or chronological ordering.

The bounded-evidence method rejects an api_token-shaped key, a tuple value and
positive infinity. The actual [record constructors](../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
also enforce canonical JSON and size/depth/item/text limits. These three examples
do not exhaust those bounds or prove that arbitrary sensitive string content is
recognized and redacted.

For both forward and compensation uncertainty-abandonment event kinds, a record
with an activity ID and recovery-decision reference maps to the expected journal
kind. Adding FailureEvidence or removing the activity ID must fail. This protects
abandonment as activity-scoped nonfailure event data, without asserting provider
success or exercising an authorized abandonment workflow. The actual
[journal adapter](../../../../control-plane-kit-operations/src/control_plane_kit_operations/activity_journal.py)
maps supported durable event kinds into Core journal values; this method inspects
the two resulting kind strings, not every translated coordinate.

The failure-evidence matrix iterates the current
[canonical lifecycle event contracts](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/lifecycle.py).
Permitted kinds preserve the supplied failure by identity; other kinds must raise
the exact nonfailure-event error with no cause/context or selected failure canary.
RECOVERY_DECISION_RECORDED is explicitly excluded because it has a separate typed
recovery-evidence contract. Operations derives its permitted-failure set from the
same canonical contract, so this is a cross-owner conformance test, not an
independent fixed oracle for the canonical set's design or a database-constraint
test for all event kinds.

## Database-timed claim creation and observation

The claim-time method brackets the service call with database clock_timestamp
reads. Persisted claimed_at must fall within that interval, lease expiry must be
exactly 600 seconds later, and persisted/returned generation must be 1. Run
creation, event occurrence and operation-action creation must all reuse the
returned claim timestamp despite the injected 1900 clock. The result descriptor
reports claim_generation, and the result dataclass must not duplicate it in a
fence_generation field.

The actual [execution store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
locks the request row, decodes its current state and only claims a queued request.
It samples clock_timestamp in the subsequent update, sets generation 1 and uses
the same sample for claimed_at and lease_expires_at. The duration value and store
both require an exact integer in 1..3600; this file's successful values and changed
duration examples are not a complete duration-validation matrix.

The locked-observation test first sees an unexpired lease, then directly moves
expiry to one microsecond before database time and sees expired=True with the
same generation. Actual observation locks the request before sampling database
time and compares expiry <= observed_at. The test covers either side using a
past expiry, not exact equality at a frozen database instant.

Two thread tests hold the request row lock on a separate connection. They obtain
the worker backend PID through a queue and poll pg_blocking_pids until the expected
blocker is visible. After sampling released_at just before the blocker commits,
the returned claim or observation timestamp must be at least that sample. This
shows time is sampled after waiting on that request lock. It does not measure the
exact commit/release instant or impose a general distributed-clock guarantee.

Queue and future waits and blocking polls use five-second bounds. The poll loops
have no sleep; individual SQL calls and ThreadPoolExecutor shutdown are not covered
by a single global test deadline. Finally blocks roll back and close the blocker
connections. These are controlled PostgreSQL interleavings, not load or fairness
tests.

## Claim replay, competing workers and identity allocation

The ordinary claim case returns a claimed request and run, worker-a, admitted
request linkage, RUN_OPENED ordinal 1 and a CLAIM_RUN action with its request ID.
Replay returns the existing run; changing duration under the same idempotency key
conflicts, missing operate scope is denied and another worker with a new key cannot
claim an already claimed request.

The target replay case requires full result equality apart from replayed=True,
zero calls to a failing identity factory and generation still 1. Changing duration
to 601 conflicts. A separate reconstructed replay uses a maximum-length canonical
run ID and also consumes no new factory value. These assertions prove no new
identity allocation and retained generation for those replays; they do not
instrument every internal time read.

Changing persisted generation to 2 makes the original claim replay conflict
without identity allocation. The lock-race variant leaves that generation update
uncommitted, verifies replay blocks on the request row rather than accepting an
earlier snapshot, commits the change, then requires a safe conflict. Actual
[lifecycle replay](../../../../control-plane-kit-operations/src/control_plane_kit_operations/lifecycle.py)
checks current claimed status, actor and generation for a claim action before
reconstructing its run/event evidence.

Calling the store's claim_request directly for the same already-claimed worker
with durations 600 and 601 returns None. The status/worker/generation/time tuple
and the three table counts remain unchanged. Store-level claim admission is not
service-level idempotent replay or lease renewal.

The two-worker test submits distinct commands using a two-thread executor,
requires exactly one non-conflict result and exactly one run for the request.
There is no barrier proving both commands reached a particular instruction
simultaneously. This checks one winner under possible concurrent execution, not
global exactly-once delivery or provider execution.

## Transitions, current authority and settlement

The start/pause/resume/complete path requires RUNNING, PAUSED, RUNNING and SUCCEEDED
results, then reads the ordered five-event history RUN_OPENED through RUN_SUCCEEDED
with ordinals 1..5. A claimed cancellation records CANCELLED, starts the run at the
cancellation event time and retains generation 1 and the supplied reason evidence.
A separately started/paused cancellation preserves the original start timestamp
and sets settlement to the later injected cancellation time.

The service requires EXECUTION_OPERATE before dispatch and, for post-claim commands,
a claimed request whose exact worker/generation fence matches the supplied
authority. The [fence value](../../../../control-plane-kit-operations/src/control_plane_kit_operations/execution_leases.py)
bounds its worker text and positive 64-bit generation; constructor agreement and
current store authority are different checks. Neither _require_worker_owns nor
claim replay independently tests wall-clock lease expiry. The database expiry
observation tests must not be presented as proof that these transition methods
reject an expired but otherwise unchanged fence.

After a successful start, replacing generation 1 with 2 denies the old command
on replay. Reusing its key with generation 2 changes intent and conflicts; a new
pause with generation 3 is denied. Event/action counts remain unchanged. This
protects exact equality with current authority, not acceptance of any numerically
higher generation.

The failure test records FAILED and the supplied adapter-error code, then rejects
CompleteActivityRun. Despite terminal-settlement wording in its name, FAILED is
unsettled: actual FailActivityRun passes settled=False, while completion expects
RUNNING. The conflict therefore does not demonstrate a previously written
settled_at resisting overwrite. Actual store status updates require the expected
status and settled_at IS NULL, and preserve existing settlement with COALESCE;
separate compensation/recovery owners decide how a failed run settles. This file
does not execute compensation or accept uncompensated failure.

Closing the session through the real OperationCommandService still allows replay
of the original claim, but a new start fails the open-session gate and leaves
only RUN_OPENED in run history. That test covers claim replay after close, not
every lifecycle command's replay after closure. Replayed results contain the
currently read run and original action/event; the service validates the action's
recorded transition evidence without generally restoring a historical run snapshot.

## Replay payload integrity and lock ordering

The post-claim corruption tests modify stored action JSON, attempt replay, then
restore the original payload. One matrix substitutes foreign execution-request
and plan IDs, paused status/event kind and ordinal 999. Another removes or gives
malformed values to request ID, plan ID, run status, event type, event ordinal and
claim generation, including bool/zero for integer fields. Each replay must raise
a safe RunLifecycleError, and run-event/action counts stay at two. Despite the
every-coordinate test name, these matrices enumerate those fields; other linkage
cases have separate tests or remain source-level checks.

For a first start transition, the test holds the request lock, waits until the
worker blocks, and independently acquires the run row with FOR UPDATE NOWAIT.
After releasing the request, start succeeds. The unchanged replay variant repeats
that probe and requires replay without identity allocation. These prove the tested
paths do not already hold the run row while waiting for the request.

Changed intent under an existing start key conflicts while the request row is
locked by another connection. That subcase establishes it does not wait for the
request row; it has no separate run-lock probe. The actual transition owner checks
the fingerprint before either request/run FOR UPDATE call on this path. It still
performs locator reads and acquires the session-scoped action-idempotency advisory
lock, so locks-neither in the test name does not mean no database locks at all.

The actual ordering for new transitions is action-idempotency lock, open-session
lock, request lock, run lock, authority/state validation and event/action writes.
For unchanged transition replay it is action-idempotency lock, request lock and
run lock before validating retained evidence. These are selected row-order proofs;
the suite does not exhaust every multi-command deadlock interleaving.

## Rollback, malformed identities and retained evidence

An invalid generated run ID is rejected before database claim generation. Tests
require the queued status, absent worker/generation/times and no run/event/action
rows, or the corresponding status/count subset. The safe-error helper requires
no cause/context, combined string/repr length <=512 and absence of specified
canaries. Identity validation happens before claim_request; no provider rollback
or compensation is involved.

A valid run-ID collision raises the exact raw psycopg UniqueViolation and leaves
request-a queued without a run. A later event-ID collision, seeded on request-b,
fails after claim/run writes and requires request-a's complete claim tuple to be
reset, no run for it and no target action. These are actual transaction rollback
checks, with different observation breadth; neither compares every table row.

For an already running run, another worker's pause is denied. A same-worker pause
then collides with an existing action ID after attempting the status/event writes.
The raw UniqueViolation must leave the run RUNNING and history containing only
RUN_OPENED and RUN_STARTED. This tests rollback after a later statement failure,
not just refusal before mutation or a fault injected before a transaction begins.

The canonical identity round trip stores a one-character prior run ID and a
200-character current run ID at attempt 2, then reads current/prior IDs and the
event's run ID back through stores. Direct corrupt current-run SQL is rejected
by cpk_activity_runs_run_id_check. Corrupt prior/event run references produce the
named foreign-key violations, not independent grammar-check failures for those
columns. The [current schema](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
combines the canonical current-run grammar with references to admitted run IDs.
This is one accepted boundary pair and three rejected examples, not exhaustive
property-based identity validation.

Claim-action tampering that points to a run/event belonging to another request
in the same workspace must fail reconstruction. Deleting its retained event must
also produce a safe lifecycle error without the candidate-bearing store KeyError
or event-ID canary. The failing identity factory protects these paths from fresh
allocation. These tests do not authorize repair of corrupted rows or retry a
provider effect; the direct tampering is fixture preparation.

## Ownership, security and remaining evidence

The structure is a closed lifecycle-command vocabulary interpreted into a request
claim or run transition plus an ordered event and operation action, with replay
correlated by durable action intent and current authority. Core owns the event
vocabulary and canonical contract; Operations records and services own admitted
durable values, and PostgreSQL interprets store operations transactionally.

Security evidence here covers operate-scope denial, worker/generation checks,
selected malformed/foreign replay data, bounded public errors and secret-shaped
evidence rejection. Raw SQL integrity errors intentionally remain raw in collision
tests; this is not proof that such exceptions are safe to expose directly through
a public transport. The documentation introduces no new network or mutation surface.

The tests do not prove provider health, runtime execution, approval generation,
lease renewal/takeover policy, compensation success, graph advancement or recovery
from an ambiguous database commit. Their source shows the assertions described
here; this companion supplies no fresh green suite or live acceptance evidence.
