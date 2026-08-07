import json
from pathlib import Path
import unittest

import control_plane_kit_core as core
import control_plane_kit_core.node_control as node_control
from control_plane_kit_core.node_control import (
    ControlPlaneCommandCodec,
    ControlPlaneResultCodec,
    ControlPlaneStateCodec,
    ControlPlaneTransitionPrecondition,
    ControlPlaneVariableDescriptor,
    ControlPlaneVariableDescriptorCodec,
    ControlPlaneVariableKind,
    ControlPlaneVariableOperationContract,
    DelegatedWorkloadNodeControlGrant,
    DelegatedWorkloadNodeControlGrantCodec,
    NodeControlCommandRequest,
    NodeControlCommandRequestCodec,
    NodeControlContractError,
    NodeControlOperation,
    NodeControlPayload,
    NodeControlTarget,
    WeightedRoutingControlState,
)
from control_plane_kit_core.probe_intents import LiteralEndpointMaterial
from control_plane_kit_core.secrets import SecretReference


FIXTURE = Path(__file__).parent / "fixtures" / "node_control_canonical_wire_v1.json"


class NodeControlGraphReferenceTests(unittest.TestCase):
    def reference_types(self):
        role_type = getattr(node_control, "NodeControlGraphReferenceRole", None)
        reference_type = getattr(node_control, "NodeControlGraphReference", None)
        self.assertNotIn(
            None,
            (role_type, reference_type),
            "nominal node-control graph reference types are missing",
        )
        return role_type, reference_type

    def reference(self, role_name: str, value: object):
        role_type, reference_type = self.reference_types()
        return reference_type(getattr(role_type, role_name), value)

    def target(self, **changes: object) -> NodeControlTarget:
        values = {
            "workspace_id": self.reference("WORKSPACE", "workspace-1"),
            "graph_revision": self.reference("GRAPH_REVISION", "revision-7"),
            "node_id": self.reference("NODE", "router"),
            "provider_socket_name": self.reference("PROVIDER_SOCKET", "control"),
        }
        values.update(changes)
        return NodeControlTarget(**values)

    def weighted_state(self) -> WeightedRoutingControlState:
        target_a = self.reference("TARGET", "target-a")
        target_b = self.reference("TARGET", "target-b")
        return WeightedRoutingControlState(
            targets=(target_a, target_b),
            weights=((target_a, 2.0), (target_b, 1.0)),
        )

    def contracts(self):
        return (
            ControlPlaneVariableOperationContract(
                operation=NodeControlOperation.READ_STATE,
                command_codec=None,
                result_codec=ControlPlaneResultCodec.STATE_V1,
            ),
            ControlPlaneVariableOperationContract(
                operation=NodeControlOperation.APPLY_COMMAND,
                command_codec=ControlPlaneCommandCodec.REPLACE_WEIGHTED_ROUTING_V1,
                result_codec=ControlPlaneResultCodec.TRANSITION_V1,
            ),
        )

    def variable(self, **changes: object) -> ControlPlaneVariableDescriptor:
        values = {
            "variable_name": self.reference("VARIABLE", "routing"),
            "kind": ControlPlaneVariableKind.WEIGHTED_ROUTING,
            "state_codec": ControlPlaneStateCodec.WEIGHTED_ROUTING_V1,
            "operation_contracts": self.contracts(),
            "description": "Atomic graph target weights.",
        }
        values.update(changes)
        return ControlPlaneVariableDescriptor(**values)

    def request(self, **changes: object) -> NodeControlCommandRequest:
        values = {
            "target": self.target(),
            "variable_name": self.reference("VARIABLE", "routing"),
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

    def test_reference_roles_are_closed_bounded_and_root_exported(self) -> None:
        role_type, reference_type = self.reference_types()
        expected = {
            "WORKSPACE": "workspace",
            "GRAPH_REVISION": "graph-revision",
            "NODE": "node",
            "PROVIDER_SOCKET": "provider-socket",
            "VARIABLE": "variable",
            "TARGET": "target",
        }
        self.assertEqual(
            {name: getattr(role_type, name).value for name in expected},
            expected,
        )
        for name in expected:
            with self.subTest(role=name):
                reference = reference_type(getattr(role_type, name), "opaque-id")
                self.assertEqual(reference.value, "opaque-id")

        for role, value in (
            ("node", "router"),
            (role_type.NODE, ""),
            (role_type.NODE, "x" * 129),
            (role_type.NODE, 7),
        ):
            with self.subTest(role=role, value=value):
                with self.assertRaises(NodeControlContractError):
                    reference_type(role, value)

        self.assertIs(getattr(core, "NodeControlGraphReferenceRole", None), role_type)
        self.assertIs(getattr(core, "NodeControlGraphReference", None), reference_type)

    def test_authority_positions_require_the_exact_reference_role(self) -> None:
        valid_target = self.target()
        self.assertEqual(valid_target.node_id.role.value, "node")

        target_cases = (
            {"workspace_id": "workspace-1"},
            {"workspace_id": self.reference("NODE", "workspace-1")},
            {"graph_revision": self.reference("WORKSPACE", "revision-7")},
            {"node_id": self.reference("PROVIDER_SOCKET", "router")},
            {"provider_socket_name": self.reference("NODE", "control")},
        )
        for changes in target_cases:
            with self.subTest(changes=changes):
                with self.assertRaises(NodeControlContractError):
                    self.target(**changes)

        with self.assertRaises(NodeControlContractError):
            self.request(variable_name="routing")
        with self.assertRaises(NodeControlContractError):
            self.request(variable_name=self.reference("NODE", "routing"))
        with self.assertRaises(NodeControlContractError):
            self.variable(variable_name="routing")
        with self.assertRaises(NodeControlContractError):
            self.grant(variable_name=self.reference("TARGET", "routing"))

        target = self.reference("TARGET", "target-a")
        wrong = self.reference("NODE", "target-a")
        for targets, weights in (
            (("target-a",), (("target-a", 1.0),)),
            ((wrong,), ((wrong, 1.0),)),
            ((target,), ((wrong, 1.0),)),
        ):
            with self.subTest(targets=targets, weights=weights):
                with self.assertRaises(NodeControlContractError):
                    WeightedRoutingControlState(targets=targets, weights=weights)

    def test_strict_codecs_preserve_string_wire_and_recover_roles(self) -> None:
        request = self.request()
        grant = self.grant(request)
        variable = self.variable()

        cases = (
            (NodeControlCommandRequestCodec(), request),
            (DelegatedWorkloadNodeControlGrantCodec(), grant),
            (ControlPlaneVariableDescriptorCodec(), variable),
        )
        for codec, value in cases:
            with self.subTest(codec=type(codec).__name__):
                encoded = codec.encode(value)
                decoded = codec.decode(encoded)
                self.assertEqual(decoded, value)
                self.assertEqual(encoded["variable_name"], "routing")

        encoded_request = NodeControlCommandRequestCodec().encode(request)
        self.assertEqual(
            encoded_request["target"],
            {
                "workspace_id": "workspace-1",
                "graph_revision": "revision-7",
                "node_id": "router",
                "provider_socket_name": "control",
            },
        )
        self.assertEqual(
            encoded_request["payload"]["state"]["targets"],
            ["target-a", "target-b"],
        )
        self.assertEqual(
            encoded_request["payload"]["state"]["weights"],
            {"target-a": 2.0, "target-b": 1.0},
        )

    def test_endpoint_and_secret_material_objects_cannot_substitute(self) -> None:
        _, reference_type = self.reference_types()
        role_type, _ = self.reference_types()
        endpoint = LiteralEndpointMaterial("router.internal")
        secret = SecretReference("secret://provider-a/node-control/target")

        for value in (endpoint, secret):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(NodeControlContractError):
                    reference_type(role_type.NODE, value)
                with self.assertRaises(NodeControlContractError):
                    self.target(node_id=value)
                with self.assertRaises(NodeControlContractError):
                    self.request(variable_name=value)
                with self.assertRaises(NodeControlContractError):
                    WeightedRoutingControlState(
                        targets=(value,),
                        weights=((value, 1.0),),
                    )

    def test_strict_codecs_reject_endpoint_secret_and_provenance_fields(self) -> None:
        request_codec = NodeControlCommandRequestCodec()
        request = request_codec.encode(self.request())
        target = request["target"]
        state = request["payload"]["state"]
        request_cases = (
            {**request, "endpoint": "router.internal"},
            {**request, "provenance": "graph-declared"},
            {**request, "target": {**target, "url": "https://router.internal"}},
            {**request, "target": {**target, "host": "router.internal"}},
            {**request, "target": {**target, "port": 8080}},
            {
                **request,
                "payload": {
                    **request["payload"],
                    "state": {**state, "address": "router.internal"},
                },
            },
        )
        for value in request_cases:
            with self.subTest(value=value):
                with self.assertRaises(NodeControlContractError):
                    request_codec.decode(value)

        grant_codec = DelegatedWorkloadNodeControlGrantCodec()
        grant = grant_codec.encode(self.grant())
        for key in ("endpoint", "credential", "secret_reference", "provenance"):
            with self.subTest(grant_key=key):
                with self.assertRaises(NodeControlContractError):
                    grant_codec.decode({**grant, key: "rejected"})

        variable_codec = ControlPlaneVariableDescriptorCodec()
        variable = variable_codec.encode(self.variable())
        with self.assertRaises(NodeControlContractError):
            variable_codec.decode({**variable, "endpoint": "router.internal"})

    def test_dns_looking_bytes_remain_a_producer_provenance_boundary(self) -> None:
        _, reference_type = self.reference_types()
        reference = self.reference("NODE", "router.internal")
        target = self.target(node_id=reference)

        self.assertEqual(target.descriptor()["node_id"], "router.internal")
        contract = (reference_type.__doc__ or "").lower()
        self.assertIn("producer-attested", contract)
        self.assertIn("does not prove graph membership", contract)
        with self.assertRaises(NodeControlContractError):
            self.target(node_id=LiteralEndpointMaterial("router.internal"))

    def test_nominal_references_preserve_canonical_golden_vectors(self) -> None:
        self.reference_types()
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        codec = NodeControlCommandRequestCodec()

        for vector in fixture["requests"]:
            with self.subTest(vector=vector["name"]):
                request = codec.decode(vector["descriptor"])
                self.assertEqual(codec.encode(request), vector["descriptor"])
                self.assertEqual(
                    request.canonical_bytes().decode("utf-8"),
                    vector["canonical_utf8"],
                )
                self.assertEqual(request.canonical_digest().value, vector["sha256"])

        source = (
            Path(node_control.__file__).read_text(encoding="utf-8")
        )
        forbidden_imports = (
            "control_plane_kit_core.topology",
            "control_plane_kit_core.secrets",
            "control_plane_kit_core.probe_intents",
            "control_plane_kit_operations",
            "fastapi",
        )
        for forbidden in forbidden_imports:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
