"""Private canonical activity-identity grammar."""

from __future__ import annotations

import re


_ACTIVITY_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}\Z")


def _is_canonical_activity_identity(value: object) -> bool:
    return type(value) is str and _ACTIVITY_IDENTITY.fullmatch(value) is not None
