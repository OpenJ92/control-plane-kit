from __future__ import annotations

from dataclasses import replace
import os
import unittest

import psycopg

from gateway_rotation_overlap_fixture import (
    GatewayRotationOverlapFixture,
    PUBLIC_KEY_OTHER,
    Sequence,
)

from control_plane_kit_core.planning import ActivityId, ActivityPlan
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.admission import (
    ExecutionAdmissionCommandService,
    ExecutionAdmissionConflict,
    ExecutionAdmissionDenied,
    RequestPlanExecution,
)
from control_plane_kit_operations.gateway_key_rotation_overlap import (
    GatewayKeyRotationOverlapProjectionService,
    PublishGatewayKeyRotationOverlapProjection,
)
from control_plane_kit_operations.planning import (
    ActivityPlanningCommandService,
    RequestActivityPlan,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import WorkspaceRecord
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    OperationCommandService,
    StartOperationSession,
)

class GatewayKeyRotationOverlapAdmissionTests(
    GatewayRotationOverlapFixture,
    unittest.TestCase,
):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError("run through control-plane-kit-operations/test.sh")
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.seed_graph_and_keys()
        self.seed_rotation_approval()
        self._publish_and_plan_overlap()

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def command(
        self,
        *,
        workspace_id: str = "workspace-a",
        session_id: str = "child-session",
        plan_id: str | None = None,
        approval_request_id: str | None = None,
        scopes: tuple[PolicyScope, ...] = (PolicyScope.PLAN_EXECUTE,),
        key: str = "admit-overlap",
    ) -> RequestPlanExecution:
        return RequestPlanExecution(
            workspace_id=workspace_id,
            session_id=session_id,
            plan_id=self.plan.plan_id if plan_id is None else plan_id,
            approval_request_id=(
                self.approval_request_id
                if approval_request_id is None
                else approval_request_id
            ),
            actor_id="operator-a",
            actor_scopes=scopes,
            idempotency_key=IdempotencyKey(key),
        )

    def service(self, *ids: str) -> ExecutionAdmissionCommandService:
        return ExecutionAdmissionCommandService(
            self.unit_of_work,
            clock=lambda: "2026-08-02T03:00:00Z",
            id_factory=Sequence(*ids),
        )

    def test_exact_rotation_approval_admits_only_the_overlap_child_plan(self) -> None:
        result = self.service("execution-a", "action-admit").execute(self.command())

        self.assertFalse(result.replayed)
        self.assertEqual(result.request.approval_request_id, self.approval_request_id)
        self.assertEqual(result.request.approval_decision_id, self.approval_decision_id)
        self.assertEqual(result.request.identity.plan_id, self.plan.plan_id)
        self.assertEqual(
            result.action.payload["base_realized_projection_id"],
            "projection-a",
        )
        self.assertEqual(
            result.action.payload["desired_realized_projection_id"],
            self.overlap_projection_id,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_approval_requests"
            ).fetchone()[0],
            1,
        )

        replay = self.service("unused", "unused").execute(self.command())
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.request, result.request)

    def test_original_rotation_approval_cannot_authorize_another_plan(self) -> None:
        forged_activity = replace(
            self.plan.plan.activities[0],
            activity_id=ActivityId("forged-activity"),
        )
        forged = replace(
            self.plan,
            plan_id="plan-forged",
            plan=ActivityPlan((forged_activity,)),
        )
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.activity_history.add_plan(forged)
            unit_of_work.commit()

        with self.assertRaises(ExecutionAdmissionConflict):
            self.service("execution-forged", "action-forged").execute(
                self.command(plan_id="plan-forged", key="admit-forged")
            )

    def test_plan_session_and_workspace_forgery_fail_closed(self) -> None:
        foreign_session = replace(
            self.plan,
            plan_id="plan-foreign-session",
            session_id="rotation-session",
        )
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.activity_history.add_plan(foreign_session)
            unit_of_work.commit()

        with self.assertRaises(ExecutionAdmissionConflict):
            self.service("execution-session", "action-session").execute(
                self.command(
                    plan_id="plan-foreign-session",
                    key="admit-foreign-session",
                )
            )
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord("workspace-foreign", "Foreign Workspace")
            )
            unit_of_work.commit()
        with self.assertRaises(ExecutionAdmissionConflict):
            self.service("execution-workspace", "action-workspace").execute(
                self.command(workspace_id="workspace-foreign", key="admit-foreign")
            )

    def test_changed_review_digest_or_rotation_approval_identity_fails(self) -> None:
        self.connection.execute(
            "UPDATE cpk_operation_actions "
            "SET payload=jsonb_set(payload, '{review_digest}', to_jsonb(%s::text)) "
            "WHERE idempotency_key='request-rotation-approval'",
            ("f" * 64,),
        )
        with self.assertRaises(ExecutionAdmissionDenied):
            self.service("execution-digest", "action-digest").execute(
                self.command(key="admit-digest")
            )

        self.connection.execute(
            "UPDATE cpk_operation_actions "
            "SET payload=jsonb_set(payload, '{review_digest}', to_jsonb(%s::text)) "
            "WHERE idempotency_key='request-rotation-approval'",
            (self.approval_review_digest,),
        )
        self.connection.execute(
            "UPDATE cpk_gateway_key_rotations SET approval_decision_id='decision-other' "
            "WHERE rotation_id=%s",
            (self.rotation_id,),
        )
        with self.assertRaises(ExecutionAdmissionDenied):
            self.service("execution-approval", "action-approval").execute(
                self.command(key="admit-wrong-approval")
            )

    def test_stale_projection_or_revision_fails_before_admission(self) -> None:
        self.connection.execute(
            "UPDATE cpk_workspaces SET current_realized_projection_id=%s "
            "WHERE workspace_id='workspace-a'",
            (self.overlap_projection_id,),
        )
        with self.assertRaises(ExecutionAdmissionConflict):
            self.service("execution-current", "action-current").execute(
                self.command(key="admit-stale-current")
            )

        self.connection.execute(
            "UPDATE cpk_workspaces SET current_realized_projection_id='projection-a', "
            "desired_realized_projection_id='projection-a' "
            "WHERE workspace_id='workspace-a'"
        )
        with self.assertRaises(ExecutionAdmissionConflict):
            self.service("execution-stale", "action-stale").execute(
                self.command(key="admit-stale-projection")
            )

        self.connection.execute(
            "UPDATE cpk_workspaces SET desired_realized_projection_id=%s, "
            "desired_graph_revision=desired_graph_revision + 1 "
            "WHERE workspace_id='workspace-a'",
            (self.overlap_projection_id,),
        )
        with self.assertRaises(ExecutionAdmissionConflict):
            self.service("execution-revision", "action-revision").execute(
                self.command(key="admit-stale-revision")
            )

    def test_changed_overlap_publication_provenance_fails_closed(self) -> None:
        self.connection.execute(
            "UPDATE cpk_operation_actions SET payload=jsonb_set("
            "payload, '{source_operation_version}', to_jsonb(%s::int)) "
            "WHERE idempotency_key='publish-overlap'",
            (self.rotation_version + 1,),
        )

        with self.assertRaises(ExecutionAdmissionDenied):
            self.service("execution-provenance", "action-provenance").execute(
                self.command(key="admit-changed-provenance")
            )

    def test_unexpected_key_or_wrong_rotation_phase_fails_closed(self) -> None:
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.delegation_signing_keys.register(
                self.signing_key("key-extra", PUBLIC_KEY_OTHER)
            )
            unit_of_work.commit()
        with self.assertRaises(ExecutionAdmissionConflict):
            self.service("execution-extra", "action-extra").execute(
                self.command(key="admit-extra-key")
            )

        self.connection.execute(
            "DELETE FROM cpk_delegation_signing_keys WHERE key_id='key-extra'"
        )
        self.connection.execute(
            "UPDATE cpk_gateway_key_rotations SET status='approved' "
            "WHERE rotation_id=%s",
            (self.rotation_id,),
        )
        with self.assertRaises(ExecutionAdmissionConflict):
            self.service("execution-phase", "action-phase").execute(
                self.command(key="admit-wrong-phase")
            )

    def test_missing_rejected_decision_and_missing_execute_scope_fail(self) -> None:
        self.connection.execute(
            "UPDATE cpk_approval_decisions SET decision='rejected' "
            "WHERE decision_id=%s",
            (self.approval_decision_id,),
        )
        with self.assertRaises(ExecutionAdmissionDenied):
            self.service("execution-rejected", "action-rejected").execute(
                self.command(key="admit-rejected")
            )

        self.connection.execute(
            "DELETE FROM cpk_approval_decisions WHERE decision_id=%s",
            (self.approval_decision_id,),
        )
        with self.assertRaises(ExecutionAdmissionDenied):
            self.service("execution-missing", "action-missing").execute(
                self.command(key="admit-missing-decision")
            )
        with self.assertRaises(ExecutionAdmissionDenied):
            self.service("execution-scope", "action-scope").execute(
                self.command(scopes=(), key="admit-missing-scope")
            )

    def _publish_and_plan_overlap(self) -> None:
        OperationCommandService(
            self.unit_of_work,
            clock=lambda: "2026-08-02T01:02:00Z",
            id_factory=Sequence("child-session", "child-session-action"),
        ).execute(
            StartOperationSession(
                "workspace-a",
                "operator-a",
                "Deploy rotation overlap",
                IdempotencyKey("start-child-session"),
            )
        )
        publication = GatewayKeyRotationOverlapProjectionService(
            self.unit_of_work,
            clock=lambda: "2026-08-02T01:02:01Z",
            action_id_factory=lambda: "overlap-publication-action",
        ).execute(
            PublishGatewayKeyRotationOverlapProjection(
                rotation_id=self.rotation_id,
                session_id="child-session",
                actor_id="operator-a",
                expected_rotation_version=self.rotation_version,
                expected_authored_graph_id="graph-a",
                expected_current_realized_projection_id="projection-a",
                expected_desired_realized_projection_id="projection-a",
                expected_desired_graph_revision=1,
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                idempotency_key=IdempotencyKey("publish-overlap"),
            )
        )
        self.overlap_projection_id = (
            publication.publication.desired_realized_projection_id
        )
        result = ActivityPlanningCommandService(
            self.unit_of_work,
            clock=lambda: "2026-08-02T01:02:02Z",
            id_factory=Sequence("overlap-plan", "overlap-plan-action"),
        ).execute(
            RequestActivityPlan(
                session_id="child-session",
                workspace_id="workspace-a",
                actor_id="operator-a",
                expected_current_graph_id="graph-a",
                expected_desired_graph_id="graph-a",
                expected_current_realized_projection_id="projection-a",
                expected_desired_realized_projection_id=self.overlap_projection_id,
                expected_desired_graph_revision=2,
                idempotency_key=IdempotencyKey("plan-overlap"),
            )
        )
        self.plan = result.plan_record

if __name__ == "__main__":
    unittest.main()
