"""Tiny byte-decoder port witness; PostgreSQL owners retain all transitions."""
from dataclasses import replace
import base64
import importlib
import importlib.util
import json

from control_plane_kit_core.configuration import ConfigurationArtifact, ConfigurationFileMode, ConfigurationMediaType
from control_plane_kit_core.delegation_keys import DelegationKeyAlgorithm, DelegationKeyPurpose, DelegationPublicKey
from control_plane_kit_core.node_control import NodeControlGraphReference, NodeControlGraphReferenceRole, NodeControlTarget, workload_node_control_audience
from control_plane_kit_core.node_control_surface_reads import WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationCodec, WorkloadNodeControlSurfaceDeclarationProfile
from control_plane_kit_core.planning import ActivityPlan, ObserveNodeHealth, PlanGraphSide, compile_graph_activity_plan
from control_plane_kit_core.products import ContainerServerProduct, OciImageReference, ProductDescriptorCodec, ProductIdentity, ProductReference, ProductRuntimeContract, ProviderRuntimePort
from control_plane_kit_core.topology import DeploymentGraph, validate_graph
from control_plane_kit_operations.products import ImportProductDescriptorCommand, InlineDescriptorSource, ProductRegistrationService
from control_plane_kit_operations.runtime_management_targets import project_management_health_target
from tests.runtime_management_fixtures import bootstrap_management_graph
from tests.test_delegation_signing_keys import PUBLIC_KEY_A, PUBLIC_KEY_B

PUBLIC_KEY_C = ("-----BEGIN PUBLIC KEY-----\n" + base64.b64encode(
    bytes.fromhex("302a300506032b6570032100") + bytes([3]) * 32).decode("ascii")
    + "\n-----END PUBLIC KEY-----\n")

MODULE = "control_plane_kit_operations.health_receiver_trust"
PROFILE = "test-health-receiver.v1"
PURPOSES = {"transit": DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT,
            "workload": DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ}
PEMS = {"transit": PUBLIC_KEY_A, "workload": PUBLIC_KEY_B, "other": PUBLIC_KEY_C}
NODES = {"transit": "gateway", "workload": "api"}


def api(test):
    test.assertIsNotNone(importlib.util.find_spec(MODULE), "#1857 receiver trust contract is missing")
    return importlib.import_module(MODULE)


def reference(role, value):
    return NodeControlGraphReference(NodeControlGraphReferenceRole(role), value)


def public_key(family, *, key_id=None, pem=None):
    return DelegationPublicKey(key_id or "health-" + family, DelegationKeyAlgorithm.ED25519,
                               PEMS[family] if pem is None else pem)


def artifact(family, surface_declaration, *, revision="health-desired", **changes):
    value = dict(profile=PROFILE, family=family, workspace="workspace-a", node=NODES[family],
        runtime="docker", issuer="cpk-server", purpose=PURPOSES[family].value,
        revision=revision, socket="http", declaration=surface_declaration.descriptor(),
        keys=[dict(key_id="health-" + family, pem=PEMS[family])])
    value.update(changes)
    return ConfigurationArtifact("test-" + family, "/etc/test/" + family + ".json",
        ConfigurationMediaType.JSON, json.dumps(value, sort_keys=True), ConfigurationFileMode.READ_ONLY)


class ByteDecoder:
    """Only interprets this fixture's bytes; knows no expected selection identities."""

    def __init__(self, contract):
        self.contract = contract
        self.calls = []

    def decode(self, selection):
        self.calls.append(selection)
        value = json.loads(selection.artifact.content)
        if value["profile"] != PROFILE:
            raise self.contract.HealthReceiverTrustError("health receiver trust is unavailable")
        keys = tuple(DelegationPublicKey(item["key_id"], DelegationKeyAlgorithm.ED25519, item["pem"])
                     for item in value["keys"])
        common = dict(runtime_id=reference("runtime", value["runtime"]), issuer=value["issuer"],
            purpose=DelegationKeyPurpose(value["purpose"]), public_keys=keys)
        if value["family"] == "transit":
            return self.contract.GatewayHealthReceiverTrust(
                workspace_id=reference("workspace", value["workspace"]),
                gateway_node_id=reference("node", value["node"]),
                audience=f"gateway:{value['workspace']}:{value['node']}", **common)
        target = NodeControlTarget(reference("workspace", value["workspace"]),
            reference("graph-revision", value["revision"]), reference("node", value["node"]),
            reference("provider-socket", value["socket"]))
        return self.contract.WorkloadHealthReceiverTrust(target=target,
            declaration=WorkloadNodeControlSurfaceDeclarationCodec().decode(value["declaration"]),
            audience=workload_node_control_audience(target), **common)


