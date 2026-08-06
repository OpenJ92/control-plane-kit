from dataclasses import replace
import math
import unittest

import control_plane_kit_core as core
from control_plane_kit_core.capabilities import CapabilityName, capability_named
from control_plane_kit_core.control_routes import (
    ControlRouteScope,
    ControlRouteSetName,
    route_set_named,
)
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.gateway_delegation import (
    DelegatedGatewayProbeGrant,
    GatewayProbeCommandKind,
    GatewayProbeRequest,
)
from control_plane_kit_core.node_control import (
    ControlPlaneCommandCodec,
    ControlPlaneResultCodec,
    ControlPlaneStateCodec,
    ControlPlaneTransitionPrecondition,
    ControlPlaneVariableDescriptor,
    ControlPlaneVariableDescriptorCodec,
    ControlPlaneVariableKind,
    DelegatedWorkloadNodeControlGrant,
    DelegatedWorkloadNodeControlGrantCodec,
    MapControlState,
    NodeControlCommandRequest,
    NodeControlCommandRequestCodec,
    NodeControlContractError,
    NodeControlEvidence,
    NodeControlEvidenceCode,
    NodeControlOperation,
    NodeControlPayload,
    NodeControlRequestDigest,
    NodeControlResult,
    NodeControlResultCodec,
    NodeControlResultStatus,
    NodeControlTarget,
    ScalarControlState,
    WeightedRoutingControlState,
    WorkloadNodeControlGrantVerificationCode,
    verify_workload_node_control_grant,
)
from control_plane_kit_core.runtime_effects import GatewayTargetId


