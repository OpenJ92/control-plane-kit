"""Postgres store for workspace-registered product descriptors."""

from __future__ import annotations

import json
from typing import Any

from psycopg.types.json import Jsonb

from control_plane_kit_core.products import (
    ProductDescriptorCodec,
    ProductDescriptorDocument,
    ProductDescriptorDigest,
    ProductReference,
    ProductReferenceCodec,
)
from control_plane_kit_operations.postgres.schema import PostgresConnection
from control_plane_kit_operations.postgres.temporal import (
    decode_postgres_timestamp,
    encode_postgres_timestamp,
)
from control_plane_kit_operations.products import (
    DescriptorSourceCodec,
    DescriptorSourceEvidence,
    ProductRegistrationConflict,
    ProductRegistrationNotFound,
    RegisteredProduct,
    RegisteredProductStatus,
)
from control_plane_kit_operations.read_pages import (
    IdentityReadCursor, ReadCollection, ReadPage, ReadPageCandidate,
    ReadPageError, ReadPageRequest, WorkspaceReadScope,
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
        encoded_imported_at = encode_postgres_timestamp(candidate.imported_at)
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
                encoded_imported_at,
                candidate.status.value,
            ),
        )
        return candidate

    def get(
        self,
        workspace_id: str,
        reference: ProductReference,
    ) -> RegisteredProduct:
        return self._get(workspace_id, reference, for_update=False)

    def get_for_update(self, workspace_id: str, reference: ProductReference) -> RegisteredProduct:
        """Fence fresh admission against concurrent registration revocation."""
        return self._get(workspace_id, reference, for_update=True)

    def _get(self, workspace_id, reference, *, for_update):
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
            """ + (" FOR UPDATE" if for_update else ""),
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

    def directory_page(self, request: ReadPageRequest) -> ReadPage[dict[str, object]]:
        """Seek bounded active summaries without retrieving descriptor bodies."""
        if not isinstance(request, ReadPageRequest) or request.collection is not ReadCollection.REGISTERED_PRODUCTS:
            raise ReadPageError("product directory request is malformed")
        after = request.cursor.item_id if request.cursor is not None else None
        rows = self._connection.execute(
            """
            SELECT descriptor_sha256,
              CASE WHEN octet_length(product_reference::text) <= 8192
                AND octet_length(registration_id) <= 2048
                AND product_reference->>'descriptor_sha256' = descriptor_sha256
                AND descriptor_document->'product'->'identity' = product_reference->'identity'
                AND jsonb_typeof(descriptor_document->'product') = 'object'
                AND descriptor_document->'product'->>'product_family' IN ('server', 'data-service')
                AND (descriptor_document->'product'->'display_name' = 'null'::jsonb
                     OR (jsonb_typeof(descriptor_document->'product'->'display_name') = 'string'
                         AND length(descriptor_document->'product'->>'display_name') <= 128))
                AND (descriptor_document->'product'->'description' = 'null'::jsonb
                     OR (jsonb_typeof(descriptor_document->'product'->'description') = 'string'
                         AND length(descriptor_document->'product'->>'description') <= 1024))
              THEN jsonb_build_object(
                'registration_id', registration_id, 'reference', product_reference,
                'status', status,
                'display_name', descriptor_document->'product'->'display_name',
                'description', descriptor_document->'product'->'description',
                'product_family', descriptor_document->'product'->'product_family')
              ELSE NULL END
            FROM cpk_registered_products
            WHERE workspace_id = %s AND status = 'active'
              AND (%s::text IS NULL OR descriptor_sha256 > %s)
            ORDER BY descriptor_sha256 ASC LIMIT %s
            """,
            (request.scope.workspace_id, after, after, request.limit + 1),
        ).fetchall()
        if any(row[1] is None for row in rows):
            raise ReadPageError("product directory record is malformed or oversized")
        return ReadPage.from_candidates(request, tuple(
            ReadPageCandidate(row[1], IdentityReadCursor(request.collection, request.scope, row[0]))
            for row in rows
        ))

    def directory_detail(self, workspace_id: str, digest: str) -> RegisteredProduct:
        """Read one canonical descriptor with bounded transport and no metadata."""
        WorkspaceReadScope(workspace_id)
        ProductDescriptorDigest(digest)
        row = self._connection.execute(
            """
            SELECT
              CASE WHEN octet_length(registration_id) <= 2048 THEN registration_id END,
              workspace_id,
              CASE WHEN octet_length(product_reference::text) <= 8192 THEN product_reference END,
              NULL,
              CASE WHEN octet_length(descriptor_content) <= 262144 THEN descriptor_content END,
              CASE WHEN octet_length(source::text) <= 8192 THEN source END,
              CASE WHEN octet_length(imported_by) <= 2048 THEN imported_by END,
              imported_at, status, '{}'::jsonb
            FROM cpk_registered_products
            WHERE workspace_id = %s AND descriptor_sha256 = %s
            """, (workspace_id, digest),
        ).fetchone()
        if row is None:
            raise ProductRegistrationNotFound("registered product was not found")
        try:
            product = _row_to_registered(row)
            if product.reference.descriptor_sha256.value != digest:
                raise ValueError
            return product
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
            raise ReadPageError("product directory record is malformed or oversized") from None

    def _configuration_product(self, workspace_id, reference, read):
        columns = (("registration_id", "text", 2048), ("workspace_id", "text", 2048),
            ("product_reference", "json", 8192), ("descriptor_document", "json", 1048576),
            ("descriptor_content", "text", 1048576), ("source", "json", 8192),
            ("imported_by", "text", 2048), ("imported_at", "time", 64),
            ("status", "text", 64), ("metadata", "json", 16384))
        rows = read.bounded_rows("cpk_registered_products", columns,
            "workspace_id=%s AND descriptor_sha256=%s", (workspace_id, reference.descriptor_sha256.value))
        if not rows:
            raise ProductRegistrationNotFound("registered product was not found")
        product = _row_to_registered(rows[0])
        if product.reference != reference or product.status is not RegisteredProductStatus.ACTIVE:
            raise ProductRegistrationNotFound("registered product is unavailable")
        return product

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
        imported_at=decode_postgres_timestamp(row[7]),
        status=RegisteredProductStatus(row[8]),
        metadata=row[9],
    )
    return registered
