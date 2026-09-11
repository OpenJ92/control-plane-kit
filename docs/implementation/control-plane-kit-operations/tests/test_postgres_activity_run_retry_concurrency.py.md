Source: [control-plane-kit-operations/tests/test_postgres_activity_run_retry_concurrency.py](../../../../control-plane-kit-operations/tests/test_postgres_activity_run_retry_concurrency.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These six tests exercise selected concurrent retry schedules using real PostgreSQL
connections, the actual
[retry interpreter](../src/control_plane_kit_operations/activity_run_retry_interpreter.py.md)
and caller-owned UoWs. They prove participating-command outcomes and particular
lock stages, not every interleaving, global deadlock freedom, process recovery or
safe re-execution of an external effect. No runtime adapter participates.

The [retry fixture](activity_run_retry_interpreter_fixture.py.md) seeds an approved,
claimed request with a failed run, constructed history and synthetic future lease
timestamps. Its inherited setup installs the schema and truncates workspace-owned
truth at the configured database URL; it does not independently establish database
ownership or a unique schema. These tests belong to the established disposable
Docker package suite, not an operator database.

The equal-key race releases two worker functions through a two-party barrier.
Both execute the same command with distinct candidate ID sequences. A shared lock
protects a counter around the actual database lease-observation method. Results
must contain one fresh execution and one replay, with equal successor and action,
and exactly one observation. The selected snapshot must have two runs, one recovery
decision event and one action. The losing candidate's ID iterator is not separately
counted here; outcome and observation assertions should not become an independent
assertion about every internal call.

The different-key race uses the same prior run but separate command keys and
candidate IDs. It catches RunLifecycleConflict as a worker result and requires
one successful result, one conflict, two retained runs and one action. This proves
one winner for this race; it neither fixes which worker wins nor tests duplicate
provider effects. Exceptions other than the expected conflict are not accepted
as equivalent losing outcomes.

A separate test forces both winner identities in two reset scenarios. A dedicated
connection first locks request-a. The designated winner starts, exposes its backend
PID and must be observed blocked by that connection. Only then is the loser started;
it must be observed blocked by the winner before the external blocker commits.
The winner must return its named successor, the loser must conflict, the winner's
Sequence must record all four IDs and the loser's sequence must remain empty.
Run/action counts still show only one new successor/action. This is more directed
than the barrier race, but covers two deliberate schedules rather than arbitrary
workloads. The actual fresh session lock explains where a distinct-key loser can
wait before reaching the request; the polling query reports blocker PIDs, not
the specific SQL statement or lock object that caused each wait.

reporting_factory opens the worker connection and puts its backend PID in a queue
before returning it to PostgresUnitOfWork. wait_until_blocked_by repeatedly reads
pg_blocking_pids(worker_pid) and waits for the expected blocker PID. Thus later
NOWAIT checks are made after an observed blocking relationship, not merely after
sleeping and assuming a worker reached a line of code. An idle worker or an unrelated
blocker does not satisfy that predicate.

The first lock-stage test holds the request externally and observes the retry
worker waiting. A separate probe must acquire run-a FOR UPDATE NOWAIT while the
lease-observation count remains zero. After releasing the request blocker, retry
completes and the count becomes one. The companion stage instead holds run-a;
while retry waits there, a probe must fail to acquire request-a with
LockNotAvailable, again before any observation. After release the retry completes
with one observation. Together these tests witness request-before-prior locking
for these schedules; they do not prove every store caller follows that order.

Replay-lock coverage first persists a retry, then repeats a replay under three
different external blockers: request, prior run and successor. Replay's observation
and ID factories are forbidden-call sentinels. When blocked on the request, probes
must lock both runs freely. When blocked on the prior, the request must already be
held and the successor remain free. When blocked on the successor, both request
and prior must be held. Releasing the blocker allows a replayed result. These
probes establish the selected staged locking behavior, not a full transaction trace
or a comparison of every stored row after replay.

The helper for expected NOWAIT conflicts rolls the probe transaction back after
LockNotAvailable so it can make another query. Blocker connections are rolled back
and closed in finally blocks around the staged checks; observation monkeypatches
are restored in finally. The forced-winner helper also attempts to cancel a pending
loser future. Future cancellation does not stop an already-running database call.

Barriers and PID queue reads use five-second limits; blocker polling has a
five-second monotonic deadline, and explicit future.result calls use ten-second
timeouts. These are local waits, not an end-to-end suite deadline: connection/SQL
calls have no timeout configured in this file, a polling query can itself stall,
and ThreadPoolExecutor context exit waits for running tasks. No whole-test hang
bound or universal cleanup guarantee follows from the timeout arguments. This
companion records the existing evidence shape rather than adding another harness.

The actual
[execution selectors](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
constrain run locking by request and run ID and select the latest run by request.
The interpreter coordinates session-scoped command idempotency, then request and
run locks. Its real
[UoW](../src/control_plane_kit_operations/postgres/unit_of_work.py.md) commits on
successful context exit after commit is requested. Those source facts explain the
observed outcomes; these tests do not identify every advisory lock collision or
exercise unrelated writers, transaction failures, lost commit acknowledgments or
cross-service lock cycles.

The inherited snapshot used by the first three tests includes selected request,
event, session-action and run fields; assertions here inspect particular counts
and record equalities, not full database equality. Later lock tests primarily
assert probes, observation counts and the returned run/replay flag. Simulated
approval and authority values do not establish real caller authentication or a
new human approval. Nothing in this suite authorizes automatic retry, compensation
or provider cleanup.

Read depth: full 420-line test including six methods and all helpers, full 466-line
interpreter, 218-line fixture, 572-line parent and actual UoW; selected actual
execution selectors and history advisory-lock/lookup SQL. Pure owner and neighboring
retry tests were fully read in the preceding reviews. No tests, application imports,
database connections, source/pin edits, credentials or provider/runtime actions
were performed. Documentation adds no security surface or runtime acceptance.
