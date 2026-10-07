"""Real receiver-bearing read rehearsal with independent wire/peak telemetry."""
from contextlib import contextmanager
import re

import psycopg

from control_plane_kit_operations._configuration_preparation import _ACCOUNTING, _configuration_accounting
from control_plane_kit_operations.configuration_cleanup import (
    ConfigurationCleanupExpectedContext, ConfigurationCleanupSourceSelector,
)
from control_plane_kit_operations.configuration_cleanup_planning import InspectConfigurationCleanup
from control_plane_kit_operations.effect_attempt_start_interpreter import _require_fresh_effect_receiver_permission
from control_plane_kit_operations.effect_run_prefix import _lock_effect_run_prefix
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_cleanup_read_ceilings import _CleanupOriginalReadCeilingsOwner
from control_plane_kit_operations.postgres.configuration_cleanup_store import _inspect
from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead, _joined_read
from tests.configuration_cleanup_read_ceilings_fixture import ConfigurationCleanupReadCeilingsFixture
from tests.test_postgres_configuration_evidence import _ObservedConnection, _ObservedRows


def _components(value):
    return (value.records, value.value_octets, value.scalar_markers, value.statements)


class _PhaseRows(_ObservedRows):
    def __init__(self, cursor, observations, query, entry):
        super().__init__(cursor, observations, query)
        self.entry = entry

    def execute(self, *args, **kwargs):
        raise AssertionError("returned cursor re-execution would bypass phase telemetry")

    def _record(self, row):
        if row is not None:
            result = self.cursor.pgresult
            widths = []
            for column, value in enumerate(row):
                if result.ftype(column) == 17 and value is not None:
                    widths.append(len(value))
                else:
                    raw = result.get_value(self.position, column)
                    widths.append(0 if raw is None else len(raw))
            self.entry["widths"].append(tuple(widths))
        result = super()._record(row)
        callback = self.observations.get("after_row")
        if row is not None and callback is not None:
            callback(self.entry, row)
        return result


class _PhaseConnection(_ObservedConnection):
    def execute(self, *args, **kwargs):
        accounting = _ACCOUNTING.get()
        if accounting is not self.observations["accounting"]:
            raise AssertionError("reader replaced the caller accounting object")
        query = str(args[0] if args else kwargs["query"])
        params = args[1] if len(args) > 1 else kwargs.get("params", ())
        limit = params[-1] if params and type(params[-1]) is int and "LIMIT %s" in query else None
        entry = dict(sql=query, peak=_components(accounting.used), widths=[], limit=limit,
            role=self.observations["role_label"](query, tuple(params or ())))
        self.observations["queries"].append(entry)
        self.observations["bytes"] += 256
        self.observations["statements"] += 1
        return _PhaseRows(self.connection.execute(*args, **kwargs), self.observations, query, entry)

    def cursor(self, *args, **kwargs):
        raise AssertionError("rehearsal must not hide an unobserved cursor execution")


