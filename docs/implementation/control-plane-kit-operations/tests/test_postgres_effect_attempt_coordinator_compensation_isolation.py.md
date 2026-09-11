Source: [control-plane-kit-operations/tests/test_postgres_effect_attempt_coordinator_compensation_isolation.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_coordinator_compensation_isolation.py).
Maintain this document alongside its source file. Recheck prepared failure
history, explicit compensation admission, inverse-attempt binding and protected
snapshot coverage when this test or its service dependencies change.

One test plus setup/teardown and a snapshot helper occupy 148 lines. It composes
the real PostgreSQL-backed forward coordinator with separate compensation
admission and attempt-start services. Its central boundary is that the forward
coordinator can produce compensation-admissible failure evidence, but does not
dispatch compensation after a program and inverse attempt have been admitted.
No test, application import, database connection or provider action was executed
while authoring this companion.

## Preparing a partially successful forward history

Setup and teardown explicitly delegate to the inherited
[coordinator fixture](postgres_effect_attempt_coordinator_fixture.py.md).
The method then truncates workspace-owned database state again and constructs a
FailedRunCompensationAttemptFixture with object.__new__. It supplies the existing
connection, database URL and assertIsNotNone method, then calls seed_truth. It
does not call that helper object's own setup/teardown; the outer fixture remains
responsible for the connection and destructive isolated database cleanup.

The fully read [compensation fixture](../../../../control-plane-kit-operations/tests/failed_run_compensation_fixture.py)
seeds a three-activity plan: StartRuntime, dependent StartNode, then dependent
WaitForHealthy. It constructs successful runtime/node attempts, retained intents,
outcomes and matching journal events using Core values and store writes. The
health-wait failure is represented by STEP_STARTED/STEP_FAILED events, without a
corresponding effect-attempt/outcome seed for that wait. The fixture also supplies
plan approval records, a claimed generation-1 execution request, failed run,
identity graph lineage and workspace pointers. These are prepared database facts,
not evidence of an actual deployment or live health failure.

The test deletes the prepared RUN_FAILED event and sets the run back to RUNNING
with settled_at null through the autocommit connection. This leaves the failed
activity journal for the ordinary coordinator to classify. The forward command
uses generation 1 to match this seed rather than the coordinator fixture's usual
generation 7.

It must return FAILED with zero effects_attempted; start/reconciliation/fold and
runtime call lists stay empty, and exactly one lifecycle command is recorded.
A store read must find exactly one RUN_FAILED event with non-null failure
evidence. The first phase does not directly assert legacy adapter calls, exact
failure contents, lifecycle command type, receipt fields or final settled_at.

Actual [coordinator classification](../../../../control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py)
derives a terminal activity-step-failed FailureEvidence from the failed schedule
and invokes FailActivityRun. It supplies one activity_id detail when only one
activity failed. This explains the newly created failure evidence rather than
claiming the original fixture failure is copied unchanged.

## Explicit program admission and first inverse binding

The test captures source_truth_snapshot and builds a compensation command using
the newly recorded RUN_FAILED failure. InvalidOperationCommand at construction
produces a targeted test failure; successful execution of the real admission
service additionally checks that evidence against durable lineage. The helper
command carries a prepared operator RecoveryAuthority with COMPENSATE scope,
expected workspace graph/revision and execution fingerprint, and its own
compensate-a idempotency key. This is an explicit separate service invocation,
not compensation automatically requested by forward classification or a tested
interactive approval workflow.

The inspected [admission service](../../../../control-plane-kit-operations/src/control_plane_kit_operations/failed_run_compensation.py)
requires failed-run lineage, unchanged workspace/execution intent and matching
latest RUN_FAILED evidence. It rejects unresolved attempts and incomplete
successful-effect evidence. For each successful effect it reads the admitted
plan's closed compensation variant: Compensate contributes a step,
NoCompensationRequired contributes none, and NonCompensatable conflicts.

