"""Postgres store for workspace-registered product descriptors."""

from __future__ import annotations

import json
from typing import Any

from psycopg.types.json import Jsonb

from control_plane_kit_core.products import (
    ProductDescriptorCodec,
    ProductDescriptorDocument,
    ProductReference,
    ProductReferenceCodec,
)
from control_plane_kit_operations.postgres.schema import PostgresConnection
from control_plane_kit_operations.products import (
    DescriptorSourceCodec,
    DescriptorSourceEvidence,
    ProductRegistrationConflict,
    ProductRegistrationNotFound,
    RegisteredProduct,
    RegisteredProductStatus,
)


class RegisteredProductStore:
    """Persist products admitted into one workspace's operational truth."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def register(
        self,
        *,
        workspace_id: str,
        descriptor_document: ProductDescriptorDocument,
        source: DescriptorSourceEvidence,
        imported_by: str,
        imported_at: str,
    ) -> RegisteredProduct:
        candidate = RegisteredProduct.from_document(
            workspace_id=workspace_id,
            descriptor_document=descriptor_document,
            source=source,
            imported_by=imported_by,
            imported_at=imported_at,
        )
        existing = self._get_by_digest(
            workspace_id,
            candidate.reference.descriptor_sha256.value,
        )
        if existing is not None:
            return existing

        active_conflict = self._active_with_identity(
            workspace_id,
            candidate.reference,
        )
        if active_conflict is not None:
            raise ProductRegistrationConflict(
                "registered product replacement requires explicit replacement policy"
            )

        self._connection.execute(
            """
            INSERT INTO cpk_registered_products (
              registration_id,
              workspace_id,
              product_reference,
              descriptor_sha256,
              descriptor_document,
              descriptor_content,
              source,
              imported_by,
              imported_at,
              status,
              metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '{}'::jsonb)
            """,
            (
                candidate.registration_id,
                candidate.workspace_id,
                Jsonb(candidate.reference.descriptor()),
                candidate.reference.descriptor_sha256.value,
                Jsonb(_document_json(candidate.descriptor_document)),
                candidate.descriptor_document.content.decode("utf-8"),
                Jsonb(DescriptorSourceCodec().encode(candidate.source)),
                candidate.imported_by,
                candidate.imported_at,
                candidate.status.value,
            ),
        )
        return candidate

    def get(
        self,
        workspace_id: str,
        reference: ProductReference,
    ) -> RegisteredProduct:
        row = self._connection.execute(
            """
            SELECT
              registration_id,
              workspace_id,
              product_reference,
              descriptor_document,
              descriptor_content,
              source,
              imported_by,
              imported_at,
              status,
              metadata
            FROM cpk_registered_products
            WHERE workspace_id = %s
              AND descriptor_sha256 = %s
            """,
            (workspace_id, reference.descriptor_sha256.value),
        ).fetchone()
        if row is None:
            raise ProductRegistrationNotFound("registered product was not found")
        registered = _row_to_registered(row)
        if registered.reference != reference:
            raise ProductRegistrationNotFound("registered product reference mismatch")
        return registered

    def list_active(self, workspace_id: str) -> tuple[RegisteredProduct, ...]:
        rows = self._connection.execute(
            """
            SELECT
              registration_id,
              workspace_id,
              product_reference,
              descriptor_document,
              descriptor_content,
              source,
              imported_by,
              imported_at,
              status,
              metadata
            FROM cpk_registered_products
            WHERE workspace_id = %s
              AND status = 'active'
            ORDER BY descriptor_sha256
            """,
            (workspace_id,),
        ).fetchall()
        return tuple(_row_to_registered(row) for row in rows)

    def revoke(
        self,
        workspace_id: str,
        reference: ProductReference,
    ) -> RegisteredProduct:
        current = self.get(workspace_id, reference)
        if current.status is RegisteredProductStatus.REVOKED:
            return current
        self._connection.execute(
            """
            UPDATE cpk_registered_products
            SET status = 'revoked'
            WHERE workspace_id = %s
              AND descriptor_sha256 = %s
            """,
            (workspace_id, reference.descriptor_sha256.value),
        )
        return self.get(workspace_id, reference)

    def _get_by_digest(
        self,
        workspace_id: str,
        descriptor_sha256: str,
    ) -> RegisteredProduct | None:
        row = self._connection.execute(
            """
            SELECT
              registration_id,
              workspace_id,
              product_reference,
              descriptor_document,
              descriptor_content,
              source,
              imported_by,
              imported_at,
              status,
              metadata
            FROM cpk_registered_products
            WHERE workspace_id = %s
              AND descriptor_sha256 = %s
            """,
            (workspace_id, descriptor_sha256),
        ).fetchone()
        if row is None:
            return None
        return _row_to_registered(row)

    def _active_with_identity(
        self,
        workspace_id: str,
        reference: ProductReference,
    ) -> RegisteredProduct | None:
        for registered in self.list_active(workspace_id):
            if registered.reference.identity == reference.identity:
                return registered
        return None


def _document_json(document: ProductDescriptorDocument) -> dict[str, object]:
    return json.loads(document.content.decode("utf-8"))


def _row_to_registered(row: tuple[Any, ...]) -> RegisteredProduct:
    reference = ProductReferenceCodec().decode(row[2])
    document = ProductDescriptorCodec().decode_document(row[4])
    registered = RegisteredProduct(
        registration_id=row[0],
        workspace_id=row[1],
        reference=reference,
        descriptor_document=document,
        source=DescriptorSourceCodec().decode(row[5]),
        imported_by=row[6],
        imported_at=row[7],
        status=RegisteredProductStatus(row[8]),
        metadata=row[9],
    )
    return registered
