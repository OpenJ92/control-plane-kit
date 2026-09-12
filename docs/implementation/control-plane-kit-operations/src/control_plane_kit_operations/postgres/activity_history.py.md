Source: [control-plane-kit-operations/src/control_plane_kit_operations/postgres/activity_history.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/activity_history.py).
Maintain this document alongside its source file. Recheck service/store ownership,
transaction and lock boundaries, plan lineage, approval-subject correlation,
pagination containment and decoder behavior when the source or its contracts change.

This 890-line store persists operation sessions, ordered actions, activity plans,
approval requests and approval decisions. It supplies detail/idempotency lookups,
unbounded internal collections, cursor pages and bounded overview queries. It
interprets record values and query requests as SQL; it does not create plan
semantics, approve an actor, execute a deployment or decide which retained command
may be replayed.

## Caller-owned transactions and append discipline

PostgresActivityHistoryStore holds the supplied PostgresConnection. It never
commits, rolls back or closes it. The ordinary
[unit of work](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
shares one connection among stores so a service can commit a session and initial
action, an approval and its action, or a graph-pointer change and its history
together. Exceptional unit-of-work exit rolls back the grouped writes. Calling
individual store methods on an autocommit connection does not provide that atomicity.

The add methods return their supplied record after insertion, not a normalized
readback or an independently committed receipt. They access typed-record fields
without an exact outer-type admission guard. Constructors, codecs and schema
constraints supply different protections; these methods are not public JSON or
hostile-object boundaries.

Session closure/cancellation is the only session-state update exposed here.
Actions, plans and approvals use insert/read methods without a general update or
delete method. That API shape does not make arbitrary database access immutable,
and it does not prevent direct insertion of a semantically inappropriate action
unless a service or schema constraint rejects it.

lock_session_idempotency uses a transaction advisory lock over the hashed
operation-session:workspace:key namespace. lock_action_idempotency uses the
separate operation-action:session:key namespace. Both allow cooperating callers
to serialize before a corresponding row exists. They do not compare fingerprints,
validate command authority or automatically wrap later calls. Input text in the
lock name is a bound SQL parameter, not executable SQL.

next_action_ordinal locks the session row FOR UPDATE and returns MAX(ordinal)+1,
defaulting to 1. The caller must keep that lock through insertion in the same
transaction; add_action does not acquire it or allocate the ordinal itself.
The schema's unique session/ordinal pair and partial idempotency indexes provide
additional consistency, not an alternative to service transaction ordering.

## Session records and terminal updates

add_session writes identity, workspace, actor, title, status, timestamps, metadata
and optional idempotency/fingerprint. get_session and get_session_for_update read
by session ID, the latter with a row lock. Missing sessions raise KeyError that
includes the supplied ID. session_for_idempotency selects workspace/key and returns
None when absent; a returned match is data for a service to validate, not automatic
replay acceptance.

sessions_for_workspace returns every matching session ordered by created_at then
session_id, with no limit or open-status restriction. session_page accepts
ACTIVITY_SESSIONS or OPEN_SESSIONS, filters by workspace, adds status='open' only
for the latter, and applies ascending (created_at, session_id) cursor seeking.

transition_open_session accepts only CLOSED or CANCELLED replacement values,
encodes closed_at and updates a row only while status='open'. It returns the
decoded changed record or None if nothing matched. The method does not emit an
operation action or check who requested closure. The replacement guard uses enum
membership, not an exact-type check before later .value access.

The actual [operation workflow service](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/workflows.py)
combines start-session and initial-action writes; compares retained fingerprints;
locks and checks OPEN before a new manual action or terminal transition; and
groups the terminal update with its action. Its start_in_unit_of_work entry point
deliberately leaves commit to an outer caller. These service conditions must not
be attributed to a bare add_session, add_action or transition_open_session call.

## Action history and idempotency lookup

add_action inserts action ID, session, ordinal, closed action kind, actor, JSON
payload, creation time and optional key/fingerprint. action_for_idempotency reads
session/key and returns a decoded action or None without comparing intent.
actions_for_session returns all actions ordered by ordinal, without a result bound
or workspace predicate.

action_page accepts SESSION_ACTIONS, restricts by session ID and uses the strict
ascending tuple seek (ordinal, action_id). A typed scope contains workspace too,
but this SQL does not check it. The read service establishes session containment
before calling the page method. An action named SET_DESIRED_GRAPH is a recorded
command/history value here; inserting it does not itself change graph pointers.

The [record contracts](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
require session metadata and action payload to be Mapping values. They are not
BoundedEvidence wrappers and do not inherit its byte/depth/secret-key policy merely
because the data is history. Jsonb adapts these mappings for PostgreSQL; callers
must preserve the relevant payload and redaction contract. The store does not
reinterpret every action payload according to its action kind.

## Plan insertion proves selected lineage in SQL

add_plan first rejects missing base or desired realized-projection IDs. Although
ActivityPlanRecord can represent absent projection IDs, this persistence path
requires both. It encodes the actual ActivityPlan with the
[Core plan codec](../../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/codec.py)
and supplies the record's authored IDs, projection IDs, desired revision, status
and timestamp to an INSERT ... SELECT.

That statement joins the session and both projection records. Both projections
must belong to the session workspace, and each must name the corresponding base
or desired authored source. Missing or incongruent joined rows cause no insertion
and the fixed complete-graph-lineage OperationsRecordError. Successful insertion
returns the supplied plan record.

This check does not require the session to be OPEN, compare the requested revision
with the workspace's current desired revision, prove that the activities realize
the graph difference, or enforce approval. A plan can have valid stored lineage
and still be unsuitable for execution. Those are distinct preparation/admission
service decisions.

get_plan reads by plan ID and raises a candidate-bearing KeyError on absence.
plans_for_session returns the whole ordered session collection. plan_page accepts
SESSION_PLANS, filters by session ID and seeks by (created_at, plan_id). Those
session-based reads do not independently check workspace membership.

overview_plans is a different bounded query. It joins sessions, requires the
specified workspace and an OPEN session, PLANNED plan status and exact desired
authored/projection/revision coordinates. It returns at most two rows ordered by
creation time and ID. Ordering supplies stable evidence, not authority to choose
one plan when several are eligible. It does not independently revalidate every
base-lineage or payload condition.

overview_pending_approvals selects at most two requests for a plan that have no
decision, ordered by request time/ID. It deliberately does not filter by session,
so a malformed cross-session request is visible to the
[overview projection](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/operator_overview.py).
That caller validates subject/session/scope correlation and treats multiple
eligible plans or approvals as ambiguity. The store does not elect a winner or
auto-approve either row.

## Approval requests and decisions remain different facts

add_approval_request persists the closed subject's kind, descriptor and review
digest, along with its plan or rotation lookup column and ordinary request
identity, actor/time, required scope, risk, destructive flag, comment and optional
key/fingerprint. Activity-plan subjects populate plan_id; gateway-key-rotation
subjects populate rotation_id. The insert does not itself evaluate a policy,
cross-check the subject's workspace against its session or prove an actor holds
the stored required scope.

get_approval_request reads by request ID. approval_request_for_idempotency selects
session/key, approval_requests_for_session returns the unbounded ordered session
history, and approval_request_for_rotation selects by rotation ID. The first
raises a candidate-bearing KeyError on absence; the two singular lookup methods
return None. Rotation lookup does not prove the returned request was approved.

add_approval_decision persists decision/request/actor, decision kind, scope,
timestamp, comment and optional key/fingerprint as a separate row. Singular
lookups select by request or request/key and return None for no match. The schema
allows only one decision row per request and unique non-NULL request/key pairs.
The insert itself neither reads the request's required scope nor checks an open
session, self-approval restrictions or current actor authority.

The actual [approval command service](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/approvals.py)
owns those decisions. For a new plan request it locks the action key and session,
checks plan/session linkage and readiness, consults policy and derives risk/scope
requirements. For a new decision it checks request/session linkage, absence of a
previous decision and the relevant plan/rotation approval policy. It groups the
request or decision insert with a corresponding action and compares fingerprints
on replay. A stored request is pending intent, not permission to execute it.

## Approval pages and other cursor contracts

approval_page accepts SESSION_APPROVALS; pending_approval_page accepts
PENDING_APPROVALS. Their shared query joins requests to sessions and left-joins
the single decision. Session pages restrict request.session_id and include decided
and undecided requests. Pending pages restrict session.workspace_id and require
the joined decision to be absent. Pending means no decision row, not rejected,
expired or awaiting provider observation. This query has no OPEN-session filter.

Each returned row becomes _ApprovalReadProjection(request, optional decision).
Decision presence is selected by the joined decision ID. The frozen projection
has no extra post-init correlation validation; the query join and typed record
decoders supply the normal relationship. Both page forms seek strictly after
(requested_at, request_id).

All page methods fetch limit+1, decode every candidate and pass correlated cursors
to [ReadPage.from_candidates](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_pages.py).
At most limit items are exposed; a next cursor is taken from the last exposed item
only when an extra candidate exists. A malformed extra row is not silently skipped.
The page language supplies collection/scope/cursor agreement and the 100-item bound
for these collections; SQL supplies order. Temporal cursors use canonical six-digit
microsecond UTC text, while action cursors use ordinal and ID.

Workspace predicates are present in session pages and workspace pending approvals.
Action, plan and session-approval pages use their parent session alone. The actual
[operations-history read service](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/operations_history.py)
requires the workspace and calls _session_in_workspace before those child queries.
Direct store consumers must retain that containment boundary. Cursor pagination
does not establish a frozen snapshot across calls or eliminate changes in membership
as sessions close and approval decisions are added.

## Decoding and the meaning of a review digest

_session_record rebuilds status/timestamps and the session record, including its
OPEN/closed_at shape rules. _action_record rebuilds a closed kind through
_action_kind, trying OperatorCommandKind first and LifecycleOperationKind second.
Neither decoder validates a kind-specific action payload schema, proves the actor
was authorized or wraps all errors in one public-safe error type.

_plan_record decodes DEFAULT_ACTIVITY_PLAN_CODEC payload into an ActivityPlan and
rebuilds its persisted lineage record. The codec's entry point checks its versioned
envelope, decodes activity values and requires lossless encode/decode agreement.
Reading a plan here does not rerun the insertion joins or compare it with current
workspace pointers.

_approval_request_record decodes a closed subject, verifies kind and recomputed
review digest against their stored columns, then verifies plan/rotation lookup
column consistency. Activity-plan subjects require matching plan_id and absent
rotation_id; rotation subjects require the reverse. It then reconstructs scope,
risk, destructive flag, timestamps and the ApprovalRequestRecord. Inconsistent
redundant subject data raises ValueError rather than being silently accepted.

The [Core approval-subject language](../../../../../../control-plane-kit-core/src/control_plane_kit_core/approval_subjects.py)
requires exact descriptor key sets for its two variants. The activity-plan review
digest hashes the tagged plan identity, not the plan's entire activity descriptor;
it relies on the persisted plan's identity/immutability contract. The rotation
digest hashes its explicit review descriptor, including rotation intent digest
and verifier-role declarations. Neither digest proves approval, actor authority,
secret availability or successful key rotation.

_approval_decision_record reconstructs the closed decision kind, scope and record
fields. _approval_read_projection slices request and optional decision columns;
it is not a separate decision service. _rotation_id is only a lookup-column
projection. Ordinary and optional timestamp conversion delegates to the
[PostgreSQL temporal codecs](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/temporal.py),
which admit canonical UTC text and decode aware datetimes. The store samples no
clock; timestamps come from callers.

Record/codec errors, unknown enum values, malformed rows and unexpected SQL failures
can propagate. Missing detail lookups often include candidate identifiers, and
_action_kind can retain the first enum failure as exception context if both fail.
This owner is not a universal exception-redaction layer or an exact-shape validator
for arbitrary forged positional rows.

## Evidence and operational limits

Selected PostgreSQL workflow tests were read for committed session/action creation,
same-intent replay and changed intent, action ordinals, closed-session refusal,
reserved manual actions and actual late action-ID collision rollback. Selected
approval tests cover replay without duplicate history, conflicting requests/second
decisions and late action collisions that roll back request or decision rows.
Those are service composition tests against a real database fixture; they do not
prove those rules reside in individual store methods. Their fixtures install and
truncate an isolated schema; no fixture or test was executed for this note.

Selected recording-connection ordinal tests check action-page seek/limit shape and
continuation cursor selection. The previously reviewed lifecycle, coordinator and
advancement suites exercise this store's action history inside their transactions.
This is not a claim of full workflow, approval, pagination, policy or codec-suite
review, nor new green test evidence.

The mathematical boundary is record/plan/approval-subject data interpreted into
durable rows and correlated reads. Services own grouped intent, authorization,
replay disposition and publication of an action alongside its state change. The
store owns selected persistence invariants and query shapes; it should not become
an alternative planner or policy engine.

Security depends on those service boundaries, caller-supplied safe payloads and
correct transaction use. Metadata/actions are not automatically bounded secret-safe
evidence, and a row carrying a scope is not proof of permission. This documentation
introduces no new network or execution surface. Retention, deletion, provider
effects, secret custody and ambiguous-commit recovery are outside this owner;
no source/tests, provider state or live acceptance changed during this review.
