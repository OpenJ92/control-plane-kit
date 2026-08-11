from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import fields, replace
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
from typing import get_args
import unittest

import rfc8785

import control_plane_kit_core as core
from control_plane_kit_core.node_control import (
    ControlPlaneCommandCodec,
    ControlPlaneResultCodec,
    ControlPlaneStateCodec,
    ControlPlaneVariableDescriptor,
    ControlPlaneVariableKind,
    ControlPlaneVariableOperationContract,
    NodeControlCanonicalization,
    NodeControlGraphReference,
    NodeControlGraphReferenceRole,
    NodeControlOperation,
    NodeControlTarget,
    WorkloadNodeControlSurfaceDescriptor,
)
from control_plane_kit_core.node_control_surface_reads import (
    NodeControlSurfaceReadContractError,
    NodeControlSurfaceReadKind,
    NodeControlSurfaceReadRequest,
    NodeControlSurfaceReadRequestCodec,
    WorkloadNodeControlSurfaceDeclaration,
    WorkloadNodeControlSurfaceDeclarationCodec,
    WorkloadNodeControlSurfaceDeclarationIdentity,
)


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "node_control_surface_read_canonical_wire_v1.json"
)
RESULT_MODULE = "control_plane_kit_core.node_control_surface_read_results"


class NodeControlSurfaceReadResultTests(unittest.TestCase):
    def result_module(self):
        self.assertIsNotNone(
            importlib.util.find_spec(RESULT_MODULE),
            "node-control surface-read result language is not implemented",
        )
        return importlib.import_module(RESULT_MODULE)

    def contract(self, name: str):
        value = getattr(self.result_module(), name, None)
        self.assertIsNotNone(value, f"{name} is not implemented")
        return value

    def fixture(self) -> dict[str, object]:
        return json.loads(FIXTURE.read_text(encoding="utf-8"))

    def reference(
        self,
        role: NodeControlGraphReferenceRole,
        value: str,
    ) -> NodeControlGraphReference:
        return NodeControlGraphReference(role, value)

    def variable(
        self,
        name: str,
        *,
        kind: ControlPlaneVariableKind = ControlPlaneVariableKind.SCALAR,
        description: str | None = None,
    ) -> ControlPlaneVariableDescriptor:
        state_codecs = {
            ControlPlaneVariableKind.SCALAR: ControlPlaneStateCodec.SCALAR_V1,
            ControlPlaneVariableKind.MAP: ControlPlaneStateCodec.MAP_V1,
            ControlPlaneVariableKind.WEIGHTED_ROUTING: (
                ControlPlaneStateCodec.WEIGHTED_ROUTING_V1
            ),
        }
        command_codecs = {
            ControlPlaneVariableKind.SCALAR: (
                ControlPlaneCommandCodec.REPLACE_SCALAR_V1
            ),
            ControlPlaneVariableKind.MAP: ControlPlaneCommandCodec.REPLACE_MAP_V1,
            ControlPlaneVariableKind.WEIGHTED_ROUTING: (
                ControlPlaneCommandCodec.REPLACE_WEIGHTED_ROUTING_V1
            ),
        }
        return ControlPlaneVariableDescriptor(
            variable_name=self.reference(
                NodeControlGraphReferenceRole.VARIABLE,
                name,
            ),
            kind=kind,
            state_codec=state_codecs[kind],
            operation_contracts=(
                ControlPlaneVariableOperationContract(
                    NodeControlOperation.READ_STATE,
                    None,
                    ControlPlaneResultCodec.STATE_V1,
                ),
                ControlPlaneVariableOperationContract(
                    NodeControlOperation.APPLY_COMMAND,
                    command_codecs[kind],
                    ControlPlaneResultCodec.TRANSITION_V1,
                ),
            ),
            description=description,
        )

    def declaration(
        self,
        *names: str,
        socket: str = "control",
        kind: ControlPlaneVariableKind = ControlPlaneVariableKind.SCALAR,
        described: bool = True,
    ) -> WorkloadNodeControlSurfaceDeclaration:
        return WorkloadNodeControlSurfaceDeclaration(
            WorkloadNodeControlSurfaceDescriptor(
                provider_socket_name=self.reference(
                    NodeControlGraphReferenceRole.PROVIDER_SOCKET,
                    socket,
                ),
                variables=tuple(
                    self.variable(
                        name,
                        kind=kind,
                        description=f"Public {name}." if described else None,
                    )
                    for name in names
                ),
            )
        )

    def target(self, socket: str = "control") -> NodeControlTarget:
        return NodeControlTarget(
            workspace_id=self.reference(
                NodeControlGraphReferenceRole.WORKSPACE,
                "workspace-1",
            ),
            graph_revision=self.reference(
                NodeControlGraphReferenceRole.GRAPH_REVISION,
                "revision-7",
            ),
            node_id=self.reference(
                NodeControlGraphReferenceRole.NODE,
                "router",
            ),
            provider_socket_name=self.reference(
                NodeControlGraphReferenceRole.PROVIDER_SOCKET,
                socket,
            ),
        )

    def request(
        self,
        declaration: WorkloadNodeControlSurfaceDeclaration,
        kind: NodeControlSurfaceReadKind,
        *,
        request_id: str = "surface-read-1",
        target_socket: str | None = None,
    ) -> NodeControlSurfaceReadRequest:
        return NodeControlSurfaceReadRequest(
            target=self.target(
                target_socket
                if target_socket is not None
                else declaration.surface.provider_socket_name.value
            ),
            kind=kind,
            declaration_identity=declaration.identity(),
            request_id=request_id,
        )

    def status_context(self):
        fixture = self.fixture()["results"]["status_context"]
        declaration = WorkloadNodeControlSurfaceDeclarationCodec().decode(
            fixture["declaration"]["descriptor"]
        )
        request = NodeControlSurfaceReadRequestCodec().decode(
            fixture["request"]["descriptor"]
        )
        codec = self.contract("NodeControlSurfaceReadResultCodec")(
            request,
            declaration,
        )
        return declaration, request, codec

    def capabilities_context(self):
        fixture = self.fixture()
        declaration = WorkloadNodeControlSurfaceDeclarationCodec().decode(
            fixture["declaration"]["descriptor"]
        )
        request = NodeControlSurfaceReadRequestCodec().decode(
            fixture["request"]["descriptor"]
        )
        codec = self.contract("NodeControlSurfaceReadResultCodec")(
            request,
            declaration,
        )
        return declaration, request, codec

    def installed(self, *names: str):
        return tuple(
            self.reference(NodeControlGraphReferenceRole.VARIABLE, name)
            for name in names
        )

    def assert_vector(self, result, codec, vector) -> None:
        encoded = codec.encode(result)
        expected_bytes = vector["canonical_utf8"].encode("utf-8")
        self.assertEqual(encoded, vector["descriptor"])
        self.assertEqual(result.canonical_bytes(), expected_bytes)
        self.assertEqual(rfc8785.dumps(encoded), expected_bytes)
        self.assertEqual(
            hashlib.sha256(expected_bytes).hexdigest(),
            vector["sha256"],
        )
        self.assertEqual(codec.decode(encoded), result)

    def assert_context_vector(self, value, vector) -> None:
        expected_bytes = vector["canonical_utf8"].encode("utf-8")
        self.assertEqual(value.descriptor(), vector["descriptor"])
        self.assertEqual(value.canonical_bytes(), expected_bytes)
        self.assertEqual(rfc8785.dumps(value.descriptor()), expected_bytes)
        self.assertEqual(
            hashlib.sha256(expected_bytes).hexdigest(),
            vector["sha256"],
        )

    def test_nominal_variants_have_exact_request_bound_canonical_wire(self) -> None:
        module = self.result_module()
        fixture = self.fixture()["results"]
        declaration, request, capability_codec = self.capabilities_context()
        capability = capability_codec.capabilities_result()

        self.assertIsInstance(
            capability,
            module.NodeControlSurfaceCapabilitiesResult,
        )
        self.assertIs(capability.kind, NodeControlSurfaceReadKind.CAPABILITIES)
        self.assertEqual(capability.request, request)
        self.assertEqual(capability.declaration, declaration)
        self.assert_vector(
            capability,
            capability_codec,
            fixture["capabilities"],
        )

        status_declaration, status_request, status_codec = self.status_context()
        self.assert_context_vector(
            status_declaration,
            fixture["status_context"]["declaration"],
        )
        self.assert_context_vector(
            status_request,
            fixture["status_context"]["request"],
        )
        coverage = module.NodeControlSurfaceRegistryCoverage
        for key, names, expected_coverage in (
            ("status_none", (), coverage.NONE),
            ("status_partial", ("alpha",), coverage.PARTIAL),
            ("status_complete", ("alpha", "beta"), coverage.COMPLETE),
        ):
            with self.subTest(key=key):
                result = status_codec.status_result(self.installed(*names))
                self.assertIsInstance(
                    result,
                    module.NodeControlSurfaceStatusResult,
                )
                self.assertIs(result.kind, NodeControlSurfaceReadKind.STATUS)
                self.assertEqual(result.request, status_request)
                self.assertIs(result.registry_coverage, expected_coverage)
                self.assert_vector(result, status_codec, fixture[key])

        self.assertEqual(
            frozenset(get_args(module.NodeControlSurfaceReadResult)),
            frozenset(
                (
                module.NodeControlSurfaceCapabilitiesResult,
                module.NodeControlSurfaceStatusResult,
                )
            ),
        )

    def test_public_values_derive_claims_and_reject_invalid_context(self) -> None:
        module = self.result_module()
        capability_type = module.NodeControlSurfaceCapabilitiesResult
        status_type = module.NodeControlSurfaceStatusResult
        profile = module.NodeControlSurfaceReadResultProfile
        declaration, request, codec = self.capabilities_context()

        self.assertEqual(
            tuple(field.name for field in fields(capability_type)),
            ("request", "declaration"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(status_type)),
            ("request", "declaration", "installed_variable_names"),
        )
        result = capability_type(request, declaration)
        self.assertIs(result.profile, profile.V1)
        self.assertIs(
            result.canonicalization,
            NodeControlCanonicalization.JCS_RFC8785_V1,
        )
        self.assertEqual(result.request_id, request.request_id)
        self.assertEqual(result.request_digest, request.canonical_digest())
        self.assertEqual(result.declaration_identity, declaration.identity())
        self.assertEqual(codec.encode(result), result.descriptor())

        status_request = replace(request, kind=NodeControlSurfaceReadKind.STATUS)
        with self.assertRaisesRegex(NodeControlSurfaceReadContractError, "kind"):
            capability_type(status_request, declaration)
        with self.assertRaisesRegex(NodeControlSurfaceReadContractError, "kind"):
            status_type(request, declaration, ())

        mismatched_declaration = self.declaration("other")
        with self.assertRaisesRegex(
            NodeControlSurfaceReadContractError,
            "declaration",
        ):
            capability_type(request, mismatched_declaration)

        socket_declaration = self.declaration("mode", socket="other-control")
        socket_request = self.request(
            socket_declaration,
            NodeControlSurfaceReadKind.CAPABILITIES,
            target_socket="control",
        )
        with self.assertRaisesRegex(NodeControlSurfaceReadContractError, "socket"):
            capability_type(socket_request, socket_declaration)
        with self.assertRaisesRegex(NodeControlSurfaceReadContractError, "socket"):
            module.NodeControlSurfaceReadResultCodec(
                socket_request,
                socket_declaration,
            )

        for values in (
            ("request", declaration),
            (request, "declaration"),
        ):
            with self.subTest(values=values):
                with self.assertRaises(NodeControlSurfaceReadContractError):
                    capability_type(*values)
                with self.assertRaises(NodeControlSurfaceReadContractError):
                    module.NodeControlSurfaceReadResultCodec(*values)

    def test_codec_rejects_cross_kind_request_and_declaration_substitution(self) -> None:
        _, request, capability_codec = self.capabilities_context()
        capability = capability_codec.capabilities_result()
        declaration, status_request, status_codec = self.status_context()
        status = status_codec.status_result(self.installed("alpha"))

        with self.assertRaises(NodeControlSurfaceReadContractError):
            status_codec.encode(capability)
        with self.assertRaises(NodeControlSurfaceReadContractError):
            capability_codec.encode(status)
        with self.assertRaises(NodeControlSurfaceReadContractError):
            status_codec.decode(capability_codec.encode(capability))
        with self.assertRaises(NodeControlSurfaceReadContractError):
            capability_codec.decode(status_codec.encode(status))

        codec_type = self.contract("NodeControlSurfaceReadResultCodec")
        other_request = replace(status_request, request_id="surface-status-2")
        other_codec = codec_type(other_request, declaration)
        with self.assertRaisesRegex(NodeControlSurfaceReadContractError, "request"):
            other_codec.encode(status)

        descriptor = status_codec.encode(status)
        substitutions = (
            ("request_id", "surface-status-2"),
            ("request_digest", "f" * 64),
            ("kind", "capabilities"),
            ("declaration_identity", "f" * 64),
            ("profile", "workload-node-control-surface-read-result.v2"),
            ("canonicalization", "unknown"),
        )
        for key, value in substitutions:
            with self.subTest(key=key):
                with self.assertRaises(NodeControlSurfaceReadContractError):
                    status_codec.decode({**descriptor, key: value})

        wrong_identity_request = replace(
            request,
            declaration_identity=WorkloadNodeControlSurfaceDeclarationIdentity(
                "f" * 64
            ),
        )
        with self.assertRaisesRegex(
            NodeControlSurfaceReadContractError,
            "declaration",
        ):
            codec_type(wrong_identity_request, capability.declaration)

        substituted_declaration = self.declaration("node")
        self.assertEqual(
            len(substituted_declaration.canonical_bytes()),
            len(capability.declaration.canonical_bytes()),
        )
        substituted_wire = {
            **capability_codec.encode(capability),
            "declaration": substituted_declaration.descriptor(),
        }
        self.assertEqual(
            len(rfc8785.dumps(substituted_wire)),
            len(capability.canonical_bytes()),
        )
        with self.assertRaisesRegex(
            NodeControlSurfaceReadContractError,
            "declaration",
        ):
            capability_codec.decode(substituted_wire)

    def test_status_coverage_is_total_canonical_and_non_authoritative(self) -> None:
        module = self.result_module()
        declaration, request, codec = self.status_context()
        result_type = module.NodeControlSurfaceStatusResult

        for names, expected in (
            ((), module.NodeControlSurfaceRegistryCoverage.NONE),
            (("alpha",), module.NodeControlSurfaceRegistryCoverage.PARTIAL),
            (
                ("alpha", "beta"),
                module.NodeControlSurfaceRegistryCoverage.COMPLETE,
            ),
        ):
            result = result_type(request, declaration, self.installed(*names))
            self.assertIs(result.registry_coverage, expected)

        invalid_values = (
            self.installed("beta", "alpha"),
            self.installed("alpha", "alpha"),
            self.installed("alpha", "gamma"),
            (
                self.reference(NodeControlGraphReferenceRole.NODE, "alpha"),
            ),
            [self.reference(NodeControlGraphReferenceRole.VARIABLE, "alpha")],
        )
        for installed in invalid_values:
            with self.subTest(installed=installed):
                with self.assertRaises(NodeControlSurfaceReadContractError):
                    result_type(request, declaration, installed)

        valid = codec.encode(codec.status_result(self.installed("alpha")))
        invalid_wire = (
            {**valid, "registry_coverage": "none"},
            {**valid, "registry_coverage": "complete"},
            {**valid, "registry_coverage": "unknown"},
            {**valid, "installed_variable_names": ["beta", "alpha"]},
            {**valid, "installed_variable_names": ["alpha", "alpha"]},
            {**valid, "installed_variable_names": ["alpha", "gamma"]},
            {**valid, "installed_variable_names": [1]},
            {**valid, "installed_variable_names": "alpha"},
        )
        for descriptor in invalid_wire:
            with self.subTest(descriptor=descriptor):
                with self.assertRaises(NodeControlSurfaceReadContractError):
                    codec.decode(descriptor)

    def test_codecs_are_strict_and_forbid_runtime_or_secret_material(self) -> None:
        _, _, capability_codec = self.capabilities_context()
        _, _, status_codec = self.status_context()
        descriptors = (
            capability_codec.encode(capability_codec.capabilities_result()),
            status_codec.encode(status_codec.status_result(self.installed("alpha"))),
        )
        hostile = {
            "state": {"value": "sk-attacker"},
            "version": 4,
            "evidence": {"code": "applied"},
            "payload": "token=attacker",
            "endpoint": "https://attacker.invalid",
            "signature": "attacker-signature",
            "error": "provider diagnostic",
            "target": {"node_id": "router"},
            "registry": ["alpha"],
            "health": "healthy",
            "readiness": "ready",
        }
        for codec, descriptor in zip(
            (capability_codec, status_codec),
            descriptors,
            strict=True,
        ):
            for key in descriptor:
                with self.subTest(kind=descriptor["kind"], missing=key):
                    candidate = deepcopy(descriptor)
                    del candidate[key]
                    with self.assertRaises(NodeControlSurfaceReadContractError):
                        codec.decode(candidate)
            for key, value in hostile.items():
                with self.subTest(kind=descriptor["kind"], hostile=key):
                    with self.assertRaises(NodeControlSurfaceReadContractError):
                        codec.decode({**descriptor, key: value})

        wrong_types = (
            (capability_codec, {**descriptors[0], "declaration": []}),
            (status_codec, {**descriptors[1], "request_id": 1}),
            (status_codec, {**descriptors[1], "request_digest": None}),
            (status_codec, {**descriptors[1], "declaration_identity": []}),
        )
        for codec, descriptor in wrong_types:
            with self.subTest(descriptor=descriptor):
                with self.assertRaises(NodeControlSurfaceReadContractError):
                    codec.decode(descriptor)

        with self.assertRaises(NodeControlSurfaceReadContractError):
            status_codec.encode(object())

    def maximum_capability_declaration(
        self,
    ) -> WorkloadNodeControlSurfaceDeclaration:
        description_lengths = [512] * 17 + [430, 1]
        return WorkloadNodeControlSurfaceDeclaration(
            WorkloadNodeControlSurfaceDescriptor(
                provider_socket_name=self.reference(
                    NodeControlGraphReferenceRole.PROVIDER_SOCKET,
                    "control",
                ),
                variables=tuple(
                    self.variable(
                        f"variable-{index:03d}",
                        description="x" * length,
                    )
                    for index, length in enumerate(description_lengths)
                ),
            )
        )

    def maximum_status_declaration(
        self,
    ) -> WorkloadNodeControlSurfaceDeclaration:
        names = tuple(
            f"v{index:03d}" + "x" * 124
            for index in range(33)
        )
        return self.declaration(
            *names,
            socket="c",
            kind=ControlPlaneVariableKind.MAP,
            described=False,
        )

    def padded_candidate(self, descriptor, key: str, maximum: int):
        candidate = {**descriptor, key: ""}
        padding = maximum + 1 - len(rfc8785.dumps(candidate))
        self.assertGreaterEqual(padding, 0)
        candidate[key] = "x" * padding
        self.assertEqual(len(rfc8785.dumps(candidate)), maximum + 1)
        return candidate

    def test_global_and_context_bounds_are_reachable_and_precede_nested_values(self) -> None:
        module = self.result_module()
        codec_type = module.NodeControlSurfaceReadResultCodec
        self.assertEqual(
            module.MAX_NODE_CONTROL_SURFACE_CAPABILITIES_RESULT_BYTES,
            16_902,
        )
        self.assertEqual(
            module.MAX_NODE_CONTROL_SURFACE_STATUS_RESULT_BYTES,
            4_811,
        )

        capability_declaration = self.maximum_capability_declaration()
        capability_request = self.request(
            capability_declaration,
            NodeControlSurfaceReadKind.CAPABILITIES,
            request_id="r" * 128,
        )
        capability_codec = codec_type(
            capability_request,
            capability_declaration,
        )
        capability = capability_codec.capabilities_result()
        self.assertEqual(len(capability_declaration.canonical_bytes()), 16_453)
        self.assertEqual(len(capability.canonical_bytes()), 16_902)

        status_declaration = self.maximum_status_declaration()
        status_request = self.request(
            status_declaration,
            NodeControlSurfaceReadKind.STATUS,
            request_id="r" * 128,
        )
        status_codec = codec_type(status_request, status_declaration)
        installed = tuple(
            variable.variable_name
            for variable in status_declaration.surface.variables
        )
        status = status_codec.status_result(installed)
        self.assertEqual(
            len(rfc8785.dumps(status_declaration.surface.descriptor())),
            16_146,
        )
        self.assertEqual(len(status.canonical_bytes()), 4_811)

        for codec, result, key, maximum in (
            (
                capability_codec,
                capability,
                "declaration",
                module.MAX_NODE_CONTROL_SURFACE_CAPABILITIES_RESULT_BYTES,
            ),
            (
                status_codec,
                status,
                "installed_variable_names",
                module.MAX_NODE_CONTROL_SURFACE_STATUS_RESULT_BYTES,
            ),
        ):
            candidate = self.padded_candidate(
                codec.encode(result),
                key,
                maximum,
            )
            with self.assertRaisesRegex(
                NodeControlSurfaceReadContractError,
                "aggregate exceeds.*bound",
            ):
                codec.decode(candidate)

        _, _, small_status_codec = self.status_context()
        complete = small_status_codec.encode(
            small_status_codec.status_result(self.installed("alpha", "beta"))
        )
        context_plus_one = {
            **complete,
            "request_id": complete["request_id"] + "x",
        }
        with self.assertRaisesRegex(
            NodeControlSurfaceReadContractError,
            "context.*bound",
        ):
            small_status_codec.decode(context_plus_one)

        _, _, small_capability_codec = self.capabilities_context()
        small_capability = small_capability_codec.encode(
            small_capability_codec.capabilities_result()
        )
        capability_context_plus_one = {
            **small_capability,
            "request_id": small_capability["request_id"] + "x",
        }
        self.assertLess(
            len(rfc8785.dumps(capability_context_plus_one)),
            module.MAX_NODE_CONTROL_SURFACE_CAPABILITIES_RESULT_BYTES,
        )
        with self.assertRaisesRegex(
            NodeControlSurfaceReadContractError,
            "context.*bound",
        ):
            small_capability_codec.decode(capability_context_plus_one)

        count_plus_one = {
            **status_codec.encode(status),
            "installed_variable_names": [
                f"n{index:03d}" for index in range(129)
            ],
        }
        self.assertLess(
            len(rfc8785.dumps(count_plus_one)),
            module.MAX_NODE_CONTROL_SURFACE_STATUS_RESULT_BYTES,
        )
        with self.assertRaisesRegex(
            NodeControlSurfaceReadContractError,
            "too many",
        ):
            status_codec.decode(count_plus_one)

    def test_failures_are_categorical_cause_free_and_repr_safe(self) -> None:
        _, _, codec = self.status_context()
        canary = "opaqueStatusCanary"
        result = codec.status_result(self.installed("alpha"))
        descriptor = codec.encode(result)

        with self.assertRaises(NodeControlSurfaceReadContractError) as caught:
            codec.decode({**descriptor, canary: "sk-attacker-value"})
        rendered = f"{caught.exception!s} {caught.exception!r}"
        self.assertNotIn(canary, rendered)
        self.assertNotIn("sk-attacker-value", rendered)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

        _, _, capability_codec = self.capabilities_context()
        capability = capability_codec.encode(
            capability_codec.capabilities_result()
        )
        malformed_declaration = deepcopy(capability)
        malformed_declaration["declaration"]["surface"]["variables"][0][
            "description"
        ] = "sk-nested"
        nested_cases = (
            (
                capability_codec,
                malformed_declaration,
                "sk-nested",
                "declaration.*malformed",
            ),
            (
                codec,
                {
                    **descriptor,
                    "installed_variable_names": ["sk-x"],
                },
                "sk-x",
                "installed.*malformed",
            ),
        )
        for candidate_codec, candidate, nested_canary, category in nested_cases:
            with self.subTest(category=category):
                with self.assertRaisesRegex(
                    NodeControlSurfaceReadContractError,
                    category,
                ) as nested:
                    candidate_codec.decode(candidate)
                nested_rendered = f"{nested.exception!s} {nested.exception!r}"
                self.assertNotIn(nested_canary, nested_rendered)
                self.assertIsNone(nested.exception.__cause__)
                self.assertIsNone(nested.exception.__context__)

        representation = repr(result)
        self.assertNotIn(result.request_id, representation)
        self.assertNotIn("Public alpha.", representation)
        self.assertNotIn("alpha", representation)
        self.assertNotIn("workspace-1", representation)

        capability_result = capability_codec.capabilities_result()
        capability_representation = repr(capability_result)
        self.assertNotIn(
            capability_result.request_id,
            capability_representation,
        )
        self.assertNotIn("Public mode.", capability_representation)
        self.assertNotIn("mode", capability_representation)
        self.assertNotIn("workspace-1", capability_representation)

    def test_root_exports_module_inventory_and_import_boundary_are_exact(self) -> None:
        module = self.result_module()
        public_names = (
            "MAX_NODE_CONTROL_SURFACE_CAPABILITIES_RESULT_BYTES",
            "MAX_NODE_CONTROL_SURFACE_STATUS_RESULT_BYTES",
            "NodeControlSurfaceCapabilitiesResult",
            "NodeControlSurfaceReadResult",
            "NodeControlSurfaceReadResultCodec",
            "NodeControlSurfaceReadResultProfile",
            "NodeControlSurfaceRegistryCoverage",
            "NodeControlSurfaceStatusResult",
        )
        for name in public_names:
            with self.subTest(name=name):
                self.assertIs(getattr(core, name, None), getattr(module, name))
                self.assertIn(name, module.__all__)

        source = Path(module.__file__)
        tree = ast.parse(source.read_text(encoding="utf-8"))
        imports = {
            node.module.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        imports.update(
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        self.assertTrue(
            imports.isdisjoint(
                {"docker", "fastapi", "httpx", "mcp", "psycopg", "uvicorn"}
            )
        )

        inventory = (
            Path(__file__).parent / "test_milestone_closeout.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"node_control_surface_read_results"', inventory)


if __name__ == "__main__":
    unittest.main()
