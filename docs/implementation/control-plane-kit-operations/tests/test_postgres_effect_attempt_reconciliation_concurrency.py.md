Source: [control-plane-kit-operations/tests/test_postgres_effect_attempt_reconciliation_concurrency.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_reconciliation_concurrency.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These three PostgreSQL tests combine a synchronous mutation during observation,
two-worker result races and reconciliation while an unrelated attempt row is
locked. The
[reconciliation fixture](postgres_effect_attempt_reconciliation_fixture.py.md)
provides real persistence, synthetic authority/observation values and actual
reconciliation/fold services. The tests do not contact a runtime provider or
resolve secret bytes. Their schedules and assertions differ substantially;
the concurrency filename does not make every case a forced parallel interleaving.

_MutatingObserver calls its supplied mutation before delegating to RecordingObserver.
The mutation therefore runs before the observer records its call or returns its
stored result. If mutation fails, the parent observer does not record a call.
This helper creates a deterministic callback seam within one reconciliation
invocation, without another thread, event, barrier or ledger.

_results creates one worker per supplied callable, submits every callable, then
reads futures in submission order. Each result(timeout=20) has its own timeout
starting when that result is requested, not one shared twenty-second deadline.
FoldConflict, FoldDenied, ReconciliationConflict and ReconciliationDenied are
collected as values. Other exceptions, including timeout, propagate. The executor
context waits for running work on exit, so timeout does not cancel a blocked
database call or bound total test duration. There is no start barrier, observer
pause, lock-acquisition acknowledgement or assertion that both workers overlap.

The first test has replacement and expiry rows, both initially using an ordinary
observed-success story and local runtime authority. In the expiry callback a
separate unit of work updates request-a's lease expiry to a date in 2000 and
commits. In the replacement callback one unit of work revokes the current runtime
registration, then a separate helper transaction registers remote TLS authority
under the same reference; its registration ID must differ. This is replacement
of runtime-authority registration, not rotation of the worker claim or fence.
The revoke and replacement are not one atomic transaction.

Both callbacks complete before the synthetic observation returns and the default
actual guarded fold is invoked. Reconciliation must raise ReconciliationDenied.
The test then checks only that the current attempt's original start event and
the read-back intent record remain equal to their seeded values. It does not
assert the fixed denial message, cause/context, observer count, full attempt
state, latest event, authorization rows, ID use or a complete snapshot. Expiry
and authority mutations are intentional durable changes, not changes the test
expects to roll back.

Actual
[reconciliation source](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_reconciliation_interpreter.py)
first locks request, scoped run and attempt, validates the current claim and
historical lineage, then checks fresh lease/intent/authority truth. It exits that
initial unit of work before authorizing uses and calling the observer. The actual
[guarded fold](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold_interpreter.py)
opens a new transaction and rechecks claim, stored intent, lease expiry and
active authority against the captured guard before allocating/writing its result.
That ordering explains why these committed callback mutations can be detected.
The test forces this before/after seam, not a race between the callback and a
simultaneously running fold.

The ordinary-versus-observer test submits an unguarded fold and reconciliation
for the same freshly seeded STARTED attempt. For both succeeded and
recovered-succeeded labels, it requires one NewlyFolded result and one conflict
from either fold or reconciliation. Although _results can collect denials, they
do not satisfy this asserted pair. There is no persisted winner read-back,
outcome/event count, observer-call assertion or after-snapshot in this test.
The winner language refers directly to returned categories backed by actual
services, not an independent durable-row inventory.

The recovered-succeeded row does not seed an UNCERTAIN predecessor. Its inherited
fold_command builds a RECONCILED transition with a recovery decision against the
same STARTED truth used by the observed-success contender. The actual
[core fold](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/recovery.py)
requires UNCERTAIN state for a nonduplicate recovery transition. This row can
therefore reject an ineligible recovery command independently of any overlap;
it is not evidence that two eligible original/recovery writers competed for one
uncertain attempt.

The final test first submits identical observed-success reconciliations, then
success-versus-failed reconciliations. Identical observers must yield exactly one
NewlyFolded and one ExistingFold; incompatible observers must yield exactly one
NewlyFolded and one ReconciliationConflict. Both use the same command and current
claim, with observer/service instances constructed separately. No barrier forces
both initial reads to see STARTED, and the test does not count observer calls,
allocated IDs, authorization rows or terminal outcome rows.

There is a source-level scheduling limitation in the incompatible case and the
ordinary-success race. If a contender completes its fold before reconciliation's
initial read, reconciliation can take its terminal ExistingFold branch without
calling the configured observer. That branch reads the stored outcome rather
than comparing a hypothetical result the observer would have returned. In
particular, late incompatible reconciliation may replay the winner rather than
produce the required conflict. The tests do not force the overlap needed to
exclude that schedule. This is a limitation identified by source inspection,
not an observed failure from executing these tests. The identical-result assertion
can also pass when the two calls effectively run serially.

The actual services serialize relevant row reads with FOR UPDATE. Each inherited
unit_of_work creates a fresh PostgreSQL connection; no statement/lock timeout is
explicitly set by that factory. Required secret uses are authorized outside the
initial read transaction and before observation. The authorization service checks
current admission, while the
[secret-use store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/secret_provider_store.py)
uses a transaction advisory lock for the workspace/correlation key. Congruent
authorization evidence can be reused and each authorize_resolution commits
separately. These inspected boundaries explain possible serialization and retained
audit evidence, but the result races do not verify authorization deduplication
or exact database-lock acquisition order.

The unrelated-lock section creates another plan/request/run in the same fixture
workspace, persists a STARTED run-foreign attempt, and takes a before
non_advancement_snapshot. A separate connection executes FOR UPDATE for that
foreign attempt before the one-worker executor submits reconciliation of run-a.
This is a forced overlap: the blocker holds its transaction open while the main
attempt must finish within future.result(timeout=5). The query result is not
fetched or asserted, though the foreign row was previously persisted and checked
by the setup helper.

On timeout the test raises its fixed unittest failure. Finally it rolls back and
closes the blocker, then calls executor.shutdown(wait=True, cancel_futures=True).
Releasing the foreign lock can allow blocked work to finish during cleanup;
cancelling futures does not stop an already running database operation. The
five-second wait is therefore an assertion window, not a hard shutdown deadline.
Cleanup calls are sequential without nested finally protection, so a rollback or
close exception can prevent later cleanup calls. No separate watchdog or database
timeout is installed here.

After the foreign lock is released, the result must be NewlyFolded, the foreign
attempt read back through the actual store must equal its original value, and
non_advancement_snapshot must remain equal. This establishes completion while
that selected unrelated row was locked and unchanged foreign attempt truth.
It does not prove independence from every workspace/request/authority lock or
unchanged foreign intent/event/outcome data. The snapshot selects surrounding
plan/request/run/workspace/graph/projection state, omitting authorization and
registration rows, attempt history and observation payloads; it is a multi-query
selection rather than a full atomic database image.

All cases use ordinary-phase observed success except the explicitly incompatible
failed observer; they do not cover compensation, claim takeover, all observation
variants, arbitrary schedules, repeated contention, deadlock recovery or restart.
Fixture seeding, secret admission and the explicit callback/foreign-row operations
mutate real test state. Inherited reset/teardown cleans tables, so execution belongs
in the isolated PostgreSQL test context. No live resource or credential access is
required to author this companion.

Read depth: the complete 228-line source, all three tests and both coordination
helpers were read, alongside the complete reconciliation fixture and relevant
authority-registration, foreign-run/attempt, fold-command, snapshot and connection
helpers. Selected actual reconciliation/replay, current-claim/guarded-fold, core
recovery, authority-store, authorization and unit-of-work paths were checked.
This is not full-owner or adjacent-suite coverage. Validation was limited to
local links, whitespace and frozen-source comparison; no application imports,
tests, database/provider calls, credential access, source/inventory changes or
publication were performed.
