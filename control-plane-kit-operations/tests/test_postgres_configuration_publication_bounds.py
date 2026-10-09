"""#1952 publication lifetime and whole receiver-finish transport laws.

Owning PostgreSQL commands over existing completion premises; no provider or
health evidence. Wire observation never replaces production query results.
"""
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
import json
import unittest
from unittest import mock

import psycopg
import rfc8785

from control_plane_kit_core.planning import NodeTarget, RemoveNodeResource
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_operations._configuration_preparation import _ACCOUNTING
from control_plane_kit_operations._configuration_preparation import _OrdinarySuffixBudget
from control_plane_kit_operations import advancement as advancement_module
from control_plane_kit_operations.advancement import (
    AdvanceCurrentGraph, CurrentGraphAdvancementCommandService, CurrentGraphAdvancementConflict,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_acceptance_store import ConfigurationAcceptanceStore
from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead, _Unavailable
from control_plane_kit_operations.postgres.configuration_evidence import _Capacity
from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint as Footprint
from control_plane_kit_operations.postgres.receiver_execution_scopes import (
    _Transport, _ExecutionScopeStorage, _REQUEST, _columns,
)
from control_plane_kit_operations.receiver_execution_scopes import (
    ExecutionReceiverScope, ReceiverScopeCapacity, ReceiverScopeUnavailable,
)
from control_plane_kit_operations.records import OperationsRecordError
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.receiver_canonical_acceptance_fixture import ReceiverCanonicalAcceptanceFixture
from tests import test_postgres_configuration_acceptance as zero_fixture
from tests import test_postgres_configuration_carry as carry_fixture
from tests.test_postgres_configuration_evidence import _ObservedConnection, _ObservedRows


def _difference(after, before):
    return Footprint(*(getattr(after, name) - getattr(before, name)
        for name in ("records", "value_octets", "scalar_markers", "statements")))


def _within(case, actual, expected):
    for name in ("records", "value_octets", "scalar_markers", "statements"):
        case.assertLessEqual(getattr(actual, name), getattr(expected, name), name)


class _ExactObservedRows(_ObservedRows):
    def _record(self, row):
        before = self.observations["bytes"]
        result = super()._record(row)
        if row is not None:
            # The existing observer measured pgresult/bytea octets, independent
            # of the production ledger. Split its physical row/cell overhead
            # from actual values so joined record weight cannot hide a leak.
            self.observations["value_octets"] += self.observations["bytes"] - before - 128 - 16 * len(row)
            self.observations["scalar_cells"] += len(row)
        return result


class _BudgetObservedConnection(_ObservedConnection):
    """Existing wire observer, selected only for the admitted command ledger."""
    def __init__(self, connection, trace, *, cold=False):
        super().__init__(connection, trace["wire"])
        self.trace, self.cold = trace, cold

    def execute(self, *args, **kwargs):
        ledger = _ACCOUNTING.get()
        if self.cold and ledger is not None and self.trace.get("accounting") is None:
            self.trace["accounting"] = ledger
        if ledger is not None and ledger is self.trace.get("accounting"):
            # Production query reservation is already outstanding at execute.
            self.trace["peaks"].append(ledger.used)
            observed = super().execute(*args, **kwargs)
            return _ExactObservedRows(observed.cursor, self.observations, observed.query)
        return self.connection.execute(*args, **kwargs)


def _trace():
    return dict(wire=dict(rows=0, bytes=0, largest_cell=0, statements=0,
        value_octets=0, scalar_cells=0), peaks=[])


@contextmanager
def _publication_budget_observation(case, database_url):
    trace = _trace()
    trace["phases"] = {}
    preflight, commit = ConfigurationAcceptanceStore._preflight, PostgresUnitOfWork.commit

    def admit(store, prepared):
        forecast = getattr(store, "_publication_budgets", None)
        case.assertTrue(callable(forecast), "publication lacks its source-derived settled/peak forecast")
        prior = _ACCOUNTING.get().used
        with mock.patch.object(_EvidenceRead, "query", side_effect=AssertionError("pure forecast issued SQL")):
            snapshot, future, publication = forecast(prepared)
        case.assertEqual(_ACCOUNTING.get().used, prior)
        preflight(store, prepared)
        case.assertEqual(_ACCOUNTING.get().used, prior, "binding work was hidden after admission")
        phase = "pre_id" if prepared.event is None else "bound"
        case.assertNotIn(phase, trace["phases"])
        trace["phases"][phase] = dict(snapshot=snapshot, future=future,
            publication=publication, prior=prior, wire=dict(trace["wire"]),
            peak_offset=len(trace["peaks"]))
        trace.update(snapshot=snapshot, future=future, publication=publication,
            prior=prior, accounting=_ACCOUNTING.get(), owner=store, prepared=prepared)

    def request_commit(uow):
        if _ACCOUNTING.get() is trace.get("accounting") and trace.get("accounting") is not None:
            case.assertFalse(uow._commit_requested)
            trace["final"] = trace["accounting"].used
        return commit(uow)

    factory = lambda: PostgresUnitOfWork(lambda: _BudgetObservedConnection(psycopg.connect(database_url), trace))
    with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", admit), \
            mock.patch.object(PostgresUnitOfWork, "commit", request_commit):
        yield trace, factory


def _assert_publication_and_cold_fit(case, trace, database_url):
    case.assertIn("final", trace)
    case.assertEqual(tuple(trace["phases"]), ("pre_id", "bound"))
    for phase, current in trace["phases"].items():
        with case.subTest(phase=phase):
            used = _difference(trace["final"], current["prior"])
            _within(case, used, current["publication"].settled)
            for peak in trace["peaks"][current["peak_offset"]:]:
                _within(case, _difference(peak, current["prior"]), current["publication"].peak)
            wire = {key: value - current["wire"][key] for key, value in trace["wire"].items()}
            case.assertEqual(used.statements, wire["statements"])
            case.assertEqual(used.value_octets, wire["value_octets"])
            case.assertEqual(used.scalar_markers, wire["scalar_cells"])
            # Ledger records additionally weight joined identities; physical rows do not.
            case.assertGreaterEqual(used.records, wire["rows"])
            case.assertGreaterEqual(used.accounted_bytes, wire["bytes"])
    cold = _trace()
    snapshots = []
    manifest = ConfigurationAcceptanceStore._receipt_manifest

    def measured_manifest(store, workspace, revision, read):
        before = read.used
        result = manifest(store, workspace, revision, read)
        snapshots.append(_difference(read.used, before))
        return result

    with mock.patch.object(ConfigurationAcceptanceStore, "_receipt_manifest", measured_manifest):
        with PostgresUnitOfWork(lambda: _BudgetObservedConnection(
                psycopg.connect(database_url), cold, cold=True)) as uow:
            observed = uow.stores.configuration_acceptance.read_current_configuration("workspace-a")
    case.assertEqual(observed.state, "complete")
    case.assertEqual(len(snapshots), 1)
    for current in trace["phases"].values():
        _within(case, snapshots[0], current["snapshot"])
    case.assertLessEqual(snapshots[0].accounted_bytes, 3 * 1024 * 1024)
    for current in trace["phases"].values():
        _within(case, cold["accounting"].used, current["future"].settled)
        for peak in cold["peaks"]:
            _within(case, peak, current["future"].peak)
    case.assertEqual(cold["accounting"].used.statements, cold["wire"]["statements"])
    case.assertEqual(cold["accounting"].used.value_octets, cold["wire"]["value_octets"])
    case.assertEqual(cold["accounting"].used.scalar_markers, cold["wire"]["scalar_cells"])
    case.assertGreaterEqual(cold["accounting"].used.records, cold["wire"]["rows"])
    case.assertGreaterEqual(cold["accounting"].used.accounted_bytes, cold["wire"]["bytes"])
    dimensions = ("records", "value_octets", "scalar_markers", "statements", "accounted_bytes")

    def footprint(value):
        return {name: getattr(value, name) for name in dimensions}

    def maxima(values):
        values = tuple(values)
        return {name: max((getattr(value, name) for value in values), default=0) for name in dimensions}

    print("publication-fit " + json.dumps(dict(prior_records=trace["prior"].records,
        prior_bytes=trace["prior"].accounted_bytes, suffix_records=used.records,
        suffix_bytes=used.accounted_bytes, suffix_statements=used.statements,
        cold_records=cold["accounting"].used.records, cold_bytes=cold["accounting"].used.accounted_bytes,
        snapshot_bytes=snapshots[0].accounted_bytes,
        forecast_snapshot=footprint(trace["snapshot"]),
        forecast_future_settled=footprint(trace["future"].settled),
        forecast_future_peak=footprint(trace["future"].peak),
        forecast_publication_settled=footprint(trace["publication"].settled),
        forecast_publication_peak=footprint(trace["publication"].peak),
        observed_publication_peak=maxima(_difference(value, trace["prior"])
            for value in trace["peaks"][trace["phases"]["bound"]["peak_offset"]:]),
        observed_future_peak=maxima(cold["peaks"]))))


class PostgresConfigurationPublicationReceiverTests(ReceiverCanonicalAcceptanceFixture, unittest.TestCase):
    def publication_command(self, claimed, suffix):
        # Prepare the command outside the wire-observed command connection.
        with self.unit_of_work() as uow:
            plan = uow.stores.activity_history.get_plan(claimed.request.identity.plan_id)
        return AdvanceCurrentGraph("workspace-a", claimed.run.run_id, plan.plan_id,
            plan.base_graph_id, plan.base_realized_projection_id,
            plan.desired_graph_id, plan.desired_realized_projection_id, plan.desired_graph_revision,
            ExecutionWorkerAuthority("worker-a", (PolicyScope.EXECUTION_OPERATE,)),
            ExecutionLeaseFence("worker-a", claimed.request.claim.generation), IdempotencyKey("advance-" + suffix))

    def observed_advance(self, claimed, suffix):
        command = self.publication_command(claimed, suffix)
        observed = dict(rows=0, bytes=0, largest_cell=0, statements=0)
        ledgers = []

        class ObservedCommand(_ObservedConnection):
            def execute(self, *args, **kwargs):
                ledgers.append(_ACCOUNTING.get())
                return super().execute(*args, **kwargs)

        ids = iter(("event-advance-" + suffix, "action-advance-" + suffix))
        result = CurrentGraphAdvancementCommandService(
            lambda: PostgresUnitOfWork(lambda: ObservedCommand(psycopg.connect(self.database_url), observed)),
            clock=self.now, id_factory=lambda: next(ids)).execute(command)
        self.assertFalse(result.replayed)
        self.assertTrue(ledgers)
        self.assertTrue(all(ledger is not None for ledger in ledgers))
        self.assertEqual(len({id(ledger) for ledger in ledgers}), 1)
        ledger = ledgers[0]
        self.assertGreaterEqual(ledger.used.records, observed["rows"])
        self.assertGreaterEqual(ledger.used.accounted_bytes, observed["bytes"])
        self.assertEqual(ledger.used.statements, observed["statements"],
            "receiver finish dispatched SQL outside the advancement ledger")
        return result

    def test_initial_acceptance_meters_every_receiver_finish_statement(self):
        self.desired_receiver("publication-initial", graph=self.canonical_receiver_graph)
        claimed, _, _ = self.retained_success("publication-initial")
        self.assertIsNone(self.receiver_origin().first_accepted_action_id)
        accepted = self.observed_advance(claimed, "publication-initial")
        self.assertEqual(self.receiver_origin().first_accepted_action_id, accepted.action.action_id)
        self.assertEqual(self.advance(claimed, "publication-initial"), replace(accepted, replayed=True))

    def test_teardown_acceptance_meters_every_receiver_finish_statement(self):
        self.desired_receiver("publication-before", graph=self.canonical_receiver_graph)
        claimed, _, _ = self.retained_success("publication-before")
        self.advance(claimed, "publication-before")
        original = self.receiver_origin()
        self.desired_receiver("publication-teardown", graph=DeploymentGraph("removed"))
        claimed, _, _ = self.retained_success("publication-teardown")
        retired = self.observed_advance(claimed, "publication-teardown")
        self.assertEqual(self.receiver_origin(), replace(original,
            retired_action_id=retired.action.action_id, retired_session_id=retired.action.session_id))

    def budgeted_advance(self, claimed, suffix):
        command = self.publication_command(claimed, suffix)
        ids = iter(("event-advance-" + suffix, "action-advance-" + suffix))
        with _publication_budget_observation(self, self.database_url) as (trace, factory):
            result = CurrentGraphAdvancementCommandService(factory,
                clock=self.now, id_factory=lambda: next(ids)).execute(command)
        _assert_publication_and_cold_fit(self, trace, self.database_url)
        return result

    def test_initial_actual_publication_and_native_cold_reader_fit(self):
        self.desired_receiver("fit-initial", graph=self.canonical_receiver_graph)
        claimed, _, _ = self.retained_success("fit-initial")
        accepted = self.budgeted_advance(claimed, "fit-initial")
        self.assertEqual(self.receiver_origin().first_accepted_action_id, accepted.action.action_id)

    def test_teardown_actual_publication_and_native_cold_reader_fit(self):
        self.desired_receiver("fit-before", graph=self.canonical_receiver_graph)
        claimed, _, _ = self.retained_success("fit-before")
        self.advance(claimed, "fit-before")
        original = self.receiver_origin()
        self.desired_receiver("fit-teardown", graph=DeploymentGraph("removed"))
        claimed, _, _ = self.retained_success("fit-teardown")
        retired = self.budgeted_advance(claimed, "fit-teardown")
        self.assertEqual(self.receiver_origin(), replace(original,
            retired_action_id=retired.action.action_id, retired_session_id=retired.action.session_id))

    def test_candidate_multiset_growth_refuses_even_with_same_request_set(self):
        self.desired_receiver("multiset", graph=self.canonical_receiver_graph)
        claimed, _, _ = self.retained_success("multiset")
        original, query = ConfigurationAcceptanceStore._preflight, _EvidenceRead.query
        checked, raw = [], []

        def observed_query(read, sql, params, **kwargs):
            rows = query(read, sql, params, **kwargs)
            if "FROM cpk_execution_receiver_scopes WHERE workspace_id=" in sql:
                raw.append(tuple(rows))
            return rows

        def preflight(store, prepared):
            reader = _ExecutionScopeStorage(store._connection, prepared.evidence_read)
            scopes = (ExecutionReceiverScope("docker", None),)
            with mock.patch.object(_EvidenceRead, "query", observed_query):
                self.assertEqual(reader.candidates("workspace-a", scopes), (prepared.request.identity.request_id,))
            self.assertTrue(any(len(rows) > len({row[0] for row in rows}) for rows in raw),
                "fixture lacks repeated raw candidate identities before deduplication")
            self.assertLessEqual(len("z".encode()),
                min(len(name.encode()) for name in self.canonical_receiver_graph.nodes))
            row = store._connection.execute("INSERT INTO cpk_execution_receiver_scopes "
                "(request_id,workspace_id,scope_ordinal,scope_kind,runtime_id,node_id) "
                "SELECT %s,%s,max(scope_ordinal)+1,'node','docker','z' "
                "FROM cpk_execution_receiver_scopes WHERE request_id=%s RETURNING scope_ordinal",
                (prepared.request.identity.request_id, "workspace-a", prepared.request.identity.request_id)).fetchone()
            self.assertIsNotNone(row)
            try:
                with self.assertRaises((_Unavailable, _Capacity, ReceiverScopeUnavailable, ReceiverScopeCapacity)):
                    reader.candidates("workspace-a", scopes)
            finally:
                store._connection.execute("DELETE FROM cpk_execution_receiver_scopes WHERE request_id=%s "
                    "AND scope_ordinal=%s", (prepared.request.identity.request_id, row[0]))
            checked.append("pre_id" if prepared.event is None else "bound")
            return original(store, prepared)

        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", preflight):
            accepted = self.advance(claimed, "multiset")
        self.assertEqual(checked, ["pre_id", "bound"])
        self.assertEqual(self.receiver_origin().first_accepted_action_id, accepted.action.action_id)

    def test_second_history_failure_rolls_back_actual_witness_within_peak(self):
        self.desired_receiver("late-history", graph=self.canonical_receiver_graph)
        claimed, _, _ = self.retained_success("late-history")
        command = self.publication_command(claimed, "late-history")
        before, origin = self.acceptance_truth(), self.receiver_origin()
        def receipts():
            return tuple(self.connection.execute("SELECT * FROM " + table
                + " ORDER BY workspace_id,pinned_revision").fetchall()
                for table in ("cpk_configuration_acceptances", "cpk_configuration_accepted_slots"))
        receipts_before = receipts()
        trace = _trace()
        trace.update(finishing=False, histories=0, late=False, phases={})
        failed, inspected, after_failure = [], [], []
        preflight = ConfigurationAcceptanceStore._preflight
        finish, history = advancement_module._finish_receiver_advancement, _ExecutionScopeStorage.evidence

        def admitted(store, prepared):
            # Keep the baseline late-failure path executable even before the
            # forecast exists. Missing forecast is still an unconditional
            # assertion below, never fabricated forecast/permission evidence.
            forecast = getattr(store, "_publication_budgets", None)
            publication = forecast(prepared)[2] if callable(forecast) else None
            result = preflight(store, prepared)
            phase = "pre_id" if prepared.event is None else "bound"
            self.assertNotIn(phase, trace["phases"])
            trace["phases"][phase] = dict(publication=publication,
                prior=_ACCOUNTING.get().used, peak_offset=len(trace["peaks"]))
            trace.update(publication=publication, accounting=_ACCOUNTING.get(),
                prior=_ACCOUNTING.get().used, owner=store, prepared=prepared)
            return result

        def finishing(*args, **kwargs):
            trace["finishing"] = True
            try:
                return finish(*args, **kwargs)
            finally:
                trace["finishing"] = False

        def history_read(reader, *args, **kwargs):
            if trace["finishing"]:
                trace["histories"] += 1
                if trace["histories"] == 2:
                    # Independent, unmetered test premise inspection on the
                    # SAME transaction, deliberately outside production/wire
                    # telemetry. It neither grants permission nor changes truth.
                    raw = reader.connection.connection
                    self.assertEqual(raw.execute("SELECT first_accepted_action_id,first_accepted_session_id "
                        "FROM cpk_graph_receiver_introductions WHERE workspace_id='workspace-a' AND receiver_id=%s",
                        ("a" * 32,)).fetchone(), ("action-advance-late-history", claimed.request.identity.session_id))
                    self.assertEqual(raw.execute("SELECT action_id,event_id FROM cpk_configuration_acceptances "
                        "WHERE workspace_id='workspace-a' AND pinned_revision=%s",
                        (command.expected_desired_graph_revision,)).fetchone(),
                        ("action-advance-late-history", "event-advance-late-history"))
                    self.assertEqual(raw.execute("SELECT current_graph_id,current_realized_projection_id "
                        "FROM cpk_workspaces WHERE workspace_id='workspace-a'").fetchone(),
                        (command.desired_graph_id, command.desired_realized_projection_id))
                    inspected.append(True)
                    trace["late"] = True
            return history(reader, *args, **kwargs)

        class LateFetchFailure(_BudgetObservedConnection):
            def execute(self, sql, *args, **kwargs):
                if failed:
                    after_failure.append(str(sql))
                cursor = super().execute(sql, *args, **kwargs)
                if (trace["late"] and not failed and "FROM cpk_activity_events" in str(sql)
                        and "CASE WHEN" in str(sql)):
                    failed.append(_ACCOUNTING.get().used)
                    # Driver/fetch fault after real execute; PostgreSQL has
                    # not aborted the transaction, so the actual close can run.
                    raise psycopg.DataError("injected second-history fetch failure")
                return cursor

        ids = iter(("event-advance-late-history", "action-advance-late-history"))
        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", admitted), \
                mock.patch.object(advancement_module, "_finish_receiver_advancement", finishing), \
                mock.patch.object(_ExecutionScopeStorage, "evidence", history_read), \
                self.assertRaises((psycopg.DataError, CurrentGraphAdvancementConflict)):
            CurrentGraphAdvancementCommandService(lambda: PostgresUnitOfWork(lambda:
                LateFetchFailure(psycopg.connect(self.database_url), trace)),
                clock=self.now, id_factory=lambda: next(ids)).execute(command)
        self.assertEqual(inspected, [True])
        self.assertEqual(trace["histories"], 2)
        self.assertEqual(len(failed), 1)
        self.assertEqual(self.receiver_origin(), origin)
        self.assertEqual(self.acceptance_truth(), before)
        self.assertEqual(receipts(), receipts_before)
        self.assertEqual(after_failure, ["SELECT txid_current()"])
        self.assertIsNotNone(trace["publication"], "late publication lacks its source-derived failure peak")
        self.assertEqual(tuple(trace["phases"]), ("pre_id", "bound"))
        for current in trace["phases"].values():
            for peak in trace["peaks"][current["peak_offset"]:]:
                _within(self, _difference(peak, current["prior"]), current["publication"].peak)
            _within(self, _difference(trace["accounting"].used, current["prior"]), current["publication"].peak)
        retained = _difference(trace["accounting"].used, failed[0])
        self.assertEqual((retained.records, retained.scalar_markers, retained.statements), (1, 1, 1))
        self.assertGreater(retained.value_octets, 0)
        with self.assertRaises((OperationsRecordError, _Unavailable)):
            trace["owner"]._require_issued(trace["prepared"])

    def test_absent_compensation_is_supported_but_appearance_is_bounded(self):
        self.desired_receiver("optional", graph=self.canonical_receiver_graph)
        claimed, _, _ = self.retained_success("optional")
        command = self.publication_command(claimed, "optional")
        action_id = self.connection.execute("SELECT action_id FROM cpk_operation_actions "
            "WHERE session_id=%s ORDER BY ordinal DESC LIMIT 1", (claimed.request.identity.session_id,)).fetchone()[0]
        event_id = self.connection.execute("SELECT event_id FROM cpk_activity_events "
            "WHERE run_id=%s ORDER BY ordinal DESC LIMIT 1", (claimed.run.run_id,)).fetchone()[0]
        original, checked = ConfigurationAcceptanceStore._preflight, []
        observed = dict(rows=0, bytes=0, largest_cell=0, statements=0)

        class CompensationObservation(_ObservedConnection):
            def execute(self, sql, *args, **kwargs):
                if "FROM cpk_failed_run_compensations" in str(sql):
                    return super().execute(sql, *args, **kwargs)
                return self.connection.execute(sql, *args, **kwargs)

        def preflight(store, prepared):
            reader = _ExecutionScopeStorage(store._connection, prepared.evidence_read)
            args = (prepared.request, (prepared.plan, prepared.current_projection, prepared.desired_projection),
                prepared.run, (), (), (), ())
            self.assertEqual(reader.compensations(*args), ((), ()))
            # Schema-valid hostile appearance, not lawful compensation evidence.
            # Invalid semantic preimage deliberately distinguishes early bounded
            # absence rejection from eventual decoder rejection after transport.
            store._connection.execute("INSERT INTO cpk_failed_run_compensations "
                "(program_id,workspace_id,request_id,run_id,plan_id,session_id,action_id,event_id,"
                "actor_id,reason,source_failure,authority_reference_fingerprint,command_fingerprint,"
                "evidence_fingerprint,program_fingerprint,program_preimage,created_at) "
                "VALUES ('late-program',%s,%s,%s,%s,%s,%s,%s,'operator-a','post-effect-failure',"
                "'{}'::jsonb,%s,%s,%s,%s,%s,now())",
                (prepared.workspace.workspace_id, prepared.request.identity.request_id, prepared.run.run_id,
                    prepared.plan.plan_id, prepared.plan.session_id, action_id, event_id,
                    *("0" * 64 for _ in range(4)), b"x" * 65000))
            try:
                with self.assertRaises((OperationsRecordError, _Unavailable, _Capacity,
                        ReceiverScopeUnavailable, ReceiverScopeCapacity)):
                    reader.compensations(*args)
            finally:
                store._connection.execute("DELETE FROM cpk_failed_run_compensations WHERE program_id='late-program'")
            self.assertLess(observed["largest_cell"], 65000,
                "an originally absent compensation transported its new preimage")
            checked.append("pre_id" if prepared.event is None else "bound")
            return original(store, prepared)

        ids = iter(("event-advance-optional", "action-advance-optional"))
        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", preflight):
            accepted = CurrentGraphAdvancementCommandService(lambda: PostgresUnitOfWork(
                lambda: CompensationObservation(psycopg.connect(self.database_url), observed)),
                clock=self.now, id_factory=lambda: next(ids)).execute(command)
        self.assertEqual(checked, ["pre_id", "bound"])
        self.assertEqual(self.receiver_origin().first_accepted_action_id, accepted.action.action_id)


class PostgresConfigurationPublicationLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.base = zero_fixture.PostgresConfigurationAcceptanceTests()
        self.addCleanup(self.cleanup_fixture)
        self.base.setUp()

    def cleanup_fixture(self):
        self.assertTrue(self.base.doCleanups(), "nested publication fixture cleanup failed")

    def test_prepared_credential_is_spent_before_database_commit(self):
        prepared_values, requested, committed = [], [], []
        original = ConfigurationAcceptanceStore._preflight
        original_commit = PostgresUnitOfWork.commit
        case = self

        def preflight(store, prepared):
            result = original(store, prepared)
            prepared_values.append((store, prepared, _ACCOUNTING.get()))
            return result

        def request_commit(uow):
            self.assertEqual(len(prepared_values), 2)
            self.assertIsNone(prepared_values[0][1].event)
            self.assertIsNotNone(prepared_values[1][1].event)
            self.assertFalse(uow._commit_requested)
            for store, prepared, accounting in prepared_values:
                self.assertIs(_ACCOUNTING.get(), accounting)
                self.assertTrue(accounting.active)
                with self.assertRaises((OperationsRecordError, _Unavailable)):
                    store._require_issued(prepared)
            requested.append(True)
            return original_commit(uow)

        class ObservedCommit:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def commit(self):
                case.assertEqual(len(prepared_values), 2)
                for store, prepared, _ in prepared_values:
                    with case.assertRaises((OperationsRecordError, _Unavailable)):
                        store._require_issued(prepared)
                committed.append(True)
                self.connection.commit()

        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", preflight), \
                mock.patch.object(PostgresUnitOfWork, "commit", request_commit):
            accepted = self.base.advance(lambda: PostgresUnitOfWork(
                lambda: ObservedCommit(psycopg.connect(self.base.database_url))))
        self.assertFalse(accepted.replayed)
        self.assertEqual(requested, [True])
        self.assertEqual(committed, [True])
        for store, prepared, _ in prepared_values:
            with self.assertRaises((OperationsRecordError, _Unavailable)):
                store._require_issued(prepared)

    def test_pending_commit_invalidates_prepared_and_rolls_back(self):
        before, active, checked = self.base.retained_snapshot(), [], []

        def factory():
            uow = self.base.unit_of_work()
            active.append(uow)
            return uow

        def preflight(store, prepared):
            self.assertEqual(len(active), 1)
            self.assertFalse(active[0]._commit_requested)
            active[0].commit()
            self.assertTrue(active[0]._commit_requested)
            with self.assertRaises((OperationsRecordError, _Unavailable)):
                store._require_issued(prepared)
            checked.append(True)
            raise _Unavailable

        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", preflight), \
                self.assertRaises(CurrentGraphAdvancementConflict):
            self.base.advance(factory)
        self.assertEqual(checked, [True])
        self.assertEqual(self.base.retained_snapshot(), before)

    def test_swallowed_database_abort_cannot_publish_or_retain_credential(self):
        before, prepared_values, injected = self.base.retained_snapshot(), [], []
        finish, preflight = advancement_module._finish_receiver_advancement, ConfigurationAcceptanceStore._preflight

        def admitted(store, prepared):
            result = preflight(store, prepared)
            prepared_values.append((store, prepared))
            return result

        def finishing(stores, *args):
            result = finish(stores, *args)
            # Real command has inserted its header and changed the pointer.
            # This deliberately swallowed server error leaves INERROR, unlike
            # the separate usable driver/fetch failure witness.
            self.assertEqual(stores.connection.execute("SELECT action_id FROM cpk_configuration_acceptances "
                "WHERE workspace_id='workspace-a'").fetchall(), [("action-advance",)])
            self.assertEqual(stores.connection.execute("SELECT current_graph_id FROM cpk_workspaces "
                "WHERE workspace_id='workspace-a'").fetchone(), ("graph-desired",))
            try:
                stores.connection.execute("SELECT 1 / 0")
            except psycopg.errors.DivisionByZero:
                injected.append(True)
            return result

        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", admitted), \
                mock.patch.object(advancement_module, "_finish_receiver_advancement", finishing), \
                self.assertRaises(CurrentGraphAdvancementConflict):
            self.base.advance()
        self.assertEqual(injected, [True])
        self.assertEqual(self.base.retained_snapshot(), before)
        self.assertEqual(len(prepared_values), 2)
        self.assertIsNone(prepared_values[0][1].event)
        self.assertIsNotNone(prepared_values[1][1].event)
        for store, prepared in prepared_values:
            with mock.patch.object(_EvidenceRead, "query", side_effect=AssertionError("spent owner queried SQL")), \
                    self.assertRaises((OperationsRecordError, _Unavailable)):
                store._require_issued(prepared)

    def test_current_schema_and_plan_owner_refuse_null_original_projection_pins(self):
        with self.base.unit_of_work() as uow:
            plan = uow.stores.activity_history.get_plan("plan-a")
            for field in ("base_realized_projection_id", "desired_realized_projection_id"):
                with self.subTest(owner_field=field), self.assertRaises(OperationsRecordError):
                    uow.stores.activity_history.add_plan(replace(plan,
                        plan_id="unreachable-null-" + field, **{field: None}))
        for field in ("base_realized_projection_id", "desired_realized_projection_id"):
            with self.subTest(sql_field=field), self.assertRaises(psycopg.errors.NotNullViolation):
                # Fixed test-owned column alternatives; no schema relaxation.
                self.base.connection.execute("UPDATE cpk_activity_plans SET " + field
                    + "=NULL WHERE plan_id='plan-a'")
        with self.base.unit_of_work() as uow:
            self.assertEqual(uow.stores.activity_history.get_plan("plan-a"), plan)
        self.assertFalse(self.base.advance().replayed)

    def reject_unclosed_read(self, *, phase, foreign_ledger=False):
        original = ConfigurationAcceptanceStore._preflight
        checked = []

        def preflight(store, prepared):
            read = _EvidenceRead(store._connection)
            if foreign_ledger:
                read.accounting = replace(read.accounting)
            transport = _Transport(store._connection, read)
            actual_phase = (("request", (prepared.request.identity.request_id,))
                if foreign_ledger else phase)
            params = (prepared.request.identity.request_id,) if actual_phase is None else actual_phase[1]
            with mock.patch.object(_EvidenceRead, "query", side_effect=AssertionError(
                    "unclosed publication read reached SQL")), self.assertRaises(_Unavailable):
                transport.read("cpk_execution_requests", _columns(_REQUEST),
                    "request_id=%s", params,
                    point=True, phase=actual_phase)
            checked.append("pre_id" if prepared.event is None else "bound")
            return original(store, prepared)

        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", preflight):
            accepted = self.base.advance()
        self.assertFalse(accepted.replayed)
        self.assertEqual(checked, ["pre_id", "bound"])

    def test_unannotated_receiver_read_refuses_before_sql(self):
        self.reject_unclosed_read(phase=None)

    def test_unselected_receiver_point_refuses_before_sql(self):
        self.reject_unclosed_read(phase=("request", ("not-in-publication-closure",)))

    def test_foreign_accounting_reader_refuses_before_sql(self):
        self.reject_unclosed_read(phase=None, foreign_ledger=True)

    def test_prospective_own_receipt_is_not_readable_before_publication(self):
        original, checked = ConfigurationAcceptanceStore._preflight, []

        def preflight(store, prepared):
            with mock.patch.object(_EvidenceRead, "query", side_effect=AssertionError(
                    "prospective own receipt reached SQL before publication")), self.assertRaises(_Unavailable):
                store._receipt(prepared.workspace.workspace_id, prepared.plan.desired_graph_revision,
                    read=prepared.evidence_read)
            checked.append("pre_id" if prepared.event is None else "bound")
            return original(store, prepared)

        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", preflight):
            self.assertFalse(self.base.advance().replayed)
        self.assertEqual(checked, ["pre_id", "bound"])

    def test_unselected_candidate_prefix_refuses_before_sql(self):
        original, checked = ConfigurationAcceptanceStore._preflight, []

        def preflight(store, prepared):
            reader = _ExecutionScopeStorage(store._connection, prepared.evidence_read)
            with mock.patch.object(_EvidenceRead, "query", side_effect=AssertionError(
                    "uncaptured candidate prefix reached SQL")), self.assertRaises(_Unavailable):
                reader.candidates(prepared.workspace.workspace_id,
                    (ExecutionReceiverScope("not-in-publication-closure", None),))
            checked.append("pre_id" if prepared.event is None else "bound")
            return original(store, prepared)

        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", preflight):
            self.assertFalse(self.base.advance().replayed)
        self.assertEqual(checked, ["pre_id", "bound"])

    def test_plan_growth_after_admission_refuses_before_large_cell_transport(self):
        before = self.base.retained_snapshot()
        plan_before = self.base.connection.execute(
            "SELECT payload FROM cpk_activity_plans WHERE plan_id='plan-a'").fetchone()
        original, injected = ConfigurationAcceptanceStore._preflight, []
        observed = dict(rows=0, bytes=0, largest_cell=0, statements=0)

        def preflight(store, prepared):
            result = original(store, prepared)
            # Real tentative SQL corruption after admission, not a fabricated
            # decoder result. Native1MiB permits this cell; captured width must
            # reject it before transport and the existing UoW must roll it back.
            store._connection.execute("UPDATE cpk_activity_plans SET payload=jsonb_set(payload, "
                "'{publication_growth}',to_jsonb(repeat('x',65000))) WHERE plan_id=%s",
                (prepared.plan.plan_id,))
            injected.append(True)
            return result

        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", preflight), \
                self.assertRaises(CurrentGraphAdvancementConflict):
            self.base.advance(lambda: PostgresUnitOfWork(lambda: _ObservedConnection(
                psycopg.connect(self.base.database_url), observed)))
        self.assertEqual(injected, [True])
        self.assertLess(observed["largest_cell"], 65000,
            "post-admission plan growth escaped its captured SQL width")
        self.assertEqual(self.base.retained_snapshot(), before)
        self.assertEqual(self.base.connection.execute(
            "SELECT payload FROM cpk_activity_plans WHERE plan_id='plan-a'").fetchone(), plan_before)


class PostgresConfigurationPublicationCarryTests(unittest.TestCase):
    def setUp(self):
        self.carry = carry_fixture.PostgresConfigurationCarryTests()
        self.addCleanup(self.cleanup_fixture)
        self.carry.setUp()

    def cleanup_fixture(self):
        self.assertTrue(self.carry.doCleanups(), "nested publication carry fixture cleanup failed")

    def test_missing_prepared_ref_cache_refuses_before_cold_fallback(self):
        original = ConfigurationAcceptanceStore._preflight
        checked = []

        def preflight(store, prepared):
            read = prepared.evidence_read
            source_key = prepared.slots[0][3:7]
            cache_key = next(key for key, row in read.refs.items() if row[:4] == source_key)
            removed = read.refs.pop(cache_key)
            try:
                with mock.patch.object(_EvidenceRead, "query", side_effect=AssertionError(
                        "missing immutable publication proof reached cold SQL")), self.assertRaises(_Unavailable):
                    store._ref(read, source_key)
            finally:
                read.refs[cache_key] = removed
            checked.append("pre_id" if prepared.event is None else "bound")
            return original(store, prepared)

        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", preflight):
            accepted = self.carry.add_runtime()
        self.assertEqual(checked, ["pre_id", "bound"])
        self.carry.assert_membership(accepted, self.carry.fixture.refs)

    def test_missing_prepared_source_caches_refuse_before_each_cold_fallback(self):
        from control_plane_kit_operations.postgres.configuration_preparation_store import _decode
        from control_plane_kit_operations.postgres.configuration_source import read_source
        from control_plane_kit_operations.postgres.effect_outcome_store import EffectAttemptOutcomeStore
        original, checked = ConfigurationAcceptanceStore._preflight, []

        def preflight(store, prepared):
            read, slot = prepared.evidence_read, prepared.slots[0]
            source = _decode(store._ref(read, slot[3:7]), read).source
            plan_key = ("configuration-source-plan", source.source.workspace_id, source.source.plan_id)
            header = read.sources[plan_key][0]
            context_key = ("configuration-receipt-context", source.source.workspace_id, header["pinned_revision"])
            slot_key = ("configuration-original-slot", source.source.workspace_id, header["pinned_revision"], *slot[:3])

            def source_read():
                self.assertEqual(read_source(store._connection, source.identity, source.ref, read=read).state, "unavailable")

            cases = (
                ("source", ("cpk_effect_attempt_intents", source.identity), source_read),
                ("outcome", ("cpk_effect_attempt_outcomes", source.identity),
                    lambda: EffectAttemptOutcomeStore(store._connection)._configuration_terminal(source, read)),
                ("source-plan", plan_key, lambda: store._original_use(read, slot, source)),
                ("receipt-context", context_key, lambda: store._receipt_context(
                    source.source.workspace_id, header["pinned_revision"], read)),
                ("original-slot", slot_key, lambda: store._original_use(read, slot, source)),
            )
            for family, key, call in cases:
                with self.subTest(family=family):
                    self.assertIn(key, read.sources, "fixture must reach the real prepared cache family")
                    value = read.sources.pop(key)
                    try:
                        with mock.patch.object(_EvidenceRead, "query", side_effect=AssertionError(
                                "missing " + family + " proof dispatched cold SQL")):
                            if family == "source":
                                call()
                            else:
                                with self.assertRaises(_Unavailable):
                                    call()
                    finally:
                        read.sources[key] = value
            checked.append("pre_id" if prepared.event is None else "bound")
            return original(store, prepared)

        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", preflight):
            accepted = self.carry.add_runtime()
        self.assertEqual(checked, ["pre_id", "bound"])
        self.carry.assert_membership(accepted, self.carry.fixture.refs)

    def test_foreign_reader_with_copied_proof_caches_refuses_before_cache_hit(self):
        original, checked = ConfigurationAcceptanceStore._preflight, []

        def preflight(store, prepared):
            foreign = _EvidenceRead(store._connection)
            foreign.refs.update(prepared.evidence_read.refs)
            foreign.sources.update(prepared.evidence_read.sources)
            with self.subTest("foreign reader"), \
                    mock.patch.object(_EvidenceRead, "query", side_effect=AssertionError("foreign proof reader queried SQL")), \
                    self.assertRaises(_Unavailable):
                store._ref(foreign, prepared.slots[0][3:7])
            checked.append("pre_id" if prepared.event is None else "bound")
            return original(store, prepared)

        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", preflight):
            accepted = self.carry.add_runtime()
        self.assertEqual(checked, ["pre_id", "bound"])
        self.carry.assert_membership(accepted, self.carry.fixture.refs)

    def test_carry_actual_publication_and_native_cold_reader_fit(self):
        with _publication_budget_observation(self, self.carry.base.database_url) as (trace, factory), \
                mock.patch.object(self.carry.base, "unit_of_work", factory):
            accepted = self.carry.add_runtime()
        self.carry.assert_membership(accepted, self.carry.fixture.refs)
        _assert_publication_and_cold_fit(self, trace, self.carry.base.database_url)

    def test_actual_readback_unknown_source_refuses_before_cold_fallback(self):
        runtime = replace(self.carry.graph.runtimes["runtime-a"], children=("worker",))
        graph = replace(self.carry.graph, nodes={"worker": self.carry.graph.nodes["worker"]},
            runtimes={"runtime-a": runtime})
        command = self.carry.prepare("remove-api", "graph-worker-only",
            RemoveNodeResource(NodeTarget("api")), graph=graph)
        before = self.carry.base.retained_snapshot()
        preflight, receipt, query = (ConfigurationAcceptanceStore._preflight,
            ConfigurationAcceptanceStore._receipt, _EvidenceRead.query)
        prepared_values, injected, cold = [], [], []

        def prepared(store, value):
            result = preflight(store, value)
            # Actual readback is witnessed by the bound action/event value.
            if value.event is not None:
                prepared_values.append(value)
            return result

        def readback(store, workspace, revision, *, read=None):
            if (prepared_values and not injected and read is prepared_values[0].evidence_read
                    and revision == prepared_values[0].plan.desired_graph_revision):
                value = prepared_values[0]
                foreign = next(row for row in self.carry.fixture.expected_slots
                    if row[1] == "api" and row[2] == value.slots[0][2])
                self.assertFalse(any(row[:4] == foreign[3:7] for row in read.refs.values()))
                altered = (*value.slots[0][:3], *foreign[3:])
                for index, cell in enumerate(altered):
                    self.assertLessEqual(len(str(cell).encode()),
                        max(len(str(row[index]).encode()) for row in value.slots),
                        "fixture must test an unknown key within existing width ceilings")
                slots = (altered, *value.slots[1:])
                store._connection.execute("UPDATE cpk_configuration_accepted_slots SET "
                    "source_run_id=%s,source_activity_id=%s,source_attempt=%s,source_artifact_id=%s,"
                    "birth_run_id=%s,birth_activity_id=%s,birth_attempt=%s,birth_artifact_id=%s,full_ref_digest=%s "
                    "WHERE workspace_id=%s AND pinned_revision=%s AND runtime_id=%s AND node_id=%s AND artifact_id=%s",
                    (*altered[3:], workspace, revision, *altered[:3]))
                digest = sha256(rfc8785.dumps([[list(row[:3]), list(row[3:7]), list(row[7:11]), row[11]]
                    for row in slots])).hexdigest()
                store._connection.execute("UPDATE cpk_configuration_acceptances SET slot_digest=%s "
                    "WHERE workspace_id=%s AND pinned_revision=%s", (digest, workspace, revision))
                injected.append(foreign[3:7])
            return receipt(store, workspace, revision, read=read)

        def observed_query(read, sql, params, **kwargs):
            if injected and tuple(params) == injected[0] and "cpk_effect_configuration_refs" in sql:
                cold.append(tuple(params))
            return query(read, sql, params, **kwargs)

        ids = iter(("event-readback-refusal", "action-readback-refusal"))
        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", prepared), \
                mock.patch.object(ConfigurationAcceptanceStore, "_receipt", readback), \
                mock.patch.object(_EvidenceRead, "query", observed_query), \
                self.assertRaises(CurrentGraphAdvancementConflict):
            CurrentGraphAdvancementCommandService(self.carry.base.unit_of_work,
                clock=lambda: "2026-07-22T13:05:00Z", id_factory=lambda: next(ids)).execute(command)
        self.assertEqual(len(injected), 1)
        self.assertEqual(cold, [], "actual changed readback slot escaped into an uncaptured proof query")
        self.assertEqual(self.carry.base.retained_snapshot(), before)


class PostgresConfigurationPublicationBudgetTests(unittest.TestCase):
    setUp = PostgresConfigurationPublicationLifetimeTests.setUp
    cleanup_fixture = PostgresConfigurationPublicationLifetimeTests.cleanup_fixture

    def test_zero_slot_actual_publication_and_native_cold_reader_fit(self):
        with _publication_budget_observation(self, self.base.database_url) as (trace, factory):
            self.assertFalse(self.base.advance(factory).replayed)
        _assert_publication_and_cold_fit(self, trace, self.base.database_url)

    def test_failed_receipt_fetch_retains_reservation_and_one_close_within_peak(self):
        before = self.base.retained_snapshot()
        failed, after_failure = [], []
        with _publication_budget_observation(self, self.base.database_url) as (trace, _):
            class FetchFailure(_BudgetObservedConnection):
                def execute(self, sql, *args, **kwargs):
                    if failed:
                        after_failure.append(str(sql))
                    cursor = super().execute(sql, *args, **kwargs)
                    if (not failed and trace.get("accounting") is not None
                            and _ACCOUNTING.get() is trace["accounting"]
                            and "FROM cpk_configuration_acceptances" in str(sql)
                            and "CASE WHEN" in str(sql)):
                        failed.append(_ACCOUNTING.get().used)
                        # Fault after real execute, before fetch; the database
                        # transaction remains usable for the owner's close.
                        raise psycopg.DataError("injected publication fetch failure")
                    return cursor

            with self.assertRaises((psycopg.DataError, CurrentGraphAdvancementConflict)):
                self.base.advance(lambda: PostgresUnitOfWork(lambda: FetchFailure(
                    psycopg.connect(self.base.database_url), trace)))
        self.assertEqual(len(failed), 1)
        self.assertEqual(after_failure, ["SELECT txid_current()"])
        self.assertEqual(tuple(trace["phases"]), ("pre_id", "bound"))
        for current in trace["phases"].values():
            for peak in trace["peaks"][current["peak_offset"]:]:
                _within(self, _difference(peak, current["prior"]), current["publication"].peak)
            _within(self, _difference(trace["accounting"].used, current["prior"]), current["publication"].peak)
        retained = _difference(trace["accounting"].used, failed[0])
        self.assertEqual((retained.records, retained.scalar_markers, retained.statements), (1, 1, 1))
        self.assertGreater(retained.value_octets, 0)
        with mock.patch.object(_EvidenceRead, "query", side_effect=AssertionError("spent publication reached SQL")), \
                self.assertRaises((OperationsRecordError, _Unavailable)):
            trace["owner"]._require_issued(trace["prepared"])
        self.assertEqual(self.base.retained_snapshot(), before)

    def synthetic_gates(self, exercise):
        original = ConfigurationAcceptanceStore._preflight
        checked = []

        def preflight(store, prepared):
            forecast = getattr(store, "_publication_budgets", None)
            self.assertTrue(callable(forecast), "publication lacks its source-derived settled/peak forecast")
            values = forecast(prepared)
            prior = _ACCOUNTING.get().used
            try:
                exercise(store, prepared, original, values, prior)
            finally:
                _ACCOUNTING.get().used = prior
            checked.append("pre_id" if prepared.event is None else "bound")
            return original(store, prepared)

        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", preflight):
            self.assertFalse(self.base.advance().replayed)
        self.assertEqual(checked, ["pre_id", "bound"])

    def test_snapshot_three_mib_gate_precedes_global_decisions(self):
        from control_plane_kit_operations import configuration_preparation as values

        def exercise(store, prepared, preflight, budgets, prior):
            snapshot, future, publication = budgets
            extra = 3 * 1024 * 1024 - snapshot.accounted_bytes
            self.assertGreaterEqual(extra, 0)
            growth = Footprint(0, extra, 0, 0)
            exact = snapshot.plus(growth)
            grown_future = _OrdinarySuffixBudget(future.settled.plus(growth), future.peak.plus(growth))
            with mock.patch.object(store, "_publication_budgets", return_value=(exact, grown_future, publication)):
                preflight(store, prepared)
            over = replace(exact, value_octets=exact.value_octets + 1)
            over_future = _OrdinarySuffixBudget(grown_future.settled.plus(Footprint(0, 1, 0, 0)),
                grown_future.peak.plus(Footprint(0, 1, 0, 0)))
            with mock.patch.object(store, "_publication_budgets", return_value=(over, over_future, publication)), \
                    mock.patch.object(values, "configuration_evidence_capacity", wraps=values.configuration_evidence_capacity) as decide, \
                    self.assertRaises(_Capacity):
                preflight(store, prepared)
            self.assertEqual(decide.call_count, 0)
            self.assertEqual(_ACCOUNTING.get().used, prior)

        self.synthetic_gates(exercise)

    def test_future_native_peak_record_and_byte_edges_use_fresh_budget(self):
        def exercise(store, prepared, preflight, budgets, prior):
            snapshot, future, publication = budgets
            # Synthetic, well-formed forecast; no claim of natural exhaustion.
            for dimension, limit in (("records", 4096), ("value_octets", 16 * 1024 * 1024)):
                settled = future.settled
                peak = replace(future.peak, **{dimension: limit})
                if dimension == "value_octets":
                    peak = replace(peak, value_octets=limit - (peak.accounted_bytes - peak.value_octets))
                exact = _OrdinarySuffixBudget(settled, peak)
                with mock.patch.object(store, "_publication_budgets", return_value=(snapshot, exact, publication)):
                    preflight(store, prepared)
                over = _OrdinarySuffixBudget(settled, replace(peak, **{dimension: getattr(peak, dimension) + 1}))
                with mock.patch.object(store, "_publication_budgets", return_value=(snapshot, over, publication)), \
                        self.assertRaises(_Capacity):
                    preflight(store, prepared)
                self.assertEqual(_ACCOUNTING.get().used, prior)

        self.synthetic_gates(exercise)

    def test_publication_peak_edges_include_all_prior_work(self):
        def exercise(store, prepared, preflight, budgets, prior):
            snapshot, future, publication = budgets
            for dimension, limit in (("records", 4096), ("value_octets", 16 * 1024 * 1024)):
                used = prior.plus(publication.peak)
                remaining = limit - (used.records if dimension == "records" else used.accounted_bytes)
                self.assertGreaterEqual(remaining, 0)
                exact = replace(prior, **{dimension: getattr(prior, dimension) + remaining})
                _ACCOUNTING.get().used = exact
                preflight(store, prepared)
                _ACCOUNTING.get().used = replace(exact, **{dimension: getattr(exact, dimension) + 1})
                with self.assertRaises(_Capacity):
                    preflight(store, prepared)
                _ACCOUNTING.get().used = prior

        self.synthetic_gates(exercise)
