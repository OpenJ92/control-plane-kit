import unittest

from control_plane_kit_core.operations import (
    ApplicationServiceBinding,
    ApprovalPolicy,
    ControlPlaneServiceRole,
    DeploymentProgramBoundary,
    ExternalEffectPolicy,
    HttpApiContract,
    HttpAuthScope,
    HttpMethod,
    HttpOperationSafety,
    McpStreamableHttpContract,
    ServiceTransactionBoundary,
    StoreParticipation,
    UnitOfWorkBoundary,
    canonical_operator_command_workflow_contract,
    canonical_operator_read_projection_set,
    operator_command_http_routes,
    operator_command_parity,
    operator_read_http_routes,
    operator_read_projection_parity,
)


def _unit_of_work() -> UnitOfWorkBoundary:
    program = DeploymentProgramBoundary(
        tuple(
            ApplicationServiceBinding(role, f"{role.value}-service")
            for role in ControlPlaneServiceRole
        )
    )
    return UnitOfWorkBoundary(
        program=program,
        services=tuple(
            ServiceTransactionBoundary(
                role,
                StoreParticipation.READ_ONLY
                if role is ControlPlaneServiceRole.READS
                else (
                    StoreParticipation.NONE
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
                uses_worker=role is ControlPlaneServiceRole.EXECUTION,
                uses_runtime_authority=role is ControlPlaneServiceRole.EXECUTION,
            )
            for role in ControlPlaneServiceRole
        ),
    )


class PublicIngressApplicationSurfaceContractTests(unittest.TestCase):
    def test_retained_resource_read_has_one_http_and_mcp_projection(self) -> None:
        http = HttpApiContract(operator_read_http_routes())
        route = http.route("read.public-ingress-resources")

        self.assertIs(route.method, HttpMethod.GET)
        self.assertEqual(
            route.path_template,
            "/workspaces/{workspace_id}/public-ingress-resources",
        )
        self.assertIs(route.service_role, ControlPlaneServiceRole.READS)
        self.assertIs(route.auth_scope, HttpAuthScope.READ)
        self.assertIs(route.safety, HttpOperationSafety.READ_ONLY)
        self.assertEqual(
            route.response_schema.name,
            "PublicIngressResourceCollectionReadResponse",
        )

        projection = canonical_operator_read_projection_set().projection(
            "read.public-ingress-resources"
        )
        binding = next(
            binding
            for binding in operator_read_projection_parity(
                http,
                McpStreamableHttpContract(),
            ).projections
            if binding.operation_id == "read.public-ingress-resources"
        )
        self.assertEqual(binding.http_route_id, route.route_id)
        self.assertEqual(binding.mcp_tool_name, "list_public_ingress_resources")
        self.assertEqual(binding.projection_schema, projection.response_schema)

    def test_exact_release_is_a_planning_command_not_an_effect_route(self) -> None:
        http = HttpApiContract(operator_command_http_routes())
        route = http.route("command.public-ingress-reservation.release-plan")

        self.assertIs(route.method, HttpMethod.POST)
        self.assertEqual(
            route.path_template,
            "/workspaces/{workspace_id}/public-ingress-reservations/"
            "{reservation_id}/release-plan",
        )
        self.assertIs(route.service_role, ControlPlaneServiceRole.PLANNING)
        self.assertIs(route.auth_scope, HttpAuthScope.PLAN_WRITE)
        self.assertIs(route.safety, HttpOperationSafety.COMMAND)
        self.assertEqual(
            route.request_schema.name,
            "RequestPublicIngressReservationRelease",
        )
        self.assertEqual(route.response_schema.name, "ActivityPlanningResult")

        workflow = canonical_operator_command_workflow_contract().command(
            "public-ingress-reservation.release-plan"
        )
        self.assertEqual(route.request_schema.name, workflow.request_schema)
        self.assertEqual(route.response_schema.name, workflow.response_schema)

        binding = next(
            binding
            for binding in operator_command_parity(
                http,
                McpStreamableHttpContract(),
                _unit_of_work(),
            ).commands
            if binding.operation_id
            == "public-ingress-reservation.release-plan"
        )
        self.assertEqual(binding.http_route_id, route.route_id)
        self.assertEqual(
            binding.mcp_tool_name,
            "plan_public_ingress_reservation_release",
        )
        self.assertIs(binding.approval, ApprovalPolicy.SUBMITS_FOR_APPROVAL)
        self.assertIs(binding.service_role, ControlPlaneServiceRole.PLANNING)


if __name__ == "__main__":
    unittest.main()
