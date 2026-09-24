"""Caller-transactional immutable health evidence and retained owner joins."""
from __future__ import annotations

from psycopg import IntegrityError
from psycopg.errors import UniqueViolation

from control_plane_kit_core.node_control import NodeControlContractError
from control_plane_kit_core.node_control_surface_reads import (
    WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationProfile,
)
from control_plane_kit_core.operations import ActivityEventKind, EffectAttemptIdentity
from control_plane_kit_core.planning import ActivityId, ObserveNodeHealth, PlanGraphSide
from control_plane_kit_core.secrets import health_signing_intent_for
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, GraphDescriptorError, validate_graph
from control_plane_kit_operations.health_effect_preparations import (
    HealthEffectPreparationCodec, HealthEffectPreparationRecord, HealthEffectPreparationError,
    HealthEffectPreparationConflict, HealthEffectPreparationCorrupt, health_effect_attempt_wire_id,
)
from control_plane_kit_operations.delegation_signing_keys import delegation_signing_key_registration_id_for
from control_plane_kit_operations.runtime_management_targets import (
    ManagementHealthTargetProjectionError, project_management_health_target,
)
from control_plane_kit_operations.secret_providers import (
    AuthorizeSecretUse, SecretProviderRegistrationError, authorized_secret_use_for, secret_use_correlation_for,
)
from control_plane_kit_operations.postgres.activity_history import PostgresActivityHistoryStore
from control_plane_kit_operations.postgres.delegation_signing_key_store import DelegationSigningKeyStore
from control_plane_kit_operations.postgres.effect_attempt_intent_store import EffectAttemptIntentStore
from control_plane_kit_operations.postgres.effect_attempt_store import EffectAttemptStore
from control_plane_kit_operations.postgres.execution import PostgresExecutionStore
from control_plane_kit_operations.postgres.graph_store import PostgresRealizedGraphProjectionStore
from control_plane_kit_operations.postgres.secret_provider_store import (
    SecretProviderStore, SecretReferenceStore, SecretUseAuthorizationStore,
)

_INPUT = "health effect preparation input or retained evidence is invalid"
_CORRUPT = "retained health effect preparation is invalid"
_COLUMNS = ("run_id", "activity_id", "attempt", "workspace_id", "logical_request_id",
    "request_fingerprint", "original_event_id", "base_realized_projection_id",
    "desired_realized_projection_id", "transit_key_registration_id", "workload_key_registration_id",
    "transit_authorization_id", "workload_authorization_id", "transit_issuer", "transit_jti",
    "workload_issuer", "workload_jti", "preimage")
# Protect transport even when retained constraints have been removed or corrupted.
_SELECT = "SELECT " + ", ".join(
    name if name == "attempt" else
    f"CASE WHEN octet_length({name}) BETWEEN 1 AND {16384 if name == 'preimage' else 2048} "
    f"THEN {name} ELSE NULL END AS {name}" for name in _COLUMNS
) + " FROM cpk_health_effect_preparations AS preparation"


class HealthEffectPreparationStore:
    """Insert once or reconstruct exact evidence on the caller's connection."""

    def __init__(self, connection: object) -> None:
        self._connection = connection

    def get(self, identity: EffectAttemptIdentity) -> HealthEffectPreparationRecord:
        health_effect_attempt_wire_id(identity)  # exact nominal validation before SQL
        row = self._connection.execute(_SELECT +
            " WHERE run_id=%s AND activity_id=%s AND attempt=%s",
            (identity.run_id.value, identity.activity_id, identity.attempt)).fetchone()
        if row is None:
            raise KeyError("health effect preparation was not found")
        return _reconstruct(self._connection, row)

    def insert_absent(self, record: HealthEffectPreparationRecord) -> HealthEffectPreparationRecord | None:
        preimage = HealthEffectPreparationCodec().encode_canonical_bytes(record)
        _require_owners(self._connection, record, HealthEffectPreparationError,
            lock_attempt=True)
        failure = None
        inserted = None
        try:
            inserted = self._connection.execute(
                "INSERT INTO cpk_health_effect_preparations (" + ", ".join(_COLUMNS) + ") VALUES ("
                + ", ".join("%s" for _ in _COLUMNS) + ") "
                "ON CONFLICT (run_id, activity_id, attempt) DO NOTHING RETURNING attempt",
                _witnesses(record) + (preimage,)).fetchone()
        except UniqueViolation:
            failure = HealthEffectPreparationConflict
        except IntegrityError:
            failure = HealthEffectPreparationError
        if failure is not None:
            raise failure(_INPUT)
        if inserted is None:
            return None
        if inserted != (record.identity.attempt,):
            raise HealthEffectPreparationError(_INPUT)
        return record


def _witnesses(record):
    return (record.identity.run_id.value, record.identity.activity_id, record.identity.attempt,
        record.workspace_id, record.logical_request_id, record.request_fingerprint,
        record.original_event_id, record.base_realized_projection_id, record.desired_realized_projection_id,
        record.transit_key_registration_id, record.workload_key_registration_id,
        record.transit_authorization_id, record.workload_authorization_id,
        record.transit_grant.issuer, record.transit_grant.jti,
        record.workload_grant.issuer, record.workload_grant.jti)


