"""Issued cleanup preparation confined to one caller-owned transaction."""
from dataclasses import dataclass

from control_plane_kit_operations._configuration_preparation import _ACCOUNTING, _execution_context
from control_plane_kit_operations.records import OperationsRecordError


_ERROR = "configuration cleanup requires live owner preparation"


@dataclass(frozen=True, repr=False)
class _PreparedCleanupStart:
    owner: object
    identity: object
    intent: object


@dataclass(frozen=True, repr=False)
class _PreparedCleanupFold:
    owner: object
    original: object
    attempt: object
    outcome: object
    request: object
    fence: object


class _CleanupPreparationOwner:
    def __init__(self, unit_of_work, store, guard, prefix):
        self.uow = unit_of_work
        self.stores = unit_of_work.stores
        self.store = store
        self.connection = self.stores.connection
        self.guard = guard
        self.prefix = prefix
        self.accounting = _ACCOUNTING.get()
        self.context = _execution_context()
        self.issued = None
        self.bound = None
        self.record = None
        self.plan = None
        self.spent = False
        self.closed = False

    def require_context(self, prepared, connection, *, write=False):
        try:
            stores = self.uow.stores
        except RuntimeError:
            raise OperationsRecordError(_ERROR) from None
        if (type(prepared) not in (_PreparedCleanupStart, _PreparedCleanupFold)
                or prepared is not self.issued or prepared.owner is not self
                or self.closed or write and self.spent or self.uow._commit_requested
                or stores is not self.stores or self.store._stores is not stores
                or connection is not self.connection or stores.connection is not connection
                or _ACCOUNTING.get() is not self.accounting or self.accounting is None
                or not self.accounting.active or self.context != _execution_context()
                or self.accounting.execution_context != self.context):
            raise OperationsRecordError(_ERROR)

    def require(self, prepared, connection, *, write=False):
        self.require_context(prepared, connection, write=write)
        # The existing lifecycle owner checks the exact transaction. This
        # entrance is charged on the same cumulative accounting object.
        self.stores.graphs._require_receiver_lifecycle(self.guard, self.guard.workspace_id)

    def require_start(self, prepared, connection, identity, intent):
        if (type(prepared) is not _PreparedCleanupStart
                or prepared.identity != identity or prepared.intent != intent):
            raise OperationsRecordError(_ERROR)
        self.require(prepared, connection, write=True)
        self.store._require_current(prepared)

    def close(self):
        self.closed = True


def _require_cleanup_start(prepared, connection, identity, intent):
    if type(prepared) is not _PreparedCleanupStart or type(prepared.owner) is not _CleanupPreparationOwner:
        raise OperationsRecordError(_ERROR)
    try:
        prepared.owner.require_start(prepared, connection, identity, intent)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise OperationsRecordError(_ERROR) from None


# Fixed source-owned SQL reservation algebra. These values forecast remaining
# work only; existing readers continue charging their actual queries. No SQL,
# arbitrary selector or externally supplied width is accepted here.
from control_plane_kit_operations.configuration_preparation import (
    ConfigurationEvidenceFootprint as _Footprint, ConfigurationCapacityDecision,
    configuration_evidence_capacity,
)


def _sum(*values):
    result = _Footprint(0, 0, 0, 0)
    for value in values:
        result = result.plus(value)
    return result


def _times(value, count):
    return _Footprint(value.records * count, value.value_octets * count,
        value.scalar_markers * count, value.statements * count)


def _q(rows, octets, cells, identities=1):
    return _Footprint(rows * identities, octets, rows * cells, 1)


def _b(columns, octets):
    return _sum(_q(1, 12 * columns, columns), _q(1, octets + 1, columns + 1))


def _v(columns, octets, count, identities=1):
    return _sum(_q(count + 1, 12 * columns * (count + 1), columns, identities),
        _q(count + 1, (octets + 1) * (count + 1), columns + 1, identities))


_T = _q(1, 20, 1)
_ACK = _q(1, 1, 1)
_EVENT = _b(6, 20704)
_COMPACT_EVENT = _b(6, 18776)
_REQUEST = _b(15, 30720)
_RUN = _b(10, 83968)
_SESSION = _b(10, 83968)
_WORKSPACE = _b(9, 81920)
_APPROVALS = _sum(_b(15, 245760), _b(9, 147456))
_ATTEMPT = _sum(_b(21, 43008), _times(_EVENT, 2))
_REF = _q(1, 32768, 19, 2)
_SOURCE = _sum(_q(1, 80032, 17, 3), _Footprint(0, 0, 0, 2))
_PAIR = _q(1, 823, 9, 2)
_CLOSED_PAIR = _sum(_PAIR, _q(1, 1, 1, 3))
_TERMINAL = _sum(_b(22, 51200), _times(_COMPACT_EVENT, 2))


