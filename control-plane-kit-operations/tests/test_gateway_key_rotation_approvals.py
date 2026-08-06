from __future__ import annotations

import itertools
import os
import unittest

import psycopg

from control_plane_kit_core.approval_subjects import (
    GatewayKeyRotationApprovalSubject,
)
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.planning import RiskLevel
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_operations.approvals import (
    ApprovalAuthorizationDenied,
    ApprovalCommandService,
    ApprovalIdempotencyConflict,
    ApprovalStateConflict,
    DecideApproval,
    RequestGatewayKeyRotationApproval,
)
from control_plane_kit_operations.gateway_key_rotations import (
    AdvanceGatewayKeyRotation,
    GatewayKeyRotationConflict,
    GatewayKeyRotationService,
    GatewayKeyRotationStatus,
    RequestGatewayKeyRotation,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import ApprovalDecisionKind, WorkspaceRecord
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    OperationCommandService,
    StartOperationSession,
)


class GatewayKeyRotationApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; run the operations test script"
            )
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self._ids = itertools.count(1)
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord("workspace-a", "Workspace A")
            )
            unit_of_work.commit()
        OperationCommandService(
            self.unit_of_work,
            clock=lambda: "2026-08-02T00:00:00Z",
            id_factory=self.id_factory,
        ).execute(
            StartOperationSession(
                "workspace-a",
                "operator-a",
                "Rotate gateway key",
                IdempotencyKey("start-rotation-session"),
            )
        )
        self.rotation_service = GatewayKeyRotationService(
            self.unit_of_work,
            clock=lambda: 1_000,
        )
        self.rotation = self.rotation_service.request(
            RequestGatewayKeyRotation(
                workspace_id="workspace-a",
                gateway_node_id="gateway-a",
                purpose=DelegationKeyPurpose.GATEWAY_PROBE,
                issuer="cpk-server",
                old_key_id="key-a",
                new_secret_reference=SecretReference(
                    "secret://provider-a/delegation-keys/key-b"
                ),
                key_generation_correlation="generate-key-b",
                maximum_grant_lifetime_seconds=120,
                clock_skew_seconds=10,
                correlation_id="rotation-a",
                requested_by="operator-a",
                requested_at="2026-08-02T00:00:01Z",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
            )
        )
        self.approvals = ApprovalCommandService(
            self.unit_of_work,
            clock=lambda: "2026-08-02T00:00:02Z",
            id_factory=self.id_factory,
        )

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def id_factory(self) -> str:
        return f"generated-{next(self._ids)}"

    def request(self, *, scopes: tuple[PolicyScope, ...] | None = None):
        return self.approvals.execute(
            RequestGatewayKeyRotationApproval(
                session_id="generated-1",
                rotation_id=self.rotation.rotation_id,
                actor_id="operator-a",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,)
                if scopes is None
                else scopes,
                idempotency_key=IdempotencyKey("request-rotation-approval"),
            )
        )

    def test_request_persists_closed_secret_free_rotation_subject(self) -> None:
        result = self.request()
        replay = self.request()

        self.assertIsInstance(result.request.subject, GatewayKeyRotationApprovalSubject)
        self.assertEqual(result.request.subject.rotation_id, self.rotation.rotation_id)
        self.assertEqual(
            result.request.required_scope,
            PolicyScope.DELEGATION_KEY_ROTATE_APPROVE,
        )
        self.assertEqual(result.request.max_risk, RiskLevel.HIGH)
        self.assertTrue(result.request.destructive)
        self.assertEqual(replay.request, result.request)
        self.assertTrue(replay.replayed)
        leak_surface = repr(result.descriptor()).lower()
        self.assertNotIn("secret://", leak_surface)
        self.assertNotIn("version_id", leak_surface)

    def test_rotation_accepts_only_one_approval_request_identity(self) -> None:
        self.request()

        with self.assertRaises(ApprovalStateConflict):
            self.approvals.execute(
                RequestGatewayKeyRotationApproval(
                    session_id="generated-1",
                    rotation_id=self.rotation.rotation_id,
                    actor_id="operator-a",
                    actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                    idempotency_key=IdempotencyKey(
                        "second-rotation-approval-request"
                    ),
                )
            )

    def test_request_and_decision_require_distinct_focused_scopes(self) -> None:
        with self.assertRaises(ApprovalAuthorizationDenied):
            self.request(scopes=(PolicyScope.PLAN_REQUEST,))

        requested = self.request()
        with self.assertRaises(ApprovalAuthorizationDenied):
            self.approvals.execute(
                DecideApproval(
                    session_id="generated-1",
                    request_id=requested.request.request_id,
                    actor_id="manager-a",
                    actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                    decision=ApprovalDecisionKind.APPROVED,
                    idempotency_key=IdempotencyKey("decide-rotation-approval"),
                )
            )
        with self.assertRaises(ApprovalAuthorizationDenied):
            self.approvals.execute(
                DecideApproval(
                    session_id="generated-1",
                    request_id=requested.request.request_id,
                    actor_id="manager-a",
                    actor_scopes=(PolicyScope.PLAN_EXECUTE,),
                    decision=ApprovalDecisionKind.APPROVED,
                    idempotency_key=IdempotencyKey("decide-rotation-approval"),
                )
            )
        with self.assertRaisesRegex(
            ApprovalAuthorizationDenied,
            "destructive approval requires a distinct principal",
        ):
            self.approvals.execute(
                DecideApproval(
                    session_id="generated-1",
                    request_id=requested.request.request_id,
                    actor_id="operator-a",
                    actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE_APPROVE,),
                    decision=ApprovalDecisionKind.APPROVED,
                    idempotency_key=IdempotencyKey("decide-rotation-approval"),
                )
            )

        decided = self.approvals.execute(
            DecideApproval(
                session_id="generated-1",
                request_id=requested.request.request_id,
                actor_id="manager-a",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE_APPROVE,),
                decision=ApprovalDecisionKind.APPROVED,
                idempotency_key=IdempotencyKey("decide-rotation-approval"),
            )
        )
        self.assertEqual(
            decided.decision.scope,
            PolicyScope.DELEGATION_KEY_ROTATE_APPROVE,
        )

    def test_request_idempotency_conflicts_for_another_rotation(self) -> None:
        self.request()
        other = self.rotation_service.request(
            RequestGatewayKeyRotation(
                workspace_id="workspace-a",
                gateway_node_id="gateway-b",
                purpose=DelegationKeyPurpose.GATEWAY_PROBE,
                issuer="cpk-server",
                old_key_id="key-x",
                new_secret_reference=SecretReference(
                    "secret://provider-a/delegation-keys/key-y"
                ),
                key_generation_correlation="generate-key-y",
                maximum_grant_lifetime_seconds=120,
                clock_skew_seconds=10,
                correlation_id="rotation-b",
                requested_by="operator-a",
                requested_at="2026-08-02T00:00:01Z",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
            )
        )

        with self.assertRaises(ApprovalIdempotencyConflict):
            self.approvals.execute(
                RequestGatewayKeyRotationApproval(
                    session_id="generated-1",
                    rotation_id=other.rotation_id,
                    actor_id="operator-a",
                    actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                    idempotency_key=IdempotencyKey(
                        "request-rotation-approval"
                    ),
                )
            )

    def test_rotation_link_rejects_approval_for_another_subject(self) -> None:
        requested = self.request()
        other_rotation = self.rotation_service.request(
            RequestGatewayKeyRotation(
                workspace_id="workspace-a",
                gateway_node_id="gateway-b",
                purpose=DelegationKeyPurpose.GATEWAY_PROBE,
                issuer="cpk-server",
                old_key_id="key-x",
                new_secret_reference=SecretReference(
                    "secret://provider-a/delegation-keys/key-y"
                ),
                key_generation_correlation="generate-key-y",
                maximum_grant_lifetime_seconds=120,
                clock_skew_seconds=10,
                correlation_id="rotation-b",
                requested_by="operator-a",
                requested_at="2026-08-02T00:00:01Z",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
            )
        )

        with self.assertRaises(GatewayKeyRotationConflict):
            self.rotation_service.advance(
                AdvanceGatewayKeyRotation(
                    rotation_id=other_rotation.rotation_id,
                    transition_id="awaiting-approval",
                    expected_status=GatewayKeyRotationStatus.REQUESTED,
                    expected_version=1,
                    target_status=GatewayKeyRotationStatus.AWAITING_APPROVAL,
                    advanced_by="operator-a",
                    advanced_at="2026-08-02T00:00:03Z",
                    actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                    approval_request_id=requested.request.request_id,
                )
            )

    def test_rejected_decision_cannot_be_linked_as_approved(self) -> None:
        requested = self.request()
        awaiting = self.rotation_service.advance(
            AdvanceGatewayKeyRotation(
                rotation_id=self.rotation.rotation_id,
                transition_id="awaiting-approval",
                expected_status=GatewayKeyRotationStatus.REQUESTED,
                expected_version=1,
                target_status=GatewayKeyRotationStatus.AWAITING_APPROVAL,
                advanced_by="operator-a",
                advanced_at="2026-08-02T00:00:03Z",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                approval_request_id=requested.request.request_id,
            )
        )
        rejected = self.approvals.execute(
            DecideApproval(
                session_id="generated-1",
                request_id=requested.request.request_id,
                actor_id="manager-a",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE_APPROVE,),
                decision=ApprovalDecisionKind.REJECTED,
                idempotency_key=IdempotencyKey("reject-rotation"),
            )
        )

        with self.assertRaises(GatewayKeyRotationConflict):
            self.rotation_service.advance(
                AdvanceGatewayKeyRotation(
                    rotation_id=awaiting.rotation_id,
                    transition_id="approve-rotation",
                    expected_status=GatewayKeyRotationStatus.AWAITING_APPROVAL,
                    expected_version=awaiting.version,
                    target_status=GatewayKeyRotationStatus.APPROVED,
                    advanced_by="operator-a",
                    advanced_at="2026-08-02T00:00:04Z",
                    actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                    approval_decision_id=rejected.decision.decision_id,
                )
            )


if __name__ == "__main__":
    unittest.main()
