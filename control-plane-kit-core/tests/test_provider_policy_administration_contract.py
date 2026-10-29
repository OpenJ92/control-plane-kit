"""Public provider-policy administration route/parity contract owned by Core (#2062)."""
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
    operator_command_http_routes,
    operator_command_parity,
    operator_read_http_routes,
    operator_read_projection_parity,
    canonical_operator_read_projection_set,
    ReadProjectionPolicy,
)


COMMANDS = (
    (
        "provider-policy.grant",
        "/administration/provider-policies/{logical_provider_id}/grants",
        "GrantProviderPolicyRequest",
        "ProviderPolicyCommandRecordResponse",
        "grant_provider_policy",
    ),
    (
        "provider-policy.revoke",
        "/administration/provider-policies/{logical_provider_id}/grants/{grant_id}/revoke",
        "RevokeProviderPolicyRequest",
        "ProviderPolicyCommandRecordResponse",
        "revoke_provider_policy",
    ),
    (
        "provider-policy.reconcile",
        "/administration/provider-policies/{logical_provider_id}/commands/{command_id}/reconcile",
        "ReconcileProviderPolicyRequest",
        "ProviderPolicyCommandRecordResponse",
        "reconcile_provider_policy",
    ),
)

READS = (
    (
        "provider-policy-command",
        "/administration/provider-policies/{logical_provider_id}/commands/{command_id}",
        "ProviderPolicyCommandReadRequest",
        "ProviderPolicyCommandRecordResponse",
        "get_provider_policy_command",
    ),
    (
        "provider-policy-grant",
        "/administration/provider-policies/{logical_provider_id}/grants/{grant_id}",
        "ProviderPolicyGrantReadRequest",
        "ProviderPolicyGrantReadResponse",
        "get_provider_policy_grant",
    ),
    (
        "provider-policy-grants",
        "/administration/provider-policies/{logical_provider_id}/grants",
        "ProviderPolicyGrantListRequest",
        "ProviderPolicyGrantPageResponse",
        "list_provider_policy_grants",
    ),
)


def administration_uow():
    program = DeploymentProgramBoundary(
        tuple(ApplicationServiceBinding(role, role.value) for role in ControlPlaneServiceRole)
    )
    return UnitOfWorkBoundary(
        program,
        tuple(
            ServiceTransactionBoundary(role, StoreParticipation.READ_ONLY)
            if role is ControlPlaneServiceRole.READS
            else ServiceTransactionBoundary(
                role,
                StoreParticipation.READ_WRITE,
                owns_transaction=True,
                external_effect_policy=ExternalEffectPolicy.AFTER_COMMIT,
            )
            if role is ControlPlaneServiceRole.EXECUTION
            else ServiceTransactionBoundary(
                role, StoreParticipation.READ_WRITE, owns_transaction=True
            )
            for role in ControlPlaneServiceRole
        ),
    )


class ProviderPolicyAdministrationContractTests(unittest.TestCase):
    def test_commands_are_closed_bounded_authorization_routes(self):
        routes = {route.route_id: route for route in operator_command_http_routes()}
        for name, path, request, response, _ in COMMANDS:
            with self.subTest(operation=name):
                route = routes["command." + name]
                self.assertEqual(route.path_template, path)
                self.assertIs(route.method, HttpMethod.POST)
                self.assertIs(route.service_role, ControlPlaneServiceRole.AUTHORIZATION)
                self.assertIs(route.auth_scope, HttpAuthScope.ADMIN)
                self.assertIs(route.safety, HttpOperationSafety.COMMAND)
                self.assertEqual((route.request_schema.name, route.request_schema.max_bytes), (request, 65536))
                self.assertEqual((route.response_schema.name, route.response_schema.max_bytes), (response, 65536))
                self.assertEqual(route.errors.statuses, (400, 401, 403, 404, 409, 413, 503))

    def test_reads_distinguish_local_command_and_current_provider_truth(self):
        routes = {route.route_id: route for route in operator_read_http_routes()}
        for name, path, request, response, _ in READS:
            with self.subTest(projection=name):
                route = routes["read." + name]
                self.assertEqual(route.path_template, path)
                self.assertIs(route.method, HttpMethod.GET)
                self.assertIs(route.service_role, ControlPlaneServiceRole.AUTHORIZATION)
                self.assertIs(route.auth_scope, HttpAuthScope.ADMIN)
                self.assertIs(route.safety, HttpOperationSafety.READ_ONLY)
                self.assertEqual((route.request_schema.name, route.request_schema.max_bytes), (request, 65536))
                self.assertEqual(route.response_schema.name, response)
                expected = 1048576 if name == "provider-policy-grants" else 65536
                self.assertEqual(route.response_schema.max_bytes, expected)
                self.assertEqual(route.errors.statuses, (400, 401, 403, 404, 409, 413, 503))

    def test_http_and_mcp_bindings_share_one_owner_and_schema(self):
        http = HttpApiContract(operator_read_http_routes() + operator_command_http_routes())
        mcp = McpStreamableHttpContract()
        reads = operator_read_projection_parity(http, mcp)
        commands = operator_command_parity(http, mcp, administration_uow())
        for name, _, request, response, tool in COMMANDS:
            binding = next(value for value in commands.commands if value.operation_id == name)
            self.assertEqual(
                (
                    binding.http_route_id,
                    binding.mcp_tool_name,
                    binding.request_schema,
                    binding.response_schema,
                ),
                ("command." + name, tool, request, response),
            )
            self.assertIs(binding.service_role, ControlPlaneServiceRole.AUTHORIZATION)
            self.assertIs(binding.approval, ApprovalPolicy.NOT_REQUIRED)
        for name, _, _, response, tool in READS:
            operation = "read." + name
            binding = next(value for value in reads.projections if value.operation_id == operation)
            self.assertEqual(
                (binding.http_route_id, binding.mcp_tool_name, binding.projection_schema),
                (operation, tool, response),
            )
            self.assertIs(binding.service_role, ControlPlaneServiceRole.AUTHORIZATION)

    def test_provider_reads_are_not_misclassified_as_workspace_scoped(self):
        projections = canonical_operator_read_projection_set()
        for name, *_ in READS:
            projection = projections.projection("read." + name)
            self.assertIs(
                projection.policy,
                ReadProjectionPolicy.AUTHORIZED_PROVIDER_POLICY_ADMINISTRATION,
            )
            self.assertFalse(projection.requires_workspace_scope)
            self.assertEqual(
                (projection.paged, projection.max_page_size),
                (name == "provider-policy-grants", 100 if name == "provider-policy-grants" else None),
            )


if __name__ == "__main__":
    unittest.main()
