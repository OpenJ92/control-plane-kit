import unittest

from control_plane_kit_core.operations import (
    AdapterParityContract,
    AdapterProjectionBinding,
    ControlPlaneServiceRole,
    HttpApiContract,
    InvalidAdapterParityContract,
    McpStreamableHttpContract,
    operator_read_http_routes,
    operator_read_projection_parity,
)


class AdapterParityContractTests(unittest.TestCase):
    def test_operator_read_projection_parity_maps_frozen_http_routes_and_mcp_tools(self) -> None:
        http_api = HttpApiContract(operator_read_http_routes())
        parity = operator_read_projection_parity(http_api, McpStreamableHttpContract())

        self.assertEqual(
            [
                (
                    binding.operation_id,
                    binding.http_route_id,
                    binding.mcp_tool_name,
                    binding.projection_schema,
                )
                for binding in parity.projections
            ],
            [
                (
                    "read.activity-timeline",
                    "read.activity",
                    "get_activity_timeline",
                    "ActivityTimelineReadResponse",
                ),
                (
                    "read.approval-detail",
                    "read.approval-detail",
                    "get_approval_detail",
                    "ApprovalDetailReadResponse",
                ),
                (
                    "read.control-surface",
                    "read.control-surface",
                    "get_control_surface",
                    "ControlSurfaceReadResponse",
                ),
                (
                    "read.current-graph",
                    "read.current-graph",
                    "get_current_graph",
                    "GraphReadResponse",
                ),
                (
                    "read.desired-graph",
                    "read.desired-graph",
                    "get_desired_graph",
                    "GraphReadResponse",
                ),
                (
                    "read.observed-state",
                    "read.observed-state",
                    "get_observed_state",
                    "ObservedStateReadResponse",
                ),
                (
                    "read.open-sessions",
                    "read.sessions",
                    "list_open_sessions",
                    "OpenSessionsReadResponse",
                ),
                (
                    "read.operator-graph",
                    "read.operator-graph",
                    "get_operator_graph",
                    "OperatorGraphReadResponse",
                ),
                (
                    "read.pending-approvals",
                    "read.pending-approvals",
                    "list_pending_approvals",
                    "PendingApprovalsReadResponse",
                ),
                (
                    "read.plan-detail",
                    "read.plan-detail",
                    "get_plan_detail",
                    "PlanDetailReadResponse",
                ),
                (
                    "read.session-detail",
                    "read.session-detail",
                    "get_session_detail",
                    "SessionDetailReadResponse",
                ),
                (
                    "read.workspace",
                    "read.workspace",
                    "get_workspace",
                    "WorkspaceReadResponse",
                ),
            ],
        )
        self.assertEqual(
            {binding.service_role for binding in parity.projections},
            {ControlPlaneServiceRole.READS},
        )

    def test_parity_descriptor_is_closed_and_round_trips(self) -> None:
        parity = operator_read_projection_parity(
            HttpApiContract(operator_read_http_routes()),
            McpStreamableHttpContract(),
        )
        descriptor = parity.descriptor()

        self.assertEqual(descriptor["kind"], "adapter-parity-contract")
        self.assertEqual(
            descriptor["http_api"]["kind"],
            "http-api-contract",
        )
        self.assertEqual(
            descriptor["mcp"]["kind"],
            "mcp-streamable-http",
        )
        self.assertEqual(AdapterParityContract.from_descriptor(descriptor), parity)

        with self.assertRaises(InvalidAdapterParityContract):
            AdapterParityContract.from_descriptor({**descriptor, "extra": True})

    def test_projection_bindings_must_match_http_route_service_and_schema(self) -> None:
        http_api = HttpApiContract(operator_read_http_routes())

        with self.assertRaises(InvalidAdapterParityContract):
            AdapterParityContract(
                http_api=http_api,
                mcp=McpStreamableHttpContract(),
                projections=(
                    AdapterProjectionBinding(
                        operation_id="read.workspace",
                        service_role=ControlPlaneServiceRole.READS,
                        projection_schema="WrongSchema",
                        http_route_id="read.workspace",
                        mcp_tool_name="get_workspace",
                    ),
                ),
            )

        with self.assertRaises(InvalidAdapterParityContract):
            AdapterParityContract(
                http_api=http_api,
                mcp=McpStreamableHttpContract(),
                projections=(
                    AdapterProjectionBinding(
                        operation_id="read.workspace",
                        service_role=ControlPlaneServiceRole.PLANNING,
                        projection_schema="WorkspaceReadResponse",
                        http_route_id="read.workspace",
                        mcp_tool_name="get_workspace",
                    ),
                ),
            )

    def test_duplicate_operations_routes_or_tools_fail_closed(self) -> None:
        http_api = HttpApiContract(operator_read_http_routes())
        binding = AdapterProjectionBinding(
            operation_id="read.workspace",
            service_role=ControlPlaneServiceRole.READS,
            projection_schema="WorkspaceReadResponse",
            http_route_id="read.workspace",
            mcp_tool_name="get_workspace",
        )
        with self.assertRaises(InvalidAdapterParityContract):
            AdapterParityContract(
                http_api=http_api,
                mcp=McpStreamableHttpContract(),
                projections=(binding, binding),
            )

    def test_parity_does_not_smuggle_transport_private_projection_state(self) -> None:
        parity = operator_read_projection_parity(
            HttpApiContract(operator_read_http_routes()),
            McpStreamableHttpContract(),
        )
        rendered = repr(parity.descriptor()).lower()

        self.assertNotIn("fastapi", rendered)
        self.assertNotIn("uvicorn", rendered)
        self.assertNotIn("dockerfile", rendered)
        self.assertNotIn("private_projection", rendered)


if __name__ == "__main__":
    unittest.main()
