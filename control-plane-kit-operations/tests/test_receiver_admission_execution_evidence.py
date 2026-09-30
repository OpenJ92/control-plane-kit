"""#1903 consumes C1 evidence through real history, without reproducing its predicate."""

from datetime import datetime, timezone
import unittest
import uuid

from control_plane_kit_core.planning import NodeTarget, StartNode
from control_plane_kit_operations import receiver_lifecycle
from control_plane_kit_operations.planning import DesiredGraphCommandError, DesiredGraphCommandService, SetDesiredGraph
from control_plane_kit_operations.postgres.temporal import decode_postgres_timestamp
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.receiver_execution_scope_fixture import ReceiverExecutionScopeFixture
from tests.receiver_storage_fixture import ReceiverStorageFixture


class ReceiverAdmissionExecutionEvidenceTests(ReceiverExecutionScopeFixture, unittest.TestCase):
    receiver_graph = ReceiverStorageFixture.receiver_graph

    def receiver_command(self, *, node="app"):
        value = getattr(receiver_lifecycle, "ReceiverLifecycleExpectation", None)
        self.assertTrue(callable(value), "#1903 missing five-pin ReceiverLifecycleExpectation")
        with self.unit_of_work() as uow:
            current = uow.stores.workspaces.get("workspace-a")
        pins = value(current_graph_id=current.current_graph_id,
            current_realized_projection_id=current.current_realized_projection_id,
            desired_graph_id=current.desired_graph_id,
            desired_realized_projection_id=current.desired_realized_projection_id,
            desired_graph_revision=current.desired_graph_revision)
        return SetDesiredGraph("session-a", "workspace-a", "operator-a", self.receiver_graph(node_id=node)[0],
            current.desired_graph_id, IdempotencyKey("receiver-admission"),
            current.desired_realized_projection_id, current.desired_graph_revision,
            receiver_lifecycle=pins)

    def desired_service(self):
        return DesiredGraphCommandService(self.unit_of_work,
            clock=lambda: decode_postgres_timestamp(datetime.now(timezone.utc)), id_factory=lambda: uuid.uuid4().hex)

    def truth(self):
        return {table: self.connection.execute("SELECT to_jsonb(t) FROM " + table
            + " AS t ORDER BY to_jsonb(t)::text").fetchall() for table in (
                "cpk_workspaces", "cpk_graph_versions", "cpk_realized_graph_projections",
                "cpk_graph_receiver_introductions", "cpk_graph_receiver_bindings", "cpk_operation_actions")}

    def assert_refused(self, disposition, command=None):
        module = self.require_scopes()
        self.assertEqual(self.evidence(module.ExecutionReceiverScope("docker", "app")).disposition, disposition)
        command = self.receiver_command() if command is None else command
        before = self.truth()
        with self.assertRaises(DesiredGraphCommandError) as captured:
            self.desired_service().execute(command)
        self.assertEqual(self.truth(), before)
        self.assertLessEqual(len(str(captured.exception)), 512)
        self.assertNotIn("execution-a", str(captured.exception))

    def test_queued_legacy_node_effect_conflicts_with_fresh_receiver_identity(self):
        command = self.receiver_command()
        self.admit()
        self.assert_refused("conflict", command)

    def test_exact_no_dispatch_cancellation_still_requires_c3_fresh_gate_closure(self):
        command = self.receiver_command()
        self.admit()
        self.cancel(self.claim())
        self.assert_refused("requires-fresh-gate-closure", command)

    def test_unavailable_original_scope_evidence_refuses_without_partial_admission(self):
        command = self.receiver_command()
        self.admit()
        original = self.connection.execute("SELECT receiver_scope_digest FROM cpk_execution_requests "
            "WHERE request_id='execution-a'").fetchone()[0]
        try:
            self.connection.execute("UPDATE cpk_execution_requests SET receiver_scope_digest=%s "
                "WHERE request_id='execution-a'", ("0" * 64,))
            self.assert_refused("unavailable", command)
        finally:
            # This negative uses the shared schema. Restore only its exact
            # premise even on assertion failure; schema re-entry must stay strict.
            self.connection.execute("UPDATE cpk_execution_requests SET receiver_scope_digest=%s "
                "WHERE request_id='execution-a'", (original,))

    def test_capacity_refuses_instead_of_treating_bounded_prefix_as_complete(self):
        command = self.receiver_command()
        for index in range(65):
            self.admit_operations("capacity-" + str(index), StartNode(NodeTarget("app")))
        self.assert_refused("capacity", command)

    def test_disjoint_retained_effect_does_not_block_new_receiver_scope(self):
        command = self.receiver_command(node="other")
        self.admit()
        module = self.require_scopes()
        self.assertEqual(self.evidence(module.ExecutionReceiverScope("docker", "other")).disposition,
                         "nonconflicting")
        result = self.desired_service().execute(command)
        with self.unit_of_work() as uow:
            bindings = uow.stores.graphs.receiver_bindings("workspace-a",
                result.graph_version_id, result.desired_realized_projection_id)
        self.assertEqual(tuple(binding.node_id for binding in bindings), ("other",))

    def test_genuine_legacy_acceptance_accounts_for_only_its_original_run(self):
        # This is genuine existing legacy advancement, not C3 receiver
        # acceptance. It supplies C1's existing evidence to C2's consumer.
        self.receiver_command()
        self.admit_operations("accepted", StartNode(NodeTarget("app")))
        claimed, _ = self.execute_effects("accepted")
        self.advance(claimed, "accepted")
        module = self.require_scopes()
        self.assertEqual(self.evidence(module.ExecutionReceiverScope("docker", "app")).disposition,
                         "nonconflicting")
        result = self.desired_service().execute(self.receiver_command())
        with self.unit_of_work() as uow:
            introduced = uow.stores.graphs.receiver_introduction("workspace-a", "a" * 32)
        self.assertEqual(introduced.introducing_action_id, result.action.action_id)
        self.assertIsNone(introduced.first_accepted_action_id)
