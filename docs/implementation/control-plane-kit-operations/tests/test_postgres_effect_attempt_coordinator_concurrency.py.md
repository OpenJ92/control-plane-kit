Source: [control-plane-kit-operations/tests/test_postgres_effect_attempt_coordinator_concurrency.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_coordinator_concurrency.py).
Maintain this document alongside its source file. Recheck command receipt scope,
thread pause locations, claim fencing and the exact assertions when this suite or
its fixture/service dependencies change.

Four tests in 209 lines exercise selected duplicate-command, existing-attempt and
claim-rotation boundaries. Two tests arrange overlapping coordinator calls with
threads and Events; the other two are sequential. The inherited
[coordinator fixture](postgres_effect_attempt_coordinator_fixture.py.md)
composes real PostgreSQL-backed Operations services with recording adapters and
scripted observations. Its isolated database setup/reset/cleanup is destructive.
No test, application import, database connection or provider action was executed
while authoring this companion.

## Local concurrency machinery and its limits

_RequestDerivedObserver records each request/authority pair, derives the runtime
intent from the supplied request and substitutes that request's effect ID and
intent fingerprint into a prepared observation value. It creates correlated
synthetic evidence; it does not query a runtime. The first test supplies this
observer to both harnesses but requires zero reconciliation calls, so the
observer's presence does not establish an exercised observation path there.

concurrent_results submits all supplied callables to a ThreadPoolExecutor and
collects their results in submission order with a 20-second timeout per future.
Only ExecutionCoordinatorConflict and ExecutionCoordinatorDenied are converted
to returned values; other errors propagate. None of this file's four tests calls
this helper. A future timeout is not cancellation, and executor context exit may
still wait for workers. Neither this helper nor the tests' five-second waits
establish a total process deadline or database statement timeout.

The two overlapping tests instead use their own two-worker executors. The first
worker signals an Event at a selected boundary and waits for release. The main
thread waits for that signal, obtains the duplicate's result while the first
future is still unfinished, then releases the first worker in finally. This
deliberately orders admission before the duplicate; it is not a simultaneous
race to create the first receipt. The fixture's TimeoutRendezvous is not used.

## Fresh command: one admitted execution and result replay

The first test creates two harnesses sharing a provider callback. The callback
records the effect ID, signals admission, waits for release and returns a
synthetic successful RuntimeEffectResult. Both coordinator commands use the same
default run and idempotency key. While the first is paused inside that callback,
the second must return UNCERTAIN with zero effects_attempted and no activity ID.
After release, the first must return COMPLETED with one effects_attempted; a
later call through the second harness must equal the entire completed result.

Across both harnesses the test requires exactly one recorded provider call, one
distinct effect ID, one start-service call and zero reconciliation calls. It
also compares graph_request_snapshot before and after. That snapshot protects
selected workspace graph pointers/revision, request linkage/claim and plan
fields; it does not compare every graph descriptor, receipt, event or table.
The distinct-ID assertion over a single call does not demonstrate identifier
uniqueness across unrelated operations.

The actual [coordinator admission and completion](../../../../control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py)
explain this result. Admission uses a transaction-scoped advisory lock keyed by
run ID and command idempotency key, then locks the request/run, checks current
worker ownership and reads the receipt for update. The command fingerprint
includes run, worker, scopes, claim generation and effect budget. A matching
incomplete receipt returns uncertainty without entering the effect loop; a
matching completed receipt returns the stored result. Different intent under
the same key conflicts. Replay still passes current claim checks first.

For a new command, admission commits the incomplete receipt before execution.
After execution, a separate transaction completes it. The
[PostgreSQL receipt store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
updates only the matching fingerprint and still-incomplete receipt with null
completion/result fields. Provider execution is outside the admission transaction;
the second caller can observe admission while the callback is paused.

Despite the test name's word global, these assertions cover one run/key/intent
and an already admitted first caller. They do not prove one provider winner for
different keys, cross-process crash recovery, arbitrary scheduling, every
provider or every effect. Receipt SQL and ownership checks explain the mechanism;
this test does not independently assert receipt contents, conflicting intent or
stale-authority replay behavior.

## Existing attempt and rotation of the current claim

The existing-attempt test seeds one running reconciliation story and invokes one
coordinator. It requires one effects_attempted, no runtime adapter calls and one
reconciliation command carrying the seeded attempt identity. Thus a selected
existing attempt consumes a budget unit without provider redispatch. Despite its
name, this method is not a matrix over every attempt state; it also does not
assert final status, observer/fold counts or exact resulting durable history.

Its queued AssertionError is not itself a throwing sentinel: RecordingRuntimeAdapter
raises only exact TypeError/RuntimeError and otherwise returns queued values or
invokes callables. The explicit empty runtime_calls assertion establishes that
the selected path did not reach that adapter.

The claim-rotation test performs the rotation inside the provider callback,
after recording the call. The inherited replace_claim helper directly updates
request-a through the fixture's autocommit connection to worker-b/generation 8
and fixed future claim/lease timestamps. This is prepared database mutation,
not an exercised recovery-approval or lease-acquisition workflow. The callback
then returns synthetic success to the original worker's invocation.

The test expects ExecutionCoordinatorDenied and requires one provider call, one
start call, one fold call and no reconciliation calls. RecordingService records
commands before invoking its inner service, so one fold call does not mean a
successful fold. Actual [fold authority validation](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold_interpreter.py)
locks and re-reads the request/run/attempt, rejects the changed current fence
before folding, and the coordinator translates that denial.

This establishes the distinction between a dispatched effect and permission to
record its result under the current claim. Rotation occurs inside an already
entered callback; the test does not arrange rotation between start commitment
and callback entry, prove cancellation of an external action, or verify recovery
afterward. It does not inspect final attempt/receipt rows. From the inspected
control flow, an escaping denial bypasses command completion; the earlier
admission receipt is not rolled back with the failed fold. No automatic retry or
compensation follows from this test.

## Duplicate terminal settlement and one untouched foreign attempt

The final test prepares recovered success, then seeds a foreign run and STARTED
attempt. The selected inherited seed copies plan/request metadata into plan-b
and request-b and adds run-foreign; it does not establish another isolated
workspace. Its attempt is persisted with its own run/event identity.

The first harness pauses in RecordingService.before_execute on the lifecycle
service, after recording the command but before calling the actual lifecycle
implementation. While paused, the duplicate must return UNCERTAIN with zero
effects_attempted and no activity ID. After release, the first must return
COMPLETED with zero effects_attempted, and a later same-command call must equal
that completed result. Across the harnesses exactly one lifecycle command is
recorded; both runtime/start/reconciliation call lists stay empty.

The test reads the foreign attempt by its identity and compares the complete
decoded record to the seed, then checks the prepared terminal start event belongs
to run-a. That is concrete noninterference evidence for one unrelated attempt;
there is no simultaneous execution of the foreign run or complete snapshot of
its surrounding durable state. The test also does not directly inspect receipt
rows, all lifecycle events, request settlement or graph advancement.

Read depth: full 209-line suite/four tests and both local helpers; actual command
fingerprint/admission/completion, request/run ownership guards, receipt SQL,
selected dispatch/fold denial paths, claim replacement and foreign run/attempt
seed owners; retained full coordinator fixture334/reconciliation385, start/fold
service boundaries and PostgreSQL UoW context. No full audit of every imported
module is claimed. This note adds no security surface and authorizes no fixture
execution, external mutation, recovery, retry or held live work.
