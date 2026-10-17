"""#1899 pure declaration only; no claim of Servers transport adoption."""

import unittest

from control_plane_kit_core.operations import (
    ControlPlaneServiceRole, HttpApiContract, HttpSchemaRef, McpStreamableHttpContract,
    ReadProjectionSet, canonical_operator_read_projection_set,
    operator_read_http_routes, operator_read_projection_parity,
)


class ReceiverAuthoringContextContractTests(unittest.TestCase):
    def test_one_closed_nonpaged_read_has_explicit_query_and_body_bounds(self):
        routes = operator_read_http_routes()
        selected = [route for route in routes if route.route_id == "read.receiver-authoring-context"]
        self.assertEqual(len(selected), 1, "#1899 missing receiver authoring context declaration")
        route = selected[0]
        self.assertEqual((route.method.value, route.path_template, route.service_role,
                          route.auth_scope.value, route.safety.value),
            ("GET", "/workspaces/{workspace_id}/receiver-authoring-context",
             ControlPlaneServiceRole.READS, "read", "read-only"))
        self.assertEqual(route.request_schema, HttpSchemaRef("ReceiverAuthoringContextReadRequest", max_bytes=16384))
        self.assertEqual(route.response_schema, HttpSchemaRef("ReceiverAuthoringContextReadResponse", max_bytes=1048576))
        projection_set = canonical_operator_read_projection_set()
        projection = projection_set.projection(route.route_id)
        self.assertEqual((projection.kind.value, projection.policy.value, projection.response_schema,
                          projection.requires_workspace_scope, projection.paged, projection.max_page_size),
            ("receiver-authoring-context", "public-receiver-authoring-context", "ReceiverAuthoringContextReadResponse",
             True, False, None))
        self.assertEqual(ReadProjectionSet.from_descriptor(projection_set.descriptor()), projection_set)
        parity = operator_read_projection_parity(HttpApiContract(routes), McpStreamableHttpContract())
        bindings = [item for item in parity.projections if item.operation_id == route.route_id]
        self.assertEqual([(item.http_route_id, item.mcp_tool_name, item.projection_schema) for item in bindings],
            [(route.route_id, "get_receiver_authoring_context", "ReceiverAuthoringContextReadResponse")])
        # The new query override must not silently alter existing GET schemas.
        for other in routes:
            if other.route_id != route.route_id:
                self.assertEqual(other.request_schema, HttpSchemaRef("EmptyRequest", max_bytes=1024))
