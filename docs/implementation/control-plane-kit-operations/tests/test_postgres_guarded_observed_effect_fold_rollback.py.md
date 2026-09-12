Source: [control-plane-kit-operations/tests/test_postgres_guarded_observed_effect_fold_rollback.py](../../../../control-plane-kit-operations/tests/test_postgres_guarded_observed_effect_fold_rollback.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These four PostgreSQL tests exercise guarded-fold lease-observation precedence,
ID planning before the first event write, selected acknowledgement mismatches,
raw write faults and a pre-commit connection failure. Their
[guarded fixture](postgres_guarded_observed_effect_fold_fixture.py.md) seeds real
database records from synthetic observation/authority values. No provider produces
those observations, and no tests or database setup were executed to author this note.

The lease test has before/equal/after labels with fixed 2099 timestamps. Its
wrapper first calls the real lease observer, records that returned value, then
replaces observed_at and expired with the selected timestamp and boolean. Before
uses False; equal and after use True. The test does not change the stored expiry
to establish those comparisons or independently compute expiry from the chosen
timestamps. It proves how the service consumes an observed expiry decision, not
the database clock's actual boundary arithmetic.

Each lease world pre-registers authority, supplies it explicitly and sets
register=False, avoiding another registration during command construction. An
active-selector replacement records workspace/reference and returns that supplied
registration; create=True permits the patch even if the method were absent.
Exactly one completed real lease observation is required. Denied worlds require
EffectAttemptFoldDenied with the fixed authority message and zero active-selector
calls; the allowed world requires NewlyFolded and exactly one matching lookup.
The must-not-allocate ID label is not an allocation spy: this method neither counts
IDs nor snapshots rollback, and does not inspect the resulting event timestamp.

The actual [execution-store observer](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
relocks the request, requires a claim and obtains clock_timestamp with the SQL
comparison lease_expires_at <= observed_at. The
[fold interpreter](../src/control_plane_kit_operations/effect_attempt_fold_interpreter.py.md)
requires the observed request to equal its initial locked request, rejects an
expired guarded fold before active-authority lookup and only then plans writes.
That source explains the ordering; these three mocked timestamps do not prove
clock freshness at commit, concurrency behavior or every request-equality branch.

The planning test selects observed_stories()[0] and [2], not all twelve stories.
It supplies a recording Sequence of one event ID plus the fixture's observation
IDs. Its add_event replacement records the candidate, requires its exact
ActivityEventRecord type and that the complete ID sequence was already consumed,
then raises first-write-canary without calling the real event writer. The caller
requires that RuntimeError message, exactly one intercepted candidate and an
unchanged complete_snapshot.

Despite the method's complete-result title, its direct constructor witness is
only the event type and completed ID allocation. It does not spy on each result,
attempt or outcome constructor. The actual interpreter's _plan_result constructs
and validates the event, attempt, endpoint observations, outcome record and
NewlyFolded before returning to add_event. Thus full-result-before-write is visible
in source; the test's independently asserted boundary is narrower. This injected
event fault is before that writer's SQL, not a post-insert rollback witness.

The acknowledgement matrix targets event, observation positions 0 and 1, outcome
and CAS. Every replacement first calls the real corresponding writer, then returns
None for the selected target and the supplied value otherwise. Observation
positions are callback counters, not the fixture's one-based ID label suffixes.
Each world must raise EffectAttemptFoldConflict with the changed-concurrently
message and restore the selected snapshot. Unlike the planning fault, these are
post-write bad acknowledgements and therefore exercise rollback after real writes.

The interpreter checks exact acknowledgement type and equality, stops later
writes on mismatch and raises the fixed conflict before requesting commit. This
file exercises None returns only: it does not independently distinguish wrong
exact types, unequal lawful records, hostile equality or every later call being
absent. Nor does it count IDs or assert acknowledgement exception cause/context.
The [atomic fold tests](test_postgres_atomic_effect_attempt_fold.py.md) supply
separate richer construction/hostile-acknowledgement witnesses.

Four raw-fault worlds patch add_event, observation put, outcome insert or CAS
with a stored RuntimeError side effect. They require the identical exception
object and snapshot equality. The selected method's real body is not called:
event fails before any event insertion; the later stages can follow earlier real
writes, but the observation fault occurs on the first put rather than enumerating
every endpoint position. These differ from the preceding real-write-then-None
matrix. They do not claim that arbitrary raw errors are sanitized or converted
into the fixed conflict category.

The final world supplies a fresh psycopg connection wrapped by
_CommitFailureConnection. Attribute access delegates to the real connection, but
commit raises the stored error without calling the underlying commit. The real
service and store bundle therefore perform their work, consume all expected IDs
and request commit; UoW exit encounters the injected failure. The caller requires
the identical error and unchanged snapshot. This is a failure before physical
commit, not a server-side committed-but-unacknowledged outcome, process crash,
network disconnect or proof that an ambiguous commit can be rolled back.

The complete [Postgres UoW](../src/control_plane_kit_operations/postgres/unit_of_work.py.md)
marks commit requests, performs physical commit on successful exit, rolls back
when that commit raises BaseException, rethrows and closes in finally. The wrapper
delegates rollback and close, so this arrangement exercises that actual path.
This test does not inject rollback/close failures, instrument their exact counts
or establish preservation of the primary error if either cleanup operation fails.
No retry, recovery policy or external compensation is introduced or tested.

Snapshot scope matters in all three failure tests. complete_snapshot combines
the [parent fixture's](postgres_effect_attempt_fold_fixture.py.md) selected
attempt/history/intent/outcome data and non-advancement projections. It omits
runtime-authority rows and full observation/outcome payloads, and some checks use
counts rather than complete row values. Default guarded-command construction can
register authority in a separate UoW after the before snapshot; that registration
can survive the later fold rollback without contradicting these assertions.
Only the lease test avoids that implicit registration by passing an existing
authority with register=False. Snapshot equality is therefore not a claim that
every database row, connection operation or side effect was unchanged.

Read depth: all 236 source lines, four tests, every nested writer/observer and the
commit wrapper; retained full guarded fixture, fold interpreter and UoW; refreshed
actual planning/write/lease SQL paths and inherited snapshot, checked-service and
Sequence helpers. No application imports, tests, database connections, source or
dependency edits, credentials, Docker or provider actions were executed.
