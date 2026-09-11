Source: [control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py).
Maintain this document alongside its source file. Recheck command-receipt meaning,
effect-attempt admission, dispatch arms, authority checks and history classification
when this owner or its service contracts change.

This 1,873-line Operations owner composes persisted execution truth with Core
activity-plan, journal, schedule, runtime-intent and result values. It does not
implement Docker, cloud resources or a provider observer. Its injected services
own start/fold/reconciliation and lifecycle transactions; its adapters interpret
selected effects. ExecutionCoordinator is a forward executor for an already
admitted request, not a topology planner, approval UI, automatic recovery agent
or workspace graph-advancement service.

The governing objects are distinct: an ExecuteActivityRun command and its receipt;
an activity selected from a pinned plan; an effect attempt and its original event;
a runtime request/result; and the run's journal/lifecycle state. Completion of one
does not imply completion of the others. The useful runtime path is:

```text
command admission -> incomplete receipt
current stored context -> Core journal projection -> schedule selection
event-free intent -> StartEffectAttempt
  NewlyStarted    -> bind original event -> runtime request -> adapter -> direct fold
  ExistingAttempt -> reconciliation service, without provider redispatch here
reload and classify -> returned result -> completed command receipt
```

Each arrow has its own validation/authority owner. The diagram does not represent
one database transaction, an exactly-once external protocol or a promise that
every uncertain state reaches reconciliation.

## Commands, contexts and visible results

ExecuteActivityRun contains run_id, ExecutionWorkerAuthority, ExecutionLeaseFence,
IdempotencyKey and positive exact-int max_effects, default 1. Its constructor uses
Core RunId to enforce canonical run text, requires the typed authority/fence/key
and worker agreement, and rejects a nonpositive effect budget. It does not require
EXECUTION_OPERATE until execute, and it does not create or approve a plan.

ActivityRealizationContext carries the selected PlannedActivity, request/run/plan
records, base/desired realized graph projections, product and authority material,
worker/fence and a committed-intent event supplied by the caller. Construction
normalizes registration collections to tuples and checks their types/workspaces,
request/run/plan linkage, graph source and optional projection IDs, claim/fence
correlation and event run/activity. STEP_STARTED and STEP_COMPENSATION_STARTED
are accepted event kinds. This shared context can describe compensation material
even though this coordinator never selects compensation work.

Those are in-memory structural checks. The context does not independently read
the database, prove the event was committed, renew a lease, verify provider state
or validate an interactive permission decision. Many checks use isinstance;
the exact-type rejection at runtime-dispatch and returned-effect boundaries is
a separate protection. A frozen dataclass is not a deep immutability guarantee
for every contained object.

ActivityExecutionOutcome is the older adapter-result family: succeeded, failed,
unsupported or uncertain, with bounded evidence, optional failure and typed
observation rows. Non-success requires failure and success forbids it. It is
distinct from Core RuntimeEffectResult and must not be passed as that runtime
arm's result.

ExecutionCoordinatorResult carries run, coordinator status, effects_attempted and
optional activity_id. Its descriptor emits only run ID/status, coordinator status,
count and activity ID, not the full context. This result dataclass itself has no
post-init validation; retained receipt results are rebuilt as validated
ExecutionCommandResultRecord values. The descriptor is a small command report,
not complete operational history or a guarantee that external resources match
the desired topology.

## Receipt admission and completion are their own durable protocol

execute checks EXECUTION_OPERATE, calls _admit_command, returns a replay result if
present, otherwise executes the admitted loop and then completes the receipt.
There is no finally that marks failed invocations complete and no automatic
redispatch of an incomplete receipt.

