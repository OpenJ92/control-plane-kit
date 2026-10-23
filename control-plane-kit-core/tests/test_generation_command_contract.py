"""Public generation contract: effect roles, exact status and shared parity."""

import unittest

from control_plane_kit_core.operations import (
    ApprovalPolicy, ControlPlaneServiceRole, HttpApiContract,
    HttpOperationSafety, McpStreamableHttpContract,
    canonical_operator_command_workflow_contract,
    canonical_operator_read_projection_set,
    operator_command_http_routes, operator_command_parity,
    operator_read_http_routes, operator_read_projection_parity,
)
from test_command_parity_contract import _uow


class GenerationCommandContractTests(unittest.TestCase):
    def test_generate_and_reconcile_use_operator_execution_without_deployment_approval(self):
        routes = HttpApiContract(operator_command_http_routes())
        parity = operator_command_parity(routes, McpStreamableHttpContract(), _uow())
        commands = canonical_operator_command_workflow_contract()
        expected = (
            ("delegation-key.generate", "generate_delegation_key",
             "/workspaces/{workspace_id}/delegation-keys/generate", "GenerateDelegationKeyRequest"),
            ("delegation-key-generation.reconcile", "reconcile_delegation_key_generation",
             "/workspaces/{workspace_id}/delegation-key-generations/{request_id}/reconcile",
             "ReconcileDelegationKeyGenerationRequest"),
        )
        for operation, tool, path, schema in expected:
            with self.subTest(operation=operation):
                self.assertIn("command." + operation, {route.route_id for route in routes.routes})
                route = routes.route("command." + operation)
                self.assertEqual(route.service_role, ControlPlaneServiceRole.EXECUTION)
                self.assertEqual(route.safety, HttpOperationSafety.COMMAND)
                self.assertEqual(route.path_template, path)
                self.assertEqual(route.request_schema.name, schema)
                self.assertEqual(route.response_schema.name, "GeneratedDelegationKeyReceipt")
                binding = next(value for value in parity.commands if value.operation_id == operation)
                self.assertEqual(binding.mcp_tool_name, tool)
                self.assertEqual(binding.approval, ApprovalPolicy.NOT_REQUIRED)
                contract = commands.command(operation)
                self.assertEqual(contract.service_role, ControlPlaneServiceRole.EXECUTION)
                self.assertFalse(contract.requires_open_session)

    def test_exact_status_is_unpaged_read_with_explicit_receipt_policy(self):
        routes = HttpApiContract(operator_read_http_routes())
        route_id = "read.delegation-key-generation"
        self.assertIn(route_id, {route.route_id for route in routes.routes})
        route = routes.route(route_id)
        self.assertEqual(route.path_template,
                         "/workspaces/{workspace_id}/delegation-key-generations/{request_id}")
        self.assertEqual(route.service_role, ControlPlaneServiceRole.READS)
        self.assertEqual(route.safety, HttpOperationSafety.READ_ONLY)
        projection = next(value for value in canonical_operator_read_projection_set().projections
                          if value.operation_id == route_id)
        self.assertFalse(projection.paged)
        self.assertTrue(projection.requires_workspace_scope)
        self.assertEqual(projection.policy.value, "authorized-generation-receipt")
        self.assertEqual(projection.response_schema, "GeneratedDelegationKeyReceipt")
        parity = operator_read_projection_parity(routes, McpStreamableHttpContract())
        binding = next(value for value in parity.projections if value.operation_id == route_id)
        self.assertEqual(binding.mcp_tool_name, "get_delegation_key_generation")
