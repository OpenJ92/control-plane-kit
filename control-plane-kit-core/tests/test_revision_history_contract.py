"""#1773 declares two bounded read collections without mutation authority."""
import unittest

from control_plane_kit_core.operations import (
    HttpApiContract, HttpAuthScope, HttpOperationSafety, McpStreamableHttpContract,
    canonical_operator_read_projection_set, operator_read_http_routes,
    operator_read_projection_parity,
)


class RevisionHistoryContractTests(unittest.TestCase):
    def test_revision_history_has_exact_read_routes_mcp_parity_and_ten_item_limits(self):
        routes = {route.route_id: route for route in operator_read_http_routes()}
        projections = canonical_operator_read_projection_set()
        bindings = {binding.http_route_id: binding for binding in
                    operator_read_projection_parity(HttpApiContract(tuple(routes.values())),
                                                    McpStreamableHttpContract()).projections}
        for collection in ("preparations", "attempts"):
            route_id = "read.desired-topology-draft-revision-" + collection
            with self.subTest(collection=collection):
                self.assertIn(route_id, routes, "missing revision history read route")
                route = routes[route_id]
                self.assertEqual(route.path_template,
                    "/workspaces/{workspace_id}/desired-topology-drafts/{draft_id}/revisions/{revision}/" + collection)
                self.assertEqual(route.method.value, "GET")
                self.assertEqual(route.auth_scope, HttpAuthScope.READ)
                self.assertEqual(route.safety, HttpOperationSafety.READ_ONLY)
                self.assertEqual(bindings[route_id].mcp_tool_name,
                                 "list_desired_topology_draft_revision_" + collection)
                projection = projections.projection(route_id)
                self.assertTrue(projection.paged)
                self.assertTrue(projection.requires_workspace_scope)
                self.assertEqual(projection.max_page_size, 10)
