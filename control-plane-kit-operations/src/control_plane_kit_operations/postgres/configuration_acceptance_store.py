"""Original accepted membership in the advancement owner's transaction."""
from hashlib import sha256
from dataclasses import replace
from functools import wraps
from contextlib import contextmanager
import json

import rfc8785

from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.configuration_instances import ConfigurationInstanceRefCodec
from control_plane_kit_core.planning import ActivityId, StartNode, ReconcileNode
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_operations._configuration_acceptance import (
    _PreparedAdvancementReceipt, _require_prepared_advancement, _history_records, _PUBLICATION_SCOPE,
    _PublicationReadBounds, _require_publication_proof,
    _observe_publication_proof,
)
from control_plane_kit_operations._configuration_preparation import _configuration_accounting, _ACCOUNTING, _execution_context
from control_plane_kit_operations.configuration_preparation import (
    ConfigurationAcceptedBinding, ConfigurationCurrentEvidence, ConfigurationAcceptedTransferRecord,
    _ref as _validate_ref,
)
from control_plane_kit_operations.revision_history import historical_advancement
from control_plane_kit_operations.records import OperationsRecordError
from .configuration_evidence import _EvidenceRead, _Unavailable, _Capacity, _joined_read
from .activity_history import PostgresActivityHistoryStore, _action_record
from .execution import PostgresExecutionStore, _activity_event
from .graph_store import (
    PostgresGraphTopologyStore, PostgresRealizedGraphProjectionStore, PostgresWorkspaceStore,
    _read_workspace_initialization,
)
from .configuration_preparation_store import _SELECT as _REF_SELECT, _decode_ref, _decode, _paired_disposition
from .effect_outcome_store import EffectAttemptOutcomeStore


_HEADER = ("workspace_id", "pinned_revision", "graph_id", "projection_id", "projection_digest",
    "action_id", "event_id", "run_id", "request_id", "plan_id", "slot_count", "slot_digest")
_ACTION = ("action_id", "session_id", "ordinal", "action_type", "actor_id", "payload", "created_at",
    "idempotency_key", "intent_fingerprint")
_EVENT = ("event_id", "run_id", "ordinal", "event_type", "occurred_at", "payload")
_LOCATOR = ("advancement_workspace_id", "advancement_request_id", "advancement_plan_id", "advancement_revision")
_SLOT = ("runtime_id", "node_id", "artifact_id", "source_run_id", "source_activity_id", "source_attempt",
    "source_artifact_id", "birth_run_id", "birth_activity_id", "birth_attempt", "birth_artifact_id", "full_ref_digest")
_REF_KEY_COLUMNS = (("run_id", "text", 2048), ("activity_id", "text", 2048),
    ("attempt", "int", 16), ("artifact_id", "text", 2048))
_TRANSFER_COLUMNS = (("run_id", "text", 200), ("activity_id", "text", 200), ("attempt", "int", 10),
    ("artifact_id", "text", 63), ("workspace_id", "text", 128), ("allocation_id", "text", 128),
    ("runtime_id", "text", 128), ("node_id", "text", 128), ("ref_digest", "text", 64),
    ("request_fingerprint", "text", 64), ("selection_fingerprint", "text", 64),
    ("outcome_fingerprint", "text", 64), ("acceptance_revision", "int", 16))


def _membership_digest(slots):
    return sha256(rfc8785.dumps([[list(row[:3]), list(row[3:7]), list(row[7:11]), row[11]] for row in slots])).hexdigest()


def _ref_key(identity, artifact):
    return identity.run_id.value, identity.activity_id, identity.attempt, artifact


def _columns(names):
    return tuple((name, "json" if name == "payload" else "time" if name in ("created_at", "occurred_at")
        else "int" if name in ("ordinal", "pinned_revision", "advancement_revision", "slot_count", "source_attempt", "birth_attempt") else "text",
        65536 if name == "payload" else 2048) for name in names)


def _closed_evidence(method):
    @wraps(method)
    def read(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except (_Capacity, _Unavailable):
            raise
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, OperationsRecordError):
            raise _Unavailable from None
    return read


def _public_read(method):
    @wraps(method)
    def observe(self, *args, **kwargs):
        try:
            current = _ACCOUNTING.get()
            if current is not None:
                if not current.active or current.execution_context != _execution_context():
                    raise _Unavailable
                # A nested observation cannot reset its caller's logical budget.
                return method(self, *args, **kwargs)
            with _configuration_accounting(("configuration-current-read", method.__name__)):
                return method(self, *args, **kwargs)
        except _Capacity:
            return ConfigurationCurrentEvidence("capacity")
        except (_Unavailable, ValueError, TypeError, KeyError, AttributeError, IndexError, OperationsRecordError):
            return ConfigurationCurrentEvidence("unavailable")
    return observe


def _read_identifier(value):
    if (type(value) is not str or not value.strip() or len(value.encode("utf-8")) > 2048
            or any(ord(character) < 32 for character in value)):
        raise _Unavailable


