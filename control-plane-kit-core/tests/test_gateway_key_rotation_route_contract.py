import unittest

from control_plane_kit_core.operations import (
    ApplicationServiceBinding,
    ControlPlaneServiceRole,
    DeploymentProgramBoundary,
)
from control_plane_kit_core.operations.http import (
    HttpApiContract,
    HttpMethod,
    HttpOperationSafety,
    operator_command_http_routes,
    operator_read_http_routes,
)
from control_plane_kit_core.operations.mcp import McpStreamableHttpContract
from control_plane_kit_core.operations.parity import (
    ApprovalPolicy,
    operator_command_parity,
    operator_read_projection_parity,
)
from control_plane_kit_core.operations.transactions import (
    ExternalEffectPolicy,
    ServiceTransactionBoundary,
    StoreParticipation,
    UnitOfWorkBoundary,
)


def _unit_of_work() -> UnitOfWorkBoundary:
    return UnitOfWorkBoundary(
        program=DeploymentProgramBoundary(
            tuple(
                ApplicationServiceBinding(role, f"{role.value}-service")
                for role in ControlPlaneServiceRole
            )
        ),
        services=tuple(
            ServiceTransactionBoundary(
                role,
                (
                    StoreParticipation.READ_ONLY
                    if role is ControlPlaneServiceRole.READS
                    else StoreParticipation.NONE
                    if role is ControlPlaneServiceRole.AUTHORIZATION
                    else StoreParticipation.READ_WRITE
                ),
                owns_transaction=role
                not in {
                    ControlPlaneServiceRole.READS,
                    ControlPlaneServiceRole.AUTHORIZATION,
                },
                external_effect_policy=(
                    ExternalEffectPolicy.AFTER_COMMIT
                    if role is ControlPlaneServiceRole.EXECUTION
                    else ExternalEffectPolicy.FORBIDDEN
                ),
            )
            for role in ControlPlaneServiceRole
        )
    )


class GatewayKeyRotationRouteContractTests(unittest.TestCase):
    def test_rotation_commands_have_one_shared_http_mcp_contract(self) -> None:
        http = HttpApiContract(operator_command_http_routes())
        parity = operator_command_parity(
            http,
            McpStreamableHttpContract(),
            _unit_of_work(),
        )

        expected = {
            "command.gateway-key-rotation.request": (
                HttpMethod.POST,
                ControlPlaneServiceRole.PLANNING,
                ApprovalPolicy.SUBMITS_FOR_APPROVAL,
            ),
            "command.gateway-key-rotation.request-approval": (
                HttpMethod.POST,
                ControlPlaneServiceRole.APPROVAL,
                ApprovalPolicy.SUBMITS_FOR_APPROVAL,
            ),
            "command.gateway-key-rotation.decide": (
                HttpMethod.POST,
                ControlPlaneServiceRole.APPROVAL,
                ApprovalPolicy.DECIDES_APPROVAL,
            ),
            "command.gateway-key-rotation.advance": (
                HttpMethod.POST,
                ControlPlaneServiceRole.PLANNING,
                ApprovalPolicy.REQUIRES_CURRENT_APPROVAL,
            ),
        }
        for route_id, (method, role, approval) in expected.items():
            route = http.route(route_id)
            binding = next(
                item for item in parity.commands if item.http_route_id == route_id
            )
            self.assertIs(route.method, method)
            self.assertIs(route.service_role, role)
            self.assertIs(route.safety, HttpOperationSafety.COMMAND)
            self.assertIs(binding.service_role, role)
            self.assertIs(binding.approval, approval)
            self.assertEqual(binding.http_route_id, route_id)
            self.assertNotEqual(binding.mcp_tool_name, route_id)

    def test_rotation_reads_are_secret_free_named_projections(self) -> None:
        http = HttpApiContract(operator_read_http_routes())
        parity = operator_read_projection_parity(
            http,
            McpStreamableHttpContract(),
        )

        expected = {
            "read.gateway-key-rotation.list": "GatewayKeyRotationCollectionReadResponse",
            "read.gateway-key-rotation.detail": "GatewayKeyRotationDetailReadResponse",
            "read.gateway-key-rotation.transitions": (
                "GatewayKeyRotationTransitionCollectionReadResponse"
            ),
        }
        for route_id, schema in expected.items():
            route = http.route(route_id)
            binding = next(
                item for item in parity.projections if item.http_route_id == route_id
            )
            self.assertIs(route.method, HttpMethod.GET)
            self.assertIs(route.service_role, ControlPlaneServiceRole.READS)
            self.assertIs(route.safety, HttpOperationSafety.READ_ONLY)
            self.assertEqual(route.response_schema.name, schema)
            self.assertEqual(binding.projection_schema, schema)


if __name__ == "__main__":
    unittest.main()
