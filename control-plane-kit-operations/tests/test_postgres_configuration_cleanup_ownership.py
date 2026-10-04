"""D2B retained-history/read/exclusion laws; no supported cleanup execution."""
from dataclasses import FrozenInstanceError, replace
import unittest
from unittest import mock

import psycopg

from control_plane_kit_core.configuration_instances import (
    ConfigurationCleanupOutcome, ConfigurationCleanupOutcomeSet,
    ConfigurationCleanupStatus, ConfigurationCleanupReason,
)
from control_plane_kit_core.operations import (
    EffectAttemptIdentity, EffectAttemptStatus, EffectAttemptTransition, EffectAttemptTransitionKind, RunId,
)
from control_plane_kit_core.runtime_effect_observation import RuntimeEffectIntentSource, runtime_effect_intent_fingerprint
from control_plane_kit_core.planning import NodeTarget, ReconcileNode, StartRuntime, RuntimeTarget
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_operations.advancement import (
    CurrentGraphAdvancementCommandService, CurrentGraphAdvancementConflict,
)
from control_plane_kit_operations.postgres import install_schema
from control_plane_kit_operations.postgres.current_data_validation import validate_current_rows, CurrentRowDrift
from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead, _Unavailable, _joined_read
from control_plane_kit_operations.records import OperationsRecordError
from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint
from control_plane_kit_operations.coordinator import ExecutionCoordinatorConflict
from control_plane_kit_operations.postgres.configuration_acceptance_store import ConfigurationAcceptanceStore
from control_plane_kit_operations.postgres.configuration_preparation_store import ConfigurationPreparationStore
from control_plane_kit_operations.effect_attempt_start import StartEffectAttempt, EffectAttemptStartConflict
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from tests import test_execution_coordinator as coordinator_fixture
from tests.configuration_cleanup_history_fixture import ConfigurationCleanupHistoryFixture, TABLES, key, insert


