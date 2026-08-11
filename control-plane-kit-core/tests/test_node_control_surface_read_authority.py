from __future__ import annotations

import ast
from dataclasses import fields, replace
import hashlib
import importlib
import importlib.util
from pathlib import Path
import unittest

import rfc8785

import control_plane_kit_core as core
from control_plane_kit_core.control_routes import (
    ControlRouteScope,
    ControlRouteSetName,
    route_set_named,
)
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.gateway_delegation import (
    DelegatedGatewayProbeGrant,
    GatewayProbeCommandKind,
    GatewayProbeRequest,
)
from control_plane_kit_core.node_control import (
    ControlPlaneCommandCodec,
    ControlPlaneResultCodec,
    ControlPlaneStateCodec,
    ControlPlaneVariableDescriptor,
    ControlPlaneVariableKind,
    ControlPlaneVariableOperationContract,
    DelegatedWorkloadNodeControlGrant,
    NodeControlCanonicalization,
    NodeControlCommandRequest,
    NodeControlGraphReference,
    NodeControlGraphReferenceRole,
    NodeControlOperation,
    NodeControlTarget,
    WorkloadNodeControlGrantVerificationCode,
    WorkloadNodeControlSurfaceDescriptor,
    verify_workload_node_control_grant,
)
from control_plane_kit_core.runtime_effects import GatewayTargetId


DECLARATION_BYTES = (
    b'{"profile":"workload-node-control-surface-declaration.v1","surface":'
    b'{"provider_socket_name":"control","variables":['
    b'{"capability":"node-controllable","description":"Public mode.",'
    b'"kind":"scalar","operation_contracts":['
    b'{"command_codec":null,"operation":"read-state","result_codec":"control.state.v1"},'
    b'{"command_codec":"control.replace-scalar.v1","operation":"apply-command",'
    b'"result_codec":"control.transition.v1"}],"route_set":"node-control",'
    b'"state_codec":"control.scalar.v1","variable_name":"mode"}]}}'
)
DECLARATION_SHA256 = "e9ada911c3087418dbbdefafa7bf58e120832b15abb16d6a0fb453e33181acd2"
REQUEST_BYTES = (
    b'{"canonicalization":"jcs-rfc8785.v1","declaration_identity":"'
    b'e9ada911c3087418dbbdefafa7bf58e120832b15abb16d6a0fb453e33181acd2",'
    b'"kind":"capabilities","profile":"workload-node-control-surface-read-request.v1",'
    b'"request_id":"surface-read-1","target":{"graph_revision":"revision-7",'
    b'"node_id":"router","provider_socket_name":"control","workspace_id":"workspace-1"}}'
)
REQUEST_SHA256 = "efba35d7602d45bf78dac53a3ea33c72f9805fcb6c74134beaca97cc038321dc"


