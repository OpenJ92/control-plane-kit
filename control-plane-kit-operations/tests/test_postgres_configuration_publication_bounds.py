"""#1952 publication lifetime and whole receiver-finish transport laws.

Owning PostgreSQL commands over existing completion premises; no provider or
health evidence. Wire observation never replaces production query results.
"""
from dataclasses import replace
import unittest
from unittest import mock

import psycopg

from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_operations._configuration_preparation import _ACCOUNTING
from control_plane_kit_operations.advancement import (
    AdvanceCurrentGraph, CurrentGraphAdvancementCommandService, CurrentGraphAdvancementConflict,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_acceptance_store import ConfigurationAcceptanceStore
from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead, _Unavailable
from control_plane_kit_operations.postgres.receiver_execution_scopes import _Transport, _REQUEST, _columns
from control_plane_kit_operations.records import OperationsRecordError
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.receiver_canonical_acceptance_fixture import ReceiverCanonicalAcceptanceFixture
from tests import test_postgres_configuration_acceptance as zero_fixture
from tests import test_postgres_configuration_carry as carry_fixture
from tests.test_postgres_configuration_evidence import _ObservedConnection


class PostgresConfigurationPublicationReceiverTests(ReceiverCanonicalAcceptanceFixture, unittest.TestCase):
    def observed_advance(self, claimed, suffix):
        # Prepare the command outside the wire-observed command connection.
        with self.unit_of_work() as uow:
            plan = uow.stores.activity_history.get_plan(claimed.request.identity.plan_id)
        command = AdvanceCurrentGraph("workspace-a", claimed.run.run_id, plan.plan_id,
            plan.base_graph_id, plan.base_realized_projection_id,
            plan.desired_graph_id, plan.desired_realized_projection_id, plan.desired_graph_revision,
            ExecutionWorkerAuthority("worker-a", (PolicyScope.EXECUTION_OPERATE,)),
            ExecutionLeaseFence("worker-a", claimed.request.claim.generation), IdempotencyKey("advance-" + suffix))
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


class PostgresConfigurationPublicationLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.base = zero_fixture.PostgresConfigurationAcceptanceTests()
        self.addCleanup(self.cleanup_fixture)
        self.base.setUp()

    def cleanup_fixture(self):
        self.assertTrue(self.base.doCleanups(), "nested publication fixture cleanup failed")

    def test_prepared_credential_is_spent_before_database_commit(self):
        prepared_values, committed = [], []
        original = ConfigurationAcceptanceStore._preflight
        case = self

        def preflight(store, prepared):
            result = original(store, prepared)
            prepared_values.append((store, prepared))
            return result

        class ObservedCommit:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def commit(self):
                case.assertEqual(len(prepared_values), 1)
                store, prepared = prepared_values[0]
                with case.assertRaises((OperationsRecordError, _Unavailable)):
                    store._require_issued(prepared)
                committed.append(True)
                self.connection.commit()

        with mock.patch.object(ConfigurationAcceptanceStore, "_preflight", preflight):
            accepted = self.base.advance(lambda: PostgresUnitOfWork(
                lambda: ObservedCommit(psycopg.connect(self.base.database_url))))
        self.assertFalse(accepted.replayed)
        self.assertEqual(committed, [True])
        store, prepared = prepared_values[0]
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
            with mock.patch.object(_EvidenceRead, "query", side_effect=AssertionError(
                    "unclosed publication read reached SQL")), self.assertRaises(_Unavailable):
                transport.read("cpk_execution_requests", _columns(_REQUEST),
                    "request_id=%s", (prepared.request.identity.request_id,),
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