class _CleanupTail:
    """The closed F/P/R call families, using issued per-column maxima."""
    def __init__(self, owner):
        self.owner = owner
        self.phase = owner.phase_bounds
        self.originals = owner.original_bounds.originals
        if type(owner.issued) is _PreparedCleanupStart:
            _, members, claims, completions = owner.fresh
        else:
            members, claims, completions = owner.record.members, owner.record.claims, owner.record.completions
        self.k, self.d, self.u = len(members), len(claims), len(completions)

    def points(self, role):
        from control_plane_kit_operations.postgres.configuration_cleanup_phase_read_bounds import _entries, _shape
        columns = _shape(role)[1]
        values = [entry.widths for entry in _entries(self.phase, role)]
        if role in ("plan", "graph", "projection"):
            values.extend(tuple(min(cap, dict(widths).get(name, cap)) for name, _, cap in columns)
                for kind, _, widths in self.originals if kind == role)
        # Calls with no captured match retain native source declarations. The
        # widest column is selected independently, never max(total row size).
        maxima = tuple(max(row[i] for row in values) for i in range(len(columns))) if values else tuple(
            cap for _, _, cap in columns)
        return _sum(_T, _b(len(columns), sum(maxima)))

    def all_points(self, role):
        from control_plane_kit_operations.postgres.configuration_cleanup_phase_read_bounds import _entries
        return _sum(*(_sum(_T, _b(len(entry.widths), sum(entry.widths)))
            for entry in _entries(self.phase, role)))

    def collections(self, role):
        from control_plane_kit_operations.postgres.configuration_cleanup_phase_read_bounds import _entries, _shape
        identities = _shape(role)[6]
        return _sum(*(_sum(_T, _v(len(entry.widths), sum(entry.widths), len(entry.keys), identities))
            for entry in _entries(self.phase, role)))

    def largest_collection(self, role):
        from control_plane_kit_operations.postgres.configuration_cleanup_phase_read_bounds import _entries, _shape
        entries = _entries(self.phase, role)
        columns, identities = _shape(role)[1], _shape(role)[6]
        if not entries:
            # No matching receiver means this family is not entered.
            return _Footprint(0, 0, 0, 0)
        maxima = tuple(max(entry.widths[i] for entry in entries) for i in range(len(columns)))
        return _sum(_T, _v(len(columns), sum(maxima), max(len(entry.keys) for entry in entries), identities))

    def intent(self):
        widths = next(widths for kind, _, widths in self.originals if kind == "intent")
        return _sum(_T, _b(10, dict(widths)["preimage"] + 9 * 2048), _b(3, 128 + 64 + 16384))

    def prefix(self):
        return _times(_sum(_ACK, _RUN), len(self.owner.prefix.held_run_ids))

    def manifest(self):
        result = _times(_q(2, 20520, 6), 2)
        occurrence = self.owner.plan.cleanup_proposal.descriptor()["context"]["current_occurrence"]
        if occurrence["kind"] == "workspace-initialization":
            # _current_manifest and the occurrence decoder each prove origin.
            return _sum(result, _times(_sum(_b(9, 5 * 2048 + 3 * 64 + 32),
                _b(7, 3 * 2048 + 32 + 1048576 + 64 + 16384), _b(9, 8 * 2048 + 1048576)), 2))
        count = len(self.phase.headers)
        slots = sum(len(entry.keys) for entry in self.phase.slots)
        return _sum(result, *(self.all_points(role) for role in (
            "header", "receipt-action", "receipt-event", "request", "run", "session")),
            _times(_sum(self.points("plan"), self.points("graph"), self.points("projection")), count),
            self.collections("slots"), _times(_REF, slots))

    def fresh(self):
        # Cold F starts with an empty immutable evidence cache. Original
        # source/terminal sharing within F is the existing read owner contract.
        original_invocations = _times(_sum(_SOURCE, _b(21, 43008),
            _times(_COMPACT_EVENT, 2), _TERMINAL), self.u)
        d1 = _sum(_times(_b(7, 730), self.u), self.collections("invocation-refs"),
            _times(_PAIR, self.d))
        return _sum(_T, self.prefix(), _REQUEST, self.points("plan"), _APPROVALS,
            _ACK, _b(9, 90304), _SESSION, _WORKSPACE, self.manifest(),
            self.collections("allocation-claims"), self.collections("allocation-refs"),
            original_invocations, _times(_PAIR, self.d), d1,
            _times(_ACK, 2 * self.k + self.u + self.d), _times(_PAIR, self.d))

    def permission(self):
        graph_pair = _sum(self.points("graph"), self.points("projection"))
        raw_pair = _sum(self.points("raw-graph"), self.points("raw-projection"))
        binding = _sum(raw_pair, self.largest_collection("bindings"))
        material = _sum(_T, _b(1, 2048), self.points("plan"), _times(graph_pair, 2),
            _b(5, 5 * 2048), self.largest_collection("scopes"))
        receivers = len(self.phase.introductions)
        acceptance = _sum(self.all_points("acceptance-action"), self.all_points("request"),
            _times(_sum(_b(1, 2048), self.points("plan"), _times(graph_pair, 2),
                _b(5, 5 * 2048)), len(self.phase.acceptance_actions)),
            *(self.collections(role) for role in ("scopes", "runs", "events", "advancement-actions", "bindings")))
        return _sum(_times(material, 2), _times(binding, 2),
            _times(_sum(graph_pair, binding), 6 * receivers),
            _times(_sum(self.points("introduction"), self.points("origin-action"), graph_pair, binding), 6 * receivers),
            _times(acceptance, 2), _REQUEST, _SESSION, _ACK, _WORKSPACE, _APPROVALS)

    def pending(self):
        from control_plane_kit_operations.postgres.configuration_cleanup_ownership_store import (
            _HEADER, _MEMBERS, _INVOCATIONS, _CLAIMS, _OUTCOMES, _columns,
        )
        def widths(names):
            return sum(cap for _, _, cap in _columns(names))
        return _sum(_b(len(_HEADER), widths(_HEADER)), *(_v(len(names), widths(names), count)
            for names, count in ((_MEMBERS, self.k), (_INVOCATIONS, self.u),
                (_CLAIMS, self.d), (_OUTCOMES, self.k))))

    def retained(self, *, folded):
        # All B rowsets and native root/claim readers remain complete. A fresh
        # final proof first compares its prepared identities; retained C only
        # narrows the complete invocation-ref reader.
        value = _sum(self.pending(), self.intent(), _ATTEMPT, _REQUEST, _RUN,
            self.points("plan"), _APPROVALS, _SESSION,
            _times(_sum(self.points("graph"), self.points("projection")), 2), _ACK,
            _times(_REF, self.k + self.d), _times(_SOURCE, self.u),
            _times(_CLOSED_PAIR, 2 * self.d), _times(_b(7, 730), self.u),
            self.collections("invocation-refs"), _times(_TERMINAL, self.u), _b(1, 2048))
        if folded:
            count = len(self.owner.issued.outcome.endpoint_observations)
            value = _sum(value, _b(22, 51200), _v(13, 11 * 8192 + 64, count, 2),
                _times(_COMPACT_EVENT, 2))
        return value


