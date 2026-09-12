Source: [control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py).
Maintain this document alongside its source file. Recheck caller-owned transactions,
claim/receipt predicates, read containment and ordering, timestamp conversion and
decoder strictness whenever these methods or their record/schema contracts change.

This 1,151-line store translates Operations request, run, event and coordinator
receipt records into PostgreSQL reads and writes. It also supplies bounded overview
reads and cursor pages. It owns SQL predicates, row decoding and selected argument
guards; it does not authenticate a worker, approve recovery, interpret a deployment
plan, call a runtime provider or coordinate an entire lifecycle transaction.

## Connection and transaction boundary

PostgresExecutionStore receives one connection and never commits, rolls back or
closes it. In ordinary service composition,
[PostgresUnitOfWork](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
supplies stores sharing one transaction. The lifecycle/coordinator/recovery caller
chooses when to lock, combine writes and request commit. A method returning a record
is not independent evidence that the transaction has committed.

FOR UPDATE and advisory transaction locks last for the caller's transaction.
Conditional UPDATE statements also acquire their normal PostgreSQL write locks,
but that does not establish the surrounding service's full lock order. Calling
these methods on an autocommit connection loses the multi-statement transaction
boundary expected by claim observation and grouped lifecycle writes.

SQL values are bound parameters. Dynamic SQL fragments here select fixed lock or
cursor-seek clauses, not caller-supplied SQL. Missing-row and decoding failures
are not uniformly sanitized; services must translate appropriate public errors.
SQL constraint and unexpected connection exceptions ordinarily propagate.

## Request admission, lookup and idempotency locks

add_request inserts all request identity, status, actor/time, approval-reference,
idempotency and optional claim columns, encoding timestamps first. It returns the
supplied record, not a re-read database-normalized copy. It does not re-evaluate
approval or current workspace/session policy, and unlike receipt insertion it
does not demand an exact outer record type before field access.

get_request reads by request ID and raises a candidate-bearing KeyError when
missing. get_request_for_update performs the locked variant and uses a fixed
missing message. request_for_idempotency reads by workspace/key and returns None
on no match. Those reads reconstruct typed request records; finding a matching
key does not itself compare intent or grant replay authority.

lock_admission_idempotency takes pg_advisory_xact_lock over a hashed
execution-admission:workspace:key namespace. It supports serialization before
the request row exists. lock_command_idempotency uses the distinct
execution-command:run:key namespace, validating canonical run identity and an
exact nonempty key of at most 200 characters without ASCII controls below 32.
The namespaces scope cooperating callers' locks; the methods do not automatically
wrap every subsequent store operation or replace uniqueness constraints.

## First claims and database-time observations

claim_request validates an exact integer duration from 1 through 3600, locks and
decodes the request, and returns None unless its current status is QUEUED. A
missing request raises KeyError. The subsequent guarded update changes status to
CLAIMED, installs the supplied worker, sets generation 1 and samples database
clock_timestamp once for claimed_at and expiry plus duration.

Sampling occurs after the request row lock has been acquired. The store does not
accept a caller-created generation or use the lifecycle service clock. It also
does not check that no run exists; the
[lifecycle service](../lifecycle.py.md) owns that admission condition and groups
claim creation with run, opening event and action writes. Repeating claim_request
for an already claimed worker returns None, not a renewed lease or replay result.

observe_request_lease_for_update obtains the locked request and requires claim
evidence, then samples clock_timestamp in a separate query while that lock is
held. Expiry is lease_expires_at <= observed_at. It returns the private frozen
_ExecutionLeaseObservation(request, observed_at, expired), which itself has no
post-init validation. Observation does not renew, abandon or otherwise act on an
expired lease. Correct multi-statement observation assumes a caller-held transaction.

## Recovery selectors and conditional claim mutation

get_latest_run_for_request_for_update validates a recovery request ID and selects
the greatest attempt for that request, LIMIT 1 FOR UPDATE. It locks the selected
run, not the request, and has a fixed missing-run error. The request-scoped
get_run_for_request_for_update validates both IDs and selects using both request
and run predicates, also with a fixed missing message. These selectors establish
request/run matching, not workspace authorization or recovery eligibility.

rotate_request_claim requires exact ExecutionLeaseFence values, an expected
generation below the maximum and replacement generation exactly one greater.
It validates canonical observed_at and a 1..3600 duration, then updates only a
CLAIMED request matching the expected worker/generation. It installs replacement
worker/generation and uses supplied observed_at for new claim/expiry times.
No match returns None.

This update does not itself compare the old expiry, sample database time, inspect
run status or require a particular same-worker/takeover relationship. The caller
must first establish which recovery action is authorized and which observation
is authoritative. The pair guard permits either same or different replacement
worker; decision-specific worker rules belong to the recovery language/service.

abandon_request_claim validates request, exact expected fence and observed_at.
Its update additionally requires lease_expires_at <= the supplied observation,
then marks the request ABANDONED and clears every claim column. It returns None
when the conditional update does not match. It does not write a recovery decision,
event or run settlement itself. Expiry is checked against supplied canonical time,
not a fresh database clock sample inside this method.

The recovery request guard accepts exact nonempty str of at most 512 characters
and rejects characters below ASCII 32; it is not Core RunId's grammar. Fence
constructors and the pair guard provide different levels of validation. Exact
outer types are not a reconstruction of objects deliberately forged around
their constructors.

## Coordinator command receipts

add_command_receipt requires an exact ExecutionCommandReceiptRecord and inserts
the run/key/fingerprint, worker, complete authority-scope list, generation, effect
budget, admission time, initial-run descriptor and incomplete/completed fields.
The budget is canonical positive decimal text in SQL, avoiding narrowing to a
PostgreSQL integer column. Descriptor helpers serialize run lineage, timestamps,
metadata, coordinator status, effect count and optional activity ID.

command_receipt_for_idempotency validates run/key, selects that pair and optionally
adds FOR UPDATE. It returns None for absence or a fully decoded receipt. The
for_update argument chooses the clause by truthiness rather than an exact-bool
guard. Lock acquisition for a not-yet-existing receipt is the separate advisory
lock method and remains a caller responsibility.

complete_command_receipt requires an exact ExecutionCommandResultRecord, encodes
the completion time/result and updates only the matching run/key/fingerprint whose
receipt is INCOMPLETE with NULL completion time and result. A completed receipt
is not overwritten; zero matching rows yields None. The method neither checks
the current worker claim nor chooses a safe replay/retry policy.

The returned row is reconstructed through
[receipt record contracts](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py).
These require canonical scopes, exact positive generation and effect budget,
the recomputed intent fingerprint, canonical admission/completion timestamps,
completion not before admission, result effects within budget and unchanged run
lineage between initial and result snapshots. An incomplete receipt cannot carry
completion data; completed data must agree with the receipt's run.

The fingerprint includes the coordinator command domain, run, worker, complete
scope tuple, generation and decimal budget. This differs from lifecycle
operation-action fingerprints. Exact type guards on insertion/completion do not
independently rerun every nested constructor before SQL. RETURNING decoding can
discover a bad completion after the UPDATE has executed; the surrounding unit
of work must roll it back on that error. Do not generalize that behavior to an
autocommit caller.

The [coordinator](../coordinator.py.md) separately owns admission of an incomplete
receipt, later completion, interrupted-command handling and whether a completed
result can be replayed. This store does not interpret an incomplete receipt as
permission to redispatch a provider effect.

## Run storage and status updates

add_run inserts run/plan/request lineage, attempt/prior-run identity, status,
creation/start/settlement times and metadata, returning the supplied record.
get_run and get_run_for_update validate Core RunId and decode the matching row;
their missing errors include the run candidate. Record constructors and schema
constraints enforce run identity, retry and timing shape on the normal paths.

compare_and_set_run_status validates run identity and encodes optional times.
It updates only the requested current status with settled_at IS NULL. It writes
the replacement status, uses supplied started_at when present, and preserves an
existing settled_at with COALESCE. The method does not choose legal lifecycle
edges or authorize the worker. In particular, a caller supplying started_at can
replace a previous start timestamp; lifecycle avoids that by only supplying a
start when the locked run lacks one. A mismatched/settled row returns None.

runs_for_request returns all matching rows ordered by attempt then run ID.
runs_for_plan returns all matching rows ordered by created_at then run ID. Neither
has a result bound or a workspace predicate. They are internal reads whose caller
must already establish containment and an appropriate use for the collection.

overview_runs is a separate bounded surface: a materialized CTE selects up to
101 plan runs, then returns at most 100 with an overflow flag. More than 100
produces None, meaning unavailable under this bound; no rows produces an empty
tuple. The ordering is attempt then run ID. It does not truncate a larger history
and pretend it is complete or select the authoritative retry branch itself.

overview_receipts returns at most two rows for a canonical run ID. If any incomplete
receipt exists, only incomplete receipts are eligible. Otherwise it returns the
newest completions. Ordering is completed_at descending with NULLS FIRST, then
admitted_at descending and key for deterministic ordering. The key does not break
semantic ambiguity between equal completion times. The
[overview projection](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/operator_overview.py)
interprets multiple unresolved admissions or tied latest completions as ambiguous;
the store supplies bounded evidence, not a winner or automatic recovery choice.

## Events and caller-owned append ordering

add_event serializes activity ID, bounded evidence, optional failure and optional
typed recovery evidence into JSON, inserts event/run/ordinal/type/time and returns
the supplied record. It does not allocate an ordinal, lock the run or update run
status itself. next_event_ordinal separately validates/locks the run and returns
MAX(ordinal)+1, defaulting to 1. Safe concurrent append requires keeping that lock
and the insert inside the same caller transaction.

get_event reads by event ID and uses a candidate-bearing missing KeyError.
events_for_run validates run identity and returns the full decoded history ordered
by ordinal without a size limit. It does not prove workspace membership or reduce
the history into a schedule. Durable-event-to-Core-journal interpretation belongs
to another owner.

## Cursor pages and containment boundaries

run_page requires PLAN_RUNS and delegates normal request/cursor validation to the
[read-page value contracts](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_pages.py).
It additionally validates a supplied cursor's run ID. SQL joins run to request,
plan and session, checks their lineage and workspace agreement, and restricts to
the requested workspace/plan. Continuation uses the strict ascending tuple seek
(created_at, run_id) > (cursor instant, cursor item). It fetches limit+1 rows.

event_page requires RUN_EVENTS, validates the scope's run ID and uses a strict
ascending (ordinal, event_id) seek with limit+1. Its SQL restricts only by run ID;
workspace in the typed scope does not itself become a query predicate. The actual
[operations-history read service](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/operations_history.py)
first requires the workspace, reads the run and its admitted request, checks that
request's workspace, then calls event_page. Missing/foreign containment is rejected
before the event query. Direct store use must preserve that boundary.

Both methods decode all returned candidates, including a possible extra row,
construct correlated cursors and use ReadPage.from_candidates to expose at most
limit items. A next cursor comes from the last exposed item only when the extra
candidate exists. The helper checks candidate count and cursor correlation; SQL
owns ordering here. A malformed extra candidate is not silently skipped.

The page contracts bound these collections to 100 items and require matching
scope/collection/cursor types. Temporal cursors use exact six-digit microsecond
UTC form; ordinal cursors use positive ordinals. These methods do not create a
stable snapshot across multiple calls or hide deletions/backdated insertions.
The caller's transaction isolation and later database state still matter.

## Decoders: strict envelopes and intentionally partial checks

_execution_request reconstructs identity, status, idempotency and optional claim
from positional columns. Claim presence is decided by claim_worker_id: when it
is NULL, the decoder does not independently inspect the other claim columns.
Normal consistency relies on schema and record contracts. _activity_run rebuilds
admission, retry, status, timestamps and bounded metadata. Neither decoder wraps
all failures in one generic store error or validates arbitrary row tuple lengths.

Receipt JSON is stricter. _run_from_descriptor requires an exact dict with exactly
the ten run-descriptor keys and exact-dict metadata. _result_from_descriptor
requires exactly run/status/effects_attempted/activity_id. _command_receipt requires
an exact list of scope values, parses canonical decimal budget and rebuilds the
full receipt. Selected TypeError/ValueError failures become fixed OperationsRecordError
messages with suppressed chaining; these catches are not universal error handling.

_activity_event requires an object payload and object evidence, defaulting missing
evidence to empty. It rebuilds ActivityEventRecord with optional activity, failure
and recovery data. Extra event-payload keys are not rejected. _failure_evidence
requires category/code/message, permits additional keys and defaults missing
details to an empty object. Its errors are not normalized like receipt-envelope
errors. This decoder is not an exact-key failure-envelope admission boundary.

Recovery evidence uses exact outer and nested fence key sets. It reconstructs
RecoveryDecisionKind, Core RunId and exact fence values, then
ExecutionLeaseRecoveryEvidence enforces decision-specific generation/worker
relationships. Selected ValueError failures become the fixed malformed-recovery
error. TypeError and unexpected dependency exceptions are not all caught by that
helper. Event-record construction additionally checks recovery/event/run agreement
and rejects contradictory ordinary evidence or failure for recovery decisions.

_json uses compact sorted JSON; value owners supply shape and secret/evidence
constraints before encoding. Ordinary timestamp columns use
[PostgreSQL temporal codecs](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/temporal.py)
to admit canonical UTC text and decode aware datetimes. Cursor codecs preserve
the separate six-digit format. Receipt JSON run timestamps are reconstructed as
record text fields, not passed through those column codecs; receipt admission and
completion timestamps have their own canonical-instant checks.

## Evidence, security and handoff

The fully read [lifecycle suite](../../../tests/test_run_lifecycle.py.md) exercises
real PostgreSQL claim time and lock waits, selected lease observations, run/event
round trips and actual grouped-write rollback. The fully read
[coordinator suite](../../../tests/test_execution_coordinator.py.md) exercises
receipt admission/replay, large-budget text storage, malformed retained receipt
rejection and interruption behavior through its own adapters. Neither proves
all store methods against every malformed row or every transaction isolation mode.

The separately read test_postgres_execution_lease_recovery_store.py is a
database-free contract suite: recording/no-SQL connections prove selected argument
rejection before SQL, request/run selector predicates, exact duration/identity
boundaries and propagation of unexpected SQL errors. Selected ordinal/temporal
page tests inspect query shape with recording connections; a selected service
containment test verifies no event query on missing/foreign parents. This source
review did not execute these tests or claim whole pagination-suite coverage.

Security and data safety depend on service-level permission/containment, correct
transaction composition, schema constraints and bounded/redacted value contracts.
Raw internal reads and candidate-bearing errors must not become unauthenticated
public endpoints. Rotation predicates do not authorize takeover; completed receipt
rows do not prove provider success; expiry observation does not authorize mutation.
No new network or execution surface is introduced by this documentation. Provider
cleanup, secret delivery, retention and ambiguous external outcomes are outside
this store, and no new live or green-test evidence was produced here.
