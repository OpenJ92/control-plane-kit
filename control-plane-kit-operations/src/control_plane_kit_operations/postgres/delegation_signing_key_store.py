"""Postgres store for immutable delegation keys and lifecycle evidence."""

from __future__ import annotations

from typing import Any

from control_plane_kit_core.delegation_keys import (
    DelegationKeyAlgorithm,
    DelegationKeyPurpose,
    DelegationPublicKey,
)
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_operations.delegation_signing_keys import (
    DelegationSigningKeyConflict,
    DelegationSigningKeyNotFound,
    RegisteredDelegationSigningKey,
    RegisteredDelegationSigningKeyStatus,
)
from control_plane_kit_operations.postgres.schema import PostgresConnection


_SELECT = """
SELECT registration_id, workspace_id, purpose, issuer, key_id, algorithm,
       public_key_pem, public_fingerprint_sha256, private_key_reference,
       admitted_by, admitted_at, status, activated_by, activated_at,
       retired_by, retired_at, revoked_by, revoked_at
FROM cpk_delegation_signing_keys
"""


class DelegationSigningKeyStore:
    """Store key identity and lifecycle on one caller-owned connection."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def register(
        self,
        candidate: RegisteredDelegationSigningKey,
    ) -> RegisteredDelegationSigningKey:
        if not isinstance(candidate, RegisteredDelegationSigningKey):
            raise TypeError("delegation key store requires registered key")
        self._lock_scope(
            candidate.workspace_id,
            candidate.purpose,
            candidate.issuer,
        )
        existing = self._get_or_none(
            candidate.workspace_id,
            candidate.purpose,
            candidate.issuer,
            candidate.key_id,
            for_update=True,
        )
        if existing is not None:
            if existing.same_identity_as(candidate):
                return existing
            raise DelegationSigningKeyConflict(
                "delegation key_id cannot be reused with changed key identity"
            )
        self._connection.execute(
            """
            INSERT INTO cpk_delegation_signing_keys (
              registration_id, workspace_id, purpose, issuer, key_id, algorithm,
              public_key_pem, public_fingerprint_sha256, private_key_reference,
              admitted_by, admitted_at, status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                candidate.registration_id,
                candidate.workspace_id,
                candidate.purpose.value,
                candidate.issuer,
                candidate.key_id,
                candidate.public_key.algorithm.value,
                candidate.public_key.public_key_pem,
                candidate.public_key.fingerprint_sha256,
                candidate.private_key_reference.reference_id,
                candidate.admitted_by,
                candidate.admitted_at,
                candidate.status.value,
            ),
        )
        return self.get(
            candidate.workspace_id,
            candidate.purpose,
            candidate.issuer,
            candidate.key_id,
        )

    def get(
        self,
        workspace_id: str,
        purpose: DelegationKeyPurpose,
        issuer: str,
        key_id: str,
    ) -> RegisteredDelegationSigningKey:
        value = self._get_or_none(workspace_id, purpose, issuer, key_id)
        if value is None:
            raise DelegationSigningKeyNotFound("delegation signing key was not found")
        return value

    def require_active(
        self,
        workspace_id: str,
        purpose: DelegationKeyPurpose,
        issuer: str,
    ) -> RegisteredDelegationSigningKey:
        row = self._connection.execute(
            f"""
            {_SELECT}
            WHERE workspace_id = %s
              AND purpose = %s
              AND issuer = %s
              AND status = 'active'
            """,
            (workspace_id, purpose.value, issuer),
        ).fetchone()
        if row is None:
            raise DelegationSigningKeyNotFound(
                "active delegation signing key was not found"
            )
        return _row(row)

    def list_workspace(
        self,
        workspace_id: str,
    ) -> tuple[RegisteredDelegationSigningKey, ...]:
        rows = self._connection.execute(
            f"""
            {_SELECT}
            WHERE workspace_id = %s
            ORDER BY purpose, issuer, key_id
            """,
            (workspace_id,),
        ).fetchall()
        return tuple(_row(row) for row in rows)

    def list_for_verification(
        self,
        workspace_id: str,
        purpose: DelegationKeyPurpose,
        issuer: str,
    ) -> tuple[RegisteredDelegationSigningKey, ...]:
        rows = self._connection.execute(
            f"""
            {_SELECT}
            WHERE workspace_id = %s
              AND purpose = %s
              AND issuer = %s
              AND status IN ('active', 'verify-only')
            ORDER BY key_id
            """,
            (workspace_id, purpose.value, issuer),
        ).fetchall()
        return tuple(_row(row) for row in rows)

    def activate(
        self,
        workspace_id: str,
        purpose: DelegationKeyPurpose,
        issuer: str,
        key_id: str,
        *,
        activated_by: str,
        activated_at: str,
    ) -> RegisteredDelegationSigningKey:
        self._lock_scope(workspace_id, purpose, issuer)
        candidate = self._get_or_none(
            workspace_id, purpose, issuer, key_id, for_update=True
        )
        if candidate is None:
            raise DelegationSigningKeyNotFound("delegation signing key was not found")
        if candidate.status is RegisteredDelegationSigningKeyStatus.ACTIVE:
            return candidate
        if candidate.status is not RegisteredDelegationSigningKeyStatus.VERIFY_ONLY:
            raise DelegationSigningKeyConflict(
                "only a verify-only delegation key may become active"
            )
        self._connection.execute(
            """
            UPDATE cpk_delegation_signing_keys
            SET status = 'verify-only'
            WHERE workspace_id = %s
              AND purpose = %s
              AND issuer = %s
              AND status = 'active'
            """,
            (workspace_id, purpose.value, issuer),
        )
        self._connection.execute(
            """
            UPDATE cpk_delegation_signing_keys
            SET status = 'active', activated_by = %s, activated_at = %s
            WHERE workspace_id = %s
              AND purpose = %s
              AND issuer = %s
              AND key_id = %s
              AND status = 'verify-only'
            """,
            (activated_by, activated_at, workspace_id, purpose.value, issuer, key_id),
        )
        return self.get(workspace_id, purpose, issuer, key_id)

    def retire(
        self,
        workspace_id: str,
        purpose: DelegationKeyPurpose,
        issuer: str,
        key_id: str,
        *,
        retired_by: str,
        retired_at: str,
    ) -> RegisteredDelegationSigningKey:
        self._lock_scope(workspace_id, purpose, issuer)
        current = self._get_or_none(
            workspace_id, purpose, issuer, key_id, for_update=True
        )
        if current is None:
            raise DelegationSigningKeyNotFound("delegation signing key was not found")
        if current.status is RegisteredDelegationSigningKeyStatus.RETIRED:
            return current
        if current.status is not RegisteredDelegationSigningKeyStatus.VERIFY_ONLY:
            raise DelegationSigningKeyConflict(
                "only a verify-only delegation key may be retired"
            )
        self._connection.execute(
            """
            UPDATE cpk_delegation_signing_keys
            SET status = 'retired', retired_by = %s, retired_at = %s
            WHERE workspace_id = %s AND purpose = %s AND issuer = %s AND key_id = %s
            """,
            (retired_by, retired_at, workspace_id, purpose.value, issuer, key_id),
        )
        return self.get(workspace_id, purpose, issuer, key_id)

    def revoke(
        self,
        workspace_id: str,
        purpose: DelegationKeyPurpose,
        issuer: str,
        key_id: str,
        *,
        revoked_by: str,
        revoked_at: str,
    ) -> RegisteredDelegationSigningKey:
        self._lock_scope(workspace_id, purpose, issuer)
        current = self._get_or_none(
            workspace_id, purpose, issuer, key_id, for_update=True
        )
        if current is None:
            raise DelegationSigningKeyNotFound("delegation signing key was not found")
        if current.status is RegisteredDelegationSigningKeyStatus.REVOKED:
            return current
        self._connection.execute(
            """
            UPDATE cpk_delegation_signing_keys
            SET status = 'revoked', revoked_by = %s, revoked_at = %s
            WHERE workspace_id = %s AND purpose = %s AND issuer = %s AND key_id = %s
            """,
            (revoked_by, revoked_at, workspace_id, purpose.value, issuer, key_id),
        )
        return self.get(workspace_id, purpose, issuer, key_id)

    def _get_or_none(
        self,
        workspace_id: str,
        purpose: DelegationKeyPurpose,
        issuer: str,
        key_id: str,
        *,
        for_update: bool = False,
    ) -> RegisteredDelegationSigningKey | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = self._connection.execute(
            f"""
            {_SELECT}
            WHERE workspace_id = %s AND purpose = %s AND issuer = %s AND key_id = %s
            {suffix}
            """,
            (workspace_id, purpose.value, issuer, key_id),
        ).fetchone()
        return None if row is None else _row(row)

    def _lock_scope(
        self,
        workspace_id: str,
        purpose: DelegationKeyPurpose,
        issuer: str,
    ) -> None:
        self._connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"delegation-key:{workspace_id}:{purpose.value}:{issuer}",),
        )


def _row(row: tuple[Any, ...]) -> RegisteredDelegationSigningKey:
    public_key = DelegationPublicKey(
        key_id=row[4],
        algorithm=DelegationKeyAlgorithm(row[5]),
        public_key_pem=row[6],
    )
    if public_key.fingerprint_sha256 != row[7]:
        raise DelegationSigningKeyConflict(
            "stored delegation public key fingerprint is inconsistent"
        )
    return RegisteredDelegationSigningKey(
        registration_id=row[0],
        workspace_id=row[1],
        purpose=DelegationKeyPurpose(row[2]),
        issuer=row[3],
        public_key=public_key,
        private_key_reference=SecretReference(row[8]),
        admitted_by=row[9],
        admitted_at=row[10],
        status=RegisteredDelegationSigningKeyStatus(row[11]),
        activated_by=row[12],
        activated_at=row[13],
        retired_by=row[14],
        retired_at=row[15],
        revoked_by=row[16],
        revoked_at=row[17],
    )


__all__ = ["DelegationSigningKeyStore"]
