import unittest

from control_plane_kit_core.operations import (
    HttpApiContract,
    HttpAuthScope,
    HttpOperationSafety,
    McpStreamableHttpContract,
    ReadProjectionKind,
    canonical_operator_read_projection_set,
    operator_read_http_routes,
    operator_read_projection_parity,
)


class TemporalHistoryReadContractTests(unittest.TestCase):
    def test_history_pages_have_exact_http_routes_and_paged_projections(self) -> None:
        routes = HttpApiContract(operator_read_http_routes())
        projections = canonical_operator_read_projection_set()
        expected = {
            "read.activity": (
                "/workspaces/{workspace_id}/activity",
                "read.activity-timeline",
                ReadProjectionKind.ACTIVITY_TIMELINE,
            ),
            "read.sessions": (
                "/workspaces/{workspace_id}/sessions",
                "read.open-sessions",
                ReadProjectionKind.OPEN_SESSIONS,
            ),
            "read.session-plans": (
                "/workspaces/{workspace_id}/sessions/{session_id}/plans",
                "read.session-plans",
                ReadProjectionKind.SESSION_PLANS,
            ),
            "read.session-approvals": (
                "/workspaces/{workspace_id}/sessions/{session_id}/approvals",
                "read.session-approvals",
                ReadProjectionKind.SESSION_APPROVALS,
            ),
            "read.pending-approvals": (
                "/workspaces/{workspace_id}/approvals/pending",
                "read.pending-approvals",
                ReadProjectionKind.PENDING_APPROVALS,
            ),
            "read.plan-runs": (
                "/workspaces/{workspace_id}/plans/{plan_id}/runs",
                "read.plan-runs",
                ReadProjectionKind.PLAN_RUNS,
            ),
        }

        for route_id, (path, operation_id, kind) in expected.items():
            with self.subTest(operation_id=operation_id):
                route = routes.route(route_id)
                projection = projections.projection(operation_id)
                self.assertEqual(route.path_template, path)
                self.assertIs(route.auth_scope, HttpAuthScope.READ)
                self.assertIs(route.safety, HttpOperationSafety.READ_ONLY)
                self.assertIs(projection.kind, kind)
                self.assertTrue(projection.paged)
                self.assertEqual(projection.max_page_size, 100)

    def test_http_and_mcp_parity_names_all_temporal_pages(self) -> None:
        parity = operator_read_projection_parity(
            HttpApiContract(operator_read_http_routes()),
            McpStreamableHttpContract(),
        )
        selected = {
            binding.operation_id: binding.mcp_tool_name
            for binding in parity.projections
            if binding.operation_id
            in {
                "read.activity-timeline",
                "read.open-sessions",
                "read.session-plans",
                "read.session-approvals",
                "read.pending-approvals",
                "read.plan-runs",
            }
        }

        self.assertEqual(
            selected,
            {
                "read.activity-timeline": "get_activity_timeline",
                "read.open-sessions": "list_open_sessions",
                "read.session-plans": "list_session_plans",
                "read.session-approvals": "list_session_approvals",
                "read.pending-approvals": "list_pending_approvals",
                "read.plan-runs": "list_plan_runs",
            },
        )


if __name__ == "__main__":
    unittest.main()
