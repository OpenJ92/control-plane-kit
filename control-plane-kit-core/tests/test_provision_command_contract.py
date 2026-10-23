"""Confidential intake has one HTTP command and a separately authorized safe read."""

from dataclasses import replace
import unittest

from control_plane_kit_core import operations
from test_command_parity_contract import _uow


class ProvisionCommandContractTests(unittest.TestCase):
    def test_http_only_command_cannot_be_advertised_by_custom_paired_contract(self):
        routes = operations.HttpApiContract(operations.operator_command_http_routes())
        paired = operations.operator_command_parity(routes, operations.McpStreamableHttpContract(), _uow())
        binding = operations.AdapterCommandBinding(
            operation_id="secret-material.provision", service_role=operations.ControlPlaneServiceRole.EXECUTION,
            request_schema="ProvisionSecretMaterialRequest", response_schema="ProvisionedSecretMaterialReceipt",
            http_route_id="command.secret-material.provision", mcp_tool_name="provision_secret_material",
            idempotency=operations.CommandIdempotencyPolicy.REQUIRED, approval=operations.ApprovalPolicy.NOT_REQUIRED,
        )
        descriptor = paired.descriptor()
        descriptor["commands"].append(binding.descriptor())
        for build in (lambda: replace(paired, commands=(*paired.commands, binding)),
                      lambda: operations.AdapterCommandParityContract.from_descriptor(descriptor)):
            with self.subTest(construction=build), self.assertRaises(operations.InvalidAdapterParityContract):
                build()

    def test_intake_is_canonical_http_only_with_confidential_payload_and_bound(self):
        routes = operations.HttpApiContract(operations.operator_command_http_routes())
        route_id = "command.secret-material.provision"
        self.assertIn(route_id, {route.route_id for route in routes.routes})
        route = routes.route(route_id)
        self.assertEqual(route.path_template, "/workspaces/{workspace_id}/secret-material/provision")
        self.assertEqual(route.service_role, operations.ControlPlaneServiceRole.EXECUTION)
        self.assertEqual(route.safety, operations.HttpOperationSafety.COMMAND)
        self.assertEqual(route.request_schema.name, "ProvisionSecretMaterialRequest")
        self.assertEqual(route.request_schema.max_bytes, 98304)
        self.assertEqual(route.response_schema.name, "ProvisionedSecretMaterialReceipt")
        command = operations.canonical_operator_command_workflow_contract().command("secret-material.provision")
        self.assertEqual(command.payload_policy.value, "confidential-ephemeral")
        self.assertEqual(command.idempotency, operations.CommandIdempotencyPolicy.REQUIRED)
        self.assertEqual(command.approval, operations.ApprovalPolicy.NOT_REQUIRED)
        self.assertFalse(command.requires_open_session)
        paired = operations.operator_command_parity(routes, operations.McpStreamableHttpContract(), _uow())
        paired_ids = {binding.http_route_id for binding in paired.commands}
        self.assertNotIn(route_id, paired_ids)
        self.assertEqual(operations.HTTP_ONLY_COMMAND_ROUTE_IDS, frozenset((route_id,)))
        self.assertEqual({route.route_id for route in routes.routes} - paired_ids,
                         operations.HTTP_ONLY_COMMAND_ROUTE_IDS)
        self.assertEqual(routes.route("command.delegation-key.generate").request_schema.max_bytes, 65536)

    def test_safe_exact_status_remains_http_mcp_paired(self):
        routes = operations.HttpApiContract(operations.operator_read_http_routes())
        route_id = "read.secret-material-provision"
        self.assertIn(route_id, {route.route_id for route in routes.routes})
        route = routes.route(route_id)
        self.assertEqual(route.path_template, "/workspaces/{workspace_id}/secret-material-provisions/{request_id}")
        self.assertEqual(route.safety, operations.HttpOperationSafety.READ_ONLY)
        projection = next(item for item in operations.canonical_operator_read_projection_set().projections
                          if item.operation_id == route_id)
        self.assertFalse(projection.paged)
        self.assertTrue(projection.requires_workspace_scope)
        self.assertEqual(projection.policy.value, "authorized-provision-receipt")
        self.assertEqual(projection.response_schema, "ProvisionedSecretMaterialReceipt")
        paired = operations.operator_read_projection_parity(routes, operations.McpStreamableHttpContract())
        binding = next(item for item in paired.projections if item.operation_id == route_id)
        self.assertEqual(binding.mcp_tool_name, "get_secret_material_provision")
