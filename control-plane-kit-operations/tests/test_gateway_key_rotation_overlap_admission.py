from __future__ import annotations

from dataclasses import replace
import os
import unittest

import psycopg

from control_plane_kit_core.algebra import BlockSockets, BlockSpec
from control_plane_kit_core.delegation_authority import (
    DelegationAuthorityBinding,
    DelegationVerifierProjection,
    materialize_delegation_verifiers,
)
from control_plane_kit_core.delegation_keys import (
    DelegationKeyAlgorithm,
    DelegationKeyPurpose,
    DelegationPublicKey,
)
from control_plane_kit_core.planning import ActivityId, ActivityPlan
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_core.topology import DeploymentGraph, Node, RuntimeRecord
from control_plane_kit_core.types import BlockFamily, RuntimeKind
from control_plane_kit_operations.admission import (
    ExecutionAdmissionCommandService,
    ExecutionAdmissionConflict,
    ExecutionAdmissionDenied,
    RequestPlanExecution,
)
from control_plane_kit_operations.approvals import (
    ApprovalCommandService,
    DecideApproval,
    RequestGatewayKeyRotationApproval,
)
from control_plane_kit_operations.delegation_signing_keys import (
    RegisteredDelegationSigningKey,
    RegisteredDelegationSigningKeyStatus,
    delegation_signing_key_registration_id_for,
)
from control_plane_kit_operations.gateway_key_rotation_overlap import (
    GatewayKeyRotationOverlapProjectionService,
    PublishGatewayKeyRotationOverlapProjection,
)
from control_plane_kit_operations.gateway_key_rotations import (
    AdvanceGatewayKeyRotation,
    GatewayKeyRotationService,
    GatewayKeyRotationStatus,
    RequestGatewayKeyRotation,
)
from control_plane_kit_operations.planning import (
    ActivityPlanningCommandService,
    RequestActivityPlan,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import (
    ActivityPlanRecord,
    ApprovalDecisionKind,
    GraphVersionRecord,
    RealizedGraphProjectionKind,
    RealizedGraphProjectionRecord,
    WorkspaceRecord,
)
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    OperationCommandService,
    StartOperationSession,
)


PUBLIC_KEY_A = """-----BEGIN PUBLIC KEY-----
QUFB
-----END PUBLIC KEY-----
"""
PUBLIC_KEY_B = """-----BEGIN PUBLIC KEY-----
QkJC
-----END PUBLIC KEY-----
"""
PUBLIC_KEY_OTHER = """-----BEGIN PUBLIC KEY-----
T1RIRVI=
-----END PUBLIC KEY-----
"""


class Sequence:
    def __init__(self, *values: str) -> None:
        self._values = iter(values)

    def __call__(self) -> str:
        return next(self._values)


class GatewayKeyRotationOverlapAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError("run through control-plane-kit-operations/test.sh")
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self._seed_graph_and_keys()
        self._seed_rotation_approval()
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

    def _seed_graph_and_keys(self) -> None:
        authored = self.authored_graph()
        realized_a = materialize_delegation_verifiers(
            authored,
            (
                self.projection(
                    "gateway-a",
                    "projection-key-a",
                    self.public_key("key-a", PUBLIC_KEY_A),
                ),
                self.projection(
                    "gateway-other",
                    "projection-other",
                    self.public_key("key-other", PUBLIC_KEY_OTHER),
                ),
            ),
        )
        authored_record = GraphVersionRecord.from_graph(
            graph_id="graph-a",
            workspace_id="workspace-a",
            version=1,
            graph=authored,
            created_by="operator-a",
            created_at="2026-08-02T01:00:00Z",
        )
        projection_a = RealizedGraphProjectionRecord.from_graph(
            projection_id="projection-a",
            workspace_id="workspace-a",
            source_authored_graph_id="graph-a",
            projection_kind=RealizedGraphProjectionKind.DELEGATION_VERIFIER,
            projection_key="seed-a",
            graph=realized_a,
            created_by="operator-a",
            created_at="2026-08-02T01:00:00Z",
        )
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.workspaces.create(WorkspaceRecord("workspace-a", "Workspace A"))
            stores.graphs.save(authored_record)
            stores.realized_graphs.save(projection_a)
            stores.workspaces.set_current_graph("workspace-a", "graph-a", "projection-a")
            stores.workspaces.set_desired_graph("workspace-a", "graph-a", "projection-a")
            stores.delegation_signing_keys.register(
                self.signing_key("key-a", PUBLIC_KEY_A)
            )
            stores.delegation_signing_keys.activate(
                "workspace-a",
                DelegationKeyPurpose.GATEWAY_PROBE,
                "cpk-server",
                "key-a",
                activated_by="operator-a",
                activated_at="2026-08-02T01:00:01Z",
            )
            stores.delegation_signing_keys.register(
                self.signing_key("key-b", PUBLIC_KEY_B)
            )
            unit_of_work.commit()

    def _seed_rotation_approval(self) -> None:
        OperationCommandService(
            self.unit_of_work,
            clock=lambda: "2026-08-02T01:01:00Z",
            id_factory=Sequence("rotation-session", "rotation-session-action"),
        ).execute(
            StartOperationSession(
                "workspace-a",
                "operator-a",
                "Rotate gateway key",
                IdempotencyKey("start-rotation-session"),
            )
        )
        rotations = GatewayKeyRotationService(self.unit_of_work, clock=lambda: 1_000)
        requested = rotations.request(
            RequestGatewayKeyRotation(
                workspace_id="workspace-a",
                gateway_node_id="gateway-a",
                purpose=DelegationKeyPurpose.GATEWAY_PROBE,
                issuer="cpk-server",
                old_key_id="key-a",
                new_secret_reference=SecretReference(
                    "secret://workspace-secrets/keys/key-b"
                ),
                key_generation_correlation="generate-key-b",
                maximum_grant_lifetime_seconds=60,
                clock_skew_seconds=5,
                correlation_id="rotation-a",
                requested_by="operator-a",
                requested_at="2026-08-02T01:01:01Z",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
            )
        )
        approvals = ApprovalCommandService(
            self.unit_of_work,
            clock=lambda: "2026-08-02T01:01:02Z",
            id_factory=Sequence(
                "rotation-approval-request",
                "rotation-approval-request-action",
                "rotation-approval-decision",
                "rotation-approval-decision-action",
            ),
        )
        approval = approvals.execute(
            RequestGatewayKeyRotationApproval(
                session_id="rotation-session",
                rotation_id=requested.rotation_id,
                actor_id="operator-a",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                idempotency_key=IdempotencyKey("request-rotation-approval"),
            )
        )
        awaiting = rotations.advance(
            AdvanceGatewayKeyRotation(
                requested.rotation_id,
                "await-approval",
                requested.status,
                requested.version,
                GatewayKeyRotationStatus.AWAITING_APPROVAL,
                "operator-a",
                "2026-08-02T01:01:03Z",
                (PolicyScope.DELEGATION_KEY_ROTATE,),
                approval_request_id=approval.request.request_id,
            )
        )
        decision = approvals.execute(
            DecideApproval(
                session_id="rotation-session",
                request_id=approval.request.request_id,
                actor_id="manager-a",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE_APPROVE,),
                decision=ApprovalDecisionKind.APPROVED,
                idempotency_key=IdempotencyKey("approve-rotation"),
            )
        )
        approved = rotations.advance(
            AdvanceGatewayKeyRotation(
                awaiting.rotation_id,
                "approve-rotation",
                awaiting.status,
                awaiting.version,
                GatewayKeyRotationStatus.APPROVED,
                "operator-a",
                "2026-08-02T01:01:04Z",
                (PolicyScope.DELEGATION_KEY_ROTATE,),
                approval_decision_id=decision.decision.decision_id,
            )
        )
        prepared = rotations.advance(
            AdvanceGatewayKeyRotation(
                approved.rotation_id,
                "prepare-generation",
                approved.status,
                approved.version,
                GatewayKeyRotationStatus.GENERATION_PREPARED,
                "operator-a",
                "2026-08-02T01:01:05Z",
                (PolicyScope.DELEGATION_KEY_ROTATE,),
                generation_provider_registration_id="provider-a",
                generation_action_digest="a" * 64,
            )
        )
        generated = rotations.advance(
            AdvanceGatewayKeyRotation(
                prepared.rotation_id,
                "complete-generation",
                prepared.status,
                prepared.version,
                GatewayKeyRotationStatus.KEY_GENERATED,
                "operator-a",
                "2026-08-02T01:01:06Z",
                (PolicyScope.DELEGATION_KEY_ROTATE,),
                new_key_id="key-b",
                new_secret_version_id="version-b",
                new_secret_version_number=1,
            )
        )
        self.rotation_id = generated.rotation_id
        self.rotation_version = generated.version
        self.approval_request_id = approval.request.request_id
        self.approval_decision_id = decision.decision.decision_id
        self.approval_review_digest = approval.request.subject.review_digest

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

    @staticmethod
    def authored_graph() -> DeploymentGraph:
        bindings = tuple(
            DelegationAuthorityBinding(
                node_id,
                DelegationKeyPurpose.GATEWAY_PROBE,
                "cpk-server",
            )
            for node_id in ("gateway-a", "gateway-other")
        )
        nodes = {
            node_id: Node(
                node_id=node_id,
                block_family=BlockFamily.PROXY,
                block_spec=BlockSpec(node_id),
                kind="container-server",
                runtime_id="docker",
                sockets=BlockSockets(),
            )
            for node_id in ("gateway-a", "gateway-other")
        }
        return DeploymentGraph(
            "gateway-island",
            nodes=nodes,
            runtimes={
                "docker": RuntimeRecord(
                    "docker",
                    RuntimeKind.DOCKER,
                    children=tuple(nodes),
                )
            },
            delegation_authorities=bindings,
        )

    @staticmethod
    def public_key(key_id: str, pem: str) -> DelegationPublicKey:
        return DelegationPublicKey(key_id, DelegationKeyAlgorithm.ED25519, pem)

    @classmethod
    def projection(
        cls,
        node_id: str,
        projection_id: str,
        key: DelegationPublicKey,
    ) -> DelegationVerifierProjection:
        return DelegationVerifierProjection(
            node_id,
            DelegationKeyPurpose.GATEWAY_PROBE,
            "cpk-server",
            f"gateway:workspace-a:{node_id}",
            projection_id,
            (key,),
        )

    @classmethod
    def signing_key(
        cls,
        key_id: str,
        pem: str,
    ) -> RegisteredDelegationSigningKey:
        public_key = cls.public_key(key_id, pem)
        private_reference = SecretReference(
            f"secret://workspace-secrets/keys/{key_id}"
        )
        return RegisteredDelegationSigningKey(
            registration_id=delegation_signing_key_registration_id_for(
                workspace_id="workspace-a",
                purpose=DelegationKeyPurpose.GATEWAY_PROBE,
                issuer="cpk-server",
                public_key=public_key,
                private_key_reference=private_reference,
            ),
            workspace_id="workspace-a",
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="cpk-server",
            public_key=public_key,
            private_key_reference=private_reference,
            admitted_by="operator-a",
            admitted_at="2026-08-02T01:00:00Z",
            status=RegisteredDelegationSigningKeyStatus.VERIFY_ONLY,
        )


if __name__ == "__main__":
    unittest.main()