class ConfigurationAcceptanceStore:
    def __init__(self, connection):
        self._connection = connection
        self._issued = None
        self._publication_uow = None
        self._publication_active = False

    def _require_publication(self, *, read=None):
        """Check the existing owner's local lifetime before any SQL or cache."""
        from .configuration_cleanup_read_ceilings import _require
        try:
            stores = self._publication_uow.stores if self._publication_uow is not None else None
        except RuntimeError:
            raise _Unavailable from None
        _require(self._publication_active and _PUBLICATION_SCOPE.get() is self
            and stores is self._publication_stores and stores.connection is self._connection
            and stores.configuration_acceptance is self and not self._publication_uow._commit_requested
            and _ACCOUNTING.get() is self._publication_accounting
            and self._publication_accounting is not None and self._publication_accounting.active
            and self._publication_context == _execution_context() == self._publication_accounting.execution_context
            and stores.graphs.owns_receiver_lifecycle(self._publication_guard, self._publication_guard.workspace_id))
        _require(read is None or read.connection is self._connection
            and read.accounting is self._publication_accounting)

    @contextmanager
    def _publication_scope(self, unit_of_work, guard):
        """Keep capture and publication inside one caller-owned transaction."""
        from control_plane_kit_operations._configuration_preparation import _BOUND_ORDINARY_START
        from control_plane_kit_operations._configuration_cleanup_phase_read_bounds import _BOUND_CLEANUP_PHASE
        from control_plane_kit_operations._configuration_cleanup_read_ceilings import _BOUND_CLEANUP_ORIGINALS
        from .configuration_cleanup_read_ceilings import _require, _transaction
        _require(not self._publication_active and _PUBLICATION_SCOPE.get() is None
            and _BOUND_ORDINARY_START.get() is None and _BOUND_CLEANUP_PHASE.get() is None
            and _BOUND_CLEANUP_ORIGINALS.get() is None)
        self._publication_uow = unit_of_work
        self._publication_stores = unit_of_work.stores
        self._publication_accounting = _ACCOUNTING.get()
        self._publication_context = _execution_context()
        self._publication_guard = guard
        self._publication_published = False
        self._publication_own_context_started = False
        self._publication_points = set()
        self._publication_observations = {}
        self._publication_shared_collections = {}
        self._publication_candidates = {}
        self._publication_proof_shapes = {}
        self._publication_receiver_receipts = {}
        self._publication_transfer_dependencies = {}
        self._publication_invocation_members = {}
        self._publication_active = True
        self._issued = None
        token = _PUBLICATION_SCOPE.set(self)
        try:
            self._require_publication()
            read = _EvidenceRead(self._connection)
            transaction = _transaction(read)
            _require(transaction == guard._transaction_id)
            self._publication_transaction = transaction
            completed = False
            try:
                yield
                completed = True
            finally:
                # Retain failed fetch reservations; the single final query is
                # still charged to the same ledger before commit is requested.
                self._require_publication(read=read)
                from psycopg.pq import TransactionStatus
                if self._connection.info.transaction_status == TransactionStatus.INERROR:
                    # A server-aborted transaction cannot execute a close query.
                    # Preserve its original error for the caller's rollback;
                    # never turn a swallowed database failure into success.
                    _require(not completed)
                else:
                    _require(_transaction(read) == transaction)
        finally:
            self._issued = None
            self._publication_active = False
            self._publication_uow = None
            self._publication_stores = None
            self._publication_accounting = None
            self._publication_context = None
            self._publication_guard = None
            self._publication_published = False
            self._publication_own_context_started = False
            self._publication_points = set()
            self._publication_observations = {}
            self._publication_shared_collections = {}
            self._publication_candidates = {}
            self._publication_proof_shapes = {}
            self._publication_receiver_receipts = {}
            self._publication_transfer_dependencies = {}
            self._publication_invocation_members = {}
            _PUBLICATION_SCOPE.reset(token)

    def _require_issued(self, prepared):
        if self._issued is not prepared:
            raise OperationsRecordError("advancement requires owner-issued preparation")
        self._require_publication()
        if (prepared.stores.configuration_acceptance is not self
                or prepared.stores is not self._publication_stores or prepared.guard is not self._publication_guard
                or prepared.stores.connection is not self._connection):
            raise OperationsRecordError("advancement requires owner-issued preparation")

    @_closed_evidence
    def _accepted_transfer(self, read, key, ref, revision):
        """Own successful completion and original receipt; never current permission."""
        accounting = _ACCOUNTING.get()
        if (accounting is None or not accounting.active or read.accounting is not accounting
                or read.connection is not self._connection or accounting.execution_context != _execution_context()
                or type(key) is not tuple or len(key) != 4
                or type(revision) is not int or not 0 <= revision <= 9007199254740991):
            raise _Unavailable
        _validate_ref(ref)
        identity = EffectAttemptIdentity(RunId(key[0]), key[1], key[2])
        if key[3] != ref.artifact_id:
            raise _Unavailable
        from .configuration_cleanup_phase_read_bounds import _cleanup_transfer_dependencies
        _cleanup_transfer_dependencies(read, key, ref, revision)
        paired = _paired_disposition(read, key, ref)
        if paired.kind != "accepted-current" or paired.acceptance_revision != revision:
            raise _Unavailable
        digest = sha256(ConfigurationInstanceRefCodec().encode_canonical_bytes(ref)).hexdigest()
        memo = ("configuration-accepted-transfer", *key, ref.workspace_id, ref.allocation_id, digest, revision)
        _require_publication_proof(read, "sources", memo)
        if memo in read.sources:
            return read.sources[memo]
        rows = read.bounded_rows("cpk_configuration_claim_transfers", _TRANSFER_COLUMNS,
            "(run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s)", key)
        if len(rows) != 1:
            raise _Unavailable
        row = rows[0]
        if (row[:9] != (*key, ref.workspace_id, ref.allocation_id, ref.runtime_id, ref.node_id, digest)
                or row[12] != revision):
            raise _Unavailable
        from .configuration_completion_store import ConfigurationCompletionStore
        completion = ConfigurationCompletionStore(self._connection)._get(identity, read)
        if (completion is None or completion.identity != identity or completion.workspace_id != ref.workspace_id
                or row[9:12] != (completion.request_fingerprint, completion.selection_fingerprint,
                    completion.outcome_fingerprint)):
            raise _Unavailable
        original = self._ref(read, key)
        evidence = _decode(original, read)
        if evidence.ref != ref:
            raise _Unavailable
        source = evidence.source
        EffectAttemptOutcomeStore(self._connection)._configuration_success(source, read)
        header, _, _, plan, request, run, material = self._receipt_context(ref.workspace_id, revision, read)
        activity = plan.plan.activity(ActivityId(identity.activity_id))
        if (run.run_id != identity.run_id.value or source.identity != identity
                or source.source.request_id != request.identity.request_id
                or source.source.workspace_id != request.identity.workspace_id
                or source.source.plan_id != plan.plan_id
                or (source.source.base_graph_id, source.source.desired_graph_id) != (plan.base_graph_id, plan.desired_graph_id)
                or header["pinned_revision"] != revision
                or type(activity.operation) not in (StartNode, ReconcileNode) or activity.operation != source.operation):
            raise _Unavailable
        slots = read.bounded_rows("cpk_configuration_accepted_slots", _columns(_SLOT),
            "(workspace_id,pinned_revision,runtime_id,node_id,artifact_id)=(%s,%s,%s,%s,%s)",
            (ref.workspace_id, revision, ref.runtime_id, ref.node_id, ref.artifact_id))
        if len(slots) != 1 or slots[0][3:7] != key:
            raise _Unavailable
        if self._material_ref(read, slots[0], material, ref.workspace_id) != original:
            raise _Unavailable
        result = ConfigurationAcceptedTransferRecord(identity, ref, revision, *row[9:12])
        _observe_publication_proof(read, "transfer", memo, row)
        _observe_publication_proof(read, "transfer-slot", memo, slots[0])
        owner = _PUBLICATION_SCOPE.get()
        if owner is not None:
            # Derive the closure from verified objects, including dependencies
            # that an earlier root already populated on this exact reader.
            owner._capture_transfer_dependencies(read, memo, ((identity, ref.artifact_id),),
                tuple(dict.fromkeys((("cpk_effect_attempt_intents", identity),
                    ("cpk_effect_attempt_outcomes", identity),
                    ("cpk_activity_events", completion.original_event_id),
                    ("cpk_activity_events", completion.direct_event_id),
                    ("configuration-receipt-context", ref.workspace_id, revision),
                    ("configuration-source-plan", ref.workspace_id, plan.plan_id)))))
        read.sources[memo] = result
        return result

    def _capture_transfer_dependencies(self, read, memo, refs, sources):
        from .configuration_cleanup_read_ceilings import _require
        self._require_publication(read=read)
        _require(self._issued is None or self._issued.read_bounds is None)
        _require(all(key in read.refs for key in refs) and all(key in read.sources for key in sources))
        captured = self._publication_transfer_dependencies.setdefault(read, {})
        dependencies = refs, sources
        _require(memo not in captured or captured[memo] == dependencies)
        captured[memo] = dependencies

    def _freeze_transfer_dependencies(self, read, proof_keys):
        from .configuration_cleanup_read_ceilings import _require
        captured = self._publication_transfer_dependencies.get(read, {})
        frozen = []
        for memo in proof_keys[1]:
            if memo[0] != "configuration-accepted-transfer":
                continue
            _require(memo in captured and memo in read.sources)
            refs, sources = captured[memo]
            _require(all(key in proof_keys[0] and key in read.refs for key in refs)
                and all(key in proof_keys[1] and key in read.sources for key in sources))
            frozen.append((memo, refs, sources))
        return tuple(frozen)

    def _require_proof_cache(self, read, family, key):
        from .configuration_cleanup_read_ceilings import _require
        self._require_publication(read=read)
        prepared = self._issued
        # Preparation still performs the full ordinary source proof. Once the
        # generated records bind, only its exact reader may reuse that proof.
        if prepared is None or prepared.read_bounds is None:
            return
        self._require_issued(prepared)
        _require(read is prepared.evidence_read)
        if family == "refs":
            _require(key in prepared.read_bounds.proof_keys[0] and key in read.refs)
            return
        _require(family == "sources" and key[0] in (
            "cpk_effect_attempt_intents", "cpk_effect_attempt_outcomes", "configuration-source-plan",
            "configuration-receipt-context", "configuration-original-slot", "cpk_activity_events",
            "configuration-accepted-transfer"))
        if key[0] == "configuration-accepted-transfer":
            _require(key in prepared.read_bounds.proof_keys[1] and key in read.sources)
            dependencies = {memo: (refs, sources)
                for memo, refs, sources in prepared.read_bounds.transfer_dependencies}
            _require(key in dependencies)
            refs, sources = dependencies[key]
            _require(all(item in prepared.read_bounds.proof_keys[0] and item in read.refs for item in refs)
                and all(item in prepared.read_bounds.proof_keys[1] and item in read.sources for item in sources))
            return
        own_context = ("configuration-receipt-context", prepared.workspace.workspace_id,
            prepared.plan.desired_graph_revision)
        own_plan = ("configuration-source-plan", prepared.workspace.workspace_id, prepared.plan.plan_id)
        if key == own_context:
            _require(self._publication_published)
            if not self._publication_own_context_started:
                # The first own context must transport actual stored records;
                # a fabricated warm entry cannot replace publication readback.
                _require(key not in read.sources and own_plan not in read.sources)
                self._publication_own_context_started = True
                return
            _require(key in read.sources)
            return
        if key == own_plan:
            _require(self._publication_published and self._publication_own_context_started
                and own_context in read.sources and key in read.sources)
            return
        _require(key in prepared.read_bounds.proof_keys[1] and key in read.sources)

    def _select_publication_point(self, role, identity):
        from .configuration_cleanup_phase_read_bounds import _shape
        self._require_publication()
        _shape(role)  # Closed package roles only; no SQL or caller registry.
        self._publication_points.add((role, identity))

    def _observe_proof_shape(self, read, family, key, row):
        from .configuration_cleanup_read_ceilings import _require
        self._require_publication(read=read)
        _require(self._issued is None or self._issued.read_bounds is None)
        if family == "invocation":
            _require(type(row) is tuple and 1 <= len(row) <= 32
                and type(key) is EffectAttemptIdentity)
            identity = (key.run_id.value, key.activity_id, key.attempt)
            _require(all(len(value) == 19 and value[:3] == identity and type(value[3]) is str for value in row))
            members = tuple(value[3] for value in row)
            _require(len(set(members)) == len(members))
            previous = self._publication_invocation_members.get(key)
            _require(previous is None or previous == members)
            self._publication_invocation_members[key] = members
            for value in row:
                # Raw completion siblings are metadata, never point-ref cache.
                self._observe_proof_shape(read, "invocation-ref", (key, value[3]), value)
            return
        columns = None
        if family == "transfer":
            columns = _TRANSFER_COLUMNS
        elif family == "completion":
            from .configuration_completion_store import _COLUMNS
            columns = _COLUMNS
        elif family == "invocation-ref":
            from .configuration_preparation_store import _PHASE_REF_COLUMNS
            columns = _PHASE_REF_COLUMNS
        elif family == "transfer-slot":
            columns = _columns(_SLOT)
        _require(family in ("ref", "source") or columns is not None)
        _require(len(row) == (len(columns) if columns is not None else 19 if family == "ref" else 17))
        _require(all(value is None or type(value) in (str, bytes, bool, int) for value in row))
        widths = tuple(0 if value is None else len(value) if type(value) is bytes else 1 if type(value) is bool
            else len(str(value).encode("utf-8")) for value in row)
        if columns is not None:
            _require(all(width <= cap for width, (_, _, cap) in zip(widths, columns, strict=True)))
        previous = self._publication_proof_shapes.get((family, key))
        if previous is not None:
            widths = tuple(max(a, b) for a, b in zip(previous, widths, strict=True))
        self._publication_proof_shapes[(family, key)] = widths

    def _observe_receiver_receipt(self, read, action, request, run, desired):
        from .configuration_cleanup_read_ceilings import _require
        self._require_publication(read=read)
        if self._issued is not None and self._issued.read_bounds is not None:
            return
        key = (action.action_id, action.session_id)
        value = (request.identity.request_id, run.run_id, desired.source_authored_graph_id, desired.projection_id)
        old = self._publication_receiver_receipts.get(key)
        _require(old is None or old == value)
        # Already validated receipt relationships for pure forecast counts;
        # these identifiers do not enroll any readable transport selector.
        self._publication_receiver_receipts[key] = value

    def _capture_native_proof_shapes(self, read, proof_keys):
        from .effect_outcome_store import _COLUMN_NAMES
        from .configuration_cleanup_read_ceilings import _require
        shapes = dict(self._publication_proof_shapes)
        for key in proof_keys[1]:
            if key[0] != "cpk_effect_attempt_outcomes":
                continue
            identity = key[1]
            row = read.sources[key][0]
            numeric = {"attempt", "fence_generation", "prior_attempt", "original_event_ordinal", "direct_event_ordinal", "observation_count"}
            columns = tuple((name, "bytes" if name == "preimage" else "int" if name in numeric else "text",
                8192 if name == "preimage" else 2048) for name in _COLUMN_NAMES)
            expressions = tuple(name if kind == "bytes" else name + "::text" for name, kind, _ in columns)
            measured = read.query("SELECT " + ",".join("octet_length(" + value + ")" for value in expressions)
                + " FROM cpk_effect_attempt_outcomes WHERE run_id=%s AND activity_id=%s AND attempt=%s",
                (identity.run_id.value, identity.activity_id, identity.attempt), records=1, octets=264, cells=22)
            _require(len(measured) == 1)
            widths = tuple(0 if value is None else value for value in measured[0])
            _require(all(type(width) is int and 0 <= width <= cap for width, (_, _, cap) in zip(widths, columns, strict=True)))
            shapes[("outcome", identity)] = widths
            for event_id in (row[15], row[18]):
                if ("event", event_id) in shapes:
                    continue
                measured = read.query("SELECT " + ",".join("octet_length(" + name + "::text)" for name in _EVENT)
                    + " FROM cpk_activity_events WHERE event_id=%s", (event_id,), records=1, octets=72, cells=6)
                _require(len(measured) == 1)
                widths = tuple(0 if value is None else value for value in measured[0])
                _require(all(type(width) is int and 0 <= width <= cap for width, cap in zip(widths, (2048, 200, 16, 64, 64, 16384), strict=True)))
                shapes[("event", event_id)] = widths
        for key in proof_keys[1]:
            if key[0] == "configuration-original-slot":
                # These cells are only canonical SQL integers and text, never
                # decoded JSON or time whose Python rendering could shrink.
                row = read.sources[key]
                _require(all(type(value) in (str, int) for value in row))
                shapes[("original-slot", key)] = tuple(len(str(value).encode("utf-8")) for value in row)
        return tuple((family, key, widths) for (family, key), widths in shapes.items())

    def _observe_publication_transport(self, read, role, identity, rows, *, point, cached=False):
        from control_plane_kit_operations._configuration_cleanup_phase_read_bounds import _PhasePoint, _PhaseCollection
        from .configuration_cleanup_phase_read_bounds import _shape
        from .configuration_cleanup_read_ceilings import _require
        self._require_publication(read=read)
        _, columns, _, _, _, keys, _ = _shape(role)
        _require(all(len(row) == len(columns) for row in rows))
        widths = tuple(max((0 if row[index] is None else len(row[index]) if type(row[index]) is bytes
            else len(row[index].encode("utf-8")) for row in rows), default=0) for index in range(len(columns)))
        _require(all(width <= cap for width, (_, _, cap) in zip(widths, columns, strict=True)))
        key = (role, identity)
        if self._issued is not None and self._issued.read_bounds is not None:
            optional = next((present for name, entry, present in self._issued.read_bounds.optional
                if name == role and entry.identity == identity), None)
            if optional is not None:
                _require(len(rows) == int(optional))
            return
        if point:
            _require(len(rows) <= 1)
            present = bool(rows)
            _require(present or role == "compensation-header")
            value = _PhasePoint(identity, widths)
        else:
            present = True
            member_keys = tuple(tuple(row[index] for index in keys) for row in rows)
            _require(len(set(member_keys)) == len(member_keys)
                and all(all(value is not None for value in member) for member in member_keys))
            value = _PhaseCollection(identity, widths, member_keys)
        previous = self._publication_observations.get(key)
        _require(not cached or previous is not None)
        if previous is not None:
            old, was_present = previous
            _require(type(old) is type(value) and was_present == present
                and (point or old.keys == value.keys))
            value = replace(value, widths=tuple(max(a, b) for a, b in zip(old.widths, widths, strict=True)))
        self._publication_observations[key] = (value, present)

    def _observe_publication_collection(self, read, role, identity, keys):
        from .configuration_cleanup_read_ceilings import _require
        self._require_publication(read=read)
        if self._issued is not None and self._issued.read_bounds is not None:
            return
        _require(role in ("bindings", "slots") and len(set(keys)) == len(keys))
        old = self._publication_shared_collections.get((role, identity))
        _require(old is None or old == keys)
        self._publication_shared_collections[(role, identity)] = keys

    def _publication_candidate(self, read, identity, rows=None):
        from .configuration_cleanup_read_ceilings import _require, _transaction
        self._require_publication(read=read)
        _require(read is not None)
        bounds = None if self._issued is None else self._issued.read_bounds
        if bounds is not None:
            entry = next((entry for entry in bounds.candidates if entry[0] == identity), None)
            _require(entry is not None)
            if rows is None:
                _require(_transaction(read) == bounds.transaction_id)
                return entry
            _require(tuple(sorted(tuple(row) for row in rows)) == entry[2])
            return
        if rows is not None:
            _require(all(len(row) == 3 and row[2] is True for row in rows))
            widths = tuple(max((len(row[index].encode("utf-8")) for row in rows), default=0) for index in (0, 1))
            value = (identity, widths, tuple(sorted(tuple(row) for row in rows)))
            previous = self._publication_candidates.get(identity)
            _require(previous is None or previous[2] == value[2])
            self._publication_candidates[identity] = value
        return None

    def _generated_publication_widths(self, read, columns, rows):
        from .configuration_cleanup_read_ceilings import _require
        if not rows:
            return (0,) * len(columns)
        casts = tuple("bytea" if kind == "bytes" else "jsonb" if kind == "json" else
            "timestamptz" if kind == "time" else "bigint" if kind == "int" else "text"
            for _, kind, _ in columns)
        values = tuple(tuple(json.dumps(value) if kind == "json" else value
            for value, (_, kind, _) in zip(row, columns, strict=True)) for row in rows)
        expressions = tuple("v.c" + str(index) for index in range(len(columns)))
        lengths = ",".join("max(octet_length(" + value + ("" if cast == "bytea" else "::text") + "))"
            for value, cast in zip(expressions, casts, strict=True))
        proposed = ",".join("(" + ",".join("%s::" + cast for cast in casts) + ")" for _ in rows)
        result = read.query("SELECT " + lengths + " FROM (VALUES " + proposed + ") AS v("
            + ",".join("c" + str(index) for index in range(len(columns))) + ")",
            tuple(value for row in values for value in row), records=1, octets=12 * len(columns), cells=len(columns))
        _require(len(result) == 1)
        widths = tuple(0 if value is None else value for value in result[0])
        _require(all(type(width) is int and 0 <= width <= cap for width, (_, _, cap) in zip(widths, columns, strict=True)))
        return widths

    def _freeze_publication(self, prepared, event, action, proof_keys):
        from control_plane_kit_operations._configuration_cleanup_phase_read_bounds import _PhasePoint, _PhaseCollection
        from .configuration_cleanup_phase_read_bounds import _capture_point, _capture_collection, _shape
        from .configuration_cleanup_read_ceilings import _require
        read = prepared.evidence_read
        points, collections, optional = {}, {}, {}
        for key, (value, present) in self._publication_observations.items():
            if key[0] == "compensation-header":
                optional[key] = (value, present)
            elif type(value) is _PhasePoint:
                points[key] = value
            else:
                collections[key] = value
        for role, identity in sorted(self._publication_points):
            key = (role, identity)
            if key not in points and key not in optional:
                points[key] = _capture_point(read, role, identity)
        for (role, identity), keys in sorted(self._publication_shared_collections.items()):
            value = _capture_collection(read, role, identity, keys)
            previous = collections.get((role, identity))
            _require(previous is None or previous.keys == value.keys)
            if previous is not None:
                value = replace(value, widths=tuple(max(a, b) for a, b in zip(previous.widths, value.widths, strict=True)))
            collections[(role, identity)] = value

        workspace = prepared.workspace.workspace_id
        revision = prepared.plan.desired_graph_revision
        locator = (workspace, prepared.request.identity.request_id, prepared.plan.plan_id, revision)
        event_values = (event.event_id, event.run_id, event.ordinal, event.kind.value, event.occurred_at,
            {"activity_id": event.activity_id, "evidence": event.evidence.descriptor(), "failure": None, "recovery": None})
        action_values = (action.action_id, action.session_id, action.ordinal, action.action_type.value,
            action.actor_id, action.payload, action.created_at, action.idempotency_key, action.intent_fingerprint)
        header = (workspace, revision, prepared.plan.desired_graph_id,
            prepared.desired_projection.projection_id, prepared.desired_projection.projection_digest,
            action.action_id, event.event_id, prepared.run.run_id, prepared.request.identity.request_id,
            prepared.plan.plan_id, len(prepared.slots), _membership_digest(prepared.slots))
        published = []
        for role, identity, values in (
                ("header", (workspace, revision), header),
                ("receipt-action", (action.action_id,), (*action_values, *locator, event.run_id)),
                ("receipt-event", (event.event_id,), (*event_values, *locator)),
                ("acceptance-action", (action.action_id, action.session_id), action_values)):
            published.append((role, _PhasePoint(identity, self._generated_publication_widths(read, _shape(role)[1], (values,)))))
        slot_widths = self._generated_publication_widths(read, _columns(_SLOT), prepared.slots)
        published.append(("slots", _PhaseCollection((workspace, revision), slot_widths,
            tuple(tuple(str(cell) for cell in row[:3]) for row in prepared.slots))))
        for role, identity, values, added in (
                ("events", (event.run_id,), event_values, (event.event_id,)),
                ("advancement-actions", (action.session_id, event.run_id), action_values, (action.action_id,))):
            key = (role, identity)
            if key in collections:
                original = collections[key]
                widths = self._generated_publication_widths(read, _shape(role)[1], (values,))
                _require(added not in original.keys)
                published.append((role, replace(original, widths=tuple(max(a, b) for a, b in zip(original.widths, widths, strict=True)),
                    keys=(*original.keys, added))))
        if prepared.receiver_truth is not None:
            before, after, origins = prepared.receiver_truth[:3]
            after_ids = {item.receiver_id for item in after}
            for receiver, origin in origins.items():
                key = ("introduction", (workspace, receiver))
                value = points[key]
                widths = list(value.widths)
                if receiver in after_ids and origin.first_accepted_action_id is None:
                    widths[10:12] = (max(widths[10], len(action.action_id.encode())), max(widths[11], len(action.session_id.encode())))
                if receiver not in after_ids and any(item.receiver_id == receiver for item in before):
                    widths[12:14] = (max(widths[12], len(action.action_id.encode())), max(widths[13], len(action.session_id.encode())))
                points[key] = replace(value, widths=tuple(widths))
        receiver_receipts = dict(self._publication_receiver_receipts)
        receiver_receipts[(action.action_id, action.session_id)] = (prepared.request.identity.request_id,
            prepared.run.run_id, prepared.plan.desired_graph_id, prepared.desired_projection.projection_id)
        return _PublicationReadBounds(proof_keys, self._publication_transaction,
            tuple((role, value) for (role, _), value in points.items()),
            tuple((role, value) for (role, _), value in collections.items()),
            tuple((role, value, present) for (role, _), (value, present) in optional.items()),
            tuple(self._publication_candidates.values()), tuple(published),
            self._capture_native_proof_shapes(read, proof_keys), tuple(receiver_receipts.items()),
            self._freeze_transfer_dependencies(read, proof_keys), tuple(self._publication_invocation_members.items()))

    def _bind_records(self, prepared, event, action):
        self._require_issued(prepared)
        if prepared.event is not None or prepared.action is not None:
            raise OperationsRecordError("advancement original records are already bound")
        prepared.stores.graphs._require_receiver_lifecycle(prepared.guard, prepared.workspace.workspace_id)
        prepared._validate_records(event, action)
        execution, history = PostgresExecutionStore(self._connection), PostgresActivityHistoryStore(self._connection)
        if (execution.get_run(prepared.run.run_id) != prepared.run
                or execution.get_request(prepared.request.identity.request_id) != prepared.request
                or history.get_plan(prepared.plan.plan_id) != prepared.plan):
            raise _Unavailable
        history.get_session(prepared.plan.session_id)
        self._projection(prepared.plan.desired_graph_id, prepared.desired_projection.projection_id,
            prepared.workspace.workspace_id)
        read = prepared.evidence_read
        proof_keys = (tuple(read.refs), tuple(key for key in read.sources
            if key[0] in ("cpk_effect_attempt_intents", "cpk_effect_attempt_outcomes", "configuration-source-plan",
                "configuration-receipt-context", "configuration-original-slot", "cpk_activity_events",
                "configuration-accepted-transfer")))
        bounds = self._freeze_publication(prepared, event, action, proof_keys)
        bound = replace(prepared, event=event, action=action, read_bounds=bounds)
        self._issued = bound
        return bound

    def _originals(self, read, action_id, event_id):
        from .configuration_cleanup_phase_read_bounds import _phase_columns, _phase_context
        _phase_context(self._connection, read=read)
        action_names = _ACTION + _LOCATOR + ("advancement_run_id",)
        event_names = _EVENT + _LOCATOR
        actions = read.bounded_rows("cpk_operation_actions",
            _phase_columns(self._connection, "receipt-action", (action_id,), _columns(action_names)), "action_id=%s", (action_id,))
        events = read.bounded_rows("cpk_activity_events",
            _phase_columns(self._connection, "receipt-event", (event_id,), _columns(event_names)), "event_id=%s", (event_id,))
        if len(actions) != 1 or len(events) != 1:
            raise _Unavailable
        payload = events[0][5]
        if type(payload) is not dict or set(payload) != {"activity_id", "evidence", "failure", "recovery"}:
            raise _Unavailable
        return _action_record(actions[0][:9]), _activity_event(events[0][:6]), actions[0][9:], events[0][6:]

    def _projection(self, graph_id, projection_id, workspace_id):
        graph = PostgresGraphTopologyStore(self._connection).get(graph_id)
        projection = PostgresRealizedGraphProjectionStore(self._connection).get(projection_id)
        if (graph.workspace_id != workspace_id or projection.workspace_id != workspace_id
                or projection.source_authored_graph_id != graph_id):
            raise _Unavailable
        material = DEFAULT_GRAPH_CODEC.decode(projection.graph_descriptor)
        slots = {(node.runtime_id, node.node_id, artifact.artifact_id): artifact
            for node in material.nodes.values() for artifact in node.configuration_artifacts}
        if len(slots) > 256:
            raise _Capacity
        return projection, slots, material

    def _ref(self, read, key):
        identity = EffectAttemptIdentity(RunId(key[0]), key[1], key[2])
        cache_key = (identity, key[3])
        _require_publication_proof(read, "refs", cache_key)
        if cache_key not in read.refs:
            rows = read.query(_REF_SELECT + " WHERE (r.run_id,r.activity_id,r.attempt,r.artifact_id)=(%s,%s,%s,%s)",
                key, records=1, octets=32768, cells=19, identities=2)
            if len(rows) != 1:
                raise _Unavailable
            _observe_publication_proof(read, "ref", cache_key, rows[0])
            _decode_ref(rows[0])
            read.refs[cache_key] = rows[0]
        row = read.refs[cache_key]
        if row[:4] != key:
            raise _Unavailable
        return row

    def _material_ref(self, read, row, material, workspace_id):
        original = self._ref(read, row[3:7])
        _, ref, birth, birth_artifact = _decode_ref(original)
        artifact = material.get(row[:3])
        if (artifact is None or ref.workspace_id != workspace_id
                or (ref.runtime_id, ref.node_id, ref.artifact_id) != row[:3]
                or _ref_key(birth, birth_artifact) != row[7:11] or original[9] != row[11]
                or (ref.target_path, ref.media_type, ref.file_mode, ref.content_digest) != (
                    artifact.target_path, artifact.media_type, artifact.file_mode, artifact.content_digest)):
            raise _Unavailable
        return original

    def _prove_use(self, read, row, plan, request, run):
        owner = _PUBLICATION_SCOPE.get()
        prepared = self._issued if owner is None else owner._issued
        bound_readback = prepared is not None and prepared.read_bounds is not None
        if bound_readback:
            # The active owner governs even a fresh store using its reader.
            # Check the outer command before historical source substitution.
            if owner is not self:
                raise _Unavailable
            self._require_issued(prepared)
            self._require_publication(read=read)
            if (read is not prepared.evidence_read or not self._publication_published
                    or row not in prepared.slots or plan != prepared.plan
                    or request != prepared.request or run != prepared.run):
                raise _Unavailable
        original = self._ref(read, row[3:7])
        evidence = _decode(original, read)
        root = self._ref(read, row[7:11])
        birth = _decode(root, read)
        if (root[16] is not True or birth.identity != birth.birth_identity
                or birth.ref != evidence.ref or birth.identity != evidence.birth_identity):
            raise _Unavailable
        # A newer outstanding use does not erase its direct birth's transfer
        # provenance. Each distinct original must prove its own disposition.
        for key in dict.fromkeys((tuple(row[3:7]), tuple(row[7:11]))):
            disposition = _paired_disposition(read, key, evidence.ref)
            if bound_readback and disposition.kind == "cleanup-closed":
                raise _Unavailable
            if disposition.kind == "accepted-current":
                self._accepted_transfer(read, key, evidence.ref, disposition.acceptance_revision)
        source = evidence.source
        if source.identity.run_id.value != run.run_id:
            plan, request, run = self._original_use(read, row, source)
        activity = plan.plan.activity(ActivityId(source.identity.activity_id))
        # The leaf use always belongs to its own verified execution. Carry
        # needs its exact original acceptance point, never an intermediate
        # receipt chain. Reuse qualifies its own original successful execution;
        # its direct birth can belong to an earlier execution.
        if (source.identity.run_id.value != run.run_id
                or source.source.workspace_id != request.identity.workspace_id
                or source.source.request_id != request.identity.request_id
                or source.source.plan_id != plan.plan_id
                or (source.source.base_graph_id, source.source.desired_graph_id) != (plan.base_graph_id, plan.desired_graph_id)
                or type(activity.operation) not in (StartNode, ReconcileNode)
                or source.operation != activity.operation):
            raise _Unavailable
        EffectAttemptOutcomeStore(self._connection)._configuration_success(source, read)
        return evidence, birth

    def _original_use(self, read, row, source):
        workspace_id = source.source.workspace_id
        plan_key = ("configuration-source-plan", workspace_id, source.source.plan_id)
        _require_publication_proof(read, "sources", plan_key)
        if plan_key not in read.sources:
            plan = PostgresActivityHistoryStore(self._connection).get_plan(source.source.plan_id)
            self._receipt_context(workspace_id, plan.desired_graph_revision, read)
        context = read.sources.get(plan_key)
        if context is None:
            raise _Unavailable
        header, _, _, plan, request, run, material = context
        if (header["workspace_id"] != workspace_id or plan.plan_id != source.source.plan_id
                or request.identity.request_id != source.source.request_id
                or run.run_id != source.identity.run_id.value):
            raise _Unavailable
        slot_key = ("configuration-original-slot", workspace_id, header["pinned_revision"], *row[:3])
        _require_publication_proof(read, "sources", slot_key)
        if slot_key not in read.sources:
            rows = read.bounded_rows("cpk_configuration_accepted_slots", _columns(_SLOT),
                "workspace_id=%s AND pinned_revision=%s AND runtime_id=%s AND node_id=%s AND artifact_id=%s",
                (workspace_id, header["pinned_revision"], *row[:3]))
            if len(rows) != 1:
                raise _Unavailable
            read.sources[slot_key] = rows[0]
        if read.sources[slot_key] != row:
            raise _Unavailable
        self._material_ref(read, row, material, workspace_id)
        return plan, request, run

    def _manifest(self, read, header, material):
        from .configuration_cleanup_phase_read_bounds import _phase_rows, _phase_context
        _phase_context(self._connection, read=read)
        rows = _phase_rows(read, "slots", (header["workspace_id"], header["pinned_revision"]))
        if rows is None:
            rows = read.bounded_rows("cpk_configuration_accepted_slots", _columns(_SLOT),
            "workspace_id=%s AND pinned_revision=%s", (header["workspace_id"], header["pinned_revision"]),
            maximum=256, point=False, order="runtime_id,node_id,artifact_id")
        if (len(rows) != header["slot_count"] or _membership_digest(rows) != header["slot_digest"]
                or tuple(row[:3] for row in rows) != tuple(sorted(material))):
            raise _Unavailable
        allocations = []
        for row in rows:
            original = self._material_ref(read, row, material, header["workspace_id"])
            allocations.append(_decode_ref(original)[1].allocation_id)
        if len(set(allocations)) != len(allocations):
            raise _Unavailable
        publication = _PUBLICATION_SCOPE.get()
        if publication is not None:
            publication._observe_publication_collection(read, "slots", (header["workspace_id"], header["pinned_revision"]),
                tuple(row[:3] for row in rows))
        return rows

    @_closed_evidence
    def _receipt_context(self, workspace_id, revision, read):
        """Original pair/header/execution point proof, without prior manifests."""
        key = ("configuration-receipt-context", workspace_id, revision)
        _require_publication_proof(read, "sources", key)
        from .configuration_cleanup_phase_read_bounds import _phase_columns, _phase_context
        _phase_context(self._connection, read=read)
        columns = _phase_columns(self._connection, "header", (workspace_id, revision), _columns(_HEADER))
        if key in read.sources:
            return read.sources[key]
        rows = read.bounded_rows("cpk_configuration_acceptances", columns,
            "workspace_id=%s AND pinned_revision=%s", (workspace_id, revision))
        if len(rows) != 1:
            raise _Unavailable
        header = dict(zip(_HEADER, rows[0]))
        from .configuration_cleanup_phase_read_bounds import _phase_require
        parent = (workspace_id, revision)
        for role, child in (("receipt-action", (header["action_id"],)), ("receipt-event", (header["event_id"],)),
                ("request", (header["request_id"],)), ("run", (header["run_id"],)), ("plan", (header["plan_id"],)),
                ("graph", (header["graph_id"],)), ("projection", (header["projection_id"],))):
            _phase_require(self._connection, "header", parent, role, child)
        if not 0 <= header["slot_count"] <= 256:
            raise _Unavailable
        action, event, action_locator, event_locator = self._originals(read, header["action_id"], header["event_id"])
        expected = (workspace_id, header["request_id"], header["plan_id"], revision)
        if event_locator != expected or action_locator != expected + (header["run_id"],):
            raise _Unavailable
        execution, history = PostgresExecutionStore(self._connection), PostgresActivityHistoryStore(self._connection)
        run = execution.get_run(header["run_id"])
        request = execution.get_request(header["request_id"])
        plan = history.get_plan(header["plan_id"])
        _phase_require(self._connection, "header", parent, "session", (plan.session_id,))
        session = history.get_session(plan.session_id)
        if (run.admission.request_id != request.identity.request_id or run.plan_id != plan.plan_id
                or request.identity.workspace_id != workspace_id or request.identity.plan_id != plan.plan_id
                or request.identity.session_id != plan.session_id or session.workspace_id != workspace_id
                or plan.desired_graph_revision != revision or plan.desired_graph_id != header["graph_id"]
                or plan.desired_realized_projection_id != header["projection_id"]):
            raise _Unavailable
        projection, material, _ = self._projection(header["graph_id"], header["projection_id"], workspace_id)
        if (projection.projection_digest != header["projection_digest"] or len(material) != header["slot_count"]
                or len(header["slot_digest"]) != 64
                or any(character not in "0123456789abcdef" for character in header["slot_digest"])):
            raise _Unavailable
        historical_event, historical_action = _history_records(event, action)
        receipt = historical_advancement(workspace_id=workspace_id, session_id=plan.session_id,
            plan_id=plan.plan_id, plan={name: getattr(plan, name) for name in (
                "base_graph_id", "base_realized_projection_id", "desired_graph_id",
                "desired_realized_projection_id", "desired_graph_revision")},
            request_id=request.identity.request_id, run_id=run.run_id,
            projection_digest=projection.projection_digest, events=(historical_event,), actions=(historical_action,))
        if receipt["state"] != "accepted":
            raise _Unavailable
        context = header, action, event, plan, request, run, material
        read.sources[key] = context
        read.sources[("configuration-source-plan", workspace_id, plan.plan_id)] = context
        return context

    @_closed_evidence
    def _receipt_manifest(self, workspace_id, revision, read):
        snapshot_start = read.used.accounted_bytes
        header, action, event, plan, request, run, material = self._receipt_context(workspace_id, revision, read)
        slots = self._manifest(read, header, material)
        if read.used.accounted_bytes - snapshot_start > 3 * 1024 * 1024:
            raise _Capacity
        return header, action, event, slots, plan, request, run

    @_closed_evidence
    def _receipt(self, workspace_id, revision, *, read=None):
        """Full-proof contract retained for replay and current-row verification."""
        read = _EvidenceRead(self._connection) if read is None else read
        receipt = self._receipt_manifest(workspace_id, revision, read)
        for row in receipt[3]:
            self._prove_use(read, row, *receipt[4:])
        return receipt[:3]

    def _latest(self, read, *, action, workspace_id):
        table, key, kind, value = (("cpk_operation_actions", "action_id", "action_type", "advance-current-graph")
            if action else ("cpk_activity_events", "event_id", "event_type", "current_graph_advanced"))
        names = (key, *_LOCATOR[:3], "advancement_run_id" if action else "run_id")
        selected = ",".join(f"CASE WHEN octet_length({name}) BETWEEN 1 AND 2048 THEN {name} END" for name in names)
        rows = read.query(f"SELECT {selected},advancement_revision FROM {table} "
            f"WHERE advancement_workspace_id=%s AND {kind}=%s ORDER BY advancement_revision DESC,{key} LIMIT 2",
            (workspace_id, value), records=2, octets=2 * (5 * 2048 + 20), cells=6)
        if any(any(value is None for value in row) or type(row[-1]) is not int or row[-1] < 0 for row in rows):
            raise _Unavailable
        if len(rows) == 2 and rows[0][-1] == rows[1][-1]:
            raise _Unavailable
        return rows[0] if rows else None

    def _current_manifest(self, workspace, read):
        action = self._latest(read, action=True, workspace_id=workspace.workspace_id)
        event = self._latest(read, action=False, workspace_id=workspace.workspace_id)
        if action is None and event is None:
            origin = _read_workspace_initialization(self._connection, workspace.workspace_id)
            if (workspace.current_graph_id, workspace.current_realized_projection_id) != (
                    origin.initial_graph_id, origin.initial_projection_id):
                raise _Unavailable
            return ({"workspace_id": workspace.workspace_id, "graph_id": origin.initial_graph_id,
                "projection_id": origin.initial_projection_id, "pinned_revision": None, "slot_count": 0},
                None, None, (), None, None, None)
        if action is None or event is None or action[1:] != event[1:]:
            raise _Unavailable
        receipt = self._receipt_manifest(workspace.workspace_id, action[-1], read)
        header = receipt[0]
        if (header["action_id"], header["event_id"], header["graph_id"], header["projection_id"]) != (
                action[0], event[0], workspace.current_graph_id, workspace.current_realized_projection_id):
            raise _Unavailable
        return receipt

    def _current_receipt(self, workspace):
        """Advancement and schema current validation retain every source proof."""
        read = _EvidenceRead(self._connection)
        receipt = self._current_manifest(workspace, read)
        for row in receipt[3]:
            self._prove_use(read, row, *receipt[4:])
        return receipt

    def _observed_bindings(self, receipt, rows, read):
        bindings = []
        for row in rows:
            source, birth = self._prove_use(read, row, *receipt[4:])
            bindings.append(ConfigurationAcceptedBinding(source.ref, source, birth))
        header = receipt[0]
        return ConfigurationCurrentEvidence("complete", header["workspace_id"], header["graph_id"],
            header["projection_id"], header["pinned_revision"], header["slot_count"], tuple(bindings))

    @_public_read
    def read_current_configuration(self, workspace_id, *, node_id=None):
        """Prove the complete manifest, then only the requested original sources."""
        _read_identifier(workspace_id)
        if node_id is not None:
            _read_identifier(node_id)
        read = _EvidenceRead(self._connection)
        receipt = self._current_manifest(PostgresWorkspaceStore(self._connection).get(workspace_id), read)
        rows = tuple(row for row in receipt[3] if node_id is None or row[1] == node_id)
        counts = {}
        for row in rows:
            counts[row[1]] = counts.get(row[1], 0) + 1
        if any(count > 32 for count in counts.values()):
            raise _Capacity
        return self._observed_bindings(receipt, rows, read)

    def _known_birth(self, read, exact_ref):
        candidates = read.bounded_rows("cpk_effect_configuration_refs", _REF_KEY_COLUMNS,
            "workspace_id=%s AND allocation_id=%s AND is_birth", (exact_ref.workspace_id, exact_ref.allocation_id),
            maximum=2, point=True, order="run_id,activity_id,attempt,artifact_id")
        if len(candidates) != 1:
            raise _Unavailable
        row = self._ref(read, candidates[0])
        birth = _decode(row, read)
        if row[16] is not True or birth.identity != birth.birth_identity or birth.ref != exact_ref:
            raise _Unavailable
        disposition = _paired_disposition(read, tuple(row[:4]), exact_ref)
        if disposition.kind == "accepted-current":
            self._accepted_transfer(read, tuple(row[:4]), exact_ref, disposition.acceptance_revision)
        # Known pending/failed/staged allocations need no successful outcome
        # merely to be observed absent. Their reciprocal claim remains protective.
        return birth

    @_public_read
    def read_configuration_use(self, workspace_id, exact_refs):
        """Exact current membership only; empty matches never grant cleanup."""
        _read_identifier(workspace_id)
        if type(exact_refs) is not tuple:
            raise _Unavailable
        if len(exact_refs) > 32:
            raise _Capacity
        for ref in exact_refs:
            _validate_ref(ref)
            if ref.workspace_id != workspace_id:
                raise _Unavailable
        if len({ref.allocation_id for ref in exact_refs}) != len(exact_refs):
            raise _Unavailable
        read = _EvidenceRead(self._connection)
        receipt = self._current_manifest(PostgresWorkspaceStore(self._connection).get(workspace_id), read)
        for ref in exact_refs:
            self._known_birth(read, ref)
        requested = {ref.allocation_id: ref for ref in exact_refs}
        rows = []
        for row in receipt[3]:
            ref = _decode_ref(self._ref(read, row[3:7]))[1]
            candidate = requested.get(ref.allocation_id)
            if candidate is not None:
                if ref != candidate:
                    raise _Unavailable
                rows.append(row)
        return self._observed_bindings(receipt, rows, read)

    @_closed_evidence
    def _prepare(self, stores, workspace, request, run, plan, guard, current_projection, desired_projection, *, receiver_truth=None):
        from control_plane_kit_operations.advancement import _require_complete_success
        self._require_publication()
        if stores is not self._publication_stores or guard is not self._publication_guard:
            raise _Unavailable
        stores.graphs._require_receiver_lifecycle(guard, workspace.workspace_id)
        if (stores.connection is not self._connection or request.identity.workspace_id != workspace.workspace_id
                or request.identity.plan_id != plan.plan_id or run.admission.request_id != request.identity.request_id
                or run.plan_id != plan.plan_id or request.identity.session_id != plan.session_id
                or (workspace.current_graph_id, workspace.current_realized_projection_id,
                    workspace.desired_graph_id, workspace.desired_realized_projection_id, workspace.desired_graph_revision)
                != (plan.base_graph_id, plan.base_realized_projection_id, plan.desired_graph_id,
                    plan.desired_realized_projection_id, plan.desired_graph_revision)):
            raise _Unavailable
        _require_complete_success(plan.plan, run, stores.execution.events_for_run(run.run_id))
        current = self._current_receipt(workspace)
        base, base_slots, base_graph = self._projection(plan.base_graph_id, plan.base_realized_projection_id, workspace.workspace_id)
        desired, material, desired_graph = self._projection(plan.desired_graph_id, plan.desired_realized_projection_id, workspace.workspace_id)
        if base != current_projection or desired != desired_projection:
            raise _Unavailable
        current_slots = {row[:3]: row for row in current[3]}
        if set(current_slots) != set(base_slots):
            raise _Unavailable
        installed_nodes = {activity.operation.target.node_id for activity in plan.plan.activities
            if type(activity.operation) in (StartNode, ReconcileNode)}
        # The current proof's caches must not make the later cold-consumer
        # admission underestimate desired source/receipt work. Fresh caches,
        # same command ledger; every actual repeat remains charged.
        read, slots = _EvidenceRead(self._connection), []
        snapshot_start = read.used.accounted_bytes
        for slot in sorted(material):
            if slot[1] not in installed_nodes:
                if (slot not in current_slots or base_graph.nodes.get(slot[1]) != desired_graph.nodes[slot[1]]):
                    raise _Unavailable
                row = current_slots[slot]
                self._material_ref(read, row, material, workspace.workspace_id)
                slots.append(row)
                continue
            # Discover only this advancing run/slot, before source joins; do not
            # turn an unrelated old successful attempt into a new installation.
            candidates = read.bounded_rows("cpk_effect_configuration_refs", _REF_KEY_COLUMNS,
                "run_id=%s AND workspace_id=%s AND runtime_id=%s AND node_id=%s AND artifact_id=%s",
                (run.run_id, workspace.workspace_id, *slot), maximum=2, point=True, order="activity_id,attempt")
            if len(candidates) != 1:
                raise _Unavailable
            original = self._ref(read, candidates[0])
            _, _, birth, birth_artifact = _decode_ref(original)
            row = (*slot, *candidates[0], *_ref_key(birth, birth_artifact), original[9])
            self._material_ref(read, row, material, workspace.workspace_id)
            slots.append(row)
        # Snapshot material is admitted before provenance work; source/slot
        # consumer size is preflighted again with the complete generated pair.
        if read.used.accounted_bytes - snapshot_start > 3 * 1024 * 1024:
            raise _Capacity
        proof_start = read.used
        self._require_current_slots(read, slots)
        for row in slots:
            self._prove_use(read, row, plan, request, run)
        from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint
        proof = ConfigurationEvidenceFootprint(*(getattr(read.used, field) - getattr(proof_start, field)
            for field in ("records", "value_octets", "scalar_markers", "statements")))
        prepared = _PreparedAdvancementReceipt(stores, guard, workspace, request, run, plan,
            current_projection, desired_projection, slots=tuple(slots), evidence_read=read, proof_footprint=proof,
            receiver_truth=receiver_truth)
        self._issued = prepared
        return prepared

    def _require_current_slots(self, read, slots):
        from .configuration_preparation_store import _paired_disposition, _require_unreserved
        for slot in slots:
            row = self._ref(read, slot[3:7])
            _, ref, _, _ = _decode_ref(row)
            _require_unreserved(read, ref)
            for key in dict.fromkeys((tuple(slot[3:7]), tuple(slot[7:11]))):
                original = self._ref(read, key)
                if _decode_ref(original)[1] != ref:
                    raise _Unavailable
                disposition = _paired_disposition(read, key, ref)
                if disposition.kind == "accepted-current":
                    self._accepted_transfer(read, key, ref, disposition.acceptance_revision)
                elif disposition.kind != "outstanding":
                    raise _Unavailable

    @_closed_evidence
    def _require_current(self, prepared):
        self._require_current_slots(prepared.evidence_read, prepared.slots)

    def _insert(self, prepared):
        workspace_id = prepared.workspace.workspace_id
        _require_prepared_advancement(prepared, self._connection, workspace_id, after_cas=True)
        values = (workspace_id, prepared.plan.desired_graph_revision, prepared.plan.desired_graph_id,
            prepared.desired_projection.projection_id, prepared.desired_projection.projection_digest,
            prepared.action.action_id, prepared.event.event_id, prepared.run.run_id,
            prepared.request.identity.request_id, prepared.plan.plan_id, len(prepared.slots), _membership_digest(prepared.slots))
        _EvidenceRead(self._connection).query("INSERT INTO cpk_configuration_acceptances (" + ",".join(_HEADER)
            + ") VALUES (" + ",".join("%s" for _ in _HEADER) + ") RETURNING 1",
            values, records=1, octets=1, cells=1)
        for slot in prepared.slots:
            _EvidenceRead(self._connection).query("INSERT INTO cpk_configuration_accepted_slots (workspace_id,pinned_revision,"
                + ",".join(_SLOT) + ") VALUES (" + ",".join("%s" for _ in range(14)) + ") RETURNING 1",
                (workspace_id, prepared.plan.desired_graph_revision, *slot), records=1, octets=1, cells=1)
        self._publication_published = True
        # Verify actual stored bytes as well as the pre-CAS consumer preflight.
        header, action, event = self._receipt(workspace_id, prepared.plan.desired_graph_revision, read=prepared.evidence_read)
        if tuple(header[name] for name in _HEADER) != values or action != prepared.action or event != prepared.event:
            raise _Unavailable

    def _preflight(self, prepared):
        """Admit separate material, native-consumer and publication S/H gates."""
        from control_plane_kit_operations.configuration_preparation import (
            ConfigurationCapacityDecision, configuration_evidence_capacity,
        )
        snapshot, future, publication = self._publication_budgets(prepared)
        if snapshot.accounted_bytes > 3 * 1024 * 1024:
            raise _Capacity
        for footprint in (future.settled, future.peak):
            if configuration_evidence_capacity(footprint) is not ConfigurationCapacityDecision.WITHIN_LIMITS:
                raise _Capacity
        prior = prepared.evidence_read.used
        for footprint in (publication.settled, publication.peak):
            if configuration_evidence_capacity(prior.plus(footprint)) is not ConfigurationCapacityDecision.WITHIN_LIMITS:
                raise _Capacity

    def _publication_budgets(self, prepared):
        """Pure composition of the fixed source query positions and captured facts."""
        from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint as F
        from control_plane_kit_operations._configuration_preparation import _OrdinarySuffixBudget as Budget
        from .configuration_cleanup_read_ceilings import _require
        self._require_issued(prepared)
        bounds, read = prepared.read_bounds, prepared.evidence_read
        zero = F(0, 0, 0, 0)
        empty = Budget(zero, zero)

        def chain(*parts):
            result = empty
            for part in parts:
                result = result.then(part)
            return result

        def q(rows, octets, cells, identities=1, *, settled=None, cleanup=zero):
            reserve = F(rows * identities, octets, rows * cells, 1)
            return Budget(reserve if settled is None else settled, reserve.plus(cleanup))

        guard = q(1, 20, 1)
        unit = q(1, 1, 1)

        def native(widths, *, count=1, maximum=1, point=True, identities=1):
            cells, octets = len(widths), sum(widths)
            nominal = maximum if point else maximum + 1
            lengths = q(nominal, nominal * 12 * cells, cells, identities,
                settled=F(count * identities, count * 12 * cells, count * cells, 1))
            if count == 0:
                return lengths
            fetched = maximum if point else count + 1
            return lengths.then(q(fetched, fetched * (octets + 1), cells + 1, identities,
                settled=F(count * identities, count * (octets + 1), count * (cells + 1), 1)))

        # Preserve exact identities as keys; role aliases use explicit maxima
        # only at source positions whose concrete key is not retained there.
        entries = {(role, entry.identity): entry for role, entry in bounds.points + bounds.collections}
        entries.update(((role, entry.identity), entry) for role, entry, _ in bounds.optional)
        entries.update(((role, entry.identity), entry) for role, entry in bounds.published)

        def point(role, identity, *, captured=True):
            value = entries.get((role, identity))
            _require(value is not None)
            result = native(value.widths, identities=2 if role == "origin-action" else 1)
            return guard.then(result) if captured else result

        def collection(role, identity, *, captured=True, native_maximum=None):
            value = entries.get((role, identity))
            _require(value is not None and hasattr(value, "keys"))
            count = len(value.keys)
            result = native(value.widths, count=count,
                maximum=count if native_maximum is None else native_maximum, point=False)
            return guard.then(result) if captured else result

        def widest_point(role):
            values = tuple(point(role, identity) for selected_role, identity in entries if selected_role == role)
            _require(bool(values))
            fields = ("records", "value_octets", "scalar_markers", "statements")
            return Budget(F(*(max(getattr(value.settled, field) for value in values) for field in fields)),
                F(*(max(getattr(value.peak, field) for value in values) for field in fields)))

        workspace = prepared.workspace.workspace_id
        request_id, run_id = prepared.request.identity.request_id, prepared.run.run_id
        revision, plan_id = prepared.plan.desired_graph_revision, prepared.plan.plan_id
        workspace_read = native((81920,) + (0,) * 8)

        def context(header, plan, *, captured):
            positions = (("header", (header["workspace_id"], header["pinned_revision"])),
                ("receipt-action", (header["action_id"],)), ("receipt-event", (header["event_id"],)),
                ("run", (header["run_id"],)), ("request", (header["request_id"],)), ("plan", (header["plan_id"],)),
                ("session", (plan.session_id,)), ("graph", (header["graph_id"],)), ("projection", (header["projection_id"],)))
            return chain(*(point(role, identity, captured=captured) for role, identity in positions))

        own_header = dict(workspace_id=workspace, pinned_revision=revision, action_id=prepared.action.action_id,
            event_id=prepared.event.event_id, run_id=run_id, request_id=request_id, plan_id=plan_id,
            graph_id=prepared.plan.desired_graph_id, projection_id=prepared.desired_projection.projection_id)
        proof_shapes = {(family, key): widths for family, key, widths in bounds.proof_shapes}
        ref_memo, source_memo, outcome_memo, event_memo = set(), set(), set(), set()
        context_memo, plan_memo, slot_memo = set(), set(), set()

        def ref_key(key):
            return EffectAttemptIdentity(RunId(key[0]), key[1], key[2]), key[3]

        def ref_budget(key):
            if key in ref_memo:
                return empty
            widths = proof_shapes[("ref", key)]
            ref_memo.add(key)
            return q(1, 32768, 19, 2, settled=F(2, sum(widths), 19, 1))

        def source_budget(identity):
            if identity in source_memo:
                return empty
            widths = proof_shapes[("source", identity)]
            source_memo.add(identity)
            source = read.sources[("cpk_effect_attempt_intents", identity)][0]
            event_memo.add(source.original_event_id)
            savepoint = q(0, 0, 0)
            joined = q(1, 80032, 17, 3, settled=F(3, sum(widths), 17, 1), cleanup=F(0, 0, 0, 2))
            return chain(savepoint, joined, savepoint)

        def outcome_budget(identity):
            if identity in outcome_memo:
                return empty
            result = native(proof_shapes[("outcome", identity)])
            row = read.sources[("cpk_effect_attempt_outcomes", identity)][0]
            for event_id in (row[15], row[18]):
                if event_id not in event_memo:
                    result = result.then(native(proof_shapes[("event", event_id)]))
                    event_memo.add(event_id)
            outcome_memo.add(identity)
            return result

        transfers = {ref_key(memo[1:5]): memo for memo, _, _ in bounds.transfer_dependencies}
        invocation_members, transfer_memo = dict(bounds.invocation_members), set()
        native_pair = q(1, 855, 11, 2)
        native_accepted = native_pair.then(q(1, 1, 1, 4))
        sibling_branches = (native_pair, native_accepted, native_pair.then(q(1, 1, 1, 3)))
        # Sibling disposition is not retained as authority. Bound its finite
        # structural branches at every repeated completion position instead.
        def sibling_cost(kind):
            return F(*(max(getattr(getattr(branch, kind), field) for branch in sibling_branches)
                for field in ("records", "value_octets", "scalar_markers", "statements")))

        sibling_disposition = Budget(sibling_cost("settled"), sibling_cost("peak"))

        def transfer_budget(key):
            memo = transfers[key]
            result = native_accepted  # Fresh D also runs on every warm T hit.
            if memo in transfer_memo:
                return result
            identity, _ = key
            members = invocation_members[identity]
            widths = tuple(proof_shapes[("invocation-ref", (identity, artifact))] for artifact in members)
            invocation = q(33, 33 * 32768, 19, 2,
                settled=F(2 * len(members), sum(map(sum, widths)), 19 * len(members), 1))
            result = chain(result, native(proof_shapes[("transfer", memo)]),
                native(proof_shapes[("completion", identity)]), invocation,
                *(source_budget(identity) for _ in members),
                *(sibling_disposition for _ in members), source_budget(identity), outcome_budget(identity),
                ref_budget(key), source_budget(identity), outcome_budget(identity))
            # Completion/I repeat per cold root; their raw siblings never
            # populate ref_memo, and there is no completion or slot memo.
            context_key = (memo[5], memo[-1])
            header, _, _, plan, _, _, _ = read.sources[("configuration-receipt-context", *context_key)]
            if context_key not in context_memo:
                result = result.then(context(header, plan, captured=False))
                context_memo.add(context_key)
                plan_memo.add((header["workspace_id"], plan.plan_id))
            result = result.then(native(proof_shapes[("transfer-slot", memo)]))
            transfer_memo.add(memo)
            return result

        snapshot_budget = chain(context(own_header, prepared.plan, captured=False),
            collection("slots", (workspace, revision), captured=False, native_maximum=256),
            *(ref_budget(ref_key(slot[3:7])) for slot in prepared.slots))
        context_memo.add((workspace, revision))
        plan_memo.add((workspace, plan_id))
        # Native S/H describes this captured post-publication proof state.
        # Later changed-state readers can cold-prove new roots under their own
        # shared ledger; they do not inherit publication's frozen-memo refusal.
        future = snapshot_budget
        for slot in prepared.slots:
            source_key, birth_key = ref_key(slot[3:7]), ref_key(slot[7:11])
            raw = read.refs[source_key]
            source = next(value for value in read.sources[("cpk_effect_attempt_intents", source_key[0])]
                if (value.ref.workspace_id, value.ref.allocation_id, value.ref.artifact_id) == (raw[4], raw[5], raw[3]))
            future = chain(future, ref_budget(source_key), source_budget(source_key[0]),
                ref_budget(birth_key), source_budget(birth_key[0]))
            for key in dict.fromkeys((source_key, birth_key)):
                future = future.then(native_accepted if key in transfers else native_pair)
                if key in transfers:
                    future = future.then(transfer_budget(key))
            if source.identity.run_id.value != run_id:
                source_plan = (source.source.workspace_id, source.source.plan_id)
                saved = read.sources[("configuration-source-plan", *source_plan)]
                header, _, _, plan, _, _, _ = saved
                old_context = (source.source.workspace_id, header["pinned_revision"])
                if source_plan not in plan_memo:
                    future = future.then(point("plan", (source.source.plan_id,), captured=False))
                    if old_context not in context_memo:
                        future = future.then(context(header, plan, captured=False))
                        context_memo.add(old_context)
                    plan_memo.add(source_plan)
                old_slot = ("configuration-original-slot", *old_context, *slot[:3])
                if old_slot not in slot_memo:
                    future = future.then(native(proof_shapes[("original-slot", old_slot)]))
                    slot_memo.add(old_slot)
            future = future.then(outcome_budget(source.identity))
        future = chain(workspace_read, q(2, 20520, 6), q(2, 20520, 6), future)

        transferred_keys = {memo[1:5] for memo, _, _ in bounds.transfer_dependencies}

        def publication_disposition(key):
            paired = q(1, 855, 11, 2)
            accepted = paired.then(q(1, 1, 1, 4))
            closed = paired.then(q(1, 1, 1, 3))
            transferred = key in transferred_keys
            success = accepted.then(accepted) if transferred else paired
            # A fresh closure refuses at either disposition position. An
            # outstanding key changed to accepted reaches both pair/anchor
            # checks before the unknown frozen transfer memo refuses.
            alternatives = (closed, accepted.then(closed) if transferred else accepted.then(accepted))
            peak = F(*(max(getattr(value.peak, field) for value in (success, *alternatives))
                for field in ("records", "value_octets", "scalar_markers", "statements")))
            return Budget(success.settled, peak)

        def publication_slot(slot):
            return chain(*(publication_disposition(key)
                for key in dict.fromkeys((tuple(slot[3:7]), tuple(slot[7:11])))))

        current_slots = chain(*(chain(q(1, 1, 1, settled=F(0, 0, 0, 1)), publication_slot(slot))
            for slot in prepared.slots))
        readback = chain(*(publication_slot(slot) for slot in prepared.slots))
        prepared_guard = chain(guard, workspace_read, point("request", (request_id,)), point("run", (run_id,)), current_slots)
        body = chain(prepared_guard, *(native((2048,)) for _ in range(3)), unit, workspace_read,
            prepared_guard, unit, prepared_guard, unit, prepared_guard, unit,
            *(unit for _ in prepared.slots), context(own_header, prepared.plan, captured=True),
            collection("slots", (workspace, revision)), readback)
        if prepared.receiver_truth is not None:
            before, after, origins, scopes, original, _, evidence = prepared.receiver_truth

            def verify(selected_request):
                return chain(widest_point("session-workspace"), widest_point("plan"),
                    widest_point("graph"), widest_point("projection"), widest_point("graph"), widest_point("projection"),
                    point("scope-header", (selected_request,)), collection("scopes", (selected_request,)))

            def binding_material(graph_id, projection_id):
                return chain(point("raw-graph", (workspace, graph_id)),
                    point("raw-projection", (workspace, projection_id, graph_id)),
                    collection("bindings", (workspace, graph_id, projection_id)))

            def retained(graph_id, projection_id):
                return chain(point("graph", (graph_id,)), point("projection", (projection_id,)), binding_material(graph_id, projection_id))

            def origin_budget(origin):
                return chain(point("introduction", (workspace, origin.receiver_id)),
                    binding_material(origin.introducing_graph_id, origin.introducing_realized_projection_id),
                    point("origin-action", (origin.introducing_action_id, origin.introducing_session_id, workspace)),
                    point("projection", (origin.introducing_realized_projection_id,)), point("graph", (origin.introducing_graph_id,)), unit)

            runtime_wide = {scope.runtime_id for scope in scopes if scope.node_id is None}
            selected_scopes = {scope for scope in scopes if scope.node_id is None or scope.runtime_id not in runtime_wide}
            prefixes = {(workspace, scope.runtime_id, kind, node) for scope in selected_scopes
                for kind, node in (("runtime", None), ("all-nodes" if scope.node_id is None else "node", scope.node_id))}
            candidates = {identity: (widths, rows) for identity, widths, rows in bounds.candidates}
            history = guard
            for identity in sorted(prefixes):
                widths, rows = candidates[identity]
                count, width = len(rows), sum(widths) + 1
                history = chain(history, guard, q(count + 1, (count + 1) * width, 3,
                    settled=F(count, count * width, count * 3, 1)))
            for item in evidence.requests:
                selected_request = item.request.identity.request_id
                history = chain(history, point("request", (selected_request,)), verify(selected_request), collection("runs", (selected_request,)))
                for run in item.runs:
                    selected_run = run.run.run_id
                    history = chain(history, collection("events", (selected_run,)), collection("attempts", (selected_run,)),
                        collection("intents", (selected_run,)), collection("outcomes", (selected_run,)))
                    for outcome in run.outcomes:
                        identity = outcome.attempt.state.identity
                        key = (identity.run_id.value, identity.activity_id, identity.attempt)
                        members = entries[("outcome-memberships", key)]
                        history = chain(history, collection("outcome-memberships", key),
                            *(widest_point("observation") for _ in members.keys))
                    optional = next((entry, present) for role, entry, present in bounds.optional
                        if role == "compensation-header" and entry.identity == (selected_run,))
                    history = chain(history, guard, native(optional[0].widths, count=int(optional[1])))
                    for record, program in run.compensations:
                        history = chain(history, collection("compensation-steps", (program.program_id,)),
                            point("compensation-action", (record.action_id,)), collection("compensation-bindings", (program.program_id,)))
                    history = chain(history, collection("advancement-actions", (item.request.identity.session_id, selected_run)),
                        collection("cancellation-actions", (item.request.identity.session_id, selected_run)))
            plan, base, desired = original
            comparison = chain(point("request", (request_id,)), point("run", (run_id,)), workspace_read,
                guard, verify(request_id), retained(plan.base_graph_id, base.projection_id), retained(plan.desired_graph_id, desired.projection_id),
                *(point("introduction", (workspace, receiver)) for receiver in origins))
            after_ids = {binding.receiver_id for binding in after}
            witnesses = [binding.receiver_id for binding in after if origins[binding.receiver_id].first_accepted_action_id is None]
            witnesses += [binding.receiver_id for binding in before if binding.receiver_id not in after_ids]
            witness = chain(*(chain(guard, q(1, 1, 1, 2), point("introduction", (workspace, receiver)),
                q(1, 32, 1), point("introduction", (workspace, receiver))) for receiver in witnesses))
            receipts, seen_requests = empty, set()
            relationships = dict(bounds.receiver_receipts)
            actions = {(origins[binding.receiver_id].first_accepted_action_id or prepared.action.action_id,
                origins[binding.receiver_id].first_accepted_session_id or prepared.action.session_id) for binding in after}
            for action_id, session_id in sorted(actions):
                selected_request, selected_run, graph_id, projection_id = relationships[(action_id, session_id)]
                receipts = receipts.then(point("acceptance-action", (action_id, session_id)))
                if selected_request not in seen_requests:
                    receipts = chain(receipts, point("request", (selected_request,)), verify(selected_request), collection("runs", (selected_request,)))
                    seen_requests.add(selected_request)
                receipts = chain(receipts, collection("events", (selected_run,)), collection("advancement-actions", (session_id, selected_run)),
                    collection("bindings", (workspace, graph_id, projection_id)))
            sources = chain(retained(plan.desired_graph_id, desired.projection_id), retained(plan.desired_graph_id, desired.projection_id),
                *(origin_budget(origins[binding.receiver_id]) for binding in after), receipts,
                *(origin_budget(origins[binding.receiver_id]) for binding in after))
            body = chain(body, history, comparison, witness, sources, comparison, history)
        # Every failed query can leave its full reservation outstanding before
        # the one usable close. INERROR cleanup performs less work, never more.
        publication = Budget(body.settled.plus(guard.settled), body.peak.plus(guard.peak))
        return snapshot_budget.settled, future, publication

    def _verify_replay(self, result):
        header, action, event = self._receipt(result.workspace_id, result.desired_graph_revision)
        if action != result.action or event != result.event:
            raise _Unavailable


