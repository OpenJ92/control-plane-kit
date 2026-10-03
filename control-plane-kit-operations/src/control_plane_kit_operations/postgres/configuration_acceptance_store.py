"""Original accepted membership in the advancement owner's transaction."""
from hashlib import sha256
from dataclasses import replace
from functools import wraps
import json

import rfc8785

from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.planning import ActivityId, StartNode, ReconcileNode
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_operations._configuration_acceptance import _PreparedAdvancementReceipt, _require_prepared_advancement, _history_records
from control_plane_kit_operations._configuration_preparation import _configuration_accounting, _ACCOUNTING, _execution_context
from control_plane_kit_operations.configuration_preparation import (
    ConfigurationAcceptedBinding, ConfigurationCurrentEvidence, _ref as _validate_ref,
)
from control_plane_kit_operations.revision_history import historical_advancement
from control_plane_kit_operations.records import OperationsRecordError
from .configuration_evidence import _EvidenceRead, _Unavailable, _Capacity
from .activity_history import PostgresActivityHistoryStore, _action_record
from .execution import PostgresExecutionStore, _activity_event
from .graph_store import (
    PostgresGraphTopologyStore, PostgresRealizedGraphProjectionStore, PostgresWorkspaceStore,
    _read_workspace_initialization,
)
from .configuration_preparation_store import _SELECT as _REF_SELECT, _decode_ref, _decode
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

    def _require_issued(self, prepared):
        if (self._issued is not prepared or prepared.stores.configuration_acceptance is not self
                or prepared.stores.connection is not self._connection):
            raise OperationsRecordError("advancement requires owner-issued preparation")

    def _bind_records(self, prepared, event, action):
        self._require_issued(prepared)
        if prepared.event is not None or prepared.action is not None:
            raise OperationsRecordError("advancement original records are already bound")
        prepared.stores.graphs._require_receiver_lifecycle(prepared.guard, prepared.workspace.workspace_id)
        prepared._validate_records(event, action)
        bound = replace(prepared, event=event, action=action)
        self._issued = bound
        return bound

    def _originals(self, read, action_id, event_id):
        action_names = _ACTION + _LOCATOR + ("advancement_run_id",)
        event_names = _EVENT + _LOCATOR
        actions = read.bounded_rows("cpk_operation_actions", _columns(action_names), "action_id=%s", (action_id,))
        events = read.bounded_rows("cpk_activity_events", _columns(event_names), "event_id=%s", (event_id,))
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
        if cache_key not in read.refs:
            rows = read.query(_REF_SELECT + " WHERE (r.run_id,r.activity_id,r.attempt,r.artifact_id)=(%s,%s,%s,%s)",
                key, records=1, octets=32768, cells=19, identities=2)
            if len(rows) != 1:
                raise _Unavailable
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
        original = self._ref(read, row[3:7])
        evidence = _decode(original, read)
        root = self._ref(read, row[7:11])
        birth = _decode(root, read)
        if (original[16] is not True or evidence.identity != evidence.birth_identity
                or root[16] is not True or birth.identity != birth.birth_identity
                or birth.ref != evidence.ref or birth.identity != evidence.birth_identity):
            raise _Unavailable
        source = evidence.source
        if source.identity.run_id.value != run.run_id:
            plan, request, run = self._original_use(read, row, source)
        activity = plan.plan.activity(ActivityId(source.identity.activity_id))
        # The leaf use always belongs to its own verified execution. Carry
        # needs its exact original acceptance point, never an intermediate
        # receipt chain. Nonbirth reuse remains a separate preparation slice.
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
        return rows

    @_closed_evidence
    def _receipt_context(self, workspace_id, revision, read):
        """Original pair/header/execution point proof, without prior manifests."""
        key = ("configuration-receipt-context", workspace_id, revision)
        if key in read.sources:
            return read.sources[key]
        rows = read.bounded_rows("cpk_configuration_acceptances", _columns(_HEADER),
            "workspace_id=%s AND pinned_revision=%s", (workspace_id, revision))
        if len(rows) != 1:
            raise _Unavailable
        header = dict(zip(_HEADER, rows[0]))
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
    def _prepare(self, stores, workspace, request, run, plan, guard, current_projection, desired_projection):
        from control_plane_kit_operations.advancement import _require_complete_success
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
        for row in slots:
            self._prove_use(read, row, plan, request, run)
        from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint
        proof = ConfigurationEvidenceFootprint(*(getattr(read.used, field) - getattr(proof_start, field)
            for field in ("records", "value_octets", "scalar_markers", "statements")))
        prepared = _PreparedAdvancementReceipt(stores, guard, workspace, request, run, plan,
            current_projection, desired_projection, slots=tuple(slots), evidence_read=read, proof_footprint=proof)
        self._issued = prepared
        return prepared

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
        # Verify actual stored bytes as well as the pre-CAS consumer preflight.
        header, action, event = self._receipt(workspace_id, prepared.plan.desired_graph_revision, read=prepared.evidence_read)
        if tuple(header[name] for name in _HEADER) != values or action != prepared.action or event != prepared.event:
            raise _Unavailable

    def _preflight(self, prepared):
        """Admit the generated pair and publication envelope before first write."""
        from control_plane_kit_operations.configuration_preparation import (
            ConfigurationEvidenceFootprint, ConfigurationCapacityDecision, configuration_evidence_capacity,
        )
        read = _EvidenceRead(self._connection)
        action, event = prepared.action, prepared.event
        texts = (action.action_id, action.session_id, action.actor_id, action.idempotency_key,
            action.intent_fingerprint, event.event_id, event.run_id, prepared.workspace.workspace_id,
            prepared.request.identity.request_id, prepared.plan.plan_id, prepared.plan.desired_graph_id,
            prepared.desired_projection.projection_id, prepared.desired_projection.projection_digest)
        if any(type(value) is not str or not 1 <= len(value.encode("utf-8")) <= 2048 for value in texts):
            raise _Capacity
        payload = {"activity_id": event.activity_id, "evidence": event.evidence.descriptor(),
            "failure": None, "recovery": None}
        # Consumers measure PostgreSQL JSON text, which is not JCS size.
        sizes = read.query("SELECT octet_length((%s::jsonb)::text),octet_length((%s::jsonb)::text)",
            (json.dumps(action.payload), json.dumps(payload)), records=1, octets=24, cells=2)
        if any(type(size) is not int or not 1 <= size <= 65536 for size in sizes[0]):
            raise _Capacity
        # Measure the exact retained owner/projection portion of a future cold
        # receipt read under this same ledger before any publication. These
        # immutable/own-locked rows must still equal the prepared owner truth.
        consumer_start = read.used
        execution, history = PostgresExecutionStore(self._connection), PostgresActivityHistoryStore(self._connection)
        if (execution.get_run(prepared.run.run_id) != prepared.run
                or execution.get_request(prepared.request.identity.request_id) != prepared.request
                or history.get_plan(prepared.plan.plan_id) != prepared.plan):
            raise _Unavailable
        history.get_session(prepared.plan.session_id)
        self._projection(prepared.plan.desired_graph_id, prepared.desired_projection.projection_id,
            prepared.workspace.workspace_id)
        owner_snapshot = ConfigurationEvidenceFootprint(*(getattr(read.used, field) - getattr(consumer_start, field)
            for field in ("records", "value_octets", "scalar_markers", "statements")))
        count = len(prepared.slots)
        slot_octets = sum(sum(len(str(value).encode("utf-8")) for value in slot) for slot in prepared.slots)
        slot_snapshot = ConfigurationEvidenceFootprint(2 * count + 2,
            slot_octets + 145 * count + 4096, 25 * count + 26, 2)
        # Cold consumers fetch each complete source ref + reciprocal claim once
        # for full material coverage before requested provenance. Use actual
        # cached row bytes; spelling bool/None is conservatively larger than SQL.
        ref_octets = sum(sum(len(value) if type(value) is bytes else len(str(value).encode("utf-8"))
            for value in self._ref(prepared.evidence_read, slot[3:7])) for slot in prepared.slots)
        ref_snapshot = ConfigurationEvidenceFootprint(2 * count, ref_octets, 19 * count, count)
        # 512KiB bounds generated header + both originals (two <=64KiB JSON
        # payloads, bounded text/scalars, size passes, statement/row overhead).
        pair_snapshot = ConfigurationEvidenceFootprint(16, 512 * 1024, 256, 16)
        snapshot = owner_snapshot.plus(slot_snapshot).plus(ref_snapshot).plus(pair_snapshot)
        if snapshot.accounted_bytes > 3 * 1024 * 1024:
            raise _Capacity
        # A fresh consumer has no warm source cache. Prove its complete source
        # work also fits, with discovery/initialization allowance, before CAS.
        consumer = snapshot.plus(prepared.proof_footprint).plus(
            ConfigurationEvidenceFootprint(64, 512 * 1024, 1024, 32))
        if configuration_evidence_capacity(consumer) is not ConfigurationCapacityDecision.WITHIN_LIMITS:
            raise _Capacity
        # The fixed owner-publication portion retains the reviewed <=1MiB graph
        # descriptor/metadata/projection/plan caps and bounded own rows. Reserve
        # its 8MiB tail plus nonempty slot work below. Actual queries continue
        # charging the same ledger, including every INSERT RETURNING row.
        # All immutable ref/source/outcome proofs were validated before this
        # point and are cached by exact PK in prepared.evidence_read. Readback
        # still transports the actual header, complete slots and original pair.
        # Add every slot INSERT return, both manifest passes (lengths + values),
        # and the bounded sentinel to the previously reviewed zero-slot tail.
        future = ConfigurationEvidenceFootprint(512 + 3 * count + 2,
            8 * 1024 * 1024 + slot_octets + 145 * count + 4096,
            8192 + 26 * count + 26, 130 + count)
        if configuration_evidence_capacity(read.used.plus(future)) is not ConfigurationCapacityDecision.WITHIN_LIMITS:
            raise _Capacity

    def _verify_replay(self, result):
        header, action, event = self._receipt(result.workspace_id, result.desired_graph_revision)
        if action != result.action or event != result.event:
            raise _Unavailable


def validate_configuration_advancement_rows(connection):
    """Bounded original-side scans; no backfill, adoption or repair."""
    store = ConfigurationAcceptanceStore(connection)
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
