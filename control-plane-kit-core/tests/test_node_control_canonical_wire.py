import json
from pathlib import Path
import struct
import tomllib
import unittest

import control_plane_kit_core as core
from control_plane_kit_core.node_control import (
    ControlPlaneCommandCodec,
    ControlPlaneTransitionPrecondition,
    DelegatedWorkloadNodeControlGrant,
    MapControlState,
    NodeControlCommandRequest,
    NodeControlCommandRequestCodec,
    NodeControlContractError,
    NodeControlOperation,
    NodeControlPayload,
    NodeControlTarget,
    ScalarControlState,
    WeightedRoutingControlState,
)


FIXTURE = Path(__file__).parent / "fixtures" / "node_control_canonical_wire_v1.json"
MAX_SAFE_INTEGER = 2**53 - 1


class NodeControlCanonicalWireTests(unittest.TestCase):
    def target(self) -> NodeControlTarget:
        return NodeControlTarget(
            workspace_id="workspace-1",
            graph_revision="revision-7",
            node_id="router",
            provider_socket_name="control",
        )

    def scalar_request(self, value: int | float) -> NodeControlCommandRequest:
        return NodeControlCommandRequest(
            target=self.target(),
            variable_name="limit",
            operation=NodeControlOperation.APPLY_COMMAND,
            request_id="request-scalar-1",
            idempotency_key="limit-change-1",
            command_codec=ControlPlaneCommandCodec.REPLACE_SCALAR_V1,
            precondition=ControlPlaneTransitionPrecondition(expected_version=4),
            payload=NodeControlPayload(
                codec=ControlPlaneCommandCodec.REPLACE_SCALAR_V1,
                state=ScalarControlState(value),
            ),
        )

    def weighted_state(self, weight: int | float) -> WeightedRoutingControlState:
        return WeightedRoutingControlState(
            targets=("target-a", "target-b"),
            weights=(("target-a", weight), ("target-b", 1.0)),
        )

    def fixture(self) -> dict[str, object]:
        return json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_request_declares_strict_canonicalization_identity(self) -> None:
        descriptor = self.scalar_request(1).descriptor()
        self.assertEqual(descriptor["canonicalization"], "jcs-rfc8785.v1")
        self.assertTrue(hasattr(core, "NodeControlCanonicalization"))

        missing = dict(descriptor)
        missing.pop("canonicalization")
        with self.assertRaises(NodeControlContractError):
            NodeControlCommandRequestCodec().decode(missing)
        with self.assertRaises(NodeControlContractError):
            NodeControlCommandRequestCodec().decode(
                {**descriptor, "canonicalization": "python-json.v1"}
            )

    def test_language_neutral_request_vectors_match_exact_bytes_and_digest(self) -> None:
        fixture = self.fixture()
        self.assertEqual(fixture["schema"], "cpk.node-control.canonical-wire.v1")
        self.assertEqual(fixture["consumers"], ["python", "java", "cpp"])

        for vector in fixture["requests"]:
            with self.subTest(vector=vector["name"]):
                request = NodeControlCommandRequestCodec().decode(vector["descriptor"])
                canonical = request.canonical_bytes()
                self.assertEqual(canonical.decode("utf-8"), vector["canonical_utf8"])
                self.assertEqual(canonical.hex(), vector["canonical_utf8_hex"])
                self.assertEqual(request.canonical_digest().value, vector["sha256"])

    def test_rfc_8785_number_vectors_survive_request_canonicalization(self) -> None:
        for vector in self.fixture()["rfc8785_number_vectors"]:
            with self.subTest(ieee754=vector["ieee754_hex"]):
                value = struct.unpack(">d", bytes.fromhex(vector["ieee754_hex"]))[0]
                canonical = self.scalar_request(value).canonical_bytes().decode("utf-8")
                self.assertIn(f'"value":{vector["canonical_json"]}', canonical)

    def test_equal_numeric_requests_have_equal_canonical_bytes_and_digest(self) -> None:
        integer = self.scalar_request(1)
        floating = self.scalar_request(1.0)

        self.assertEqual(integer, floating)
        self.assertEqual(integer.canonical_bytes(), floating.canonical_bytes())
        self.assertEqual(integer.canonical_digest(), floating.canonical_digest())

    def test_negative_zero_is_rejected_at_every_state_boundary(self) -> None:
        invalid = (
            lambda: ScalarControlState(-0.0),
            lambda: MapControlState((("value", -0.0),)),
            lambda: self.weighted_state(-0.0),
        )
        for factory in invalid:
            with self.subTest(factory=factory):
                with self.assertRaises(NodeControlContractError):
                    factory()

    def test_unsafe_integers_fail_closed_without_runtime_overflow(self) -> None:
        for value in (MAX_SAFE_INTEGER + 1, 10**400):
            with self.subTest(boundary="scalar", value=value):
                with self.assertRaises(NodeControlContractError):
                    ScalarControlState(value)
            with self.subTest(boundary="map", value=value):
                with self.assertRaises(NodeControlContractError):
                    MapControlState((("value", value),))
            with self.subTest(boundary="weight", value=value):
                with self.assertRaises(NodeControlContractError):
                    self.weighted_state(value)

            request = self.scalar_request(1)
            with self.subTest(boundary="epoch", value=value):
                with self.assertRaises(NodeControlContractError):
                    DelegatedWorkloadNodeControlGrant(
                        issuer="cpk-server",
                        key_id="workload-key-1",
                        audience="workload:router:control",
                        target=request.target,
                        variable_name=request.variable_name,
                        operation=request.operation,
                        command_codec=request.command_codec,
                        request_id=request.request_id,
                        idempotency_key=request.idempotency_key,
                        request_digest=request.canonical_digest(),
                        issued_at=value,
                        not_before=value,
                        expires_at=value + 1,
                        jti="grant-1",
                    )

    def test_safe_numeric_edges_remain_representable(self) -> None:
        for value in (-MAX_SAFE_INTEGER, MAX_SAFE_INTEGER, 1e20, 1e-7):
            with self.subTest(value=value):
                request = self.scalar_request(value)
                self.assertEqual(
                    NodeControlCommandRequestCodec().decode(request.descriptor()),
                    request,
                )
                self.assertIsInstance(request.canonical_bytes(), bytes)

        self.assertEqual(
            self.weighted_state(MAX_SAFE_INTEGER).weights[0][1],
            float(MAX_SAFE_INTEGER),
        )

    def test_core_declares_exact_canonicalizer_dependency(self) -> None:
        project = tomllib.loads(
            (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]
        self.assertIn("rfc8785==0.1.4", project["dependencies"])


if __name__ == "__main__":
    unittest.main()
