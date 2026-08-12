import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import sys
import unittest

import rfc8785

import control_plane_kit_core as core
import control_plane_kit_core.node_control as node_control
from control_plane_kit_core.node_control import (
    ControlPlaneCommandCodec,
    ControlPlaneTransitionPrecondition,
    DelegatedWorkloadNodeControlGrant,
    DelegatedWorkloadNodeControlGrantCodec,
    NodeControlCommandRequest,
    NodeControlCommandRequestCodec,
    NodeControlContractError,
    NodeControlGraphReference,
    NodeControlGraphReferenceRole,
    NodeControlOperation,
    NodeControlPayload,
    NodeControlRequestDigest,
    NodeControlTarget,
    ScalarControlState,
)


FIXTURE = Path(__file__).parent / "fixtures" / "node_control_canonical_wire_v1.json"
MAX_SAFE_INTEGER = 2**53 - 1


class NodeControlWorkloadWireTests(unittest.TestCase):
    def fixture(self) -> dict[str, object]:
        return json.loads(FIXTURE.read_text(encoding="utf-8"))

    def reference(
        self,
        role: NodeControlGraphReferenceRole,
        value: str,
    ) -> NodeControlGraphReference:
        return NodeControlGraphReference(role, value)

    def target(self, value: str | None = None) -> NodeControlTarget:
        return NodeControlTarget(
            workspace_id=self.reference(
                NodeControlGraphReferenceRole.WORKSPACE,
                value or "workspace-1",
            ),
            graph_revision=self.reference(
                NodeControlGraphReferenceRole.GRAPH_REVISION,
                value or "revision-7",
            ),
            node_id=self.reference(
                NodeControlGraphReferenceRole.NODE,
                value or "router",
            ),
            provider_socket_name=self.reference(
                NodeControlGraphReferenceRole.PROVIDER_SOCKET,
                value or "control",
            ),
        )

    def scalar_request(self, value: int | float) -> NodeControlCommandRequest:
        return NodeControlCommandRequest(
            target=self.target(),
            variable_name=self.reference(
                NodeControlGraphReferenceRole.VARIABLE,
                "limit",
            ),
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

    def grant(
        self,
        request: NodeControlCommandRequest | None = None,
        **changes: object,
    ) -> DelegatedWorkloadNodeControlGrant:
        request = request or self.scalar_request(1)
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
            "jti": "workload-grant-1",
        }
        values.update(changes)
        return DelegatedWorkloadNodeControlGrant(**values)

    def test_language_neutral_grant_vector_and_public_contract(self) -> None:
        fixture = self.fixture()
        self.assertIn("workload_grants", fixture)
        vector = fixture["workload_grants"][0]
        encoded = vector["canonical_utf8"].encode("utf-8")
        self.assertEqual(rfc8785.dumps(vector["descriptor"]), encoded)
        self.assertEqual(encoded.hex(), vector["canonical_utf8_hex"])
        self.assertEqual(hashlib.sha256(encoded).hexdigest(), vector["sha256"])

        constant = getattr(
            node_control,
            "MAX_DELEGATED_WORKLOAD_NODE_CONTROL_GRANT_BYTES",
        )
        digest_type = getattr(node_control, "WorkloadNodeControlGrantDigest")
        grant = DelegatedWorkloadNodeControlGrantCodec().decode(vector["descriptor"])
        self.assertEqual(constant, 2_111)
        self.assertEqual(grant.canonical_bytes(), encoded)
        self.assertEqual(grant.canonical_digest(), digest_type(vector["sha256"]))
        self.assertIs(
            getattr(core, "WorkloadNodeControlGrantDigest"),
            digest_type,
        )

    def test_exact_grant_maximum_and_every_constituent_bound(self) -> None:
        identifier = "a" * 128
        reference = "a" * 256
        target = self.target(identifier)
        maximum = self.grant(
            issuer=reference,
            key_id=identifier,
            audience=reference,
            target=target,
            variable_name=self.reference(
                NodeControlGraphReferenceRole.VARIABLE,
                identifier,
            ),
            operation=NodeControlOperation.APPLY_COMMAND,
            command_codec=ControlPlaneCommandCodec.REPLACE_WEIGHTED_ROUTING_V1,
            request_id=identifier,
            idempotency_key=identifier,
            request_digest=NodeControlRequestDigest("a" * 64),
            issued_at=MAX_SAFE_INTEGER - 300,
            not_before=MAX_SAFE_INTEGER - 1,
            expires_at=MAX_SAFE_INTEGER,
            jti=identifier,
        )
        maximum_bytes = maximum.canonical_bytes()
        self.assertEqual(
            getattr(
                node_control,
                "MAX_DELEGATED_WORKLOAD_NODE_CONTROL_GRANT_BYTES",
            ),
            2_111,
        )
        self.assertEqual(len(maximum_bytes), 2_111)

        codec = DelegatedWorkloadNodeControlGrantCodec()
        descriptor = codec.encode(maximum)
        for key in ("key_id", "request_id", "idempotency_key", "jti"):
            with self.subTest(identifier=key):
                with self.assertRaises(NodeControlContractError):
                    codec.decode({**descriptor, key: "a" * 129})
        for key in ("issuer", "audience"):
            with self.subTest(reference=key):
                with self.assertRaises(NodeControlContractError):
                    codec.decode({**descriptor, key: "a" * 257})
        with self.assertRaises(NodeControlContractError):
            codec.decode({**descriptor, "request_digest": "a" * 65})
        for key in descriptor["target"]:
            with self.subTest(target=key):
                candidate = deepcopy(descriptor)
                candidate["target"][key] = "a" * 129
                with self.assertRaises(NodeControlContractError):
                    codec.decode(candidate)

    def test_request_and_grant_strict_raw_round_trips(self) -> None:
        fixture = self.fixture()
        request_codec = NodeControlCommandRequestCodec()
        request_decoder = getattr(request_codec, "decode_canonical_bytes")
        request_encoder = getattr(request_codec, "encode_canonical_bytes")
        for vector in fixture["requests"]:
            with self.subTest(request=vector["name"]):
                encoded = vector["canonical_utf8"].encode("utf-8")
                request = request_decoder(encoded)
                self.assertEqual(request_encoder(request), encoded)
                self.assertEqual(request.canonical_digest().value, vector["sha256"])

        grant_vector = fixture["workload_grants"][0]
        grant_codec = DelegatedWorkloadNodeControlGrantCodec()
        grant_decoder = getattr(grant_codec, "decode_canonical_bytes")
        grant_encoder = getattr(grant_codec, "encode_canonical_bytes")
        encoded = grant_vector["canonical_utf8"].encode("utf-8")
        grant = grant_decoder(encoded)
        self.assertEqual(grant_encoder(grant), encoded)
        self.assertEqual(grant.canonical_digest().value, grant_vector["sha256"])

    def test_jcs_number_observation_round_trips_and_rejects_ambiguity(self) -> None:
        decoder = getattr(NodeControlCommandRequestCodec(), "decode_canonical_bytes")
        for vector in self.fixture()["rfc8785_number_vectors"]:
            with self.subTest(number=vector["canonical_json"]):
                value = struct.unpack(
                    ">d",
                    bytes.fromhex(vector["ieee754_hex"]),
                )[0]
                request = self.scalar_request(value)
                self.assertEqual(decoder(request.canonical_bytes()), request)

        canonical = self.scalar_request(1e20).canonical_bytes()
        self.assertIn(b"100000000000000000000", canonical)
        candidates = (
            canonical.replace(
                b"100000000000000000000",
                b"100000000000000000001",
            ),
            canonical.replace(b"100000000000000000000", b"1e20"),
            canonical.replace(b"100000000000000000000", b"-0"),
            canonical.replace(b"100000000000000000000", b"1e400"),
            canonical.replace(b'"expected_version":4', b'"expected_version":9007199254740992'),
        )
        for candidate in candidates:
            with self.subTest(candidate=candidate[-96:]):
                with self.assertRaises(NodeControlContractError):
                    decoder(candidate)

    def test_strict_decoders_reject_malformed_noncanonical_and_foreign_bytes(self) -> None:
        request_codec = NodeControlCommandRequestCodec()
        grant_codec = DelegatedWorkloadNodeControlGrantCodec()
        request_decoder = getattr(request_codec, "decode_canonical_bytes")
        grant_decoder = getattr(grant_codec, "decode_canonical_bytes")
        request_bytes = self.scalar_request(1).canonical_bytes()
        grant_bytes = self.grant().canonical_bytes()
        duplicate_request = request_bytes.replace(
            b'{"canonicalization":"jcs-rfc8785.v1",',
            b'{"canonicalization":"jcs-rfc8785.v1","canonicalization":"jcs-rfc8785.v1",',
            1,
        )
        duplicate_grant_target = grant_bytes.replace(
            b'{"graph_revision":"revision-7","node_id":"router",',
            b'{"graph_revision":"revision-7","node_id":"router","node_id":"other",',
            1,
        )
        cases = (
            (request_decoder, b"x" * 16_385),
            (grant_decoder, b"x" * 2_112),
            (request_decoder, b"\xff"),
            (request_decoder, b"{"),
            (request_decoder, b"[]"),
            (request_decoder, duplicate_request),
            (grant_decoder, duplicate_grant_target),
            (request_decoder, b" " + request_bytes),
            (grant_decoder, grant_bytes + b" "),
            (request_decoder, grant_bytes),
            (grant_decoder, request_bytes),
        )
        for decoder, candidate in cases:
            with self.subTest(decoder=decoder, candidate=candidate[:24]):
                with self.assertRaises(NodeControlContractError) as caught:
                    decoder(candidate)
                self.assertLessEqual(len(str(caught.exception)), 128)
                self.assertNotIn("workload-key-1", str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)

    def test_recursion_failure_is_nominal_and_runtime_state_is_restored(self) -> None:
        decoder = getattr(NodeControlCommandRequestCodec(), "decode_canonical_bytes")
        previous_limit = sys.getrecursionlimit()
        try:
            sys.setrecursionlimit(120)
            candidate = b"[" * 100 + b"0" + b"]" * 100
            with self.assertRaises(NodeControlContractError) as caught:
                decoder(candidate)
            self.assertIsNone(caught.exception.__cause__)
            self.assertIsNone(caught.exception.__context__)
        finally:
            sys.setrecursionlimit(previous_limit)
        self.assertEqual(sys.getrecursionlimit(), previous_limit)

    def test_exports_documentation_and_effect_boundary_are_explicit(self) -> None:
        for name in (
            "MAX_DELEGATED_WORKLOAD_NODE_CONTROL_GRANT_BYTES",
            "WorkloadNodeControlGrantDigest",
        ):
            with self.subTest(export=name):
                self.assertTrue(hasattr(core, name))
                self.assertIn(name, core.__all__)

        module_path = Path(node_control.__file__)
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".")[0]
            for statement in ast.walk(tree)
            if isinstance(statement, ast.Import)
            for alias in statement.names
        } | {
            (statement.module or "").split(".")[0]
            for statement in ast.walk(tree)
            if isinstance(statement, ast.ImportFrom)
        }
        self.assertTrue(
            {
                "control_plane_kit_operations",
                "control_plane_kit_server_sdk",
                "control_plane_kit_interpreters",
                "fastapi",
                "httpx",
                "jwt",
                "psycopg",
            }.isdisjoint(imported)
        )

        core_docs = Path(__file__).parents[1] / "docs" / "NODE_CONTROL_CANONICAL_WIRE.md"
        text = core_docs.read_text(encoding="utf-8")
        for term in ("#1578", "2,111", "WorkloadNodeControlGrantDigest", "#1555"):
            with self.subTest(documented=term):
                self.assertIn(term, text)


if __name__ == "__main__":
    unittest.main()
