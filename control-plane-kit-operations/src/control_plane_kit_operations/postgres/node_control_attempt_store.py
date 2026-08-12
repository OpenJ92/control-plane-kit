"""Postgres persistence for exact node-control intended attempts."""

from __future__ import annotations

import hashlib
from typing import Any

from psycopg import IntegrityError
from psycopg.errors import UniqueViolation

from control_plane_kit_core.node_control import (
    DelegatedWorkloadNodeControlGrantCodec,
    NodeControlCommandRequestCodec,
)
from control_plane_kit_core.node_control_transit import (
    DelegatedGatewayNodeControlTransitGrantCodec,
)
from control_plane_kit_operations.node_control_attempts import (
    NodeControlAttemptConflict,
    NodeControlAttemptCorrupt,
    NodeControlAttemptError,
    NodeControlIntendedAttempt,
    _require_node_control_identifier,
)
from control_plane_kit_operations.postgres.schema import PostgresConnection
from control_plane_kit_operations.postgres.temporal import (
    decode_postgres_timestamp,
    encode_postgres_timestamp,
)


class NodeControlAttemptStore:
    """Append and reconstruct exact intended command evidence."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def lock_request_id(self, workspace_id: str, request_id: str) -> None:
        workspace_id = _require_node_control_identifier(workspace_id)
        request_id = _require_node_control_identifier(request_id)
        self._connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"node-control-attempt:{workspace_id}:{request_id}",),
        )

    def add(self, value: NodeControlIntendedAttempt) -> NodeControlIntendedAttempt:
        conflict = False
        invalid = False
        try:
            self._connection.execute(
                f"INSERT INTO cpk_node_control_attempts ({_COLUMNS}) VALUES ({_VALUES})",
                _record_values(value),
            )
        except UniqueViolation:
            conflict = True
        except IntegrityError:
            invalid = True
        if conflict:
            raise NodeControlAttemptConflict(
                "node-control attempt replay conflicts with durable intent"
            ) from None
        if invalid:
            raise NodeControlAttemptError(
                "node-control attempt references are unavailable"
            ) from None
        return value

    def get(self, attempt_id: str) -> NodeControlIntendedAttempt:
        attempt_id = _require_node_control_identifier(attempt_id)
        row = self._connection.execute(
            f"{_SELECT} WHERE attempt.attempt_id=%s",
            (attempt_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"missing node-control attempt {attempt_id!r}")
        return _decode_row(row)

    def get_by_request_id(
        self,
        workspace_id: str,
        request_id: str,
    ) -> NodeControlIntendedAttempt | None:
        workspace_id = _require_node_control_identifier(workspace_id)
        request_id = _require_node_control_identifier(request_id)
        row = self._connection.execute(
            f"{_SELECT} WHERE attempt.workspace_id=%s AND attempt.request_id=%s",
            (workspace_id, request_id),
        ).fetchone()
        return None if row is None else _decode_row(row)


_COLUMN_NAMES = (
    "attempt_id", "workspace_id", "request_id", "actor_subject",
    "current_graph_id", "current_realized_projection_id", "gateway_runtime_id",
    "transit_key_registration_id", "workload_key_registration_id",
    "transit_authorization_id", "workload_authorization_id",
    "transit_correlation_id", "workload_correlation_id", "request_bytes",
    "request_digest", "transit_grant_bytes", "transit_grant_digest",
    "workload_grant_bytes", "workload_grant_digest", "transit_issuer",
    "transit_key_id", "transit_jti", "workload_issuer", "workload_key_id",
    "workload_jti", "intended_at", "intent_fingerprint",
)
_COLUMNS = ", ".join(_COLUMN_NAMES)
_VALUES = ", ".join("%s" for _ in _COLUMN_NAMES)
_SELECT = f"""
SELECT {', '.join('attempt.' + value for value in _COLUMN_NAMES)},
       transit_key.purpose = 'gateway-node-control-transit',
       transit_key.issuer = attempt.transit_issuer,
       transit_key.key_id = attempt.transit_key_id,
       transit_auth.use_intent = 'gateway.node-control-transit-signing-key',
       transit_auth.actor_subject = attempt.actor_subject,
       transit_auth.correlation_id = attempt.transit_correlation_id,
       transit_auth.secret_reference = transit_key.private_key_reference,
       workload_key.purpose = 'workload-node-control',
       workload_key.issuer = attempt.workload_issuer,
       workload_key.key_id = attempt.workload_key_id,
       workload_auth.use_intent = 'workload.node-control-signing-key',
       workload_auth.actor_subject = attempt.actor_subject,
       workload_auth.correlation_id = attempt.workload_correlation_id,
       workload_auth.secret_reference = workload_key.private_key_reference
