Source: [control-plane-kit-operations/tests/test_execution_coordinator.py](../../../../control-plane-kit-operations/tests/test_execution_coordinator.py).
Maintain this document alongside its source file. Recheck adapter translation,
transaction tracking, receipt replay, lifecycle history and fixture seed semantics
when these tests or their owners change.

The 1,619-line file contains one command-constructor test and 25 PostgreSQL
composition tests, with all their local setup, adapters and seed helpers. Unlike
the database-free coordinator-contract suite, it invokes ordinary coordinator
admission, start/fold and lifecycle services against stored state. Adapters still
return synthetic results; the configured observer raises if used. No test,
application import, database connection or provider action was executed while
authoring this companion.

## Fixtures, clocks and the meaning of a provider call

Database setup requires CPK_OPERATIONS_TEST_DATABASE_URL, connects with autocommit,
installs the schema and truncates cpk_workspaces CASCADE before seeding. Reset
truncates again and replaces the ID sequence; teardown only closes the fixture
connection. This is destructive isolated-test setup, not a production startup or
cleanup contract. The seed combines direct SQL with committed store operations;
it is not one atomic application admission workflow.

The seed registers an inline hello-server product with a synthetic OCI digest,
builds authored graphs and identity projections, records a plan, then inserts
approval and queued execution-request facts. claim/claim_and_start use the real
lifecycle service to open run-a and start it under worker-a/generation 1. The
service supplies fixed timestamps for ordinary transitions; actual claim_request
uses PostgreSQL clock_timestamp for claim/lease evidence. Fixed fixture clocks
must not be mistaken for a fully frozen database clock.

single_activity_plan starts api. two_step_plan starts api, then starts api-ready
with a dependency on the first activity. The second activity is named wait-api,
but its operation is StartNode, not WaitForHealthy. _realized_graph assumes these
node-target operations and builds Docker runtime/node values for their product;
it is not a generic graph constructor for every activity language variant.

Sequence returns supplied IDs and then generated-N values. It has no collision
avoidance across supplied/generated namespaces or thread-safety contract.
TrackingUnitOfWork wraps the actual
[PostgreSQL UoW](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py).
active tracks entered wrappers, and committed counts commit requests, not verified
physical database commits. entered/active increment before the inner enter, so
an enter failure would leave those counters incremented. Successful exit delegates
commit/rollback/close to the real UoW and decrements active in finally. This suite
does not exercise every failure path in the tracker.

RecordingAdapter records activity/context and tracker.active before consuming an
outcome. A queued BaseException is raised; the runtime arm can invoke a callable,
return an exact RuntimeEffectResult or translate an ActivityExecutionOutcome.
The local _runtime_result_for_outcome preserves successful evidence and copies
failure code/message/details into a runtime failure, but deliberately omits the
legacy outcome's observations. Consequently, absence of those observation rows
in these tests is partly determined by adapter translation before the coordinator
receives anything. It is not proof that a production runtime result carrying
endpoint observations would have them discarded.

Although several adapters implement execute, all seeded activities here are
StartNode and use execute_runtime. This suite therefore does not directly cover
the coordinator's legacy ingress/socket event/outcome persistence paths. Those
actual paths were inspected for comparison; they have separate observation and
evidence-envelope handling. Adapters whose runtime arm calls their own execute
method do not thereby exercise the coordinator's legacy branch.

## Command identity and early authority/lineage rejection

The constructor test accepts three representative canonical run IDs, then rejects
an object, bool, str subclass, empty/blank text, forbidden leading characters,
slash/space, ASCII controls including DEL and an overlong identifier. Argument
construction occurs before a local callback, whose list must stay empty. Errors
must have no cause/context, combined str/repr length at most 256 and no selected
canaries. The actual command delegates run validation to Core RunId's exact-str
grammar: alphanumeric first character, then alphanumeric or ._:-, at most 200
characters. This method does not independently test every other command field.

The stale-generation test changes the stored claim to generation 2 before
execution and requires denial, no adapter calls, no worker canary in repr, no
exception chain and only RUN_OPENED/RUN_STARTED history. The scope/ownership test
separately rejects empty scopes and worker-b before adapter dispatch. A claimed
but unstarted run produces the must-be-started conflict without an adapter call.
These are different admission/classification boundaries; no-adapter assertions
do not universally imply that no receipt could have been admitted.

Missing run translation must produce the exact fixed activity-run-not-found
message with no cause/context or missing-run canary, and no adapter call. The
cross-workspace graph test deliberately changes the plan's desired graph and
projection to workspace-b, then requires the desired-graph/workspace conflict
before STEP_STARTED or dispatch. It restores the plan pointers in finally. It
does not compare all durable rows or assert absence of a command receipt.

