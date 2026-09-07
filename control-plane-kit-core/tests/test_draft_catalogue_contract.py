"""Public contract additions for #1762, without duplicating graph laws."""

import unittest

from control_plane_kit_core.operations import (
    ApprovalPolicy, CommandIdempotencyPolicy, HttpApiContract, HttpAuthScope,
    HttpOperationSafety, McpStreamableHttpContract, operator_command_http_routes,
    operator_command_parity, operator_read_http_routes, operator_read_projection_parity,
    canonical_operator_read_projection_set,
)
from test_command_parity_contract import _uow


class DraftCatalogueContractTests(unittest.TestCase):
    def test_catalogue_command_routes_require_idempotency_and_do_not_request_approval(self):
        routes = {route.route_id: route for route in operator_command_http_routes()}
        parity = operator_command_parity(
            HttpApiContract(tuple(routes.values())), McpStreamableHttpContract(), _uow(),
        )
        bindings = {binding.http_route_id: binding for binding in parity.commands}
        for suffix, path, tool in (
            ("create", "/desired-topology-drafts", "create_desired_topology_draft"),
            ("revise", "/desired-topology-drafts/{draft_id}/revisions", "revise_desired_topology_draft"),
        ):
            route_id = "command.desired-topology-draft." + suffix
            with self.subTest(route=route_id):
                self.assertIn(route_id, routes)
                route = routes[route_id]
                self.assertEqual(route.method.value, "POST")
                self.assertEqual(route.path_template, "/workspaces/{workspace_id}" + path)
                self.assertEqual(route.auth_scope, HttpAuthScope.PLAN_WRITE)
                self.assertEqual(route.safety, HttpOperationSafety.COMMAND)
                self.assertGreater(route.request_schema.max_bytes, 0)
                self.assertGreater(route.response_schema.max_bytes, 0)
                self.assertEqual(bindings[route_id].mcp_tool_name, tool)
                self.assertEqual(bindings[route_id].idempotency, CommandIdempotencyPolicy.REQUIRED)
                self.assertEqual(bindings[route_id].approval, ApprovalPolicy.NOT_REQUIRED)

    def test_catalogue_reads_have_distinct_http_and_read_only_mcp_contracts(self):
        routes = {route.route_id: route for route in operator_read_http_routes()}
        parity = operator_read_projection_parity(HttpApiContract(tuple(routes.values())), McpStreamableHttpContract())
        bindings = {binding.http_route_id: binding for binding in parity.projections}
        projections = {value.operation_id: value for value in canonical_operator_read_projection_set().projections}
        for route_id, path, tool, paged in (
            ("read.desired-topology-drafts", "/desired-topology-drafts", "list_desired_topology_drafts", True),
            ("read.desired-topology-draft-revisions", "/desired-topology-drafts/{draft_id}/revisions", "list_desired_topology_draft_revisions", True),
            ("read.desired-topology-draft-revision", "/desired-topology-drafts/{draft_id}/revisions/{revision}", "get_desired_topology_draft_revision", False),
        ):
            with self.subTest(route=route_id):
                self.assertIn(route_id, routes)
                route = routes[route_id]
                self.assertEqual(route.method.value, "GET")
                self.assertEqual(route.path_template, "/workspaces/{workspace_id}" + path)
                self.assertEqual(route.auth_scope, HttpAuthScope.READ)
                self.assertEqual(route.safety, HttpOperationSafety.READ_ONLY)
                self.assertGreater(route.response_schema.max_bytes, 0)
                self.assertEqual(bindings[route_id].mcp_tool_name, tool)
                self.assertIn(route_id, projections)
                self.assertEqual(projections[route_id].paged, paged)
                self.assertEqual(projections[route_id].max_page_size, 100 if paged else None)
