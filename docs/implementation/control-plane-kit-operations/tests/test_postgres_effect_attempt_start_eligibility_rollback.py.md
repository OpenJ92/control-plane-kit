Source: [control-plane-kit-operations/tests/test_postgres_effect_attempt_start_eligibility_rollback.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_start_eligibility_rollback.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 592-line suite contains fifteen unittest methods for first-start eligibility,
claim/replay authority, expected data-error categorization and rollback after
selected execution failures. It uses actual PostgreSQL connections and stores
through the
[start fixture](postgres_effect_attempt_start_fixture.py.md), combining committed
setup mutations with class-method patches and two connection wrappers. It does
not run providers, introduce competing threads or test a process restart. The
main guard runs unittest on direct execution; this documentation pass did not
execute the suite or access a database.

The inherited fixture prepares a running or compensating run, a claimed request,
matching intent and optional persisted attempt. Raw setup SQL uses its autocommit
connection, while the start service uses a fresh PostgresUnitOfWork connection.
Snapshots are taken after deliberate setup corruption or claim changes. Equality
after rejection therefore concerns the prepared state, not restoration to the
original healthy fixture. The snapshot includes selected request/run/history fields
and attempt/intent rows across separate queries; it is not an atomic whole-database
snapshot and does not include the activity-plan payload.

_CommitFailureConnection delegates unknown attributes to a real connection but
overrides commit to raise the supplied error without calling the underlying commit.
Rollback and close still delegate normally. _ClockRejectingConnection delegates
everything except execute: it rejects queries whose string rendering contains
the literal clock_timestamp(), otherwise forwarding the query and optional
parameters. It is a check for that SQL spelling, not every database clock or
possible time-observation mechanism.

The phase/status test has five cases: forward execution while paused or compensating,
compensation while running or paused, and forward execution after an already
appended STEP_STARTED. Each must raise EffectAttemptStartConflict with the
eligibility message, consume no ID, preserve the snapshot and avoid the patched
lease-observation method. A separate test supplies an internally coherent but
foreign activity identity and intent in each phase and requires the same outcome.
These finite cases protect phase/readiness admission, not every possible journal.

The absent-attempt authority test covers an expired lease, foreign worker and
stale generation. All must be denied with the authority message and no consumed
ID or snapshot change. A delegating observation wrapper counts exactly one call
for expiry and zero for the worker/generation mismatches. This distinguishes
database-time expiry checks from earlier matching-claim checks. The claimless
test instead directly sets request status queued and clears claim fields; the
clock-rejecting connection must permit the expected denial without encountering
its forbidden SQL, consuming an ID or changing the selected snapshot.

Two existing-attempt cases distinguish current authorization from historical
attempt identity. After claim replacement, the old command must receive an
authority denial. A command matching the new worker/generation passes that claim
comparison but conflicts with the old attempt's fence, using the replay message.
Both cases forbid lease observation, assert no consumed ID and compare snapshots.
Thus an existing attempt is not transferable simply by replacing the current
claim or submitting the new worker/fence values.

The changed-observation test calls the real observation method, then replaces
requested_by only in the returned request value. The service must reject its
inequality with the earlier locked request using the invalid-truth message,
without ID consumption or snapshot change. This is an in-memory return-value
substitution after a real observation, not a concurrent database update.

Missing request and request-scoped run cases construct commands naming absent
coordinates. They require EffectAttemptStartNotFound, the fixed not-found message,
canary-free rendering, no consumed ID and no lease-observation call. Unlike many
other cases here, this method does not compare a before/after snapshot. The test
therefore does not carry its own explicit no-write snapshot assertion.

The malformed plan/journal method commits two separate corruption scenarios:
plan payload replaced with JSON scalar 1, and the seeded run-start event's evidence
replaced with scalar 1. Both must produce invalid-truth conflicts before observation
or ID allocation and preserve the selected snapshot. The plan query and event
query use actual stores; no decoder is patched in these two cases. Since the
snapshot omits plan payload, its equality does not independently assert preservation
or repair of that corrupted plan field.

The expected-decoder matrix patches seven boundaries: locked request, scoped run,
latest run, plan, run events, locked attempt and lease observation. Each is made
to raise ValueError and OperationsRecordError, producing fourteen categorical
cases. They require a fixed invalid-truth conflict, no cause/context, bounded
rendering without the injected canary, no ID consumption and unchanged snapshot.
The observation method is forbidden for earlier seams; nullcontext avoids replacing
the observation seam's own injected failure. These are method-level error injections,
not fourteen distinct corrupt database encodings.

Each of the same seven seams also receives a RuntimeError. Those cases assert that
the identical raw exception escapes. They do not retain the ID sequence for an
allocation assertion, compare snapshots or install the separate clock guard.
The raw error's canary is intentionally not required to be redacted. The suite
therefore distinguishes expected representation failures from internal errors,
rather than claiming that every exception is normalized or safe for public output.

The actual
[start-service read helpers](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_start_interpreter.py)
convert selected ValueError/OperationsRecordError failures after leaving their
handlers, supporting fixed unchained errors. Missing request/run/plan truth maps
to not-found, while a missing attempt selects the first-start path. The helpers
do not catch arbitrary RuntimeError. Current-claim checks precede replay, and
first-start admission requires the latest run plus coherent plan/session and
exact ready-phase membership before observing expiry or allocating an event.

The changed-replay method covers two more prepared states. One changes the command
intent by removing products and authority deliveries, then generates its matching
transition fingerprint; this must conflict with the stored attempt using the
replay message. The other corrupts the persisted original event's evidence and
requires invalid-truth conflict during record retrieval. Both forbid lease
observation, consume no ID and preserve snapshots. The supplied rendering canary
is c-times-64; it is not an assertion against every field of the changed intent
or corrupt row.

The historical-run case first seeds an attempt and adds a linked retry through
the fixture helper. It then deletes only the old effect-attempt row before asking
for a new start on that historical run. The service must reject it as ineligible
without observation, ID use or selected-state change. The setup retains other
history and does not explicitly delete the old intent evidence. It is a concrete
missing-attempt scenario after retry, not a pristine empty history or general
orphan-repair test.

The main rollback method injects RuntimeError before the original ordinal,
event-insert and attempt-insert methods run. It separately uses an ID factory
that immediately throws and a connection whose commit immediately throws. All
five cases require the identical raw error and an unchanged fixture snapshot.
At ordinal/ID failure, start writes have not begun. Event failure occurs before
that original event insert; attempt failure occurs after the service's actual
event and intent-evidence writes. The later cases therefore exercise rollback
of writes already made within the unit of work, not just early rejection.

The actual
[PostgresUnitOfWork](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
requests commit explicitly and performs it on successful context exit. Exceptional
exit rolls back; a commit exception triggers rollback and re-raise, with close in
finally. The test's commit wrapper fails before the underlying database commit,
so it covers that deterministic failure path, not an ambiguous commit that may
have reached the server. Snapshot equality does not directly count rollback/close
calls, and this suite does not inject failures into rollback or close themselves.

The insert-absent-miss test replaces attempt insertion with a None return and
requires the fixed serialization conflict plus unchanged snapshot. Because it
does not call the original attempt insert, this is a simulated acknowledgement
miss rather than a real uniqueness race. The actual service has already written
the candidate event and intent evidence in its transaction; its rejection must
leave the selected snapshot unchanged after exit.

The final method tests event and attempt writers returning changed values after
calling their original database methods. changed_event returns an event with a
different ID. changed_attempt returns a newly constructed STARTED record with a
different original-event prefix; that replacement is not itself persisted by the
wrapper. Both must cause the serialization conflict with safe rendering, exactly
one consumed start ID and unchanged snapshot. This specifically exercises changed
acknowledgements after real writes, unlike the before-call exceptions and stubbed
None result.

The actual start service compares event and attempt acknowledgements to the values
it supplied, and checks intent acknowledgement separately before requesting commit.
This file injects neither intent-insert failures nor changed intent acknowledgements;
that boundary is not added to its coverage merely because a normal start traverses
it. The inspected
[attempt store](../src/control_plane_kit_operations/postgres/effect_attempt_store.py.md)
shares the caller's transaction and does not commit its own insertion.

Class-method patches and inherited observation guards are restored by mock.patch
contexts. They affect all instances of the patched class while active and provide
no concurrent-patch isolation. Error assertions use the fixture's 512-character
str/repr limit, absent chaining and selected nonempty canaries. They do not inspect
logs or tracebacks. Direct setup mutations, synthetic attempt seeding and snapshots
are supporting test mechanisms, not normal operator workflows or evidence of live
deployment recovery.

Read depth: the complete 592-line suite, both connection wrappers and every local
callback were read. Full start fixture416, start interpreter431 and actual
PostgresUnitOfWork context was retained/refreshed. Selected real plan/event reads,
attempt storage and service eligibility/replay/error/write boundaries were checked.
The full execution/history stores and neighboring suites were not reviewed for
this slice. Validation was documentation-only: local links, whitespace and frozen
source comparison. No application imports, tests, database/provider calls,
credential access, source/inventory edits or publication were performed.
