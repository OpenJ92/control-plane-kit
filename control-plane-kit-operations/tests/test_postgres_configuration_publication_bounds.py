"""#1952 publication lifetime and whole receiver-finish transport laws.

Owning PostgreSQL commands over existing completion premises; no provider or
health evidence. Wire observation never replaces production query results.
"""
from contextlib import contextmanager
from dataclasses import replace
import json
import unittest
from unittest import mock

import psycopg

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_operations._configuration_preparation import _ACCOUNTING
from control_plane_kit_operations._configuration_preparation import _OrdinarySuffixBudget
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
from control_plane_kit_operations.receiver_execution_scopes import ExecutionReceiverScope
from control_plane_kit_operations.records import OperationsRecordError
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.receiver_canonical_acceptance_fixture import ReceiverCanonicalAcceptanceFixture
from tests import test_postgres_configuration_acceptance as zero_fixture
from tests import test_postgres_configuration_carry as carry_fixture
from tests.test_postgres_configuration_evidence import _ObservedConnection


def _difference(after, before):
    return Footprint(*(getattr(after, name) - getattr(before, name)
        for name in ("records", "value_octets", "scalar_markers", "statements")))


def _within(case, actual, expected):
    for name in ("records", "value_octets", "scalar_markers", "statements"):
        case.assertLessEqual(getattr(actual, name), getattr(expected, name), name)


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
            return super().execute(*args, **kwargs)
        return self.connection.execute(*args, **kwargs)


def _trace():
    return dict(wire=dict(rows=0, bytes=0, largest_cell=0, statements=0), peaks=[])


@contextmanager
def _publication_budget_observation(case, database_url):
    trace = _trace()
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
        trace.update(snapshot=snapshot, future=future, publication=publication,
            prior=prior, accounting=_ACCOUNTING.get())

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
    used = _difference(trace["final"], trace["prior"])
    _within(case, used, trace["publication"].settled)
    for peak in trace["peaks"]:
        _within(case, _difference(peak, trace["prior"]), trace["publication"].peak)
    case.assertEqual(used.statements, trace["wire"]["statements"])
    case.assertGreaterEqual(used.records, trace["wire"]["rows"])
    case.assertGreaterEqual(used.accounted_bytes, trace["wire"]["bytes"])
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
    _within(case, snapshots[0], trace["snapshot"])
    case.assertLessEqual(snapshots[0].accounted_bytes, 3 * 1024 * 1024)
    _within(case, cold["accounting"].used, trace["future"].settled)
    for peak in cold["peaks"]:
        _within(case, peak, trace["future"].peak)
    case.assertEqual(cold["accounting"].used.statements, cold["wire"]["statements"])
    case.assertGreaterEqual(cold["accounting"].used.records, cold["wire"]["rows"])
    case.assertGreaterEqual(cold["accounting"].used.accounted_bytes, cold["wire"]["bytes"])
    print("publication-fit " + json.dumps(dict(prior_records=trace["prior"].records,
        prior_bytes=trace["prior"].accounted_bytes, suffix_records=used.records,
        suffix_bytes=used.accounted_bytes, suffix_statements=used.statements,
        cold_records=cold["accounting"].used.records, cold_bytes=cold["accounting"].used.accounted_bytes,
        snapshot_bytes=snapshots[0].accounted_bytes)))


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
            self.assertEqual(len(prepared_values), 1)
            store, prepared, accounting = prepared_values[0]
            self.assertFalse(uow._commit_requested)
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
                case.assertEqual(len(prepared_values), 1)
                store, prepared, _ = prepared_values[0]
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
        store, prepared, _ = prepared_values[0]
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
            checked.append(True)
            return original(store, prepared)

        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", preflight):
            accepted = self.base.advance()
        self.assertFalse(accepted.replayed)
        self.assertEqual(checked, [True])

    def test_unannotated_receiver_read_refuses_before_sql(self):
        self.reject_unclosed_read(phase=None)

    def test_unselected_receiver_point_refuses_before_sql(self):
        self.reject_unclosed_read(phase=("request", ("not-in-publication-closure",)))

    def test_foreign_accounting_reader_refuses_before_sql(self):
        self.reject_unclosed_read(phase=None, foreign_ledger=True)

    def test_unselected_candidate_prefix_refuses_before_sql(self):
        original, checked = ConfigurationAcceptanceStore._preflight, []

        def preflight(store, prepared):
            reader = _ExecutionScopeStorage(store._connection, prepared.evidence_read)
            with mock.patch.object(_EvidenceRead, "query", side_effect=AssertionError(
                    "uncaptured candidate prefix reached SQL")), self.assertRaises(_Unavailable):
                reader.candidates(prepared.workspace.workspace_id,
                    (ExecutionReceiverScope("not-in-publication-closure", None),))
            checked.append(True)
            return original(store, prepared)

        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", preflight):
            self.assertFalse(self.base.advance().replayed)
        self.assertEqual(checked, [True])

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
            checked.append(True)
            return original(store, prepared)

        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", preflight):
            accepted = self.carry.add_runtime()
        self.assertEqual(checked, [True])
        self.carry.assert_membership(accepted, self.carry.fixture.refs)

    def test_carry_actual_publication_and_native_cold_reader_fit(self):
        with _publication_budget_observation(self, self.carry.base.database_url) as (trace, factory), \
                mock.patch.object(self.carry.base, "unit_of_work", factory):
            accepted = self.carry.add_runtime()
        self.carry.assert_membership(accepted, self.carry.fixture.refs)
        _assert_publication_and_cold_fit(self, trace, self.carry.base.database_url)


class PostgresConfigurationPublicationBudgetTests(unittest.TestCase):
    setUp = PostgresConfigurationPublicationLifetimeTests.setUp
    cleanup_fixture = PostgresConfigurationPublicationLifetimeTests.cleanup_fixture

    def test_zero_slot_actual_publication_and_native_cold_reader_fit(self):
        with _publication_budget_observation(self, self.base.database_url) as (trace, factory):
            self.assertFalse(self.base.advance(factory).replayed)
        _assert_publication_and_cold_fit(self, trace, self.base.database_url)

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
            checked.append(True)
            return original(store, prepared)

        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", preflight):
            self.assertFalse(self.base.advance().replayed)
        self.assertEqual(checked, [True])

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
