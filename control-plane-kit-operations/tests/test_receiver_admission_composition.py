"""#1903 original parent/child receipts select compatibility, never caller fallback."""

from dataclasses import replace
import unittest

from control_plane_kit_operations.deployment_program import PrepareDeploymentProgram
from control_plane_kit_operations.deployment_program_interpreter import (
    DeploymentProgramStateConflict, _child_keys, _intent_digest,
)
from control_plane_kit_operations.planning import SetDesiredGraph
from control_plane_kit_operations.records import RealizedGraphProjectionKind
from control_plane_kit_operations.workflows import IdempotencyKey, StartOperationSession
from tests.draft_catalogue_fixture import principal
from tests.receiver_admission_fixture import ReceiverAdmissionFixture


class ReceiverAdmissionCompositionTests(ReceiverAdmissionFixture, unittest.TestCase):
    def test_old_parent_replay_preserves_legacy_reference_outside_new_pin_language(self):
        legacy_id = "p" * 257
        with self.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get("workspace-a")
            original = uow.stores.realized_graphs.get(workspace.current_realized_projection_id)
            legacy = replace(original, projection_id=legacy_id,
                projection_kind=RealizedGraphProjectionKind.DELEGATION_VERIFIER,
                projection_key="legacy-reference")
            uow.stores.realized_graphs.save(legacy)
            uow.stores.workspaces.set_current_graph("workspace-a", original.source_authored_graph_id, legacy_id)
            uow.stores.workspaces.set_desired_graph("workspace-a", original.source_authored_graph_id, legacy_id)
            uow.commit()
        parent = self.parent()
        child, original = self.original_legacy_child(parent)
        graphs = self.rows("cpk_graph_versions")
        result = self.program().prepare(parent)
        self.assertEqual(self.rows("cpk_graph_versions"), graphs)
        self.assertEqual(self.command_action(child), original.action)
        self.assertEqual(self.program().prepare(parent), result)
        # A different parent key owns no old receipt: the new product must
        # reject the legacy-only reference instead of choosing old format.
        with self.assertRaises(DeploymentProgramStateConflict):
            self.program().prepare(replace(parent, idempotency_key=IdempotencyKey("fresh-long-pin")))
        self.assertEqual(self.rows("cpk_graph_versions"), graphs)

    def parent(self, *, receiver=False, key="parent"):
        workspace = self.workspace()
        return PrepareDeploymentProgram(context=principal().command_context("workspace-a"),
            desired=self.receiver_graph()[0] if receiver else self.graph(),
            expected_current=workspace.current_lineage, expected_desired=workspace.desired_lineage,
            expected_desired_graph_revision=workspace.desired_graph_revision,
            title="Prepare receiver", idempotency_key=IdempotencyKey(key), approval_comment="Review")

    def original_legacy_child(self, parent):
        # Established parent digest/key algorithm is intentionally unchanged.
        # Create a real original old-shape child through its legacy owner;
        # stop before planning to exercise the interrupted-parent branch.
        keys = _child_keys(parent)
        session = self.operations().execute(StartOperationSession("workspace-a", "operator-a", parent.title,
            keys["session"], metadata={"deployment_prepare_intent_sha256": _intent_digest(parent)}))
        child = SetDesiredGraph(session.session.session_id, "workspace-a", "operator-a", parent.desired,
            parent.expected_desired.authored_graph_id, keys["desired"],
            parent.expected_desired.realized_projection_id, parent.expected_desired_graph_revision)
        result = self.desired_service().execute(child)
        self.assertNotIn("receiver_lifecycle", result.action.payload)
        return child, result

    def test_interrupted_old_parent_replays_exact_owned_old_child_then_completes(self):
        self.require_expectation()
        parent = self.parent()
        child, original = self.original_legacy_child(parent)
        graphs = self.rows("cpk_graph_versions")
        result = self.program().prepare(parent)
        self.assertEqual(self.rows("cpk_graph_versions"), graphs)
        self.assertEqual(self.command_action(child), original.action)
        before = self.admission_truth()
        self.assertEqual(self.program().prepare(parent), result)
        self.assertEqual(self.admission_truth(), before)

    def test_completed_old_parent_replay_survives_later_desired_truth(self):
        self.require_expectation()
        parent = self.parent()
        child, original = self.original_legacy_child(parent)
        result = self.program().prepare(parent)
        self.desired_service().execute(self.desired_command(graph=self.graph("later"), key="later"))
        before = self.admission_truth()
        self.assertEqual(self.program().prepare(parent), result)
        self.assertEqual(self.command_action(child), original.action)
        self.assertEqual(self.admission_truth(), before)

    def test_mismatched_owned_child_receipt_is_not_an_old_format_fallback(self):
        self.require_expectation()
        parent = self.parent()
        child, _ = self.original_legacy_child(parent)
        self.connection.execute("UPDATE cpk_operation_actions SET intent_fingerprint=%s "
            "WHERE session_id=%s AND idempotency_key=%s", ("0" * 64, child.session_id, child.idempotency_key.value))
        before = self.admission_truth()
        with self.assertRaises(DeploymentProgramStateConflict):
            self.program().prepare(parent)
        self.assertEqual(self.admission_truth(), before)

    def test_fresh_inline_child_uses_original_current_pins_before_any_graph_write(self):
        self.require_expectation()
        parent = self.parent(receiver=True)
        parent = replace(parent, expected_current=replace(parent.expected_current,
            realized_projection_id="stale-original-current"))
        graphs = self.rows("cpk_graph_versions")
        with self.assertRaises(DeploymentProgramStateConflict):
            self.program().prepare(parent)
        self.assertEqual(self.rows("cpk_graph_versions"), graphs)
        self.assertEqual(self.rows("cpk_graph_receiver_introductions"), [])
        # Parent session creation may have committed; no child graph/action
        # may be committed and subsequently excused by planning's stale check.
        actions = [row[0] for row in self.rows("cpk_operation_actions")]
        self.assertFalse(any(action["action_type"] == "set-desired-graph" for action in actions))