Admission opens a UoW and takes a run/idempotency-key advisory lock through the
[execution store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py).
It locates the run, locks its request before locking the run, verifies the linkage
again and checks current worker ownership. That shared helper checks CLAIMED
status and matching worker/fence, not wall-clock lease expiry; the runtime
service boundaries own their additional lease observations. Admission then
reads the receipt for update.
The [receipt fingerprint](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
covers run ID, worker ID, canonical complete scope tuple, generation and effect
budget. The budget is represented as positive decimal text; it is not narrowed
to a PostgreSQL 32-bit integer.

Existing receipt behavior has three cases:

- Different fingerprint under the same key raises an intent conflict.
- A completed receipt returns its stored result, including a PROGRESSED or
  UNCERTAIN result; completed command does not mean completed run.
- An incomplete receipt returns UNCERTAIN with the currently loaded run, zero
  attempted effects and no activity ID, without entering the effect loop.

Ownership checks precede both forms of replay. For a new command, admission stores
the initial run and full command intent, requests commit and exits the UoW before
effect selection. A subsequent context error or effect failure does not undo that
admission. A new key is a new command, not an implicit safe way to retry ambiguous
external work.

_complete_command uses a separate UoW and the same key lock. It supplies the command
fingerprint, completion time and result to the store, which updates only matching
still-incomplete receipt fields; a missing completion acknowledgment conflicts.
This method does not call _locked_request_and_run or independently recheck current
claim ownership. Its boundary records the result for the admitted command;
selection, external-effect folding and lifecycle transitions have their own
authority checks.

The retained record validates intent fingerprint, complete/incomplete shape,
timestamp ordering, result count within budget and initial/result run lineage.
These checks do not make external execution and receipt completion atomic. A
provider call, start/fold or lifecycle transition can commit before receipt
completion fails. A BaseException interruption can leave a committed start and
incomplete receipt. Same-key replay reports uncertainty rather than inventing a
successful completion or resuming those effects.

## Reloading context and interpreting the schedule

_load_context opens a fresh UoW, locks/checks request and run, loads the pinned plan
and retrieves its exact realized projection IDs when present. Without a pinned
projection ID it checks the authored graph's workspace and asks the projection
store for identity material. It loads active products, image-pull/runtime/delivery/
ingress registrations, owned ingress resources, generated-secret references and
run events. It does not write new graph pointers or publish a projection here.

After the UoW closes, activity_journal_events adapts stored events to Core,
project_activity_journal reconstructs saga state and derive_schedule computes the
current activity schedule. The private _CoordinatorContext validates correlation
of those supplied records and can construct a public realization context with
the same fence object. It does not itself recompute the supplied projection or
schedule. Fresh loading between iterations lets later activities see committed
side evidence from earlier ones; it does not lock all provider truth throughout
an external call.

_classify_current first handles run status: CLAIMED conflicts until started;
PAUSED, CANCELLED and COMPENSATING block; SUCCEEDED completes; FAILED reports
failure; other non-RUNNING states block. For a RUNNING run, precedence is:

1. An uncertain journal activity returns UNCERTAIN and its activity ID.
2. Failed scheduled work calls FailActivityRun with bounded terminal failure
   evidence and the current fence, then returns FAILED.
3. Running scheduled work returns IN_FLIGHT with the first activity ID.
4. A successful schedule calls CompleteActivityRun and returns COMPLETED.
5. Ready work returns PROGRESSED with the first ready activity; blocked/waiting
   or remaining unmatched state returns BLOCKED.

Classification can therefore mutate lifecycle state. It is not a pure read-only
status query. Lifecycle completion/failure use deterministic per-run keys and
the same fence; the [lifecycle owner](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/lifecycle.py)
groups its state/event/action mutations. Completion requests settlement while
failure does not. If failing the run raises RunLifecycleConflict, this classifier
reloads the run and still returns CoordinatorStatus.FAILED; it does not reclassify
the fresh status. Completion errors do not use that same fallback. Consumers
should retain both reported coordinator status and returned run evidence.

The loop selects only PROGRESSED or IN_FLIGHT. In particular, an uncertain
projection stops before start/reconciliation selection. Completed terminal
history can also settle without a start-service call. The presence of a
reconciliation dependency is not a general recovery loop over every uncertain
or terminal state.

## Runtime attempt admission, request binding and folding

For non-legacy work, the private [runtime-effects projection](runtime_effects.py.md)
derives an event-free Core RuntimeEffectIntent from the selected context/activity.
This includes graph/product/authority-delivery checks before a start command.
The coordinator constructs a STARTED transition for attempt 1, fingerprints the
intent and calls StartEffectAttempt with request identity, worker authority and
fence. It does not generate the original effect event ID itself.

The ordinary [start service](effect_attempt_start_interpreter.py.md)
owns first-start eligibility, current claim/lease and retained intent checks,
then atomically records the event/intent/attempt. Its existing-attempt path checks
exact retained intent/fence correlation before returning ExistingAttempt. Merely
having a STEP_STARTED event without the corresponding attempt is not admission
for another provider start.

Returned start values must have exact NewlyStarted or ExistingAttempt type and
an exact EffectAttemptRecord. The coordinator reconstructs the record, then
compares issued attempt identity, request fingerprint, worker/generation and
original STEP_STARTED run/activity coordinates. It checks the returned record;
it does not query the store again to prove the service's returned variant is
truthful. The public start-result constructors impose additional variant rules,
and the normal service is part of the trusted composition. Do not turn these
selected checks into a claim that every constructor-bypassed forged value is
independently revalidated at this layer.

For NewlyStarted, the original start event becomes both realization.intent_event
and the Core request's effect/event identity. The request is built from the same
intent with no transient secret-resolution grants initially. execute_runtime is
called outside the coordinator's DB transactions. Ordinary Exception, a returned
wrong exact type or a mismatched effect ID becomes a new direct uncertain result
with fixed code/message and categorical adapter boundary/reason. BaseException
subclasses such as KeyboardInterrupt escape this handler.

ExecutionEffectOutcome binds the exact result to attempt identity and request
fingerprint. Public outcome transformations produce the transition/failure for
FoldEffectAttempt. The ordinary [fold service](effect_attempt_fold_interpreter.py.md)
rechecks current authority and owns its event/outcome/observation/attempt update
transaction. A claim can change after dispatch, so a returned provider result
can fail to enter durable history. Rejection does not revoke the already executed
external action; it leaves a distinct reconciliation/recovery problem.

For ExistingAttempt, this coordinator never calls the runtime adapter or direct
fold path. A recovery-bearing record conflicts with the explicit-recovery-authority
message. Otherwise it calls ReconcileEffectAttempt with the selected identity and
current authority/fence. The [reconciliation service](effect_attempt_reconciliation_interpreter.py.md)
owns retained-attempt replay or observation and guarded folding. Its selected
implementation observes a STARTED attempt outside its initial read transaction;
other admitted states use existing fold evidence. Observation authorization and
provider observation do not occur directly in ExecutionCoordinator.

Both fold paths admit only exact NewlyFolded/ExistingFold, reconstruct those
results and require matching attempt identity/request fingerprint. This rejects
selected malformed, hostile or internally lawful foreign results. It is a result
admission boundary, not a comparison of every provider consequence or an undo
mechanism for an effect whose fold acknowledgment is invalid.

Named start/fold/reconciliation NotFound, Conflict and Denied exceptions become
fixed coordinator messages after leaving their catch blocks. Unexpected internal
service exceptions propagate. The runtime adapter's broad ordinary-exception
normalization must not be mistaken for a universal exception sanitizer around
all services, context loading or persistence.

effects_attempted counts selected work, not provider calls: both newly started
work and an existing-attempt selection consume one unit. Legacy selection counts
after its start event is recorded. Classification/lifecycle-only steps do not
increment it. Each iteration reloads context; after exhausting the budget, the
coordinator reloads and classifies again. It selects one activity at a time and
does not execute ready activities concurrently inside this loop.

## Legacy ingress/socket effects remain a different path

AllocatePublicIngress, RemovePublicIngress and Add/Switch/RemoveSocketConnection
use direct step-event/outcome persistence rather than the runtime start/fold
services. An already IN_FLIGHT legacy activity returns immediately, without
another adapter call. A fresh one records STEP_STARTED in a committed UoW, then
calls adapter.execute and records its outcome in a later UoW.

Legacy event/outcome writers lock/check request and run and require RUNNING. They
wrap evidence with the actual claim generation and nested details. Returned
observation workspace mismatch replaces the entire outcome with uncertainty;
an evidence-envelope OperationsRecordError similarly becomes categorical
uncertainty. Accepted observation rows are written alongside the result event.
There is no exact RuntimeEffectResult-style returned-arm reconstruction on this
path; it relies on the legacy adapter outcome contract and may surface unexpected
attribute/type errors from malformed values.

An ordinary legacy adapter exception becomes fixed uncertain failure with the
exception class name as evidence, not its message. After recording a non-success
outcome, the coordinator reloads/classifies and returns the selected legacy
UNSUPPORTED, UNCERTAIN or FAILED status. This differs from a runtime unsupported
step whose derived schedule may report a FAILED run. Keep event status, effect
kind and command status separate.

ActivityExecutionDispatcher routes ingress operations to an optional ingress
adapter, reporting explicit unsupported evidence when absent. Socket operations
go to its runtime adapter's legacy arm. Other operations must use execute_runtime,
which delegates to runtime.execute_runtime. Constructor checks only the presence
of execute on adapters, not a complete protocol/callability audit.

RuntimeInterpreterDispatcher's legacy arm handles socket operations by producing
socket-connection-recorded evidence. That helper does not call a runtime provider,
change a graph or demonstrate network reconfiguration. Any broader effect of a
topology change belongs to its other planned activities and owners.

## Runtime interpreter and secret-use authority

RuntimeInterpreterDispatcher copies its RuntimeKind-to-interpreter mapping and
requires execute attributes. The resulting dictionary is still mutable despite
the frozen outer dataclass. execute_runtime requires exact context/request types
and congruent effect/event, workspace/request/run/plan/graph, activity and operation
coordinates before dispatch. It does not reconstruct the entire request from
the context here; private projection and upstream value contracts own that
material derivation.

The request's runtime_kind selects an interpreter. A missing interpreter returns
explicit unsupported evidence. With no authority reference, it calls execute;
with one, it selects the first matching reference/runtime-kind registration in
the supplied context and requires execute_with_authority. Missing registration
or missing authority-aware method is unsupported. The helper does not refresh
registrations from the database or itself filter their status; normal context
loading supplied active workspace-owned values. Direct callers must not treat
an arbitrary constructed context as fresh external authorization.

Before interpreter I/O, required_secret_uses_for_runtime_effect enumerates sorted,
deduplicated reference/intent pairs for environment/file deliveries, image-pull
credentials, PostgreSQL verification passwords and remote Docker TLS material.
No required uses returns the request unchanged. Otherwise the dispatcher requires
a SecretUseResolutionAuthorizer and submits one AuthorizeSecretUse per pair.
Correlation includes workspace/reference/intent, worker, request-as-operation ID,
run, activity and effect; session is omitted. requested_at comes from the intent
event. Correlation is stable across a changed timestamp, but includes the worker.

Returned grants must be SecretResolutionGrant instances matching workspace and
effect and permitting the requested reference/intent. The request is replaced
with that grant tuple. These selected checks rely on the authorizer's contract
for the remaining grant evidence; they do not independently compare every grant
field. The inspected [secret authorization service](secret_providers.py.md)
requires SECRET_PROVIDER_USE, locks current reference/provider admission and
correlation, and commits reference-only authorization evidence before returning
a grant. It does not resolve secret bytes. Multiple required uses are authorized
one at a time, so a later denial does not roll back earlier committed uses.

Known secret registration errors map to secret-use-not-authorized unsupported
results; InvalidOperationCommand maps to authorizer-invalid unsupported results.
Unexpected failures outside the inner interpreter try block can escape the
dispatcher; when called through the coordinator's adapter boundary an ordinary
exception becomes direct uncertainty there. The inner interpreter call separately
normalizes ordinary exceptions, wrong result type or effect ID to uncertainty
tagged with boundary=interpreter. A lawful result is returned unchanged, including
its raw endpoint observations. This layer does not convert it to legacy
ObservationRecord rows.

## History, tests and practical limits

Structured history spans command receipts, effect intents/attempts/outcomes and
lifecycle events/actions; the small returned descriptor does not replace it.
There is no receipt-retention sweep, automatic compensation, cleanup, failover,
graph advancement or blind retry here. Incomplete receipts and uncertain effects
remain separate facts until an appropriate owner and explicit authority resolve
them. Provider state remains external truth; a synthetic success in a test is
not a live deployment guarantee.

The full [database-free contract suite](../../tests/test_effect_attempt_coordinator_contract.py.md)
checks constructor/dispatch shape, exact returned-value admission, selected
hostile hooks, error categories and static import/call occurrences. Its pinned
context bypasses command receipts and never advances durable history. The full
[execution-coordinator suite](../../tests/test_execution_coordinator.py.md)
uses real PostgreSQL composition to check receipt progress/replay, interruption,
lock order, history, fencing and refreshed side evidence with synthetic adapters.
Its legacy-to-runtime test adapter drops legacy observations before dispatch;
those row-absence assertions are not general production redaction evidence.

The reviewed PostgreSQL first/replay, budget/lifecycle, concurrency, crash/rollback
and compensation-isolation suites add selected service composition and preserved
state checks. Their same-key overlap cases are not universal exactly-once or
all-process crash-recovery proofs. Selected freshly read
[runtime dispatcher tests](../../../../../control-plane-kit-operations/tests/test_runtime_interpreter_dispatcher.py)
check exact request/result arms, hostile subclasses, foreign context correlation
and grant propagation with recording interpreters/authorizers; no full fresh
review of that entire test file is claimed here.

Known categorical errors avoid copying selected lower-level messages, but
contexts contain private addresses, identifiers and secret references, legacy
evidence retains caller-supplied data, and unexpected errors may remain raw.
Neither frozen values nor bounded evidence make every repr/exception safe for
public display. Runtime provider credentials, actual endpoint exposure and
external mutation policy belong to the composing entrypoint/provider contracts.
This file assumes an already admitted request and does not itself authenticate
an HTTP caller or ask the user for permission.

Read depth: full 1,873-line coordinator source, all local classes/functions and
late imports; fresh full start command/result contract, selected runtime secret
enumeration and secret authorization/correlation implementation, selected
reconciliation/start replay and dispatcher tests. Retained full reviewed
coordinator test suites/fixtures and actual receipt/store/UoW, lifecycle,
attempt/fold/outcome and private projection contracts support the cross-owner
explanations. No fresh complete audit of every dependency or runtime interpreter
is claimed. This documentation adds no execution authority or security surface;
no tests, imports, database/provider actions, source/inventory edits or publication
were performed while authoring it.