def _reconstruct(connection, row):
    record = None
    try:
        if len(row) != len(_COLUMNS):
            raise ValueError
        candidate = HealthEffectPreparationCodec().decode_canonical_bytes(row[-1])
        if tuple(row[:-1]) != _witnesses(candidate):
            raise ValueError
        record = candidate
    except (ValueError, TypeError, KeyError):
        pass
    if record is None:
        raise HealthEffectPreparationCorrupt(_CORRUPT)
    _require_owners(connection, record, HealthEffectPreparationCorrupt)
    return record


class _OwnerMismatch(Exception):
    """Explicit refusal of retained data; contains no candidate material."""


class _OwnerAdapterFailure(Exception):
    """Carry adapter provenance past retained-contract exception categories."""

    def __init__(self, failure):
        super().__init__("owner read adapter failed")
        self.failure = failure


class _OwnerReadBoundary:

    """Distinguish adapter failures from a typed owner's decode/missing refusal."""

    def __init__(self, connection):
        self.connection = connection
        self.failure = None

    def _call(self, action, *args):
        try:
            return action(*args)
        except (ValueError, TypeError, KeyError) as error:
            self.failure = error
            raise

    def execute(self, query, parameters=()):
        return _OwnerCursorBoundary(self, self._call(self.connection.execute, query, parameters))


class _OwnerCursorBoundary:
    def __init__(self, boundary, cursor):
        self.boundary, self.cursor = boundary, cursor

    def fetchone(self):
        return self.boundary._call(self.cursor.fetchone)


def _load_owner(connection, owner, method, *args):
    boundary = _OwnerReadBoundary(connection)
    failed = False
    try:
        return getattr(owner(boundary), method)(*args)
    except (ValueError, TypeError, KeyError):
        failed = True
    # Never translate an execute/fetch exception, even when its type is also
    # used by the owner's retained row decoding or missing-record contract.
    if boundary.failure is not None:
        raise _OwnerAdapterFailure(boundary.failure)
    if failed:
        raise _OwnerMismatch


def _require_owners(connection, record, error_type, *, lock_attempt=False):
    valid = False
    adapter_failure = None
    try:
        _check_owners(connection, record, lock_attempt=lock_attempt)
        valid = True
    except _OwnerAdapterFailure as error:
        adapter_failure = error.failure
    except (_OwnerMismatch, GraphDescriptorError, ManagementHealthTargetProjectionError,
            NodeControlContractError, SecretProviderRegistrationError):
        pass
    if adapter_failure is not None:
        raise adapter_failure
    if not valid:
        raise error_type(_CORRUPT if error_type is HealthEffectPreparationCorrupt else _INPUT)


