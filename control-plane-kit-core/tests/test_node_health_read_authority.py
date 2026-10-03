"""Public health authority laws; crypto and runtime admission live elsewhere."""
from dataclasses import replace
import hashlib
import unittest

import rfc8785
import control_plane_kit_core as core
from tests import test_node_health_declarations as declarations
from tests import test_node_control_surface_read_authority as static
from tests import test_node_control_transit as transit


class HealthReadFixtures:
    def api(self, name):
        value = getattr(core, name, None)
        self.assertIsNotNone(value, name + " is missing")
        return value

    def declaration(self):
        return declarations.NodeHealthDeclarationTests().declaration()

    def request(self, **changes):
        request_type = self.api("NodeHealthReadRequest")
        values = dict(target=static.NodeControlSurfaceReadAuthorityTests().target(),
                      runtime_id=core.NodeControlGraphReference(core.NodeControlGraphReferenceRole.RUNTIME, "runtime-1"),
                      kind=core.NodeHealthReadKind.READINESS,
                      declaration_identity=self.declaration().identity(), request_id="health-read-1")
        values.update(changes)
        return request_type(**values)

    def grant(self, request=None, **changes):
        request = request or self.request()
        values = dict(profile=self.api("DelegatedWorkloadNodeHealthReadGrantProfile").V1,
                      canonicalization=core.NodeControlCanonicalization.JCS_RFC8785_V1,
                      purpose=core.DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ,
                      issuer="cpk-server", key_id="health-key-1", audience="workload:router:control",
                      target=request.target, runtime_id=request.runtime_id, kind=request.kind,
                      declaration_identity=request.declaration_identity, request_id=request.request_id,
                      request_digest=request.canonical_digest(), issued_at=100, not_before=100,
                      expires_at=200, jti="health-grant-1")
        values.update(changes)
        return self.api("DelegatedWorkloadNodeHealthReadGrant")(**values)

    def verify(self, grant, request=None, **changes):
        request = request or self.request()
        values = dict(expected_target=request.target, expected_runtime_id=request.runtime_id,
                      expected_declaration=self.declaration(), expected_kind=request.kind,
                      expected_issuer="cpk-server", expected_key_id="health-key-1",
                      expected_audience="workload:router:control", now=150)
        values.update(changes)
        return self.api("verify_workload_node_health_read_grant")(grant, request, **values)


