"""Exact-request health results, independent of transport or persistence."""
from dataclasses import replace
import unittest

import rfc8785
import control_plane_kit_core as core
from tests.test_node_health_read_authority import HealthReadFixtures


class NodeHealthReadResultTests(HealthReadFixtures, unittest.TestCase):
    def test_closed_outcomes_and_exact_context_derived_wire(self):
        request = self.request()
        declaration = self.declaration()
        outcome = self.api("NodeHealthReadOutcome")
        self.assertEqual({item.value for item in outcome}, {"healthy", "unhealthy", "unknown", "unsupported"})
        codec = self.api("NodeHealthReadResultCodec")(request, declaration)
        for status in outcome:
            result = self.api("NodeHealthReadResult")(request, declaration, status)
            expected = dict(profile="workload-node-health-read-result.v1", canonicalization="jcs-rfc8785.v1",
                            request_id=request.request_id, request_digest=request.canonical_digest().value,
                            declaration_identity=declaration.identity().value, kind="readiness", outcome=status.value)
            self.assertEqual(codec.encode(result), expected)
            self.assertEqual(result.canonical_bytes(), rfc8785.dumps(expected))
            self.assertEqual(codec.decode(expected), result)
        with self.assertRaises(ValueError):
            self.api("NodeHealthReadResult")(request, declaration, "healthy")

    def test_old_observation_cannot_satisfy_fresh_request_or_changed_context(self):
        request = self.request()
        declaration = self.declaration()
        result = self.api("NodeHealthReadResult")(request, declaration, self.api("NodeHealthReadOutcome").HEALTHY)
        codec_type = self.api("NodeHealthReadResultCodec")
        for candidate in (replace(request, request_id="health-read-2"),
                          replace(request, runtime_id=replace(request.runtime_id, value="runtime-2")),
                          replace(request, kind=core.NodeHealthReadKind.LIVENESS),
                          replace(request, target=replace(request.target, node_id=replace(request.target.node_id, value="other")))):
            with self.subTest(request_id=candidate.request_id), self.assertRaises(ValueError):
                codec_type(candidate, declaration).decode(result.descriptor())
            with self.assertRaises(ValueError):
                codec_type(candidate, declaration).encode(result)
        self.assertEqual(codec_type(request, declaration).decode(result.descriptor()), result)
        with self.assertRaises(ValueError):
            codec_type(replace(request, declaration_identity=core.WorkloadNodeControlSurfaceDeclarationIdentity("f"*64)), declaration)
        with self.assertRaises(ValueError):
            codec_type(replace(request, target=replace(request.target, provider_socket_name=replace(request.target.provider_socket_name, value="other"))), declaration)

    def test_strict_result_shape_bounds_and_no_transport_outcomes(self):
        request = self.request(request_id="a" * 128)
        declaration = self.declaration()
        result = self.api("NodeHealthReadResult")(request, declaration, self.api("NodeHealthReadOutcome").UNSUPPORTED)
        codec = self.api("NodeHealthReadResultCodec")(request, declaration)
        self.assertEqual(len(result.canonical_bytes()), 446)
        self.assertEqual(codec.decode(codec.encode(result)), result)
        for change in ({"profile": "workload-node-control-surface-read-result.v2"}, {"outcome": "timeout"},
                       {"outcome": "unauthorized"}, {"outcome": True}, {"details": "private-test-value"},
                       {"request_digest": "f"*64}, {"declaration_identity": "f"*64}, {"kind": "status"}):
            with self.subTest(change=tuple(change)), self.assertRaises(ValueError):
                codec.decode({**result.descriptor(), **change})
        raw = result.descriptor()
        raw["outcome"] = "x" * 12
        self.assertEqual(len(rfc8785.dumps(raw)), 447)
        with self.assertRaisesRegex(ValueError, "aggregate"):
            codec.decode(raw)
        short = self.request(request_id="a")
        short_codec = self.api("NodeHealthReadResultCodec")(short, declaration)
        with self.assertRaisesRegex(ValueError, "aggregate"):
            short_codec.decode(result.descriptor())
        with self.assertRaises(ValueError):
            replace(result, outcome="https://private.invalid")


if __name__ == "__main__":
    unittest.main()