def _check_owners(connection, record, *, lock_attempt=False):
    evidence = _load_owner(connection, EffectAttemptIntentStore, "get", record.identity)
    # Serialize inserts for this immutable owner before independent unique
    # indexes compete. The caller retains the lock through commit/rollback;
    # reconstruction and schema checks never request it.
    attempt = _load_owner(connection, EffectAttemptStore,
        "get_for_update" if lock_attempt else "get", record.identity)
    intent, source = evidence.intent, evidence.intent.source
    if (evidence.identity != record.identity or evidence.request_fingerprint != record.request_fingerprint
            or evidence.original_start_event.event_id != record.original_event_id
            or evidence.original_start_event.kind is not ActivityEventKind.STEP_STARTED
            or attempt.original_start_event != evidence.original_start_event
            or attempt.state.request_fingerprint != record.request_fingerprint
            or source.workspace_id != record.workspace_id or source.run_id != record.identity.run_id
            or intent.activity_id.value != record.identity.activity_id
            or type(intent.operation) is not ObserveNodeHealth):
        raise _OwnerMismatch
    request = _load_owner(connection, PostgresExecutionStore, "get_request", source.request_id)
    run = _load_owner(connection, PostgresExecutionStore, "get_run", source.run_id.value)
    plan = _load_owner(connection, PostgresActivityHistoryStore, "get_plan", source.plan_id)
    if (request.identity.request_id != source.request_id
            or request.identity.workspace_id != source.workspace_id
            or request.identity.plan_id != source.plan_id or plan.plan_id != source.plan_id
            or run.run_id != source.run_id.value or run.plan_id != source.plan_id
            or run.admission.request_id != source.request_id
            or request.identity.session_id != plan.session_id
            or (plan.base_graph_id, plan.desired_graph_id) != (source.base_graph_id, source.desired_graph_id)
            or (plan.base_realized_projection_id, plan.desired_realized_projection_id)
                != (record.base_realized_projection_id, record.desired_realized_projection_id)):
        raise _OwnerMismatch
    base = _load_owner(connection, PostgresRealizedGraphProjectionStore, "get", record.base_realized_projection_id)
    desired = _load_owner(connection, PostgresRealizedGraphProjectionStore, "get", record.desired_realized_projection_id)
    for projection, authored in ((base, source.base_graph_id), (desired, source.desired_graph_id)):
        if projection.workspace_id != source.workspace_id or projection.source_authored_graph_id != authored:
            raise _OwnerMismatch
    selected = project_management_health_target(plan.plan, ActivityId(record.identity.activity_id),
        intent.operation, validate_graph(DEFAULT_GRAPH_CODEC.decode(base.graph_descriptor)),
        validate_graph(DEFAULT_GRAPH_CODEC.decode(desired.graph_descriptor)))
    operation = selected.operation
    authored = source.base_graph_id if operation.target.graph_side is PlanGraphSide.BASE_GRAPH else source.desired_graph_id
    target = record.request.target
    declaration = WorkloadNodeControlSurfaceDeclaration(selected.workload_surface,
        WorkloadNodeControlSurfaceDeclarationProfile.V2).identity()
    if ((target.graph_revision.value, target.node_id.value, target.provider_socket_name.value,
            record.request.runtime_id.value, record.request.kind, record.request.declaration_identity)
            != (authored, operation.node_id, operation.provider_socket_name,
                operation.target.runtime_id, operation.health_kind, declaration)
            or record.transit_grant.gateway_node_id.value != selected.gateway_node_id):
        raise _OwnerMismatch
    keys, uses = [], []
    for family, grant in (("transit", record.transit_grant), ("workload", record.workload_grant)):
        key = _load_owner(connection, DelegationSigningKeyStore, "get", record.workspace_id, grant.purpose, grant.issuer, grant.key_id)
        use = _load_owner(connection, SecretUseAuthorizationStore, "get", record.workspace_id, getattr(record, family + "_authorization_id"))
        reference = _load_owner(connection, SecretReferenceStore, "get_by_registration", record.workspace_id, use.reference_registration_id)
        provider = _load_owner(connection, SecretProviderStore, "get_by_registration", record.workspace_id, use.provider_registration_id)
        registration_id = delegation_signing_key_registration_id_for(
            workspace_id=key.workspace_id, purpose=key.purpose, issuer=key.issuer,
            public_key=key.public_key, private_key_reference=key.private_key_reference)
        if (key.registration_id != registration_id
                or key.registration_id != getattr(record, family + "_key_registration_id")
                or (key.workspace_id, key.purpose, key.issuer, key.key_id)
                    != (record.workspace_id, grant.purpose, grant.issuer, grant.key_id)
                or use.authorization_id != getattr(record, family + "_authorization_id")
                or any(item.workspace_id != record.workspace_id for item in (use, reference, provider))
                or use.reference_registration_id != reference.registration_id
                or use.provider_registration_id != reference.provider_registration_id
                or use.provider_registration_id != provider.registration_id
                or not (reference.reference == use.reference == key.private_key_reference)
                or use.intent is not health_signing_intent_for(grant.purpose)
                or (use.operation_id, use.session_id, use.run_id, use.activity_id, use.effect_id, use.probe_id)
                    != (health_effect_attempt_wire_id(record.identity), plan.session_id,
                        record.identity.run_id.value, record.identity.activity_id, None, None)):
            raise _OwnerMismatch
        fields = dict(workspace_id=use.workspace_id, reference=use.reference, intent=use.intent,
            actor_subject=use.actor_subject, operation_id=use.operation_id, session_id=use.session_id,
            run_id=use.run_id, activity_id=use.activity_id, effect_id=use.effect_id, probe_id=use.probe_id)
        if use.correlation_id != secret_use_correlation_for(**fields):
            raise _OwnerMismatch
        command = AuthorizeSecretUse(**fields, correlation_id=use.correlation_id,
            requested_at=use.requested_at, actor_scopes=())
        if authorized_secret_use_for(command, reference=reference, provider=provider) != use:
            raise _OwnerMismatch
        keys.append(key)
        uses.append(use)
    if (keys[0].registration_id == keys[1].registration_id
            or keys[0].public_key.fingerprint_sha256 == keys[1].public_key.fingerprint_sha256
            or keys[0].private_key_reference == keys[1].private_key_reference
            or uses[0].actor_subject != uses[1].actor_subject):
        raise _OwnerMismatch


def _validate_current_rows(connection: object) -> None:
    """Bounded keyset traversal; the same reconstruction verifies every row."""
    after = None
    while True:
        predicate = "" if after is None else " WHERE (preparation.run_id, preparation.activity_id, preparation.attempt) > (%s, %s, %s)"
        rows = connection.execute(_SELECT + predicate +
            " ORDER BY preparation.run_id, preparation.activity_id, preparation.attempt LIMIT 8", () if after is None else after).fetchall()
        for row in rows:
            record = _reconstruct(connection, row)
            identity = record.identity
            after = (identity.run_id.value, identity.activity_id, identity.attempt)
        if len(rows) < 8:
            return


__all__ = ["HealthEffectPreparationStore"]
