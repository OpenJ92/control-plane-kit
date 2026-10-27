"""Public workspace administration route/parity contract owned by Core (#2053)."""
import unittest
from dataclasses import replace

from control_plane_kit_core.operations import (
    ApplicationServiceBinding, ApprovalPolicy, ControlPlaneServiceRole,
    DeploymentProgramBoundary, ExternalEffectPolicy, HttpApiContract, HttpApiRouteContract,
    HttpAuthScope, HttpMethod, HttpOperationSafety, InvalidAdapterParityContract,
    InvalidHttpApiContract, InvalidReadProjectionContract, McpStreamableHttpContract,
    ReadProjectionContract, ReadProjectionPolicy, canonical_operator_read_projection_set,
    ServiceTransactionBoundary, StoreParticipation, UnitOfWorkBoundary,
    operator_adapter_security_parity, operator_command_http_routes, operator_command_parity,
    operator_read_http_routes, operator_read_projection_parity,
)


COMMANDS = (
    ("workspace-administration.create", "/administration/workspaces", "create_workspace_as_administrator", "WorkspaceAdministrationResult"),
    ("workspace-permission.grant", "/workspaces/{workspace_id}/permissions/grant", "grant_workspace_permissions", "WorkspacePermissionResult"),
    ("workspace-permission.revoke", "/workspaces/{workspace_id}/permissions/revoke", "revoke_workspace_permissions", "WorkspacePermissionResult"),
    ("managed-runtime-access.grant", "/workspaces/{workspace_id}/runtime-management/grant", "grant_managed_runtime_access", "ManagedRuntimeAssignmentResult"),
    ("managed-runtime-access.revoke", "/workspaces/{workspace_id}/runtime-management/revoke", "revoke_managed_runtime_access", "ManagedRuntimeAssignmentResult"),
)
READS = (
    ("workspace-permissions", "/workspaces/{workspace_id}/permissions", "list_workspace_permissions", "WorkspacePermissionPage"),
    ("workspace-permission", "/workspaces/{workspace_id}/permissions/inspect", "inspect_workspace_permission", "WorkspacePermissionRecord"),
    ("workspace-permission-history", "/workspaces/{workspace_id}/permissions/history", "list_workspace_permission_history", "WorkspacePermissionPage"),
    ("managed-runtime-assignments", "/workspaces/{workspace_id}/runtime-management", "list_managed_runtime_assignments", "ManagedRuntimeAssignmentPage"),
    ("managed-runtime-assignment", "/workspaces/{workspace_id}/runtime-management/inspect", "inspect_managed_runtime_assignment", "ManagedRuntimeAssignment"),
    ("managed-runtime-assignment-history", "/workspaces/{workspace_id}/runtime-management/history", "list_managed_runtime_assignment_history", "ManagedRuntimeAssignmentPage"),
)


def administration_uow():
    program = DeploymentProgramBoundary(tuple(ApplicationServiceBinding(role, role.value)
        for role in ControlPlaneServiceRole))
    return UnitOfWorkBoundary(program, tuple(
        ServiceTransactionBoundary(role, StoreParticipation.READ_ONLY)
        if role is ControlPlaneServiceRole.READS else
        ServiceTransactionBoundary(role, StoreParticipation.READ_WRITE, owns_transaction=True,
            external_effect_policy=ExternalEffectPolicy.AFTER_COMMIT)
        if role is ControlPlaneServiceRole.EXECUTION else
        ServiceTransactionBoundary(role, StoreParticipation.READ_WRITE, owns_transaction=True)
        for role in ControlPlaneServiceRole))


