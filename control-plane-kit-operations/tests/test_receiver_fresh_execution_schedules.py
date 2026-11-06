"""#1898/#1904 real owners serialize admission, clearance and native start."""

from dataclasses import replace
import unittest

from control_plane_kit_core.operations.lifecycle import ExecutionRequestStatus
from control_plane_kit_operations.admission import ExecutionAdmissionCommandService, ExecutionAdmissionConflict
from control_plane_kit_operations.effect_attempt_start import EffectAttemptStartError, NewlyStarted
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.planning import DesiredGraphCommandError
from control_plane_kit_operations.lifecycle import PauseActivityRun
from control_plane_kit_operations.workflows import IdempotencyKey
from control_plane_kit_operations.records import GraphVersionRecord, RealizedGraphProjectionRecord
from control_plane_kit_operations.receiver_lifecycle import derive_receiver_bindings
from tests.lifecycle_lock_fixture import LifecycleLockFixture
from tests.postgres_effect_attempt_coordinator_fixture import GeneratedIds
from tests.receiver_fresh_execution_fixture import ReceiverFreshExecutionFixture
from tests.test_execution_admission import Sequence


def lifecycle_acquired(query, parameters):
    return "pg_advisory_xact_lock" in query and parameters == ("receiver-lifecycle:workspace-a",)