Actual [coordinator context loading](../../../../control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py)
locks request/run, loads the pinned plan and graph projections plus active
registrations, then projects journal history through Core after leaving the UoW.
_CoordinatorContext checks request/run/plan, projection/source and workspace
correlation. These checks explain the prepared graph rejection; this file is not
a complete matrix of every context invariant or graph-version drift.

## Success, transaction separation and lock order

The main success test explicitly pins stored realized projection IDs and returns
a synthetic success with adapter evidence including a misleading claim_generation
value. It requires COMPLETED, one attempted effect, one start-api adapter call
and tracker.active equal to zero at that call. It verifies the context's request,
workspace, run, pinned plan/projections, registered product, worker/fence and
STEP_STARTED event coordinates.

Stored run status must be SUCCEEDED, exactly one effect outcome must exist, and
the event sequence must be RUN_OPENED, RUN_STARTED, STEP_STARTED, STEP_SUCCEEDED,
RUN_SUCCEEDED. The two step events must name start-api and contain only the
effect_attempt evidence key. Thus adapter evidence does not replace step-event
commitment structure. This is selected real database composition with a fake
effect, not proof that any external resource exists or that workspace current
graph pointers advanced.

The lock-order test holds request-a FOR UPDATE on a separate connection, starts
the coordinator in one worker and obtains that worker connection's backend PID.
It polls pg_blocking_pids until the worker is blocked by the holder, then proves
run-a is still lockable through FOR UPDATE NOWAIT on a third connection. After
releasing the request lock, execution must complete. This demonstrates the
selected shared boundary waits on the request before locking the run, rather
than holding a run lock while waiting on that request.

Its custom UoW factory reports connection PIDs; the default nested lifecycle
still uses the normal tracker. Queue/future/deadline waits use five seconds, but
the polling query is a busy loop without a statement timeout, and executor exit
may wait for its worker. These are test synchronization bounds, not a guaranteed
whole-process deadline or proof of all possible database lock orderings.

## Claim replacement after dispatch and outcome evidence

GenerationReplacingAdapter changes request-a's generation to 2 using its own
autocommit connection during the adapter call. It either returns synthetic
success with a legacy observation or raises a provider-canary RuntimeError.
The runtime translation discards that legacy observation before a successful
return reaches the coordinator.

Both cases must raise ExecutionCoordinatorDenied after one adapter call. The
success-return case checks the old fence in the supplied context, no latest api
observation, and the exact three-event history ending at STEP_STARTED with
effect_attempt evidence. The raising case checks only that the last event is
STEP_STARTED and does not echo its canary. Neither case asserts every attempt,
outcome or receipt row. Actual fold rechecks the current claim; a prior dispatch
cannot authorize settlement under a replaced fence. This is not cancellation of
an external effect or an exercised recovery workflow.

Two successful-evidence cases use a nested value and a larger multi-key value.
They require completion, a STEP_SUCCEEDED event with only effect_attempt evidence,
no selected legacy observation/link rows and exactly one stored outcome preimage.
That preimage must decode to the exact RuntimeEffectResult descriptor with the
start event ID and original evidence. Evidence canaries must remain in that
descriptor; observation canaries must be absent from event payloads and outcome.
This establishes selected evidence preservation and representation separation,
not universal byte/depth limits or removal of all arbitrary sensitive strings.

## Completed receipts, progress and the full command intent

Completed-success replay requires COMPLETED both times, one adapter call and an
unchanged five-event history. It does not assert entire result equality in that
method. The two-step progress test does: same-key replay must equal the previously
returned PROGRESSED result, name wait-api and leave only the first activity's
four events. A new execute-b key then completes the next activity. A completed
command receipt can contain a progress result while the run is still unfinished.

The complete-scopes test passes every PolicyScope, asserts there are more than 16,
and uses max_effects=2**31. It requires full result replay equality, one adapter
call, all canonical sorted scopes in the loaded receipt, the unchanged integer
bound and its exact decimal text in PostgreSQL. The run completes early; it does
not perform billions of effects or prove all arbitrarily large integers work.

Reusing the first progress command's key with a changed budget must raise the
idempotency-intent conflict before a second adapter call, with four events left.
This file tests budget drift specifically, not separate changed-worker/scope/
generation cases under the same key.