@contextmanager
def ordinary_start_feasibility(case, label):
    """#1950 test-only observations; no bound issuance or production correction.

    Existing capture queries and the extra permission pass really consume the
    start ledger. Neither a rollback nor this observer refunds that consumption.
    """
    from hashlib import sha256
    import json
    from unittest import mock
    from control_plane_kit_core.runtime_effects import RuntimeEffectKind
    from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
    from control_plane_kit_operations.postgres.configuration_preparation_store import ConfigurationPreparationStore
    from control_plane_kit_operations.postgres.activity_history import PostgresActivityHistoryStore
    from control_plane_kit_operations.postgres import configuration_cleanup_phase_read_bounds as bounds

    point_roles = frozenset(("plan", "graph", "projection", "raw-graph", "raw-projection",
        "introduction", "origin-action", "request", "session"))
    collection_roles = frozenset(("scopes", "bindings"))
    original_execute = EffectAttemptStartService._execute_once
    original_prepare = ConfigurationPreparationStore._prepare
    original_columns, original_rows = bounds._phase_columns, bounds._phase_rows
    original_query = _EvidenceRead.query
    original_approval = PostgresActivityHistoryStore.get_approval_request
    reports, active = [], {}

    def approval(store, request_id):
        result = original_approval(store, request_id)
        if active:
            active["approval_kinds"].add(result.subject.kind.value)
        return result

    def columns(connection, role, identity, declared):
        if active.get("stage") == "existing" and role in point_roles:
            active["points"].add((role, identity))
        return original_columns(connection, role, identity, declared)

    def rows(read, role, identity, **kwargs):
        if active.get("stage") == "existing" and role in collection_roles:
            active["collections"].add((role, identity))
        return original_rows(read, role, identity, **kwargs)

    def query(reader, sql, params, **kwargs):
        entry = None
        if active and reader.accounting is active["accounting"]:
            matched = re.search(r"(?:FROM|INTO) (cpk_[a-z_]+)", sql)
            entry = dict(stage=active["stage"], role=matched.group(1) if matched else "scalar-or-control",
                kind="length" if sql.startswith("SELECT octet_length(") else
                    "write" if sql.startswith("INSERT") else "read",
                before=_components(reader.used),
                reserve=(kwargs["records"] * kwargs.get("identities", 1), kwargs["octets"],
                    kwargs["records"] * kwargs["cells"], 1))
            active["ledger_queries"].append(entry)
        try:
            return original_query(reader, sql, params, **kwargs)
        finally:
            if entry is not None:
                entry["after"] = _components(reader.used)

    def prepare(store, stores, command, request, run, plan, guard, event_kind):
        if not active:
            return original_prepare(store, stores, command, request, run, plan, guard, event_kind)
        active["before_preparation"] = _components(active["accounting"].used)
        active["offsets"]["before_preparation"] = len(active["observed"]["queries"])
        prepared = original_prepare(store, stores, command, request, run, plan, guard, event_kind)
        active["after_preparation"] = _components(active["accounting"].used)
        active["offsets"]["after_preparation"] = len(active["observed"]["queries"])
        active["ref_count"] = len(prepared.intent.configuration_instances.instances)
        active["stage"] = "capture"
        read = _EvidenceRead(stores.connection)
        for role, identity in sorted(active["points"]):
            bound = bounds._capture_point(read, role, identity)
            active["bounds"].append(dict(role=role, rows=1, widths=bound.widths))
        for role, identity in sorted(active["collections"]):
            table, declared, where, order, maximum, keys, identities = bounds._shape(role)
            if role == "bindings":
                # Existing semantic owner derives the complete expected set.
                expected = stores.graphs.receiver_bindings(*identity)
                expected = tuple((value.node_id, value.provider_socket_name) for value in expected)
            else:
                actual = read.bounded_rows(table, declared, where, identity,
                    maximum=maximum, point=False, order=order, identities=identities)
                expected = tuple(tuple(None if row[i] is None else str(row[i]) for i in keys) for row in actual)
            bound = bounds._capture_collection(read, role, identity, expected)
            active["bounds"].append(dict(role=role, rows=len(bound.keys), widths=bound.widths,
                key_digest=sha256(json.dumps(bound.keys, separators=(",", ":")).encode()).hexdigest()))
        active["after_capture"] = _components(read.used)
        active["offsets"]["after_capture"] = len(active["observed"]["queries"])
        active["stage"] = "revalidation"
        _require_fresh_effect_receiver_permission(stores, request, guard, command.intent, compensation=False)
        active["after_revalidation"] = _components(read.used)
        active["offsets"]["suffix"] = len(active["observed"]["queries"])
        active["stage"] = "suffix"
        return prepared

    def execute(service, command, health):
        if command.intent.kind is not RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1:
            return original_execute(service, command, health)
        case.assertFalse(active, "nested diagnostic start")
        accounting = _ACCOUNTING.get()
        active.update(stage="existing", accounting=accounting, points=set(), collections=set(),
            bounds=[], ledger_queries=[], approval_kinds=set(), offsets={"entrance": 0})
        observed = dict(bytes=0, rows=0, largest_cell=0, statements=0, queries=[],
            accounting=accounting, role_label=lambda sql, params: active["stage"])
        active["observed"] = observed
        factory = service._unit_of_work_factory
        service._unit_of_work_factory = lambda: PostgresUnitOfWork(
            lambda: _PhaseConnection(psycopg.connect(case.database_url), observed))
        outcome = "refused"
        try:
            result = original_execute(service, command, health)
            outcome = type(result).__name__
            return result
        finally:
            service._unit_of_work_factory = factory
            report = {key: active[key] for key in ("before_preparation", "after_preparation",
                "after_capture", "after_revalidation", "ref_count") if key in active}
            physical_stages = {}
            for stage in ("existing", "capture", "revalidation", "suffix"):
                entries = [entry for entry in observed["queries"] if entry["role"] == stage]
                widths = [row for entry in entries for row in entry["widths"]]
                raw = {table: sum(bool(re.search(r"^\s*INSERT INTO " + table + r"\b", entry["sql"]))
                    for entry in entries) for table in ("cpk_activity_events", "cpk_effect_attempt_intents",
                        "cpk_effect_attempts", "cpk_effect_configuration_refs", "cpk_configuration_claims")}
                physical_stages[stage] = dict(rows=len(widths), value_octets=sum(map(sum, widths)),
                    scalar_markers=sum(map(len, widths)), statements=len(entries), insert_roles=raw)
            report.update(label=label, outcome=outcome, final=_components(accounting.used),
                bounds=active["bounds"], ledger_queries=active["ledger_queries"],
                physical_stages=physical_stages, query_offsets=active["offsets"],
                approval_kinds=sorted(active["approval_kinds"]),
                physical=dict(rows=observed["rows"], accounted_bytes=observed["bytes"],
                    statements=observed["statements"], publication_action_widths=[entry["widths"]
                        for entry in observed["queries"] if "payload->>'desired_realized_projection_id'" in entry["sql"]]))
            reports.append(report)
            print("#1950 feasibility " + json.dumps(report, sort_keys=True))
            active.clear()

    with mock.patch.object(EffectAttemptStartService, "_execute_once", execute), \
            mock.patch.object(ConfigurationPreparationStore, "_prepare", prepare), \
            mock.patch.object(bounds, "_phase_columns", columns), \
            mock.patch.object(bounds, "_phase_rows", rows), \
            mock.patch.object(_EvidenceRead, "query", query), \
            mock.patch.object(PostgresActivityHistoryStore, "get_approval_request", approval):
        yield reports