class NodeControlSurfaceReadAuthorityTests(unittest.TestCase):
    def contract_module(self):
        name = "control_plane_kit_core.node_control_surface_reads"
        self.assertIsNotNone(
            importlib.util.find_spec(name),
            "node-control surface-read authority language is not implemented",
        )
        return importlib.import_module(name)

    def contract(self, name: str):
        value = getattr(self.contract_module(), name, None)
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

    def variable(
        self,
        name: str = "mode",
        description: str = "Public mode.",
    ) -> ControlPlaneVariableDescriptor:
        return ControlPlaneVariableDescriptor(
            variable_name=self.reference(
                NodeControlGraphReferenceRole.VARIABLE,
                name,
            ),
            kind=ControlPlaneVariableKind.SCALAR,
            state_codec=ControlPlaneStateCodec.SCALAR_V1,
            operation_contracts=(
                ControlPlaneVariableOperationContract(
                    operation=NodeControlOperation.READ_STATE,
                    command_codec=None,
                    result_codec=ControlPlaneResultCodec.STATE_V1,
                ),
                ControlPlaneVariableOperationContract(
                    operation=NodeControlOperation.APPLY_COMMAND,
                    command_codec=ControlPlaneCommandCodec.REPLACE_SCALAR_V1,
                    result_codec=ControlPlaneResultCodec.TRANSITION_V1,
                ),
            ),
            description=description,
        )

    def surface(self) -> WorkloadNodeControlSurfaceDescriptor:
        return WorkloadNodeControlSurfaceDescriptor(
            provider_socket_name=self.reference(
                NodeControlGraphReferenceRole.PROVIDER_SOCKET,
                "control",
            ),
            variables=(self.variable(),),
        )

    def maximum_surface(self) -> WorkloadNodeControlSurfaceDescriptor:
        description_lengths = [512] * 17 + [430, 1]
        return WorkloadNodeControlSurfaceDescriptor(
            provider_socket_name=self.reference(
                NodeControlGraphReferenceRole.PROVIDER_SOCKET,
                "control",
            ),
            variables=tuple(
                self.variable(
                    name=f"variable-{index:03d}",
                    description="x" * length,
                )
                for index, length in enumerate(description_lengths)
            ),
        )

    def declaration(self):
        declaration_type = self.contract("WorkloadNodeControlSurfaceDeclaration")
        return declaration_type(surface=self.surface())

    def request(self, **changes: object):
        request_type = self.contract("NodeControlSurfaceReadRequest")
        kind_type = self.contract("NodeControlSurfaceReadKind")
        values = {
            "target": self.target(),
            "kind": kind_type.CAPABILITIES,
            "declaration_identity": self.declaration().identity(),
            "request_id": "surface-read-1",
        }
        values.update(changes)
        return request_type(**values)

    def grant(self, request=None, **changes: object):
        request = request or self.request()
        grant_type = self.contract("DelegatedWorkloadNodeControlSurfaceReadGrant")
        profile_type = self.contract(
            "DelegatedWorkloadNodeControlSurfaceReadGrantProfile"
        )
        values = {
            "profile": profile_type.V1,
            "canonicalization": NodeControlCanonicalization.JCS_RFC8785_V1,
            "purpose": DelegationKeyPurpose.WORKLOAD_NODE_CONTROL_SURFACE_READ,
            "issuer": "cpk-server",
            "key_id": "surface-read-key-1",
            "audience": "workload:router:control",
            "target": request.target,
            "kind": request.kind,
            "declaration_identity": request.declaration_identity,
            "request_id": request.request_id,
            "request_digest": request.canonical_digest(),
            "issued_at": 100,
            "not_before": 100,
            "expires_at": 200,
            "jti": "surface-read-grant-1",
        }
        values.update(changes)
        return grant_type(**values)

    def test_declaration_has_one_exact_domain_separated_identity_and_bound(self) -> None:
        module = self.contract_module()
        declaration = self.declaration()
        codec = self.contract("WorkloadNodeControlSurfaceDeclarationCodec")()

        self.assertEqual(declaration.canonical_bytes(), DECLARATION_BYTES)
        self.assertEqual(hashlib.sha256(DECLARATION_BYTES).hexdigest(), DECLARATION_SHA256)
        self.assertEqual(declaration.identity().value, DECLARATION_SHA256)
        self.assertEqual(codec.decode(codec.encode(declaration)), declaration)
        self.assertEqual(module.MAX_NODE_CONTROL_SURFACE_DECLARATION_BYTES, 16_453)

        maximum = self.contract("WorkloadNodeControlSurfaceDeclaration")(
            surface=self.maximum_surface()
        )
        self.assertEqual(len(rfc8785.dumps(maximum.surface.descriptor())), 16_384)
        self.assertEqual(len(maximum.canonical_bytes()), 16_453)

        raw = {"profile": declaration.profile.value, "surface": ""}
        padding = module.MAX_NODE_CONTROL_SURFACE_DECLARATION_BYTES + 1 - len(
            rfc8785.dumps(raw)
        )
        raw["surface"] = "x" * padding
        self.assertEqual(len(rfc8785.dumps(raw)), 16_454)
        with self.assertRaisesRegex(Exception, "aggregate exceeds.*bound"):
            codec.decode(raw)

    def test_request_has_exact_wire_identity_strict_codec_and_closed_bound(self) -> None:
        module = self.contract_module()
        request = self.request()
        codec = self.contract("NodeControlSurfaceReadRequestCodec")()

        self.assertEqual(request.canonical_bytes(), REQUEST_BYTES)
        self.assertEqual(hashlib.sha256(REQUEST_BYTES).hexdigest(), REQUEST_SHA256)
        self.assertEqual(request.canonical_digest().value, REQUEST_SHA256)
        self.assertEqual(codec.decode(codec.encode(request)), request)
        self.assertEqual(module.MAX_NODE_CONTROL_SURFACE_READ_REQUEST_BYTES, 951)

        identity_type = self.contract(
            "WorkloadNodeControlSurfaceDeclarationIdentity"
        )
        kind_type = self.contract("NodeControlSurfaceReadKind")
        changed = (
            replace(
                request,
                target=self.target(
                    workspace_id=self.reference(
                        NodeControlGraphReferenceRole.WORKSPACE,
                        "workspace-2",
                    )
                ),
            ),
            replace(request, kind=kind_type.STATUS),
            replace(request, declaration_identity=identity_type("f" * 64)),
            replace(request, request_id="surface-read-2"),
        )
        for candidate in changed:
            with self.subTest(candidate=candidate):
                self.assertNotEqual(candidate.canonical_digest(), request.canonical_digest())

        maximum_identifier = "a" * 128
        maximum_request = self.contract("NodeControlSurfaceReadRequest")(
            target=NodeControlTarget(
                workspace_id=self.reference(
                    NodeControlGraphReferenceRole.WORKSPACE,
                    maximum_identifier,
                ),
                graph_revision=self.reference(
                    NodeControlGraphReferenceRole.GRAPH_REVISION,
                    maximum_identifier,
                ),
                node_id=self.reference(
                    NodeControlGraphReferenceRole.NODE,
                    maximum_identifier,
                ),
                provider_socket_name=self.reference(
                    NodeControlGraphReferenceRole.PROVIDER_SOCKET,
                    maximum_identifier,
                ),
            ),
            kind=kind_type.CAPABILITIES,
            declaration_identity=identity_type("a" * 64),
            request_id=maximum_identifier,
        )
        self.assertEqual(len(maximum_request.canonical_bytes()), 951)

        raw = codec.encode(request)
        raw["target"] = ""
        padding = module.MAX_NODE_CONTROL_SURFACE_READ_REQUEST_BYTES + 1 - len(
            rfc8785.dumps(raw)
        )
        raw["target"] = "x" * padding
        self.assertEqual(len(rfc8785.dumps(raw)), 952)
        with self.assertRaisesRegex(Exception, "aggregate exceeds.*bound"):
            codec.decode(raw)

    def test_grant_shape_is_exact_bounded_and_structurally_redacted(self) -> None:
        module = self.contract_module()
        request = self.request()
        grant = self.grant(request)
        codec = self.contract("DelegatedWorkloadNodeControlSurfaceReadGrantCodec")()

        self.assertEqual(codec.decode(codec.encode(grant)), grant)
        self.assertEqual(module.MAX_DELEGATED_NODE_CONTROL_SURFACE_READ_GRANT_BYTES, 1_984)
        self.assertEqual(
            grant.purpose,
            DelegationKeyPurpose.WORKLOAD_NODE_CONTROL_SURFACE_READ,
        )
        self.assertEqual(
            grant.profile.value,
            "workload-node-control-surface-read-grant.v1",
        )

        request_fields = {value.name: value for value in fields(type(request))}
        grant_fields = {value.name: value for value in fields(type(grant))}
        self.assertFalse(request_fields["request_id"].repr)
        for name in ("issuer", "key_id", "audience", "request_id", "jti"):
            with self.subTest(name=name):
                self.assertFalse(grant_fields[name].repr)
                self.assertNotIn(getattr(grant, name), repr(grant))

        maximum_identifier = "a" * 128
        maximum_reference = "a" * 256
        identity_type = self.contract(
            "WorkloadNodeControlSurfaceDeclarationIdentity"
        )
        digest_type = self.contract("NodeControlSurfaceReadRequestDigest")
        maximum = type(grant)(
            profile=grant.profile,
            canonicalization=grant.canonicalization,
            purpose=grant.purpose,
            issuer=maximum_reference,
            key_id=maximum_identifier,
            audience=maximum_reference,
            target=NodeControlTarget(
                workspace_id=self.reference(NodeControlGraphReferenceRole.WORKSPACE, maximum_identifier),
                graph_revision=self.reference(NodeControlGraphReferenceRole.GRAPH_REVISION, maximum_identifier),
                node_id=self.reference(NodeControlGraphReferenceRole.NODE, maximum_identifier),
                provider_socket_name=self.reference(NodeControlGraphReferenceRole.PROVIDER_SOCKET, maximum_identifier),
            ),
            kind=request.kind,
            declaration_identity=identity_type("a" * 64),
            request_id=maximum_identifier,
            request_digest=digest_type("a" * 64),
            issued_at=9_007_199_254_740_691,
            not_before=9_007_199_254_740_691,
            expires_at=9_007_199_254_740_991,
            jti=maximum_identifier,
        )
        self.assertEqual(len(rfc8785.dumps(maximum.descriptor())), 1_984)

        raw = codec.encode(grant)
        raw["target"] = ""
        padding = module.MAX_DELEGATED_NODE_CONTROL_SURFACE_READ_GRANT_BYTES + 1 - len(
            rfc8785.dumps(raw)
        )
        raw["target"] = "x" * padding
        self.assertEqual(len(rfc8785.dumps(raw)), 1_985)
        with self.assertRaisesRegex(Exception, "aggregate exceeds.*bound"):
            codec.decode(raw)

    def test_verifier_binds_every_claim_with_frozen_precedence(self) -> None:
        request = self.request()
        grant = self.grant(request)
        verify = self.contract("verify_workload_node_control_surface_read_grant")
        code = self.contract("WorkloadNodeControlSurfaceReadGrantVerificationCode")

        def result(candidate):
            return verify(
                candidate,
                request,
                expected_issuer="cpk-server",
                expected_key_id="surface-read-key-1",
                expected_audience="workload:router:control",
                now=150,
            )

        self.assertTrue(result(grant).is_accepted)
        other_target = self.target(
            workspace_id=self.reference(NodeControlGraphReferenceRole.WORKSPACE, "workspace-2"),
            graph_revision=self.reference(NodeControlGraphReferenceRole.GRAPH_REVISION, "revision-8"),
            node_id=self.reference(NodeControlGraphReferenceRole.NODE, "router-2"),
            provider_socket_name=self.reference(NodeControlGraphReferenceRole.PROVIDER_SOCKET, "control-2"),
        )
        kind_type = self.contract("NodeControlSurfaceReadKind")
        identity_type = self.contract(
            "WorkloadNodeControlSurfaceDeclarationIdentity"
        )
        digest_type = self.contract("NodeControlSurfaceReadRequestDigest")
        cases = (
            (object(), code.GRANT_TYPE_MISMATCH),
            (replace(grant, purpose=DelegationKeyPurpose.WORKLOAD_NODE_CONTROL, issuer="other"), code.PURPOSE_MISMATCH),
            (replace(grant, issuer="other", key_id="other-key"), code.ISSUER_MISMATCH),
            (replace(grant, key_id="other-key", audience="other"), code.KEY_MISMATCH),
            (replace(grant, audience="other", not_before=151), code.AUDIENCE_MISMATCH),
            (replace(grant, not_before=151, target=other_target), code.TEMPORALLY_INVALID),
            (replace(grant, target=other_target), code.WORKSPACE_MISMATCH),
            (replace(grant, target=replace(other_target, workspace_id=request.target.workspace_id)), code.REVISION_MISMATCH),
            (replace(grant, target=replace(other_target, workspace_id=request.target.workspace_id, graph_revision=request.target.graph_revision)), code.NODE_MISMATCH),
            (replace(grant, target=replace(other_target, workspace_id=request.target.workspace_id, graph_revision=request.target.graph_revision, node_id=request.target.node_id), kind=kind_type.STATUS), code.SOCKET_MISMATCH),
            (replace(grant, kind=kind_type.STATUS, declaration_identity=identity_type("f" * 64)), code.KIND_MISMATCH),
            (replace(grant, declaration_identity=identity_type("f" * 64), request_id="surface-read-2"), code.DECLARATION_MISMATCH),
            (replace(grant, request_id="surface-read-2", request_digest=digest_type("f" * 64)), code.REQUEST_MISMATCH),
        )
        for candidate, expected in cases:
            with self.subTest(expected=expected):
                observed = result(candidate)
                self.assertFalse(observed.is_accepted)
                self.assertIs(observed.code, expected)

    def test_authority_languages_are_disjoint_in_both_directions(self) -> None:
        request = self.request()
        surface_grant = self.grant(request)
        command_request = NodeControlCommandRequest(
            target=self.target(),
            variable_name=self.reference(NodeControlGraphReferenceRole.VARIABLE, "mode"),
            operation=NodeControlOperation.READ_STATE,
            request_id="command-read-1",
            idempotency_key="command-read-1",
        )
        command_grant = DelegatedWorkloadNodeControlGrant(
            issuer="cpk-server",
            key_id="command-key-1",
            audience="workload:router:control",
            target=command_request.target,
            variable_name=command_request.variable_name,
            operation=command_request.operation,
            command_codec=None,
            request_id=command_request.request_id,
            idempotency_key=command_request.idempotency_key,
            request_digest=command_request.canonical_digest(),
            issued_at=100,
            not_before=100,
            expires_at=200,
            jti="command-grant-1",
        )
        command_result = verify_workload_node_control_grant(
            surface_grant,
            command_request,
            expected_issuer="cpk-server",
            expected_audience="workload:router:control",
            now=150,
        )
        self.assertIs(
            command_result.code,
            WorkloadNodeControlGrantVerificationCode.GRANT_TYPE_MISMATCH,
        )

        verify_surface = self.contract(
            "verify_workload_node_control_surface_read_grant"
        )
        surface_code = self.contract(
            "WorkloadNodeControlSurfaceReadGrantVerificationCode"
        )
        for candidate in (command_grant, self.gateway_grant()):
            with self.subTest(candidate=type(candidate).__name__):
                observed = verify_surface(
                    candidate,
                    request,
                    expected_issuer="cpk-server",
                    expected_key_id="surface-read-key-1",
                    expected_audience="workload:router:control",
                    now=150,
                )
                self.assertIs(observed.code, surface_code.GRANT_TYPE_MISMATCH)

    def gateway_grant(self) -> DelegatedGatewayProbeGrant:
        request = GatewayProbeRequest(
            GatewayProbeCommandKind.HTTP_STATUS,
            GatewayTargetId("router.internal"),
            "/health",
        )
        return DelegatedGatewayProbeGrant(
            issuer="cpk-server",
            key_id="gateway-key-1",
            audience="gateway",
            workspace_id="workspace-1",
            operation_id="operation-1",
            request_id="probe-1",
            gateway_node_id="gateway-1",
            probe_kind=request.kind,
            target_id=request.target_id,
            request_digest=request.canonical_digest(),
            issued_at=100,
            expires_at=200,
            jti="probe-grant-1",
        )

    def test_malformed_wire_failures_are_bounded_redacted_and_cause_free(self) -> None:
        request_codec = self.contract("NodeControlSurfaceReadRequestCodec")()
        grant_codec = self.contract("DelegatedWorkloadNodeControlSurfaceReadGrantCodec")()
        candidates = (
            (request_codec, {**request_codec.encode(self.request()), "kind": "candidate-secret-kind"}),
            (grant_codec, {**grant_codec.encode(self.grant()), "purpose": "candidate-secret-purpose"}),
            (grant_codec, {**grant_codec.encode(self.grant()), "candidate-secret-key": "candidate-secret-value"}),
        )
        error_type = self.contract("NodeControlSurfaceReadContractError")
        for codec, candidate in candidates:
            with self.subTest(codec=type(codec).__name__):
                try:
                    codec.decode(candidate)
                except error_type as error:
                    self.assertLessEqual(len(str(error)), 128)
                    self.assertNotIn("candidate-secret", str(error))
                    self.assertIsNone(error.__cause__)
                    self.assertIsNone(error.__context__)
                else:
                    self.fail("malformed public wire material was accepted")

    def test_open_authority_material_rejects_credentials_and_runtime_endpoints(self) -> None:
        error_type = self.contract("NodeControlSurfaceReadContractError")
        request = self.request()
        grant = self.grant(request)
        unsafe = (
            lambda: replace(request, request_id="sk-secret-value"),
            lambda: replace(grant, issuer="127.0.0.1"),
            lambda: replace(grant, audience="localhost"),
            lambda: replace(grant, jti="sg.secret-value"),
        )
        for factory in unsafe:
            with self.subTest(factory=factory):
                with self.assertRaises(error_type):
                    factory()

        self.assertEqual(
            replace(grant, issuer="cpk.example.internal").issuer,
            "cpk.example.internal",
        )

    def test_routes_root_exports_and_module_imports_preserve_pure_ownership(self) -> None:
        scope = getattr(ControlRouteScope, "READ_NODE_CONTROL_SURFACE", None)
        self.assertIsNotNone(scope, "READ_NODE_CONTROL_SURFACE is not implemented")
        route_set = route_set_named(ControlRouteSetName.NODE_CONTROL)
        self.assertEqual(
            [(route.method.value, route.path, route.scope) for route in route_set.routes],
            [
                ("GET", "/__control/capabilities", scope),
                ("GET", "/__control/status", scope),
                ("GET", "/__control/variables/{variable_name}", ControlRouteScope.READ_NODE_CONTROL),
                ("POST", "/__control/variables/{variable_name}/commands", ControlRouteScope.APPLY_NODE_CONTROL),
            ],
        )

        exports = (
            "DelegatedWorkloadNodeControlSurfaceReadGrant",
            "DelegatedWorkloadNodeControlSurfaceReadGrantCodec",
            "DelegatedWorkloadNodeControlSurfaceReadGrantProfile",
            "NodeControlSurfaceReadKind",
            "NodeControlSurfaceReadRequest",
            "NodeControlSurfaceReadRequestCodec",
            "NodeControlSurfaceReadRequestDigest",
            "WorkloadNodeControlSurfaceDeclaration",
            "WorkloadNodeControlSurfaceDeclarationCodec",
            "WorkloadNodeControlSurfaceDeclarationIdentity",
            "WorkloadNodeControlSurfaceDeclarationProfile",
            "WorkloadNodeControlSurfaceReadGrantVerificationCode",
            "verify_workload_node_control_surface_read_grant",
        )
        for name in exports:
            with self.subTest(name=name):
                self.assertIsNotNone(getattr(core, name, None), name)

        module = self.contract_module()
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        self.assertFalse(
            any(
                name.startswith(
                    (
                        "control_plane_kit_operations",
                        "control_plane_kit_interpreters",
                        "fastapi",
                        "docker",
                        "psycopg",
                    )
                )
                for name in imports
            )
        )


if __name__ == "__main__":
    unittest.main()