FROM cpk_node_control_attempts AS attempt
JOIN cpk_delegation_signing_keys AS transit_key
  ON transit_key.registration_id=attempt.transit_key_registration_id
 AND transit_key.workspace_id=attempt.workspace_id
JOIN cpk_delegation_signing_keys AS workload_key
  ON workload_key.registration_id=attempt.workload_key_registration_id
 AND workload_key.workspace_id=attempt.workspace_id
JOIN cpk_secret_use_authorizations AS transit_auth
  ON transit_auth.authorization_id=attempt.transit_authorization_id
 AND transit_auth.workspace_id=attempt.workspace_id
JOIN cpk_secret_use_authorizations AS workload_auth
  ON workload_auth.authorization_id=attempt.workload_authorization_id
 AND workload_auth.workspace_id=attempt.workspace_id
"""


def _record_values(value: NodeControlIntendedAttempt) -> tuple[object, ...]:
    return (
        value.attempt_id, value.workspace_id, value.request_id, value.actor_subject,
        value.current_graph_id, value.current_realized_projection_id,
        value.gateway_runtime_id, value.transit_key_registration_id,
        value.workload_key_registration_id, value.transit_authorization_id,
        value.workload_authorization_id, value.transit_correlation_id,
        value.workload_correlation_id, value.request_bytes,
        value.request.canonical_digest().value, value.transit_grant_bytes,
        value.transit_grant.canonical_digest().value, value.workload_grant_bytes,
        value.workload_grant.canonical_digest().value, value.transit_grant.issuer,
        value.transit_grant.key_id, value.transit_grant.jti,
        value.workload_grant.issuer, value.workload_grant.key_id,
        value.workload_grant.jti, encode_postgres_timestamp(value.intended_at),
        value.intent_fingerprint,
    )


def _decode_row(row: tuple[Any, ...]) -> NodeControlIntendedAttempt:
    failed = False
    try:
        if len(row) != 41 or not all(value is True for value in row[27:]):
            raise ValueError
        request = NodeControlCommandRequestCodec().decode_canonical_bytes(bytes(row[13]))
        transit = DelegatedGatewayNodeControlTransitGrantCodec().decode_canonical_bytes(
            bytes(row[15])
        )
        workload = DelegatedWorkloadNodeControlGrantCodec().decode_canonical_bytes(
            bytes(row[17])
        )
        if (
            hashlib.sha256(bytes(row[13])).hexdigest() != row[14]
            or hashlib.sha256(bytes(row[15])).hexdigest() != row[16]
            or hashlib.sha256(bytes(row[17])).hexdigest() != row[18]
            or request.request_id != row[2]
            or request.target.workspace_id.value != row[1]
            or request.target.graph_revision.value != row[4]
            or transit.attempt_id != row[0]
            or transit.issuer != row[19]
            or transit.key_id != row[20]
            or transit.jti != row[21]
            or workload.issuer != row[22]
            or workload.key_id != row[23]
            or workload.jti != row[24]
        ):
            raise ValueError
        value = NodeControlIntendedAttempt(
            attempt_id=row[0], actor_subject=row[3], current_graph_id=row[4],
            current_realized_projection_id=row[5], gateway_runtime_id=row[6],
            transit_key_registration_id=row[7], workload_key_registration_id=row[8],
            transit_authorization_id=row[9], workload_authorization_id=row[10],
            transit_correlation_id=row[11], workload_correlation_id=row[12],
            intended_at=decode_postgres_timestamp(row[25]), request=request,
            transit_grant=transit, workload_grant=workload,
        )
        if value.intent_fingerprint != row[26]:
            raise ValueError
    except Exception:
        failed = True
        value = None
    if failed or value is None:
        raise NodeControlAttemptCorrupt(
            "node-control intended attempt is corrupt"
        ) from None
    return value


__all__ = ["NodeControlAttemptStore"]
