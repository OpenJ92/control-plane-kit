"""Receiver command laws over the existing variable and canonical-number algebra."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
import importlib
import importlib.util
import json
import struct
import unittest

import rfc8785
import control_plane_kit_core as core
from tests import test_node_control as historical
from tests import test_node_control_canonical_wire as canonical
from tests import test_node_control_result_variants as outcomes
from tests import test_node_control_transit as historical_transit
from tests import test_node_control_surface_read_authority as historical_surface
from tests import test_receiver_health_reads as health
from tests import test_receiver_control_surface_reads as surface
from tests.test_receiver_control_surface_reads import trim_to_bound


class ReceiverNodeControlFixtures:
    module_name = "receiver_node_control"

    def load_api(self, name):
        qualified = "control_plane_kit_core." + name
        self.assertIsNotNone(importlib.util.find_spec(qualified), name + " successor contract is missing")
        return importlib.import_module(qualified)

    def setUp(self):
        self.api = self.load_api(self.module_name)
        self.control = self.load_api("receiver_node_control")

    def reference(self, role, value):
        return core.NodeControlGraphReference(role, value)

    def target(self):
        role = core.NodeControlGraphReferenceRole
        return core.NodeControlReceiverTarget(self.reference(role.WORKSPACE, "workspace-1"),
            self.reference(role.RUNTIME, "runtime-1"), self.reference(role.NODE, "worker"),
            self.reference(role.PROVIDER_SOCKET, "control"), "a"*32)

    def context(self, name="A"):
        return core.NodeControlAuthorityContext("graph-" + name, "projection-" + name)

    def variables(self):
        return outcomes.NodeControlResultVariantTests().variables()

    def variable(self):
        return self.variables()[0][0]

    def declaration(self, variable=None):
        variable = self.variable() if variable is None else variable
        return core.WorkloadNodeControlSurfaceDeclaration(core.WorkloadNodeControlSurfaceDescriptor(
            self.target().provider_socket_name, (variable,)))

    def request(self, *, variable=None, state=None, declaration=None, **changes):
        variable = self.variable() if variable is None else variable
        declaration = self.declaration(variable) if declaration is None else declaration
        state = next(s for v, s in self.variables() if v.kind is variable.kind) if state is None else state
        command = variable.contract_for(core.NodeControlOperation.APPLY_COMMAND).command_codec
        values = dict(target=self.target(), authority_context=self.context(), declaration_identity=declaration.identity(),
            variable_name=variable.variable_name, operation=core.NodeControlOperation.APPLY_COMMAND,
            request_id="request-1", idempotency_key="change-1", command_codec=command,
            precondition=core.ControlPlaneTransitionPrecondition(4), payload=core.NodeControlPayload(command, state))
        if changes.get("operation") is core.NodeControlOperation.READ_STATE:
            values.update(command_codec=None, precondition=None, payload=None)
        return self.control.ReceiverNodeControlRequest(**(values | changes))

    def grant(self, request=None, **changes):
        request = self.request() if request is None else request
        values = dict(profile=self.control.DelegatedWorkloadReceiverNodeControlGrantProfile.V2,
            issuer="cpk-server", key_id="workload-key-1", audience="workload:worker:control",
            target=request.target, authority_context=request.authority_context, declaration_identity=request.declaration_identity,
            variable_name=request.variable_name, operation=request.operation, command_codec=request.command_codec,
            request_id=request.request_id, idempotency_key=request.idempotency_key,
            request_digest=request.canonical_digest(), issued_at=100, not_before=100, expires_at=200, jti="grant-1")
        return self.control.DelegatedWorkloadReceiverNodeControlGrant(**(values | changes))

    def verify(self, grant, request=None, **changes):
        request = self.request() if request is None else request
        expected = dict(expected_target=self.target(), expected_declaration=self.declaration(),
            expected_variable_name=self.variable().variable_name, expected_operation=core.NodeControlOperation.APPLY_COMMAND,
            expected_issuer="cpk-server", expected_key_id="workload-key-1", expected_audience="workload:worker:control", now=150)
        return self.control.verify_workload_receiver_node_control_grant(grant, request, **(expected | changes))

    def refusal(self, action):
        with self.assertRaises(self.control.ReceiverNodeControlContractError) as caught:
            action()
        self.assertLessEqual(len(str(caught.exception)), 128)
        self.assertNotIn("private-test-value", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertEqual(vars(caught.exception), {})

    def check_codec(self, codec, value):
        document = codec.encode(value)
        raw = rfc8785.dumps(document)
        self.assertEqual(codec.encode_canonical_bytes(value), raw)
        self.assertEqual(codec.decode(document), value)
        self.assertEqual(codec.decode_canonical_bytes(raw), value)
        for key in document:
            missing = deepcopy(document)
            del missing[key]
            self.refusal(lambda: codec.decode(missing))
        for bad in (None, [], document | {"private": "private-test-value"}):
            self.refusal(lambda: codec.decode(bad))
        field = next(iter(document))
        duplicate = ("{" + json.dumps(field) + ":" + json.dumps(document[field]) + ",").encode() + raw[1:]
        nested = raw.replace(b'"runtime_id":"runtime-1"', b'"runtime_id":"runtime-1","runtime_id":"runtime-1"')
        cases = [b"", b"\xff", b"{", b"[]", b"NaN", b"Infinity", b"["*1100+b"0"+b"]"*1100,
                 b" "+raw, raw+b"{}", json.dumps(document, indent=1).encode(), duplicate, bytearray(raw), raw.decode()]
        if nested != raw:
            cases.append(nested)
        for bad in cases:
            self.refusal(lambda: codec.decode_canonical_bytes(bad))
        self.refusal(lambda: codec.encode(object()))

    def check_exports(self, module, names):
        self.assertEqual(set(module.__all__), set(names))
        for name in names:
            self.assertIs(getattr(core, name), getattr(module, name))
            self.assertIn(name, core.__all__)

    def request_document(self):
        return {"profile": "workload-node-control-request.v2", "canonicalization": "jcs-rfc8785.v1",
            "target": {"workspace_id": "workspace-1", "runtime_id": "runtime-1", "node_id": "worker",
                       "provider_socket_name": "control", "receiver_id": "a"*32},
            "authority_context": {"authored_graph_id": "graph-A", "realized_projection_id": "projection-A"},
            "declaration_identity": self.declaration().identity().value, "variable_name": "scalar-variable",
            "operation": "apply-command", "request_id": "request-1", "idempotency_key": "change-1",
            "command_codec": "control.replace-scalar.v1", "precondition": {"expected_version": 4},
            "payload": {"codec": "control.replace-scalar.v1", "state": {"kind": "scalar", "value": "target-a"}}}

    def changed_target(self, target, name):
        value = "b"*32 if name == "receiver_id" else replace(getattr(target, name), value="other")
        return replace(target, **{name: value})


class ReceiverNodeControlTests(ReceiverNodeControlFixtures, unittest.TestCase):
    def test_exact_request_and_grant_vectors_and_nominal_digests(self):
        request = self.request()
        expected = self.request_document()
        self.assertEqual(request.descriptor(), expected)
        self.assertEqual(request.canonical_bytes(), rfc8785.dumps(expected))
        self.assertEqual(request.canonical_digest().value, hashlib.sha256(rfc8785.dumps(expected)).hexdigest())
        grant_expected = {k: v for k, v in expected.items() if k not in {"canonicalization", "payload", "precondition"}}
        grant_expected.update(profile="workload-node-control-grant.v2", issuer="cpk-server", key_id="workload-key-1",
            audience="workload:worker:control", request_digest=request.canonical_digest().value,
            issued_at=100, not_before=100, expires_at=200, jti="grant-1")
        grant = self.grant(request)
        self.assertEqual(grant.descriptor(), grant_expected)
        self.assertNotIn("purpose", grant.descriptor())
        self.assertNotIn("canonicalization", grant.descriptor())
        self.assertEqual(grant.canonical_bytes(), rfc8785.dumps(grant_expected))
        self.assertEqual(grant.canonical_digest().value, hashlib.sha256(rfc8785.dumps(grant_expected)).hexdigest())
        self.assertNotEqual(request.canonical_digest(), core.NodeControlRequestDigest(request.canonical_digest().value))
        self.assertNotEqual(grant.canonical_digest(), core.WorkloadNodeControlGrantDigest(grant.canonical_digest().value))
        with self.assertRaises(FrozenInstanceError):
            request.request_id = "other"
        for value in (request, grant):
            self.assertNotIn("request-1", repr(value))
            self.assertNotIn("target-a", repr(value))
        self.check_codec(self.control.ReceiverNodeControlRequestCodec(), request)
        self.check_codec(self.control.DelegatedWorkloadReceiverNodeControlGrantCodec(), grant)

    def test_existing_typed_payload_precondition_read_and_idempotency_laws(self):
        codec = self.control.ReceiverNodeControlRequestCodec()
        for variable, state in self.variables():
            for operation in core.NodeControlOperation:
                request = self.request(variable=variable, state=state, operation=operation)
                self.check_codec(codec, request)
                self.assertTrue(self.verify(self.grant(request), request, expected_declaration=self.declaration(variable),
                    expected_variable_name=variable.variable_name, expected_operation=operation).is_accepted)
                if operation is core.NodeControlOperation.READ_STATE:
                    for field, value in (("command_codec", core.ControlPlaneCommandCodec.REPLACE_SCALAR_V1),
                                         ("precondition", core.ControlPlaneTransitionPrecondition(1)),
                                         ("payload", self.request().payload)):
                        self.refusal(lambda: replace(request, **{field: value}))
                else:
                    for field in ("command_codec", "precondition", "payload"):
                        self.refusal(lambda: replace(request, **{field: None}))
        request = self.request()
        map_request = self.request(variable=self.variables()[1][0])
        self.refusal(lambda: replace(request, payload=map_request.payload))
        for key in ("request_id", "idempotency_key"):
            for bad in ("", "x"*129, "Bearer private-test-value"):
                self.refusal(lambda: replace(request, **{key: bad}))
        for bad in (-1, True, 2**53):
            document = request.descriptor() | {"precondition": {"expected_version": bad}}
            self.refusal(lambda: codec.decode(document))

    def test_original_numeric_observation_and_jcs_ambiguity_rules_survive(self):
        codec = self.control.ReceiverNodeControlRequestCodec()
        fixture = canonical.NodeControlCanonicalWireTests().fixture()
        for vector in fixture["rfc8785_number_vectors"]:
            number = struct.unpack(">d", bytes.fromhex(vector["ieee754_hex"]))[0]
            request = self.request(state=core.ScalarControlState(number))
            self.assertIn(('"value":'+vector["canonical_json"]).encode(), request.canonical_bytes())
            self.assertEqual(codec.decode_canonical_bytes(request.canonical_bytes()), request)
        integer, floating = self.request(state=core.ScalarControlState(1)), self.request(state=core.ScalarControlState(1.0))
        self.assertEqual(integer, floating)
        self.assertEqual(integer.canonical_digest(), floating.canonical_digest())
        raw = self.request(state=core.ScalarControlState(1e20)).canonical_bytes()
        for replacement in (b"100000000000000000001", b"1e20", b"-0", b"1e400"):
            self.refusal(lambda: codec.decode_canonical_bytes(raw.replace(b"100000000000000000000", replacement)))
        self.refusal(lambda: codec.decode_canonical_bytes(integer.canonical_bytes().replace(b'"value":1', b'"value":1.0')))
        for bad in (True, 2**53):
            self.refusal(lambda: codec.decode(integer.descriptor() | {"precondition": {"expected_version": bad}}))
        for state in ({"kind": "scalar", "value": -0.0}, {"kind": "scalar", "value": 2**53}):
            document = integer.descriptor()
            document["payload"]["state"] = state
            self.refusal(lambda: codec.decode(document))

    def test_complete_request_identity_changes_for_every_semantic_input(self):
        request = self.request()
        contexts = [replace(request, authority_context=self.context(n)) for n in "ABC"]
        self.assertEqual(len({r.target for r in contexts}), 1)
        self.assertEqual(len({r.canonical_digest() for r in contexts}), 3)
        for candidate in contexts:
            self.assertTrue(self.verify(self.grant(candidate), candidate).is_accepted)
        changes = [dict(target=self.changed_target(request.target, n)) for n in
                   ("workspace_id", "runtime_id", "node_id", "provider_socket_name", "receiver_id")]
        changes += [dict(authority_context=replace(request.authority_context, **{n: "other"})) for n in
                    ("authored_graph_id", "realized_projection_id")]
        changes += [dict(declaration_identity=core.WorkloadNodeControlSurfaceDeclarationIdentity("f"*64)),
                    dict(variable_name=replace(request.variable_name, value="other")), dict(request_id="other"),
                    dict(idempotency_key="other"), dict(precondition=core.ControlPlaneTransitionPrecondition(5)),
                    dict(payload=core.NodeControlPayload(request.command_codec, core.ScalarControlState("other"))),
                    dict(operation=core.NodeControlOperation.READ_STATE, command_codec=None, precondition=None, payload=None)]
        for change in changes:
            self.assertNotEqual(replace(request, **change).canonical_digest(), request.canonical_digest())

    def test_independent_local_expectations_and_field_ordered_binding(self):
        code = self.control.WorkloadReceiverNodeControlGrantVerificationCode
        request, grant = self.request(), self.grant()
        for field, reason in (("workspace_id", code.WORKSPACE_MISMATCH), ("runtime_id", code.RUNTIME_MISMATCH),
                             ("node_id", code.NODE_MISMATCH), ("provider_socket_name", code.SOCKET_MISMATCH),
                             ("receiver_id", code.RECEIVER_MISMATCH)):
            candidate = replace(request, target=self.changed_target(request.target, field))
            self.assertIs(self.verify(self.grant(candidate), candidate).code, reason)
            self.assertIs(self.verify(replace(grant, target=candidate.target)).code, reason)
        changed_declaration = self.declaration(replace(self.variable(), description="Different declaration."))
        consistent = replace(request, declaration_identity=changed_declaration.identity())
        self.assertIs(self.verify(self.grant(consistent), consistent).code, code.DECLARATION_MISMATCH)
        self.assertIs(self.verify(grant, expected_variable_name=replace(request.variable_name, value="other")).code, code.VARIABLE_MISMATCH)
        self.assertIs(self.verify(grant, expected_operation=core.NodeControlOperation.READ_STATE).code, code.COMMAND_MISMATCH)
        changes = [(dict(issuer="other", key_id="other"), code.ISSUER_MISMATCH),
                   (dict(key_id="other", audience="other"), code.KEY_MISMATCH),
                   (dict(audience="other", not_before=151), code.AUDIENCE_MISMATCH),
                   (dict(not_before=151, authority_context=self.context("B")), code.TEMPORALLY_INVALID),
                   (dict(authority_context=self.context("B"), declaration_identity=changed_declaration.identity()), code.AUTHORITY_CONTEXT_MISMATCH),
                   (dict(declaration_identity=changed_declaration.identity(), variable_name=replace(request.variable_name, value="other")), code.DECLARATION_MISMATCH),
                   (dict(variable_name=replace(request.variable_name, value="other"), operation=core.NodeControlOperation.READ_STATE, command_codec=None), code.VARIABLE_MISMATCH),
                   (dict(operation=core.NodeControlOperation.READ_STATE, command_codec=None, request_id="other"), code.COMMAND_MISMATCH),
                   (dict(request_id="other"), code.REQUEST_MISMATCH), (dict(idempotency_key="other"), code.REQUEST_MISMATCH),
                   (dict(request_digest=self.control.ReceiverNodeControlRequestDigest("f"*64)), code.REQUEST_MISMATCH)]
        for change, reason in changes:
            self.assertIs(self.verify(replace(grant, **change)).code, reason)
        self.assertIs(self.verify(replace(grant, target=self.changed_target(grant.target, "workspace_id")),
            expected_target=self.changed_target(request.target, "node_id")).code, code.WORKSPACE_MISMATCH)
        self.assertEqual(self.verify(grant).descriptor(), {"accepted": True, "code": None})
        result_type = self.control.WorkloadReceiverNodeControlGrantVerificationResult
        for args in ((True, code.REQUEST_MISMATCH), (False, None), (1, None), (False, "request-mismatch")):
            self.refusal(lambda: result_type(*args))

    def test_missing_forged_nominal_and_local_inputs_fail_categorically(self):
        grant = self.grant()
        code = self.control.WorkloadReceiverNodeControlGrantVerificationCode
        fields = ("profile", "issuer", "key_id", "audience", "target", "authority_context", "declaration_identity",
                  "variable_name", "operation", "command_codec", "request_id", "idempotency_key", "request_digest",
                  "issued_at", "not_before", "expires_at", "jti")
        for name in fields:
            broken = deepcopy(grant)
            object.__delattr__(broken, name)
            self.assertIs(self.verify(broken).code, code.GRANT_INVALID)
            self.refusal(lambda: self.control.DelegatedWorkloadReceiverNodeControlGrantCodec().encode(broken))
        self.assertIs(self.verify(object()).code, code.GRANT_TYPE_MISMATCH)
        class Text(str):
            pass
        for change in (dict(expected_target=object()), dict(expected_declaration=object()), dict(expected_variable_name="scalar-variable"),
                       dict(expected_operation="read-state"), dict(expected_key_id=Text("key")), dict(now=True),
                       dict(expected_issuer="Bearer private-test-value"), dict(expected_audience="https://private.invalid")):
            self.refusal(lambda: self.verify(grant, **change))
        for field, value in (("profile", "workload-node-control-grant.v2"), ("request_digest", core.NodeControlRequestDigest("a"*64)),
                             ("issued_at", True), ("target", object())):
            broken = deepcopy(grant)
            object.__setattr__(broken, field, value)
            self.assertIs(self.verify(broken).code, code.GRANT_INVALID)
        request = deepcopy(self.request())
        object.__delattr__(request, "authority_context")
        self.refusal(lambda: self.verify(grant, request))
        for digest in ("a"*63, "a"*65, "A"*64, Text("a"*64), True):
            self.refusal(lambda: self.control.ReceiverNodeControlRequestDigest(digest))
            self.refusal(lambda: self.control.WorkloadReceiverNodeControlGrantDigest(digest))

    def test_temporal_and_constituent_limits_are_unchanged(self):
        grant = self.grant()
        for now, accepted in ((99, False), (100, True), (199, True), (200, False)):
            self.assertEqual(self.verify(grant, now=now).is_accepted, accepted)
        self.assertTrue(self.verify(replace(grant, issued_at=0, not_before=0, expires_at=300), now=0).is_accepted)
        self.assertTrue(self.verify(replace(grant, expires_at=400), now=399).is_accepted)
        for change in (dict(issued_at=-1), dict(issued_at=True), dict(not_before=99), dict(expires_at=100),
                       dict(expires_at=401), dict(expires_at=2**53), dict(key_id="x"*129), dict(jti="x"*129),
                       dict(issuer="x"*257), dict(audience="x"*257), dict(request_id="x"*129), dict(idempotency_key="x"*129)):
            self.refusal(lambda: replace(grant, **change))
        self.refusal(lambda: replace(grant, operation=core.NodeControlOperation.READ_STATE))
        self.refusal(lambda: replace(grant, command_codec=None))

    def test_reachable_request_and_workload_bounds_include_new_fields(self):
        variable = self.variables()[1][0]
        request = self.request(variable=variable)
        document = request.descriptor()
        document["payload"]["state"]["entries"] = {f"v{i:03d}": "a"*128 for i in range(128)}
        paths = [("payload", "state", "entries", f"v{i:03d}") for i in range(128)]
        exact, overflow = trim_to_bound(document, 16384, paths)
        codec = self.control.ReceiverNodeControlRequestCodec()
        value = codec.decode(exact)
        self.assertEqual(len(value.canonical_bytes()), 16384)
        self.assertEqual(codec.decode_canonical_bytes(value.canonical_bytes()), value)
        self.assertEqual(len(rfc8785.dumps(overflow)), 16385)
        self.refusal(lambda: codec.decode(overflow))
        self.refusal(lambda: codec.decode_canonical_bytes(rfc8785.dumps(overflow)))
        maximum = self.grant().descriptor()
        for name in ("workspace_id", "runtime_id", "node_id", "provider_socket_name"):
            maximum["target"][name] = "a"*128
        for name in maximum["authority_context"]:
            maximum["authority_context"][name] = "a"*128
        for name in ("key_id", "variable_name", "request_id", "idempotency_key", "jti"):
            maximum[name] = "a"*128
        maximum.update(issuer="a"*256, audience="a"*256, issued_at=2**53-301, not_before=2**53-2, expires_at=2**53-1)
        exact, overflow = trim_to_bound(maximum, 2111, [("authority_context", "authored_graph_id"),
            ("authority_context", "realized_projection_id"), ("issuer",), ("request_id",)])
        codec = self.control.DelegatedWorkloadReceiverNodeControlGrantCodec()
        self.refusal(lambda: codec.decode(maximum))
        value = codec.decode(exact)
        self.assertEqual(len(value.canonical_bytes()), core.MAX_DELEGATED_WORKLOAD_NODE_CONTROL_GRANT_BYTES)
        self.assertEqual(codec.decode_canonical_bytes(value.canonical_bytes()), value)
        self.assertEqual(len(rfc8785.dumps(overflow)), 2112)
        self.refusal(lambda: codec.decode(overflow))
        self.refusal(lambda: codec.decode_canonical_bytes(rfc8785.dumps(overflow)))

    def test_legacy_and_foreign_profiles_are_pairwise_disjoint(self):
        request, grant = self.request(), self.grant()
        old = historical.NodeControlContractTests()
        request_codec, grant_codec = self.control.ReceiverNodeControlRequestCodec(), self.control.DelegatedWorkloadReceiverNodeControlGrantCodec()
        self.refusal(lambda: request_codec.decode(old.request().descriptor()))
        self.refusal(lambda: grant_codec.decode(old.grant().descriptor()))
        with self.assertRaises(ValueError):
            core.NodeControlCommandRequestCodec().decode(request.descriptor())
        with self.assertRaises(ValueError):
            core.DelegatedWorkloadNodeControlGrantCodec().decode(grant.descriptor())
        self.assertFalse(core.verify_workload_node_control_grant(grant, old.request(), expected_issuer="cpk-server",
            expected_audience="workload:worker:control", now=150).is_accepted)
        other_families = [old.grant(), historical_transit.NodeControlTransitTests().grant(),
                          historical_surface.NodeControlSurfaceReadAuthorityTests().gateway_grant()]
        for fixture in (health.ReceiverHealthReadTests(), surface.ReceiverControlSurfaceReadTests()):
            fixture.setUp()
            other_families.append(fixture.grant())
            self.assertFalse(fixture.verify(grant).is_accepted)
            self.refusal(lambda: request_codec.decode(fixture.request().descriptor()))
        for other in other_families:
            self.assertFalse(self.verify(other).is_accepted)
            self.refusal(lambda: grant_codec.decode(other.descriptor()))
        for codec, value in ((request_codec, request), (grant_codec, grant)):
            for addition in ({"runtime_id": "runtime-1"}, {"graph_revision": "old"},
                             {"profile": value.profile.value.replace("v2", "v1")}, {"profile": None}):
                self.refusal(lambda: codec.decode(value.descriptor() | addition))
        for addition in ({"purpose": "workload-node-control"}, {"canonicalization": "jcs-rfc8785.v1"}):
            self.refusal(lambda: grant_codec.decode(grant.descriptor() | addition))

    def test_public_exports_are_exact(self):
        self.check_exports(self.control, ("ReceiverNodeControlContractError", "ReceiverNodeControlRequestProfile",
            "ReceiverNodeControlRequestDigest", "ReceiverNodeControlRequest", "ReceiverNodeControlRequestCodec",
            "DelegatedWorkloadReceiverNodeControlGrantProfile", "WorkloadReceiverNodeControlGrantDigest",
            "DelegatedWorkloadReceiverNodeControlGrant", "DelegatedWorkloadReceiverNodeControlGrantCodec",
            "WorkloadReceiverNodeControlGrantVerificationCode", "WorkloadReceiverNodeControlGrantVerificationResult",
            "verify_workload_receiver_node_control_grant"))


if __name__ == "__main__":
    unittest.main()