class NodeControlContractTests(unittest.TestCase):
    def target(self, **changes: object) -> NodeControlTarget:
        values = {
            "workspace_id": "workspace-1",
            "graph_revision": "revision-7",
            "node_id": "router",
            "provider_socket_name": "control",
        }
        values.update(changes)
        return NodeControlTarget(**values)

    def weighted_state(self) -> WeightedRoutingControlState:
        return WeightedRoutingControlState(
            targets=("target-a", "target-b"),
            weights=(("target-a", 2.0), ("target-b", 1.0)),
        )

    def request(self, **changes: object) -> NodeControlCommandRequest:
        values = {
            "target": self.target(),
            "variable_name": "routing",
            "operation": NodeControlOperation.APPLY_COMMAND,
            "request_id": "request-1",
            "idempotency_key": "routing-change-1",
            "command_codec": ControlPlaneCommandCodec.REPLACE_WEIGHTED_ROUTING_V1,
            "precondition": ControlPlaneTransitionPrecondition(expected_version=4),
            "payload": NodeControlPayload(
                codec=ControlPlaneCommandCodec.REPLACE_WEIGHTED_ROUTING_V1,
                state=self.weighted_state(),
            ),
        }
        values.update(changes)
        return NodeControlCommandRequest(**values)

    def grant(
        self,
        request: NodeControlCommandRequest | None = None,
        **changes: object,
    ) -> DelegatedWorkloadNodeControlGrant:
        request = request or self.request()
        values = {
            "issuer": "cpk-server",
            "key_id": "workload-key-1",
            "audience": "workload:router:control",
            "target": request.target,
            "variable_name": request.variable_name,
            "operation": request.operation,
            "command_codec": request.command_codec,
            "request_id": request.request_id,
            "idempotency_key": request.idempotency_key,
            "request_digest": request.canonical_digest(),
            "issued_at": 100,
            "not_before": 100,
            "expires_at": 200,
            "jti": "grant-1",
        }
        values.update(changes)
        return DelegatedWorkloadNodeControlGrant(**values)

    def test_exact_contract_codecs_round_trip(self) -> None:
        variable = ControlPlaneVariableDescriptor(
            variable_name="routing",
            kind=ControlPlaneVariableKind.WEIGHTED_ROUTING,
            state_codec=ControlPlaneStateCodec.WEIGHTED_ROUTING_V1,
            command_codec=ControlPlaneCommandCodec.REPLACE_WEIGHTED_ROUTING_V1,
            result_codec=ControlPlaneResultCodec.TRANSITION_V1,
            description="Atomic graph target weights.",
        )
        request = self.request()
        grant = self.grant(request)
        result = NodeControlResult(
            request_id=request.request_id,
            status=NodeControlResultStatus.SUCCEEDED,
            codec=ControlPlaneResultCodec.TRANSITION_V1,
            version=5,
            payload=None,
            evidence=(NodeControlEvidence(NodeControlEvidenceCode.APPLIED),),
        )

        for codec, value in (
            (ControlPlaneVariableDescriptorCodec(), variable),
            (NodeControlCommandRequestCodec(), request),
            (DelegatedWorkloadNodeControlGrantCodec(), grant),
            (NodeControlResultCodec(), result),
        ):
            with self.subTest(codec=type(codec).__name__):
                self.assertEqual(codec.decode(codec.encode(value)), value)

        self.assertEqual(variable.descriptor()["route_set"], "node-control")
        self.assertEqual(variable.descriptor()["capability"], "node-controllable")
        self.assertEqual(request.canonical_digest().value.__len__(), 64)

        read = NodeControlCommandRequest(
            target=self.target(),
            variable_name="routing",
            operation=NodeControlOperation.READ_STATE,
            request_id="request-read-1",
            idempotency_key="routing-read-1",
        )
        self.assertEqual(
            NodeControlCommandRequestCodec().decode(read.descriptor()),
            read,
        )

    def test_strict_codecs_reject_unknown_keys_kinds_and_codecs(self) -> None:
        descriptor = ControlPlaneVariableDescriptorCodec().encode(
            ControlPlaneVariableDescriptor(
                variable_name="routing",
                kind=ControlPlaneVariableKind.WEIGHTED_ROUTING,
                state_codec=ControlPlaneStateCodec.WEIGHTED_ROUTING_V1,
                command_codec=ControlPlaneCommandCodec.REPLACE_WEIGHTED_ROUTING_V1,
                result_codec=ControlPlaneResultCodec.TRANSITION_V1,
            )
        )
        cases = (
            {**descriptor, "kind": "unknown"},
            {**descriptor, "command_codec": "unknown"},
            {**descriptor, "credential": "secret"},
        )
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(NodeControlContractError):
                    ControlPlaneVariableDescriptorCodec().decode(value)

        request = NodeControlCommandRequestCodec().encode(self.request())
        with self.assertRaises(NodeControlContractError):
            NodeControlCommandRequestCodec().decode(
                {**request, "command_codec": "arbitrary-http.v1"}
            )

        grant = DelegatedWorkloadNodeControlGrantCodec().encode(self.grant())
        with self.assertRaises(NodeControlContractError):
            DelegatedWorkloadNodeControlGrantCodec().decode(
                {**grant, "signature": "compact-secret-material"}
            )

    def test_weighted_state_is_one_structurally_valid_snapshot(self) -> None:
        value = self.weighted_state()
        self.assertEqual(
            value.descriptor(),
            {
                "kind": "weighted-routing",
                "targets": ["target-a", "target-b"],
                "weights": {"target-a": 2.0, "target-b": 1.0},
            },
        )
        invalid = (
            (("target-a", "target-b"), (("target-a", 1.0),)),
            (("target-a", "target-a"), (("target-a", 1.0),)),
            (("target-a",), (("target-a", -1.0),)),
            (("target-a",), (("target-a", 0.0),)),
            (("target-a",), (("target-a", math.inf),)),
            (("target-a",), (("target-a", math.nan),)),
        )
        for targets, weights in invalid:
            with self.subTest(targets=targets, weights=weights):
                with self.assertRaises(NodeControlContractError):
                    WeightedRoutingControlState(targets=targets, weights=weights)

    def test_state_values_are_immutable_bounded_and_secret_free(self) -> None:
        self.assertEqual(ScalarControlState("target-a").descriptor(), {
            "kind": "scalar",
            "value": "target-a",
        })
        self.assertEqual(
            MapControlState((("target-a", True), ("target-b", 2))).descriptor(),
            {
                "kind": "map",
                "entries": {"target-a": True, "target-b": 2},
            },
        )
        unsafe = (
            lambda: ScalarControlState("https://private.internal"),
            lambda: ScalarControlState("token=secret"),
            lambda: ScalarControlState("SG.secret-value"),
            lambda: MapControlState((("target", "127.0.0.1:8080"),)),
            lambda: MapControlState(tuple((f"target-{index}", index) for index in range(129))),
        )
        for factory in unsafe:
            with self.subTest(factory=factory):
                with self.assertRaises(NodeControlContractError):
                    factory()

    def test_graph_and_replay_identities_are_bounded_references(self) -> None:
        invalid_targets = (
            {"node_id": "https://private.internal"},
            {"provider_socket_name": "127.0.0.1:8080"},
            {"workspace_id": "token=secret"},
            {"graph_revision": ""},
        )
        for changes in invalid_targets:
            with self.subTest(changes=changes):
                with self.assertRaises(NodeControlContractError):
                    self.target(**changes)

        for changes in (
            {"request_id": "contains spaces"},
            {"idempotency_key": "authorization: bearer secret"},
            {"variable_name": "https://variable"},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(NodeControlContractError):
                    self.request(**changes)

    def test_grant_binding_is_exact_and_secret_free(self) -> None:
        request = self.request()
        grant = self.grant(request)
        accepted = verify_workload_node_control_grant(
            grant,
            request,
            expected_issuer="cpk-server",
            expected_audience="workload:router:control",
            now=150,
        )
        self.assertTrue(accepted.is_accepted)

        changed_request = self.request(
            payload=NodeControlPayload(
                codec=ControlPlaneCommandCodec.REPLACE_WEIGHTED_ROUTING_V1,
                state=WeightedRoutingControlState(
                    targets=("target-a", "target-b"),
                    weights=(("target-a", 1.0), ("target-b", 2.0)),
                ),
            )
        )
        self.assertNotEqual(
            changed_request.canonical_digest(),
            request.canonical_digest(),
        )
        changed_result = verify_workload_node_control_grant(
            grant,
            changed_request,
            expected_issuer="cpk-server",
            expected_audience="workload:router:control",
            now=150,
        )
        self.assertIs(
            changed_result.code,
            WorkloadNodeControlGrantVerificationCode.REQUEST_MISMATCH,
        )

        cases = (
            (replace(grant, issuer="other-issuer"), WorkloadNodeControlGrantVerificationCode.ISSUER_MISMATCH),
            (replace(grant, audience="other-audience"), WorkloadNodeControlGrantVerificationCode.AUDIENCE_MISMATCH),
            (replace(grant, target=self.target(workspace_id="workspace-2")), WorkloadNodeControlGrantVerificationCode.WORKSPACE_MISMATCH),
            (replace(grant, target=self.target(graph_revision="revision-8")), WorkloadNodeControlGrantVerificationCode.REVISION_MISMATCH),
            (replace(grant, target=self.target(node_id="other-node")), WorkloadNodeControlGrantVerificationCode.NODE_MISMATCH),
            (replace(grant, target=self.target(provider_socket_name="other-socket")), WorkloadNodeControlGrantVerificationCode.SOCKET_MISMATCH),
            (replace(grant, variable_name="other-variable"), WorkloadNodeControlGrantVerificationCode.VARIABLE_MISMATCH),
            (replace(grant, operation=NodeControlOperation.READ_STATE, command_codec=None), WorkloadNodeControlGrantVerificationCode.COMMAND_MISMATCH),
            (replace(grant, request_id="request-2"), WorkloadNodeControlGrantVerificationCode.REQUEST_MISMATCH),
        )
        for candidate, expected in cases:
            with self.subTest(expected=expected):
                result = verify_workload_node_control_grant(
                    candidate,
                    request,
                    expected_issuer="cpk-server",
                    expected_audience="workload:router:control",
                    now=150,
                )
                self.assertFalse(result.is_accepted)
                self.assertIs(result.code, expected)

        self.assertNotIn("secret", str(grant.descriptor()).lower())

    def test_grant_rejects_temporal_invalidity_and_transit_substitution(self) -> None:
        request = self.request()
        grant = self.grant(request)
        for now in (99, 201):
            with self.subTest(now=now):
                result = verify_workload_node_control_grant(
                    grant,
                    request,
                    expected_issuer="cpk-server",
                    expected_audience="workload:router:control",
                    now=now,
                )
                self.assertIs(
                    result.code,
                    WorkloadNodeControlGrantVerificationCode.TEMPORALLY_INVALID,
                )

        probe_request = GatewayProbeRequest(
            GatewayProbeCommandKind.HTTP_STATUS,
            GatewayTargetId("router.internal"),
            "/health",
        )
        transit_grant = DelegatedGatewayProbeGrant(
            issuer="cpk-server",
            key_id="gateway-key-1",
            audience="gateway",
            workspace_id="workspace-1",
            operation_id="operation-1",
            request_id="request-1",
            gateway_node_id="gateway-1",
            probe_kind=probe_request.kind,
            target_id=probe_request.target_id,
            request_digest=probe_request.canonical_digest(),
            issued_at=100,
            expires_at=200,
            jti="probe-grant-1",
        )
        result = verify_workload_node_control_grant(
            transit_grant,
            request,
            expected_issuer="cpk-server",
            expected_audience="workload:router:control",
            now=150,
        )
        self.assertIs(
            result.code,
            WorkloadNodeControlGrantVerificationCode.GRANT_TYPE_MISMATCH,
        )

    def test_result_evidence_is_closed_bounded_and_redacted(self) -> None:
        failure = NodeControlResult(
            request_id="request-1",
            status=NodeControlResultStatus.FAILED,
            codec=ControlPlaneResultCodec.TRANSITION_V1,
            version=4,
            payload=None,
            evidence=(NodeControlEvidence(NodeControlEvidenceCode.INTERNAL_FAILURE),),
        )
        encoded = NodeControlResultCodec().encode(failure)
        self.assertEqual(encoded["evidence"], [{"code": "internal-failure"}])
        self.assertNotIn("message", str(encoded))

        with self.assertRaises(NodeControlContractError):
            NodeControlResult(
                request_id="request-1",
                status=NodeControlResultStatus.FAILED,
                codec=ControlPlaneResultCodec.TRANSITION_V1,
                version=4,
                payload=None,
                evidence=(),
            )
        with self.assertRaises(NodeControlContractError):
            NodeControlResult(
                request_id="request-1",
                status=NodeControlResultStatus.REJECTED,
                codec=ControlPlaneResultCodec.TRANSITION_V1,
                version=4,
                payload=None,
                evidence=tuple(
                    NodeControlEvidence(NodeControlEvidenceCode.INVALID_COMMAND)
                    for _ in range(33)
                ),
            )

    def test_route_capability_key_purpose_and_root_exports_are_linked(self) -> None:
        route_set = route_set_named(ControlRouteSetName.NODE_CONTROL)
        self.assertEqual(
            [(route.method.value, route.path, route.scope) for route in route_set.routes],
            [
                ("GET", "/__control/variables/{variable_name}", ControlRouteScope.READ_NODE_CONTROL),
                ("POST", "/__control/variables/{variable_name}/commands", ControlRouteScope.APPLY_NODE_CONTROL),
            ],
        )
        capability = capability_named(CapabilityName.NODE_CONTROLLABLE)
        self.assertIs(capability.route_set, ControlRouteSetName.NODE_CONTROL)
        self.assertEqual(
            DelegationKeyPurpose.WORKLOAD_NODE_CONTROL.value,
            "workload-node-control",
        )

        exports = (
            "ControlPlaneVariableDescriptor",
            "DelegatedWorkloadNodeControlGrant",
            "NodeControlCommandRequest",
            "WeightedRoutingControlState",
            "verify_workload_node_control_grant",
        )
        for name in exports:
            with self.subTest(name=name):
                self.assertTrue(hasattr(core, name), name)


if __name__ == "__main__":
    unittest.main()
