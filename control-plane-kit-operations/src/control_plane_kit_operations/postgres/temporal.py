"""Canonical UTC codecs for PostgreSQL temporal columns."""

from __future__ import annotations

from datetime import datetime, timezone
import re


_CANONICAL_UTC = re.compile(
    r"^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])"
    r"T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
    r"(?:[.][0-9]{6})?Z$"
)
_MAX_TIMESTAMP_BYTES = 27


def encode_postgres_timestamp(value: object) -> datetime:
    """Encode exact canonical UTC text for psycopg."""

    if not isinstance(value, str):
        raise ValueError("postgres timestamp must be canonical UTC text")
    failed = False
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        failed = True
        encoded = b""
    if failed or len(encoded) > _MAX_TIMESTAMP_BYTES:
        raise ValueError("postgres timestamp must be canonical UTC text")
    if _CANONICAL_UTC.fullmatch(value) is None:
        raise ValueError("postgres timestamp must be canonical UTC text")

    failed = False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except (OverflowError, ValueError):
        failed = True
        parsed = None
    if failed or parsed is None or _render_utc(parsed) != value:
        raise ValueError("postgres timestamp must be canonical UTC text")
    return parsed


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
