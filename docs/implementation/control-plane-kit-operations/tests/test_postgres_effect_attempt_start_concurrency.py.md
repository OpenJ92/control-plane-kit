Source: [control-plane-kit-operations/tests/test_postgres_effect_attempt_start_concurrency.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_start_concurrency.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 305-line suite contains five tests for start-service lock ordering, identical
starter serialization and stale replay after claim replacement. It combines real
PostgreSQL row locks, backend PID observations, thread executors, NOWAIT probes
and an ID-allocation pause. It inherits the
[PostgreSQL start fixture](postgres_effect_attempt_start_fixture.py.md).
Its final claim-replacement test is sequential. No test executes runtime providers
or restarts a process; the main guard runs unittest on direct execution, which
was not performed during this documentation pass.

_BlockingId stores a supplied ID, entered/release threading events and a call
counter. Invocation increments the counter, signals entered and waits up to ten
seconds for release before returning the ID; timeout raises AssertionError.
In the selected actual service, ID allocation occurs after admission, lease-time
observation and ordinal calculation, while the transaction still holds its locks
and before start-event insertion. The pause therefore holds a first-start
transaction at that particular point, not at an arbitrary barrier before admission.

_factory_with_pids creates fresh psycopg connections, sets lock_timeout to ten
seconds and statement_timeout to twelve seconds, then publishes each backend PID
to a queue before returning the connection to PostgresUnitOfWork. These settings
apply to those worker connections, not every fixture, blocker or probe connection.
The actual unit of work commits on successful requested exit, rolls back failure
and closes its connection. Connection setup before return has no local cleanup
handler if a SET statement fails.

_wait_until_blocked_by repeatedly queries pg_blocking_pids(worker_pid) through the
fixture connection and returns only when the expected blocker PID appears. It
checks a five-second monotonic deadline between queries and has no sleep/backoff.
This is a database-reported blocking relationship, not an inference from an
unfinished future alone. The deadline does not independently bound a single query
on the fixture connection, which this helper does not configure with a timeout.

The probe helpers open separate connections. _assert_run_lockable acquires run-a
with FOR UPDATE NOWAIT and checks its returned ID. _assert_retained instead
requires LockNotAvailable for a specified row. Its table and column strings come
from fixed internal call sites; the row value is parameterized. That exception
establishes contention but does not itself identify the lock holder.

_assert_attempt_absent_and_lockable requires no visible matching attempt from a
FOR UPDATE NOWAIT query. Because no row is returned, this does not acquire or
prove ownership of a tuple lock or establish a gap lock for a nonexistent attempt.
_assert_foreign_truth_lockable acquires request-b, run-foreign and its attempt
through three NOWAIT reads in one probe connection. Those values share the fixture's
workspace/session; they demonstrate selected different-request coordinates, not
cross-tenant isolation or absence of locks on every unrelated row.

_blocked_execution optionally seeds existing and foreign attempts, opens a blocker
connection and locks request-a, run-a or the existing attempt. It submits one
actual service call to a single-worker executor with the fixed ID lock-order-event.
If the future remains incomplete after 100 ms, the helper gets its PID, requires
the expected pg_blocking_pids relationship and performs the relevant probes.

When blocked on the request, run-a must remain lockable and the attempt must be
absent. When blocked on the run, the request must be retained and the attempt
absent. When blocked on the existing attempt, both request and run must be retained
while the three foreign rows remain lockable. The blocker then rolls back and
closes, and the worker must return within a further five-second future wait.
The three consumer tests require NewlyStarted for request/run blockers and
ExistingAttempt for the existing-attempt blocker.

There is an important alternate branch: if the future returns within the initial
100 ms, _blocked_execution returns that result immediately without PID/blocker or
retained/free-lock probes. The tests then check only its result category. Their
source therefore permits a successful fast path without the advertised blocking
evidence; the helper does not assert that its timeout branch was taken. This is
a static limit of the test, not an observed pass, failure or runtime race from this
documentation review.

The helper's finally block releases an open blocker and shuts down the executor
with wait=True and cancel_futures=True. Allocation/locking/submission before the
try block is not covered by that cleanup, and rollback/close/shutdown are sequential
within it. Future timeouts and cancellation do not forcibly stop an already-running
worker; wait=True can extend cleanup beyond an individual future's timeout.

The identical-starters test runs two rounds, swapping left/right labels for the
deliberately first service. That first service uses _BlockingId; the second uses
a Sequence containing an ID that must remain unused. A class-level wrapper counts
lease-observation calls under a threading.Lock and delegates to the real method.
The first call is submitted, then the test waits briefly for its ID-allocation
signal before submitting the second and obtaining its PID.

The main-thread entry handshake waits 100 ms; if it has not seen entered, it calls
first_future.result with another 100 ms timeout rather than continuing to wait
for the ten-second ID-pause window. A slow first worker can therefore fail this
handshake before reaching the intended pause. This timing behavior is distinct
from an observed defect and from the later explicit database blocking assertion.

The test requires pg_blocking_pids to show the second worker blocked by the first,
releases the first ID pause and waits up to ten seconds for each future. Finally
it always signals release and shuts down the executor. The observation patch
remains active through worker shutdown and is then restored. This schedule forces
a chosen first worker ahead of the second; swapping labels does not turn it into
an unconstrained simultaneous-start or randomized-winner test.

Each round requires exactly one NewlyStarted and one ExistingAttempt instance,
one observed lease-method call, one first-ID invocation and no second-ID consumption.
SQL additionally requires one effect-attempt row in the table and one matching
STEP_STARTED event for run-a/start-runtime. The test does not compare the two
returned records for equality, read back the exact intent record, count intent
rows or assert the persisted event ID separately. It covers ordinary starts only.
The name's "dispatch result" refers to the result variant; no provider is invoked.

The actual
[start interpreter](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_start_interpreter.py)
locks the request, request-scoped run and attempt before checking current claim
authority and choosing replay or first-start work. The selected actual
[execution-store methods](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
use FOR UPDATE for request/run reads; the
[attempt store](../src/control_plane_kit_operations/postgres/effect_attempt_store.py.md)
uses the full run/activity/attempt key with FOR UPDATE for existing-attempt reads.
The request lock supplies an existing row on which identical absent-attempt starts
can serialize; the absent-attempt query itself does not reserve a nonexistent row.

After the first start commits, the interpreter's replay path checks current claim,
attempt identity/fingerprint/prior/fence and stored intent evidence. It returns
ExistingAttempt before the fresh-start lease observation and ID-allocation path.
That implementation explains the one-observation/one-ID assertions. The fixture
test does not independently trace every SQL lock or prove serializability for all
commands, isolation levels, phases or adapter implementations.

The final test performs a successful real first start, directly replaces the
claim through the fixture, and then submits the old command. It requires
EffectAttemptStartDenied with the inherited safe-error checks, unchanged selected
snapshot and no call to the patched lease-observation method. It does not assert
the exact error message or retain the stale service's ID sequence for an explicit
allocation-count check. The replacement is a committed raw setup update after
the first call, not a concurrently executed lease-rotation command.

The snapshot comparison in that test is the fixture's selected multi-query view,
not an atomic whole-database snapshot. Safe-error assertions bound combined
str/repr at 512 characters and require absent chaining; they do not inspect logs
or tracebacks. The class-level observation patch affects all store instances while
active; its counter has a lock, but the patch is not isolated from unrelated
concurrent users of the class.

Read depth: the complete 305-line suite, all helper methods, ID blocker and callback
were read. Full start fixture416, interpreter431, attempt-store280 and unit-of-work
context was retained/refreshed, with selected actual execution-store locking and
observation SQL. The full execution store and neighboring suites were not reviewed
for this slice. Validation was documentation-only: local links, whitespace and
frozen-source comparison. No application imports, tests, database/provider calls,
credential access, source/inventory edits or publication were performed.
