import unittest

from control_plane_kit_core.operations import (
    AdapterParityContract,
    ControlPlaneServiceRole,
    HttpApiContract,
    HttpAuthScope,
    HttpOperationSafety,
    InvalidReadProjectionContract,
    McpStreamableHttpContract,
    ReadProjectionContract,
    ReadProjectionKind,
    ReadProjectionPolicy,
    ReadProjectionSet,
    canonical_operator_read_projection_set,
    operator_read_http_routes,
    operator_read_projection_parity,
)


class ReadProjectionContractTests(unittest.TestCase):
    def test_canonical_projection_set_is_closed_bounded_and_read_only(self) -> None:
        projections = canonical_operator_read_projection_set()

        self.assertEqual(
            [
                (
                    projection.operation_id,
                    projection.kind,
                    projection.response_schema,
                    projection.policy,
                    projection.requires_workspace_scope,
                    projection.paged,
                )
                for projection in projections.projections
            ],
            [
                (
                    "read.activity-timeline",
                    ReadProjectionKind.ACTIVITY_TIMELINE,
                    "ActivityTimelineReadResponse",
                    ReadProjectionPolicy.REDACTED_PAGED_HISTORY,
                    True,
                    True,
                ),
                (
                    "read.approval-detail",
                    ReadProjectionKind.APPROVAL_DETAIL,
                    "ApprovalDetailReadResponse",
                    ReadProjectionPolicy.PINNED_PLAN_AND_RECOVERY,
                    True,
                    False,
                ),
                (
                    "read.control-surface",
                    ReadProjectionKind.CONTROL_SURFACE,
                    "ControlSurfaceReadResponse",
                    ReadProjectionPolicy.REDACTED_CONTROL_SURFACE,
                    True,
                    False,
                ),
                (
                    "read.current-graph",
                    ReadProjectionKind.CURRENT_GRAPH,
                    "GraphReadResponse",
                    ReadProjectionPolicy.REDACTED_GRAPH_DESCRIPTOR,
                    True,
                    False,
                ),
                (
                    "read.desired-graph",
                    ReadProjectionKind.DESIRED_GRAPH,
                    "GraphReadResponse",
                    ReadProjectionPolicy.REDACTED_GRAPH_DESCRIPTOR,
                    True,
                    False,
                ),
                (
                    "read.observed-state",
                    ReadProjectionKind.OBSERVED_STATE,
                    "ObservedStateReadResponse",
                    ReadProjectionPolicy.OBSERVED_STATE_EVIDENCE,
                    True,
                    False,
                ),
                (
                    "read.open-sessions",
                    ReadProjectionKind.OPEN_SESSIONS,
                    "OpenSessionsReadResponse",
                    ReadProjectionPolicy.REDACTED_PAGED_HISTORY,
                    True,
                    True,
                ),
                (
                    "read.operator-graph",
                    ReadProjectionKind.OPERATOR_GRAPH,
                    "OperatorGraphReadResponse",
                    ReadProjectionPolicy.REDACTED_GRAPH_DESCRIPTOR,
                    True,
                    False,
                ),
                (
                    "read.pending-approvals",
                    ReadProjectionKind.PENDING_APPROVALS,
                    "PendingApprovalsReadResponse",
                    ReadProjectionPolicy.REDACTED_PAGED_HISTORY,
                    True,
                    True,
                ),
                (
                    "read.plan-detail",
                    ReadProjectionKind.PLAN_DETAIL,
                    "PlanDetailReadResponse",
                    ReadProjectionPolicy.PINNED_PLAN_AND_RECOVERY,
                    True,
                    False,
                ),
                (
                    "read.session-detail",
                    ReadProjectionKind.SESSION_DETAIL,
                    "SessionDetailReadResponse",
                    ReadProjectionPolicy.REDACTED_PAGED_HISTORY,
                    True,
                    False,
                ),
                (
                    "read.workspace",
                    ReadProjectionKind.WORKSPACE,
                    "WorkspaceReadResponse",
                    ReadProjectionPolicy.REDACTED_WORKSPACE,
                    True,
                    False,
                ),
            ],
        )
        self.assertTrue(
            all(
                projection.service_role is ControlPlaneServiceRole.READS
                for projection in projections.projections
            )
        )
        self.assertTrue(
            all(
                projection.auth_scope is HttpAuthScope.READ
                for projection in projections.projections
            )
        )
        self.assertTrue(
            all(
                projection.safety is HttpOperationSafety.READ_ONLY
                for projection in projections.projections
            )
        )

    def test_descriptor_round_trips_without_server_or_store_terms(self) -> None:
        projections = canonical_operator_read_projection_set()
        descriptor = projections.descriptor()

        self.assertEqual(descriptor["kind"], "operator-read-projection-set")
        self.assertEqual(ReadProjectionSet.from_descriptor(descriptor), projections)
        self.assertNotIn("fastapi", repr(descriptor).lower())
        self.assertNotIn("mcp-server", repr(descriptor).lower())
        self.assertNotIn("postgres", repr(descriptor).lower())
        self.assertNotIn("store", repr(descriptor).lower())
        self.assertNotIn("token", repr(descriptor).lower())
        self.assertNotIn("secret", repr(descriptor).lower())

        with self.assertRaises(InvalidReadProjectionContract):
            ReadProjectionSet.from_descriptor({**descriptor, "extra": True})

    def test_projection_contract_rejects_mutation_and_unbounded_shapes(self) -> None:
        with self.assertRaises(InvalidReadProjectionContract):
            ReadProjectionContract(
                operation_id="read.workspace",
                kind=ReadProjectionKind.WORKSPACE,
                service_role=ControlPlaneServiceRole.PLANNING,
                response_schema="WorkspaceReadResponse",
                policy=ReadProjectionPolicy.REDACTED_WORKSPACE,
                auth_scope=HttpAuthScope.READ,
                safety=HttpOperationSafety.READ_ONLY,
                requires_workspace_scope=True,
                paged=False,
                max_page_size=None,
            )

        with self.assertRaises(InvalidReadProjectionContract):
            ReadProjectionContract(
                operation_id="read.activity-timeline",
                kind=ReadProjectionKind.ACTIVITY_TIMELINE,
                service_role=ControlPlaneServiceRole.READS,
                response_schema="ActivityTimelineReadResponse",
                policy=ReadProjectionPolicy.REDACTED_PAGED_HISTORY,
                auth_scope=HttpAuthScope.READ,
                safety=HttpOperationSafety.READ_ONLY,
                requires_workspace_scope=True,
                paged=True,
                max_page_size=None,
            )

    def test_http_and_mcp_projection_parity_uses_same_projection_identities(self) -> None:
        projections = canonical_operator_read_projection_set()
        parity = operator_read_projection_parity(
            HttpApiContract(operator_read_http_routes()),
            McpStreamableHttpContract(),
        )

        self.assertIsInstance(parity, AdapterParityContract)
        self.assertEqual(
            [projection.operation_id for projection in projections.projections],
            [binding.operation_id for binding in parity.projections],
        )
        self.assertEqual(
            [projection.response_schema for projection in projections.projections],
            [binding.projection_schema for binding in parity.projections],
        )


if __name__ == "__main__":
    unittest.main()
