Source: [control-plane-kit-operations/tests/effect_attempt_coordinator_fixture.py](../../../../control-plane-kit-operations/tests/effect_attempt_coordinator_fixture.py).
Maintain this document alongside its source file. Recheck coordinator entry
points, injected service/result contracts, pinned context and imported evidence
builders whenever these helpers or their consumers change.

This 509-line fixture supplies scripted services, an adapter, effect sentinels and
synthetic attempt/outcome builders for coordinator contract tests. It defines no
test methods or database setup. Its central purpose is to exercise the actual
coordinator's admitted effect loop while making selected unexpected interactions
visible. It does not implement durable start, fold, reconciliation or recovery.

## Recorded interactions and scripted failures

ForbiddenInteraction builds dotted labels on attribute access. Calling it appends
the label to a shared ledger and raises AssertionError; attribute lookup alone
does not append. db_free_coordinator installs it for the unit-of-work factory and,
unless supplied, clock and ID factory. An empty ledger therefore establishes only
that these particular sentinel calls were not made, not universal absence of
effects, imports or attribute access elsewhere.

RecordingStartService, RecordingFoldService and RecordingReconciliationService
append each command before removing the next queued result. They raise only exact
instances of their three named service errors or RuntimeError; other exception
types/subclasses are returned as values. RecordingFoldService additionally invokes
a callable result with the command. Start/reconciliation do not invoke callables.
Exhausting any of these queues raises the list's IndexError rather than a custom
service failure. The helpers neither validate command semantics nor change a
database, check authority, allocate events or provide idempotent replay.

RecordingCoordinatorAdapter has separate legacy-context and runtime-call lists.
Legacy execute records context and returns ActivityExecutionOutcome.succeeded.
execute_runtime records context/request, pops a queued value and raises any
BaseException instance, otherwise returning it. RecordingLifecycle similarly
records commands and returns/raises queued values, but explicitly asserts on an
empty queue. These are broader exception rules than the three service doubles.
The actual coordinator catches ordinary Exception at its runtime adapter boundary;
a BaseException such as interruption is not thereby guaranteed to become an
uncertain result. Recording success is not provider success.

## What the database-free subclass bypasses

DBFreeExecutionCoordinator.execute directly calls _execute_admitted. Actual
[ExecutionCoordinator.execute](../../../../control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py)
first checks operate scope, admits a durable command receipt, handles replay and
finally completes the receipt. This subclass bypasses those outer steps. Its
_load_context records the command and always returns pinned_context instead of
loading current stores. It overrides legacy direct event/outcome writers to
record an attempted write and raise.

Consequently this harness can exercise service dispatch, result admission and
classification using the real inner loop, but cannot prove receipt idempotency,
crash recovery, database transactions, fresh lease/approval validation or durable
command completion. The pinned context does not automatically advance after a
fake fold. Repeated loads see the same state until a test replaces it; increasing
max_effects is not proof that each iteration sees newly committed progress.

db_free_coordinator inspects the actual constructor signature. When start_service
is present, it calls the ordinary constructor with supplied dependencies. A
fallback allocates the subclass with object.__new__ and assigns private fields
for the older constructor shape. The current constructor explicitly requires all
three services, so the first branch applies at the inspected source coordinate.
The fallback is fixture compatibility scaffolding, not evidence that either
missing production dependencies or an obsolete constructor is acceptable.
Pinned context and empty load/write/interaction ledgers are attached afterward.

Optional lifecycle, clock and ID dependencies use truthiness-based defaults.
Providing a clock/ID callable replaces the sentinel; later empty-ledger assertions
no longer establish that the supplied callable was unused. The default lifecycle
also asserts on any unexpected call rather than silently completing a run.

## Pinned topology, intent and initial attempts

pinned_runtime_context uses the actual test
[_context builder](../../../../control-plane-kit-operations/tests/test_runtime_effect_translation.py)
for one StartNode(api) activity. That builder constructs a running run, claimed
request, pinned plan, graph projections and synthetic registered product values.
The fixture replaces the request claim and lease fence with worker-a/generation
seven, carries the other selected material through, and derives a journal
projection and schedule from empty events using the imported Operations journal
conversion and Core saga interpreters. These are real pure transformations over
synthetic data, not store reads or executed Docker activities.

The real _CoordinatorContext checks relationships among request/run/plan,
graph lineage, claim/fence/worker and workspace-scoped material; it does not turn
fixture values into committed authority. The dates are fixed fixture coordinates,
not present-time lease evidence. Tests can replace the pinned context to select
blocked, running or other classification paths.

runtime_intent builds a realization using a fixture intent event, translates it
through the actual
[runtime-effect translation](../../../../control-plane-kit-operations/src/control_plane_kit_operations/runtime_effects.py)
and projects the request back to event-free Core intent. Translation builds a
request with the selected event ID and empty transient grants; projection removes
that event coordinate. This helper provides an expected intent/fingerprint for
the coordinator, not an independently committed start event or a secret grant.

