Source: [control-plane-kit-operations/tests/test_postgres_guarded_observed_effect_fold_first_replay.py](../../../../control-plane-kit-operations/tests/test_postgres_guarded_observed_effect_fold_first_replay.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These six PostgreSQL tests cover selected ordinary/guarded folds, terminal replay,
claim-precedence rejection and malformed stored-outcome returns. Two additional
matrix helpers are called from the precedence test; _forge_exact constructs
invalid exact-type adapter returns without constructor admission. The
[guarded PostgreSQL fixture](postgres_guarded_observed_effect_fold_fixture.py.md)
provides real store setup with synthetic intent, observation and authority data.
No provider performs or observes the represented runtime effect, and no tests or
database setup were executed during documentation authoring.

The first control seeds a guarded start and reads back its exact intent record.
It then calls persist_terminal, which resets the fixture again, builds a terminal
state/event/outcome and writes event, endpoint observations, outcome and attempt
CAS in one unit of work. The outcome is read back by identity and latest event ID;
the final intent-table count must be one. These are two successive arranged worlds,
not a single start-to-terminal service execution. persist_terminal uses the real
stores and core fold helper but does not invoke execute_observed to produce the
terminal result. The count concerns the second world after its reset, and is not
an exhaustive schema inspection or concurrent uniqueness proof.

The ordinary compatibility control covers succeeded and recovered-succeeded.
Its observation wrapper calls the real lease observer and replaces only the
returned expired flag with True. Each fresh unguarded execute must return
NewlyFolded; each subsequent replay, under a lease-observation sentinel, must
return ExistingFold. The test does not expire the database claim here, compare
the exact replay value, count IDs or take snapshots. Broad Exception catches
turn unexpected failures into fixed unittest failures after leaving the handler;
they do not establish a general service error-redaction policy.

The terminal replay matrix has eight ordinary-phase worlds: execution/observed
profiles, original/newer current claim and unexpired/expired stored lease. Each
uses persist_terminal, optionally rotates the current claim to worker-b/generation
8, optionally sets its expiry to a fixed date in 2000, then supplies matching
current worker/fence values. Execution outcomes use execute; observed outcomes
use execute_observed with a constructed guard. Every call forbids lease
observation, returns exactly ExistingFold(attempt, outcome) and leaves the retained
ID sequence empty. This demonstrates selected terminal replay under a later
lawful claim or expired-but-current claim, not acceptance of stale authority.
It does not query an aggregate snapshot or assert every store method is unused.

Guard construction is not always pure in this fixture. By default it registers
local Docker authority through a separate unit of work before the service call.
The actual [authority store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/runtime_authority_store.py)
inserts a registration or reuses an active registration when its runtime kind
and concrete authority match the candidate. Its get_active_for_update separately
queries active workspace/reference rows FOR UPDATE and requires exactly one valid row.
These are durable test registrations containing synthetic data, not proof of
Docker access, live authentication or resolved secret references. Replay setup
can register authority even though the replay service branch does not reread it.

The actual [interpreter](../src/control_plane_kit_operations/effect_attempt_fold_interpreter.py.md)
locks request, scoped run and attempt, checks their relationships and requires
the exact current claim before guarded-entry or transition decisions. An exact
terminal fold then validates failure meaning, reads its outcome record and
reconstructs ExistingFold. That branch precedes the fresh-fold intent lookup,
lease observation and active-registration lookup. Supplied guard construction
and execute_observed still validate the guard's shape and correlations before
the transaction. The replay tests explicitly forbid lease observation, while
the broader skipped-lookup explanation comes from this source path.

Four authorized precedence rows request an observed success over a recovery
predecessor, over an execution-profile terminal success, under a lower current
generation or under a current equal-generation foreign worker. All require the
exact incongruent-fold conflict and unchanged complete_snapshot. The recovery
label calls seed_fold_source("recovered-succeeded"), which persists an UNCERTAIN
predecessor; it does not commit a recovery decision in that row. This therefore
tests an incompatible direct transition from uncertainty, not a direct rewrite
of an already recovered result. The lower/equal-foreign rows make the command
match the current claim before checking its historical attempt lineage.

The cross-profile case also differs in outcome fingerprint, so its conflict need
not isolate a later stored-outcome profile comparison: the core fold can reject
the incongruent transition earlier. These four rows do not instrument ID calls,
lease access or exact lower-store call order. Guard construction occurs after
the snapshot and can register authority, which the snapshot does not include.
The fixture's complete_snapshot combines selected attempt/history/intent/outcome
values and non-advancement projections. It uses multiple queries, omits runtime
authority rows and full observation/outcome payloads, and includes counts that
can miss in-place changes. Its name does not make it a full atomic database image.

The stale-claim helper covers four arranged worlds—uncertain recovery predecessor,
matching observed terminal, execution terminal and fresh start—with two stale
claimants each: worker-a/generation 7 and worker-c/generation 8, against current
worker-b/generation 8. It registers authority before installing sentinels and
passes register=False when constructing the guard. Every stale request must give
the fixed authority denial and allocate no IDs before reaching any forbidden
lower interaction. This isolates denial precedence over the selected underlying
state differences and includes an equal-generation wrong-worker case.

The [fixture's lower-interaction sentinels](../../../../control-plane-kit-operations/tests/postgres_guarded_observed_effect_fold_fixture.py)
cover outcome/intent reads, lease observation, active authority lookup, event
append, observation put, outcome insert and attempt CAS. They do not forbid
request/run/attempt reads, ordinal allocation, unit-of-work creation or cleanup.
The stale matrix asserts category/message and ID calls, not safe-error bounds,
cause/context or snapshots. Its confidentiality claim is this selected rejection
precedence and fixed response, not a side-channel or timing analysis.

The unguarded-observed-entry helper covers fresh/terminal observed values with
current/stale claims. Fresh setup obtains the observed fold through guard
construction, then deliberately passes only its fold to execute. With the same
lower-interaction sentinels active, it requires exact Conflict/REPLAY_ERROR for
current authority or exact Denied/AUTHORITY_ERROR for stale authority. It also
requires empty cause/context, exactly one completed locked-request read and no
ID allocations. The request wrapper records after the real read returns. These
assertions establish the selected current-authority-before-entrypoint-rejection
order while permitting run/attempt lookup; they do not prove only one SQL query
or the absence of every other effect. No rendering-length or snapshot assertion
is added in this helper.

The durable-evidence matrix has twelve cases: execution and observed terminal
profiles, each with six patched outcome-store returns. Missing/corrupt inject
KeyError or OperationsRecordError. Foreign changes workspace, drifted substitutes
a different failed attempt, incomplete drops an endpoint observation and reordered
reverses the observation tuple. _forge_exact shallowly copies candidate dataclass
fields onto an uninitialized object of the same type, bypassing validation.
The drifted attempt differs along several coordinates/state fields, so it is not
an isolated status or identity-law case. These are adapter-return injections,
not malformed SQL rows or direct exercises of each store decoder.

Every evidence fault must escape as exactly EffectAttemptFoldConflict with the
invalid-truth message. The actual replay path checks record workspace/attempt,
then the [fold result validator](../src/control_plane_kit_operations/effect_attempt_fold.py.md)
reconstructs the aggregate. Its [outcome record](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_outcome_evidence.py)
requires observation count and positional correspondence, explaining the missing
and reversed-row failures. Despite the test title's before-lower-effects wording,
this matrix installs no lower-interaction sentinels, inspects no ID sequence and
takes no snapshot. It proves categorical rejection of these returns, not an
independent no-write or no-clock claim. Cause/context and bounded rendering are
also not checked in this matrix.

The final fresh-fold matrix uses all twelve inherited observed stories across
ordinary and compensation phases. Each seeds an intent-consistent started
attempt, obtains a durable registration through guarded-command construction,
executes the guarded service and requires NewlyFolded, exact consumption of one
event ID plus positional endpoint-observation IDs, and equality of the returned
outcome with the fixture's expected outcome. There is no explicit count assertion
for the catalogue, full fresh-connection aggregate read-back, registration-lookup
spy or database-clock count in this method. It exercises the successful service
path for the catalogue rather than independently asserting every internal step.

On that fresh path, inspected source compares stored intent to both the locked
attempt and supplied guard, requires lease-observed request equality and rejects
expired guarded observations, then checks the locked active registration when
one is required. It plans and writes the event/observations/outcome/attempt in the
caller-owned transaction. Fresh expired-guard denial, revoked/changed-registration
denial, rollback and concurrent arbitration need their own tests; the ordinary
expiry control and terminal replay acceptance do not cover those failures.
The inherited fixture owns table reset and connection cleanup. This file adds
no runtime resource lifecycle or restart test.

Read depth: all 399 source lines, six tests, both matrix helpers and _forge_exact;
full 295-line guarded PostgreSQL fixture; retained full parent fold-fixture and
fold-owner context; actual intent/outcome replay and fresh guarded branches,
registration insert/lookup, result/outcome reconstruction and snapshot scope.
No tests, application imports, database connections, source/dependency changes,
credentials, Docker or provider actions were executed while authoring this note.
