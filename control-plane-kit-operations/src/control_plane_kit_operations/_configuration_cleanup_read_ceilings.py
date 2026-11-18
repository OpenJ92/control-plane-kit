"""Private immutable read bounds, never cleanup execution authority."""
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True, repr=False)
class _CleanupOriginalReadCeilings:
    owner: object
    # Closed kind, exact point identity, fixed expression/width pairs.
    originals: tuple
    transaction_id: int


_BOUND_CLEANUP_ORIGINALS = ContextVar("cpk_cleanup_original_read_ceilings", default=None)
