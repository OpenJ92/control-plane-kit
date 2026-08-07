import json
from pathlib import Path
import unittest

import control_plane_kit_core.node_control as node_control
from control_plane_kit_core.node_control import (
    ControlPlaneCommandCodec,
    ControlPlaneResultCodec,
    ControlPlaneStateCodec,
    ControlPlaneTransitionPrecondition,
    ControlPlaneVariableDescriptor,
    ControlPlaneVariableKind,
    ControlPlaneVariableOperationContract,
    DelegatedWorkloadNodeControlGrant,
    MapControlState,
    NodeControlCommandRequest,
    NodeControlCommandRequestCodec,
    NodeControlContractError,
    NodeControlEvidence,
    NodeControlEvidenceCode,
    NodeControlFailed,
    NodeControlGraphReference,
    NodeControlGraphReferenceRole,
    NodeControlOperation,
    NodeControlPayload,
    NodeControlReadStateSucceeded,
    NodeControlRejected,
    NodeControlRequestDigest,
    NodeControlTransitionSucceeded,
    ScalarControlState,
)


FIXTURE = (
    Path(__file__).parent / "fixtures" / "node_control_public_material_v1.json"
)


class NodeControlPublicMaterialTests(unittest.TestCase):
    def reference(
        self,
        role: NodeControlGraphReferenceRole,
        value: str,
    ) -> NodeControlGraphReference:
        return NodeControlGraphReference(role, value)

    def target(self, value: str = "router") -> node_control.NodeControlTarget:
        return node_control.NodeControlTarget(
            workspace_id=self.reference(
                NodeControlGraphReferenceRole.WORKSPACE,
                "workspace-1",
            ),
            graph_revision=self.reference(
                NodeControlGraphReferenceRole.GRAPH_REVISION,
                "revision-7",
            ),
            node_id=self.reference(NodeControlGraphReferenceRole.NODE, value),
            provider_socket_name=self.reference(
                NodeControlGraphReferenceRole.PROVIDER_SOCKET,
                "control",
            ),
        )

    def contracts(self) -> tuple[ControlPlaneVariableOperationContract, ...]:
        return (
            ControlPlaneVariableOperationContract(
                NodeControlOperation.READ_STATE,
                None,
                ControlPlaneResultCodec.STATE_V1,
            ),
            ControlPlaneVariableOperationContract(
                NodeControlOperation.APPLY_COMMAND,
                ControlPlaneCommandCodec.REPLACE_SCALAR_V1,
                ControlPlaneResultCodec.TRANSITION_V1,
            ),
        )

    def variable(self, description: str | None = None) -> ControlPlaneVariableDescriptor:
        return ControlPlaneVariableDescriptor(
            variable_name=self.reference(
                NodeControlGraphReferenceRole.VARIABLE,
                "limit",
            ),
            kind=ControlPlaneVariableKind.SCALAR,
            state_codec=ControlPlaneStateCodec.SCALAR_V1,
            operation_contracts=self.contracts(),
            description=description,
        )

    def request(self, canary: str = "request-1") -> NodeControlCommandRequest:
        return NodeControlCommandRequest(
            target=self.target(),
            variable_name=self.reference(
                NodeControlGraphReferenceRole.VARIABLE,
                "limit",
            ),
            operation=NodeControlOperation.APPLY_COMMAND,
            request_id=canary,
            idempotency_key=canary,
            command_codec=ControlPlaneCommandCodec.REPLACE_SCALAR_V1,
            precondition=ControlPlaneTransitionPrecondition(4),
            payload=NodeControlPayload(
                ControlPlaneCommandCodec.REPLACE_SCALAR_V1,
                ScalarControlState(canary),
            ),
        )

    def grant(
        self,
        request: NodeControlCommandRequest,
        canary: str = "grant-1",
    ) -> DelegatedWorkloadNodeControlGrant:
        return DelegatedWorkloadNodeControlGrant(
            issuer=canary,
            key_id=canary,
            audience=canary,
            target=request.target,
            variable_name=request.variable_name,
            operation=request.operation,
            command_codec=request.command_codec,
            request_id=request.request_id,
            idempotency_key=request.idempotency_key,
            request_digest=request.canonical_digest(),
            issued_at=100,
            not_before=100,
            expires_at=200,
            jti=canary,
        )

    def fixture(self) -> dict[str, object]:
        return json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_language_neutral_public_material_vectors(self) -> None:
        fixture = self.fixture()
        self.assertEqual(fixture["schema"], "cpk.node-control.public-material.v1")
        self.assertEqual(
            fixture["projection"],
            "literal-and-one-pass-ascii-percent",
        )

        for value in fixture["accepted"]:
            with self.subTest(admitted=value):
                self.assertEqual(self.variable(value).description, value)

        for vector in fixture["rejected"]:
            with self.subTest(rejected=vector):
                with self.assertRaisesRegex(
                    NodeControlContractError,
                    vector["law"],
                ):
                    self.variable(vector["value"])

    def test_identifier_positions_apply_exact_envelopes_without_dns_guessing(self) -> None:
        for value in (
            "router.internal",
            "secret-agent",
            "bearer-capacity",
            "token-count",
        ):
            with self.subTest(admitted=value):
                self.assertEqual(ScalarControlState(value).value, value)
                self.assertEqual(
                    self.reference(NodeControlGraphReferenceRole.NODE, value).value,
                    value,
                )

        for value, law in (
            ("sk-abc12345", "credential-envelope"),
            ("SG.abc12345", "credential-envelope"),
            ("localhost", "endpoint-envelope"),
            ("192.0.2.10", "endpoint-envelope"),
        ):
            with self.subTest(rejected=value):
                with self.assertRaisesRegex(NodeControlContractError, law):
                    ScalarControlState(value)

    def test_authority_references_reject_endpoints_but_accept_bare_dns(self) -> None:
        request = self.request()
        self.assertEqual(self.grant(request, "router.internal").issuer, "router.internal")

        for value in (
            "https://router.internal/control",
            "router.internal:443",
        ):
            with self.subTest(rejected=value):
                with self.assertRaisesRegex(
                    NodeControlContractError,
                    "endpoint-envelope",
                ):
                    self.grant(request, value)

    def test_failures_never_echo_attacker_material_or_retain_context(self) -> None:
        request = self.request()
        descriptor = request.descriptor()
        attacker_key = "token=unknown-field-canary"
        with self.assertRaises(NodeControlContractError) as caught:
            NodeControlCommandRequestCodec().decode(
                {**descriptor, attacker_key: "unknown-value-canary"}
            )
        self.assertNotIn(attacker_key, str(caught.exception))
        self.assertNotIn("unknown-value-canary", repr(caught.exception))

        with self.assertRaises(NodeControlContractError) as caught:
            MapControlState((("attacker-map-key-canary", "sk-attacker-value"),))
        rendered = f"{caught.exception!s} {caught.exception!r}"
        self.assertNotIn("attacker-map-key-canary", rendered)
        self.assertNotIn("sk-attacker-value", rendered)

        invalid_enum = {**descriptor, "operation": "attacker-enum-canary"}
        with self.assertRaises(NodeControlContractError) as caught:
            NodeControlCommandRequestCodec().decode(invalid_enum)
        self.assertNotIn("attacker-enum-canary", repr(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def test_open_material_is_omitted_from_object_representations(self) -> None:
        canary = "opaqueMaterialCanary"
        request = self.request(canary)
        grant = self.grant(request, canary)
        variable = self.variable(canary)
        state = ScalarControlState(canary)
        map_state = MapControlState(((canary, canary),))
        results = (
            NodeControlReadStateSucceeded(
                canary,
                ControlPlaneStateCodec.SCALAR_V1,
                4,
                state,
            ),
            NodeControlTransitionSucceeded(
                canary,
                5,
                NodeControlEvidence(NodeControlEvidenceCode.APPLIED),
            ),
            NodeControlRejected(
                canary,
                NodeControlOperation.APPLY_COMMAND,
                NodeControlEvidence(NodeControlEvidenceCode.INVALID_COMMAND),
            ),
            NodeControlFailed(canary, NodeControlOperation.READ_STATE),
        )
        values = (
            self.reference(NodeControlGraphReferenceRole.NODE, canary),
            state,
            map_state,
            request,
            grant,
            variable,
            *results,
        )
        for value in values:
            with self.subTest(value_type=type(value).__name__):
                self.assertNotIn(canary, repr(value))

        self.assertIn(canary, repr(request.descriptor()))
        self.assertIn(canary, repr(variable.descriptor()))

    def test_public_material_policy_preserves_wire_and_closed_evidence(self) -> None:
        request = self.request("public-value")
        encoded = NodeControlCommandRequestCodec().encode(request)
        self.assertEqual(NodeControlCommandRequestCodec().decode(encoded), request)
        self.assertEqual(request.canonical_digest(), NodeControlRequestDigest(
            request.canonical_digest().value
        ))
        self.assertEqual(
            NodeControlEvidence(NodeControlEvidenceCode.INTERNAL_FAILURE).descriptor(),
            {"code": "internal-failure"},
        )

        source = Path(node_control.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "urllib.parse",
            "control_plane_kit_operations",
            "control_plane_kit_core.secrets",
            "fastapi",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
