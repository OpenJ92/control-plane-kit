"""Explicit recorded history, distinct from lawful execution admission.

Read/codec/schema fixtures retain their original relationships and timestamps.
Their immutable footprint uses production derivation over real original source;
these raw SQL rows do not claim approval, dispatch or supported-writer evidence.
Service execution fixtures use the real admission helper separately.
"""

from control_plane_kit_core.planning import ActivityPlan
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.admission import ExecutionAdmissionCommandService, RequestPlanExecution
from control_plane_kit_operations.plan_derivation import encode_stored_activity_plan
from control_plane_kit_operations.postgres.receiver_execution_scopes import _ExecutionScopeStorage
from control_plane_kit_operations.records import ExecutionRequestIdentity
from control_plane_kit_operations.workflows import IdempotencyKey


def empty_plan_payload():
    return encode_stored_activity_plan(ActivityPlan(()), profile=None)


def clone_recorded_request(connection, *, source_id="request-a", **changes):
    columns = ("request_id", "workspace_id", "session_id", "plan_id", "status", "requested_by",
        "requested_at", "approval_request_id", "approval_decision_id", "idempotency_key", "intent_fingerprint",
        "claim_worker_id", "claim_generation", "claimed_at", "lease_expires_at")
    original = connection.execute("SELECT " + ",".join(columns)
        + " FROM cpk_execution_requests WHERE request_id=%s", (source_id,)).fetchone()
    insert_recorded_request(connection, **(dict(zip(columns, original)) | changes))


def insert_recorded_request(connection, *, request_id="request-a", workspace_id="workspace-a",
                            session_id="session-a", plan_id="plan-a", **fields):
    """Insert deliberate history; never manufacture a semantic admission receipt."""
    insert_recorded_requests(connection, ({"request_id": request_id, "workspace_id": workspace_id,
        "session_id": session_id, "plan_id": plan_id, **fields},))


def insert_recorded_requests(connection, requests):
    """Bulk recorded population with shared originals, but identity-specific witnesses."""
    storage = _ExecutionScopeStorage(connection)
    rows, scopes = [], []
    for fields in requests:
        row, derived = _recorded_request(storage, **fields)
        rows.append(tuple(row.values()))
        scopes.extend((row["request_id"], row["workspace_id"], ordinal, scope.scope_kind,
                       scope.runtime_id, scope.node_id) for ordinal, scope in enumerate(derived.scopes))
    if not rows:
        return
    with connection.cursor() as cursor:
        cursor.executemany("INSERT INTO cpk_execution_requests (" + ",".join(row) + ") VALUES ("
                           + ",".join("%s" for _ in row) + ")", rows)
        if scopes:
            cursor.executemany("INSERT INTO cpk_execution_receiver_scopes "
                "(request_id,workspace_id,scope_ordinal,scope_kind,runtime_id,node_id) VALUES (%s,%s,%s,%s,%s,%s)", scopes)


def _recorded_request(storage, *, request_id="request-a", workspace_id="workspace-a",
                      session_id="session-a", plan_id="plan-a", **fields):
    identity = ExecutionRequestIdentity(request_id, workspace_id, session_id, plan_id)
    derived = storage.derive(identity)
    row = {
        "request_id": request_id, "workspace_id": workspace_id, "session_id": session_id, "plan_id": plan_id,
        "status": "queued", "requested_by": "operator-a", "requested_at": "2026-07-22T12:04:00Z",
        "approval_request_id": "approval-request-a", "approval_decision_id": "approval-decision-a",
        "idempotency_key": "execute-a", "intent_fingerprint": "fingerprint-a",
    }
    allowed = set(row) | {"claim_worker_id", "claim_generation", "claimed_at", "lease_expires_at"}
    if set(fields) - allowed:
        raise ValueError("unexpected recorded request field")
    row.update(fields)
    row.update(receiver_scope_count=len(derived.scopes), receiver_scope_digest=derived.source_digest)
    return row, derived


def admit_fixture_plan(case, *, request_id="request-a", session_id="session-a", plan_id="plan-a",
                       approval_request_id="approval-request-a", key="execute-a",
                       requested_at="2026-07-22T12:04:00Z", workspace_id="workspace-a",
                       actor_scopes=(PolicyScope.PLAN_EXECUTE,)):
    """Compose the production admission owner; no fixture admission algorithm."""
    identities = iter((request_id, "action-admit-fixture-" + request_id))
    return ExecutionAdmissionCommandService(case.unit_of_work, clock=lambda: requested_at,
        id_factory=lambda: next(identities)).execute(RequestPlanExecution(
            workspace_id=workspace_id, session_id=session_id, plan_id=plan_id,
            approval_request_id=approval_request_id, actor_id="operator-a",
            actor_scopes=actor_scopes, idempotency_key=IdempotencyKey(key)))
