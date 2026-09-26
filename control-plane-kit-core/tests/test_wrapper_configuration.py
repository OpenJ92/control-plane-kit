"""Shared wrapper wire and artifact binding; no SDK host or Operations policy."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import importlib
import importlib.util
import json
import unittest

import rfc8785

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
from control_plane_kit_core.configuration import ConfigurationArtifact, ConfigurationFileMode, ConfigurationMediaType
from control_plane_kit_core.delegation_keys import DelegationKeyAlgorithm, DelegationKeyPurpose, DelegationPublicKey
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.node_control import NodeControlGraphReference, NodeControlGraphReferenceRole, NodeControlTarget
from control_plane_kit_core.products import ProductDescriptorCodec, ProductIdentity, ProductInstanceConfiguration, instantiate_product
from control_plane_kit_core.topology import GraphDescriptorCodec, compile_topology
from tests.test_delegation_keys import PUBLIC_KEY_A
from tests.test_node_health_declarations import NodeHealthDeclarationTests
from tests.test_node_control_surfaces import WorkloadNodeControlSurfaceTests
from control_plane_kit_core.node_control_surface_reads import WorkloadNodeControlSurfaceDeclaration

MODULE = "control_plane_kit_core.wrapper_configuration"
PROFILE = "workload-node-control-configuration.v1"
PATH_KEY = "CPK_WRAPPER_CONFIGURATION_FILE"


class WrapperConfigurationTests(unittest.TestCase):
    def setUp(self):
        # Establish existing typed fixtures before the intentional missing API assertion.
        self.declaration = NodeHealthDeclarationTests().declaration()
        self.target = NodeControlTarget(*(
            NodeControlGraphReference(role, name) for role, name in (
                (NodeControlGraphReferenceRole.WORKSPACE, "workspace-a"),
                (NodeControlGraphReferenceRole.GRAPH_REVISION, "revision-a"),
                (NodeControlGraphReferenceRole.NODE, "service-a"),
                (NodeControlGraphReferenceRole.PROVIDER_SOCKET, "control"))))
        self.runtime = NodeControlGraphReference(NodeControlGraphReferenceRole.RUNTIME, "runtime-a")
        self.key = DelegationPublicKey("public-a", DelegationKeyAlgorithm.ED25519, PUBLIC_KEY_A)
        self.product = NodeHealthDeclarationTests().product(self.declaration.surface)
        self.assertIsNotNone(importlib.util.find_spec(MODULE), "shared wrapper configuration is not implemented")
        self.api = importlib.import_module(MODULE)
        self.codec = self.api.WorkloadNodeControlConfigurationCodec()

    def document(self, declaration=None):
        declaration = self.declaration if declaration is None else declaration
        purposes = [DelegationKeyPurpose.WORKLOAD_NODE_CONTROL_SURFACE_READ]
        if declaration.surface.variables:
            purposes.append(DelegationKeyPurpose.WORKLOAD_NODE_CONTROL)
        if declaration.surface.health_reads:
            purposes.append(DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ)
        return {"profile": PROFILE, "target": self.target.descriptor(),
                "runtime_id": self.runtime.value, "declaration": declaration.descriptor(),
                "verifiers": [{"purpose": purpose.value, "issuer": "issuer-" + str(index),
                    "public_keys": [{"key_id": self.key.key_id, "algorithm": "ed25519",
                                     "public_key_pem": self.key.public_key_pem}]}
                    for index, purpose in enumerate(sorted(purposes))]}

    def refusal(self, action):
        with self.assertRaises(self.api.WrapperConfigurationError) as caught:
            action()
        self.assertEqual(str(caught.exception), "wrapper configuration is invalid")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertEqual(vars(caught.exception), {})

    def artifact(self, artifact_id="receiver", path="/etc/example/control.json", document=None):
        content = self.codec.encode_bytes(self.codec.decode(self.document() if document is None else document))
        return ConfigurationArtifact(artifact_id, path, ConfigurationMediaType.JSON, content.decode())

    def select(self, artifacts, environment=None, surfaces=None):
        if environment is None:
            environment = (PublicStaticEnvironmentBinding(PATH_KEY, artifacts[0].target_path),)
        if surfaces is None:
            surfaces = (self.declaration.surface,)
        return self.api.select_workload_node_control_configuration_artifact(
            artifacts=artifacts, environment=environment, control_surfaces=surfaces)

    def test_shared_wire_round_trip_is_canonical_and_preserves_configured_facts(self):
        document = self.document()
        value = self.codec.decode(document)
        self.assertEqual(value.target, self.target)
        self.assertEqual(value.runtime_id, self.runtime)
        self.assertEqual(value.declaration, self.declaration)
        self.assertEqual({v.purpose for v in value.verifiers}, {
            DelegationKeyPurpose.WORKLOAD_NODE_CONTROL_SURFACE_READ,
            DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ})
        self.assertEqual(self.codec.encode(value), document)
        self.assertEqual(self.codec.encode_bytes(value), rfc8785.dumps(document))
        self.assertEqual(self.codec.decode_bytes(json.dumps(document, indent=2).encode()), value)
        document["target"]["node_id"] = "service-b"
        document["verifiers"][1]["issuer"] = "issuer-b"
        other = self.codec.decode(document)
        self.assertNotEqual(value.target, other.target)
        self.assertNotEqual(value.verifiers, other.verifiers)
        self.assertEqual(value.target, self.target)
        self.assertNotIn(self.key.public_key_pem, repr(value))
        with self.assertRaises(FrozenInstanceError):
            value.runtime_id = self.runtime

    def test_existing_variable_only_and_mixed_declarations_require_exact_key_families(self):
        legacy = WorkloadNodeControlSurfaceDeclaration(WorkloadNodeControlSurfaceTests().surface("control", "mode"))
        mixed = NodeHealthDeclarationTests().declaration("mode")
        for declaration in (legacy, self.declaration, mixed):
            document = self.document(declaration)
            value = self.codec.decode(document)
            self.assertEqual(self.codec.decode_bytes(self.codec.encode_bytes(value)), value)
            for index in range(len(document["verifiers"])):
                missing = deepcopy(document)
                del missing["verifiers"][index]
                self.refusal(lambda: self.codec.decode(missing))
            duplicate = deepcopy(document)
            duplicate["verifiers"].append(deepcopy(duplicate["verifiers"][0]))
            self.refusal(lambda: self.codec.decode(duplicate))
        unknown = self.document()
        unknown["verifiers"][0]["purpose"] = "gateway-node-health-read-transit"
        self.refusal(lambda: self.codec.decode(unknown))
        surplus = self.document()
        command = deepcopy(surplus["verifiers"][0])
        command["purpose"] = "workload-node-control"
        surplus["verifiers"].append(command)
        self.refusal(lambda: self.codec.decode(surplus))

    def test_closed_bounded_input_refuses_unknown_profiles_and_material_without_leaks(self):
        original = self.document()
        cases = []
        for name, value in (("profile", "workload-node-control-configuration.v2"),
                            ("extra", "private-canary"), ("runtime_id", ""),
                            ("runtime_id", 1), ("verifiers", []), ("verifiers", {})):
            cases.append({**original, name: value})
        for field, value in (("issuer", "token=private-canary"), ("purpose", "unknown"),
                             ("public_keys", []), ("public_keys", original["verifiers"][0]["public_keys"] * 17)):
            candidate = deepcopy(original)
            candidate["verifiers"][0][field] = value
            cases.append(candidate)
        for field, value in (("algorithm", "unknown"), ("key_id", ""),
                             ("public_key_pem", "-----BEGIN PRIVATE KEY-----\nprivate-canary\n-----END PRIVATE KEY-----"),
                             ("extra", True)):
            candidate = deepcopy(original)
            candidate["verifiers"][0]["public_keys"][0][field] = value
            cases.append(candidate)
        mismatch = deepcopy(original)
        mismatch["target"]["provider_socket_name"] = "other"
        cases.append(mismatch)
        for candidate in cases:
            self.refusal(lambda: self.codec.decode(candidate))
        encoded = json.dumps(original).encode()
        raw_cases = (b"", b"\xff", b" " * 65537, b"[]", b"null", b'{"x":NaN}',
                     encoded.replace(b'"profile":', b'"profile":"duplicate","profile":', 1),
                     b"[" * 1000 + b"]" * 1000)
        for raw in raw_cases:
            self.refusal(lambda: self.codec.decode_bytes(raw))
        self.refusal(lambda: self.codec.decode_bytes(encoded.decode()))

    def test_key_families_are_unique_canonical_and_do_not_mutate_caller_inputs(self):
        document = self.document()
        second = {"key_id": "public-b", "algorithm": "ed25519",
                  "public_key_pem": PUBLIC_KEY_A.replace("aaaaaaaa", "bbbbbbbb")}
        document["verifiers"][0]["public_keys"].insert(0, second)
        value = self.codec.decode(document)
        self.assertEqual([key.key_id for key in value.verifiers[0].public_keys], ["public-a", "public-b"])
        self.assertEqual(document["verifiers"][0]["public_keys"][0]["key_id"], "public-b")
        for duplicate in ({**second, "key_id": "public-a"},
                          {**second, "public_key_pem": self.key.public_key_pem}):
            candidate = self.document()
            candidate["verifiers"][0]["public_keys"].append(duplicate)
            self.refusal(lambda: self.codec.decode(candidate))
        self.refusal(lambda: replace(value, runtime_id=NodeControlGraphReference(NodeControlGraphReferenceRole.NODE, "runtime-a")))
        self.refusal(lambda: replace(value, verifiers=list(value.verifiers)))
        self.refusal(lambda: replace(value.verifiers[0], purpose=DelegationKeyPurpose.GATEWAY_PROBE))

    def test_independent_products_and_paths_use_existing_descriptor_and_graph_delivery(self):
        self.assertEqual(self.api.WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT, PATH_KEY)
        for name, path in (("weather", "/opt/weather/receiver.json"), ("inventory", "/etc/inventory/management.json")):
            artifact = self.artifact(name + "-settings", path)
            environment = (PublicStaticEnvironmentBinding(PATH_KEY, path),)
            contract = replace(self.product.runtime_contract, configuration_artifacts=(artifact,), public_environment=environment)
            product = replace(self.product, identity=ProductIdentity("independent", name, 1), runtime_contract=contract)
            product_codec = ProductDescriptorCodec()
            restored = product_codec.decode_document(product_codec.encode_document(product).content).product
            self.assertEqual(restored.runtime_contract.configuration_artifacts, (artifact,))
            block = instantiate_product(product, "service-a", ProductInstanceConfiguration.from_contract(contract))
            graph = compile_topology(DeploymentTopology(name, DockerRuntime(children=(block,))))
            node = GraphDescriptorCodec().decode(GraphDescriptorCodec().encode(graph)).node("service-a")
            self.assertEqual(self.select(node.configuration_artifacts, node.public_environment, node.block_spec.control_surfaces), artifact)
            self.assertEqual(self.codec.decode_bytes(artifact.content.encode()).target, self.target)

    def test_artifact_selection_is_exact_unambiguous_and_ignores_unrelated_application_files(self):
        artifact = self.artifact()
        other = ConfigurationArtifact("application", "/etc/application/config.txt", ConfigurationMediaType.TEXT, "workers=2")
        environment = (PublicStaticEnvironmentBinding("APP_MODE", "ready"), PublicStaticEnvironmentBinding(PATH_KEY, artifact.target_path))
        self.assertIs(self.select((other, artifact), environment), artifact)
        cases = (((), environment, (self.declaration.surface,)),
                 ((artifact,), (), (self.declaration.surface,)),
                 ((artifact,), environment + (environment[-1],), (self.declaration.surface,)),
                 ((artifact,), (PublicStaticEnvironmentBinding(PATH_KEY, other.target_path),), (self.declaration.surface,)),
                 ((artifact,), environment, ()),
                 ((artifact,), environment, (self.declaration.surface, self.declaration.surface)),
                 ((artifact, artifact), environment, (self.declaration.surface,)))
        for artifacts, env, surfaces in cases:
            self.refusal(lambda: self.select(artifacts, env, surfaces))
        for changed in (replace(artifact, file_mode=ConfigurationFileMode.OWNER_READ_ONLY),
                        replace(artifact, media_type=ConfigurationMediaType.TEXT),
                        replace(artifact, content=artifact.content.replace(PROFILE, "workload-node-control-configuration.v2"), source_digest=None)):
            self.refusal(lambda: self.select((changed,)))
        other_surface = replace(self.declaration.surface, provider_socket_name=NodeControlGraphReference(NodeControlGraphReferenceRole.PROVIDER_SOCKET, "other"))
        self.refusal(lambda: self.select((artifact,), surfaces=(other_surface,)))


if __name__ == "__main__":
    unittest.main()
