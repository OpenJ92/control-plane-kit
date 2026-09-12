Source: [control-plane-kit-operations/tests/postgres_effect_attempt_fold_fixture.py](../../../../control-plane-kit-operations/tests/postgres_effect_attempt_fold_fixture.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 476-line fixture builds PostgreSQL worlds and assertions for direct-result,
observed-result and recovery folds. It defines helpers rather than test methods.
Its seven string stories comprise succeeded, failed, unsupported and uncertain,
then recovered-succeeded, recovered-failed and abandoned. Error constants are
expected categories for consumers, not automatic assertions or error translation
performed by every helper.

PostgresEffectAttemptFoldFixture combines
[outcome-evidence helpers](../../../../control-plane-kit-operations/tests/effect_outcome_evidence_fixture.py)
with the [PostgreSQL start fixture](../../../../control-plane-kit-operations/tests/postgres_effect_attempt_start_fixture.py).
It adds no setUp/tearDown. Inherited setup reaches the recovery fixture's required
test database, schema installation and TRUNCATE cpk_workspaces CASCADE, then seeds
constructed request/run history. The start layer sets RUNNING or COMPENSATING
truth and supplies runtime-level intents, record persistence and separate unit-of-
work connections. Teardown truncates/closes the setup connection if open, without
a finally guaranteeing close after truncation failure. This is disposable-database
apparatus, not a live deployment or provider cleanup boundary.

Method resolution matters: the shared record fixture is reached through both
parent branches, while the start branch supplies worker authority, execution
fence and canonical attempt identity. Its overridden intent_for_attempt reads
request/plan from the run row when computing an attempt request fingerprint.
Consequently inherited outcome-story construction and some command/expectation
helpers can perform database reads; their apparent value-building shape does not
make them wholly pure. The authority and fence remain constructed values, not
authenticated credentials.

outcome_story prefixes unqualified names with execution-, accepts already prefixed
execution-/observed- names, and selects the first catalogue entry matching name
and compensation. Its default compensation comes from fixture state. Unknown
combinations raise StopIteration; it is not a general validated command parser.
The inherited stories method builds typed execution/observation examples and
their attempt commitments; no provider is invoked to produce them.

fold_outcome chooses an explicit story or the seed's selected story, returning
None if neither exists. It replaces the sample effect ID with effect-0-start or
effect-1-start and builds an ExecutionEffectOutcome with a current request
fingerprint, or an ObservedEffectOutcome from the sample observation. These are
typed evidence constructors, not proof that the represented effect happened.
fold_transition maps direct names or story objects through the actual outcome
transformation. Recovery strings instead create a decision tied to the generated
uncertain outcome fingerprint, with a fixed decision ID and all-d recovery
evidence fingerprint. They are constructed recovery evidence, not an operator
approval or an independent observation receipt.

fold_command supplies request-a, the selected transition, inherited authority and
fence, and either the actual outcome-derived failure or a synthetic terminal
failure for recovered-failed. Direct outcomes are attached; recovery commands
carry no direct outcome. Caller changes overwrite this dictionary before the
actual [FoldEffectAttempt](../src/control_plane_kit_operations/effect_attempt_fold.py.md)
constructor validates it. Prefixed strings accepted by outcome_story are not
interchangeable with every fold_command argument: direct/recovery dispatch uses
the fixed direct-name tuple or a story object. Arbitrary combinations may fail.

Service factories instantiate the actual
[fold interpreter](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold_interpreter.py)
with the inherited unit-of-work factory and a deterministic Sequence or supplied
ID factory. A single supplied event ID expands into that ID plus 64 possible
observation IDs. Other argument counts are used unchanged. Sequence records
consumption and raises when exhausted; this expansion does not assert that all
65 IDs should be consumed or define a universal observation limit.

_CheckedFoldService converts NotImplementedError into an explicit missing-work
test failure for each entry point. execute additionally recognizes two exact
TypeError strings for missing outcome_record result arguments and fails with an
atomic-outcome diagnostic; other TypeErrors are re-raised. execute_observed has
no corresponding TypeError special case. This is narrow test scaffolding, not
runtime exception normalization or a redaction guarantee.

execute_fold routes a provider-observation story object through
guarded_observed_command and execute_observed; other inputs use execute. The guard
helper is not defined by this fixture or its listed parents. The selected
[guarded consumer fixture](../../../../control-plane-kit-operations/tests/postgres_guarded_observed_effect_fold_fixture.py)
provides it, loading intent/current state and optionally registering runtime
authority before forming the guard. Thus the observed path needs that consuming
extension. Constructing an ObservedEffectOutcome alone does not bypass the
service's guarded observed-fold entry point.

seed_fold_source records compensation/selected-story state, resets start truth
and persists a matching STARTED attempt with its event and intent through the
inherited helper. For direct stories it returns that record. Recovery stories
first call _persist_uncertain: the actual core fold computes the next state,
the helper builds its state-fingerprint event and a replacement attempt, then
writes the event and compare-and-set in one unit of work. That scope asserts
adapter equality and requests commit. It does not call the fold service or write
a direct-outcome record/observation rows for this uncertain seed. The failure
message and 2030 timestamp are fixture values.

The actual [core fold](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/recovery.py)
checks identity/fence and requires uncertain state plus matching uncertain
fingerprint for recovery. expected_fold_state invokes that same function with
the current state and selected transition. It is a production-law oracle reused
by consumers, not a second independently implemented state machine.

fold_ids returns an event ID followed by one derived ID per endpoint observation,
or only the event ID when no outcome is selected. expected_outcome_record uses
those IDs, the actual observation bridge and fixed workspace-a to construct an
expected outcome record. It does not persist it. The
[bridge owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_outcome_evidence.py)
checks attempt/outcome commitments and observation-ID count/uniqueness. This
helper omits the optional protected intent_record; it should not be credited
with the intent-dependent verification-completion path that the service can use.
Its expected observation projection is also shared production logic.

replace_current_claim delegates a direct SQL fixture update of request-a's worker,
generation and far-future claim/expiry timestamps. It does not execute renewal
or takeover. current_attempt performs an actual typed store get in a unit of work
without requesting commit. persisted_event_count counts only run-a events.

The three snapshot shapes serve different comparisons. attempt_only_snapshot
selects attempt identity/status, outcome/recovery markers and latest-event
coordinates. attempt_snapshot extends inherited request/event/action/run and
attempt/intent snapshots with graph/projection/observation counts, selected outcome
columns and outcome-observation link rows. Counts cannot detect every in-place
change, and the selected outcome columns omit protected outcome content.
non_advancement_snapshot instead reads selected plan, request, run, workspace,
authored-graph and realized-projection fields, intentionally leaving effect
events, outcomes and observations outside that comparison.

These helpers use multiple queries on the setup connection, generally over
entire tables without pagination. They are not one atomic cross-table snapshot,
a full dump of every durable fact or redacted operator projections. Some include
graph descriptors and protected intent preimages. Consumers establish invariance
only for the fields/counts each helper returns; no external-effect absence follows
from an equal snapshot.

changed_observation returns a patch function that first calls the real database
lease observer, then replaces requested_by only in the returned request value.
It does not update that stored field or avoid the original observation/lock.
reject_fold_database_observation temporarily replaces the observer with an
AssertionError sentinel and restores it when the patch scope ends. The context
manager itself does not assert it was never called; callers' successful behavior
or captured failures provide that evidence.

The inspected interpreter separates exact replay from a fresh fold. Both enter
request/run/attempt ownership checks; fresh work reads intent evidence and a
database observation, plans history, then writes event, any endpoint/outcome
records and the attempt CAS before requesting commit. Guarded observation adds
its own current-authority checks. The actual
[unit of work](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
commits on successful exit only after that request and otherwise rolls back.
These source paths explain the fixture seams; this companion does not claim a
full interpreter/security review or execution coverage of all branches.

Selected [first/replay consumers](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_fold_first_replay.py)
compare state/IDs/event history and expected outcomes, use current_attempt for
read-back and compare non-advancement snapshots. Selected
[rollback consumers](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_fold_rollback.py)
inject failures at observation, ordinal, event, observation-write, outcome and CAS
seams and compare the fixture snapshot. An
[authority-error case](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_fold_authority_errors.py)
uses changed_observation to require a conflict before ID allocation. These are
crossreads of specific assertions, not full coverage credit for those test owners
or proof of ambiguous-commit recovery.

Security and history limits are explicit: story outcomes and recovery decisions
are constructed; selected helpers write real test-database evidence; actual
service calls remain behind their command/guard and transaction boundaries.
No provider observation, credential resolution, runtime effect, deployment
restart or live resource cleanup is performed by these builders. Error canaries
and snapshots acquire force only in consumers that assert them.

Read depth: all 476 fixture lines/helpers; inherited setup, intent/record/story,
snapshot and persistence paths; selected actual fold command/interpreter, core
transition and outcome-bridge contracts, unit of work and consumer assertions.
Other fixtures/owners were crossread without claiming full review. No tests,
application imports, database connections or provider actions ran in authoring.
