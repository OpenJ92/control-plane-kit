"""Exact command transit scope, independent gateway identity and refusal order."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import unittest

import rfc8785
import control_plane_kit_core as core
from tests import test_node_control_transit as historical
from tests import test_receiver_health_transit as health
from tests.test_receiver_node_control import ReceiverNodeControlFixtures, trim_to_bound


class ReceiverNodeControlTransitTests(ReceiverNodeControlFixtures, unittest.TestCase):
    module_name = "receiver_node_control_transit"

    def gateway(self):
        target = self.target()
        return replace(target, node_id=replace(target.node_id, value="gateway"),
            provider_socket_name=replace(target.provider_socket_name, value="gateway-control"), receiver_id="b"*32)

    def grant(self, request=None, **changes):
        request = self.request() if request is None else request
        values = dict(profile=self.api.DelegatedGatewayReceiverNodeControlTransitGrantProfile.V2,
            canonicalization=core.NodeControlCanonicalization.JCS_RFC8785_V1,
            purpose=core.DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT,
            issuer="cpk-server", key_id="transit-key-1", attempt_id="attempt-1", gateway_target=self.gateway(),
            target=request.target, authority_context=request.authority_context, declaration_identity=request.declaration_identity,
            variable_name=request.variable_name, operation=request.operation, command_codec=request.command_codec,
            request_id=request.request_id, idempotency_key=request.idempotency_key, request_digest=request.canonical_digest(),
            issued_at=100, not_before=100, expires_at=200, jti="transit-1")
        return self.api.DelegatedGatewayReceiverNodeControlTransitGrant(**(values | changes))

    def verify(self, grant, request=None, **changes):
        request = self.request() if request is None else request
        values = dict(expected_issuer="cpk-server", expected_key_id="transit-key-1", expected_attempt_id="attempt-1",
            expected_gateway_target=self.gateway(), expected_target=self.target(), expected_declaration=self.declaration(),
            expected_variable_name=self.variable().variable_name, expected_operation=core.NodeControlOperation.APPLY_COMMAND, now=150)
        return self.api.verify_gateway_receiver_node_control_transit_grant(grant, request, **(values | changes))

    def test_exact_transit_vector_and_contexts_do_not_change_installed_receivers(self):
        request, grant = self.request(), self.grant()
        expected = {k: v for k, v in self.request_document().items() if k not in {"payload", "precondition"}}
        expected.update(profile="gateway-node-control-transit-grant.v2", purpose="gateway-node-control-transit",
            issuer="cpk-server", key_id="transit-key-1", attempt_id="attempt-1", audience="gateway:workspace-1:gateway",
            gateway_target={"workspace_id": "workspace-1", "runtime_id": "runtime-1", "node_id": "gateway",
                            "provider_socket_name": "gateway-control", "receiver_id": "b"*32},
            request_digest=request.canonical_digest().value, issued_at=100, not_before=100, expires_at=200, jti="transit-1")
        self.assertEqual(grant.descriptor(), expected)
        self.assertEqual(grant.canonical_bytes(), rfc8785.dumps(expected))
        self.assertEqual(grant.canonical_digest().value, hashlib.sha256(rfc8785.dumps(expected)).hexdigest())
        self.assertNotEqual(grant.canonical_digest(), core.GatewayNodeControlTransitGrantDigest(grant.canonical_digest().value))
        self.assertNotEqual(grant.gateway_target.provider_socket_name, grant.target.provider_socket_name)
        self.check_codec(self.api.DelegatedGatewayReceiverNodeControlTransitGrantCodec(), grant)
        for name in "ABC":
            request = self.request(authority_context=self.context(name))
            self.assertTrue(self.verify(self.grant(request), request).is_accepted)
        self.assertNotIn("request-1", repr(grant))

    def test_independent_gateway_and_local_scope_cannot_be_selected_by_claims(self):
        code = self.api.GatewayReceiverNodeControlTransitGrantVerificationCode
        request = self.request()
        for name in ("workspace_id", "runtime_id", "node_id", "provider_socket_name", "receiver_id"):
            changed = self.changed_target(self.gateway(), name)
            if name == "receiver_id":
                changed = replace(changed, receiver_id="c"*32)
            self.assertNotEqual(changed, self.gateway())
            if name in ("workspace_id", "runtime_id"):
                self.refusal(lambda: self.grant(gateway_target=changed))
                self.refusal(lambda: self.verify(self.grant(), expected_gateway_target=changed))
            else:
                self.assertIs(self.verify(self.grant(gateway_target=changed)).code, code.GATEWAY_MISMATCH)
        for name, reason in (("workspace_id", code.WORKSPACE_MISMATCH), ("runtime_id", code.RUNTIME_MISMATCH),
                             ("node_id", code.NODE_MISMATCH), ("provider_socket_name", code.SOCKET_MISMATCH),
                             ("receiver_id", code.RECEIVER_MISMATCH)):
            candidate = replace(request, target=self.changed_target(request.target, name))
            gateway = self.gateway()
            if name in ("workspace_id", "runtime_id"):
                gateway = replace(gateway, **{name: getattr(candidate.target, name)})
            self.assertIs(self.verify(self.grant(candidate, gateway_target=gateway), candidate).code, reason)
        different = self.declaration(replace(self.variable(), description="Different declaration."))
        candidate = replace(request, declaration_identity=different.identity())
        self.assertIs(self.verify(self.grant(candidate), candidate).code, code.DECLARATION_MISMATCH)
        self.assertIs(self.verify(self.grant(), expected_variable_name=replace(request.variable_name, value="other")).code, code.VARIABLE_MISMATCH)
        self.assertIs(self.verify(self.grant(), expected_operation=core.NodeControlOperation.READ_STATE).code, code.COMMAND_MISMATCH)

    def test_historical_workspace_before_gateway_order_and_all_request_bindings(self):
        code = self.api.GatewayReceiverNodeControlTransitGrantVerificationCode
        request, grant = self.request(), self.grant()
        cases = [(dict(issuer="other", key_id="other"), code.ISSUER_MISMATCH),
                 (dict(key_id="other", not_before=151), code.KEY_MISMATCH),
                 (dict(not_before=151, attempt_id="other"), code.TEMPORALLY_INVALID),
                 (dict(attempt_id="other", gateway_target=self.changed_target(self.gateway(), "node_id")), code.ATTEMPT_MISMATCH),
                 (dict(gateway_target=self.changed_target(self.gateway(), "node_id"), target=self.changed_target(request.target, "node_id")), code.GATEWAY_MISMATCH),
                 (dict(target=self.changed_target(request.target, "node_id"), authority_context=self.context("B")), code.NODE_MISMATCH),
                 (dict(authority_context=self.context("B"), declaration_identity=core.WorkloadNodeControlSurfaceDeclarationIdentity("f"*64)), code.AUTHORITY_CONTEXT_MISMATCH),
                 (dict(declaration_identity=core.WorkloadNodeControlSurfaceDeclarationIdentity("f"*64), variable_name=replace(request.variable_name, value="other")), code.DECLARATION_MISMATCH),
                 (dict(variable_name=replace(request.variable_name, value="other"), operation=core.NodeControlOperation.READ_STATE, command_codec=None), code.VARIABLE_MISMATCH),
                 (dict(operation=core.NodeControlOperation.READ_STATE, command_codec=None, request_id="other"), code.COMMAND_MISMATCH),
                 (dict(request_id="other"), code.REQUEST_MISMATCH), (dict(idempotency_key="other"), code.REQUEST_MISMATCH),
                 (dict(request_digest=self.control.ReceiverNodeControlRequestDigest("f"*64)), code.REQUEST_MISMATCH)]
        for change, reason in cases:
            self.assertIs(self.verify(replace(grant, **change)).code, reason)
        for field, reason in (("workspace_id", code.WORKSPACE_MISMATCH), ("runtime_id", code.RUNTIME_MISMATCH)):
            foreign = self.changed_target(request.target, field)
            gateway = replace(self.changed_target(self.gateway(), "node_id"), **{field: getattr(foreign, field)})
            self.assertIs(self.verify(replace(grant, target=foreign, gateway_target=gateway)).code, reason)
        self.assertIs(self.verify(replace(grant, target=self.changed_target(request.target, "workspace_id"),
            gateway_target=self.changed_target(self.gateway(), "workspace_id")),
            expected_gateway_target=self.changed_target(self.gateway(), "node_id")).code, code.WORKSPACE_MISMATCH)
        for now, accepted in ((99, False), (100, True), (199, True), (200, False)):
            self.assertEqual(self.verify(grant, now=now).is_accepted, accepted)
        self.assertTrue(self.verify(replace(grant, expires_at=400), now=399).is_accepted)

    def test_forged_missing_and_wrong_purpose_inputs_preserve_categorical_order(self):
        code = self.api.GatewayReceiverNodeControlTransitGrantVerificationCode
        grant = self.grant()
        fields = ("profile", "canonicalization", "purpose", "issuer", "key_id", "attempt_id", "gateway_target",
            "target", "authority_context", "declaration_identity", "variable_name", "operation", "command_codec",
            "request_id", "idempotency_key", "request_digest", "issued_at", "not_before", "expires_at", "jti")
        for name in fields:
            broken = deepcopy(grant)
            object.__delattr__(broken, name)
            self.assertIs(self.verify(broken).code, code.GRANT_INVALID)
        broken = deepcopy(grant)
        object.__delattr__(broken, "issuer")
        object.__setattr__(broken, "purpose", core.DelegationKeyPurpose.WORKLOAD_NODE_CONTROL)
        self.assertIs(self.verify(broken).code, code.PURPOSE_MISMATCH)
        for change in (dict(expected_gateway_target=object()), dict(expected_attempt_id="Bearer private-test-value"),
                       dict(expected_declaration=object()), dict(expected_variable_name="bad"), dict(now=True)):
            self.refusal(lambda: self.verify(grant, **change))
        for change in (dict(expires_at=401), dict(not_before=99), dict(expires_at=100), dict(issued_at=True),
                       dict(expires_at=2**53), dict(attempt_id="x"*129), dict(purpose=core.DelegationKeyPurpose.WORKLOAD_NODE_CONTROL)):
            self.refusal(lambda: replace(grant, **change))
        self.assertIs(self.verify(object()).code, code.GRANT_TYPE_MISMATCH)
        result = self.api.GatewayReceiverNodeControlTransitGrantVerificationResult
        for args in ((False, None), (True, code.REQUEST_MISMATCH), (1, None), (False, "request-mismatch")):
            self.refusal(lambda: result(*args))

    def test_reachable_transit_cap_audience_and_first_overflow(self):
        document = self.grant().descriptor()
        for field in ("target", "gateway_target"):
            for name in ("workspace_id", "runtime_id", "node_id", "provider_socket_name"):
                document[field][name] = "a"*128
        document["audience"] = "gateway:" + "a"*128 + ":" + "a"*128
        for name in document["authority_context"]:
            document["authority_context"][name] = "a"*128
        for name in ("key_id", "attempt_id", "variable_name", "request_id", "idempotency_key", "jti"):
            document[name] = "a"*128
        document.update(issuer="a"*256, issued_at=2**53-301, not_before=2**53-2, expires_at=2**53-1)
        exact, overflow = trim_to_bound(document, 2834, [("authority_context", "authored_graph_id"),
            ("authority_context", "realized_projection_id"), ("issuer",), ("request_id",), ("jti",)])
        codec = self.api.DelegatedGatewayReceiverNodeControlTransitGrantCodec()
        self.refusal(lambda: codec.decode(document))
        grant = codec.decode(exact)
        self.assertEqual(len(grant.audience), core.MAX_GATEWAY_NODE_CONTROL_TRANSIT_AUDIENCE_BYTES)
        self.assertEqual(len(grant.canonical_bytes()), core.MAX_DELEGATED_GATEWAY_NODE_CONTROL_TRANSIT_GRANT_BYTES)
        self.assertEqual(codec.decode_canonical_bytes(grant.canonical_bytes()), grant)
        self.assertEqual(len(rfc8785.dumps(overflow)), 2835)
        self.refusal(lambda: codec.decode(overflow))
        self.refusal(lambda: codec.decode_canonical_bytes(rfc8785.dumps(overflow)))
        self.refusal(lambda: codec.decode(exact | {"audience": "gateway:other:other"}))

    def test_transit_is_disjoint_from_old_and_other_purpose_languages(self):
        codec = self.api.DelegatedGatewayReceiverNodeControlTransitGrantCodec()
        grant = self.grant()
        old = historical.NodeControlTransitTests()
        self.refusal(lambda: codec.decode(old.grant().descriptor()))
        with self.assertRaises(ValueError):
            core.DelegatedGatewayNodeControlTransitGrantCodec().decode(grant.descriptor())
        self.assertFalse(old.verify(grant).is_accepted)
        fixture = health.ReceiverHealthTransitTests()
        fixture.setUp()
        self.assertFalse(self.verify(fixture.transit()).is_accepted)
        self.assertFalse(fixture.admit(grant).is_accepted)
        self.refusal(lambda: codec.decode(fixture.transit().descriptor()))
        workload = ReceiverNodeControlFixtures.grant(self)
        self.assertFalse(self.verify(workload).is_accepted)
        self.assertFalse(ReceiverNodeControlFixtures.verify(self, grant).is_accepted)
        self.refusal(lambda: codec.decode(workload.descriptor()))
        for extra in ({"workspace_id": "workspace-1"}, {"graph_revision": "old"}, {"gateway_node_id": "gateway"},
                      {"runtime_id": "runtime-1"}, {"profile": "gateway-node-control-transit-grant.v1"}):
            self.refusal(lambda: codec.decode(grant.descriptor() | extra))

    def test_public_exports_are_exact(self):
        self.check_exports(self.api, ("GatewayReceiverNodeControlTransitContractError",
            "DelegatedGatewayReceiverNodeControlTransitGrantProfile", "GatewayReceiverNodeControlTransitGrantDigest",
            "DelegatedGatewayReceiverNodeControlTransitGrant", "DelegatedGatewayReceiverNodeControlTransitGrantCodec",
            "GatewayReceiverNodeControlTransitGrantVerificationCode", "GatewayReceiverNodeControlTransitGrantVerificationResult",
            "verify_gateway_receiver_node_control_transit_grant"))


if __name__ == "__main__":
    unittest.main()
