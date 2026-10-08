"""Distinct fresh reuse starts serialize protection of a transferred allocation."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import queue
import unittest

from control_plane_kit_core.operations import EffectAttemptIdentity, EffectAttemptTransition, EffectAttemptTransitionKind
from control_plane_kit_core.planning import NodeTarget, ReconcileNode
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_operations.effect_attempt_start import StartEffectAttempt, NewlyStarted, EffectAttemptStartConflict
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from tests import test_postgres_configuration_transfer_producer as producer
from tests import test_postgres_configuration_carry as carry
from tests import test_execution_coordinator as execution
from tests import test_postgres_effect_attempt_start_concurrency as concurrency
from tests.receiver_fresh_execution_fixture import load_execution_context


class PostgresConfigurationTransferProducerConcurrentTests(unittest.TestCase):
    _factory_with_pids = concurrency.PostgresEffectAttemptStartConcurrencyTests._factory_with_pids
    _wait_until_blocked_by = concurrency.PostgresEffectAttemptStartConcurrencyTests._wait_until_blocked_by

    def test_distinct_concurrent_reuse_preserves_first_active_claim_and_refuses_second(self):
        fixture = producer.PostgresConfigurationTransferProducerTests()
        self.addCleanup(lambda: self.assertTrue(fixture.doCleanups(), "producer fixture cleanup failed"))
        member = fixture.configured_member()
        accepted = member.advance()
        base = member.fixture
        self.connection, self.database_url = member.connection, base.database_url
        refs = member.refs
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_configuration_claim_transfers").fetchone(),
            (len(refs),))
        operator = carry.PostgresConfigurationCarryTests()
        operator.base, operator.graph_version = base, 3
        with base.unit_of_work() as uow:
            graph = DEFAULT_GRAPH_CODEC.decode(uow.stores.graphs.get("graph-configured").graph_descriptor)
        admitted = operator.admit("concurrent-reuse", "graph-concurrent-reuse", ReconcileNode(NodeTarget("api")), graph=graph)
        competitor = operator.admit("competing-reuse", "graph-concurrent-reuse", ReconcileNode(NodeTarget("api")),
            reuse_selected_desired=True)
        self.assertEqual(admitted.expected_desired_graph_revision, competitor.expected_desired_graph_revision)
        engine = base.engine
        adapter = execution.RecordingAdapter(engine.tracker)
        execution_command = replace(engine.command(generation=admitted.fence.generation), run_id=admitted.run_id)
        context = load_execution_context(engine.coordinator(adapter), execution_command)
        intent = context.configuration_intent
        self.assertIsNotNone(intent)
        self.assertEqual(intent.configuration_instances.instances, refs)
        identity = EffectAttemptIdentity(intent.source.run_id, intent.activity_id.value, 1)
        command = StartEffectAttempt(intent.source.request_id,
            EffectAttemptTransition(EffectAttemptTransitionKind.STARTED, identity,
                request_fingerprint=runtime_effect_intent_fingerprint(intent)),
            intent, admitted.authority, admitted.fence)
        competing_context = load_execution_context(engine.coordinator(adapter), replace(
            engine.command(generation=competitor.fence.generation), run_id=competitor.run_id))
        competing_intent = competing_context.configuration_intent
        self.assertIsNotNone(competing_intent)
        self.assertEqual(competing_intent.configuration_instances.instances, refs)
        competing_identity = EffectAttemptIdentity(competing_intent.source.run_id, competing_intent.activity_id.value, 1)
        competing_command = StartEffectAttempt(competing_intent.source.request_id,
            EffectAttemptTransition(EffectAttemptTransitionKind.STARTED, competing_identity,
                request_fingerprint=runtime_effect_intent_fingerprint(competing_intent)),
            competing_intent, competitor.authority, competitor.fence)
        transfers = self.connection.execute("SELECT * FROM cpk_configuration_claim_transfers "
            "ORDER BY run_id,activity_id,attempt,artifact_id").fetchall()
        pids, blocker = queue.Queue(), concurrency._BlockingId("concurrent-reuse-original")
        first = EffectAttemptStartService(self._factory_with_pids(pids), id_factory=blocker)
        second = EffectAttemptStartService(self._factory_with_pids(pids),
            id_factory=lambda: self.fail("losing distinct start allocated an identity"))
        with ThreadPoolExecutor(max_workers=2) as pool:
            winner = pool.submit(first.execute, command)
            try:
                if not blocker.entered.wait(timeout=30):
                    winner.result(timeout=1)
                    self.fail("new reuse did not reach identity allocation")
                first_pid = pids.get(timeout=5)
                loser = pool.submit(second.execute, competing_command)
                second_pid = pids.get(timeout=5)
                self._wait_until_blocked_by(second_pid, first_pid)
                blocker.release.set()
                result = winner.result(timeout=30)
                with self.assertRaises(EffectAttemptStartConflict):
                    loser.result(timeout=30)
            finally:
                blocker.release.set()
        self.assertIs(type(result), NewlyStarted)
        self.assertEqual(adapter.calls, [], "this test owns start permission, not provider dispatch")
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_activity_events "
            "WHERE run_id=%s AND event_type='step_started'", (admitted.run_id,)).fetchone(), (1,))
        for table in ("cpk_effect_attempt_intents", "cpk_effect_attempts", "cpk_effect_configuration_refs",
                "cpk_configuration_claims", "cpk_activity_events"):
            where = " AND event_type='step_started'" if table == "cpk_activity_events" else ""
            self.assertEqual(self.connection.execute("SELECT count(*) FROM " + table
                + " WHERE run_id=%s" + where, (competitor.run_id,)).fetchone(), (0,))
        for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
            self.assertEqual(self.connection.execute("SELECT artifact_id,protective,accepted_revision FROM " + table
                + " WHERE run_id=%s ORDER BY artifact_id", (admitted.run_id,)).fetchall(),
                [(ref.artifact_id, True, None) for ref in refs])
        birth = member.original.identity
        self.assertEqual(self.connection.execute("SELECT artifact_id,birth_run_id,birth_activity_id,birth_attempt,"
            "birth_artifact_id,is_birth FROM cpk_effect_configuration_refs WHERE run_id=%s ORDER BY artifact_id",
            (admitted.run_id,)).fetchall(), [(ref.artifact_id, birth.run_id.value, birth.activity_id,
                birth.attempt, ref.artifact_id, False) for ref in refs])
        self.assertEqual(self.connection.execute("SELECT * FROM cpk_configuration_claim_transfers "
            "ORDER BY run_id,activity_id,attempt,artifact_id").fetchall(), transfers)
        with base.unit_of_work() as uow:
            current = uow.stores.configuration_acceptance.read_current_configuration("workspace-a")
        self.assertEqual((current.state, current.pinned_revision), ("complete", accepted.desired_graph_revision))
        self.assertEqual(tuple(binding.ref for binding in current.bindings), refs)
