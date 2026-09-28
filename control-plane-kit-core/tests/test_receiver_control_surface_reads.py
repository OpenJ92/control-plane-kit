"""Receiver surface-description laws, distinct from health and READ_STATE."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
import importlib
import importlib.util
import json
import unittest

import rfc8785
import control_plane_kit_core as core
from tests import test_node_health_declarations as declarations
from tests import test_node_health_read_authority as historical_health
from tests import test_node_control_surface_read_authority as historical_surface
from tests import test_node_control_transit as historical_command
from tests import test_node_control_surface_read_results as surface_results
from tests import test_receiver_health_reads as receiver_health


def trim_to_bound(document, bound, paths):
    """Independent ASCII fixture arithmetic; never ask the implementation to fit."""
    value = deepcopy(document)
    last = None
    for path in paths:
        excess = len(rfc8785.dumps(value)) - bound
        if excess <= 0:
            break
        parent = value
        for key in path[:-1]:
            parent = parent[key]
        amount = min(excess, len(parent[path[-1]]) - 1)
        if amount:
            parent[path[-1]] = parent[path[-1]][:-amount]
            last = path
    if len(rfc8785.dumps(value)) != bound or last is None:
        raise AssertionError("independent boundary fixture cannot reach its stated cap")
    overflow = deepcopy(value)
    parent = overflow
    for key in last[:-1]:
        parent = parent[key]
    parent[last[-1]] += "x"
    return value, overflow


class ReceiverControlSurfaceFixtures:
    module_name = "receiver_control_surface_reads"

    def load_api(self, name):
        qualified = "control_plane_kit_core." + name
        self.assertIsNotNone(importlib.util.find_spec(qualified), name + " successor contract is missing")
        return importlib.import_module(qualified)

    def setUp(self):
        self.api = self.load_api(self.module_name)
        self.reads = self.load_api("receiver_control_surface_reads")

    def declaration(self):
        return surface_results.NodeControlSurfaceReadResultTests().declaration("alpha", "beta")

    def target(self):
        return core.NodeControlReceiverTarget(*(
            core.NodeControlGraphReference(role, value) for role, value in (
                (core.NodeControlGraphReferenceRole.WORKSPACE, "workspace-1"),
                (core.NodeControlGraphReferenceRole.RUNTIME, "runtime-1"),
                (core.NodeControlGraphReferenceRole.NODE, "router"),
                (core.NodeControlGraphReferenceRole.PROVIDER_SOCKET, "control"))), "a" * 32)

    def context(self, name="A"):
        return core.NodeControlAuthorityContext("graph-" + name, "projection-" + name)

    def request(self, **changes):
        values = dict(target=self.target(), authority_context=self.context(),
                      kind=core.NodeControlSurfaceReadKind.CAPABILITIES,
                      declaration_identity=self.declaration().identity(), request_id="surface-read-1")
        return self.reads.ReceiverControlSurfaceReadRequest(**(values | changes))

    def grant(self, request=None, **changes):
        request = self.request() if request is None else request
        values = dict(profile=self.reads.DelegatedWorkloadReceiverControlSurfaceReadGrantProfile.V2,
                      canonicalization=core.NodeControlCanonicalization.JCS_RFC8785_V1,
                      purpose=core.DelegationKeyPurpose.WORKLOAD_NODE_CONTROL_SURFACE_READ,
                      issuer="cpk-server", key_id="surface-key-1", audience="workload:router:control",
                      target=request.target, authority_context=request.authority_context,
                      kind=request.kind, declaration_identity=request.declaration_identity,
                      request_id=request.request_id, request_digest=request.canonical_digest(),
                      issued_at=100, not_before=100, expires_at=200, jti="surface-grant-1")
        return self.reads.DelegatedWorkloadReceiverControlSurfaceReadGrant(**(values | changes))

    def verify(self, grant, request=None, **changes):
        request = self.request() if request is None else request
        values = dict(expected_target=self.target(), expected_declaration=self.declaration(),
                      expected_kind=core.NodeControlSurfaceReadKind.CAPABILITIES, expected_issuer="cpk-server",
                      expected_key_id="surface-key-1", expected_audience="workload:router:control", now=150)
        return self.reads.verify_workload_receiver_control_surface_read_grant(grant, request, **(values | changes))

    def historical_command_grant(self):
        request = historical_command.NodeControlTransitTests().request()
        return core.DelegatedWorkloadNodeControlGrant(
            issuer="cpk-server", key_id="command-key-1", audience="workload:router:control",
            target=request.target, variable_name=request.variable_name, operation=request.operation,
            command_codec=request.command_codec, request_id=request.request_id,
            idempotency_key=request.idempotency_key, request_digest=request.canonical_digest(),
            issued_at=100, not_before=100, expires_at=200, jti="command-grant-1")

    def refusal(self, action, error=None):
        error = self.reads.ReceiverControlSurfaceReadContractError if error is None else error
        with self.assertRaises(error) as caught:
            action()
        self.assertLessEqual(len(str(caught.exception)), 128)
        self.assertNotIn("private-test-value", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertEqual(vars(caught.exception), {})

    def request_document(self):
        return {"profile": "workload-node-control-surface-read-request.v2", "canonicalization": "jcs-rfc8785.v1",
                "target": {"workspace_id": "workspace-1", "runtime_id": "runtime-1", "node_id": "router",
                           "provider_socket_name": "control", "receiver_id": "a" * 32},
                "authority_context": {"authored_graph_id": "graph-A", "realized_projection_id": "projection-A"},
                "kind": "capabilities", "declaration_identity": self.declaration().identity().value,
                "request_id": "surface-read-1"}

    def maximum_request_documents(self):
        document = self.request_document()
        for name in ("workspace_id", "runtime_id", "node_id", "provider_socket_name"):
            document["target"][name] = "a" * 128
        for name in document["authority_context"]:
            document["authority_context"][name] = "a" * 128
        document["request_id"] = "a" * 128
        positive, overflow = trim_to_bound(document, 951, (
            ("authority_context", "authored_graph_id"),
            ("authority_context", "realized_projection_id"), ("request_id",)))
        return document, positive, overflow

    def check_closed_raw_codec(self, codec, value):
        document = codec.encode(value)
        raw = rfc8785.dumps(document)
        self.assertEqual(codec.encode_canonical_bytes(value), raw)
        self.assertEqual(codec.decode_canonical_bytes(raw), value)
        for key in document:
            missing = deepcopy(document)
            del missing[key]
            with self.subTest(missing=key):
                self.refusal(lambda: codec.decode(missing))
            with self.subTest(wrong_type=key):
                self.refusal(lambda: codec.decode(document | {key: None}))
        for bad in (None, [], document | {"extra": "private-test-value"}):
            self.refusal(lambda: codec.decode(bad))
        field = next(iter(document))
        duplicate = ("{" + json.dumps(field) + ":" + json.dumps(document[field]) + ",").encode() + raw[1:]
        nested_duplicate = raw.replace(b'"runtime_id":"runtime-1"',
                                       b'"runtime_id":"runtime-1","runtime_id":"runtime-1"')
        candidates = [b"", b"\xff", b"{", b"NaN", b"Infinity", b"["*1100+b"0"+b"]"*1100,
                      b" " + raw, json.dumps(document, indent=1).encode(), duplicate,
                      bytearray(raw), raw.decode()]
        if nested_duplicate != raw:
            candidates.append(nested_duplicate)
        for bad in candidates:
            with self.subTest(raw_type=type(bad).__name__):
                self.refusal(lambda: codec.decode_canonical_bytes(bad))
        self.refusal(lambda: codec.encode(object()))

    def check_exports(self, module, names):
        self.assertEqual(set(module.__all__), set(names))
        for name in names:
            self.assertIs(getattr(core, name), getattr(module, name))
            self.assertIn(name, core.__all__)


class ReceiverControlSurfaceReadTests(ReceiverControlSurfaceFixtures, unittest.TestCase):
    def test_exact_request_grant_vectors_and_stable_receiver_across_contexts(self):
        request = self.request()
        expected = self.request_document()
        self.assertEqual(request.descriptor(), expected)
        self.assertEqual(request.canonical_bytes(), rfc8785.dumps(expected))
        self.assertEqual(request.canonical_digest().value, hashlib.sha256(rfc8785.dumps(expected)).hexdigest())
        grant = self.grant(request)
        expected_grant = expected | {"profile": "workload-node-control-surface-read-grant.v2",
            "purpose": "workload-node-control-surface-read", "issuer": "cpk-server", "key_id": "surface-key-1",
            "audience": "workload:router:control", "request_digest": request.canonical_digest().value,
            "issued_at": 100, "not_before": 100, "expires_at": 200, "jti": "surface-grant-1"}
        self.assertEqual(grant.descriptor(), expected_grant)
        self.assertEqual(grant.canonical_bytes(), rfc8785.dumps(expected_grant))
        requests = [self.request(authority_context=self.context(name)) for name in "ABC"]
        self.assertEqual(len({value.target for value in requests}), 1)
        self.assertEqual(len({value.canonical_digest() for value in requests}), 3)
        for value in requests:
            self.assertTrue(self.verify(self.grant(value), value).is_accepted)
        changes = [dict(authority_context=replace(request.authority_context, **{name: "other"}))
                   for name in ("authored_graph_id", "realized_projection_id")]
        changes += [dict(request_id="other"), dict(kind=core.NodeControlSurfaceReadKind.STATUS),
                    dict(declaration_identity=core.WorkloadNodeControlSurfaceDeclarationIdentity("f"*64)),
                    dict(target=replace(request.target, receiver_id="b"*32))]
        changes += [dict(target=replace(request.target, **{name: replace(getattr(request.target, name), value="other")}))
                    for name in ("workspace_id", "runtime_id", "node_id", "provider_socket_name")]
        for change in changes:
            self.assertNotEqual(replace(request, **change).canonical_digest(), request.canonical_digest())
        with self.assertRaises(FrozenInstanceError):
            request.request_id = "other"
        for value in (request, grant):
            self.assertNotIn("surface-read-1", repr(value))
        self.assertNotEqual(request.canonical_digest(), core.NodeControlSurfaceReadRequestDigest(request.canonical_digest().value))

    def test_independent_local_scope_declaration_and_kind_cannot_be_chosen_by_claims(self):
        code = self.reads.WorkloadReceiverControlSurfaceReadGrantVerificationCode
        request = self.request()
        fields = (("workspace_id", code.WORKSPACE_MISMATCH), ("runtime_id", code.RUNTIME_MISMATCH),
                  ("node_id", code.NODE_MISMATCH), ("provider_socket_name", code.SOCKET_MISMATCH))
        for name, reason in fields:
            candidate = replace(request, target=replace(request.target, **{name: replace(getattr(request.target, name), value="other")}))
            self.assertIs(self.verify(self.grant(candidate), candidate).code, reason)
        candidate = replace(request, target=replace(request.target, receiver_id="b"*32))
        self.assertIs(self.verify(self.grant(candidate), candidate).code, code.RECEIVER_MISMATCH)
        for declaration in (declarations.NodeHealthDeclarationTests().declaration("mode"),
                            historical_surface.NodeControlSurfaceReadAuthorityTests().declaration()):
            self.assertIs(self.verify(self.grant(), expected_declaration=declaration).code, code.DECLARATION_MISMATCH)
        self.assertIs(self.verify(self.grant(), expected_kind=core.NodeControlSurfaceReadKind.STATUS).code, code.KIND_MISMATCH)
        for declaration in (self.declaration(), declarations.NodeHealthDeclarationTests().declaration(),
                            declarations.NodeHealthDeclarationTests().declaration("alpha", "beta")):
            for kind in core.NodeControlSurfaceReadKind:
                candidate = replace(request, declaration_identity=declaration.identity(), kind=kind)
                self.assertTrue(self.verify(self.grant(candidate), candidate,
                    expected_declaration=declaration, expected_kind=kind).is_accepted)
        socket_mismatch = replace(self.declaration(), surface=replace(self.declaration().surface,
                                  provider_socket_name=replace(request.target.provider_socket_name, value="other")))
        candidate = replace(request, declaration_identity=socket_mismatch.identity())
        self.assertIs(self.verify(self.grant(candidate), candidate, expected_declaration=socket_mismatch).code,
                      code.DECLARATION_MISMATCH)

    def test_every_binding_and_multi_mismatch_precedence(self):
        code = self.reads.WorkloadReceiverControlSurfaceReadGrantVerificationCode
        request, grant = self.request(), self.grant()
        changes = [(dict(issuer="other", key_id="other"), code.ISSUER_MISMATCH),
                   (dict(key_id="other", audience="other"), code.KEY_MISMATCH),
                   (dict(audience="other", not_before=151), code.AUDIENCE_MISMATCH),
                   (dict(not_before=151, authority_context=self.context("B")), code.TEMPORALLY_INVALID),
                   (dict(authority_context=self.context("B"), kind=core.NodeControlSurfaceReadKind.STATUS), code.AUTHORITY_CONTEXT_MISMATCH),
                   (dict(kind=core.NodeControlSurfaceReadKind.STATUS,
                         declaration_identity=core.WorkloadNodeControlSurfaceDeclarationIdentity("f"*64)), code.KIND_MISMATCH),
                   (dict(declaration_identity=core.WorkloadNodeControlSurfaceDeclarationIdentity("f"*64), request_id="other"), code.DECLARATION_MISMATCH),
                   (dict(request_id="other"), code.REQUEST_MISMATCH),
                   (dict(request_digest=self.reads.ReceiverControlSurfaceReadRequestDigest("f"*64)), code.REQUEST_MISMATCH)]
        for name, reason in (("workspace_id", code.WORKSPACE_MISMATCH), ("runtime_id", code.RUNTIME_MISMATCH),
                             ("node_id", code.NODE_MISMATCH), ("provider_socket_name", code.SOCKET_MISMATCH)):
            changes.append((dict(target=replace(request.target, **{name: replace(getattr(request.target, name), value="other")})), reason))
        changes.append((dict(target=replace(request.target, receiver_id="b"*32), authority_context=self.context("B")), code.RECEIVER_MISMATCH))
        for change, reason in changes:
            with self.subTest(change=tuple(change)):
                self.assertIs(self.verify(replace(grant, **change), request).code, reason)
        local = replace(self.target(), workspace_id=replace(self.target().workspace_id, value="other"))
        self.assertIs(self.verify(replace(grant, authority_context=self.context("B")), expected_target=local).code, code.WORKSPACE_MISMATCH)
        self.assertEqual(self.verify(grant).descriptor(), {"accepted": True, "code": None})
        result = self.reads.WorkloadReceiverControlSurfaceReadGrantVerificationResult
        for args in ((True, code.REQUEST_MISMATCH), (False, None), (1, None), (False, "request-mismatch")):
            self.refusal(lambda: result(*args))

    def test_forged_or_missing_grant_fields_are_categorical_and_purpose_first(self):
        code = self.reads.WorkloadReceiverControlSurfaceReadGrantVerificationCode
        grant = self.grant()
        fields = ("profile", "canonicalization", "purpose", "issuer", "key_id", "audience", "target",
                  "authority_context", "kind", "declaration_identity", "request_id", "request_digest",
                  "issued_at", "not_before", "expires_at", "jti")
        for name in fields:
            forged = deepcopy(grant)
            object.__delattr__(forged, name)
            with self.subTest(missing=name):
                self.assertIs(self.verify(forged).code, code.GRANT_INVALID)
        forged = deepcopy(grant)
        object.__delattr__(forged, "issuer")
        object.__setattr__(forged, "purpose", core.DelegationKeyPurpose.WORKLOAD_NODE_CONTROL)
        self.assertIs(self.verify(forged).code, code.PURPOSE_MISMATCH)
        for name, value in (("issuer", "Bearer private-test-value"), ("issued_at", True),
                            ("profile", "workload-node-control-surface-read-grant.v2"), ("target", object())):
            forged = deepcopy(grant)
            object.__setattr__(forged, name, value)
            self.assertIs(self.verify(forged).code, code.GRANT_INVALID)
            self.refusal(lambda: self.reads.DelegatedWorkloadReceiverControlSurfaceReadGrantCodec().encode(forged))
        self.assertIs(self.verify(object()).code, code.GRANT_TYPE_MISMATCH)

    def test_malformed_local_values_raise_without_erasing_semantic_refusals(self):
        class Text(str):
            pass
        for changes in (dict(expected_target=object()), dict(expected_declaration=object()),
                        dict(expected_kind="capabilities"), dict(now=True),
                        dict(expected_issuer="Bearer private-test-value"), dict(expected_key_id=Text("key")),
                        dict(expected_audience="https://private.invalid")):
            self.refusal(lambda: self.verify(self.grant(), **changes))
        request = deepcopy(self.request())
        object.__delattr__(request, "authority_context")
        self.refusal(lambda: self.verify(self.grant(), request))
        target = deepcopy(self.target())
        object.__setattr__(target, "receiver_id", Text("a"*32))
        self.refusal(lambda: self.verify(self.grant(), expected_target=target))
        declaration = deepcopy(self.declaration())
        object.__setattr__(declaration.surface, "health_reads", [core.NodeHealthReadKind.READINESS])
        self.refusal(lambda: self.verify(self.grant(), expected_declaration=declaration))

    def test_temporal_constituent_nominal_and_private_material_boundaries(self):
        class Text(str):
            pass
        grant = self.grant()
        for now, accepted in ((99, False), (100, True), (199, True), (200, False)):
            self.assertEqual(self.verify(grant, now=now).is_accepted, accepted)
        self.assertTrue(self.verify(replace(grant, expires_at=400), now=399).is_accepted)
        self.assertTrue(self.verify(replace(grant, issued_at=0, not_before=0, expires_at=300), now=0).is_accepted)
        for changes in (dict(issued_at=True), dict(not_before=99), dict(expires_at=100),
                        dict(expires_at=401), dict(expires_at=2**53), dict(issued_at=-1),
                        dict(purpose=core.DelegationKeyPurpose.WORKLOAD_NODE_CONTROL),
                        dict(key_id="x"*129), dict(jti="x"*129), dict(issuer="x"*257),
                        dict(audience="x"*257), dict(request_id="x"*129),
                        dict(request_digest=core.NodeControlSurfaceReadRequestDigest("a"*64)),
                        dict(issuer=Text("issuer"))):
            self.refusal(lambda: replace(grant, **changes))
        for value in ("token=private-test-value", "https://private.invalid", "127.0.0.1:8000"):
            self.refusal(lambda: replace(grant, issuer=value))
        for value in ("a"*63, "a"*65, "A"*64, True, Text("a"*64)):
            self.refusal(lambda: self.reads.ReceiverControlSurfaceReadRequestDigest(value))
        bad_identity = deepcopy(self.request().declaration_identity)
        object.__setattr__(bad_identity, "value", Text("a"*64))
        self.refusal(lambda: replace(self.request(), declaration_identity=bad_identity))
        for changes in (dict(target=historical_health.NodeHealthReadAuthorityTests().request().target),
                        dict(authority_context=object()), dict(kind="capabilities"), dict(request_id=Text("id"))):
            self.refusal(lambda: replace(self.request(), **changes))

    def test_independent_reachable_aggregate_caps_and_first_overflow(self):
        request_codec = self.reads.ReceiverControlSurfaceReadRequestCodec()
        maximal, exact, overflow = self.maximum_request_documents()
        self.assertGreater(len(rfc8785.dumps(maximal)), 951)
        self.refusal(lambda: request_codec.decode(maximal))
        request = request_codec.decode(exact)
        self.assertEqual(len(request.canonical_bytes()), core.MAX_NODE_CONTROL_SURFACE_READ_REQUEST_BYTES)
        self.assertEqual(request_codec.decode_canonical_bytes(rfc8785.dumps(exact)), request)
        self.assertEqual(len(rfc8785.dumps(overflow)), 952)
        self.refusal(lambda: request_codec.decode(overflow))
        self.refusal(lambda: request_codec.decode_canonical_bytes(rfc8785.dumps(overflow)))
        grant_document = exact | {"profile": "workload-node-control-surface-read-grant.v2",
            "purpose": "workload-node-control-surface-read", "issuer": "a"*256, "key_id": "a"*128,
            "audience": "a"*256, "request_digest": "b"*64, "issued_at": 2**53-301,
            "not_before": 2**53-300, "expires_at": 2**53-1, "jti": "a"*128}
        codec = self.reads.DelegatedWorkloadReceiverControlSurfaceReadGrantCodec()
        self.assertEqual(len(rfc8785.dumps(grant_document)), 1984)
        grant = codec.decode(grant_document)
        self.assertEqual(len(grant.canonical_bytes()), core.MAX_DELEGATED_WORKLOAD_NODE_CONTROL_SURFACE_READ_GRANT_BYTES)
        self.assertEqual(codec.decode_canonical_bytes(grant.canonical_bytes()), grant)
        bad = grant_document | {"authority_context": overflow["authority_context"]}
        self.assertEqual(len(rfc8785.dumps(bad)), 1985)
        self.refusal(lambda: codec.decode(bad))
        self.refusal(lambda: codec.decode_canonical_bytes(rfc8785.dumps(bad)))

    def test_closed_raw_profiles_are_disjoint_from_historical_and_other_authority(self):
        request, grant = self.request(), self.grant()
        request_codec = self.reads.ReceiverControlSurfaceReadRequestCodec()
        grant_codec = self.reads.DelegatedWorkloadReceiverControlSurfaceReadGrantCodec()
        self.check_closed_raw_codec(request_codec, request)
        self.check_closed_raw_codec(grant_codec, grant)
        for value, codec in ((request, request_codec), (grant, grant_codec)):
            for change in ({"runtime_id": "runtime-1"}, {"profile": value.profile.value.replace("v2", "v1")},
                           {"authority_context": {"graph_revision": "old"}},
                           {"target": value.target.descriptor() | {"graph_revision": "old"}}):
                self.refusal(lambda: codec.decode(value.descriptor() | change))
        old = historical_surface.NodeControlSurfaceReadAuthorityTests()
        self.refusal(lambda: request_codec.decode(old.request().descriptor()))
        with self.assertRaises(ValueError):
            core.NodeControlSurfaceReadRequestCodec().decode(request.descriptor())
        health = receiver_health.ReceiverHealthReadTests()
        health.setUp()
        candidates = (old.grant(), historical_health.NodeHealthReadAuthorityTests().grant(),
                      health.grant(),
                      historical_surface.NodeControlSurfaceReadAuthorityTests().gateway_grant(), historical_command.NodeControlTransitTests().grant(),
                      self.historical_command_grant())
        for value in candidates:
            self.assertFalse(self.verify(value).is_accepted)
            self.refusal(lambda: grant_codec.decode(value.descriptor()))
        self.assertFalse(core.verify_workload_node_control_surface_read_grant(grant, old.request(),
            expected_issuer="cpk-server", expected_key_id="surface-read-key-1",
            expected_audience="workload:router:control", now=150).is_accepted)
        self.assertFalse(health.verify(grant).is_accepted)
        self.refusal(lambda: request_codec.decode(health.request().descriptor()))
        with self.assertRaises(ValueError):
            core.ReceiverHealthReadRequestCodec().decode(request.descriptor())
        for codec in (core.DelegatedWorkloadNodeHealthReadGrantCodec(),
                      core.DelegatedWorkloadReceiverHealthReadGrantCodec(),
                      core.DelegatedWorkloadNodeControlSurfaceReadGrantCodec(),
                      core.DelegatedWorkloadNodeControlGrantCodec(),
                      core.DelegatedGatewayNodeControlTransitGrantCodec(), core.DelegatedGatewayProbeGrantCodec()):
            with self.assertRaises(ValueError):
                codec.decode(grant.descriptor())

    def test_public_exports_and_distinct_successor_digests(self):
        self.check_exports(self.reads, (
            "ReceiverControlSurfaceReadContractError", "ReceiverControlSurfaceReadRequestProfile", "ReceiverControlSurfaceReadRequestDigest",
            "ReceiverControlSurfaceReadRequest", "ReceiverControlSurfaceReadRequestCodec", "DelegatedWorkloadReceiverControlSurfaceReadGrantProfile",
            "DelegatedWorkloadReceiverControlSurfaceReadGrant", "DelegatedWorkloadReceiverControlSurfaceReadGrantCodec",
            "WorkloadReceiverControlSurfaceReadGrantVerificationCode", "WorkloadReceiverControlSurfaceReadGrantVerificationResult",
            "verify_workload_receiver_control_surface_read_grant"))


if __name__ == "__main__":
    unittest.main()
