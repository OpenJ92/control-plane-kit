from __future__ import annotations

from dataclasses import dataclass, field
import unittest

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.identity import (
    AuthenticatedPrincipal,
    PrincipalIdentity,
    PrincipalKind,
    WorkspaceGrant,
)
from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_operations.cpk_server import (
    CpkServerApplicationError,
    CpkServerApprovalService,
    CpkServerPlanningService,
    CpkServerReadService,
)
from control_plane_kit_operations.gateway_key_rotation_application import (
    GatewayKeyRotationApprovalView,
    GatewayKeyRotationProgramView,
    GatewayKeyRotationPublicView,
    GatewayKeyRotationTransitionView,
)
from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotationStatus,
)
from control_plane_kit_operations.records import ApprovalDecisionKind


def principal(*scopes: PolicyScope) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        PrincipalIdentity(
            issuer="urn:test:rotation-adapter",
            subject_id="operator-a",
            kind=PrincipalKind.OPERATOR,
        ),
        (WorkspaceGrant("workspace-a", tuple(scopes)),),
    )


@dataclass(frozen=True)
class RouteRequest:
    surface: str
    route_id: str
    service_role: ControlPlaneServiceRole
    path_parameters: dict[str, str]
    payload: dict[str, object]
    principal: AuthenticatedPrincipal


class UnusedService:
    def execute(self, command):
        raise AssertionError(f"unexpected low-level command: {command!r}")


class RecordingRotationApplication:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, str, tuple[PolicyScope, ...]]] = []

    @staticmethod
    def view(
        status: GatewayKeyRotationStatus = GatewayKeyRotationStatus.REQUESTED,
        version: int = 1,
    ) -> GatewayKeyRotationPublicView:
        return GatewayKeyRotationPublicView(
            rotation_id="rotation-a",
            workspace_id="workspace-a",
            gateway_node_id="gateway-a",
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="cpk-server",
            old_key_id="key-a",
            new_key_id=None,
            status=status,
            version=version,
            correlation_id="request-a",
            requested_by="operator-a",
            requested_at="2026-08-03T10:00:00Z",
            drain_deadline_epoch=None,
            failure_code=None,
            updated_at=None,
        )

    def _record(self, name, value, context) -> None:
        self.calls.append(
            (name, value, context.actor_id, context.granted_scopes)
        )

    def request(self, command, context):
        self._record("request", command, context)
        return self.view()

    def request_approval(self, command, context):
        self._record("request-approval", command, context)
        return GatewayKeyRotationApprovalView(
            self.view(GatewayKeyRotationStatus.AWAITING_APPROVAL, 2),
            "approval-a",
            None,
            None,
            False,
        )

    def decide(self, command, context):
        self._record("decide", command, context)
        return GatewayKeyRotationApprovalView(
            self.view(GatewayKeyRotationStatus.APPROVED, 3),
            "approval-a",
            "decision-a",
            ApprovalDecisionKind.APPROVED,
            False,
        )

    def advance(self, command, context):
        self._record("advance", command, context)
        return GatewayKeyRotationProgramView(
            self.view(GatewayKeyRotationStatus.GENERATION_PREPARED, 4),
            "generation",
            "prepared",
        )

    def list(self, workspace_id, context):
        self._record("list", workspace_id, context)
        return (self.view(),)

    def detail(self, workspace_id, rotation_id, context):
        self._record("detail", (workspace_id, rotation_id), context)
        return self.view()

    def transitions(self, workspace_id, rotation_id, context):
        self._record("transitions", (workspace_id, rotation_id), context)
        return (
            GatewayKeyRotationTransitionView(
                transition_id="transition-a",
                from_status=GatewayKeyRotationStatus.REQUESTED,
                to_status=GatewayKeyRotationStatus.AWAITING_APPROVAL,
                from_version=1,
                to_version=2,
                advanced_by="operator-a",
                advanced_at="2026-08-03T10:01:00Z",
                failure_code=None,
            ),
        )


class CpkServerGatewayKeyRotationAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rotations = RecordingRotationApplication()
        self.planning = CpkServerPlanningService(
            UnusedService(),
            gateway_key_rotations=self.rotations,
        )
        self.approval = CpkServerApprovalService(
            UnusedService(),
            gateway_key_rotations=self.rotations,
        )
        self.reads = CpkServerReadService(
            lambda: (_ for _ in ()).throw(AssertionError("unexpected UoW")),
            gateway_key_rotations=self.rotations,
        )

    def request(self, surface: str):
        return RouteRequest(
            surface=surface,
            route_id="command.gateway-key-rotation.request",
            service_role=ControlPlaneServiceRole.PLANNING,
            path_parameters={"workspace_id": "workspace-a"},
            payload={
                "gateway_node_id": "gateway-a",
                "purpose": "gateway-probe",
                "issuer": "cpk-server",
                "old_key_id": "key-a",
                "new_secret_reference": "secret://provider/key-b",
                "key_generation_correlation": "generate-b",
                "maximum_grant_lifetime_seconds": 60,
                "clock_skew_seconds": 5,
                "idempotency_key": "request-a",
                "requested_at": "2026-08-03T10:00:00Z",
                "actor_id": "forged",
                "actor_scopes": [scope.value for scope in PolicyScope],
            },
            principal=principal(PolicyScope.DELEGATION_KEY_ROTATE),
        )

    def test_http_and_mcp_request_share_one_trusted_operations_boundary(self) -> None:
        http = self.planning.handle(self.request("http"))
        mcp = self.planning.handle(self.request("mcp"))

        self.assertEqual(http, mcp)
        self.assertEqual([call[0] for call in self.rotations.calls], ["request"] * 2)
        for _name, command, actor_id, scopes in self.rotations.calls:
            self.assertEqual(actor_id, "operator-a")
            self.assertEqual(scopes, (PolicyScope.DELEGATION_KEY_ROTATE,))
            self.assertEqual(command.workspace_id, "workspace-a")
            self.assertEqual(
                command.new_secret_reference,
                SecretReference("secret://provider/key-b"),
            )
            self.assertFalse(hasattr(command, "actor_id"))
            self.assertFalse(hasattr(command, "actor_scopes"))

    def test_approval_and_advance_routes_delegate_without_phase_policy(self) -> None:
        approve_request = RouteRequest(
            "http",
            "command.gateway-key-rotation.request-approval",
            ControlPlaneServiceRole.APPROVAL,
            {"workspace_id": "workspace-a", "rotation_id": "rotation-a"},
            {"session_id": "session-a", "idempotency_key": "approval-request-a"},
            principal(PolicyScope.DELEGATION_KEY_ROTATE),
        )
        decide = RouteRequest(
            "mcp",
            "command.gateway-key-rotation.decide",
            ControlPlaneServiceRole.APPROVAL,
            {"workspace_id": "workspace-a", "rotation_id": "rotation-a"},
            {
                "session_id": "session-a",
                "approval_request_id": "approval-a",
                "decision": "approved",
                "idempotency_key": "approval-decision-a",
            },
            principal(PolicyScope.DELEGATION_KEY_ROTATE_APPROVE),
        )
        advance = RouteRequest(
            "http",
            "command.gateway-key-rotation.advance",
            ControlPlaneServiceRole.PLANNING,
            {"workspace_id": "workspace-a", "rotation_id": "rotation-a"},
            {"expected_version": 3, "idempotency_key": "advance-a"},
            principal(PolicyScope.DELEGATION_KEY_ROTATE),
        )

        self.assertEqual(
            self.approval.handle(approve_request)["rotation"]["status"],
            "awaiting-approval",
        )
        self.assertEqual(
            self.approval.handle(decide)["rotation"]["status"],
            "approved",
        )
        self.assertEqual(self.planning.handle(advance)["phase"], "generation")
        self.assertEqual(
            [call[0] for call in self.rotations.calls],
            ["request-approval", "decide", "advance"],
        )

    def test_public_reads_are_secret_free_and_share_the_facade(self) -> None:
        routes = (
            "read.gateway-key-rotation.list",
            "read.gateway-key-rotation.detail",
            "read.gateway-key-rotation.transitions",
        )
        documents = []
        for surface, route_id in zip(("http", "mcp", "http"), routes):
            documents.append(
                self.reads.handle(
                    RouteRequest(
                        surface,
                        route_id,
                        ControlPlaneServiceRole.READS,
                        {
                            "workspace_id": "workspace-a",
                            "rotation_id": "rotation-a",
                        },
                        {},
                        principal(PolicyScope.DELEGATION_KEY_READ),
                    )
                )
            )

        public_text = repr(documents)
        self.assertNotIn("secret://", public_text)
        self.assertNotIn("private", public_text.lower())
        self.assertNotIn("provider_registration", public_text)
        self.assertEqual(
            [call[0] for call in self.rotations.calls],
            ["list", "detail", "transitions"],
        )

    def test_route_permissions_reject_before_facade_access(self) -> None:
        denied = self.request("http")
        object.__setattr__(denied, "principal", principal(PolicyScope.PLAN_EXECUTE))

        with self.assertRaises(CpkServerApplicationError) as raised:
            self.planning.handle(denied)

        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(self.rotations.calls, [])


if __name__ == "__main__":
    unittest.main()
