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