def validate_configuration_advancement_rows(connection):
    """Bounded original-side scans; no backfill, adoption or repair."""
    store = ConfigurationAcceptanceStore(connection)
    _validate_transfer_rows(connection, store)
    for table, key, kind, value in (("cpk_operation_actions", "action_id", "action_type", "advance-current-graph"),
            ("cpk_activity_events", "event_id", "event_type", "current_graph_advanced")):
        cursor = ""
        while True:
            rows = _EvidenceRead(connection, standalone=True).bounded_rows(table,
                _columns((key, "advancement_workspace_id", "advancement_revision")),
                f"{key}>%s AND {kind}=%s", (cursor, value), maximum=32, order=key)
            if not rows:
                break
            for identity, workspace_id, revision in rows:
                with _configuration_accounting(("configuration-verification", table, identity)):
                    header, _, _ = store._receipt(workspace_id, revision)
                    if header[key] != identity:
                        raise _Unavailable
            cursor = rows[-1][0]
    # Also discover headers independently: a header pointing at unrelated
    # history must not evade the original-kind scans above.
    cursor = ("", -1)
    previous_workspace = None
    while True:
        rows = _EvidenceRead(connection, standalone=True).bounded_rows("cpk_configuration_acceptances",
            _columns(("workspace_id", "pinned_revision")), "(workspace_id,pinned_revision)>(%s,%s)",
            cursor, maximum=32, order="workspace_id,pinned_revision")
        if not rows:
            return
        for workspace_id, revision in rows:
            with _configuration_accounting(("configuration-verification", workspace_id, revision)):
                store._receipt(workspace_id, revision)
                if workspace_id != previous_workspace:
                    store._current_receipt(PostgresWorkspaceStore(connection).get(workspace_id))
                    previous_workspace = workspace_id
        cursor = rows[-1]


def _validate_transfer_rows(connection, store):
    """Independent original-key pages; every retained disposition is point-proved."""
    cursor = ("", "", 0, "")
    columns = _TRANSFER_COLUMNS[:4] + (_TRANSFER_COLUMNS[-1],)
    while True:
        rows = _EvidenceRead(connection, standalone=True).bounded_rows("cpk_configuration_claim_transfers",
            columns, "(run_id,activity_id,attempt,artifact_id)>(%s,%s,%s,%s)", cursor,
            maximum=32, order="run_id,activity_id,attempt,artifact_id")
        if not rows:
            return
        for row in rows:
            key = row[:4]
            with _configuration_accounting(("configuration-transfer-verification", *key)):
                with _joined_read(connection) as read:
                    ref = _decode(store._ref(read, key), read).ref
                    store._accepted_transfer(read, key, ref, row[4])
            cursor = key
