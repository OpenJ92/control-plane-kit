"""#1950 new ordinary-owner lifetime, invalidation and rollback laws."""
from contextlib import contextmanager
from dataclasses import replace
import unittest
from unittest import mock

from control_plane_kit_operations import _configuration_preparation as support
from control_plane_kit_operations.effect_attempt_start import EffectAttemptStartConflict, NewlyStarted
from control_plane_kit_operations.postgres import configuration_preparation_store as preparation
from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead, _Unavailable
from control_plane_kit_operations.records import OperationsRecordError
from tests.configuration_preparation_fixture import ConfigurationPreparationFixture


class PostgresConfigurationStartBoundsTests(ConfigurationPreparationFixture, unittest.TestCase):
    def owner_type(self):
        owner = getattr(preparation, "_OrdinaryStartReadBoundsOwner", None)
        self.assertIsNotNone(owner, "ordinary start lacks its transaction-local transport owner")
        return owner

    @contextmanager
    def trace_binding(self, *, fail_capture=False, fail_bind=False):
        owner_type = self.owner_type()
        capture, bind, query = owner_type.capture, owner_type.bind, _EvidenceRead.query
        trace = dict(capture=0, bind=0, active=0, close=0, phase=None)

        def observed_capture(owner, *args, **kwargs):
            result = capture(owner, *args, **kwargs)
            trace["capture"] += 1
            if fail_capture:
                raise _Unavailable
            return result

        @contextmanager
        def observed_bind(owner, *args, **kwargs):
            trace["bind"] += 1
            trace["phase"] = "bind"
            try:
                with bind(owner, *args, **kwargs):
                    trace["active"] += 1
                    trace["phase"] = "body"
                    try:
                        yield
                    finally:
                        trace["phase"] = "close"
            finally:
                trace["phase"] = None

        def observed_query(read, sql, params, **kwargs):
            result = query(read, sql, params, **kwargs)
            if sql == "SELECT txid_current()":
                if trace["phase"] == "close":
                    trace["close"] += 1
                if fail_bind and trace["phase"] == "bind":
                    # The real bind query was charged; its transaction identity
                    # is corrupted only for this negative owner-boundary test.
                    return [(result[0][0] + 1,)]
            return result

        with mock.patch.object(owner_type, "capture", observed_capture), \
                mock.patch.object(owner_type, "bind", observed_bind), \
                mock.patch.object(_EvidenceRead, "query", observed_query):
            yield trace

    def test_failed_capture_never_binds_or_closes(self):
        command, before = self.configuration_command(), self.complete_start_snapshot()
        with self.trace_binding(fail_capture=True) as trace, self.assertRaises(EffectAttemptStartConflict):
            self.start_service("unused").execute(command)
        self.assertEqual((trace["capture"], trace["bind"], trace["active"], trace["close"]), (1, 0, 0, 0))
        self.assertIsNone(support._BOUND_ORDINARY_START.get())
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_failed_bind_never_activates_or_closes(self):
        command, before = self.configuration_command(), self.complete_start_snapshot()
        with self.trace_binding(fail_bind=True) as trace, self.assertRaises(EffectAttemptStartConflict):
            self.start_service("unused").execute(command)
        self.assertEqual((trace["capture"], trace["bind"], trace["active"], trace["close"]), (1, 1, 0, 0))
        self.assertIsNone(support._BOUND_ORDINARY_START.get())
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_successful_bind_closes_exactly_once_after_real_start(self):
        command = self.configuration_command()
        with self.trace_binding() as trace:
            self.assertIsInstance(self.start_service("bounded-start").execute(command), NewlyStarted)
        self.assertEqual((trace["capture"], trace["bind"], trace["active"], trace["close"]), (1, 1, 1, 1))
        self.assertIsNone(support._BOUND_ORDINARY_START.get())

    def test_late_write_failure_closes_once_retains_raw_charge_and_rolls_back(self):
        from control_plane_kit_operations import configuration_preparation as values
        command, before = self.configuration_command(), self.complete_start_snapshot()
        actual = preparation.ConfigurationPreparationStore._insert_original
        actual_prepare = preparation.ConfigurationPreparationStore._prepare
        actual_capacity = values.configuration_preparation_capacity
        captured = {}

        def capacity(**kwargs):
            captured["last_capacity"] = kwargs["current"]
            return actual_capacity(**kwargs)

        def prepared(store, *args, **kwargs):
            result = actual_prepare(store, *args, **kwargs)
            self.assertEqual(support._ACCOUNTING.get().used.statements - captured["last_capacity"].statements,
                1 + 2 * len(result.intent.configuration_instances.instances))
            return result

        def fail_after_writes(store, record, prepared):
            self.assertIsNotNone(support._BOUND_ORDINARY_START.get())
            start = support._ACCOUNTING.get().used
            actual(store, record, prepared)
            captured.update(accounting=support._ACCOUNTING.get(), after=support._ACCOUNTING.get().used,
                raw_count=1 + 2 * len(record.intent.configuration_instances.instances))
            self.assertGreaterEqual(captured["after"].statements, start.statements)
            raise OperationsRecordError("injected late start failure")

        with self.trace_binding() as trace, \
                mock.patch.object(values, "configuration_preparation_capacity", capacity), \
                mock.patch.object(preparation.ConfigurationPreparationStore, "_prepare", prepared), \
                mock.patch.object(preparation.ConfigurationPreparationStore, "_insert_original", fail_after_writes), \
                self.assertRaises(EffectAttemptStartConflict):
            self.start_service("rolled-back-start").execute(command)
        self.assertEqual((trace["active"], trace["close"]), (1, 1))
        self.assertEqual(captured["accounting"].used.statements, captured["after"].statements + 1)
        self.assertGreaterEqual(captured["accounting"].used.statements, captured["raw_count"])
        self.assertIsNone(support._BOUND_ORDINARY_START.get())
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_owner_rejects_forgery_foreign_context_reentry_spent_and_cleanup_use(self):
        self.owner_type()
        from control_plane_kit_operations._configuration_cleanup_ownership import _require_cleanup_start
        from control_plane_kit_operations._configuration_cleanup_phase_read_bounds import _BOUND_CLEANUP_PHASE
        from control_plane_kit_operations._configuration_cleanup_read_ceilings import _BOUND_CLEANUP_ORIGINALS
        actual = preparation.ConfigurationPreparationStore._prepare
        captured = {}

        def observe(store, *args, **kwargs):
            prepared = actual(store, *args, **kwargs)
            issued = support._BOUND_ORDINARY_START.get()
            self.assertIsNotNone(issued)
            owner = issued.owner
            captured.update(owner=owner, issued=issued, connection=store._connection)
            owner._require(issued, store._connection)
            with self.assertRaises(_Unavailable):
                owner._require(replace(issued), store._connection)
            with self.unit_of_work() as foreign:
                with self.assertRaises(_Unavailable):
                    owner._require(issued, foreign.stores.connection)
            with support._configuration_accounting():
                with self.assertRaises(_Unavailable):
                    owner._require(issued, store._connection)
            with self.assertRaises(_Unavailable):
                with owner.bind(issued):
                    self.fail("issued owner rebound")
            self.assertIsNone(_BOUND_CLEANUP_PHASE.get())
            self.assertIsNone(_BOUND_CLEANUP_ORIGINALS.get())
            with self.assertRaises(OperationsRecordError):
                _require_cleanup_start(prepared, store._connection, prepared.identity, prepared.intent)
            return prepared

        with mock.patch.object(preparation.ConfigurationPreparationStore, "_prepare", observe):
            self.assertIsInstance(self.start_service("owner-start").execute(self.configuration_command()), NewlyStarted)
        with self.assertRaises(_Unavailable):
            captured["owner"]._require(captured["issued"], captured["connection"])
        self.assertIsNone(support._BOUND_ORDINARY_START.get())

    def _late_material_change(self, change):
        self.owner_type()
        command, before = self.configuration_command(), self.complete_start_snapshot()
        actual = preparation.ConfigurationPreparationStore._prepare
        entered = []

        def mutate(store, *args, **kwargs):
            prepared = actual(store, *args, **kwargs)
            self.assertIsNotNone(support._BOUND_ORDINARY_START.get())
            entered.append(prepared)
            change(store._connection)
            return prepared

        with mock.patch.object(preparation.ConfigurationPreparationStore, "_prepare", mutate), \
                self.assertRaises(EffectAttemptStartConflict):
            self.start_service("invalidated-start").execute(command)
        self.assertEqual(len(entered), 1)
        self.assertIsNone(support._BOUND_ORDINARY_START.get())
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_captured_graph_column_growth_refuses_and_rolls_back_late_start(self):
        self._late_material_change(lambda connection: connection.execute(
            "UPDATE cpk_graph_versions SET metadata=jsonb_build_object('growth',%s) WHERE graph_id='graph-desired'",
            ("x" * 4096,)))

    def test_captured_scope_key_growth_refuses_and_rolls_back_late_start(self):
        self._late_material_change(lambda connection: connection.execute(
            "INSERT INTO cpk_execution_receiver_scopes "
            "(request_id,workspace_id,scope_ordinal,scope_kind,runtime_id,node_id) "
            "SELECT request_id,workspace_id,scope_ordinal+1,scope_kind,runtime_id,'unexpected-node' "
            "FROM cpk_execution_receiver_scopes WHERE request_id='request-a' AND scope_ordinal=0"))

    def test_same_width_approval_rejection_still_requires_fresh_authorization(self):
        self._late_material_change(lambda connection: connection.execute(
            "UPDATE cpk_approval_decisions SET decision='rejected' WHERE request_id="
            "(SELECT approval_request_id FROM cpk_execution_requests WHERE request_id='request-a')"))

    def test_publication_reader_preserves_zero_one_two_candidates_on_the_same_ledger(self):
        from psycopg.types.json import Jsonb
        for count in range(3):
            with self.subTest(candidates=count):
                if count:
                    self.connection.execute("INSERT INTO cpk_operation_actions "
                        "(action_id,session_id,ordinal,action_type,actor_id,payload,created_at) "
                        "VALUES (%s,'session-a',%s,'publish-desired-realized-projection','operator-a',%s,%s)",
                        ("publication-" + str(count), 100 + count,
                            Jsonb({"desired_realized_projection_id": "diagnostic-publication"}),
                            "2026-08-15T03:59:21Z"))
                with support._configuration_accounting() as accounting, self.unit_of_work() as uow:
                    rows = uow.stores.activity_history._projection_publication_actions(
                        "session-a", "diagnostic-publication")
                    self.assertEqual(len(rows), count)
                    self.assertEqual(accounting.used.statements, 1 if count == 0 else 2)
                    self.assertEqual(accounting.used.records, 2 * count)
                    self.assertEqual(accounting.used.scalar_markers, 19 * count)
                    if count:
                        self.assertGreater(accounting.used.value_octets, 0)

    def test_source_failures_retain_full_query_reservation_cleanup_and_one_close(self):
        import psycopg
        from control_plane_kit_operations.postgres import PostgresUnitOfWork
        from control_plane_kit_operations.postgres.configuration_source import _SOURCE
        from tests.configuration_cleanup_phase_read_bounds_fixture import _PhaseConnection, _components
        self.owner_type()
        for error_type, final_statements in ((psycopg.DataError, 3), (_Unavailable, 2)):
            with self.subTest(error=error_type.__name__):
                self.reset_start_truth()
                command, before = self.configuration_command(), self.complete_start_snapshot()
                service = self.start_service("source-failure-start")
                captured = {}
                observed = dict(bytes=0, rows=0, largest_cell=0, statements=0, queries=[],
                    accounting=None, role_label=lambda sql, params: "ordinary-source-failure")

                class FailingConnection(_PhaseConnection):
                    def execute(connection, sql, params=()):
                        if support._BOUND_ORDINARY_START.get() is not None:
                            if sql == "SAVEPOINT cpk_configuration_source_read":
                                captured["savepoint"] = support._ACCOUNTING.get().used
                            if sql == _SOURCE:
                                # _EvidenceRead has reserved before dispatch to
                                # this connection. Fail that dispatch, not the
                                # accounting function or permission decision.
                                captured["failed_query"] = support._ACCOUNTING.get().used
                                raise error_type("injected source transport failure")
                        return super().execute(sql, params)

                class MeasuredUnitOfWork(PostgresUnitOfWork):
                    def __exit__(uow, *args):
                        try:
                            return super().__exit__(*args)
                        finally:
                            captured["end"] = observed["accounting"].used

                def factory():
                    observed["accounting"] = support._ACCOUNTING.get()
                    return MeasuredUnitOfWork(lambda: FailingConnection(
                        psycopg.connect(self.database_url), observed))

                with self.trace_binding() as trace, mock.patch.object(service, "_unit_of_work_factory", factory), \
                        self.assertRaises(EffectAttemptStartConflict):
                    service.execute(command)
                self.assertEqual((trace["active"], trace["close"]), (1, 1))
                reservation = tuple(a - b for a, b in zip(_components(captured["failed_query"]),
                    _components(captured["savepoint"]), strict=True))
                self.assertEqual(reservation, (3, 80032, 17, 1))
                cleanup = tuple(a - b for a, b in zip(_components(captured["end"]),
                    _components(captured["failed_query"]), strict=True))
                self.assertEqual((cleanup[0], cleanup[2], cleanup[3]), (1, 1, final_statements))
                self.assertGreater(cleanup[1], 0)
                self.assertLessEqual(cleanup[1], 20)
                self.assertEqual(self.complete_start_snapshot(), before)
