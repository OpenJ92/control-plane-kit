Source: [control-plane-kit-operations/tests/test_postgres_activity_run_retry_first_replay.py](../../../../control-plane-kit-operations/tests/test_postgres_activity_run_retry_first_replay.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These 14 tests exercise the actual
[retry interpreter](../src/control_plane_kit_operations/activity_run_retry_interpreter.py.md)
against PostgreSQL for first persistence, historical replay, retained authority
and selected corruption boundaries. They use the
[retry fixture](activity_run_retry_interpreter_fixture.py.md), actual stores and
PostgresUnitOfWork; wrappers either preserve the implementation while tracing it,
substitute selected reads or forbid clock/ID work. They do not call a runtime
adapter, authenticate a caller or demonstrate a live failed deployment retry.

The inherited fixture installs the schema and truncates workspace-owned test
truth using the supplied test database URL; it does not itself provision a unique
database or check ownership of that URL. reset_retry_truth starts with constructed
approval, claimed request, failed run and step history, then assigns synthetic
2098/2099 lease timestamps. The fixture's authority_reference and operator are
test data. Approval admission here concerns reconstruction/linkage of retained
records, not a real principal's credential or a new human approval flow.

The first test requires exactly four ID factory calls in run/decision/opened/action
order. Its returned successor is CLAIMED on attempt two with prior_run_id run-a
and matching retry metadata. The old run is FAILED; decision evidence is
retry-as-new-run with equal worker-a generation-7 fences. The decision belongs
to the old run and the opening event to the new run at ordinal one. Run/event/
action times must agree; action actor, idempotency and fingerprint must match
the command. A separate UoW reloads the successor, both events and idempotent
action and compares each complete record to the result, then the fixture snapshot
must contain two runs. This proves the asserted database representation across
connections, not a process restart or execution of the successor.

The exact-replay test first executes retry, then uses SQL to close the session and
expire its request lease. Replay forbids observe_request_lease_for_update and ID
allocation. Its complete result must equal the first result with only the current
request and replayed flag replaced, and the selected snapshot stays unchanged.
The actual interpreter still requires OPERATE, retained approval, current CLAIMED
request and matching fence. Read-only here means no domain record changes; replay
still enters a transaction, takes locks and requests commit.

The evolved-status test iterates ten explicit status strings. It changes the
successor status and appropriate started/settled timestamps directly through SQL,
then requires replayed=True and the supplied status without clock or ID calls.
It does not drive each status through lifecycle commands, validate the complete
transition history or compare a before/after snapshot in this method. Unlike the
neighboring pure result test, this list is not asserted equal to the enum itself.

Two tests build actual linked retries run-a to run-b to run-c and replay the first.
Between retries, _fail_run_for_retry invokes real lifecycle Start/Fail commands
around manually inserted STEP_STARTED/STEP_FAILED events in separate transactions.
Thus the first successor really evolves in stored records, but its failed step
does not come from an adapter invocation. Historical replay must return run-b,
now FAILED, with its original prior, while run-c remains the second successor
on the same request fence. A companion negative changes run-c's prior_run_id to
run-a and requires bounded conflict. Both forbid clock/IDs and preserve selected
snapshots. This single broken-link mutation can disagree with both stored metadata
and successor lineage; it is not isolated coverage of every traversal rejection.

The complete-truth test applies eleven mutations after a successful first retry:
replaced or abandoned claim; missing prior, successor, decision or opening event;
changed attempt/metadata; action request coordinate or actor; opening time; and
replacement recovery generation. Missing records expect RunLifecycleNotFound,
while the other forms expect RunLifecycleConflict. Each replay forbids observation
and IDs, keeps its post-corruption snapshot unchanged and checks selected canaries.
The missing-prior helper deletes both runs and their events; it does not isolate
a database state with only the predecessor missing. These tests reject retained
drift rather than repairing it, and do not enumerate every malformed payload.

The selector-error test injects OperationsRecordError and RuntimeError into
get_run_for_request_for_update after first persistence. The first becomes bounded
RunLifecycleConflict; the second must preserve exact exception identity. Since
the patched selector fails on every invocation, this exercises the first prior-run
lookup reached in replay, not separate failures at every successor lookup. Neither
case may observe time, allocate IDs or change the selected snapshot.

The foreign-successor test seeds a second request/run, then substitutes the
loaded action's run_id in memory to name that foreign run. It requires the exact
selector call sequence (request-a, run-a), then (request-a, run-foreign), followed
by NotFound, no clock/IDs and unchanged selected truth. Inside the second selector
wrapper, a separate connection can lock the foreign row with NOWAIT before the
original selector is called. That probe shows the row is free at that point; it
does not independently sample locks after the original selector. The actual
[execution store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
supplies the stronger implementation fact: both request and run ID constrain its
WHERE clause before FOR UPDATE. The foreign fixture copies session/approval fields
and seeds a row; it is not an independently authorized deployment.

Changed-intent coverage reuses the original idempotency key with fence generation
eight instead of seven. It requires RunLifecycleIdempotencyConflict, no clock/IDs
and unchanged snapshot. This is one fingerprint-changing example, not every input
in the fingerprint law. The [pure command/result companion](test_activity_run_retry_contract.py.md)
covers the separate fingerprint input and representation contract.

Two approval tests exercise both activity-plan and gateway-key-rotation subjects.
One forbids both mutable gateway-store get methods and requires a fresh result for
each subject. The other wraps the actual shared approval and journal functions,
records their call order and checks the journal decision is RETRY_AS_NEW_RUN for
each execution. The wrappers still call the real functions. These assertions
protect delegation and supported retained subject types, not absence of every
possible store read or an independent authorization policy implementation.

Approval-corruption coverage crosses both subjects with four edits: review digest,
decision scope, rejected decision and an approval request moved to another session.
Each must conflict before observation or ID allocation, omit selected canaries and
preserve the snapshot taken after corruption. The label plan-link corresponds to
the approval-session edit, not a direct change to the plan row. The actual
[shared support](../src/control_plane_kit_operations/_execution_lease_recovery_support.py.md)
and typed store decoders jointly establish rejection; these cases do not prove
which individual check rejects every malformed approval.

The retry/lease-rotation test runs two sequential worlds. After retry creates
run-b, it expires the lease and attempts expired renewal against old run-a; that
must conflict without allocating IDs. After a reset, a real expired renewal first
advances the fence to generation eight; retry with the stale generation-seven
command must then conflict without allocating IDs. Each rejected operation leaves
the selected snapshot unchanged. This is ordering/stale-target evidence, not a
concurrent race or proof that all retries after lawful renewal are disallowed.

The final test adds a newer failed run and journal directly through stores, without
a retry decision/action linking it as service-produced evolution. Presenting the
old prior must conflict and preserve the snapshot. It does not assert every
generated-ID sentinel is unused in this method or establish eligibility of the
newly seeded run; it protects rejection of the presented stale prior.

The actual [base fixture](../../../../control-plane-kit-operations/tests/execution_lease_recovery_fixture.py)
snapshot contains selected request status/claim fields, events for that request's
runs, session actions and run IDs/attempts/prior/status/timing fields. It omits some
columns, including run metadata/creation time and action creation time, and does
not cover every table or the foreign request. The first test's complete record
reloads are stronger for its four asserted records than this common snapshot.
No snapshot assertion should be read as byte-for-byte equality of the database.

safe_error requires no cause/context, combined str/repr length at most 512 and
absence of the supplied canaries. This supports selected expected-error redaction;
the raw RuntimeError identity test deliberately preserves an unexpected dependency
exception. There is no universal log/exception sanitization claim. Patches are
restored with finally blocks, but several setup mutations are committed fixture
edits; these tests are designed for the owning isolated test harness.

Other files own missing-scope-before-UoW, complete planning/persistence order,
rollback injection and concurrent lock schedules. This file does not independently
prove those guarantees, provider safety, ambiguous commit recovery, automatic retry,
compensation, process restart or cleanup of live resources. Source review did not
execute any of its database setup, mutations or tests.

Read depth: full 765-line source with all 14 methods/helpers, full 218-line retry
fixture, retained full 466-line interpreter, 299-line pure owner and 390-line shared
support; actual UoW and selected execution selectors, lease-recovery first path,
base-fixture setup, snapshot, seed, newer-run and event construction were inspected.
No source/pin changes, executable tests, credentials/private-key access, database
setup, provider/runtime actions or publication occurred. This documentation adds
no security surface or permission to repeat the represented live operations.
