"""Canonical UTC codecs for PostgreSQL temporal columns."""

from __future__ import annotations

from datetime import datetime, timezone

from control_plane_kit_operations._temporal import (
    validate_canonical_utc_timestamp,
)


def encode_postgres_timestamp(value: object) -> datetime:
    """Encode exact canonical UTC text for psycopg."""

    failed = False
    try:
        canonical = validate_canonical_utc_timestamp(value)
    except ValueError:
        failed = True
        canonical = ""
    if failed:
        raise ValueError("postgres timestamp must be canonical UTC text")
    return datetime.fromisoformat(canonical[:-1] + "+00:00")


def decode_postgres_timestamp(value: object) -> str:
    """Decode one aware PostgreSQL datetime to canonical UTC text."""

    if not isinstance(value, datetime):
        raise ValueError("postgres timestamp value is not timezone-aware")
    failed = False
    try:
        aware = value.utcoffset() is not None
        rendered = _render_utc(value) if aware else None
    except Exception:
        failed = True
        aware = False
        rendered = None
    if failed or not aware or rendered is None:
        raise ValueError("postgres timestamp value is not timezone-aware")
    return rendered


def _render_utc(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc)
    timespec = "microseconds" if normalized.microsecond else "seconds"
    return normalized.isoformat(timespec=timespec).replace("+00:00", "Z")


__all__ = ["decode_postgres_timestamp", "encode_postgres_timestamp"]