class WorkspaceAdministrationContractTests(unittest.TestCase):
    def routes(self, values, prefix, contracts):
        by_id = {route.route_id: route for route in contracts}
        wanted = {prefix + name for name, *_ in values}
        self.assertTrue(wanted <= by_id.keys(), "workspace administration routes are missing")
        return by_id

    def test_administrative_commands_have_distinct_bounded_authenticated_contracts(self):
        routes = self.routes(COMMANDS, "command.", operator_command_http_routes())
        for name, path, _, response in COMMANDS:
            with self.subTest(operation=name):
                route = routes["command." + name]
                self.assertEqual(route.path_template, path)
                self.assertIs(route.method, HttpMethod.POST)
                self.assertIs(route.service_role, ControlPlaneServiceRole.AUTHORIZATION)
                self.assertIs(route.auth_scope, HttpAuthScope.ADMIN)
                self.assertIs(route.safety, HttpOperationSafety.COMMAND)
                self.assertEqual(route.request_schema.max_bytes, 65536)
                self.assertEqual((route.response_schema.name, route.response_schema.max_bytes), (response, 65536))
                self.assertTrue({400, 401, 403, 404, 409, 413, 503} <= set(route.errors.statuses))
                self.assertEqual(HttpApiRouteContract.from_descriptor(route.descriptor()), route)

    def test_administrative_reads_preserve_full_escaped_identity_and_page_budgets(self):
        routes = self.routes(READS, "read.", operator_read_http_routes())
        for name, path, _, response in READS:
            with self.subTest(projection=name):
                route = routes["read." + name]
                self.assertEqual(route.path_template, path)
                self.assertIs(route.method, HttpMethod.GET)
                self.assertIs(route.service_role, ControlPlaneServiceRole.AUTHORIZATION)
                self.assertIs(route.auth_scope, HttpAuthScope.ADMIN)
                self.assertIs(route.safety, HttpOperationSafety.READ_ONLY)
                self.assertEqual(route.request_schema.max_bytes, 65536)
                self.assertEqual(route.response_schema.name, response)
                self.assertEqual(route.response_schema.max_bytes, 1048576 if response.endswith("Page") else 65536)
                self.assertIn(413, route.errors.statuses)

    def test_mcp_bindings_share_the_exact_administration_owner_and_result_contracts(self):
        self.routes(COMMANDS, "command.", operator_command_http_routes())
        self.routes(READS, "read.", operator_read_http_routes())
        http = HttpApiContract(operator_read_http_routes() + operator_command_http_routes())
        mcp = McpStreamableHttpContract()
        reads = operator_read_projection_parity(http, mcp)
        commands = operator_command_parity(http, mcp, administration_uow())
        security = operator_adapter_security_parity(projection_parity=reads, command_parity=commands)
        for name, _, tool, response in COMMANDS:
            binding = next(item for item in commands.commands if item.operation_id == name)
            self.assertEqual((binding.http_route_id, binding.mcp_tool_name, binding.response_schema),
                ("command." + name, tool, response))
            self.assertIs(binding.service_role, ControlPlaneServiceRole.AUTHORIZATION)
            self.assertIs(binding.approval, ApprovalPolicy.NOT_REQUIRED)
            self.assertIs(security.operation(name).auth_scope, HttpAuthScope.ADMIN)
        for name, _, tool, response in READS:
            binding = next(item for item in reads.projections if item.operation_id == "read." + name)
            self.assertEqual((binding.http_route_id, binding.mcp_tool_name, binding.projection_schema),
                ("read." + name, tool, response))
            self.assertIs(binding.service_role, ControlPlaneServiceRole.AUTHORIZATION)
            self.assertIs(security.operation("read." + name).safety, HttpOperationSafety.READ_ONLY)

    def test_legacy_creation_retains_its_existing_response_contract(self):
        routes = {route.route_id: route for route in operator_command_http_routes()}
        legacy = routes["command.workspace.create"]
        self.assertEqual((legacy.path_template, legacy.response_schema.name),
            ("/workspaces", "WorkspaceReadResponse"))

    def test_administration_projection_cannot_disagree_with_http_mutation_safety(self):
        routes = tuple(replace(route, method=HttpMethod.POST, safety=HttpOperationSafety.COMMAND)
            if route.route_id == "read.workspace-permissions" else route
            for route in operator_read_http_routes())
        with self.assertRaises(InvalidAdapterParityContract):
            operator_read_projection_parity(HttpApiContract(routes), McpStreamableHttpContract())

    def test_read_route_owner_and_scope_pairs_cannot_be_interchanged(self):
        route = next(route for route in operator_read_http_routes()
            if route.route_id == "read.workspace-permissions")
        for role, scope in (
            (ControlPlaneServiceRole.AUTHORIZATION, HttpAuthScope.READ),
            (ControlPlaneServiceRole.READS, HttpAuthScope.ADMIN),
            (ControlPlaneServiceRole.PLANNING, HttpAuthScope.ADMIN),
        ):
            with self.subTest(role=role, scope=scope):
                with self.assertRaises(InvalidHttpApiContract):
                    replace(route, service_role=role, auth_scope=scope)

    def test_canonical_administration_projections_preserve_workspace_target_and_policy(self):
        projections = canonical_operator_read_projection_set()
        administrative = tuple(value for value in projections.projections
            if value.service_role is ControlPlaneServiceRole.AUTHORIZATION)
        self.assertEqual({value.operation_id for value in administrative},
            {"read." + name for name, *_ in READS})
        for name, _, _, schema in READS:
            with self.subTest(projection=name):
                value = projections.projection("read." + name)
                self.assertEqual((value.kind.value, value.response_schema), (name, schema))
                self.assertIs(value.policy, ReadProjectionPolicy.AUTHORIZED_WORKSPACE_ADMINISTRATION)
                self.assertIs(value.auth_scope, HttpAuthScope.ADMIN)
                self.assertIs(value.safety, HttpOperationSafety.READ_ONLY)
                self.assertTrue(value.requires_workspace_scope)
                paged = schema.endswith("Page")
                self.assertEqual((value.paged, value.max_page_size), (paged, 10 if paged else None))
                self.assertEqual(ReadProjectionContract.from_descriptor(value.descriptor()), value)
                with self.assertRaises(InvalidReadProjectionContract):
                    replace(value, auth_scope=HttpAuthScope.READ)
                with self.assertRaises(InvalidReadProjectionContract):
                    replace(value, service_role=ControlPlaneServiceRole.READS)


if __name__ == "__main__":
    unittest.main()
