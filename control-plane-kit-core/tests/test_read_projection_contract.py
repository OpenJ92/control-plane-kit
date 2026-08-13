import unittest

from contract_security_assertions import assert_descriptor_excludes_secret_material
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
    def test_gateway_probe_timeline_uses_the_common_page_bound(self) -> None:
        projection = canonical_operator_read_projection_set().projection(
            "read.gateway-probe-timeline"
        )

        self.assertTrue(projection.paged)
        self.assertEqual(projection.max_page_size, 100)

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
                    "read.delegation-keys",
                    ReadProjectionKind.DELEGATION_KEYS,
                    "DelegationSigningKeyCollectionReadResponse",
                    ReadProjectionPolicy.REDACTED_DELEGATION_KEY,
                    True,
                    True,
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
                    "read.gateway-probe-detail",
                    ReadProjectionKind.GATEWAY_PROBE_DETAIL,
                    "GatewayProbeDetailReadResponse",
                    ReadProjectionPolicy.DELEGATED_GATEWAY_PROBE_EVIDENCE,
                    True,
                    False,
                ),
                (
                    "read.gateway-probe-timeline",
                    ReadProjectionKind.GATEWAY_PROBE_TIMELINE,
                    "GatewayProbeTimelineReadResponse",
                    ReadProjectionPolicy.DELEGATED_GATEWAY_PROBE_EVIDENCE,
                    True,
                    True,
                ),
                (
                    "read.gateway-verifier-configuration",
                    ReadProjectionKind.GATEWAY_VERIFIER_CONFIGURATION,
                    "GatewayVerifierConfigurationReadResponse",
                    ReadProjectionPolicy.PUBLIC_GATEWAY_VERIFIER_CONFIGURATION,
                    True,
                    False,
                ),
                (
                    "read.ingress-authorities",
                    ReadProjectionKind.INGRESS_AUTHORITIES,
                    "IngressAuthorityCollectionReadResponse",
                    ReadProjectionPolicy.REDACTED_INGRESS_AUTHORITY,
                    True,
                    True,
                ),
                (
                    "read.ingress-authority-detail",
                    ReadProjectionKind.INGRESS_AUTHORITY_DETAIL,
                    "IngressAuthorityDetailReadResponse",
                    ReadProjectionPolicy.REDACTED_INGRESS_AUTHORITY,
                    True,
                    False,
                ),
                (
                    "read.observed-state",
                    ReadProjectionKind.OBSERVED_STATE,
                    "ObservedStateReadResponse",
                    ReadProjectionPolicy.OBSERVED_STATE_EVIDENCE,
                    True,
                    True,
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
                    "read.plan-runs",
                    ReadProjectionKind.PLAN_RUNS,
                    "PlanRunsReadResponse",
                    ReadProjectionPolicy.REDACTED_PAGED_HISTORY,
                    True,
                    True,
                ),
                (
                    "read.run-events",
                    ReadProjectionKind.RUN_EVENTS,
                    "RunEventsReadResponse",
                    ReadProjectionPolicy.REDACTED_PAGED_HISTORY,
                    True,
                    True,
                ),
                (
                    "read.runtime-authorities",
                    ReadProjectionKind.RUNTIME_AUTHORITIES,
                    "RuntimeAuthorityCollectionReadResponse",
                    ReadProjectionPolicy.REDACTED_RUNTIME_AUTHORITY,
                    True,
                    True,
                ),
                (
                    "read.runtime-authority-deliveries",
                    ReadProjectionKind.RUNTIME_AUTHORITY_DELIVERIES,
                    "RuntimeAuthorityDeliveryCollectionReadResponse",
                    ReadProjectionPolicy.REDACTED_RUNTIME_AUTHORITY_DELIVERY,
                    True,
                    True,
                ),
                (
                    "read.runtime-authority-delivery-detail",
                    ReadProjectionKind.RUNTIME_AUTHORITY_DELIVERY_DETAIL,
                    "RuntimeAuthorityDeliveryDetailReadResponse",
                    ReadProjectionPolicy.REDACTED_RUNTIME_AUTHORITY_DELIVERY,
                    True,
                    False,
                ),
                (
                    "read.runtime-authority-detail",
                    ReadProjectionKind.RUNTIME_AUTHORITY_DETAIL,
                    "RuntimeAuthorityDetailReadResponse",
                    ReadProjectionPolicy.REDACTED_RUNTIME_AUTHORITY,
                    True,
                    False,
                ),
                (
                    "read.secret-provider-detail",
                    ReadProjectionKind.SECRET_PROVIDER_DETAIL,
                    "SecretProviderDetailReadResponse",
                    ReadProjectionPolicy.REDACTED_SECRET_PROVIDER,
                    True,
                    False,
                ),
                (
                    "read.secret-providers",
                    ReadProjectionKind.SECRET_PROVIDERS,
                    "SecretProviderCollectionReadResponse",
                    ReadProjectionPolicy.REDACTED_SECRET_PROVIDER,
                    True,
                    True,
                ),
                (
                    "read.secret-reference-detail",
                    ReadProjectionKind.SECRET_REFERENCE_DETAIL,
                    "SecretReferenceDetailReadResponse",
                    ReadProjectionPolicy.REDACTED_SECRET_REFERENCE,
                    True,
                    False,
                ),
                (
                    "read.secret-references",
                    ReadProjectionKind.SECRET_REFERENCES,
                    "SecretReferenceCollectionReadResponse",
                    ReadProjectionPolicy.REDACTED_SECRET_REFERENCE,
                    True,
                    True,
                ),
                (
                    "read.session-actions",
                    ReadProjectionKind.SESSION_ACTIONS,
                    "SessionActionsReadResponse",
                    ReadProjectionPolicy.REDACTED_PAGED_HISTORY,
                    True,
                    True,
                ),
                (
                    "read.session-approvals",
                    ReadProjectionKind.SESSION_APPROVALS,
                    "SessionApprovalsReadResponse",
                    ReadProjectionPolicy.REDACTED_PAGED_HISTORY,
                    True,
                    True,
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
                    "read.session-plans",
                    ReadProjectionKind.SESSION_PLANS,
                    "SessionPlansReadResponse",
                    ReadProjectionPolicy.REDACTED_PAGED_HISTORY,
                    True,
                    True,
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
        assert_descriptor_excludes_secret_material(self, descriptor)

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
