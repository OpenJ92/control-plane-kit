from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import fields, replace
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import unittest

import rfc8785

import control_plane_kit_core as core
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.gateway_delegation import (
    DelegatedGatewayProbeGrant,
    DelegatedGatewayProbeGrantCodec,
    GatewayDelegationContractError,
    GatewayProbeCommandKind,
    GatewayProbeRequest,
)
from control_plane_kit_core.node_control import (
    ControlPlaneCommandCodec,
    ControlPlaneTransitionPrecondition,
    DelegatedWorkloadNodeControlGrant,
    DelegatedWorkloadNodeControlGrantCodec,
    MapControlState,
    NodeControlCanonicalization,
    NodeControlCommandRequest,
    NodeControlContractError,
    NodeControlGraphReference,
    NodeControlGraphReferenceRole,
    NodeControlOperation,
    NodeControlPayload,
    NodeControlRequestDigest,
    NodeControlTarget,
    ScalarControlState,
    WeightedRoutingControlState,
    WorkloadNodeControlGrantVerificationCode,
    verify_workload_node_control_grant,
)
from control_plane_kit_core.node_control_surface_reads import (
    DelegatedWorkloadNodeControlSurfaceReadGrant,
    DelegatedWorkloadNodeControlSurfaceReadGrantCodec,
    DelegatedWorkloadNodeControlSurfaceReadGrantProfile,
    NodeControlSurfaceReadKind,
    NodeControlSurfaceReadContractError,
    NodeControlSurfaceReadRequest,
    NodeControlSurfaceReadRequestDigest,
    WorkloadNodeControlSurfaceDeclarationIdentity,
    WorkloadNodeControlSurfaceReadGrantVerificationCode,
    verify_workload_node_control_surface_read_grant,
)
from control_plane_kit_core.runtime_effects import GatewayTargetId


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "node_control_transit_canonical_wire_v1.json"
)
MODULE_NAME = "control_plane_kit_core.node_control_transit"


class NodeControlTransitTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(
            importlib.util.find_spec(MODULE_NAME),
            "gateway node-control transit language is not implemented",
        )
        return importlib.import_module(MODULE_NAME)

    def contract(self, name: str):
        value = getattr(self.module(), name, None)
        self.assertIsNotNone(value, f"{name} is not implemented")
        return value

    def reference(
        self,
        role: NodeControlGraphReferenceRole,
        value: str,
    ) -> NodeControlGraphReference:
        return NodeControlGraphReference(role, value)

    def target(self, **changes: object) -> NodeControlTarget:
        values = {
            "workspace_id": self.reference(
                NodeControlGraphReferenceRole.WORKSPACE,
                "workspace-1",
            ),
            "graph_revision": self.reference(
                NodeControlGraphReferenceRole.GRAPH_REVISION,
                "revision-7",
            ),
            "node_id": self.reference(NodeControlGraphReferenceRole.NODE, "router"),
            "provider_socket_name": self.reference(
                NodeControlGraphReferenceRole.PROVIDER_SOCKET,
                "control",
            ),
        }
        values.update(changes)
        return NodeControlTarget(**values)

    def request(self, **changes: object) -> NodeControlCommandRequest:
        values = {
            "target": self.target(),
            "variable_name": self.reference(
                NodeControlGraphReferenceRole.VARIABLE,
                "mode",
            ),
            "operation": NodeControlOperation.APPLY_COMMAND,
            "request_id": "request-1",
            "idempotency_key": "idempotency-1",
            "command_codec": ControlPlaneCommandCodec.REPLACE_SCALAR_V1,
            "precondition": ControlPlaneTransitionPrecondition(7),
            "payload": NodeControlPayload(
                ControlPlaneCommandCodec.REPLACE_SCALAR_V1,
                ScalarControlState("green"),
            ),
        }
        values.update(changes)
        return NodeControlCommandRequest(**values)

    def grant(self, request=None, **changes: object):
        request = request or self.request()
        grant_type = self.contract("DelegatedGatewayNodeControlTransitGrant")
        profile = self.contract(
            "DelegatedGatewayNodeControlTransitGrantProfile"
        )
        values = {
            "profile": profile.V1,
            "canonicalization": NodeControlCanonicalization.JCS_RFC8785_V1,
            "purpose": DelegationKeyPurpose.GATEWAY_NODE_CONTROL_TRANSIT,
            "issuer": "cpk-server",
            "key_id": "gateway-transit-key-1",
            "attempt_id": "attempt-1",
            "workspace_id": request.target.workspace_id,
            "graph_revision": request.target.graph_revision,
            "gateway_node_id": self.reference(
                NodeControlGraphReferenceRole.NODE,
                "gateway-1",
            ),
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
            "jti": "transit-grant-1",
        }
        values.update(changes)
        return grant_type(**values)

    def verify(self, grant, request=None, now: int = 150):
        return self.contract("verify_gateway_node_control_transit_grant")(
            grant,
            request or self.request(),
            expected_issuer="cpk-server",
            expected_key_id="gateway-transit-key-1",
            expected_attempt_id="attempt-1",
            expected_gateway_node_id=self.reference(
                NodeControlGraphReferenceRole.NODE,
                "gateway-1",
            ),
            now=now,
        )

    def test_exact_canonical_vector_digest_profile_and_public_exports(self) -> None:
        module = self.module()
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))["grant"]
        grant = self.grant()
        codec = self.contract("DelegatedGatewayNodeControlTransitGrantCodec")()
        fixture_bytes = fixture["canonical_utf8"].encode("utf-8")

        self.assertEqual(rfc8785.dumps(fixture["descriptor"]), fixture_bytes)
        self.assertEqual(
            hashlib.sha256(fixture_bytes).hexdigest(),
            fixture["sha256"],
        )

        self.assertEqual(
            grant.profile.value,
            "gateway-node-control-transit-grant.v1",
        )
        self.assertEqual(codec.encode(grant), fixture["descriptor"])
        self.assertEqual(
            codec.encode_canonical_bytes(grant),
            fixture_bytes,
        )
        self.assertEqual(grant.canonical_bytes(), codec.encode_canonical_bytes(grant))
        self.assertEqual(grant.canonical_digest().value, fixture["sha256"])
        self.assertEqual(
            hashlib.sha256(grant.canonical_bytes()).hexdigest(),
            fixture["sha256"],
        )
        self.assertEqual(codec.decode(codec.encode(grant)), grant)
        self.assertEqual(codec.decode_canonical_bytes(grant.canonical_bytes()), grant)

        expected_exports = {
            "MAX_DELEGATED_GATEWAY_NODE_CONTROL_TRANSIT_GRANT_BYTES",
            "MAX_GATEWAY_NODE_CONTROL_TRANSIT_AUDIENCE_BYTES",
            "MAX_GATEWAY_NODE_CONTROL_TRANSIT_GRANT_LIFETIME_SECONDS",
            "DelegatedGatewayNodeControlTransitGrantProfile",
            "GatewayNodeControlTransitGrantDigest",
            "DelegatedGatewayNodeControlTransitGrant",
            "DelegatedGatewayNodeControlTransitGrantCodec",
            "GatewayNodeControlTransitContractError",
            "GatewayNodeControlTransitGrantVerificationCode",
            "GatewayNodeControlTransitGrantVerificationResult",
            "verify_gateway_node_control_transit_grant",
        }
        for name in expected_exports:
            with self.subTest(name=name):
                self.assertIs(getattr(core, name), getattr(module, name))
                self.assertIn(name, core.__all__)
        self.assertNotIn("_node_control_public_wire", core.__all__)

    def test_exact_reachable_audience_and_aggregate_maxima(self) -> None:
        module = self.module()
        identifier = "a" * 128
        reference = "a" * 256
        maximum_target = NodeControlTarget(
            workspace_id=self.reference(
                NodeControlGraphReferenceRole.WORKSPACE,
                identifier,
            ),
            graph_revision=self.reference(
                NodeControlGraphReferenceRole.GRAPH_REVISION,
                identifier,
            ),
            node_id=self.reference(NodeControlGraphReferenceRole.NODE, identifier),
            provider_socket_name=self.reference(
                NodeControlGraphReferenceRole.PROVIDER_SOCKET,
                identifier,
            ),
        )
        maximum = self.grant(
            issuer=reference,
            key_id=identifier,
            attempt_id=identifier,
            workspace_id=maximum_target.workspace_id,
            graph_revision=maximum_target.graph_revision,
            gateway_node_id=self.reference(
                NodeControlGraphReferenceRole.NODE,
                identifier,
            ),
            target=maximum_target,
            variable_name=self.reference(
                NodeControlGraphReferenceRole.VARIABLE,
                identifier,
            ),
            operation=NodeControlOperation.APPLY_COMMAND,
            command_codec=ControlPlaneCommandCodec.REPLACE_WEIGHTED_ROUTING_V1,
            request_id=identifier,
            idempotency_key=identifier,
            request_digest=NodeControlRequestDigest("a" * 64),
            issued_at=9_007_199_254_740_691,
            not_before=9_007_199_254_740_692,
            expires_at=9_007_199_254_740_991,
            jti=identifier,
        )
        self.assertEqual(module.MAX_GATEWAY_NODE_CONTROL_TRANSIT_AUDIENCE_BYTES, 265)
        self.assertEqual(len(maximum.audience.encode("ascii")), 265)
        self.assertEqual(
            maximum.audience,
            f"gateway:{identifier}:{identifier}",
        )
        self.assertEqual(
            module.MAX_DELEGATED_GATEWAY_NODE_CONTROL_TRANSIT_GRANT_BYTES,
            2_834,
        )
        self.assertEqual(len(maximum.canonical_bytes()), 2_834)
        self.assertEqual(
            module.MAX_GATEWAY_NODE_CONTROL_TRANSIT_GRANT_LIFETIME_SECONDS,
            300,
        )

        codec = self.contract("DelegatedGatewayNodeControlTransitGrantCodec")()
        error_type = self.contract("GatewayNodeControlTransitContractError")
        with self.assertRaisesRegex(error_type, "aggregate.*bound") as caught:
            codec.decode_canonical_bytes(b"x" * 2_835)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    def test_every_published_field_bound_is_executable(self) -> None:
        codec = self.contract("DelegatedGatewayNodeControlTransitGrantCodec")()
        error_type = self.contract("GatewayNodeControlTransitContractError")
        descriptor = codec.encode(self.grant())
        safe_epoch = 9_007_199_254_740_991
        identifier_fields = (
            "key_id",
            "attempt_id",
            "request_id",
            "idempotency_key",
            "jti",
        )

        for key in identifier_fields:
            with self.subTest(identifier=key):
                with self.assertRaises(error_type):
                    codec.decode({**descriptor, key: "a" * 129})
        with self.assertRaises(error_type):
            codec.decode({**descriptor, "issuer": "a" * 257})
        with self.assertRaises(error_type):
            codec.decode({**descriptor, "request_digest": "a" * 65})

        for key in ("workspace_id", "graph_revision", "gateway_node_id", "variable_name"):
            with self.subTest(graph_reference=key):
                with self.assertRaises(error_type):
                    codec.decode({**descriptor, key: "a" * 129})
        for key in descriptor["target"]:
            with self.subTest(target_reference=key):
                candidate = deepcopy(descriptor)
                candidate["target"][key] = "a" * 129
                with self.assertRaises(error_type):
                    codec.decode(candidate)

        for key in ("issued_at", "not_before", "expires_at"):
            with self.subTest(epoch=key):
                with self.assertRaises(error_type):
                    codec.decode({**descriptor, key: safe_epoch + 1})

        maximum = self.grant(
            issued_at=safe_epoch - 300,
            not_before=safe_epoch - 299,
            expires_at=safe_epoch,
        )
        self.assertEqual(maximum.expires_at, safe_epoch)

    def test_strict_raw_byte_decoder_observes_duplicates_and_canonical_form(self) -> None:
        codec = self.contract("DelegatedGatewayNodeControlTransitGrantCodec")()
        error_type = self.contract("GatewayNodeControlTransitContractError")
        canonical = self.grant().canonical_bytes()
        duplicate_top = canonical.replace(
            b'{"attempt_id":"attempt-1",',
            b'{"attempt_id":"attempt-1","attempt_id":"other",',
            1,
        )
        duplicate_nested = canonical.replace(
            b'{"graph_revision":"revision-7","node_id":"router",',
            b'{"graph_revision":"revision-7","node_id":"router","node_id":"other",',
            1,
        )
        candidates = (
            b"\xff",
            b"{",
            duplicate_top,
            duplicate_nested,
            canonical.replace(b'"issued_at":100', b'"issued_at":NaN'),
            b" " + canonical,
            canonical.replace(b'"attempt_id":"attempt-1",', b"", 1)
            + b" ",
        )
        for candidate in candidates:
            with self.subTest(candidate=candidate[:24]):
                try:
                    codec.decode_canonical_bytes(candidate)
                except error_type as error:
                    self.assertLessEqual(len(str(error)), 128)
                    self.assertNotIn("attempt-1", str(error))
                    self.assertIsNone(error.__cause__)
                    self.assertIsNone(error.__context__)
                else:
                    self.fail("malformed or noncanonical raw grant bytes were accepted")

        descriptor = codec.encode(self.grant())
        self.assertEqual(codec.decode(descriptor), self.grant())
        for key in descriptor:
            with self.subTest(missing=key):
                candidate = dict(descriptor)
                candidate.pop(key)
                with self.assertRaises(error_type):
                    codec.decode(candidate)
            with self.subTest(wrong_type=key):
                with self.assertRaises(error_type):
                    codec.decode({**descriptor, key: None})
        with self.assertRaises(error_type):
            codec.decode({**descriptor, "unknown": "candidate-secret"})

        nested = deepcopy(descriptor)
        nested["target"].pop("node_id")
        with self.assertRaises(error_type):
            codec.decode(nested)
        nested = deepcopy(descriptor)
        nested["target"]["unknown"] = "candidate-secret"
        with self.assertRaises(error_type):
            codec.decode(nested)

    def test_constructor_locks_coordinates_audience_and_temporal_laws(self) -> None:
        error_type = self.contract("GatewayNodeControlTransitContractError")
        grant = self.grant()
        codec = self.contract("DelegatedGatewayNodeControlTransitGrantCodec")()
        descriptor = codec.encode(grant)

        self.assertNotIn("audience", {item.name for item in fields(type(grant))})
        self.assertEqual(grant.audience, "gateway:workspace-1:gateway-1")

        changed_workspace = self.reference(
            NodeControlGraphReferenceRole.WORKSPACE,
            "workspace-2",
        )
        changed_revision = self.reference(
            NodeControlGraphReferenceRole.GRAPH_REVISION,
            "revision-8",
        )
        invalid_constructions = (
            {"workspace_id": changed_workspace},
            {"graph_revision": changed_revision},
            {"workspace_id": grant.gateway_node_id},
            {"gateway_node_id": grant.workspace_id},
            {"not_before": 99},
            {"expires_at": 100},
            {"expires_at": 401},
            {"issued_at": True},
            {"purpose": "gateway-node-control-transit"},
            {"profile": "gateway-node-control-transit-grant.v1"},
        )
        for changes in invalid_constructions:
            with self.subTest(changes=changes):
                with self.assertRaises(error_type):
                    replace(grant, **changes)

        malformed_wire = (
            {**descriptor, "audience": "gateway:workspace-1:other"},
            {**descriptor, "workspace_id": "workspace-2"},
            {**descriptor, "graph_revision": "revision-8"},
            {**descriptor, "profile": "unknown"},
            {**descriptor, "canonicalization": "unknown"},
        )
        for candidate in malformed_wire:
            with self.subTest(candidate=candidate):
                with self.assertRaises(error_type) as caught:
                    codec.decode(candidate)
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)

    def test_verifier_binds_every_claim_with_exact_precedence(self) -> None:
        code = self.contract("GatewayNodeControlTransitGrantVerificationCode")
        grant = self.grant()
        request = self.request()
        self.assertEqual(
            tuple(member.value for member in code),
            (
                "grant-type-mismatch",
                "purpose-mismatch",
                "issuer-mismatch",
                "key-mismatch",
                "temporally-invalid",
                "attempt-mismatch",
                "workspace-mismatch",
                "revision-mismatch",
                "gateway-mismatch",
                "node-mismatch",
                "socket-mismatch",
                "variable-mismatch",
                "command-mismatch",
                "request-mismatch",
            ),
        )
        accepted = self.verify(grant, request)
        self.assertTrue(accepted.is_accepted)
        self.assertIsNone(accepted.code)
        self.assertTrue(self.verify(grant, request, grant.not_before).is_accepted)
        self.assertIs(
            self.verify(grant, request, grant.expires_at).code,
            code.TEMPORALLY_INVALID,
        )

        other_target = self.target(
            workspace_id=self.reference(
                NodeControlGraphReferenceRole.WORKSPACE,
                "workspace-2",
            ),
            graph_revision=self.reference(
                NodeControlGraphReferenceRole.GRAPH_REVISION,
                "revision-8",
            ),
            node_id=self.reference(NodeControlGraphReferenceRole.NODE, "router-2"),
            provider_socket_name=self.reference(
                NodeControlGraphReferenceRole.PROVIDER_SOCKET,
                "control-2",
            ),
        )
        changed_variable = self.reference(
            NodeControlGraphReferenceRole.VARIABLE,
            "mode-2",
        )
        cases = (
            (object(), code.GRANT_TYPE_MISMATCH, {}),
            (
                replace(
                    grant,
                    purpose=DelegationKeyPurpose.WORKLOAD_NODE_CONTROL,
                    issuer="other",
                ),
                code.PURPOSE_MISMATCH,
                {},
            ),
            (replace(grant, issuer="other", key_id="other"), code.ISSUER_MISMATCH, {}),
            (replace(grant, key_id="other", not_before=151), code.KEY_MISMATCH, {}),
            (
                replace(grant, not_before=151, attempt_id="attempt-2"),
                code.TEMPORALLY_INVALID,
                {},
            ),
            (
                replace(grant, attempt_id="attempt-2", target=other_target,
                        workspace_id=other_target.workspace_id,
                        graph_revision=other_target.graph_revision),
                code.ATTEMPT_MISMATCH,
                {},
            ),
            (grant, code.WORKSPACE_MISMATCH, {"request": replace(request, target=other_target)}),
            (
                grant,
                code.REVISION_MISMATCH,
                {"request": replace(request, target=replace(other_target, workspace_id=request.target.workspace_id))},
            ),
            (
                grant,
                code.GATEWAY_MISMATCH,
                {"gateway": self.reference(NodeControlGraphReferenceRole.NODE, "gateway-2")},
            ),
            (
                grant,
                code.NODE_MISMATCH,
                {"request": replace(request, target=replace(other_target, workspace_id=request.target.workspace_id, graph_revision=request.target.graph_revision))},
            ),
            (
                grant,
                code.SOCKET_MISMATCH,
                {"request": replace(request, target=replace(other_target, workspace_id=request.target.workspace_id, graph_revision=request.target.graph_revision, node_id=request.target.node_id))},
            ),
            (grant, code.VARIABLE_MISMATCH, {"request": replace(request, variable_name=changed_variable)}),
            (
                grant,
                code.COMMAND_MISMATCH,
                {"request": NodeControlCommandRequest(
                    target=request.target,
                    variable_name=request.variable_name,
                    operation=NodeControlOperation.READ_STATE,
                    request_id=request.request_id,
                    idempotency_key=request.idempotency_key,
                )},
            ),
            (
                replace(grant, request_id="request-2", request_digest=NodeControlRequestDigest("f" * 64)),
                code.REQUEST_MISMATCH,
                {},
            ),
        )
        for candidate, expected, options in cases:
            with self.subTest(expected=expected):
                observed = self.contract("verify_gateway_node_control_transit_grant")(
                    candidate,
                    options.get("request", request),
                    expected_issuer="cpk-server",
                    expected_key_id="gateway-transit-key-1",
                    expected_attempt_id="attempt-1",
                    expected_gateway_node_id=options.get(
                        "gateway",
                        grant.gateway_node_id,
                    ),
                    now=150,
                )
                self.assertFalse(observed.is_accepted)
                self.assertIs(observed.code, expected)

        result_type = self.contract(
            "GatewayNodeControlTransitGrantVerificationResult"
        )
        error_type = self.contract("GatewayNodeControlTransitContractError")
        with self.assertRaises(error_type):
            result_type(True, code.REQUEST_MISMATCH)
        with self.assertRaises(error_type):
            result_type(False, None)

    def test_four_authority_families_are_pairwise_non_substitutable(self) -> None:
        transit = self.grant()
        request = self.request()
        command = DelegatedWorkloadNodeControlGrant(
            issuer="cpk-server",
            key_id="command-key-1",
            audience="workload:router:control",
            target=request.target,
            variable_name=request.variable_name,
            operation=request.operation,
            command_codec=request.command_codec,
            request_id=request.request_id,
            idempotency_key=request.idempotency_key,
            request_digest=request.canonical_digest(),
            issued_at=100,
            not_before=100,
            expires_at=200,
            jti="command-grant-1",
        )
        surface_request = NodeControlSurfaceReadRequest(
            target=request.target,
            kind=NodeControlSurfaceReadKind.CAPABILITIES,
            declaration_identity=WorkloadNodeControlSurfaceDeclarationIdentity(
                "a" * 64
            ),
            request_id="surface-read-1",
        )
        surface = DelegatedWorkloadNodeControlSurfaceReadGrant(
            profile=DelegatedWorkloadNodeControlSurfaceReadGrantProfile.V1,
            canonicalization=NodeControlCanonicalization.JCS_RFC8785_V1,
            purpose=DelegationKeyPurpose.WORKLOAD_NODE_CONTROL_SURFACE_READ,
            issuer="cpk-server",
            key_id="surface-key-1",
            audience="workload:router:control",
            target=surface_request.target,
            kind=surface_request.kind,
            declaration_identity=surface_request.declaration_identity,
            request_id=surface_request.request_id,
            request_digest=surface_request.canonical_digest(),
            issued_at=100,
            not_before=100,
            expires_at=200,
            jti="surface-grant-1",
        )
        probe_request = GatewayProbeRequest(
            GatewayProbeCommandKind.HTTP_STATUS,
            GatewayTargetId("router.internal"),
            "/health",
        )
        probe = DelegatedGatewayProbeGrant(
            issuer="cpk-server",
            key_id="probe-key-1",
            audience="gateway",
            workspace_id="workspace-1",
            operation_id="operation-1",
            request_id="probe-1",
            gateway_node_id="gateway-1",
            probe_kind=probe_request.kind,
            target_id=probe_request.target_id,
            request_digest=probe_request.canonical_digest(),
            issued_at=100,
            expires_at=200,
            jti="probe-grant-1",
        )
        codecs = {
            "transit": self.contract("DelegatedGatewayNodeControlTransitGrantCodec")(),
            "command": DelegatedWorkloadNodeControlGrantCodec(),
            "surface": DelegatedWorkloadNodeControlSurfaceReadGrantCodec(),
            "probe": DelegatedGatewayProbeGrantCodec(),
        }
        grants = {
            "transit": transit,
            "command": command,
            "surface": surface,
            "probe": probe,
        }
        errors = {
            "transit": self.contract("GatewayNodeControlTransitContractError"),
            "command": NodeControlContractError,
            "surface": NodeControlSurfaceReadContractError,
            "probe": GatewayDelegationContractError,
        }
        for destination, codec in codecs.items():
            for source, foreign in grants.items():
                if destination == source:
                    continue
                with self.subTest(destination=destination, source=source, seam="object"):
                    with self.assertRaises(errors[destination]):
                        codec.encode(foreign)
                with self.subTest(destination=destination, source=source, seam="descriptor"):
                    with self.assertRaises(errors[destination]):
                        codec.decode(codecs[source].encode(foreign))

        transit_code = self.contract(
            "GatewayNodeControlTransitGrantVerificationCode"
        )
        for foreign in (command, surface, probe):
            with self.subTest(transit_rejects=type(foreign).__name__):
                self.assertIs(
                    self.verify(foreign).code,
                    transit_code.GRANT_TYPE_MISMATCH,
                )
        self.assertIs(
            verify_workload_node_control_grant(
                transit,
                request,
                expected_issuer="cpk-server",
                expected_audience="workload:router:control",
                now=150,
            ).code,
            WorkloadNodeControlGrantVerificationCode.GRANT_TYPE_MISMATCH,
        )
        self.assertIs(
            verify_workload_node_control_surface_read_grant(
                transit,
                surface_request,
                expected_issuer="cpk-server",
                expected_key_id="surface-key-1",
                expected_audience="workload:router:control",
                now=150,
            ).code,
            WorkloadNodeControlSurfaceReadGrantVerificationCode.GRANT_TYPE_MISMATCH,
        )

    def test_public_material_repr_errors_and_private_ownership_are_bounded(self) -> None:
        module = self.module()
        error_type = self.contract("GatewayNodeControlTransitContractError")
        canaries = {
            "issuer": "issuer-canary",
            "key_id": "key-canary",
            "attempt_id": "attempt-canary",
            "workspace_id": "workspace-canary",
            "graph_revision": "revision-canary",
            "gateway_node_id": "gateway-canary",
            "node_id": "workload-canary",
            "provider_socket_name": "socket-canary",
            "variable_name": "variable-canary",
            "request_id": "request-canary",
            "idempotency_key": "idempotency-canary",
            "request_digest": "d" * 64,
            "jti": "jti-canary",
        }
        target = NodeControlTarget(
            workspace_id=self.reference(
                NodeControlGraphReferenceRole.WORKSPACE,
                canaries["workspace_id"],
            ),
            graph_revision=self.reference(
                NodeControlGraphReferenceRole.GRAPH_REVISION,
                canaries["graph_revision"],
            ),
            node_id=self.reference(
                NodeControlGraphReferenceRole.NODE,
                canaries["node_id"],
            ),
            provider_socket_name=self.reference(
                NodeControlGraphReferenceRole.PROVIDER_SOCKET,
                canaries["provider_socket_name"],
            ),
        )
        grant = self.grant(
            issuer=canaries["issuer"],
            key_id=canaries["key_id"],
            attempt_id=canaries["attempt_id"],
            workspace_id=target.workspace_id,
            graph_revision=target.graph_revision,
            gateway_node_id=self.reference(
                NodeControlGraphReferenceRole.NODE,
                canaries["gateway_node_id"],
            ),
            target=target,
            variable_name=self.reference(
                NodeControlGraphReferenceRole.VARIABLE,
                canaries["variable_name"],
            ),
            request_id=canaries["request_id"],
            idempotency_key=canaries["idempotency_key"],
            request_digest=NodeControlRequestDigest(canaries["request_digest"]),
            jti=canaries["jti"],
        )
        grant_fields = {item.name: item for item in fields(type(grant))}
        for name in (
            "issuer",
            "key_id",
            "attempt_id",
            "request_id",
            "idempotency_key",
            "jti",
        ):
            with self.subTest(redacted=name):
                self.assertFalse(grant_fields[name].repr)
                self.assertNotIn(getattr(grant, name), repr(grant))
        self.assertNotIn(grant.audience, repr(grant))
        rendered = repr(grant)
        for name, canary in canaries.items():
            with self.subTest(topology_redacted=name):
                self.assertNotIn(canary, rendered)

        codec = self.contract("DelegatedGatewayNodeControlTransitGrantCodec")()
        descriptor = codec.encode(grant)
        candidates = (
            {**descriptor, "issuer": "sk-candidate-secret"},
            {**descriptor, "issuer": "service.internal:8443"},
            {**descriptor, "purpose": "candidate-secret-purpose"},
            {**descriptor, "candidate-secret-key": "candidate-secret-value"},
        )
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                try:
                    codec.decode(candidate)
                except error_type as error:
                    self.assertLessEqual(len(str(error)), 128)
                    self.assertNotIn("candidate-secret", str(error))
                    self.assertNotIn("service.internal", str(error))
                    self.assertIsNone(error.__cause__)
                    self.assertIsNone(error.__context__)
                else:
                    self.fail("forbidden or malformed public material was accepted")

        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        self.assertIn(
            "control_plane_kit_core._node_control_public_wire",
            imported_modules,
        )
        self.assertTrue(
            imported_modules.isdisjoint(
                {
                    "control_plane_kit",
                    "control_plane_kit_operations",
                    "docker",
                    "fastapi",
                    "httpx",
                    "mcp",
                    "psycopg",
                    "requests",
                    "rfc8785",
                    "ipaddress",
                    "re",
                    "socket",
                    "uvicorn",
                }
            )
        )
        definitions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        }
        self.assertTrue(
            definitions.isdisjoint(
                {
                    "canonical_json_bytes",
                    "identifier_violation",
                    "reference_violation",
                    "digest_violation",
                    "epoch_violation",
                    "public_material_violation",
                }
            )
        )
        public_members = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
            and not node.name.startswith("_")
        }
        self.assertTrue(
            public_members.isdisjoint(
                {
                    "URL",
                    "Host",
                    "Path",
                    "Body",
                    "Token",
                    "Signature",
                    "PrivateKey",
                    "SecretReference",
                    "ProviderDiagnostic",
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
