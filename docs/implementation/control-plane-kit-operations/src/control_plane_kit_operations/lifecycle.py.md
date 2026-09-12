Source: [control-plane-kit-operations/src/control_plane_kit_operations/lifecycle.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/lifecycle.py).
Maintain this document alongside its source file. Recheck command admission,
claim time, transition time, current authority, replay evidence, lock ordering and
settlement whenever this service or its store/value contracts change.

This 877-line Operations owner interprets seven lifecycle command values into
durable request/run state, ordered activity events and session operation actions.
It opens the first run for an already admitted execution request and owns the
transaction boundary for subsequent start, pause, resume, complete, fail and
cancel commands. It does not create approval decisions, execute a deployment
plan, inspect providers, dispatch runtime effects or advance the workspace graph.

The useful structure is:

```text
typed command + worker authority
  -> session-scoped idempotency lookup
  -> current request/run authority and state
  -> one transaction: claim or transition + event + operation action
  -> RunLifecycleResult

existing action
  -> fingerprint + current authority + retained evidence checks
  -> current request/run with original event/action, replayed=True
```

The replay branch does not repeat a mutation or reconstruct a frozen historical
run snapshot. Provider success and lifecycle success remain separate claims.

## Command and result values

ExecutionWorkerAuthority contains worker_id and PolicyScope values. Construction
requires nonblank worker text, validates scope values, deduplicates them and
sorts by scope value. This value is supplied authority, not authenticated identity
discovery. ExecutionLeaseDuration requires an exact integer from 1 through 3600;
bool is rejected. ClaimAndOpenActivityRun contains request_id, that authority,
the duration and an IdempotencyKey. Its descriptor names command, request, worker,
duration and key; it does not include scopes or create a fence in advance.

The six post-claim commands contain run_id, authority, ExecutionLeaseFence and
IdempotencyKey. Shared validation delegates run identity to
[Core RunId](../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/run_identity.py):
exact str, at most 200 characters, an alphanumeric first character, then
alphanumeric or ._:-. Authority, fence and key use isinstance checks; worker IDs
must agree between authority and fence. The
[fence contract](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/execution_leases.py)
adds bounded worker text and an exact positive generation at most 2**63-1.
The [idempotency-key contract](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/workflows.py)
requires nonblank text of at most 200 characters.

Pause, Complete and Cancel additionally carry BoundedEvidence, defaulting to an
empty object. Fail requires FailureEvidence. These constructors check the typed
wrappers; the wrappers own JSON/evidence bounds and failure-shape admission.
Start and Resume have no separate evidence field. The union LifecycleCommand
names the seven commands, but execute dispatches with isinstance after reading
command.authority. This is a typed application-service API, not an exhaustive
hostile-object or untrusted-JSON admission boundary.

RunLifecycleResult contains request, run, event, action and replayed=False by
default. It has no post-init validation. Its descriptor exposes request/run IDs,
current run status, event ID/type/ordinal, action ID/type, replay flag and, when
present, claim_generation from request.claim. It does not duplicate generation
as a result field or return the full failure/evidence/context. Callers needing
history use the returned records or the history stores.

## State transitions interpreted by execute

Every command first requires EXECUTION_OPERATE. The service then selects the
following transition parameters; the store's conditional update is the final
state check.

| Command | Required current run status | Replacement | Event | Timestamp changes |
| --- | --- | --- | --- | --- |
| ClaimAndOpenActivityRun | No existing run; queued request | CLAIMED | RUN_OPENED | Database claim time becomes creation time |
| StartActivityRun | CLAIMED | RUNNING | RUN_STARTED | Set started_at if absent |
| PauseActivityRun | RUNNING | PAUSED | RUN_PAUSED | Preserve start; no settlement |
| ResumeActivityRun | PAUSED | RUNNING | RUN_RESUMED | Preserve start; no settlement |
| CompleteActivityRun | RUNNING | SUCCEEDED | RUN_SUCCEEDED | Set settled_at |
| FailActivityRun | RUNNING or PAUSED | FAILED | RUN_FAILED | Preserve start; remain unsettled |
| CancelActivityRun | CLAIMED or PAUSED | CANCELLED | RUN_CANCELLED | Set start if absent and set settlement |

Cancellation of RUNNING is not one of these admitted transitions; a claimed
cancellation records a start timestamp even though no runtime work need have
begun. Cancelling a previously started paused run preserves its original start.
FAILED is deliberately unsettled so a separate explicit compensation/recovery
decision can follow. It is not interchangeable with settled uncompensated failure.