The selected [successful-effect projection](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/failed_run_compensation_store.py)
joins succeeded outcome/attempt/event evidence and orders by descending completion
ordinal, then activity ID. Core's inspected
[operation compensation mapping](../../../../control-plane-kit-core/src/control_plane_kit_core/planning/activity_plan.py)
maps StartNode to StopNode and StartRuntime to StopRuntime, both using desired
graph material. In this prepared history that yields node compensation before
runtime compensation. The test does not itself assert the full step sequence,
operation variants or material-source values; this is traced implementation
context, not additional asserted coverage.

Admission stores the program, its event/action and FAILED-to-COMPENSATING run
transition in a single UoW. Sequence supplies program-a, compensation-started and
action-a; it is a finite pop-and-record ID source, not a globally unique allocator.
The test asserts the returned program ID but not the entire allocation sequence.

Next it directly changes request claim/lease timestamps to fixed future values
and calls the separate inverse-attempt start service for position 1. The fully
read [attempt fixture](../../../../control-plane-kit-operations/tests/failed_run_compensation_attempt_fixture.py)
derives that command's runtime intent from the admitted step, supplies worker-a
with EXECUTION_OPERATE scope and generation 1, and uses inverse-start-a as the
event ID. It performs no runtime adapter call for this binding operation.

The selected [attempt-start implementation](../../../../control-plane-kit-operations/src/control_plane_kit_operations/failed_run_compensation_attempt.py)
checks program lineage, current claim/lease and retained approval, requires the
first incomplete step and succeeded prior bindings, then checks the exact source
attempt/outcome/intent correlation. Its expected inverse intent replaces the
source intent's operation with the admitted step operation. It creates a new
attempt identity with the same run/activity, source attempt number plus one and
prior_attempt pointing to the source. Event, intent, attempt and binding are
written in one UoW. Binding an attempt is not executing or completing the inverse.

The test requires the returned binding's program ID and position 1, then equality
of source_truth_snapshot. That helper compares selected fields of attempt=1 rows
and their outcome rows/preimages. It omits source intents, events, some attempt
fields and surrounding lineage. This establishes preservation of selected
forward evidence, not equality of every source-owned durable fact.

## Forward coordinator remains blocked after compensation starts

After binding, the test takes a broader protected snapshot and creates a new
forward harness. It invokes the coordinator with a distinct key,
coordinator-after-compensation, and generation 1. The distinct key matters: the
call must classify current state rather than replay the earlier FAILED result.
Actual classification returns BLOCKED for a COMPENSATING run before selecting
forward or inverse work.

Assertions require BLOCKED with zero effects_attempted; empty start,
reconciliation, fold, runtime, legacy and lifecycle call lists; and no calls to
the four harness lifecycle/start/fold/direct ID generators. The protected snapshot
must remain equal. No inverse result is folded, no next compensation step starts
and no compensation completion or cleanup is demonstrated.

_compensation_snapshot reads every row as JSON, with explicit ordering, from nine
relations: compensation programs, steps and attempt bindings; effect intents,
attempts and outcomes; activity events and runs; and operation actions. It uses
fixed local relation/order strings, not user-provided SQL identifiers. These are
full-row comparisons for those relations, unlike the narrower source snapshot,
but they remain serial reads rather than a whole-database atomic snapshot.

The protected list omits command receipts, execution requests, workspaces, graphs,
plans and observations. The ordinary coordinator still admits and completes a
receipt for the new blocked command. Therefore unchanged protected rows and zero
ID-generator calls do not mean zero durable writes or no store interaction.
This single prepared case also does not prove concurrent compensation isolation,
every compensation operation, stale-authority rejection or crash recovery.

Read depth: full 148-line suite and snapshot helper; full compensation fixture459
and attempt fixture288; actual coordinator classification with retained
admission/receipt context; selected compensation constructor/admission, attempt
start/lineage/source-truth, successful-effect SQL and Core compensation mapping.
No full audit of every imported owner or replay branch is claimed. This note adds
no security surface and authorizes no fixture execution, compensation, provider
mutation or held live work.
