Source: [control-plane-kit-operations/tests/test_postgres_effect_attempt_start_first_replay.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_start_first_replay.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 331-line suite contains seven unittest methods for first-start persistence,
existing-attempt replay and selected read/write ordering. It uses actual PostgreSQL
stores and the actual start service through
[PostgresEffectAttemptStartFixture](postgres_effect_attempt_start_fixture.py.md).
Its wrappers record calls while delegating to the original store methods. There
are no worker threads, barriers, provider calls or process restarts in this file.
The main guard runs unittest on direct execution; this documentation task did not
execute the suite or access its database.

The inherited fixture requires an explicit test database URL, installs schema,
truncates/seeds the test world and supplies an autocommit observation connection.
Each service/readback unit of work gets a fresh transaction connection. Ordinary
setup leaves run-a running with the next event ordinal three; compensation setup
leaves it compensating with the next ordinal seven. Its helpers use fixed IDs and
synthetic history, not actual runtime execution. Teardown truncates the workspace
table with CASCADE before closing the observation connection.

The first test resets each of the ordinary and compensation worlds, builds its
runtime intent and matching start transition, and executes one first-start command.
It requires an instance of NewlyStarted and exactly the supplied ID in Sequence.calls.
The returned attempt must identify run-a/start-runtime/attempt one, retain the
command's request fingerprint, use worker-a/generation seven and have no prior
attempt. The start event must belong to run-a, have the appropriate ordinary or
compensation kind and use ordinal three or seven respectively.

It additionally requires equal original/latest events and evidence equal to the
fixture's state-commitment envelope. A fresh unit of work reads the attempt back
and compares the reconstructed record for equality. The actual attempt store
also reads its referenced events when reconstructing that record. The test then
requires the fixture snapshot to differ from its pre-start value. This provides
record/event readback assertions, but the changed-snapshot assertion alone does
not prove every expected table changed: the test does not separately fetch and
compare the persisted intent record or an exact full database delta.

The first replay case seeds a STARTED attempt directly with persisted_started,
expires the claim through raw SQL and constructs a new service for replay. It
requires equality with ExistingAttempt(current), no consumed ID and unchanged
fixture snapshot. During execution it forbids the particular
observe_request_lease_for_update method. Its name refers to restart replay, but
the setup uses direct fixture persistence and a fresh service/connection, not a
prior start-service invocation followed by a process or database restart.

The evolved-attempt case directly seeds STARTED and uses the fixture's synthetic
fold/CAS helper to persist SUCCEEDED. Replay must return that evolved record,
retain the original event and expose a different latest event. It also requires
no consumed ID, an unchanged snapshot and no call to the forbidden observation
method. The setup does not execute a runtime effect or the actual fold service;
the test concerns how start replay observes already-persisted evolved truth.

The historical case seeds STARTED and uses add_lawful_linked_retry to append failed
history and invoke the actual run-retry service. Replaying the original start
must still return ExistingAttempt(started), consume no ID and preserve the snapshot
while the lease-observation method is forbidden. A direct query additionally
requires run-a then run-b when ordered by attempt for request-a. The helper's
preparatory writes and retry service are separate committed steps, not part of
the start replay transaction being tested.

These three cases share the actual
[start interpreter's replay branch](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_start_interpreter.py).
It validates command/scope/fence, reads the locked request, request-scoped run and
attempt, and requires current claimed status plus a matching claim fence. Existing
attempts must agree with command identity, fingerprint, prior and effect fence;
the stored intent record must equal the expected record tied to the original
start event. That branch requests commit and returns before first-start latest-run,
journal-readiness and database-expiry observation. Expired lease replay is therefore
not permission to ignore a changed claim or mismatched intent.

The inherited snapshot combines selected request/run/action/event fields with
all attempt rows and selected intent rows, including preimage. Its reads are
separate autocommit queries, with differently scoped history versus attempt/intent
tables. Equality is evidence for those selected observations in these tests, not
a transactionally consistent whole-database comparison or an assertion that no
unobserved field changed. The observation patch forbids one named method, not all
database reads or every possible source of database time. Sequence.calls records
successfully returned IDs; each no-allocation probe has a supplied ID available.

The replay-order test seeds the existing attempt before patching four class methods:
request lookup, request-scoped run lookup, attempt lookup and intent lookup. Each
wrapper records its label and calls the original method. Execution must produce
exactly request, run, attempt, intent. The service result and ID consumption are
not separately asserted in this method. The expected list concerns these four
instrumented methods, not every SQL query: attempt reconstruction reads events,
and intent retrieval joins its original event through its own query.

The first-start order/time test patches request, scoped run, attempt, latest-run,
lease observation and ordinal allocation methods and supplies a recording ID
factory. All wrappers delegate to actual stores. It requires this exact trace:

```text
request, run, attempt, latest, request, clock, ordinal, identity
```

The second request call comes from the actual
[PostgresExecutionStore observation method](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py),
which locks/reads the request again before querying clock_timestamp() and expiry.
The observation wrapper appends clock after the original method returns, whereas
the other store wrappers record before delegation. The test requires exactly one
captured observation and equality between its observed_at and the returned start
event's occurred_at. This verifies propagation of the store's database observation;
it is not an independent timing oracle or a bound on elapsed time.

The inspected stores use FOR UPDATE for request, scoped/latest run and attempt
reads; ordinal allocation separately locks the run and reads MAX(ordinal)+1.
The trace test does not introduce a competing connection to demonstrate blocking,
nor does it instrument every plan/event read. Its "locks complete truth" name
must not be expanded into exhaustive lock-coverage or concurrency evidence.
The actual first-start service separately checks latest-run identity, plan/session
agreement, projected journal readiness and run phase, then compares intent to the
plan/operation and requires an unexpired observed claim before generating the event.
Those implementation checks explain the path; this positive ordering test does
not individually exercise their rejection branches.

The final test wraps add_event, intent insert and attempt insert. The event wrapper
records the supplied event before delegating. The evidence wrapper requires that
the appended-event list equals its record's original event; the attempt wrapper
requires the same equality and verifies that NewlyStarted(record) can be constructed
from the supplied record. All three still call the original database writers.
After service execution, the test requires a NewlyStarted instance and the exact
trace event, evidence, attempt.

This establishes a supplied event and admissible complete record at the relevant
write seams. It does not capture the identity of the service's earlier result object
or inspect a committed row from another connection while those wrappers run.
The actual service constructs its result and intent record before writing, checks
writer acknowledgements and requests commit afterward. The inspected
[intent store](../src/control_plane_kit_operations/postgres/effect_attempt_intent_store.py.md)
inserts evidence and the
[attempt store](../src/control_plane_kit_operations/postgres/effect_attempt_store.py.md)
uses insert-if-absent within the caller's transaction; neither commits independently.
No deliberate failure or rollback is injected by this suite's ordering test.

mock.patch contexts restore original class bindings after each instrumented test.
They are process-global class patches rather than isolated per-instance mocks, so
the fixture provides no concurrent-patch isolation. The suite does not exercise
adversarial error rendering, stale-fence rejection, failed acknowledgements,
rollback, contention or provider dispatch. Such behavior must be supported by its
own tests, not inferred from these successful starts and replays.

Read depth: the complete 331-line suite and every wrapper were read, with retained
full 416-line start fixture and its inherited helper context. The complete 431-line
start interpreter was read across this and the preceding companion slices,
including replay and first-start admission helpers. Selected actual PostgreSQL
request/run/clock/ordinal/event, attempt reconstruction and intent read/write
methods were checked; full execution/intent store modules and neighboring suites
were not reviewed here. Validation was documentation-only: local links, whitespace
and frozen-source comparison. No application imports, tests, database/provider
calls, credential access, source/inventory edits or publication were performed.