started_attempt builds a STARTED Core state using the intent fingerprint unless
overridden, a supplied/default identity and fence, and one STEP_STARTED event at
ordinal one. Imported record-fixture evidence commits the state; original and
latest events are the same. newly_started wraps it in the actual NewlyStarted
result. The words newly started describe the result variant: no start service
transaction occurred. Overrides deliberately permit values unrelated to the
pinned request so consumers can test correlation rejection.

forged_original_event_attempt first builds a lawful record, changes the event's
activity to a foreign one, then bypasses EffectAttemptRecord construction with
object.__new__/__setattr__. This intentionally creates an exact-type but invalid
record. The real
[start-result wrappers](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_start.py)
have narrower checks than full record reconstruction; the coordinator reconstructs
returned records before effects. The forged value exists to test that boundary,
not to demonstrate a legal authoring path.

## Fold and recovery candidates

exact_fold_result builds a successful RuntimeEffectResult for the synthetic
original event, derives a real FoldEffectAttempt command and calls fold_result_for.
direct_attempt extracts its record. execution_outcome and fold_command_for use
the ordinary ExecutionEffectOutcome, effect_outcome_transition and
effect_outcome_failure owners; the fixture does not duplicate their fingerprint
or failure-projection algorithms.

fold_result_for requires an exact ExecutionEffectOutcome via an assertion. It
constructs a new state from the supplied started attempt's identity/fingerprint/
fence and the outcome's status/fingerprint, chooses one of four direct event
kinds, and creates the latest event at original ordinal plus one with the command's
failure. It uses fixed event/time/workspace values and an empty observation tuple,
then constructs the real EffectAttemptOutcomeRecord and NewlyFolded/ExistingFold.
The helper does not call a transition interpreter or persist anything, preserve
arbitrary predecessor/recovery history, allocate globally unique coordinates or
build observation rows. The actual
[outcome-record admission](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_outcome_evidence.py)
requires observation-count agreement, so this empty-observation construction is
not a general fixture for results containing endpoint observations.

The actual [fold-result validation](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold.py)
reconstructs attempt/outcome records and checks their agreement. It supplies
record-level admission, not proof that the fixture's command was authorized or
committed. ExistingFold versus NewlyFolded is selected by a Boolean argument,
not discovered from a store.

lawful_foreign_fold_result creates an internally admitted result with a different
run identity or request fingerprint and matching changed event ID. It derives an
outcome and command for those values before wrapping them. This separates a
structurally valid foreign result from the deliberately forged original-event
case: successful construction does not mean it belongs to the coordinator's
issued attempt or that its fingerprint came from that run's actual intent.

recovery_attempt replaces the direct success with a synthetic succeeded recovery
state carrying a fixed decision ID and uncertain fingerprint, and a later
uncertainty-resolved event. No uncertain predecessor is persisted or inspected,
no recovery command is interpreted and no recovery authority is checked. It is
record-level input for testing the forward coordinator's recovery boundary.
coordinator_command similarly supplies fixed run/authority/fence/idempotency data
and a configurable effect budget; it does not establish a receipt.

## Actual consumer checks and limits

Selected [coordinator contract tests](../../../../control-plane-kit-operations/tests/test_effect_attempt_coordinator_contract.py)
use these helpers to assert the following distinct paths:

- A newly started result binds the original event into the runtime request and
  invokes a direct fold once. The test name's live start still uses the recording
  adapter; its provider=docker evidence is synthetic text.
- An existing attempt calls reconciliation without runtime dispatch or direct
  fold. Invocation and command contents are asserted, not real observation or
  durable reconciliation success.
- Identity, fingerprint, fence and forged-event start mismatches are rejected
  before adapter/fold/reconciliation calls. Internally lawful foreign fold and
  reconciliation results are separately rejected against the issued attempt.
- Adapter exception, wrong result arm and wrong effect ID produce selected
  uncertain fold commands; the supplied result helper constructs the return.
  The test excludes a canary from the represented result, not every possible
  exception/log disclosure path.
- A compensating pinned run is blocked before the recorded effect services.
  This establishes selected forward-loop routing, not compensation execution.

The inspected actual loop distinguishes legacy operation arms, newly started
dispatch and existing-attempt reconciliation; rejects recovery-bearing existing
attempts for explicit recovery authority; checks returned record correlation; and
classifies the context after its bounded iterations. Real receipt admission,
fresh context loading, PostgreSQL services and runtime interpreters remain outside
this fixture's evidence. Its narrow fake results should remain examples, not grow
into a second implementation of those state machines.

Read depth: full 509-line fixture; actual coordinator constructor, public
admission/completion, inner loop, classification and context construction;
selected actual start/fold/outcome admission and translation; inherited evidence
helper and selected translation context/product/graph builders; selected consumer
assertions for dispatch, mismatch, uncertainty, compensation and result
correlation. This is not a full read of the large coordinator consumer suite or
every imported fixture/owner. No tests, application imports, database/provider
actions, credentials or source edits were performed. This documentation adds no
security surface; synthetic references and fingerprints grant no live authority.