class NodeHealthReadAuthorityTests(HealthReadFixtures, unittest.TestCase):
    def test_exact_request_vector_roundtrip_and_domain_separation(self):
        request = self.request()
        expected = {
            "profile": "workload-node-health-read-request.v1", "canonicalization": "jcs-rfc8785.v1",
            "target": request.target.descriptor(), "runtime_id": "runtime-1", "kind": "readiness",
            "declaration_identity": self.declaration().identity().value, "request_id": "health-read-1",
        }
        self.assertEqual(request.descriptor(), expected)
        self.assertEqual(request.canonical_bytes(), rfc8785.dumps(expected))
        self.assertEqual(request.canonical_digest().value, hashlib.sha256(rfc8785.dumps(expected)).hexdigest())
        codec = self.api("NodeHealthReadRequestCodec")()
        self.assertEqual(codec.decode(codec.encode(request)), request)
        changes = [dict(kind=core.NodeHealthReadKind.LIVENESS), dict(request_id="health-read-2"),
                   dict(runtime_id=replace(request.runtime_id, value="runtime-2")),
                   dict(declaration_identity=core.WorkloadNodeControlSurfaceDeclarationIdentity("f" * 64))]
        for field in ("workspace_id", "graph_revision", "node_id", "provider_socket_name"):
            changes.append(dict(target=replace(request.target, **{field: replace(getattr(request.target, field), value="other")})))
        for change in changes:
            with self.subTest(change=tuple(change)):
                self.assertNotEqual(replace(request, **change).canonical_digest(), request.canonical_digest())
        for change in ({"profile": "workload-node-control-surface-read-request.v1"}, {"kind": "status"},
                       {"runtime_id": "https://private.invalid"}, {"request_id": True}, {"extra": None}):
            with self.subTest(change=tuple(change)), self.assertRaises(ValueError):
                codec.decode({**expected, **change})
        with self.assertRaises(ValueError):
            replace(request, runtime_id=request.target.node_id)

    def test_local_bindings_are_independent_of_consistent_signed_claims(self):
        request = self.request()
        grant = self.grant(request)
        self.assertTrue(self.verify(grant, request).is_accepted)
        for field in ("workspace_id", "graph_revision", "node_id", "provider_socket_name"):
            local = replace(request.target, **{field: replace(getattr(request.target, field), value="other")})
            with self.subTest(field=field):
                self.assertFalse(self.verify(grant, request, expected_target=local).is_accepted)
        for local in (dict(expected_runtime_id=replace(request.runtime_id, value="other")),
                      dict(expected_declaration=declarations.NodeHealthDeclarationTests().declaration("mode")),
                      dict(expected_kind=core.NodeHealthReadKind.LIVENESS)):
            with self.subTest(local=tuple(local)):
                self.assertFalse(self.verify(grant, request, **local).is_accepted)
        undeclared = replace(self.declaration(), surface=replace(self.declaration().surface, health_reads=(core.NodeHealthReadKind.LIVENESS,)))
        candidate = replace(request, declaration_identity=undeclared.identity())
        self.assertFalse(self.verify(self.grant(candidate), candidate, expected_declaration=undeclared).is_accepted)
        for kwargs in (dict(expected_target=object()), dict(expected_runtime_id=request.target.node_id),
                       dict(expected_kind="readiness"), dict(now=True)):
            with self.subTest(kwargs=tuple(kwargs)), self.assertRaises(ValueError):
                self.verify(grant, request, **kwargs)

    def test_every_grant_binding_and_temporal_boundary(self):
        request = self.request()
        grant = self.grant(request)
        self.assertTrue(self.verify(grant, now=100).is_accepted)
        self.assertFalse(self.verify(grant, now=99).is_accepted)
        self.assertFalse(self.verify(grant, now=200).is_accepted)
        changes = [dict(issuer="other"), dict(key_id="other"), dict(audience="other"),
                   dict(kind=core.NodeHealthReadKind.LIVENESS), dict(request_id="other"),
                   dict(runtime_id=replace(request.runtime_id, value="other")),
                   dict(declaration_identity=core.WorkloadNodeControlSurfaceDeclarationIdentity("f" * 64)),
                   dict(request_digest=self.api("NodeHealthReadRequestDigest")("f" * 64))]
        for field in ("workspace_id", "graph_revision", "node_id", "provider_socket_name"):
            changes.append(dict(target=replace(request.target, **{field: replace(getattr(request.target, field), value="other")})))
        for change in changes:
            with self.subTest(change=tuple(change)):
                self.assertFalse(self.verify(replace(grant, **change), request).is_accepted)
        for change in (dict(issued_at=True), dict(not_before=99), dict(expires_at=100), dict(expires_at=401),
                       dict(purpose=core.DelegationKeyPurpose.WORKLOAD_NODE_CONTROL_SURFACE_READ)):
            with self.subTest(change=tuple(change)), self.assertRaises(ValueError):
                replace(grant, **change)
        self.assertTrue(self.verify(replace(grant, expires_at=400), now=399).is_accepted)

    def test_health_and_existing_authority_families_do_not_substitute(self):
        health = self.grant()
        static_fixture = static.NodeControlSurfaceReadAuthorityTests()
        transit_fixture = transit.NodeControlTransitTests()
        command_request = transit_fixture.request()
        command = core.DelegatedWorkloadNodeControlGrant(
            issuer="cpk-server", key_id="command-key-1", audience="workload:router:control",
            target=command_request.target, variable_name=command_request.variable_name,
            operation=command_request.operation, command_codec=command_request.command_codec,
            request_id=command_request.request_id, idempotency_key=command_request.idempotency_key,
            request_digest=command_request.canonical_digest(), issued_at=100, not_before=100,
            expires_at=200, jti="command-grant-1")
        for candidate in (static_fixture.grant(), static_fixture.gateway_grant(), transit_fixture.grant(), command):
            with self.subTest(candidate=type(candidate).__name__):
                self.assertFalse(self.verify(candidate).is_accepted)
                with self.assertRaises(ValueError):
                    self.api("DelegatedWorkloadNodeHealthReadGrantCodec")().decode(candidate.descriptor())
        self.assertFalse(core.verify_workload_node_control_surface_read_grant(
            health, static_fixture.request(), expected_issuer="cpk-server", expected_key_id="health-key-1",
            expected_audience="workload:router:control", now=150).is_accepted)
        self.assertFalse(core.verify_workload_node_control_grant(
            health, command_request, expected_issuer="cpk-server", expected_audience="workload:router:control", now=150).is_accepted)
        self.assertFalse(transit_fixture.verify(health).is_accepted)
        # Each old codec also refuses the new envelope, before any outer interpreter.
        for codec in (core.DelegatedWorkloadNodeControlSurfaceReadGrantCodec(), core.DelegatedGatewayNodeControlTransitGrantCodec()):
            with self.assertRaises(ValueError):
                codec.decode(health.descriptor())

    def test_reachable_request_grant_bounds_and_strict_redacted_errors(self):
        request = self.request()
        identifier = "a" * 128
        target = core.NodeControlTarget(**{field: replace(getattr(request.target, field), value=identifier)
                                          for field in ("workspace_id", "graph_revision", "node_id", "provider_socket_name")})
        maximum = replace(request, target=target, runtime_id=replace(request.runtime_id, value=identifier), request_id=identifier)
        grant = self.grant(maximum, issuer="a" * 256, audience="a" * 256, key_id=identifier, jti=identifier,
                           issued_at=2**53-301, not_before=2**53-301, expires_at=2**53-1)
        for value, codec, bound in ((maximum, self.api("NodeHealthReadRequestCodec")(), 1083),
                                    (grant, self.api("DelegatedWorkloadNodeHealthReadGrantCodec")(), 2107)):
            self.assertEqual(len(value.canonical_bytes()), bound)
            self.assertEqual(codec.decode(codec.encode(value)), value)
            raw = value.descriptor()
            raw["target"] = ""
            raw["target"] = "x" * (bound + 1 - len(rfc8785.dumps(raw)))
            with self.assertRaisesRegex(ValueError, "aggregate"):
                codec.decode(raw)
            raw = value.descriptor()
            del raw["profile"]
            with self.assertRaises(ValueError):
                codec.decode(raw)
        for value in ("token=private-test-value", "https://private.invalid", "127.0.0.1:8000"):
            with self.assertRaises(ValueError) as caught:
                replace(grant, issuer=value)
            self.assertNotIn(value, str(caught.exception))
            self.assertIsNone(caught.exception.__cause__)
        for field in ("issuer", "key_id", "audience", "request_id", "jti"):
            self.assertNotIn(getattr(self.grant(), field), repr(self.grant()))
        with self.assertRaises(ValueError):
            replace(maximum, request_id=identifier + "a")


if __name__ == "__main__":
    unittest.main()
