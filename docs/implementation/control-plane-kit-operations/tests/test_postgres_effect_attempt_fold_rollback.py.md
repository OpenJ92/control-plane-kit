Source: [control-plane-kit-operations/tests/test_postgres_effect_attempt_fold_rollback.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_fold_rollback.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These seven PostgreSQL tests exercise selected fold failure seams, adapter
acknowledgement mismatches, exact replay and visibility before commit. Every case
uses the ordinary succeeded story from the
[fold fixture](postgres_effect_attempt_fold_fixture.py.md), with two endpoint
observations. They do not repeat compensation, guarded provider-observation or
recovery modes. Setup constructs the database lineage and typed outcome; no
provider executes the represented effect, and these tests were not run during
documentation authoring.

The first matrix injects a specific RuntimeError at lease observation, ordinal
allocation, event append, observation put, outcome insert and attempt CAS. Each
must escape as the same exception object and leave the fixture's attempt snapshot
equal to its pre-call value. A separate ID-factory case throws its exact supplied
RuntimeError and checks the same invariance. These identity assertions show the
selected injected failure was reached rather than an unrelated earlier error.
The mocks raise before invoking the replaced method, so they do not execute that
method's own SQL. Observation and ordinal failures also occur before history
writes, despite the test's late-failure title.

_CommitFailureConnection delegates ordinary attributes to a real psycopg
connection but overrides commit to raise the supplied error without calling the
underlying commit. The commit-failure test uses this wrapper in a real
PostgresUnitOfWork. It requires the same RuntimeError, exact consumption of the
event plus two observation IDs, and an unchanged snapshot. This proves the
represented rollback path when commit is prevented from reaching PostgreSQL.
It does not simulate a server-committed transaction whose acknowledgement was
lost, uncertain commit outcome, reconnect/adoption logic or automatic retry.

The actual [unit of work](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
binds stores to one connection and treats commit as a request until successful
context exit. An exception before then rolls back; an exception from connection
commit triggers a rollback attempt and the outer finally closes the connection.
The test wrapper delegates those cleanup operations normally. There are no
rollback/close failure injections, cleanup call-count assertions or guarantees
that an original error survives a separate cleanup failure.

The lost-CAS test replaces compare_and_set with None and requires the categorical
serialization conflict, bounded chain-free rendering and an unchanged snapshot.
This is an adapter-level loss simulation: it does not produce a real competing
transaction or run the actual conditional UPDATE at that seam. The actual
[attempt store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_attempt_store.py)
returns None when its complete-prior-state UPDATE matches no row, which explains
the service's expected reaction.

The changed-return matrix is stronger about writes already performed. Each
wrapper calls the original adapter and then lies about its acknowledgement:
event append returns a changed event ID, the first or second observation returns
a changed observation ID, outcome insertion returns None, and CAS returns the
old record after calling the real CAS. Every case must give the exact serialization
error, pass safe-error checks for the return canary, consume all planned IDs and
restore the selected snapshot. These cases exercise rollback after real selected
writes, including the outcome aggregate or attempt update at the later seams.
They do not exhaust every malformed return type or acknowledgement field.

The actual [interpreter](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold_interpreter.py)
requires exact acknowledgement types and equality at each step. It appends the
event, writes endpoint observations, inserts the outcome aggregate and then CASes
the attempt; a mismatch stops progress with the serialization conflict before
commit is requested. The
[observation store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/observed_state.py)
writes one row per put, while the
[outcome store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_outcome_store.py)
inserts the outcome and its ordered observation-membership rows on that same
caller connection. No compensating provider operation is used for this rollback.

The observation-position test independently fails the first put, second put or
outcome insert. Non-target puts delegate to the real store. In the second-position
case the first observation has therefore already been written; in the outcome
case both puts complete before the injected insert error. Exact error identity
and snapshot equality verify those selected paths. The local counter controls
which call fails but is not itself asserted afterward, and this method does not
inspect ID consumption or inject a failure halfway through an outcome's internal
membership inserts.

The exact-replay test first commits one successful fold. For replay it forbids
lease observation, ordinal allocation, event append and CAS with one AssertionError
sentinel, then requires the expected ExistingFold, no ID calls and unchanged
snapshot. It does not separately sentinel observation put or outcome insert;
their represented state is covered by the snapshot. Replay still uses the
service's current-authority reads/locks and unit-of-work completion. This test
does not assert absence of every SQL command, all clock APIs or transaction work.

The final test records actual adapter order as event, observation, observation,
outcome, CAS. Before the real CAS executes, a separate connection must still see
the old attempt status/latest-event ID and no row for the candidate event. Because
the event append has already executed, its invisibility is meaningful evidence
that this pending write has not committed separately. The old attempt is also
expected because the real CAS has not run yet.

The outcome wrapper opens another observer before invoking the original insert
and requires an outcome count of zero. That is a baseline check before the write,
not evidence that an already inserted outcome is invisible to other connections.
The test does not query observation rows, inspect outcome visibility after insert,
observe after CAS but before commit or compare connection identities directly.
Actual shared-connection unit-of-work source supplies the broader transaction
context; the explicit visibility assertion isolates the appended event. After
the call, the test checks NewlyFolded and adapter order rather than a complete
fresh-connection read-back of the committed aggregate.

All rollback snapshots are projections from the inherited fixture: selected
request/event/action/run values, attempt columns and protected intent rows, plus
graph/projection/observation counts and selected outcome/link columns. They are
multiple queries, not a transactionally consistent full-database snapshot.
Counts can miss in-place changes; outcome preimages and observation content are
not compared exhaustively. Equality supports the represented rollback laws but
does not establish absence of all effects or changes to omitted state.

Conflict cases use the inherited helper requiring no cause/context, combined
str/repr at most 512 characters and exclusion of supplied nonempty canaries.
Raw RuntimeError cases instead require identity and deliberately allow the raw
canary-bearing error through; they are not secret-redaction tests. Mock patches
are scoped to their contexts. The inherited fixture handles test-table reset
and connection cleanup; this file adds no live-resource cleanup behavior.

Security and operational evidence is rejection of untrustworthy write returns,
rollback of selected history/outcome writes, no new mutation work at the named
replay seams and one pre-commit visibility witness. No credentials are resolved,
provider effects executed or live deployment restarted. The tests do not prove
ambiguous-commit recovery, concurrent CAS arbitration or every cleanup failure.

Read depth: all 332 source lines, seven tests and the commit wrapper; retained
full fold-fixture review; actual unit-of-work exception paths, interpreter write
acknowledgements, event/observation/outcome/CAS store behavior and snapshot scope.
No tests, application imports, database connections, source changes or provider
actions were executed while authoring this companion.
