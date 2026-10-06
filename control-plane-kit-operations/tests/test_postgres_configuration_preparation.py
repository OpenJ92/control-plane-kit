"""#1923 first target batch; new laws at Operations' durable start boundary."""
from dataclasses import replace
from hashlib import sha256
import unittest

from control_plane_kit_core.configuration_instances import (
    ConfigurationInstanceRefCodec, ConfigurationInstanceSelection,
)
from control_plane_kit_core.planning import ActivityId
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
from control_plane_kit_operations.effect_attempt_start import (
    EffectAttemptStartConflict, ExistingAttempt, NewlyStarted,
)
from control_plane_kit_operations.records import OperationsRecordError
from control_plane_kit_operations.receiver_execution_scopes import ReceiverScopeUnavailable
from tests.configuration_preparation_fixture import ConfigurationPreparationFixture


class PostgresConfigurationPreparationTests(ConfigurationPreparationFixture, unittest.TestCase):
    def corrupt_current_authority(self, case):
        """Defensive corruption after genuine zero-slot owner acceptance."""
        receipt = self.configuration_origin
        if case == "pointer-base":
            # Lawful empty origin is still not the approved runtime base.
            origin = self.connection.execute("SELECT initial_graph_id,initial_projection_id "
                "FROM cpk_workspace_initializations WHERE workspace_id=%s", ("workspace-a",)).fetchone()
            self.assertEqual(self.connection.execute("UPDATE cpk_workspaces SET current_graph_id=initial_graph_id, "
                "current_realized_projection_id=initial_projection_id FROM cpk_workspace_initializations "
                "WHERE cpk_workspaces.workspace_id=cpk_workspace_initializations.workspace_id "
                "AND cpk_workspaces.workspace_id=%s RETURNING current_graph_id,current_realized_projection_id",
                ("workspace-a",)).fetchall(), [origin])
            self.assertEqual(origin[0], "graph-initial")
            return
        action = self.connection.execute("SELECT * FROM cpk_operation_actions WHERE action_id=%s",
            (receipt.action.action_id,)).fetchall()
        event = self.connection.execute("SELECT * FROM cpk_activity_events WHERE event_id=%s",
            (receipt.event.event_id,)).fetchall()
        self.assertEqual((len(action), len(event)), (1, 1))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_configuration_accepted_slots "
            "WHERE workspace_id=%s AND pinned_revision=%s", ("workspace-a", receipt.desired_graph_revision)).fetchone(), (0,))
        # The exact header owns immediate FKs to both originals; remove it
        # before either side, preserving the other original byte-for-byte.
        self.assertEqual(self.connection.execute("DELETE FROM cpk_configuration_acceptances "
            "WHERE workspace_id=%s AND pinned_revision=%s RETURNING slot_count",
            ("workspace-a", receipt.desired_graph_revision)).fetchall(), [(0,)])
        if case in ("action", "origin-fallback"):
            self.assertEqual(self.connection.execute("DELETE FROM cpk_operation_actions WHERE action_id=%s RETURNING action_id",
                (receipt.action.action_id,)).fetchall(), [(receipt.action.action_id,)])
        if case in ("event", "origin-fallback"):
            self.assertEqual(self.connection.execute("DELETE FROM cpk_activity_events WHERE event_id=%s RETURNING event_id",
                (receipt.event.event_id,)).fetchall(), [(receipt.event.event_id,)])
        self.assertEqual(self.connection.execute("SELECT * FROM cpk_operation_actions WHERE action_id=%s",
            (receipt.action.action_id,)).fetchall(), [] if case in ("action", "origin-fallback") else action)
        self.assertEqual(self.connection.execute("SELECT * FROM cpk_activity_events WHERE event_id=%s",
            (receipt.event.event_id,)).fetchall(), [] if case in ("event", "origin-fallback") else event)

    def authority_snapshot(self):
        return self.complete_start_snapshot(), tuple((table, self.connection.execute(
            f"SELECT * FROM {table} ORDER BY {order}").fetchall()) for table, order in (
                ("cpk_workspaces", "workspace_id"), ("cpk_workspace_initializations", "workspace_id"),
                ("cpk_operation_actions", "action_id"), ("cpk_activity_events", "event_id"),
                ("cpk_configuration_acceptances", "workspace_id,pinned_revision"),
                ("cpk_configuration_accepted_slots", "workspace_id,pinned_revision,runtime_id,node_id,artifact_id")))

    def test_fresh_start_requires_original_current_authority_even_without_any_history(self):
        for case in ("header", "action", "event", "origin-fallback", "pointer-base"):
            with self.subTest(case=case):
                self.reset_start_truth()
                command = self.configuration_command()
                self.assertEqual(self.protection_rows(), ([], []))
                self.corrupt_current_authority(case)
                before = self.authority_snapshot()
                service, ids = self.start_service_with_sequence("must-not-be-used")
                with self.assertRaises(EffectAttemptStartConflict):
                    service.execute(command)
                self.assertEqual(ids.calls, [])
                self.assertEqual(self.authority_snapshot(), before)

    def test_original_replay_does_not_require_todays_current_authority(self):
        command = self.configuration_command()
        first = self.start_service("configuration-start").execute(command)
        self.corrupt_current_authority("origin-fallback")
        self.expire_claim()
        before = self.authority_snapshot()
        service, ids = self.start_service_with_sequence("must-not-be-used")
        with self.reject_database_observation("original replay sampled database time"):
            replay = service.execute(command)
        self.assertIsInstance(replay, ExistingAttempt)
        self.assertEqual(replay.attempt, first.attempt)
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.authority_snapshot(), before)

    def test_new_start_freezes_every_exact_birth_ref_and_protective_claim(self):
        command = self.configuration_command()
        result = self.start_service("configuration-start").execute(command)
        self.assertIsInstance(result, NewlyStarted)
        refs, claims = self.protection_rows()
        selected = sorted(command.intent.configuration_instances.instances, key=lambda ref: ref.artifact_id)
        expected_keys = [("run-a", "start-api", 1, ref.artifact_id, ref.workspace_id, ref.allocation_id)
                         for ref in selected]
        self.assertEqual(claims, expected_keys)
        self.assertEqual([row[:6] for row in refs], expected_keys)
        self.assertEqual([row[6:] for row in refs], [(
            ref.runtime_id, ref.node_id, ConfigurationInstanceRefCodec().encode_canonical_bytes(ref),
            runtime_effect_intent_fingerprint(command.intent), "configuration-start", True,
            "run-a", "start-api", 1, ref.artifact_id,
            sha256(ConfigurationInstanceRefCodec().encode_canonical_bytes(ref)).hexdigest(),
        ) for ref in selected])
        with self.unit_of_work() as uow:
            original = uow.stores.effect_attempt_intents.get(command.transition.identity)
            self.assertEqual(original.intent, command.intent)
            self.assertEqual(original.original_start_event, result.attempt.original_start_event)

    def test_1950_receiverless_feasibility(self):
        from tests.configuration_cleanup_phase_read_bounds_fixture import ordinary_start_feasibility
        command = self.configuration_command()
        with ordinary_start_feasibility(self, "receiverless") as reports:
            result = self.start_service("configuration-feasibility").execute(command)
        self.assertIsInstance(result, NewlyStarted)
        self.assertEqual(len(reports), 1)
        self.assertIn("after_revalidation", reports[0])
        refs, claims = self.protection_rows()
        self.assertEqual(len(refs), len(command.intent.configuration_instances.instances))
        self.assertEqual(len(claims), len(refs))

    def test_caller_chosen_allocation_is_refused_before_ids_or_durable_writes(self):
        intent = self.intent()
        proposed = intent.configuration_instances.instances
        forged = replace(intent, configuration_instances=ConfigurationInstanceSelection((
            replace(proposed[0], allocation_id="caller-chosen-allocation"), *proposed[1:])))
        before = self.complete_start_snapshot()
        service, ids = self.start_service_with_sequence("must-not-be-used")
        with self.assertRaises(EffectAttemptStartConflict) as caught:
            service.execute(self.configuration_command(forged))
        self.assert_safe_error(caught.exception, "caller-chosen-allocation")
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_public_intent_insert_cannot_bypass_configuration_preparation(self):
        intent = self.intent()
        attempt, evidence = self.intent_attempt(activity_id="start-api", intent=intent)
        before = self.complete_start_snapshot()
        with self.assertRaises(ReceiverScopeUnavailable):
            with self.unit_of_work() as uow:
                uow.stores.execution.add_event(attempt.original_start_event)
                uow.stores.effect_attempt_intents.insert(evidence)
                uow.commit()
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_self_consistent_material_drift_cannot_replace_approved_selection(self):
        original = self.intent()
        material = original.products[0]
        contract = material.product.runtime_contract
        artifact, *rest = contract.configuration_artifacts
        altered = replace(artifact, content='{"selection":"unapproved-material"}')
        changed_product = replace(material.product, runtime_contract=replace(contract,
            configuration_artifacts=(altered, *rest)))
        changed = replace(original, products=(replace(material, product=changed_product),),
            kind=type(original.kind).REALIZE_ACTIVITY, configuration_instances=None)
        changed = replace(changed, kind=original.kind,
            configuration_instances=self.configuration_selection(changed))
        # Both Core material and its new deterministic refs agree. Only the
        # durable owner can discover disagreement with the approved graph.
        command = self.configuration_command(changed)
        before = self.complete_start_snapshot()
        service, ids = self.start_service_with_sequence("must-not-be-used")
        with self.assertRaises(EffectAttemptStartConflict) as caught:
            service.execute(command)
        self.assert_safe_error(caught.exception, "unapproved-material")
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_private_intent_writer_without_owner_guard_refuses_configuration(self):
        attempt, evidence = self.intent_attempt(activity_id="start-api", intent=self.intent())
        before = self.complete_start_snapshot()
        with self.assertRaises(OperationsRecordError):
            with self.unit_of_work() as uow:
                uow.stores.execution.add_event(attempt.original_start_event)
                uow.stores.effect_attempt_intents._insert(evidence)
                uow.commit()
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_existing_configured_node_without_b2_truth_cannot_fall_back_to_birth(self):
        self.configuration_existing_node = True
        self.reset_start_truth()
        # Explicit malformed snapshot, not evidence of a lawful configured
        # genesis. Keep the direct guarded-start refusal and zero-ID law.
        with self.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get("workspace-a")
            graph = uow.stores.realized_graphs.get(workspace.current_realized_projection_id)
        from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
        self.assertEqual(workspace.current_graph_id, "graph-current")
        self.assertTrue(DEFAULT_GRAPH_CODEC.decode(graph.graph_descriptor).nodes["api"].configuration_artifacts)
        for table in ("cpk_workspace_initializations", "cpk_configuration_acceptances", "cpk_configuration_accepted_slots"):
            self.assertEqual(self.connection.execute(f"SELECT count(*) FROM {table} WHERE workspace_id=%s",
                ("workspace-a",)).fetchone(), (0,))
        command = self.configuration_command()
        before = self.complete_start_snapshot()
        service, ids = self.start_service_with_sequence("must-not-be-used")
        with self.assertRaises(EffectAttemptStartConflict):
            service.execute(command)
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_another_approved_activity_cannot_remint_an_unresolved_slot(self):
        self.configuration_history_count = 2
        self.reset_start_truth()
        original = self.configuration_command()
        self.start_service("unresolved-configuration-start").execute(original)
        other = replace(original.intent, activity_id=ActivityId("history-use-001"))
        other = replace(other, configuration_instances=self.configuration_selection(other))
        self.assertNotEqual(other.configuration_instances, original.intent.configuration_instances)
        command = self.command(intent=other, transition=self.transition(
            identity=self.identity(activity_id="history-use-001"), intent=other))
        before = self.complete_start_snapshot()
        service, ids = self.start_service_with_sequence("must-not-be-used")
        with self.assertRaises(EffectAttemptStartConflict):
            service.execute(command)
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.complete_start_snapshot(), before)

    def test_exact_replay_keeps_original_refs_claims_and_attempt_without_ids(self):
        command = self.configuration_command()
        first = self.start_service("configuration-start").execute(command)
        self.expire_claim()
        before = self.attempt_snapshot(), self.protection_rows()
        service, ids = self.start_service_with_sequence("must-not-be-used")
        with self.reject_database_observation("original replay sampled database time"):
            replay = service.execute(command)
        self.assertIsInstance(replay, ExistingAttempt)
        self.assertEqual(replay.attempt, first.attempt)
        self.assertEqual(ids.calls, [])
        self.assertEqual((self.attempt_snapshot(), self.protection_rows()), before)
