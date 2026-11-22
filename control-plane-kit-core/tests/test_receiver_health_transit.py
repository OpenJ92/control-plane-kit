"""Gateway own-receiver identity is independent of workload and transit routing."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import unittest

import rfc8785
import control_plane_kit_core as core
from tests.test_receiver_health_reads import ReceiverHealthFixtures, trim_to_bound
from tests import test_node_health_transit as historical_transit
from tests import test_node_control_surface_read_authority as historical_surface
from tests import test_node_control_transit as historical_command


class ReceiverHealthTransitTests(ReceiverHealthFixtures, unittest.TestCase):
    module_name = "receiver_health_transit"

    def gateway_target(self):
        target = self.target()
        return replace(target, node_id=replace(target.node_id, value="gateway-1"),
                       provider_socket_name=replace(target.provider_socket_name, value="gateway-control"),
                       receiver_id="b"*32)

    def transit(self, request=None, **changes):
        request = self.request() if request is None else request
        values = dict(profile=self.api.DelegatedGatewayReceiverHealthReadTransitGrantProfile.V2,
                      canonicalization=core.NodeControlCanonicalization.JCS_RFC8785_V1,
                      purpose=core.DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT,
                      issuer="cpk-server", key_id="health-transit-key-1", attempt_id="attempt-1",
                      gateway_target=self.gateway_target(), target=request.target,
                      authority_context=request.authority_context, kind=request.kind,
                      declaration_identity=request.declaration_identity, request_id=request.request_id,
                      request_digest=request.canonical_digest(), issued_at=100, not_before=100,
                      expires_at=200, jti="health-transit-grant-1")
        return self.api.DelegatedGatewayReceiverHealthReadTransitGrant(**(values | changes))

    def admit(self, grant, request=None, **changes):
        request = self.request() if request is None else request
        values = dict(expected_issuer="cpk-server", expected_key_id="health-transit-key-1",
                      expected_attempt_id="attempt-1", expected_gateway_target=self.gateway_target(),
                      expected_target=self.target(), expected_declaration=self.declaration(),
                      expected_kind=core.NodeHealthReadKind.READINESS, now=150)
        return self.api.verify_gateway_receiver_health_read_transit_grant(grant, request, **(values | changes))

    def test_exact_vector_and_contexts_preserve_distinct_own_receiver_socket(self):
        request, grant = self.request(), self.transit()
        expected = self.request_document() | {"profile": "gateway-node-health-read-transit-grant.v2",
            "purpose": "gateway-node-health-read-transit", "issuer": "cpk-server", "key_id": "health-transit-key-1",
            "attempt_id": "attempt-1", "gateway_target": {"workspace_id": "workspace-1", "runtime_id": "runtime-1",
            "node_id": "gateway-1", "provider_socket_name": "gateway-control", "receiver_id": "b"*32},
            "audience": "gateway:workspace-1:gateway-1", "request_digest": request.canonical_digest().value,
            "issued_at": 100, "not_before": 100, "expires_at": 200, "jti": "health-transit-grant-1"}
        self.assertEqual(grant.descriptor(), expected)
        self.assertEqual(grant.canonical_bytes(), rfc8785.dumps(expected))
        self.assertEqual(grant.canonical_digest().value, hashlib.sha256(rfc8785.dumps(expected)).hexdigest())
        self.assertNotEqual(grant.gateway_target.provider_socket_name, grant.target.provider_socket_name)
        self.assertNotIn("transit_socket", expected)
        self.assertNotIn("runtime_id", expected)
        self.assertNotIn("gateway_node_id", expected)
        for name in "ABC":
            candidate = self.request(authority_context=self.context(name))
            self.assertTrue(self.admit(self.transit(candidate), candidate).is_accepted)
        changed = replace(grant, attempt_id="attempt-2")
        self.assertEqual(changed.request_digest, request.canonical_digest())
        changed = replace(grant, gateway_target=replace(grant.gateway_target, receiver_id="c"*32))
        self.assertEqual(changed.request_digest, request.canonical_digest())
        self.assertNotEqual(changed.canonical_digest(), grant.canonical_digest())

    def test_every_independent_gateway_and_workload_scope_is_checked(self):
        code = self.api.GatewayReceiverHealthReadTransitGrantVerificationCode
        grant = self.transit()
        for name in ("workspace_id", "runtime_id"):
            other_gateway = replace(grant.gateway_target, **{name: replace(getattr(grant.gateway_target, name), value="other")})
            self.refusal(lambda: replace(grant, gateway_target=other_gateway))
            self.refusal(lambda: self.admit(grant, expected_gateway_target=other_gateway))
            other_target = replace(grant.target, **{name: replace(getattr(grant.target, name), value="other")})
            self.refusal(lambda: replace(grant, target=other_target))
            # A self-consistent foreign pair must still lose to independent local scope.
            request = self.request(target=other_target)
            candidate = self.transit(request, gateway_target=other_gateway)
            self.assertIs(self.admit(candidate, request).code, code.GATEWAY_MISMATCH)
        for name in ("node_id", "provider_socket_name", "receiver_id"):
            other = ("c"*32 if name == "receiver_id" else replace(getattr(grant.gateway_target, name), value="other"))
            changed = replace(grant.gateway_target, **{name: other})
            self.assertIs(self.admit(replace(grant, gateway_target=changed)).code, code.GATEWAY_MISMATCH)
            self.assertIs(self.admit(grant, expected_gateway_target=changed).code, code.GATEWAY_MISMATCH)
        for name, reason in (("node_id", code.NODE_MISMATCH), ("provider_socket_name", code.SOCKET_MISMATCH),
                             ("receiver_id", code.RECEIVER_MISMATCH)):
            other = "c"*32 if name == "receiver_id" else replace(getattr(grant.target, name), value="other")
            target = replace(grant.target, **{name: other})
            candidate = self.request(target=target)
            self.assertIs(self.admit(self.transit(candidate), candidate).code, reason)
            self.assertIs(self.admit(replace(grant, target=target)).code, reason)
        # Independent local pair can share a new scope; an old request cannot claim it.
        for name, reason in (("workspace_id", code.WORKSPACE_MISMATCH), ("runtime_id", code.RUNTIME_MISMATCH)):
            target = replace(grant.target, **{name: replace(getattr(grant.target, name), value="other")})
            gateway = replace(grant.gateway_target, **{name: replace(getattr(grant.gateway_target, name), value="other")})
            candidate = replace(grant, gateway_target=gateway, target=target)
            self.assertIs(self.admit(candidate, expected_target=target, expected_gateway_target=gateway).code, reason)

    def test_attempt_claim_context_and_local_semantic_precedence(self):
        code = self.api.GatewayReceiverHealthReadTransitGrantVerificationCode
        grant = self.transit()
        other_gateway = replace(grant.gateway_target, receiver_id="c"*32)
        changes = [(dict(issuer="other", key_id="other"), code.ISSUER_MISMATCH),
                   (dict(key_id="other", not_before=151), code.KEY_MISMATCH),
                   (dict(not_before=151, attempt_id="other"), code.TEMPORALLY_INVALID),
                   (dict(attempt_id="other", gateway_target=other_gateway), code.ATTEMPT_MISMATCH),
                   (dict(gateway_target=other_gateway, authority_context=self.context("B")), code.GATEWAY_MISMATCH),
                   (dict(authority_context=self.context("B"), kind=core.NodeHealthReadKind.LIVENESS), code.AUTHORITY_CONTEXT_MISMATCH),
                   (dict(kind=core.NodeHealthReadKind.LIVENESS,
                         declaration_identity=core.WorkloadNodeControlSurfaceDeclarationIdentity("f"*64)), code.KIND_MISMATCH),
                   (dict(declaration_identity=core.WorkloadNodeControlSurfaceDeclarationIdentity("f"*64), request_id="other"), code.DECLARATION_MISMATCH),
                   (dict(request_id="other"), code.REQUEST_MISMATCH),
                   (dict(request_digest=self.reads.ReceiverHealthReadRequestDigest("f"*64)), code.REQUEST_MISMATCH)]
        for change, reason in changes:
            with self.subTest(change=tuple(change)):
                self.assertIs(self.admit(replace(grant, **change)).code, reason)
        self.assertIs(self.admit(grant, expected_attempt_id="other").code, code.ATTEMPT_MISMATCH)
        old_declaration = historical_surface.NodeControlSurfaceReadAuthorityTests().declaration()
        self.assertIs(self.admit(grant, expected_declaration=old_declaration).code, code.DECLARATION_MISMATCH)
        self.assertIs(self.admit(grant, expected_kind=core.NodeHealthReadKind.LIVENESS).code, code.KIND_MISMATCH)
        undeclared = replace(self.declaration(), surface=replace(self.declaration().surface,
                             health_reads=(core.NodeHealthReadKind.LIVENESS,)))
        request = self.request(declaration_identity=undeclared.identity())
        self.assertIs(self.admit(self.transit(request), request, expected_declaration=undeclared).code, code.KIND_MISMATCH)
        self.assertEqual(self.admit(grant).descriptor(), {"accepted": True, "code": None})

    def test_deleted_forged_fields_and_malformed_local_values_fail_without_leaks(self):
        code = self.api.GatewayReceiverHealthReadTransitGrantVerificationCode
        grant = self.transit()
        fields = ("profile", "canonicalization", "purpose", "issuer", "key_id", "attempt_id", "gateway_target",
                  "target", "authority_context", "kind", "declaration_identity", "request_id", "request_digest",
                  "issued_at", "not_before", "expires_at", "jti")
        for name in fields:
            forged = deepcopy(grant)
            object.__delattr__(forged, name)
            with self.subTest(missing=name):
                self.assertIs(self.admit(forged).code, code.GRANT_INVALID)
        forged = deepcopy(grant)
        object.__delattr__(forged, "gateway_target")
        object.__setattr__(forged, "purpose", core.DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT)
        self.assertIs(self.admit(forged).code, code.PURPOSE_MISMATCH)
        for name, value in (("gateway_target", object()), ("issuer", "Bearer private-test-value"),
                            ("issued_at", True), ("profile", "gateway-node-health-read-transit-grant.v2")):
            forged = deepcopy(grant)
            object.__setattr__(forged, name, value)
            self.assertIs(self.admit(forged).code, code.GRANT_INVALID)
            self.refusal(lambda: self.api.DelegatedGatewayReceiverHealthReadTransitGrantCodec().encode(forged))
        for changes in (dict(expected_gateway_target=object()), dict(expected_target=object()),
                        dict(expected_declaration=object()), dict(expected_kind="readiness"), dict(now=True),
                        dict(expected_attempt_id="bad attempt"), dict(expected_issuer="Bearer private-test-value")):
            self.refusal(lambda: self.admit(grant, **changes))
        self.assertIs(self.admit(object()).code, code.GRANT_TYPE_MISMATCH)
        result = self.api.GatewayReceiverHealthReadTransitGrantVerificationResult
        for args in ((True, code.REQUEST_MISMATCH), (False, None), (1, None), (False, "request-mismatch")):
            self.refusal(lambda: result(*args))

    def test_interval_and_constituent_bounds_preserve_original_expiry(self):
        class Text(str):
            pass
        grant = self.transit()
        for now, accepted in ((99, False), (100, True), (199, True), (200, False)):
            self.assertEqual(self.admit(grant, now=now).is_accepted, accepted)
        self.assertTrue(self.admit(replace(grant, expires_at=400), now=399).is_accepted)
        self.assertTrue(self.admit(replace(grant, issued_at=0, not_before=0, expires_at=300), now=0).is_accepted)
        for changes in (dict(issued_at=True), dict(not_before=99), dict(expires_at=100), dict(expires_at=401),
                        dict(expires_at=2**53), dict(attempt_id="x"*129), dict(key_id="x"*129),
                        dict(jti="x"*129), dict(issuer="x"*257), dict(request_id="x"*129),
                        dict(attempt_id=Text("attempt")), dict(request_digest=core.NodeHealthReadRequestDigest("b"*64)),
                        dict(purpose=core.DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT)):
            self.refusal(lambda: replace(grant, **changes))
        for value in ("a"*63, "A"*64, True, Text("a"*64)):
            self.refusal(lambda: self.api.GatewayReceiverHealthReadTransitGrantDigest(value))
        self.assertNotEqual(grant.canonical_digest(), core.GatewayNodeHealthReadTransitGrantDigest(grant.canonical_digest().value))
        for field in ("issuer", "key_id", "attempt_id", "request_id", "jti"):
            self.assertNotIn(getattr(grant, field), repr(grant))

    def test_independent_reachable_transit_cap_and_audience_maximum(self):
        _, request, _ = self.maximum_request_documents()
        gateway = deepcopy(request["target"])
        gateway["receiver_id"] = "b"*32
        document = request | {"profile": "gateway-node-health-read-transit-grant.v2",
            "purpose": "gateway-node-health-read-transit", "issuer": "a"*256, "key_id": "a"*128,
            "attempt_id": "a"*128, "gateway_target": gateway, "audience": "gateway:"+"a"*128+":"+"a"*128,
            "request_digest": "b"*64, "issued_at": 2**53-301, "not_before": 2**53-300,
            "expires_at": 2**53-1, "jti": "a"*128}
        self.assertEqual(len(document["audience"]), 265)
        self.assertGreater(len(rfc8785.dumps(document)), 2423)
        codec = self.api.DelegatedGatewayReceiverHealthReadTransitGrantCodec()
        self.refusal(lambda: codec.decode(document))
        exact, overflow = trim_to_bound(document, 2423, (("issuer",), ("jti",), ("attempt_id",), ("key_id",)))
        grant = codec.decode(exact)
        self.assertEqual(len(grant.audience), core.MAX_GATEWAY_NODE_HEALTH_READ_TRANSIT_AUDIENCE_BYTES)
        self.assertEqual(len(grant.canonical_bytes()), core.MAX_DELEGATED_GATEWAY_NODE_HEALTH_READ_TRANSIT_GRANT_BYTES)
        self.assertEqual(codec.decode_canonical_bytes(rfc8785.dumps(exact)), grant)
        self.assertEqual(len(rfc8785.dumps(overflow)), 2424)
        self.refusal(lambda: codec.decode(overflow))
        self.refusal(lambda: codec.decode_canonical_bytes(rfc8785.dumps(overflow)))

    def test_strict_wire_and_other_authority_families_remain_disjoint(self):
        grant = self.transit()
        codec = self.api.DelegatedGatewayReceiverHealthReadTransitGrantCodec()
        self.check_closed_raw_codec(codec, grant)
        for change in ({"audience": "gateway:workspace-1:other"}, {"gateway_node_id": "gateway-1"},
                       {"runtime_id": "runtime-1"}, {"profile": "gateway-node-health-read-transit-grant.v1"},
                       {"authority_context": {"graph_revision": "old"}},
                       {"gateway_target": grant.gateway_target.descriptor() | {"transit_socket": "relay"}}):
            self.refusal(lambda: codec.decode(grant.descriptor() | change))
        old = historical_transit.NodeHealthTransitTests()
        candidates = (old.transit(), old.grant(), self.grant(), historical_command.NodeControlTransitTests().grant(),
                      historical_surface.NodeControlSurfaceReadAuthorityTests().grant(), historical_surface.NodeControlSurfaceReadAuthorityTests().gateway_grant(),
                      self.historical_command_grant())
        for value in candidates:
            self.assertFalse(self.admit(value).is_accepted)
            self.refusal(lambda: codec.decode(value.descriptor()))
        for other in (core.DelegatedGatewayNodeHealthReadTransitGrantCodec(),
                      core.DelegatedWorkloadNodeHealthReadGrantCodec(), core.DelegatedGatewayNodeControlTransitGrantCodec(),
                      core.DelegatedWorkloadNodeControlGrantCodec(),
                      core.DelegatedWorkloadNodeControlSurfaceReadGrantCodec(), core.DelegatedGatewayProbeGrantCodec(),
                      self.reads.DelegatedWorkloadReceiverHealthReadGrantCodec()):
            with self.assertRaises(ValueError):
                other.decode(grant.descriptor())
        self.assertFalse(old.admit(grant).is_accepted)
        self.assertFalse(self.verify(grant).is_accepted)
        fresh = self.request(request_id="health-read-2")
        self.assertFalse(self.admit(grant, fresh).is_accepted)
        self.assertTrue(self.admit(self.transit(fresh), fresh).is_accepted)

    def test_public_exports_have_one_canonical_owner(self):
        self.check_exports(self.api, (
            "GatewayReceiverHealthReadTransitContractError", "DelegatedGatewayReceiverHealthReadTransitGrantProfile",
            "GatewayReceiverHealthReadTransitGrantDigest", "DelegatedGatewayReceiverHealthReadTransitGrant",
            "DelegatedGatewayReceiverHealthReadTransitGrantCodec", "GatewayReceiverHealthReadTransitGrantVerificationCode",
            "GatewayReceiverHealthReadTransitGrantVerificationResult", "verify_gateway_receiver_health_read_transit_grant"))


if __name__ == "__main__":
    unittest.main()
