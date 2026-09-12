Source: [control-plane-kit-operations/tests/test_postgres_effect_attempt_coordinator_crash_rollback.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_coordinator_crash_rollback.py).
Maintain this document alongside its source file. Recheck injection positions,
receipt persistence, exception translation and the exact snapshot/assertion
boundaries when this suite or its service dependencies change.

Five methods in 187 lines cover a post-start exception, a provider exception,
three expected fold errors, three expected reconciliation errors and six raw
service-error combinations. The inherited
[coordinator fixture](postgres_effect_attempt_coordinator_fixture.py.md)
composes ordinary PostgreSQL-backed coordinator/start/fold/reconciliation/lifecycle
services with recording adapters and scripted observations. Its isolated database
setup/reset/cleanup is destructive. No test, application import, database
connection or provider action was executed while authoring this companion.

## Transaction boundaries behind the injected failures

The actual [coordinator](../../../../control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py)
commits an incomplete command receipt before entering its effect loop. A new
runtime effect then has a separate start transaction, an adapter call outside
that transaction, and a separate fold transaction. Command receipt completion
occurs only after the effect loop returns a result. An exception escaping that
loop bypasses completion without rolling back an earlier committed receipt or
start. There is no single transaction covering the entire invocation and its
external effect.

The inspected [PostgreSQL UoW](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
implements commit as a request flag: successful context exit physically commits;
an exceptional exit or missing commit request rolls back, and exit closes the
connection. A commit exception attempts rollback and propagates. These are
implementation boundaries, not all independently exercised failure cases in
this file. In particular, the suite does not inject a database error after each
event/intent/outcome write, interrupt a physical commit or kill a process.

## Committed start followed by a simulated crash

The first method temporarily patches EffectAttemptStartService.execute at the
class level. Its wrapper calls the original service to completion and then
raises the exact prepared RuntimeError. Actual
[start interpretation](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_start_interpreter.py)
writes the start event, retained intent and attempt in one UoW and returns through
that UoW's successful exit. Thus this injection is after that service transaction
commits, before the coordinator receives the start result or calls the adapter.
It is a Python exception simulation, not a process restart or database outage.

Assertions require exception object identity, an empty adapter call list and a
fresh store read whose current attempt status is STARTED. The inherited
current_attempt reads the start-runtime attempt by identity through a new UoW.
The test checks its status, not equality of every event/intent row or receipt.

After restoring the patch, a new harness invokes the same default command key.
It must return UNCERTAIN with zero effects_attempted and no adapter or
reconciliation calls. This is same-command admission replay: the retained
incomplete receipt stops execution before attempt reconciliation. It is not a
demonstration that a new command can recover or complete the attempt. The test
does not assert restart start-service/lifecycle counts, activity_id, receipt
contents, final attempt equality or an operator-facing recovery action.

Both adapters queue AssertionError as a warning value, but RecordingRuntimeAdapter
only raises exact TypeError/RuntimeError; otherwise it returns a queued value or
invokes a callable. The empty runtime_calls assertions establish the absence of
dispatch. Those queued AssertionErrors do not themselves act as throwing guards.

## Provider exception becomes a direct uncertain outcome

The provider-fault method queues an exact RuntimeError, which the recording
adapter really raises after recording its call. The test requires UNCERTAIN,
one effects_attempted, one runtime call and one recorded fold command whose
outcome status is UNCERTAIN. It also requires that the provider's canary message
is absent from repr of that fold command.

Actual runtime dispatch catches ordinary Exception and constructs a new
RuntimeEffectResult.uncertain with the request's effect ID, fixed code/message
and categorical boundary/reason details. It does not copy the caught exception
message into that outcome. The admitted ExecutionEffectOutcome supplies the
transition and failure evidence used by the real fold service. The coordinator
then reloads projected history to classify the uncertain activity.

The normal [fold implementation](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold_interpreter.py)
groups its event, optional observations/outcome and attempt compare-and-set in
one committed UoW. That code explains the durable path used by this unpatched
case; the test itself does not directly compare those rows or their exact
fingerprints. It also does not assert reconciliation/lifecycle counts, receipt
replay or universal log/exception redaction. Unlike an escaping service fault,
this uncertain result returns normally, so inspected coordinator control flow
can complete the command receipt with uncertainty rather than leaving it
incomplete. This distinction is not an explicit receipt-row assertion here.

## Expected fold and reconciliation errors

The fold matrix patches the inner fold service's execute to raise NotFound,
Conflict or Denied before its body runs. For each separate reset, the coordinator
must emit the corresponding coordinator exception with the exact fixed fold
truth-not-found, truth-invalid or authority-invalid message and both cause and
context absent. It also requires a current attempt still in STARTED status and
no lifecycle commands.

The ordinary default adapter returns synthetic success before the injected fold
error. This is a start-committed/fold-not-entered scenario, not proof of rollback
after partial writes inside fold. The method does not directly assert provider
or fold call counts, whole-attempt equality, receipt state, or equality of all
durable rows. One recorded service call alone would not prove successful service
execution: the wrapper records before calling the patched inner method.

The reconciliation matrix seeds one existing running attempt and patches the
inner reconciliation execute with the corresponding three expected errors. It
requires the exact fixed reconciliation messages, absent cause/context, complete
current-attempt equality with the seed and zero runtime calls. Because the patch
raises at service entry, it does not exercise an observer failure, partial
reconciliation transaction or rollback within guarded folding. Observer/fold/
lifecycle counts and all surrounding durable rows are not asserted.

In the inspected coordinator, these known service exceptions are caught and
translated after leaving the original exception handler. This accounts for the
fixed messages and empty chains. It does not imply arbitrary service errors are
sanitized: the next matrix deliberately preserves them.

## Unexpected service faults and the selected graph snapshot

The final method patches start, fold and reconciliation separately with exact
TypeError and RuntimeError objects. Reconciliation cases first seed an existing
attempt. Every case requires the identical error object to escape and compares
graph_request_snapshot before and after.

That snapshot contains workspace current/desired graph pointers and desired
revision, request workspace/plan/claim worker and generation, and plan status/
base/desired graph IDs. It omits graph descriptors, command receipts, run status,
events, attempt/intent/outcome rows and other tables; its serial reads are not a
whole-database atomic snapshot. Equality establishes no advancement of those
selected fields, not zero durable mutation. In particular, admission has already
committed; fold-stage failures can follow a newly committed start and a synthetic
provider result. This method does not assert provider counts or post-error attempt
state for each stage.

Raw exception identity is the intended internal boundary tested here. These
assertions provide no permission to expose arbitrary exception strings to a UI,
logs or a remote client, and no public transport sanitizer is inspected here.
No matrix exercises automatic retry, compensation or authoritative resolution
of an uncertain external action.

Read depth: full 187-line suite/five methods and local crash wrapper; full
PostgreSQL UoW; actual start commit path, coordinator admission/completion and
dispatch/error translation/uncertainty classification, selected fold writes and
outcome-evidence helpers, current-attempt read and graph snapshot; retained full
coordinator fixture334/reconciliation385 and related service context. No full
audit of every imported module is claimed. This note adds no security surface
and authorizes no fixture execution, provider mutation, recovery or held live
work.