class ReceiverFreshExecutionScheduleTests(ReceiverFreshExecutionFixture, LifecycleLockFixture, unittest.TestCase):
    def original_admission_records(self):
        with self.unit_of_work() as uow:
            history = uow.stores.activity_history
            return (history.get_plan("scope-plan-a"),
                history.get_approval_request("scope-approval-a"),
                history.approval_decision_for_request("scope-approval-a"))

    def assert_only_winner_inserts(self, before, after, winners):
        for table, original in before.items():
            if table not in winners:
                self.assertEqual(after[table], original, table)
                continue
            key, identities = winners[table]
            inserted = [row for row in after[table] if row[0][key] in identities]
            self.assertEqual(len(inserted), len(identities), table)
            self.assertEqual({row[0][key] for row in inserted}, set(identities), table)
            self.assertEqual([row for row in after[table] if row[0][key] not in identities], original, table)

    def test_execution_admission_first_makes_waiting_receiver_selection_observe_queued_scope(self):
        admission = self.command(key="race-execute")
        selection = self.receiver_command()
        originals = self.original_admission_records()
        before = self.graph_truth() | self.execution_truth()
        self.assertEqual(before["cpk_execution_requests"], [])
        self.assertEqual(before["cpk_execution_receiver_scopes"], [])
        first, second = self.opposing_commands(
            lambda factory: ExecutionAdmissionCommandService(factory, clock=lambda: "2026-07-22T12:04:00Z",
                id_factory=Sequence("execution-a", "race-admission-action")).execute(admission),
            lambda factory: self.desired_service(factory).execute(selection), lifecycle_acquired)
        admitted = first.result(timeout=1)
        with self.assertRaises(DesiredGraphCommandError):
            second.result(timeout=1)
        self.assertFalse(admitted.replayed)
        self.assertEqual(admitted.request.identity.request_id, "execution-a")
        self.assertEqual(admitted.action.action_id, "race-admission-action")
        self.assertEqual(admitted.request.identity.plan_id, originals[0].plan_id)
        self.assertEqual((admitted.request.approval_request_id, admitted.request.approval_decision_id),
            (originals[1].request_id, originals[2].decision_id))
        self.assertIs(admitted.request.status, ExecutionRequestStatus.QUEUED)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.execution.get_request("execution-a"), admitted.request)
            self.assertEqual(uow.stores.activity_history.action_for_idempotency(
                admission.session_id, admission.idempotency_key.value), admitted.action)
        derived = self.require_scopes().derive_execution_receiver_scopes(*self.source())
        self.assertEqual(self.scope_rows(), [(i, scope.scope_kind, scope.runtime_id, scope.node_id)
            for i, scope in enumerate(derived.scopes)])
        self.assertEqual(self.scope_header(), (len(derived.scopes), derived.source_digest))
        self.assertEqual(len(derived.scopes), 1)
        self.assert_only_winner_inserts(before, self.graph_truth() | self.execution_truth(), {
            "cpk_execution_requests": ("request_id", ("execution-a",)),
            "cpk_execution_receiver_scopes": ("request_id", ("execution-a",)),
            "cpk_operation_actions": ("action_id", (admitted.action.action_id,)),
        })
        self.assertIsNone(self.receiver_origin())
        self.assertEqual(self.original_admission_records(), originals)
        self.assert_advisory_available("receiver-lifecycle:workspace-a", available=True)

    def test_receiver_selection_first_makes_waiting_original_execution_admission_refuse_stale_pins(self):
        admission = self.command(key="race-execute")
        selection = replace(self.receiver_command(), proposed_graph_id="race-receiver-graph")
        originals = self.original_admission_records()
        before = self.graph_truth() | self.execution_truth()
        self.assertEqual(before["cpk_execution_requests"], [])
        self.assertEqual(before["cpk_execution_receiver_scopes"], [])
        first, second = self.opposing_commands(
            lambda factory: self.desired_service(factory).execute(selection),
            lambda factory: ExecutionAdmissionCommandService(factory, clock=lambda: "2026-07-22T12:04:00Z",
                id_factory=lambda: self.fail("stale admission allocated an identity")).execute(admission),
            lifecycle_acquired)
        selected = first.result(timeout=1)
        with self.assertRaises(ExecutionAdmissionConflict):
            second.result(timeout=1)
        self.assertFalse(selected.replayed)
        self.assertEqual(selected.graph_version_id, "race-receiver-graph")
        self.assertEqual(selected.desired_graph_revision, selection.expected_desired_graph_revision + 1)
        expected_graph = GraphVersionRecord.from_graph(graph_id=selected.graph_version_id,
            workspace_id=selection.workspace_id, version=selected.graph_version, graph=selection.graph,
            created_by=selection.actor_id, created_at=selected.action.created_at)
        expected_projection = RealizedGraphProjectionRecord.identity_for_authored(authored_record=expected_graph)
        self.assertEqual(selected.desired_realized_projection_id, expected_projection.projection_id)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.graphs.get(selected.graph_version_id), expected_graph)
            self.assertEqual(uow.stores.realized_graphs.get(selected.desired_realized_projection_id), expected_projection)
            self.assertEqual(uow.stores.activity_history.action_for_idempotency(
                selection.session_id, selection.idempotency_key.value), selected.action)
            bindings = uow.stores.graphs.receiver_bindings(selection.workspace_id,
                selected.graph_version_id, selected.desired_realized_projection_id)
        self.assertEqual(bindings, derive_receiver_bindings(selection.workspace_id,
            selected.graph_version_id, selected.desired_realized_projection_id, expected_projection.graph_descriptor))
        self.assertEqual(tuple(binding.receiver_id for binding in bindings), ("a" * 32,))
        origin = self.receiver_origin()
        self.assertEqual((origin.introducing_graph_id, origin.introducing_realized_projection_id,
            origin.introducing_action_id, origin.introducing_session_id),
            (selected.graph_version_id, selected.desired_realized_projection_id,
             selected.action.action_id, selection.session_id))
        self.assertIsNone(origin.first_accepted_action_id)
        self.assertIsNone(origin.retired_action_id)
        self.assertEqual(selected.action.payload["receiver_lifecycle"], selection.receiver_lifecycle.descriptor())
        self.assertEqual(len(before["cpk_workspaces"]), 1)
        expected = dict(before)
        expected["cpk_workspaces"] = [(dict(before["cpk_workspaces"][0][0],
            desired_graph_id=selected.graph_version_id,
            desired_realized_projection_id=selected.desired_realized_projection_id,
            desired_graph_revision=selected.desired_graph_revision),)]
        self.assert_only_winner_inserts(expected, self.graph_truth() | self.execution_truth(), {
            "cpk_graph_versions": ("graph_id", (selected.graph_version_id,)),
            "cpk_realized_graph_projections": ("projection_id", (selected.desired_realized_projection_id,)),
            "cpk_graph_receiver_introductions": ("receiver_id", (origin.receiver_id,)),
            "cpk_graph_receiver_bindings": ("receiver_id", (origin.receiver_id,)),
            "cpk_operation_actions": ("action_id", (selected.action.action_id,)),
        })
        self.assertEqual(self.original_admission_records(), originals)
        self.assert_advisory_available("receiver-lifecycle:workspace-a", available=True)

    def start_with(self, factory, command):
        return EffectAttemptStartService(factory, id_factory=GeneratedIds("schedule-start")).execute(command)

    def pause_then_cancel(self, claimed, start):
        self.lifecycle("schedule-pause", "schedule-pause-action").execute(PauseActivityRun(
            claimed.run.run_id, start.authority, start.fence, IdempotencyKey("schedule-pause")))
        return self.cancel(claimed)

    def test_clearance_and_receiver_reuse_first_refuses_waiting_old_native_activation(self):
        self.admit()
        claimed = self.ready_run()
        old_start = self.native_start_command(claimed)
        cancelled = self.pause_then_cancel(claimed, old_start)
        competing = self.receiver_command()
        before = self.execution_truth()
        first, second = self.opposing_commands(
            lambda factory: self.desired_service(factory).execute(competing),
            lambda factory: self.start_with(factory, old_start), lifecycle_acquired)
        introduced = first.result(timeout=1)
        with self.assertRaises(EffectAttemptStartError):
            second.result(timeout=1)
        origin = self.receiver_origin()
        self.assertEqual(origin.introducing_action_id, introduced.action.action_id)
        self.assertIsNone(origin.first_accepted_action_id)
        after = self.execution_truth()
        # Only the real desired-selection action may be added. The refused
        # activation cannot add an event, attempt, intent, request or run.
        for table in before.keys() - {"cpk_operation_actions"}:
            self.assertEqual(after[table], before[table])
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.execution.get_event(cancelled.event.event_id), cancelled.event)

    def test_native_start_first_blocks_waiting_reuse_and_cancellation_or_expiry_cannot_clear_it(self):
        self.admit()
        claimed = self.ready_run()
        start = self.native_start_command(claimed)
        competing = self.receiver_command()
        before = self.graph_truth()
        first, second = self.opposing_commands(
            lambda factory: self.start_with(factory, start),
            lambda factory: self.desired_service(factory).execute(competing), lifecycle_acquired)
        started = first.result(timeout=1)
        self.assertIsInstance(started, NewlyStarted)
        with self.assertRaises(DesiredGraphCommandError):
            second.result(timeout=1)
        self.assertEqual(self.graph_truth(), before)
        self.assert_advisory_available("receiver-lifecycle:workspace-a", available=True)
        self.pause_then_cancel(claimed, start)
        before = self.graph_truth()
        with self.assertRaises(DesiredGraphCommandError):
            self.desired_service().execute(competing)
        self.assertEqual(self.graph_truth(), before)
        # Explicit retained expiry premise; it grants no replay/redispatch.
        original = self.connection.execute("SELECT lease_expires_at FROM cpk_execution_requests "
            "WHERE request_id='execution-a'").fetchone()[0]
        try:
            self.connection.execute("UPDATE cpk_execution_requests SET lease_expires_at=claimed_at "
                "WHERE request_id='execution-a'")
            with self.assertRaises(DesiredGraphCommandError):
                self.desired_service().execute(competing)
            self.assertEqual(self.graph_truth(), before)
            with self.unit_of_work() as uow:
                self.assertEqual(uow.stores.effect_attempts.get(start.transition.identity), started.attempt)
                self.assertEqual(uow.stores.effect_attempt_intents.get(start.transition.identity).intent, start.intent)
        finally:
            self.connection.execute("UPDATE cpk_execution_requests SET lease_expires_at=%s "
                "WHERE request_id='execution-a'", (original,))
