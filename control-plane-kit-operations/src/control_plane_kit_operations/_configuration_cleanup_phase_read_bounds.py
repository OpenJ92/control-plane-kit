"""Private phase transport values; neither retained material nor authority."""
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True, repr=False)
class _PhasePoint:
    identity: tuple
    widths: tuple[int, ...]


@dataclass(frozen=True, repr=False)
class _PhaseCollection:
    identity: tuple
    widths: tuple[int, ...]
    keys: tuple[tuple, ...]


@dataclass(frozen=True, repr=False)
class _CleanupPhaseReadBounds:
    owner: object
    transaction_id: int
    plans: tuple[_PhasePoint, ...]
    graphs: tuple[_PhasePoint, ...]
    projections: tuple[_PhasePoint, ...]
    raw_graphs: tuple[_PhasePoint, ...]
    raw_projections: tuple[_PhasePoint, ...]
    introductions: tuple[_PhasePoint, ...]
    origin_actions: tuple[_PhasePoint, ...]
    acceptance_actions: tuple[_PhasePoint, ...]
    receipt_actions: tuple[_PhasePoint, ...]
    receipt_events: tuple[_PhasePoint, ...]
    requests: tuple[_PhasePoint, ...]
    runs: tuple[_PhasePoint, ...]
    sessions: tuple[_PhasePoint, ...]
    headers: tuple[_PhasePoint, ...]
    scopes: tuple[_PhaseCollection, ...]
    run_histories: tuple[_PhaseCollection, ...]
    event_histories: tuple[_PhaseCollection, ...]
    advancement_actions: tuple[_PhaseCollection, ...]
    bindings: tuple[_PhaseCollection, ...]
    slots: tuple[_PhaseCollection, ...]
    allocation_refs: tuple[_PhaseCollection, ...]
    allocation_claims: tuple[_PhaseCollection, ...]
    invocation_refs: tuple[_PhaseCollection, ...]


_BOUND_CLEANUP_PHASE = ContextVar("cpk_cleanup_phase_read_bounds", default=None)