CompleteActivityRun does not load the activity plan or prove all steps succeeded.
The [execution coordinator](coordinator.py.md) owns its journal/schedule-based
decision to call lifecycle completion or failure. Direct callers of this service
must satisfy their own higher-level workflow requirements. A stored SUCCEEDED
status alone is not an independently verified claim about external resources.

## Claim creation: database time and one command transaction

_claim computes an intent fingerprint, enters the injected unit of work and reads
the request as a locator for its session. A missing locator becomes the fixed
RunLifecycleNotFound message. It takes the session/key action-idempotency lock
and checks for an existing operation action before allocating any identities.

For a new claim it locks and requires the session to be OPEN, re-reads the request
and verifies session linkage, QUEUED status, absent claim and no existing run for
that request. It then allocates and validates the run ID before calling the
store's claim_request. A malformed generated ID therefore fails before the
database-generated claim, even though lookup/locking has already occurred.

The actual [PostgreSQL execution store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
locks the request row FOR UPDATE and rechecks that it is queued. A subsequent
update samples clock_timestamp once, sets generation 1 and uses the sample for
claimed_at and expiry plus the requested duration. The store returns None for
an already claimed request, even for the same worker; it does not treat that as
replay or renewal. Its duration guard duplicates the 1..3600 exact-int boundary
at the store interface.

The service requires the returned request to be CLAIMED with claim evidence.
It uses claim.claimed_at for all three new records: the claimed run's created_at,
RUN_OPENED's occurred_at and CLAIM_RUN action's created_at. The run has
AdmittedRun(request_id), RetryIdentity(1) and attempt:1 metadata; the opening event
also records attempt:1. Event and action IDs are allocated afterward. Ordinals
come from their stores, and the action retains request/run/event correlation plus
the command key and fingerprint.

The injected service clock is not consulted on the claim path. Database time is
sampled after the request lock wait, not when the caller constructs the command.
This is first-run admission only: prior-run retries, claim renewal, takeover and
abandonment require other owners and authority decisions.

## Post-claim transitions: lock and clock boundaries

_transition fingerprints the command and samples the injected clock before
entering the unit of work, including on replay. It reads run/request locators,
takes the session/key action-idempotency lock and looks for a retained action.
For a new transition it locks the open session, then the request, then the run.
It rechecks run/request and request/session linkage and current worker ownership.

_require_worker_owns requires request status CLAIMED, present claim, exact claim
fence equality and authority.worker_id equal to fence.worker_id. A larger generation
does not confer authority. This helper does not compare lease_expires_at with any
clock. The claim replay branch likewise checks current status/actor/generation,
not wall-clock expiry. The execution store offers a separate locked lease-expiry
observation operation; this service does not call it.

For each allowed source status, the service tries compare_and_set_run_status until
one succeeds. PostgreSQL requires both the expected status and settled_at IS NULL.
The service only supplies started_at when requested and currently absent; the
store preserves settlement using COALESCE(settled_at, supplied_time). If no
transition succeeds, the service raises RunLifecycleConflict.

It then inserts the run event with evidence/failure, inserts the correlated
session action and requests commit. Event, action and any new start/settlement
timestamp share the earlier injected clock sample. Unlike claim time, this sample
can precede a database lock wait. The module does not impose a chronological law
comparing that sample with the database-created claim timestamp. The injected
clock and PostgreSQL timestamp codec remain composition responsibilities.

The new-command row-lock order is therefore session before request before run,
after the action-idempotency advisory lock. Event ordinal allocation locks the
run, and action ordinal allocation locks the session; on these paths those rows
are already owned by the transaction or newly inserted. This describes the
ordinary PostgreSQL composition, not a proof against every possible cross-service
deadlock.

## Idempotency and retained action intent

The [activity-history store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/activity_history.py)
implements lock_action_idempotency with a transaction-scoped advisory lock over
the session/key namespace, and looks up actions by those two fields. The key is
not scoped only to one run or one command type. Reuse across different lifecycle
intent in the same session must conflict.

_fingerprint hashes compact sorted JSON with SHA-256. A claim's input includes
command kind, request ID, worker ID and lease duration. Post-claim input includes
kind, run ID, worker ID and fence generation; Pause/Complete/Cancel add evidence,
and Fail adds failure category, code, message and details. The key selects the
stored action and is not itself hashed. Scope tuples are not part of this
fingerprint; execute checks the required scope on every call independently.
Timestamps are also outside the fingerprint.

Claim replay locks the current request before _replay checks the fingerprint.
It allocates no identity, does not call claim_request, does not sample the service
clock and does not require an open session. For a retained CLAIM_RUN action,
current request status must still be CLAIMED and its worker/generation must agree
with the recorded action. Changing stored generation invalidates the old replay.
The run is read through the store without a separate explicit run FOR UPDATE in
this claim-replay branch.

Post-claim replay first compares the fingerprint, before request/run row locks.
Changed intent therefore conflicts without waiting for those row locks, although
locator reads and the action-idempotency advisory lock have already occurred.
Matching intent locks request before run, checks linkage and current authority,
then passes the locked run, expected action type and fence into _replay. It does
not require the session to remain open. A changed fence under the same key is
changed intent; an unchanged old fence can still be denied by current authority.

_replay reads the retained run/event identifiers, reconstructs the relevant
records and derives the recorded status/event kind from the lifecycle action
type. It checks session/request/plan lineage, action payload run identity/status,
run admission, event-to-run linkage, event kind/type/ordinal and, for post-claim
replay, expected action type, worker and generation. Payload integers require an
exact positive int; payload run IDs use Core RunId. Other required payload text
must be nonempty str, not an independently canonical identifier grammar.

The status in action payload must describe the original transition. The current
run is not required to still have that original status. Thus replay can return
an advanced current run with an earlier event/action and replayed=True. It does
not rerun the original effect, rewrite state or restore a historical snapshot.
The helper verifies selected correlation fields, not a cryptographic rederivation
of all action/event evidence; ordinary store and record contracts remain trusted.

## Errors, rollback and operational history

RunLifecycleError is the base for Conflict, Denied, IdempotencyConflict and
NotFound. Constructor/unsupported-command errors use InvalidOperationCommand.
Selected missing-store and malformed-run failures are translated outside their
catch blocks to fixed messages, clearing candidate-bearing KeyError/ValueError
context. Payload failures name the expected field without rendering its candidate
value. This is selective translation, not a blanket exception sanitizer.

ID factory errors, SQL constraint failures and other unhandled dependency errors
propagate. A valid colliding ID remains a raw PostgreSQL UniqueViolation. The
[PostgreSQL unit of work](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
rolls back the full command on exceptional exit; a requested commit is performed
only on successful exit, and the connection is closed afterward. An event or
action insertion failure can therefore undo an earlier claim or status update.
There is no provider-side compensation to perform inside this module.

The retained operation action is the idempotency evidence; this owner does not
create a separate incomplete command receipt before its transaction. Run state,
run event and action are committed together on ordinary success. A caller facing
an interrupted or ambiguous database commit must establish durable evidence;
this module has no special ambiguous-commit recovery protocol or permission to
blindly dispatch external effects.

_payload retains execution_request_id, plan_id, run_id, run_status, event_id,
event_type, event_ordinal and current claim_generation when present. Events carry
bounded evidence or failure; actions retain command correlation. Ordinals provide
run/session ordering, not a global history order. This module owns no retention,
history deletion, resource cleanup, graph advancement or request-claim release
after run settlement.

## Executable evidence and handoff limits

The fully read [run-lifecycle tests](../../tests/test_run_lifecycle.py.md) contain
four record-law methods and 33 PostgreSQL composition methods. They cover database
claim timestamps and lock waits; representative expiry observations; replay with
no identity allocation; stale generation and competing workers; request-before-run
locking; selected payload corruption; start/pause/resume/complete/cancel/fail paths;
claim replay after session closure; canonical identity round trips; and actual
SQL-collision rollback at run, event and action boundaries.

Those tests use seeded approval/request facts and empty graph/plan material,
not an application deployment. The failure test rejects completion after FAILED
but does not establish that failure has a settlement timestamp. The changed-intent
lock test shows no request-lock wait; source establishes its earlier fingerprint
branch. Controlled two-thread claims and lock probes do not establish global
exactly-once semantics or eliminate every deadlock. Other coordinator tests cover
when the executor calls these transitions, using their own effect boundaries.

The mathematical design is a finite command language interpreted over durable
request/run values into a new state and correlated history. Core event contracts,
Operations record invariants and PostgreSQL conditional writes each contribute
different laws. Preserving these boundaries prevents transport handlers or runtime
adapters from becoming alternative lifecycle state machines.

Security relies on authenticated callers supplying truthful worker scopes and on
the current stored claim/fence checks. No credentials or provider connections are
handled here. BoundedEvidence constrains history shape and secret-shaped keys;
raw dependency exceptions are not promised safe for direct public exposure.
Elapsed lease expiry, new permission decisions, failed-run recovery and external
verification remain explicit integration responsibilities. This documentation adds
no new execution surface, and its source review is not fresh test or live evidence.