Actual receipt admission serializes a run/key with a transaction advisory lock,
then checks current request ownership before replay. Its fingerprint covers run,
worker, canonical scopes, claim generation and decimal effect bound. New admission
commits an incomplete receipt; returning from the effect loop allows a separate
transaction to complete it. The
[receipt store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
only completes a matching fingerprint with still-incomplete/null result fields.
These boundaries explain the tests without implying the whole execution is one
atomic transaction or that replay bypasses current authority checks.

## Incomplete receipts, malformed records and uncertainty

The interruption test raises the identical KeyboardInterrupt from the adapter,
then directly changes the run to PAUSED. Same-key replay must return UNCERTAIN
with that current PAUSED run, zero attempted effects, no activity ID and no second
adapter call. KeyboardInterrupt escapes the ordinary Exception-to-uncertainty
adapter handler, leaving an incomplete receipt. This is an in-process exception
scenario, not a killed worker or restart of PostgreSQL.

The completion-persistence test patches complete_command_receipt to raise before
its body, after the effect loop has run. It requires raw error identity followed
by UNCERTAIN same-key replay with zero effects and one adapter call total. It
does not directly assert final run status or receipt row fields, and does not
inject a failure during PostgreSQL's physical commit. A receipt can require
attention even after other work has already committed.

The malformed-receipt test starts with a completed receipt and uses dataclasses
replace to reject wrong intent fingerprint, result count above the bound,
changed result plan lineage and completion before admission. It then corrupts
initial_run.plan_id through SQL and requires a store read to raise
OperationsRecordError. The inspected
[receipt value contracts](../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
recompute the fingerprint, distinguish incomplete/completed shapes, compare
timestamp order, effect count and retained run lineage. Store decoding rebuilds
these values. The test proves selected constructor and decode rejection, not
that SQL itself prevents every corruption or that all malformed JSON shapes are
covered here.

A RuntimeError adapter case instead returns normally as UNCERTAIN, repeats that
status without a second adapter call, retains RUNNING run status and ends with
STEP_UNCERTAIN/UNCERTAIN failure category. Here the coordinator can complete a
receipt containing an uncertain result. That differs from an incomplete receipt
created by an escaping interruption or completion-store error.

The foreign-effect-ID case requires a direct uncertain result, no latest api
observation in either named workspace, and STEP_UNCERTAIN with the normalized
runtime.effect-uncertain event failure code. A legacy diagnostic branch inspects
a caught fold conflict, but the next assertion requires that no such conflict
escaped. Despite the test name's without-persisting-row phrase, it does not
assert absence of all outcome rows: a valid direct uncertain outcome is the
intended replacement, and no observation was supplied by that runtime result.
Exact stored outcome content and canary absence there are not checked by this
method.

The orphan-start test commits STEP_STARTED without an attempt and then requires
the fixed start-truth conflict, empty exception chain and no adapter calls.
Actual first-start eligibility sees a running journal activity and cannot treat
the missing attempt row as permission to start anew. No automatic reconciliation,
repair or entire-database rollback is demonstrated.

## Lifecycle status, fence forwarding and refreshed context

Failure and unsupported scenarios both produce FAILED coordinator/run status.
The unsupported case additionally requires STEP_UNSUPPORTED, normalized
runtime.effect-unsupported failure code and effect_attempt-only step evidence.
Thus the run-level outcome is shared while selected step history distinguishes
unsupported behavior. The failed case does not separately inspect its complete
event/failure payload.

The nested lifecycle tests wrap the real service and require exactly one
CompleteActivityRun or FailActivityRun command of the relevant type, carrying the
identical fence object supplied to the coordinator. They also check the resulting
COMPLETED or FAILED status. Actual
[lifecycle transitions](../../../../control-plane-kit-operations/src/control_plane_kit_operations/lifecycle.py)
check ownership and group run transition, event and action in a UoW. Success asks
for settlement while failure does not; this suite's fence/status assertions are
not complete settlement, cleanup or compensation laws.

The max-effects test completes the first activity under budget 1, then uses a
new key and budget 2 to finish the dependent activity. It checks progress count,
next activity and adapter order, but not the second command's exact attempt
count. Unlike the database-free fixture, these invocations reload actual journal
state between steps.

SideEvidenceWritingAdapter writes an owned Cloudflare-resource record and a
generated-secret reference through a separate committed tracked UoW during the
first adapter call. The test requires no tracked UoW active at entry to either
adapter call, an initially empty ingress-resource tuple and the expected tunnel
ID/secret reference in the second context. This shows selected newly stored
side evidence is reloaded for the next activity. It is not evidence that Cloudflare
was called, DNS changed, a token was generated/resolved or secret custody exists;
the helper constructs and stores those metadata values directly. Its own database
transaction also means active-at-entry zero must not be read as no database work
anywhere inside the adapter.

Read depth: full 1,619-line source/all 26 tests and every local helper; freshly
read command/Core run-ID validation, actual lifecycle claim/transition, receipt
value/fingerprint/decimal and decoder boundaries, coordinator context loading,
legacy writers, classification/admission/completion context, and selected outcome
failure mapping. Retained full actual effect-loop/start/fold/UoW and receipt SQL
reads informed the explanations. No fresh full audit of every imported owner,
database schema or external adapter is claimed. This companion adds no security
surface and authorizes no test/database/provider execution, source changes,
recovery, compensation or held live work.
