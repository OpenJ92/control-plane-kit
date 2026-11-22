"""New #1763 public command laws; no duplicate topology semantics."""
import unittest

from control_plane_kit_core.operations import (
    ApprovalPolicy, CommandIdempotencyPolicy, HttpApiContract, HttpAuthScope,
    HttpOperationSafety, McpStreamableHttpContract, operator_command_http_routes,
    operator_command_parity,
)
from test_command_parity_contract import _uow


class DraftSelectionContractTests(unittest.TestCase):
    def test_select_and_tombstone_are_authenticated_idempotent_effect_free_commands(self):
        routes = {route.route_id: route for route in operator_command_http_routes()}
        parity = operator_command_parity(HttpApiContract(tuple(routes.values())),
                                        McpStreamableHttpContract(), _uow())
        bindings = {binding.http_route_id: binding for binding in parity.commands}
        for verb in ("select", "delete"):
            route_id = f"command.desired-topology-draft.{verb}"
            with self.subTest(command=verb):
                self.assertIn(route_id, routes)
                route = routes[route_id]
                self.assertEqual(route.method.value, "POST")
                self.assertEqual(route.path_template,
                    f"/workspaces/{{workspace_id}}/desired-topology-drafts/{{draft_id}}/{verb}")
                self.assertEqual(route.auth_scope, HttpAuthScope.PLAN_WRITE)
                self.assertEqual(route.safety, HttpOperationSafety.COMMAND)
                self.assertGreater(route.request_schema.max_bytes, 0)
                self.assertGreater(route.response_schema.max_bytes, 0)
                binding = bindings[route_id]
                self.assertEqual(binding.mcp_tool_name, f"{verb}_desired_topology_draft")
                self.assertEqual(binding.idempotency, CommandIdempotencyPolicy.REQUIRED)
                self.assertEqual(binding.approval, ApprovalPolicy.NOT_REQUIRED)
