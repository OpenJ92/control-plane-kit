Source: [control-plane-kit-operations/tests/test_postgres_effect_attempt_fold_first_replay.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_fold_first_replay.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These eleven PostgreSQL tests exercise initial folds, exact replay, bounded
failure projection, one legacy failure shape and selected call ordering. They
inherit the [fold fixture](postgres_effect_attempt_fold_fixture.py.md) through its
[guarded observed extension](../../../../control-plane-kit-operations/tests/postgres_guarded_observed_effect_fold_fixture.py).
The fixtures seed synthetic approval/run/attempt truth and typed execution or
observation outcomes, then call actual stores/services. No provider runs to
produce those outcomes, and this documentation review executed no tests.

The first test covers seven direct/recovery string stories in normal and
compensation modes: fourteen cases. Each must return NewlyFolded, consume exactly
the fixture-derived event/observation IDs, preserve the original start event and
produce the expected state, event ID, next ordinal, event kind, state-commitment
evidence and failure. A wrapper delegates to the real lease observer and requires
one observation whose timestamp equals the new event time. Typed current-attempt
read-back must match and the selected run's event count must grow by one.
Direct cases compare the returned outcome record with the fixture's expected
record; recovery cases require None.

Those expectations reuse the production core fold, state fingerprint and
observation bridge. They preserve explicit integration equalities, but are not
independent implementations of those laws. This method does not independently
read every returned outcome/observation field from SQL. The fixture's uncertain
recovery seed is a direct event/CAS construction, not an earlier provider result
fold executed by the service.

The non-advancement test asserts twenty execution/observed catalogue stories and
adds six recovery worlds, then requires NewlyFolded and unchanged selected
plan/request/run/workspace/authored-graph/projection snapshots in all 26 worlds.
Observed story objects route through execute_observed with the guarded extension's
intent and optional runtime-authority registration. That registration writes
test-database authority data; it does not access a Docker socket or authenticate
a provider. The snapshot excludes those authority rows and effect history.

A separate successful case makes the exclusion concrete: the non-advancement
snapshot remains equal while the result carries two endpoint observations and
SQL reports one outcome row, two outcome-membership rows and two observation rows.
Thus non-advancement concerns the represented program/graph fields, not absence
of all database mutation. The snapshot is assembled from multiple queries and
does not prove global isolation, unchanged omitted columns or no external effects.

The exact restart-replay test first commits each of the seven string stories,
then constructs another service with a tracked ID sequence. A sentinel forbids
the lease-observation method; replay must equal ExistingFold with the first
attempt/outcome record, allocate no IDs and leave the selected attempt snapshot
unchanged. The test does not repeat compensation mode here. Restart means a new
service and inherited unit-of-work connection over stored truth, not a process or
database-server restart. The sentinel monitors that observation entry point,
not every possible clock API; the snapshot is not a complete write audit.

The actual [interpreter](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold_interpreter.py)
checks request/run/attempt ownership before distinguishing replay from new state.
Replay compares failure meaning and, for direct outcomes, loads the persisted
outcome and compares its workspace, attempt and complete outcome. It returns an
ExistingFold and still completes a unit of work with locks/reads. New folds read
protected intent and database lease observation, construct the complete result,
then write history/outcome/observations and attempt CAS. No snapshot-equality test
should be read as forbidding the replay path's locks or transaction completion.

_current_failed_command builds a failed runtime result with a specific namespaced
secret-resolution code plus raw message/detail sentinels, wraps it as typed
execution outcome and derives transition/failure through the actual
[outcome owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_outcome_evidence.py).
The current-projection test requires exactly three nested effect_outcome fields:
profile, outcome_fingerprint and runtime_failure_code. It compares event failure,
typed current attempt and a separately loaded outcome record to the fold result.
The exact details mapping excludes the raw message/payload from that projection;
the test does not scan every stored byte or log for those sentinels.

_persist_legacy_failed_projection instead constructs failure details containing
only profile and outcome_fingerprint, keeping the current outer category/code/
message. It computes the next state with the core fold, samples the actual lease
observer for the event timestamp, and writes event, outcome record and attempt
CAS in one committed unit of work. This produces selected legacy-shaped evidence
through present constructors/stores; it is not an imported historical database,
migration or provider execution.

The legacy replay test sends the current command against that stored shape and
requires the exact persisted ExistingFold, no ID consumption, no lease observation
and an unchanged snapshot. The inspected compatibility check accepts either exact
current failure equality or a precisely reconstructed legacy failure when the
command carries the current outcome-derived failure. The outcome record validator
also admits those two shapes. Replay preserves the original two-field evidence;
it does not normalize or rewrite it into the current projection.

Six legacy near-misses alter one stored failure dimension: extra details key,
wrong profile, wrong outcome fingerprint, outer category, outer code or outer
message. Each SQL update must affect one row. The test snapshots selected truth
plus that exact failure JSON, then requires EffectAttemptFoldConflict with the
replay-error message, bounded chain-free/canary-safe rendering, no IDs and no
lease observation. Both snapshots must remain unchanged. These are selected
compatibility boundaries, not every possible old evidence shape or arbitrary
legacy-data repair.

The different-status replay test folds failed and submits succeeded/unsupported
commands. Both must conflict before the observation sentinel, preserve snapshots
and leave the current attempt equal to the first result. It invokes the safe-error
helper but does not assert the exact replay-error message or inspect an ID
sequence in this method; the service is merely given a must-not-allocate label.
Despite the test title, it does not independently vary every failure field.

The same-status test has four cases: execution success changed to observed
success, and recovered success with changed decision ID, uncertain fingerprint
or evidence fingerprint. Each must give the exact replay conflict, no tracked
IDs, no observation and unchanged snapshots. The first case submits the observed
command through unguarded execute. The inspected interpreter rejects that profile
at its guard boundary before complete replay comparison, so this case alone does
not prove rejection of every changed same-profile outcome. The recovery cases
retain resolution and attempt identity; the title's every-coordinate wording
does not make those separately varied cases.

Safe-error assertions inherited here require absent cause/context, combined
str/repr at most 512 characters and absence of supplied nonempty canaries.
assertRaises/IsInstance checks generally admit subclasses unless an exact type
comparison is explicitly made. Different tests apply different subsets of these
guards; their titles are not broader executable guarantees.

The ordering test instruments actual request/run/attempt reads, lease observation,
ordinal allocation, IDs, event append, observation writes, outcome insert and CAS.
For ordinary success its exact ledger is request, run, attempt, request, clock,
ordinal, three IDs, event, two observations, outcome, CAS. The second request entry
comes from the actual observer's get_request_for_update before clock_timestamp.
One observed timestamp must equal the result event time. Wrappers delegate to
real store methods, but the ledger does not capture every SQL call, intent read,
lock wait or commit. It establishes this one path's selected method order, not
concurrency serialization or a universal order for all stories.

The final test wraps NewlyFolded.__post_init__, calling the real validator first,
and records event append and CAS. At event append exactly one complete planned
result must already exist with the same event. At CAS its planned attempt must
equal the replacement and the observed attempt must equal the seeded current
record. The ledger must be result, event, CAS. Observation and outcome writes
between the latter calls are not instrumented here, so that short ledger does
not assert their absence. The actual _plan_result creates the attempt/outcome
record and validated NewlyFolded before returning to the write phase.

The [outcome store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_outcome_store.py)
read used by replay and the current-projection test loads the selected transition
and its observation memberships before reconstructing evidence. The
[unit of work](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
supplies transaction completion; this file does not inject late write failures
or lost commit acknowledgements. Rollback, exhaustive guard failures, corruption
coverage and concurrency belong to separate test owners.

Security/operational evidence is bounded failure projection, narrow legacy
acceptance, selected exact replay, guard routing and preserved program/graph
fields alongside new history. Constructed secret references/outcomes confer no
live authority and no provider calls occur. The tests do not establish deployment
restart/history acceptance, runtime cleanup or an absence of all stored sensitive
material from their selected snapshots.

Read depth: all 729 source lines, eleven tests and two helpers; retained full fold
fixture review plus selected guarded fixture, actual replay/new-write and result
planning paths, current/legacy failure contracts, outcome read, lease observer
and unit-of-work contracts. Other test owners are not credited with full review.
No tests, application imports, database connections, source edits or provider
actions were executed during documentation authoring.