class PostgresConfigurationCleanupOwnershipTests(ConfigurationCleanupHistoryFixture, unittest.TestCase):
    def test_supported_runtime_fixture_keeps_real_ordinary_completion_and_pins(self):
        # Baseline control executes even before B's owner/schema exist.
        with self.unit_of_work() as uow:
            self.assertIsNotNone(uow.stores.configuration_completions.get(self.original.identity))
            workspace = uow.stores.workspaces.get("workspace-a")
            for graph_id in (workspace.current_graph_id, workspace.desired_graph_id):
                graph = DEFAULT_GRAPH_CODEC.decode(uow.stores.graphs.get(graph_id).graph_descriptor)
                self.assertEqual(graph.runtimes["runtime-a"].authority_ref, self.runtime_authority_ref)
            self.assertEqual(uow.stores.runtime_authorities.get("workspace-a", self.runtime_authority_ref),
                self.base.runtime_registration)
        plan = self.publish().plan_record
        self.assertEqual(plan.plan.activities[0].operation.instances, tuple(sorted(self.refs, key=lambda ref: ref.allocation_id)))

    def test_absent_reservation_is_none_without_claiming_permission(self):
        before = self.truth()
        with self.unit_of_work() as uow:
            self.assertIsNone(self.ownership(uow.stores).get(EffectAttemptIdentity(RunId("missing"), "cleanup", 1)))
        self.assertEqual(self.truth(), before)

    def test_retained_started_reservation_proves_whole_closure_and_zero_protectors(self):
        identity = self.retain_cleanup()
        before = self.cleanup_snapshot()
        record = self.read_retained()
        self.assertEqual((record.identity, record.workspace_id, record.status),
            (identity, "workspace-a", EffectAttemptStatus.STARTED))
        self.assertEqual(tuple(member.ref for member in record.members),
            tuple(sorted(self.refs, key=lambda ref: ref.allocation_id)))
        self.assertEqual(record.completions, (self.retained_completion,))
        self.assertEqual({claim.ref for claim in record.claims}, set(self.refs))
        self.assertIsNone(record.outcomes)
        self.assertIsNone(record.outcome_fingerprint)
        self.assertEqual(record.registration_id, self.base.runtime_registration.registration_id)
        with self.assertRaises(FrozenInstanceError):
            record.workspace_id = "forged"
        with self.unit_of_work() as uow:
            with _joined_read(uow.stores.connection) as read:
                for ref in self.refs:
                    protective = uow.stores.configuration_preparation._protective_allocation_evidence(ref, read)
                    self.assertEqual(protective.birth.ref, ref)
                    self.assertEqual(protective.claims, ())
                self.assertEqual(uow.stores.configuration_completions.get(self.original.identity), self.retained_completion)
        install_schema(self.connection)
        self.assertEqual(self.cleanup_snapshot(), before)

    def test_full_reader_and_current_verifier_detect_both_locators_reset(self):
        self.retain_cleanup()
        before = self.cleanup_snapshot()
        with self.unit_of_work() as uow:
            for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
                uow.stores.connection.execute("UPDATE " + table
                    + " SET cleanup_run_id=NULL,cleanup_activity_id=NULL,cleanup_attempt=NULL WHERE run_id='run-config'")
            with self.assertRaises(OperationsRecordError):
                self.ownership(uow.stores).get(self.retained_intent.identity)
            with self.assertRaises(CurrentRowDrift):
                validate_current_rows(uow.stores.connection)
        self.assertEqual(self.cleanup_snapshot(), before)

    def test_either_one_sided_locator_refuses_fresh_discovery_with_warm_material(self):
        self.retain_cleanup()
        before = self.cleanup_snapshot()
        for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
            with self.subTest(table=table), self.unit_of_work() as uow:
                with _joined_read(uow.stores.connection) as read:
                    store = uow.stores.configuration_preparation
                    ref = self.refs[0]
                    self.assertEqual(store._protective_allocation_evidence(ref, read).claims, ())
                    prior = read.used
                    uow.stores.connection.execute("UPDATE " + table
                        + " SET cleanup_run_id=NULL,cleanup_activity_id=NULL,cleanup_attempt=NULL "
                        "WHERE (run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s)",
                        (*key(self.original.identity), ref.artifact_id))
                    with self.assertRaises(_Unavailable):
                        store._protective_allocation_evidence(ref, read)
                    self.assertGreater(read.used.statements, prior.statements)
                    with self.assertRaises(_Unavailable):
                        store._node_history(ref, read)
            self.assertEqual(self.cleanup_snapshot(), before)

    def test_exact_completion_commitment_and_typed_closure_parent_cannot_retarget(self):
        self.retain_cleanup()
        before = self.cleanup_snapshot()
        cases = (
            ("UPDATE cpk_configuration_invocation_completions SET selection_fingerprint=%s WHERE run_id='run-config'", ("f" * 64,)),
            ("UPDATE cpk_configuration_claim_closures SET allocation_id=%s WHERE artifact_id=%s",
                ("unowned", self.refs[0].artifact_id)),
        )
        for sql, values in cases:
            with self.subTest(sql=sql), self.assertRaises(psycopg.errors.ForeignKeyViolation):
                with self.unit_of_work() as uow:
                    uow.stores.connection.execute(sql, values)
                    uow.commit()
            self.assertEqual(self.cleanup_snapshot(), before)

    def mixed_outcomes(self):
        refs = tuple(sorted(self.refs, key=lambda ref: ref.allocation_id))
        self.assertGreaterEqual(len(refs), 2)
        return ConfigurationCleanupOutcomeSet(tuple(ConfigurationCleanupOutcome(ref,
            ConfigurationCleanupStatus.REMOVED if index == 0 else ConfigurationCleanupStatus.UNKNOWN,
            None if index == 0 else ConfigurationCleanupReason.PROVIDER_UNCERTAIN)
            for index, ref in enumerate(refs)))

    def test_canonical_mixed_terminal_requires_exact_total_member_evidence(self):
        self.retain_cleanup()
        outcomes = self.mixed_outcomes()
        terminal = self.retain_cleanup_result(outcomes)
        record = self.read_retained()
        self.assertEqual(record.outcomes, outcomes)
        self.assertEqual(record.outcome_fingerprint, terminal.attempt.state.outcome_fingerprint)
        self.assertIs(record.status, EffectAttemptStatus.UNCERTAIN)
        install_schema(self.connection)
        before = self.cleanup_snapshot()
        cases = (
            ("DELETE FROM cpk_configuration_cleanup_member_outcomes WHERE allocation_id=%s", (self.refs[0].allocation_id,)),
            ("UPDATE cpk_configuration_cleanup_member_outcomes SET status='unknown',reason='not-attempted' "
                "WHERE allocation_id=%s", (outcomes.outcomes[0].ref.allocation_id,)),
        )
        for sql, values in cases:
            with self.subTest(sql=sql), self.unit_of_work() as uow:
                uow.stores.connection.execute(sql, values)
                with self.assertRaises(OperationsRecordError):
                    self.ownership(uow.stores).get(self.retained_intent.identity)
                with self.assertRaises(CurrentRowDrift):
                    validate_current_rows(uow.stores.connection)
            self.assertEqual(self.cleanup_snapshot(), before)
        self.assertEqual(len(self.connection.execute("SELECT 1 FROM " + TABLES[1]).fetchall()), len(self.refs))

    def test_generic_observation_retains_exclusion_without_member_retirement(self):
        self.retain_cleanup()
        terminal = self.retain_generic_observation()
        record = self.read_retained()
        self.assertIsNone(record.outcomes)
        self.assertEqual(record.outcome_fingerprint, terminal.attempt.state.outcome_fingerprint)
        self.assertEqual(self.connection.execute("SELECT 1 FROM " + TABLES[4]).fetchall(), [])
        self.assertEqual(len(self.connection.execute("SELECT 1 FROM " + TABLES[1]).fetchall()), len(self.refs))
        install_schema(self.connection)
        before = self.cleanup_snapshot()
        with self.unit_of_work() as uow:
            ref = self.refs[0]
            insert(uow.stores.connection, TABLES[4],
                ("cleanup_run_id", "cleanup_activity_id", "cleanup_attempt", "workspace_id", "allocation_id",
                 "request_fingerprint", "outcome_fingerprint", "status", "reason"),
                (*key(self.retained_intent.identity), ref.workspace_id, ref.allocation_id,
                 self.retained_intent.request_fingerprint, terminal.attempt.state.outcome_fingerprint, "removed", None))
            with self.assertRaises(OperationsRecordError):
                self.ownership(uow.stores).get(self.retained_intent.identity)
            with self.assertRaises(CurrentRowDrift):
                validate_current_rows(uow.stores.connection)
        self.assertEqual(self.cleanup_snapshot(), before)

    def test_whole_invocation_cannot_lose_one_claim_even_with_consistent_counts(self):
        with self.unit_of_work() as uow:
            self.ownership(uow.stores)
        self.member.advance()
        command, _ = self.later_use("second-completed-use")
        operator = self.carry_operator()
        operator.advance(command)
        from control_plane_kit_core.planning import RemoveNodeResource
        runtime = replace(operator.graph.runtimes["runtime-a"], children=())
        departed = replace(operator.graph, nodes={}, runtimes={"runtime-a": runtime})
        departure = operator.prepare("depart-completed-uses", "graph-departed", RemoveNodeResource(NodeTarget("api")),
            graph=departed, expected_claims=self.member.protective_claims())
        operator.advance(departure)
        self.retain_cleanup()
        self.assertEqual(len(self.retained_completions), 2)
        self.assertEqual(len(self.retained_claims), 2 * len(self.refs))
        self.assertIsNotNone(self.read_retained())
        before = self.cleanup_snapshot()
        source, ref = self.retained_claims[-1]
        with self.unit_of_work() as uow:
            connection = uow.stores.connection
            for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
                connection.execute("UPDATE " + table
                    + " SET cleanup_run_id=NULL,cleanup_activity_id=NULL,cleanup_attempt=NULL "
                    "WHERE (run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s)", (*key(source), ref.artifact_id))
            connection.execute("DELETE FROM cpk_configuration_claim_closures "
                "WHERE (run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s)", (*key(source), ref.artifact_id))
            connection.execute("UPDATE cpk_configuration_cleanup_reservations SET claim_count=claim_count-1")
            connection.execute("SET CONSTRAINTS ALL IMMEDIATE")
            with self.assertRaises(OperationsRecordError):
                self.ownership(uow.stores).get(self.retained_intent.identity)
            with self.assertRaises(CurrentRowDrift):
                validate_current_rows(connection)
        self.assertEqual(self.cleanup_snapshot(), before)

    def test_reserved_installed_slots_refuse_real_advancement_before_ids(self):
        self.retain_cleanup()
        before = self.cleanup_snapshot()
        with self.assertRaises(CurrentGraphAdvancementConflict):
            CurrentGraphAdvancementCommandService(self.unit_of_work,
                clock=lambda: self.fail("excluded advancement sampled clock"),
                id_factory=lambda: self.fail("excluded advancement allocated ID")).execute(self.member.command())
        self.assertEqual(self.cleanup_snapshot(), before)

    def test_historical_read_survives_revoked_registration_and_changed_desired(self):
        self.retain_cleanup()
        expected = self.read_retained()
        self.connection.execute("UPDATE cpk_runtime_authorities SET status='revoked' WHERE registration_id=%s",
            (self.base.runtime_registration.registration_id,))
        with self.unit_of_work() as uow:
            uow.stores.workspaces.set_desired_graph("workspace-a", "graph-current")
            uow.commit()
        before = self.cleanup_snapshot()
        self.assertEqual(self.read_retained(), expected)
        install_schema(self.connection)
        self.assertEqual(self.cleanup_snapshot(), before)

    def test_reserved_carried_slots_refuse_real_advancement_before_ids(self):
        self.prepare_recorded_cleanup()
        self.member.advance()
        operator = self.carry_operator()
        from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
        from control_plane_kit_core.topology import compile_topology
        extra = compile_topology(DeploymentTopology("extra", DockerRuntime(runtime_id="runtime-b")))
        command = operator.prepare("carry-excluded", "graph-carry-excluded", StartRuntime(RuntimeTarget("runtime-b")),
            graph=operator.graph.add_runtime(extra.runtimes["runtime-b"]))
        with self.unit_of_work() as uow:
            self.insert_recorded_cleanup(uow.stores)
            uow.commit()
        before = self.cleanup_snapshot()
        with self.assertRaises(CurrentGraphAdvancementConflict):
            CurrentGraphAdvancementCommandService(self.unit_of_work,
                clock=lambda: self.fail("excluded carry sampled clock"),
                id_factory=lambda: self.fail("excluded carry allocated ID")).execute(command)
        self.assertEqual(self.cleanup_snapshot(), before)

    def test_zero_protectors_never_allow_real_ordinary_reuse(self):
        self.prepare_recorded_cleanup()
        self.member.advance()
        operator = self.carry_operator()
        command = operator.admit("excluded-reuse", "graph-excluded-reuse", ReconcileNode(NodeTarget("api")),
            graph=operator.graph)
        with self.unit_of_work() as uow:
            self.insert_recorded_cleanup(uow.stores)
            uow.commit()
        before = self.cleanup_snapshot()
        engine = self.base.engine
        adapter = coordinator_fixture.RecordingAdapter(engine.tracker,
            lambda *_: self.fail("excluded ordinary use reached the simulated adapter"))
        with self.assertRaises(ExecutionCoordinatorConflict):
            engine.coordinator(adapter).execute(replace(engine.command(generation=command.fence.generation,
                idempotency_key="execute-excluded-reuse"), run_id=command.run_id))
        # Coordinator may retain its own bounded command receipt; no original
        # effect, membership or cleanup truth may change on this refusal.
        self.assertEqual(adapter.calls, [])
        self.assertEqual(self.cleanup_snapshot(), before)

    def test_same_uow_reservation_invalidates_prepared_acceptance_before_cas(self):
        self.prepare_recorded_cleanup()
        before = self.cleanup_snapshot()
        actual = ConfigurationAcceptanceStore._prepare
        injected = []

        def then_record(store, stores, *args, **kwargs):
            prepared = actual(store, stores, *args, **kwargs)
            self.insert_recorded_cleanup(stores)
            injected.append(True)
            return prepared

        ids = iter(("stale-acceptance-event", "stale-acceptance-action"))
        with mock.patch.object(ConfigurationAcceptanceStore, "_prepare", then_record):
            with self.assertRaises(CurrentGraphAdvancementConflict):
                CurrentGraphAdvancementCommandService(self.unit_of_work,
                    clock=lambda: "2026-10-03T12:00:00Z", id_factory=lambda: next(ids)).execute(self.member.command())
        self.assertEqual(injected, [True])
        self.assertEqual(self.cleanup_snapshot(), before)

    def test_full_read_joins_existing_ledger_instead_of_resetting_capacity(self):
        self.retain_cleanup()
        before = self.cleanup_snapshot()
        with self.unit_of_work() as uow:
            with _joined_read(uow.stores.connection) as read:
                accounting = read.accounting
                read.used = ConfigurationEvidenceFootprint(4095, 0, 0, 0)
                with self.assertRaises(OperationsRecordError):
                    self.ownership(uow.stores).get(self.retained_intent.identity)
                self.assertIs(read.accounting, accounting)
                self.assertGreaterEqual(read.used.records, 4095)
        self.assertEqual(self.cleanup_snapshot(), before)

    def test_same_uow_reservation_invalidates_issued_ordinary_start(self):
        self.prepare_recorded_cleanup()
        self.member.advance()
        operator = self.carry_operator()
        command = operator.admit("stale-start", "graph-stale-start", ReconcileNode(NodeTarget("api")),
            graph=operator.graph)
        with self.unit_of_work() as uow:
            plan = uow.stores.activity_history.get_plan(command.plan_id)
        activity = plan.plan.activities[0]
        identity = EffectAttemptIdentity(RunId(command.run_id), activity.activity_id.value, 1)
        intent = replace(self.original.intent, activity_id=activity.activity_id, operation=activity.operation,
            source=RuntimeEffectIntentSource("workspace-a", "request-stale-start", identity.run_id,
                plan.plan_id, plan.base_graph_id, plan.desired_graph_id))
        start = StartEffectAttempt("request-stale-start", EffectAttemptTransition(
            EffectAttemptTransitionKind.STARTED, identity, request_fingerprint=runtime_effect_intent_fingerprint(intent)),
            intent, self.base.engine.authority(), command.fence)
        before = self.cleanup_snapshot()
        actual = ConfigurationPreparationStore._prepare
        injected = []

        def then_record(store, stores, *args, **kwargs):
            prepared = actual(store, stores, *args, **kwargs)
            self.insert_recorded_cleanup(stores)
            injected.append(True)
            return prepared

        with mock.patch.object(ConfigurationPreparationStore, "_prepare", then_record):
            with self.assertRaises(EffectAttemptStartConflict):
                EffectAttemptStartService(self.unit_of_work, id_factory=lambda: "stale-start-event").execute(start)
        self.assertEqual(injected, [True], "must reach real issued preparation before recording exclusion")
        self.assertEqual(self.cleanup_snapshot(), before)


if __name__ == "__main__":
    unittest.main()