def _admit_tail(owner, future, unmetered):
    from control_plane_kit_operations.postgres.configuration_evidence import _Capacity
    if configuration_evidence_capacity(owner.accounting.used.plus(future)) is not ConfigurationCapacityDecision.WITHIN_LIMITS:
        raise _Capacity
    # Only raw generic writer statements are charged here. Forecasted tracked
    # reads/writes are not charged twice and never reset prior command usage.
    owner.accounting.used = owner.accounting.used.plus(unmetered)


def _preflight_start(prepared):
    owner = prepared.owner
    tail = _CleanupTail(owner)
    raw = _Footprint(0, 0, 0, 1)
    lease = _sum(_ACK, _REQUEST, _q(1, 65, 2))
    ordinal = _sum(_ACK, _T)
    future = _sum(lease, ordinal, _ACK, _times(tail.fresh(), 3), raw,
        tail.intent(), _q(1, 200, 1), _times(_ACK, 1 + tail.k + tail.u + 3 * tail.d),
        tail.prefix(), tail.permission(), _EVENT, tail.intent(), _ATTEMPT,
        _T, tail.retained(folded=False))
    _admit_tail(owner, future, raw)


def _preflight_fold(prepared):
    owner = prepared.owner
    tail = _CleanupTail(owner)
    observations = len(prepared.outcome.endpoint_observations)
    raw = _Footprint(2, len(prepared.request.identity.request_id.encode())
        + len(owner.record.workspace_id.encode()), 2, 2 + 2 * observations)
    future = _sum(_ACK, _T, _times(_T, 2), _ACK, raw, _q(1, 200, 1), _q(1, 1, 1, 8),
        tail.pending(), _times(_CLOSED_PAIR, tail.d),
        _times(_ACK, tail.k if owner.outcomes is not None else 0), tail.retained(folded=True))
    _admit_tail(owner, future, raw)
