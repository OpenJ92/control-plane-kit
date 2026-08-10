"""Closed V1 interpreter for canonical retained product descriptor content."""

from __future__ import annotations

from collections.abc import Mapping

from control_plane_kit_core.products import (
    ProductDescriptorCodec,
    ProductReference,
    ProductReferenceCodec,
)
from control_plane_kit_operations.postgres.migrations import SchemaMigrationError
from control_plane_kit_operations.postgres.schema import PostgresConnection


_BATCH_SIZE = 64
_REGISTRATION_ID_BYTES = 2_048
_DESCRIPTOR_SHA256_BYTES = 64
_JSONB_TRANSPORT_BYTES = 524_288
_DESCRIPTOR_CONTENT_BYTES = ProductDescriptorCodec().max_bytes
_SELECT_BATCH = """
SELECT octet_length(registration_id) BETWEEN 1 AND %s,
       CASE WHEN octet_length(registration_id) BETWEEN 1 AND %s
            THEN registration_id ELSE NULL END,
       octet_length(descriptor_sha256) = %s,
       CASE WHEN octet_length(descriptor_sha256) = %s
            THEN descriptor_sha256 ELSE NULL END,
       jsonb_typeof(descriptor_document) = 'object'
         AND octet_length(descriptor_document::text) <= %s,
       CASE WHEN jsonb_typeof(descriptor_document) = 'object'
                   AND octet_length(descriptor_document::text) <= %s
            THEN descriptor_document ELSE NULL END,
       jsonb_typeof(product_reference) = 'object'
         AND octet_length(product_reference::text) <= %s,
       CASE WHEN jsonb_typeof(product_reference) = 'object'
                   AND octet_length(product_reference::text) <= %s
            THEN product_reference ELSE NULL END,
       descriptor_content IS NULL,
       descriptor_content IS NULL
         OR octet_length(descriptor_content) BETWEEN 1 AND %s,
       CASE WHEN descriptor_content IS NULL THEN NULL
            WHEN octet_length(descriptor_content) BETWEEN 1 AND %s
            THEN descriptor_content ELSE NULL END
FROM cpk_registered_products
WHERE registration_id > %s
ORDER BY registration_id
LIMIT %s
FOR UPDATE
"""
_UPDATE_CONTENT = """
UPDATE cpk_registered_products
SET descriptor_content = %s
WHERE registration_id = %s
  AND descriptor_content IS NULL
"""


def backfill_product_descriptor_content_v1(connection: PostgresConnection) -> None:
    """Reconstruct every retained descriptor through the public core codec."""

    last_registration_id = ""
    while True:
        rows = connection.execute(
            _SELECT_BATCH,
            (
                _REGISTRATION_ID_BYTES,
                _REGISTRATION_ID_BYTES,
                _DESCRIPTOR_SHA256_BYTES,
                _DESCRIPTOR_SHA256_BYTES,
                _JSONB_TRANSPORT_BYTES,
                _JSONB_TRANSPORT_BYTES,
                _JSONB_TRANSPORT_BYTES,
                _JSONB_TRANSPORT_BYTES,
                _DESCRIPTOR_CONTENT_BYTES,
                _DESCRIPTOR_CONTENT_BYTES,
                last_registration_id,
                _BATCH_SIZE,
            ),
        ).fetchall()
        if not rows:
            return
        if len(rows) > _BATCH_SIZE:
            _raise_backfill_failure()
        for row in rows:
            decoded = _decode_row(row)
            (
                registration_id,
                canonical_content,
                content_is_missing,
            ) = decoded
            if content_is_missing:
                cursor = connection.execute(
                    _UPDATE_CONTENT,
                    (canonical_content, registration_id),
                )
                if cursor.rowcount != 1:
                    _raise_backfill_failure()
            last_registration_id = registration_id
        if len(rows) < _BATCH_SIZE:
            return


def _decode_row(row: object) -> tuple[str, str, bool]:
    failed = False
    result = None
    try:
        if type(row) not in (tuple, list) or len(row) != 11:
            raise ValueError("row shape")
        (
            registration_ok,
            registration_id,
            digest_ok,
            descriptor_sha256,
            document_ok,
            descriptor_document,
            reference_ok,
            product_reference,
            content_is_missing,
            content_ok,
            descriptor_content,
        ) = row
        if (
            registration_ok is not True
            or digest_ok is not True
            or document_ok is not True
            or reference_ok is not True
            or type(content_is_missing) is not bool
            or content_ok is not True
            or type(registration_id) is not str
            or not registration_id
            or len(registration_id) > 512
            or any(ord(character) < 32 for character in registration_id)
            or type(descriptor_sha256) is not str
            or not isinstance(descriptor_document, Mapping)
            or not isinstance(product_reference, Mapping)
            or (content_is_missing and descriptor_content is not None)
            or (not content_is_missing and type(descriptor_content) is not str)
        ):
            raise ValueError("row values")
        document = ProductDescriptorCodec().decode_document(descriptor_document)
        if document.content_digest != descriptor_sha256:
            raise ValueError("digest mismatch")
        retained_reference = ProductReferenceCodec().decode(product_reference)
        if retained_reference != ProductReference.from_document(document):
            raise ValueError("reference mismatch")
        canonical_content = document.content.decode("utf-8")
        if not content_is_missing and descriptor_content != canonical_content:
            raise ValueError("content mismatch")
        result = (registration_id, canonical_content, content_is_missing)
    except Exception:
        failed = True
    if failed or result is None:
        _raise_backfill_failure()
    return result


def _raise_backfill_failure() -> None:
    raise SchemaMigrationError("product descriptor content backfill failed")
