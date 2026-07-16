"""Postgres integration tests for focused workflow read projections."""

from control_plane_kit import (
    ActivityImpact,
    ActivityPlan,
    DeploymentGraph,
    DEFAULT_GRAPH_CODEC,
    RiskLevel,
    compile_activity_plan,
    compile_recipe,
    diff_graphs,
    validate_graph,
)
from control_plane_kit.read_services import InstanceReadService, ReadModelError
from control_plane_kit.stores import (
    ActivityPlanRecord,
    ApprovalDecisionKind,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    GraphVersionRecord,
    OperationActionKind,
    OperationActionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
    WorkspaceRecord,
)
from examples.router_swap import recipe as router_recipe
from tests.postgres_case import PostgresStoreTestCase


class RecordingGraphCodec:
    def __init__(self) -> None:
        self.decoded = 0

    def decode(self, descriptor):
        self.decoded += 1
        return DEFAULT_GRAPH_CODEC.decode(descriptor)

    def encode(self, graph):
        return DEFAULT_GRAPH_CODEC.encode(graph)

    def encode_block_spec(self, spec):
        return DEFAULT_GRAPH_CODEC.encode_block_spec(spec)

    def supports_same_block_specs_as(self, other) -> bool:
        return other is self


class FocusedWorkflowReadTests(PostgresStoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.stores.workspace.create(WorkspaceRecord("workspace-a", "Workspace A"))
        self.stores.workspace.create(WorkspaceRecord("workspace-b", "Workspace B"))
        populated = compile_recipe(router_recipe("api-v1"))
        empty = DeploymentGraph(populated.name)
        self.stores.graph_topology.save(
            GraphVersionRecord.from_graph(
                graph_id="graph-populated",
                workspace_id="workspace-a",
                version=1,
                graph=populated,
                created_by="jacob",
                created_at="2026-07-16T00:00:00Z",
            )
        )
        self.stores.graph_topology.save(
            GraphVersionRecord.from_graph(
                graph_id="graph-empty",
                workspace_id="workspace-a",
                version=2,
                graph=empty,
                created_by="jacob",
                created_at="2026-07-16T00:01:00Z",
            )
        )
        self._session("session-a", "workspace-a", "2026-07-16T00:00:00Z")
        self._session("session-b", "workspace-a", "2026-07-16T00:00:01Z")
        self._session("session-other", "workspace-b", "2026-07-16T00:00:02Z")
        self.stores.activity_history.add_action(
            OperationActionRecord(
                action_id="action-a",
                session_id="session-a",
                ordinal=1,
                action_type=OperationActionKind.PATCH_VARIABLE,
                actor_id="jacob",
                payload={"api_token": "secret", "note": "visible"},
                created_at="2026-07-16T00:02:00Z",
            )
        )
        destructive = compile_activity_plan(
            diff_graphs(validate_graph(populated), validate_graph(empty))
        )
        self.stores.activity_history.add_plan(
            ActivityPlanRecord(
                plan_id="plan-a",
                session_id="session-a",
                base_graph_id="graph-populated",
                desired_graph_id="graph-empty",
                status="planned",
                created_at="2026-07-16T00:03:00Z",
                plan=destructive,
            )
        )
        self._approval("pending-a", "session-a", "2026-07-16T00:04:00Z")
        self._approval("approved-a", "session-a", "2026-07-16T00:05:00Z")
        self.stores.activity_history.add_approval_decision(
            ApprovalDecisionRecord(
                decision_id="decision-a",
                request_id="approved-a",
                actor_id="manager",
                decision=ApprovalDecisionKind.APPROVED,
                scope="plan:approve-destructive",
                decided_at="2026-07-16T00:06:00Z",
            )
        )

    def _service(self) -> InstanceReadService:
        return InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
            activity_history_store=self.stores.activity_history,
            execution_store=self.stores.execution,
        )

    def _session(self, session_id: str, workspace_id: str, created_at: str) -> None:
        self.stores.activity_history.add_session(
            OperationSessionRecord(
                session_id=session_id,
                workspace_id=workspace_id,
                actor_id="jacob",
                title=f"Session {session_id}",
                status=OperationSessionStatus.OPEN,
                created_at=created_at,
                metadata={"callback_url": "http://private", "label": "visible"},
            )
        )

    def _approval(self, request_id: str, session_id: str, requested_at: str) -> None:
        self.stores.activity_history.add_approval_request(
            ApprovalRequestRecord(
                request_id=request_id,
                session_id=session_id,
                plan_id="plan-a",
                requested_by="jacob",
                requested_at=requested_at,
                required_scope="plan:approve-destructive",
                max_risk=RiskLevel.CRITICAL,
                destructive=True,
            )
        )

    def test_open_sessions_are_workspace_scoped_redacted_and_paged(self):
        payload = self._service().open_sessions(
            "workspace-a", limit=1, offset=1
        ).descriptor()

        self.assertEqual(payload["total"], 2)
        self.assertFalse(payload["has_more"])
        self.assertEqual([item["session_id"] for item in payload["items"]], ["session-b"])
        self.assertEqual(payload["items"][0]["metadata"]["callback_url"], "<redacted>")
        self.assertNotIn("session-other", str(payload))

    def test_session_detail_is_bounded_and_redacted(self):
        payload = self._service().session_detail(
            "workspace-a", "session-a", limit=1
        ).descriptor()

        self.assertEqual(payload["kind"], "session-detail")
        self.assertEqual(len(payload["session"]["actions"]), 1)
        self.assertEqual(
            payload["session"]["actions"][0]["payload"]["api_token"],
            "<redacted>",
        )
        self.assertEqual(len(payload["session"]["approvals"]), 1)

    def test_plan_detail_reuses_canonical_plan_risk_and_recovery(self):
        plan = self._service().plan_detail("workspace-a", "plan-a").descriptor()["plan"]

        self.assertEqual(plan["payload"]["schema"], "control-plane-kit.activity-plan")
        self.assertEqual(plan["risk_summary"]["max_risk"], RiskLevel.CRITICAL.value)
        self.assertGreater(plan["risk_summary"]["destructive_count"], 0)
        self.assertEqual(
            plan["recovery"]["schema"],
            "control-plane-kit.recovery-candidate",
        )
        self.assertEqual(plan["recovery"]["mode"], "reverse-transition")
        self.assertTrue(
            any(
                activity.impact is ActivityImpact.DESTRUCTIVE
                for activity in self.stores.activity_history.get_plan("plan-a").plan.activities
            )
        )

    def test_plan_recovery_uses_the_injected_graph_codec(self):
        codec = RecordingGraphCodec()
        service = InstanceReadService(
            workspace_store=self.stores.workspace,
            graph_topology_store=self.stores.graph_topology,
            activity_history_store=self.stores.activity_history,
            execution_store=self.stores.execution,
            graph_codec=codec,
        )

        service.plan_detail("workspace-a", "plan-a")

        self.assertGreaterEqual(codec.decoded, 2)

    def test_pending_approval_queue_excludes_decisions_and_is_bounded(self):
        payload = self._service().pending_approvals(
            "workspace-a", limit=1
        ).descriptor()

        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["request_id"], "pending-a")
        self.assertEqual(payload["items"][0]["state"], "pending")
        self.assertFalse(payload["has_more"])

    def test_focused_reads_fail_closed_across_workspace_boundaries(self):
        service = self._service()

        with self.assertRaisesRegex(ReadModelError, "missing session 'session-other'"):
            service.session_detail("workspace-a", "session-other")
        with self.assertRaisesRegex(ReadModelError, "missing plan 'plan-a'"):
            service.plan_detail("workspace-b", "plan-a")

    def test_focused_bounds_reject_unbounded_or_invalid_pages(self):
        service = self._service()

        with self.assertRaisesRegex(ReadModelError, "must not exceed 100"):
            service.open_sessions("workspace-a", limit=101)
        with self.assertRaisesRegex(ReadModelError, "offset must be non-negative"):
            service.pending_approvals("workspace-a", offset=-1)


if __name__ == "__main__":
    import unittest

    unittest.main()