class ConfigurationCleanupPhaseReadBoundsFixture(ConfigurationCleanupReadCeilingsFixture):
    def collection_role(self, query, params):
        # Match fixture identities locally; retain/print only the bounded label.
        acceptance = self.companion_acceptance
        accepted_request = acceptance.action.payload["execution_request_id"]
        identity = self.source_identity
        roles = (
            ("cleanup-scopes", "cpk_execution_receiver_scopes", (self.intent.source.request_id,)),
            ("acceptance-scopes", "cpk_execution_receiver_scopes", (accepted_request,)),
            ("acceptance-runs", "cpk_activity_runs", (accepted_request,)),
            ("acceptance-events", "cpk_activity_events", (acceptance.run_id,)),
            ("advancement-actions", "cpk_operation_actions", (acceptance.action.session_id, acceptance.run_id)),
            ("bindings", "cpk_graph_receiver_bindings", ("workspace-a", self.plan.base_graph_id,
                self.plan.base_realized_projection_id)),
            ("allocation-refs", "cpk_effect_configuration_refs", ("workspace-a", self.selected_ref.allocation_id)),
            ("allocation-claims", "cpk_configuration_claims", ("workspace-a", self.selected_ref.allocation_id)),
            ("invocation-refs", "cpk_effect_configuration_refs", (identity.run_id.value, identity.activity_id,
                identity.attempt)),
            ("current-slots", "cpk_configuration_accepted_slots", ("workspace-a", acceptance.desired_graph_revision)),
        )
        relation = re.search(r"\bFROM\s+([a-z_]+)", query, re.I)
        table = relation.group(1) if relation else None
        matches = [role for role, expected_table, key in roles
            if table == expected_table and params[:len(key)] == key]
        if len(matches) > 1:
            raise AssertionError("ambiguous fixed fixture role")
        return matches[0] if matches else None

    @contextmanager
    def phase_premise(self):
        with _configuration_accounting(self.identity.run_id.value) as accounting:
            observed = dict(bytes=0, rows=0, largest_cell=0, statements=0,
                queries=[], accounting=accounting, role_label=self.collection_role)
            factory = lambda: _PhaseConnection(psycopg.connect(self.database_url), observed)
            with PostgresUnitOfWork(factory) as uow, _joined_read(uow.stores.connection) as read:
                guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
                request = uow.stores.execution.get_request_for_update(self.intent.source.request_id)
                prefix = _lock_effect_run_prefix(uow, request, self.identity.run_id.value, latest_required=True)
                original_owner = _CleanupOriginalReadCeilingsOwner(uow)
                original = original_owner.capture(guard, prefix, self.plan,
                    intent_identity=self.identity, prospective_intent=self.intent)
                with original_owner.bind(original):
                    yield uow, guard, prefix, read, observed

    def inspect_command(self):
        plan = self.plan
        return InspectConfigurationCleanup("session-a", "workspace-a",
            ConfigurationCleanupExpectedContext(plan.base_graph_id, plan.base_realized_projection_id,
                plan.desired_graph_id, plan.desired_realized_projection_id, plan.desired_graph_revision),
            (ConfigurationCleanupSourceSelector(self.source_identity, "settings", self.selected_ref),))

    def read_chain(self, uow, guard, prefix, read, observed):
        before = read.used
        offset = len(observed["queries"])
        _require_fresh_effect_receiver_permission(uow.stores, prefix.request, guard, self.intent,
            compensation=False)
        permission_queries = observed["queries"][offset:]
        raw_graphs = [q for q in permission_queries if q["sql"].startswith("SELECT octet_length(")
            and " FROM cpk_graph_versions WHERE workspace_id=%s AND graph_id=%s" in q["sql"]]
        raw_projections = [q for q in permission_queries if q["sql"].startswith("SELECT octet_length(")
            and " FROM cpk_realized_graph_projections WHERE workspace_id=%s AND projection_id=%s" in q["sql"]]
        histories = [q for q in permission_queries if q["sql"].startswith("SELECT octet_length(")
            and " FROM cpk_activity_events WHERE run_id=%s ORDER BY ordinal" in q["sql"]]
        self.assertEqual((len(raw_graphs), len(raw_projections), len(histories)), (14, 14, 2))
        # Establish the cross-row prerequisite in ordinary source BEFORE any
        # missing phase-owner assertion, using observed rows and no extra SQL.
        event_fetches = [q for q in permission_queries if q["role"] == "acceptance-events"
            and q["sql"].startswith("SELECT CASE WHEN")]
        self.assertEqual(len(event_fetches), 2)
        for query in event_fetches:
            widths = [row[:-1] for row in query["widths"]]
            self.assertGreater(len(widths), 1)
            maxima = tuple(max(row[i] for row in widths) for i in range(6))
            self.assertGreater(sum(maxima), max(map(sum, widths)),
                "existing history must exercise maxima in different event rows")
        fresh = _EvidenceRead(uow.stores.connection)
        self.assertIs(fresh.accounting, read.accounting)
        inspected, proposal = _inspect(uow.stores, self.inspect_command(), fresh)
        self.assertEqual(inspected.state, "complete")
        self.assertEqual(proposal, self.plan.cleanup_proposal)
        self.assertEqual(uow.stores.configuration_completions._get(self.source_identity, fresh), self.completion)
        slot_fetches = [q for q in observed["queries"][offset:] if q["role"] == "current-slots"
            and q["sql"].startswith("SELECT CASE WHEN")]
        self.assertTrue(slot_fetches and slot_fetches[0]["widths"],
            "later manifest growth target requires a real nonempty accepted slot set")
        for after, prior in zip(_components(read.used), _components(before), strict=True):
            self.assertGreaterEqual(after, prior)
        self.assertLessEqual(read.used.records, 4096)
        self.assertLessEqual(read.used.accounted_bytes, 16 * 1024 * 1024)
        self.assertGreaterEqual(read.used.records, observed["rows"])
        self.assertGreaterEqual(read.used.accounted_bytes, observed["bytes"])
        self.assertEqual(read.used.statements, observed["statements"])
        for query in observed["queries"]:
            r, b, c, s = query["peak"]
            self.assertLessEqual(r, 4096)
            self.assertLessEqual(b + 128*r + 16*c + 256*s, 16 * 1024 * 1024)
        peaks = [b + 128*r + 16*c + 256*s for r, b, c, s in
            (query["peak"] for query in observed["queries"])]
        print("#1941 read rehearsal", dict(prefix=_components(before), used=_components(read.used),
            physical_weighted_bytes=observed["bytes"], maximum_reservation_bytes=max(peaks),
            raw_graph_pairs=len(raw_graphs), raw_projection_pairs=len(raw_projections),
            acceptance_event_reads=len(histories)))
