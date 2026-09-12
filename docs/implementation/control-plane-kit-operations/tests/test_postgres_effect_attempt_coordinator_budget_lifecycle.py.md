Source: [control-plane-kit-operations/tests/test_postgres_effect_attempt_coordinator_budget_lifecycle.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_coordinator_budget_lifecycle.py).
Maintain this document alongside its source file. Recheck budget counting,
terminal classification, dispatcher arms and inherited service/seed behavior when
the assertions or their dependencies change.

Five tests in 129 lines distinguish selected coordinator iterations from adapter
calls and lifecycle-only work. They inherit the real PostgreSQL service harness
from [postgres_effect_attempt_coordinator_fixture.py](postgres_effect_attempt_coordinator_fixture.py.md).
All methods inherit database setup, schema verification, destructive test resets
and teardown, even the method that begins with pure dispatcher assertions.
Execution belongs to the owning isolated Docker/PostgreSQL suite. This note is
static source review; no tests, imports or database actions were performed.

The inherited harness composes the ordinary coordinator and real start, fold,
reconciliation and lifecycle services. Runtime results and observer responses
remain scripted. RecordingService counts attempted delegations before executing
the inner service; the runtime adapter can return synthetic success. Fixed
timestamps, generated IDs and authority/fence values are fixture coordinates,
not live provider or credential evidence. The companion above records setup,
transaction, snapshot and default-adapter limits.

The first method has two distinct checks. It directly calls
[ActivityExecutionDispatcher](../../../../control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py)
with AllocatePublicIngress and AddSocketConnection contexts from the translation
fixture. Separate RecordingCoordinatorAdapter instances must receive exactly
the ingress and socket legacy contexts. This checks those two dispatcher arms,
not an end-to-end coordinator execution of ingress/socket activities. It does
not assert returned outcomes or exercise removal, switching, missing-ingress or
runtime-dispatch rejection cases. The method then resets database truth, persists
synthetic recovered success and requires the ordinary coordinator to report
COMPLETED with zero effects_attempted.

The fresh-start method scripts RuntimeEffectResult.succeeded using the received
request's effect ID. With max_effects=1 it requires COMPLETED, one effects_attempted
and exactly one call each to start, runtime adapter, fold and lifecycle. This
captures start/dispatch/fold plus final run completion within the same invocation;
it does not mean all those services share one database transaction. It checks
counts and result status, not exact command payloads, event/receipt rows, lifecycle
command type or a separate fresh-connection read of all resulting state.

The existing-attempt method seeds zero-use reconciliation with a scripted observer.
With budget one, it asserts one effects_attempted, one start-service call, one
reconciliation call, no runtime calls and one lifecycle call. It does not directly
assert the final CoordinatorStatus, observer/fold counts or persisted settlement
fields. An existing-attempt selection can consume budget without invoking the
runtime adapter; the test name is not proof of external observation success.

The recovery method separately seeds recovered success and recovered failure,
then runs a fresh harness with budget three. Expected coordinator statuses are
COMPLETED and FAILED, both with zero effects_attempted, no start/reconciliation/
runtime calls and one lifecycle call. Recovery is prepared before this execution
by the inherited seed and real fold service; the coordinator is classifying that
terminal activity history, not deciding or performing a new recovery. Fold-call
absence and all lifecycle payload fields are not separately asserted here.

Actual coordinator _classify_current delegates a successful schedule to
CompleteActivityRun and a failed schedule to FailActivityRun. The
[lifecycle owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/lifecycle.py)
uses different transitions: completion marks success with settled=True, whereas
failure uses settled=False. Do not infer identical request settlement or timestamp
behavior from this suite's use of the word settlement. The tests assert selected
statuses/counts, not those complete durable lifecycle laws.

The final method uses an existing attempt and budget two, yet requires only one
effects_attempted and one reconciliation call, no runtime calls and at most one
start call. The last assertion permits zero as well as one; it is not an exact
call-count law. Final status, lifecycle count, multi-activity fairness and invalid
or boundary budget inputs are outside this method's assertions.

The actual _execute_admitted loop increments its counter for the selected start
path, including an existing attempt; reconciliation and subsequent lifecycle
classification do not add a separate unit. Classification runs both before
selection and after the bounded loop, allowing lifecycle completion after a
one-unit start/fold. These inspected semantics explain the examples; the suite
does not establish arbitrary concurrency, wall-clock/resource bounds, exactly-once
provider execution or every branch of the budget state machine.

Read depth: full 129-line suite/five tests; retained full PostgreSQL coordinator
fixture334, database-free fixture509 and reconciliation fixture385 plus selected
actual seed/service/UoW/coordinator paths; fresh actual dispatcher and lifecycle
completion/failure arms. No exhaustive audit of the large coordinator or all
lifecycle assertions is claimed. This documentation changes no runtime/security
surface and grants no authority for the inherited destructive database setup or
held live work.
