"""Postgres store for durable gateway key-rotation state."""

from __future__ import annotations

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotation,
    GatewayKeyRotationConflict,
    GatewayKeyRotationDeploymentCheckpoint,
    GatewayKeyRotationDeploymentPhase,
    GatewayKeyRotationDeploymentStatus,
    GatewayKeyRotationNotFound,
    GatewayKeyRotationStatus,
    GatewayKeyRotationTransition,
)
from control_plane_kit_operations.postgres.schema import PostgresConnection


_COLUMNS = """rotation_id, workspace_id, gateway_node_id, purpose, issuer,
old_key_id, new_secret_reference, key_generation_correlation,
maximum_grant_lifetime_seconds, clock_skew_seconds, correlation_id,
requested_by, requested_at, intent_fingerprint, status, version,
approval_request_id, approval_decision_id, generation_provider_registration_id,
generation_action_digest, new_key_id, new_secret_version_id,
new_secret_version_number, new_key_activated_at, drain_deadline_epoch,
old_key_retired_at, old_secret_revoked_at, failure_code, updated_by, updated_at"""


class GatewayKeyRotationStore:
    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def lock_binding(self, workspace_id, gateway_node_id, purpose, issuer) -> None:
        self._connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"gateway-key-rotation:{workspace_id}:{gateway_node_id}:{purpose.value}:{issuer}",))

    def add(self, value: GatewayKeyRotation) -> GatewayKeyRotation:
        self._connection.execute(
            f"""INSERT INTO cpk_gateway_key_rotations ({_COLUMNS})
            VALUES ({', '.join(['%s'] * 30)})""", _values(value))
        return value

    def get(self, rotation_id: str) -> GatewayKeyRotation:
        value = self._get(rotation_id, False)
        if value is None:
            raise GatewayKeyRotationNotFound("gateway key rotation was not found")
        return value

    def get_for_update(self, rotation_id: str) -> GatewayKeyRotation:
        value = self._get(rotation_id, True)
        if value is None:
            raise GatewayKeyRotationNotFound("gateway key rotation was not found")
        return value

    def for_correlation(self, workspace_id, correlation_id):
        row = self._connection.execute(
            f"SELECT {_COLUMNS} FROM cpk_gateway_key_rotations "
            "WHERE workspace_id = %s AND correlation_id = %s",
            (workspace_id, correlation_id)).fetchone()
        return None if row is None else self._row(row)

    def nonterminal_for_binding(self, workspace_id, gateway_node_id, purpose, issuer):
        row = self._connection.execute(
            f"SELECT {_COLUMNS} FROM cpk_gateway_key_rotations "
            "WHERE workspace_id=%s AND gateway_node_id=%s AND purpose=%s "
            "AND issuer=%s AND status NOT IN ('completed','blocked','rejected')",
            (workspace_id, gateway_node_id, purpose.value, issuer)).fetchone()
        return None if row is None else self._row(row)

    def compare_and_set(self, current, replacement):
        row = self._connection.execute(
            f"""UPDATE cpk_gateway_key_rotations SET
            status=%s, version=%s, approval_request_id=%s,
            approval_decision_id=%s, generation_provider_registration_id=%s,
            generation_action_digest=%s, new_key_id=%s,
            new_secret_version_id=%s, new_secret_version_number=%s,
            new_key_activated_at=%s, drain_deadline_epoch=%s,
            old_key_retired_at=%s, old_secret_revoked_at=%s, failure_code=%s,
            updated_by=%s, updated_at=%s
            WHERE rotation_id=%s AND status=%s AND version=%s
            RETURNING {_COLUMNS}""",
            (replacement.status.value, replacement.version,
             replacement.approval_request_id, replacement.approval_decision_id,
             replacement.generation_provider_registration_id,
             replacement.generation_action_digest, replacement.new_key_id,
             replacement.new_secret_version_id,
             replacement.new_secret_version_number, replacement.new_key_activated_at,
             replacement.drain_deadline_epoch, replacement.old_key_retired_at,
             replacement.old_secret_revoked_at, replacement.failure_code,
             replacement.updated_by, replacement.updated_at, current.rotation_id,
             current.status.value, current.version)).fetchone()
        if row is None:
            return None
        for checkpoint in (replacement.overlap_deployment,
                           replacement.retirement_deployment):
            if checkpoint is not None:
                self._put_checkpoint(replacement.rotation_id, checkpoint)
        return self.get(replacement.rotation_id)

    def transition_for_id(self, rotation_id, transition_id):
        row = self._connection.execute("""
            SELECT rotation_id,transition_id,from_status,to_status,
              from_version,to_version,transition_fingerprint,advanced_by,
              advanced_at,failure_code
            FROM cpk_gateway_key_rotation_transitions
            WHERE rotation_id=%s AND transition_id=%s
            """, (rotation_id, transition_id)).fetchone()
        return None if row is None else _transition_row(row)

    def add_transition(self, value):
        self._connection.execute("""
            INSERT INTO cpk_gateway_key_rotation_transitions
              (rotation_id,transition_id,from_status,to_status,from_version,
               to_version,transition_fingerprint,advanced_by,advanced_at,failure_code)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (value.rotation_id, value.transition_id, value.from_status.value,
                    value.to_status.value, value.from_version, value.to_version,
                    value.transition_fingerprint, value.advanced_by,
                    value.advanced_at, value.failure_code))
        return value

    def transitions(self, rotation_id):
        rows = self._connection.execute("""
            SELECT rotation_id,transition_id,from_status,to_status,
              from_version,to_version,transition_fingerprint,advanced_by,
              advanced_at,failure_code
            FROM cpk_gateway_key_rotation_transitions
            WHERE rotation_id=%s ORDER BY to_version
            """, (rotation_id,)).fetchall()
        return tuple(_transition_row(row) for row in rows)

    def _get(self, rotation_id, for_update):
        row = self._connection.execute(
            f"SELECT {_COLUMNS} FROM cpk_gateway_key_rotations WHERE rotation_id=%s"
            + (" FOR UPDATE" if for_update else ""), (rotation_id,)).fetchone()
        return None if row is None else self._row(row)

    def _row(self, row):
        checkpoints = {value.phase: value for value in self._checkpoints(row[0])}
        return GatewayKeyRotation(
            rotation_id=row[0], workspace_id=row[1], gateway_node_id=row[2],
            purpose=DelegationKeyPurpose(row[3]), issuer=row[4], old_key_id=row[5],
            new_secret_reference=SecretReference(row[6]),
            key_generation_correlation=row[7],
            maximum_grant_lifetime_seconds=row[8], clock_skew_seconds=row[9],
            correlation_id=row[10], requested_by=row[11], requested_at=row[12],
            intent_fingerprint=row[13], status=GatewayKeyRotationStatus(row[14]),
            version=row[15], approval_request_id=row[16], approval_decision_id=row[17],
            generation_provider_registration_id=row[18],
            generation_action_digest=row[19], new_key_id=row[20],
            new_secret_version_id=row[21], new_secret_version_number=row[22],
            overlap_deployment=checkpoints.get(GatewayKeyRotationDeploymentPhase.OVERLAP),
            new_key_activated_at=row[23], drain_deadline_epoch=row[24],
            retirement_deployment=checkpoints.get(GatewayKeyRotationDeploymentPhase.RETIREMENT),
            old_key_retired_at=row[25], old_secret_revoked_at=row[26],
            failure_code=row[27], updated_by=row[28], updated_at=row[29])

    def _put_checkpoint(self, rotation_id, value):
        row = self._connection.execute("""
            INSERT INTO cpk_gateway_key_rotation_deployments AS current
              (rotation_id, phase, status, session_id, plan_id,
               approval_request_id, approval_decision_id, execution_request_id,
               run_id, base_authored_graph_id, base_realized_projection_id,
               desired_authored_graph_id, desired_realized_projection_id,
               desired_revision, prepared_at, accepted_current_graph_id,
               accepted_current_projection_id, accepted_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (rotation_id, phase) DO UPDATE SET
              status=EXCLUDED.status,
              accepted_current_graph_id=EXCLUDED.accepted_current_graph_id,
              accepted_current_projection_id=EXCLUDED.accepted_current_projection_id,
              accepted_at=EXCLUDED.accepted_at
            WHERE current.session_id=EXCLUDED.session_id
              AND current.plan_id=EXCLUDED.plan_id
              AND current.approval_request_id=EXCLUDED.approval_request_id
              AND current.approval_decision_id=EXCLUDED.approval_decision_id
              AND current.execution_request_id=EXCLUDED.execution_request_id
              AND current.run_id=EXCLUDED.run_id
              AND current.base_authored_graph_id=EXCLUDED.base_authored_graph_id
              AND current.base_realized_projection_id=EXCLUDED.base_realized_projection_id
              AND current.desired_authored_graph_id=EXCLUDED.desired_authored_graph_id
              AND current.desired_realized_projection_id=EXCLUDED.desired_realized_projection_id
              AND current.desired_revision=EXCLUDED.desired_revision
              AND current.prepared_at=EXCLUDED.prepared_at
              AND (current.status='prepared' OR current.status=EXCLUDED.status)
            RETURNING rotation_id
            """, (rotation_id, value.phase.value, value.status.value,
                    value.session_id, value.plan_id, value.approval_request_id,
                    value.approval_decision_id, value.execution_request_id,
                    value.run_id, value.base_authored_graph_id,
                    value.base_realized_projection_id,
                    value.desired_authored_graph_id,
                    value.desired_realized_projection_id, value.desired_revision,
                    value.prepared_at, value.accepted_current_graph_id,
                    value.accepted_current_projection_id, value.accepted_at)).fetchone()
        if row is None:
            raise GatewayKeyRotationConflict(
                "deployment checkpoint identity changed concurrently")

    def _checkpoints(self, rotation_id):
        rows = self._connection.execute("""
            SELECT phase,status,session_id,plan_id,approval_request_id,
              approval_decision_id,execution_request_id,run_id,
              base_authored_graph_id,base_realized_projection_id,
              desired_authored_graph_id,desired_realized_projection_id,
              desired_revision,prepared_at,accepted_current_graph_id,
              accepted_current_projection_id,accepted_at
            FROM cpk_gateway_key_rotation_deployments WHERE rotation_id=%s
            ORDER BY phase""", (rotation_id,)).fetchall()
        return tuple(GatewayKeyRotationDeploymentCheckpoint(
            phase=GatewayKeyRotationDeploymentPhase(row[0]),
            status=GatewayKeyRotationDeploymentStatus(row[1]), session_id=row[2],
            plan_id=row[3], approval_request_id=row[4], approval_decision_id=row[5],
            execution_request_id=row[6], run_id=row[7], base_authored_graph_id=row[8],
            base_realized_projection_id=row[9], desired_authored_graph_id=row[10],
            desired_realized_projection_id=row[11], desired_revision=row[12],
            prepared_at=row[13], accepted_current_graph_id=row[14],
            accepted_current_projection_id=row[15], accepted_at=row[16]) for row in rows)


def _values(value):
    return (value.rotation_id,value.workspace_id,value.gateway_node_id,value.purpose.value,
            value.issuer,value.old_key_id,value.new_secret_reference.reference_id,
            value.key_generation_correlation,value.maximum_grant_lifetime_seconds,
            value.clock_skew_seconds,value.correlation_id,value.requested_by,
            value.requested_at,value.intent_fingerprint,value.status.value,value.version,
            value.approval_request_id,value.approval_decision_id,
            value.generation_provider_registration_id,value.generation_action_digest,
            value.new_key_id,
            value.new_secret_version_id,value.new_secret_version_number,
            value.new_key_activated_at,value.drain_deadline_epoch,value.old_key_retired_at,
            value.old_secret_revoked_at,value.failure_code,value.updated_by,value.updated_at)


def _transition_row(row):
    return GatewayKeyRotationTransition(
        rotation_id=row[0], transition_id=row[1],
        from_status=GatewayKeyRotationStatus(row[2]),
        to_status=GatewayKeyRotationStatus(row[3]),
        from_version=row[4], to_version=row[5], transition_fingerprint=row[6],
        advanced_by=row[7], advanced_at=row[8], failure_code=row[9])
