"""Closed health outcomes belong to one independently supplied successor request."""
from copy import deepcopy
from dataclasses import replace
import unittest

import rfc8785
import control_plane_kit_core as core
from tests.test_receiver_health_reads import ReceiverHealthFixtures
from tests import test_node_health_read_authority as historical_health
from tests import test_node_health_declarations as declarations


class ReceiverHealthReadResultTests(ReceiverHealthFixtures, unittest.TestCase):
    module_name = "receiver_health_read_results"

    def result(self, request=None, outcome=core.NodeHealthReadOutcome.HEALTHY):
        request = self.request() if request is None else request
        return self.api.ReceiverHealthReadResult(request, self.declaration(), outcome)

    def test_all_outcomes_have_exact_context_derived_v2_vectors(self):
        request = self.request()
        codec = self.api.ReceiverHealthReadResultCodec(request, self.declaration())
        for outcome in core.NodeHealthReadOutcome:
            result = self.result(request, outcome)
            expected = {"profile": "workload-node-health-read-result.v2", "canonicalization": "jcs-rfc8785.v1",
                        "request_id": "health-read-1", "request_digest": request.canonical_digest().value,
                        "declaration_identity": self.declaration().identity().value, "kind": "readiness",
                        "outcome": outcome.value}
            self.assertEqual(result.descriptor(), expected)
            self.assertEqual(codec.encode(result), expected)
            self.assertEqual(result.canonical_bytes(), rfc8785.dumps(expected))
            self.assertEqual(codec.decode(expected), result)
            self.assertEqual(codec.decode_canonical_bytes(rfc8785.dumps(expected)), result)

    def test_old_observation_refuses_fresh_request_scope_and_each_context_field(self):
        request, result = self.request(), self.result()
        codec_type = self.api.ReceiverHealthReadResultCodec
        candidates = [replace(request, request_id="fresh"), replace(request, kind=core.NodeHealthReadKind.LIVENESS),
                      replace(request, target=replace(request.target, receiver_id="b"*32))]
        candidates += [replace(request, authority_context=replace(request.authority_context, **{name: "other"}))
                       for name in ("authored_graph_id", "realized_projection_id")]
        candidates += [replace(request, target=replace(request.target, **{name: replace(getattr(request.target, name), value="other")}))
                       for name in ("workspace_id", "runtime_id", "node_id")]
        for candidate in candidates:
            codec = codec_type(candidate, self.declaration())
            self.refusal(lambda: codec.decode(result.descriptor()))
            self.refusal(lambda: codec.encode(result))
        for candidate in (replace(request, declaration_identity=core.WorkloadNodeControlSurfaceDeclarationIdentity("f"*64)),
                          replace(request, target=replace(request.target,
                              provider_socket_name=replace(request.target.provider_socket_name, value="other")))):
            self.refusal(lambda: codec_type(candidate, self.declaration()))
        different = declarations.NodeHealthDeclarationTests().declaration("mode")
        self.refusal(lambda: codec_type(request, different))
        rebound = replace(request, declaration_identity=different.identity())
        self.refusal(lambda: codec_type(rebound, different).decode(result.descriptor()))

    def test_exact_result_cap_context_tightening_and_first_overflow(self):
        request = self.request(request_id="a"*128)
        result = self.result(request, core.NodeHealthReadOutcome.UNSUPPORTED)
        codec = self.api.ReceiverHealthReadResultCodec(request, self.declaration())
        self.assertEqual(len(rfc8785.dumps(result.descriptor())), 446)
        self.assertEqual(len(result.canonical_bytes()), core.MAX_NODE_HEALTH_READ_RESULT_BYTES)
        self.assertEqual(codec.decode_canonical_bytes(result.canonical_bytes()), result)
        bad = result.descriptor() | {"outcome": "x"*12}
        self.assertEqual(len(rfc8785.dumps(bad)), 447)
        self.refusal(lambda: codec.decode(bad))
        self.refusal(lambda: codec.decode_canonical_bytes(rfc8785.dumps(bad)))
        short = self.request(request_id="a", kind=core.NodeHealthReadKind.LIVENESS)
        short_result = self.result(short, core.NodeHealthReadOutcome.UNSUPPORTED)
        self.assertEqual(len(short_result.canonical_bytes()), 446-127-1)
        self.refusal(lambda: self.api.ReceiverHealthReadResultCodec(short, self.declaration()).decode(result.descriptor()))

    def test_strict_result_profile_fields_outcomes_and_legacy_isolation(self):
        request, result = self.request(), self.result()
        codec = self.api.ReceiverHealthReadResultCodec(request, self.declaration())
        self.check_closed_raw_codec(codec, result)
        for change in ({"profile": "workload-node-health-read-result.v1"}, {"outcome": "timeout"},
                       {"outcome": "unauthorized"}, {"outcome": True}, {"details": "private-test-value"},
                       {"authority_context": {}}, {"request_digest": "f"*64},
                       {"declaration_identity": "f"*64}, {"kind": "status"}):
            self.refusal(lambda: codec.decode(result.descriptor() | change))
        old_request = historical_health.NodeHealthReadAuthorityTests().request()
        old_result = core.NodeHealthReadResult(old_request, self.declaration(), core.NodeHealthReadOutcome.HEALTHY)
        old_codec = core.NodeHealthReadResultCodec(old_request, self.declaration())
        self.refusal(lambda: codec.decode(old_result.descriptor()))
        self.refusal(lambda: codec.encode(old_result))
        with self.assertRaises(ValueError):
            old_codec.decode(result.descriptor())
        with self.assertRaises(ValueError):
            old_codec.encode(result)
        self.refusal(lambda: self.api.ReceiverHealthReadResult(old_request, self.declaration(), core.NodeHealthReadOutcome.HEALTHY))

    def test_context_and_member_forgery_is_revalidated_at_encode_and_decode(self):
        class Text(str):
            pass
        request, declaration = self.request(), self.declaration()
        result = self.result()
        codec = self.api.ReceiverHealthReadResultCodec(request, declaration)
        for value in ("healthy", Text("healthy"), None, True):
            self.refusal(lambda: replace(result, outcome=value))
        forged = deepcopy(result)
        object.__setattr__(forged, "outcome", "healthy")
        self.refusal(lambda: codec.encode(forged))
        forged_request = deepcopy(request)
        object.__setattr__(forged_request, "request_id", Text("health-read-1"))
        self.refusal(lambda: self.api.ReceiverHealthReadResultCodec(forged_request, declaration))
        forged_codec = deepcopy(codec)
        object.__setattr__(forged_codec.request, "request_id", Text("health-read-1"))
        self.refusal(lambda: forged_codec.encode(result))
        self.refusal(lambda: forged_codec.decode(result.descriptor()))
        absent = replace(declaration, surface=replace(declaration.surface, health_reads=(core.NodeHealthReadKind.LIVENESS,)))
        request = self.request(declaration_identity=absent.identity())
        self.refusal(lambda: self.api.ReceiverHealthReadResultCodec(request, absent))

    def test_result_public_exports_reuse_existing_outcome_owner(self):
        self.check_exports(self.api, ("ReceiverHealthReadResultProfile", "ReceiverHealthReadResult", "ReceiverHealthReadResultCodec"))


if __name__ == "__main__":
    unittest.main()