def context(test, *, side=PlanGraphSide.DESIRED_GRAPH, changes=None, slot_changes=None):
    graph = bootstrap_management_graph(test)
    declaration = WorkloadNodeControlSurfaceDeclaration(graph.node("api").block_spec.control_surfaces[0],
        WorkloadNodeControlSurfaceDeclarationProfile.V2)
    documents, selected = {}, {}
    nodes = dict(graph.nodes)
    revision = "health-base" if side is PlanGraphSide.BASE_GRAPH else "health-desired"
    for family, node_id in NODES.items():
        node = graph.node(node_id)
        chosen = artifact(family, declaration, **({"revision": revision} | (changes or {}).get(family, {})))
        selected[family] = replace(chosen, **(slot_changes or {}).get(family, {}))
        # Deliberately wrong default makes accidental descriptor fallback visible.
        default = artifact(family, declaration, keys=[dict(key_id="default-only", pem=PUBLIC_KEY_C)])
        contract = ProductRuntimeContract(sockets=node.sockets,
            provider_ports=(ProviderRuntimePort("http", 8000),),
            capabilities=node.block_spec.capabilities, control_surfaces=node.block_spec.control_surfaces,
            gateway_transit=node.block_spec.gateway_transit, configuration_artifacts=(default,))
        document = ProductDescriptorCodec().encode_document(ContainerServerProduct(
            ProductIdentity("test", "health-" + family, 1),
            OciImageReference("ghcr.io", "test/health-" + family, "sha256:" + "b" * 64), contract))
        documents[family] = document
        ref = ProductReference.from_document(document)
        nodes[node_id] = replace(node, configuration_artifacts=(selected[family],), metadata={
            **node.metadata, "product_identity": ref.identity.key,
            "product_descriptor_digest": ref.descriptor_sha256.value})
    desired = validate_graph(replace(graph, nodes=nodes))
    current = validate_graph(DeploymentGraph(graph.name))
    current.require_valid()
    desired.require_valid()
    plan = compile_graph_activity_plan(current, desired)
    activity = next(item for item in plan.activities
        if type(item.operation) is ObserveNodeHealth and item.operation.node_id == "api")
    if side is PlanGraphSide.BASE_GRAPH:
        operation = replace(activity.operation, target=replace(activity.operation.target, graph_side=side))
        plan = ActivityPlan(tuple(replace(item, operation=operation) if item == activity else item
                                  for item in plan.activities))
        activity = plan.activity(activity.activity_id)
        current = desired
    projected = project_management_health_target(plan, activity.activity_id, activity.operation, current, desired)
    return (plan, activity, current, desired, projected), documents, selected


def register_products(test, documents):
    service = ProductRegistrationService(test.unit_of_work)
    return {family: service.import_descriptor(ImportProductDescriptorCommand(
        workspace_id="workspace-a", descriptor_document=document, source=InlineDescriptorSource(),
        imported_by="operator-a", imported_at="2026-08-01T10:00:00Z"))
        for family, document in documents.items()}


def bindings(contract, documents, decoder):
    return tuple(contract.HealthReceiverDecoderBinding(
        product_reference=ProductReference.from_document(document), purpose=PURPOSES[family],
        configuration_profile=PROFILE, artifact_id="test-" + family,
        target_path="/etc/test/" + family + ".json", media_type=ConfigurationMediaType.JSON,
        file_mode=ConfigurationFileMode.READ_ONLY, decoder=decoder)
        for family, document in documents.items())


def selection(contract, document, chosen, family="transit"):
    return contract.HealthReceiverSelection(workspace_id="workspace-a", authored_graph_id="health-desired",
        realized_projection_id="projection-desired", graph_side=PlanGraphSide.DESIRED_GRAPH,
        receiver_node_id=NODES[family], runtime_id="docker", provider_socket_name="http",
        product_reference=ProductReference.from_document(document), descriptor_document=document,
        artifact=chosen)
