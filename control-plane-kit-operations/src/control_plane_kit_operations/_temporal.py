"""Package-private canonical UTC text admission."""

from __future__ import annotations

from datetime import datetime
import re


_CANONICAL_UTC = re.compile(
    r"^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])"
    r"T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
    r"(?:[.][0-9]{6})?Z$"
)
_MAX_TIMESTAMP_BYTES = 27
_ERROR = "timestamp must be canonical UTC text"


def validate_canonical_utc_timestamp(value: object) -> str:
    """Admit exact canonical UTC text without retaining rejected material."""

    if type(value) is not str:
        raise ValueError(_ERROR)
    failed = False
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        failed = True
        encoded = b""
    if (
        failed
        or len(encoded) > _MAX_TIMESTAMP_BYTES
        or _CANONICAL_UTC.fullmatch(value) is None
    ):
        raise ValueError(_ERROR)

    failed = False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        timespec = "microseconds" if parsed.microsecond else "seconds"
        rendered = parsed.isoformat(timespec=timespec).replace("+00:00", "Z")
    except (OverflowError, ValueError):
        failed = True
        rendered = None
    if failed or rendered != value:
        raise ValueError(_ERROR)
    return value


__all__: list[str] = []
