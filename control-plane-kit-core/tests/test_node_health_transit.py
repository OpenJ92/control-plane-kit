"""Pure health-transit laws over the actual workload health request language."""
from copy import deepcopy
from dataclasses import replace
import hashlib
from pathlib import Path
import unittest

import rfc8785
import control_plane_kit_core as core
from tests.test_node_health_read_authority import HealthReadFixtures
from tests import test_node_control_transit as legacy
from tests import test_node_control_surface_read_authority as static


class NodeHealthTransitTests(HealthReadFixtures, unittest.TestCase):
    def transit(self, request=None, **changes):
        grant_type = self.api("DelegatedGatewayNodeHealthReadTransitGrant")
        request = request or self.request()
        values = dict(profile=self.api("DelegatedGatewayNodeHealthReadTransitGrantProfile").V1,
                      canonicalization=core.NodeControlCanonicalization.JCS_RFC8785_V1,
                      purpose=core.DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT,
                      issuer="cpk-server", key_id="health-transit-key-1", attempt_id="attempt-1",
                      gateway_node_id=core.NodeControlGraphReference(core.NodeControlGraphReferenceRole.NODE, "gateway-1"),
                      target=request.target, runtime_id=request.runtime_id, kind=request.kind,
                      declaration_identity=request.declaration_identity, request_id=request.request_id,
                      request_digest=request.canonical_digest(), issued_at=100, not_before=100,
                      expires_at=200, jti="health-transit-grant-1")
        values.update(changes)
        return grant_type(**values)

    def admit(self, grant, request=None, **changes):
        request = request or self.request()
        values = dict(expected_issuer="cpk-server", expected_key_id="health-transit-key-1",
                      expected_attempt_id="attempt-1",
                      expected_gateway_node_id=core.NodeControlGraphReference(core.NodeControlGraphReferenceRole.NODE, "gateway-1"),
                      expected_target=request.target, expected_runtime_id=request.runtime_id,
                      expected_declaration=self.declaration(), expected_kind=request.kind, now=150)
        values.update(changes)
        return self.api("verify_gateway_node_health_read_transit_grant")(grant, request, **values)

    def test_fixed_canonical_vector_digest_and_codec_roundtrip(self):
        grant = self.transit(declaration_identity=core.WorkloadNodeControlSurfaceDeclarationIdentity("a"*64),
                             request_digest=core.NodeHealthReadRequestDigest("b"*64))
        expected = (Path(__file__).parent / "fixtures" / "node_health_transit_canonical_v1.json").read_bytes().rstrip(b"\n")
        codec = self.api("DelegatedGatewayNodeHealthReadTransitGrantCodec")()
        self.assertEqual(grant.canonical_bytes(), expected)
        self.assertEqual(codec.encode_canonical_bytes(grant), expected)
        self.assertEqual(grant.canonical_digest().value, hashlib.sha256(expected).hexdigest())
        self.assertEqual(codec.decode_canonical_bytes(expected), grant)
        self.assertEqual(codec.decode(codec.encode(grant)), grant)
        self.assertEqual(grant.audience, "gateway:workspace-1:gateway-1")
        self.assertNotIn("workspace_id", grant.descriptor())
        self.assertNotIn("graph_revision", grant.descriptor())
        self.assertEqual(len(grant.descriptor()), 18)

    def test_independent_gateway_target_runtime_and_declaration_are_required(self):
        request = self.request()
        grant = self.transit(request)
        self.assertTrue(self.admit(grant, request).is_accepted)
        contexts = [dict(expected_gateway_node_id=replace(grant.gateway_node_id, value="other-gateway")),
                    dict(expected_runtime_id=replace(request.runtime_id, value="other-runtime")),
                    dict(expected_kind=core.NodeHealthReadKind.LIVENESS), dict(expected_attempt_id="other-attempt"),
                    dict(expected_declaration=replace(self.declaration(), surface=replace(self.declaration().surface, health_reads=(core.NodeHealthReadKind.LIVENESS,))))]
        for field in ("workspace_id", "graph_revision", "node_id", "provider_socket_name"):
            contexts.append(dict(expected_target=replace(request.target, **{field: replace(getattr(request.target, field), value="other")})))
        for context in contexts:
            with self.subTest(context=tuple(context)):
                self.assertFalse(self.admit(grant, request, **context).is_accepted)
        declaration = replace(self.declaration(), surface=replace(self.declaration().surface, health_reads=(core.NodeHealthReadKind.LIVENESS,)))
        candidate = replace(request, declaration_identity=declaration.identity())
        self.assertFalse(self.admit(self.transit(candidate), candidate, expected_declaration=declaration).is_accepted)
        for context in (dict(expected_gateway_node_id=request.runtime_id), dict(expected_target=object()),
                        dict(expected_runtime_id=request.target.node_id), dict(expected_declaration=object()),
                        dict(expected_kind="readiness"), dict(expected_attempt_id="bad attempt"), dict(now=True)):
            with self.subTest(context=tuple(context)), self.assertRaises(ValueError):
                self.admit(grant, request, **context)

    def test_each_claim_and_declared_rejection_precedence(self):
        request = self.request()
        grant = self.transit(request)
        code = self.api("GatewayNodeHealthReadTransitGrantVerificationCode")
        changes = [(dict(issuer="other", key_id="other"), code.ISSUER_MISMATCH),
                   (dict(key_id="other", not_before=151), code.KEY_MISMATCH),
                   (dict(not_before=151, attempt_id="other"), code.TEMPORALLY_INVALID),
                   (dict(attempt_id="other", gateway_node_id=replace(grant.gateway_node_id, value="other")), code.ATTEMPT_MISMATCH),
                   (dict(gateway_node_id=replace(grant.gateway_node_id, value="other")), code.GATEWAY_MISMATCH),
                   (dict(runtime_id=replace(grant.runtime_id, value="other")), code.RUNTIME_MISMATCH),
                   (dict(kind=core.NodeHealthReadKind.LIVENESS), code.KIND_MISMATCH),
                   (dict(declaration_identity=core.WorkloadNodeControlSurfaceDeclarationIdentity("f"*64)), code.DECLARATION_MISMATCH),
                   (dict(request_id="other"), code.REQUEST_MISMATCH),
                   (dict(request_digest=core.NodeHealthReadRequestDigest("f"*64)), code.REQUEST_MISMATCH)]
        for field, reason in (("workspace_id", code.WORKSPACE_MISMATCH), ("graph_revision", code.REVISION_MISMATCH),
                              ("node_id", code.NODE_MISMATCH), ("provider_socket_name", code.SOCKET_MISMATCH)):
            changes.append((dict(target=replace(request.target, **{field: replace(getattr(request.target, field), value="other")})), reason))
        for change, reason in changes:
            with self.subTest(change=tuple(change)):
                self.assertIs(self.admit(replace(grant, **change), request).code, reason)
        self.assertIs(self.admit(object()).code, code.GRANT_TYPE_MISMATCH)
        self.assertEqual(self.admit(grant).descriptor(), {"accepted": True, "code": None})
        result_type = self.api("GatewayNodeHealthReadTransitGrantVerificationResult")
        for args in ((True, code.REQUEST_MISMATCH), (False, None), (1, None)):
            with self.assertRaises(ValueError):
                result_type(*args)

    def test_exact_audience_aggregate_and_field_boundaries(self):
        grant = self.transit()
        identifier = "a"*128
        target = core.NodeControlTarget(**{field: replace(getattr(grant.target, field), value=identifier)
                                          for field in ("workspace_id", "graph_revision", "node_id", "provider_socket_name")})
        maximum = replace(grant, issuer="a"*256, key_id=identifier, attempt_id=identifier,
                          gateway_node_id=replace(grant.gateway_node_id, value=identifier), target=target,
                          runtime_id=replace(grant.runtime_id, value=identifier), request_id=identifier, jti=identifier,
                          issued_at=2**53-301, not_before=2**53-300, expires_at=2**53-1)
        codec = self.api("DelegatedGatewayNodeHealthReadTransitGrantCodec")()
        self.assertEqual(len(maximum.audience), self.api("MAX_GATEWAY_NODE_HEALTH_READ_TRANSIT_AUDIENCE_BYTES"))
        self.assertEqual(len(maximum.audience), 265)
        self.assertEqual(len(maximum.canonical_bytes()), self.api("MAX_DELEGATED_GATEWAY_NODE_HEALTH_READ_TRANSIT_GRANT_BYTES"))
        self.assertEqual(len(maximum.canonical_bytes()), 2423)
        self.assertEqual(codec.decode_canonical_bytes(maximum.canonical_bytes()), maximum)
        with self.assertRaisesRegex(ValueError, "aggregate"):
            codec.decode_canonical_bytes(b"x"*2424)
        raw = maximum.descriptor()
        raw["target"] = ""
        raw["target"] = "x"*(2424-len(rfc8785.dumps(raw)))
        with self.assertRaisesRegex(ValueError, "aggregate"):
            codec.decode(raw)
        for field in ("key_id", "attempt_id", "request_id", "jti"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                replace(grant, **{field: identifier+"a"})
        for change in (dict(issuer="a"*257), dict(gateway_node_id=grant.runtime_id),
                       dict(request_digest=core.NodeControlRequestDigest("a"*64)),
                       dict(purpose=core.DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT)):
            with self.subTest(change=tuple(change)), self.assertRaises(ValueError):
                replace(grant, **change)
        with self.assertRaises(ValueError):
            codec.decode({**grant.descriptor(), "audience": "gateway:workspace-1:other"})

    def test_raw_codec_is_strict_and_failures_are_bounded_cause_free(self):
        grant = self.transit()
        codec = self.api("DelegatedGatewayNodeHealthReadTransitGrantCodec")()
        error = self.api("GatewayNodeHealthReadTransitContractError")
        raw = grant.canonical_bytes()
        candidates = [b"\xff", b"{", b"["*1100+b"0"+b"]"*1100, b" "+raw,
                      raw.replace(b'"attempt_id":"attempt-1",', b'"attempt_id":"attempt-1","attempt_id":"other",', 1),
                      raw.replace(b'"node_id":"router",', b'"node_id":"router","node_id":"other",', 1),
                      raw.replace(b'"issued_at":100', b'"issued_at":NaN'),
                      raw.replace(b'"issued_at":100', b'"issued_at":true')]
        for candidate in candidates:
            with self.subTest(length=len(candidate)), self.assertRaises(error) as caught:
                codec.decode_canonical_bytes(candidate)
            self.assertLessEqual(len(str(caught.exception)), 128)
            self.assertIsNone(caught.exception.__cause__)
            self.assertIsNone(caught.exception.__context__)
        for key in grant.descriptor():
            missing = grant.descriptor()
            del missing[key]
            with self.subTest(missing=key), self.assertRaises(error):
                codec.decode(missing)
            with self.subTest(wrong_type=key), self.assertRaises(error):
                codec.decode({**grant.descriptor(), key: None})
        for extra in ({"unknown": "private-test-value"}, {"variable_name": "mode"}, {"idempotency_key": "old"}):
            with self.assertRaises(error):
                codec.decode({**grant.descriptor(), **extra})
        nested = deepcopy(grant.descriptor())
        nested["target"]["unknown"] = "private-test-value"
        with self.assertRaises(error):
            codec.decode(nested)
        for value in ("token=private-test-value", "https://private.invalid", "127.0.0.1:8000"):
            with self.assertRaises(error) as caught:
                replace(grant, issuer=value)
            self.assertNotIn(value, str(caught.exception))
        for field in ("issuer", "key_id", "attempt_id", "request_id", "jti"):
            self.assertNotIn(getattr(grant, field), repr(grant))

    def test_all_five_other_authority_families_remain_distinct(self):
        health_transit = self.transit()
        old = legacy.NodeControlTransitTests()
        static_fixture = static.NodeControlSurfaceReadAuthorityTests()
        command_request = old.request()
        command = core.DelegatedWorkloadNodeControlGrant(
            issuer="cpk-server", key_id="command-key", audience="workload:router:control",
            target=command_request.target, variable_name=command_request.variable_name,
            operation=command_request.operation, command_codec=command_request.command_codec,
            request_id=command_request.request_id, idempotency_key=command_request.idempotency_key,
            request_digest=command_request.canonical_digest(), issued_at=100, not_before=100, expires_at=200, jti="command-grant")
        codec = self.api("DelegatedGatewayNodeHealthReadTransitGrantCodec")()
        for candidate in (old.grant(), self.grant(), static_fixture.grant(), static_fixture.gateway_grant(), command):
            with self.subTest(family=type(candidate).__name__):
                self.assertFalse(self.admit(candidate).is_accepted)
                with self.assertRaises(ValueError):
                    codec.decode(candidate.descriptor())
        for other_codec in (core.DelegatedGatewayNodeControlTransitGrantCodec(), core.DelegatedWorkloadNodeHealthReadGrantCodec(),
                            core.DelegatedWorkloadNodeControlSurfaceReadGrantCodec(), core.DelegatedGatewayProbeGrantCodec(),
                            core.DelegatedWorkloadNodeControlGrantCodec()):
            with self.subTest(codec=type(other_codec).__name__), self.assertRaises(ValueError):
                other_codec.decode(health_transit.descriptor())
        self.assertFalse(old.verify(health_transit).is_accepted)
        self.assertFalse(self.verify(health_transit).is_accepted)
        self.assertFalse(core.verify_workload_node_control_surface_read_grant(health_transit, static_fixture.request(),
                         expected_issuer="cpk-server", expected_key_id="key", expected_audience="workload:router:control", now=150).is_accepted)
        self.assertFalse(core.verify_workload_node_control_grant(health_transit, command_request,
                         expected_issuer="cpk-server", expected_audience="workload:router:control", now=150).is_accepted)
        read = old.request(operation=core.NodeControlOperation.READ_STATE, command_codec=None, precondition=None, payload=None)
        read_grant = old.grant(read, operation=read.operation, command_codec=None)
        self.assertTrue(old.verify(read_grant, read).is_accepted)

    def test_attempt_and_gateway_changes_do_not_manufacture_new_observations(self):
        request = self.request()
        grant = self.transit(request)
        self.assertEqual(replace(grant, attempt_id="attempt-2").request_digest, request.canonical_digest())
        self.assertEqual(replace(grant, gateway_node_id=replace(grant.gateway_node_id, value="gateway-2")).request_digest, request.canonical_digest())
        fresh = replace(request, request_id="health-read-2")
        self.assertFalse(self.admit(grant, fresh).is_accepted)
        self.assertTrue(self.admit(self.transit(fresh), fresh).is_accepted)
        for now, accepted in ((99, False), (100, True), (199, True), (200, False)):
            with self.subTest(now=now):
                self.assertEqual(self.admit(grant, now=now).is_accepted, accepted)
        self.assertEqual(self.api("MAX_GATEWAY_NODE_HEALTH_READ_TRANSIT_GRANT_LIFETIME_SECONDS"), 300)
        self.assertTrue(self.admit(replace(grant, expires_at=400), now=399).is_accepted)
        for change in (dict(issued_at=True), dict(not_before=99), dict(expires_at=100), dict(expires_at=401), dict(expires_at=2**53)):
            with self.subTest(change=tuple(change)), self.assertRaises(ValueError):
                replace(grant, **change)


if __name__ == "__main__":
    unittest.main()
