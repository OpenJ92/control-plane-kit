"""Tiny byte-decoder port witness; PostgreSQL owners retain all transitions."""
from dataclasses import replace
import base64
import importlib
import importlib.util
import json

from control_plane_kit_core.configuration import ConfigurationArtifact, ConfigurationFileMode, ConfigurationMediaType
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.wrapper_configuration import WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT, WorkloadNodeControlConfigurationCodec
from control_plane_kit_core.delegation_keys import DelegationKeyAlgorithm, DelegationKeyPurpose, DelegationPublicKey
from control_plane_kit_core.node_control import NodeControlGraphReference, NodeControlGraphReferenceRole, NodeControlTarget, workload_node_control_audience
from control_plane_kit_core.receiver_identity import NodeControlReceiverTarget
from control_plane_kit_core.node_control_surface_reads import WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationCodec, WorkloadNodeControlSurfaceDeclarationProfile
from control_plane_kit_core.planning import (
    ActivityPlan, ManagementBootstrapStage, ObserveManagementBootstrap,
    ObserveNodeHealth, PlanGraphSide, compile_graph_activity_plan,
)
from control_plane_kit_core.products import ContainerServerProduct, OciImageReference, ProductDescriptorCodec, ProductIdentity, ProductReference, ProductRuntimeContract, ProviderRuntimePort
from control_plane_kit_core.topology import DeploymentGraph, validate_graph
from control_plane_kit_operations.products import ImportProductDescriptorCommand, InlineDescriptorSource, ProductRegistrationService
from control_plane_kit_operations.runtime_management_targets import (
    ManagementHealthTargetProjectionError, project_management_health_target,
)
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


def artifact(family, surface_declaration, *, revision="health-desired", shared=True, receiver=False, **changes):
    value = dict(profile=PROFILE, family=family, workspace="workspace-a", node=NODES[family],
        runtime="docker", issuer="cpk-server", purpose=PURPOSES[family].value,
        revision=revision, socket="http", declaration=surface_declaration.descriptor(),
        keys=[dict(key_id="health-" + family, pem=PEMS[family])])
    value.update(changes)
    if shared and family == "workload":
        # Literal shared wire data permits malformed-profile/purpose witnesses;
        # the actual Core codec is exercised by the owner, not replaced here.
        health = dict(purpose=value["purpose"], issuer=value["issuer"], public_keys=[
            dict(key_id=key["key_id"], algorithm="ed25519", public_key_pem=key["pem"])
            for key in value["keys"]])
        surface = dict(purpose=DelegationKeyPurpose.WORKLOAD_NODE_CONTROL_SURFACE_READ.value,
            issuer=value["issuer"], public_keys=[dict(key_id="surface-read", algorithm="ed25519",
                                                   public_key_pem=PUBLIC_KEY_C)])
        target = NodeControlTarget(reference("workspace", value["workspace"]),
            reference("graph-revision", value["revision"]), reference("node", value["node"]),
            reference("provider-socket", value["socket"]))
        value = dict(profile="workload-node-control-configuration.v1" if value["profile"] == PROFILE else value["profile"],
            target=target.descriptor(), runtime_id=value["runtime"], declaration=value["declaration"],
            verifiers=[surface, health])
        if receiver:
            value["profile"] = ("workload-node-control-configuration.v2"
                if value["profile"] == "workload-node-control-configuration.v1" else value["profile"])
            value["target"] = NodeControlReceiverTarget(target.workspace_id,
                reference("runtime", value.pop("runtime_id")), target.node_id, target.provider_socket_name,
                changes.get("receiver_id", ("b" if target.node_id.value == "gateway" else "a") * 32)).descriptor()
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
        raise self.contract.HealthReceiverTrustError("health receiver trust is unavailable")


def workload_trust(contract, chosen):
    """Construct public fact values for their pure nominal-value tests only."""
    configured = WorkloadNodeControlConfigurationCodec().decode_bytes(chosen.content.encode())
    family = next(value for value in configured.verifiers if value.purpose is PURPOSES["workload"])
    return contract.WorkloadHealthReceiverTrust(target=configured.target, runtime_id=configured.runtime_id,
        declaration=configured.declaration, purpose=family.purpose, issuer=family.issuer,
        audience=workload_node_control_audience(configured.target), public_keys=family.public_keys)


def context(test, *, side=PlanGraphSide.DESIRED_GRAPH, changes=None, slot_changes=None, stage=None,
            shared=True, transform=None, receiver=False):
    if stage is not None:
        return _bootstrap_context(test, stage=stage, side=side,
            changes=changes, slot_changes=slot_changes, shared=shared, receiver=receiver)
    graph = bootstrap_management_graph(test)
    declaration = WorkloadNodeControlSurfaceDeclaration(graph.node("api").block_spec.control_surfaces[0],
        WorkloadNodeControlSurfaceDeclarationProfile.V2)
    documents, selected = {}, {}
    nodes = dict(graph.nodes)
    revision = "health-base" if side is PlanGraphSide.BASE_GRAPH else "health-desired"
    for family, node_id in NODES.items():
        node = graph.node(node_id)
        chosen = artifact(family, declaration, shared=shared, receiver=receiver,
            **({"revision": revision} | (changes or {}).get(family, {})))
        selected[family] = replace(chosen, **(slot_changes or {}).get(family, {}))
        # Deliberately wrong default makes accidental descriptor fallback visible.
        default = artifact(family, declaration, shared=shared, receiver=receiver,
            keys=[dict(key_id="default-only", pem=PUBLIC_KEY_C)])
        extras = ()
        if receiver and family == "transit":
            own_declaration = WorkloadNodeControlSurfaceDeclaration(node.block_spec.control_surfaces[0],
                WorkloadNodeControlSurfaceDeclarationProfile.V2)
            extras = (artifact("workload", own_declaration, receiver=True, node=node_id),)
        defaults = (default, *extras)
        environment = (wrapper_environment(defaults) if shared and (family == "workload" or extras)
            else node.public_environment)
        contract = ProductRuntimeContract(sockets=node.sockets,
            provider_ports=(ProviderRuntimePort("http", 8000),),
            capabilities=node.block_spec.capabilities, control_surfaces=node.block_spec.control_surfaces,
            gateway_transit=node.block_spec.gateway_transit, configuration_artifacts=defaults, public_environment=environment)
        document = ProductDescriptorCodec().encode_document(ContainerServerProduct(
            ProductIdentity("test", "health-" + family, 1),
            OciImageReference("ghcr.io", "test/health-" + family, "sha256:" + "b" * 64), contract))
        documents[family] = document
        ref = ProductReference.from_document(document)
        nodes[node_id] = replace(node, configuration_artifacts=(selected[family], *extras), public_environment=environment, metadata={
            **node.metadata, "product_identity": ref.identity.key,
            "product_descriptor_digest": ref.descriptor_sha256.value})
    if transform is not None:
        nodes, documents, selected = transform(nodes, documents, selected)
    desired = validate_graph(replace(graph, nodes=nodes))
    current = validate_graph(DeploymentGraph("empty" if receiver else graph.name))
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


def _bootstrap_context(test, *, stage, side, changes, slot_changes, shared, receiver=False):
    """Both signed receiver families inhabit one gateway product, not two nodes.

    This is a focused admission/reload world. Its caller may seed predecessor
    journal facts; it is not evidence that creation or bootstrap effects ran.
    The workload-only context above retains its original defaults and pins.
    """
    test.assertIs(side, PlanGraphSide.DESIRED_GRAPH)
    test.assertIn(stage, (
        ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH,
        ManagementBootstrapStage.GATEWAY_INGRESS_READY,
    ))
    graph = bootstrap_management_graph(test)
    gateway = graph.node("gateway")
    declaration = WorkloadNodeControlSurfaceDeclaration(
        gateway.block_spec.control_surfaces[0], WorkloadNodeControlSurfaceDeclarationProfile.V2)
    selected, defaults = {}, []
    for family in PURPOSES:
        values = {"node": "gateway", "revision": "health-desired"}
        chosen = artifact(family, declaration, shared=shared, receiver=receiver,
            **(values | (changes or {}).get(family, {})))
        selected[family] = replace(chosen, **(slot_changes or {}).get(family, {}))
        defaults.append(artifact(family, declaration, shared=shared, receiver=receiver, **values,
            keys=[dict(key_id="default-only", pem=PUBLIC_KEY_C)]))
    contract = ProductRuntimeContract(sockets=gateway.sockets,
        provider_ports=(ProviderRuntimePort("http", 8000),),
        capabilities=gateway.block_spec.capabilities,
        control_surfaces=gateway.block_spec.control_surfaces,
        gateway_transit=gateway.block_spec.gateway_transit,
        configuration_artifacts=tuple(defaults),
        public_environment=wrapper_environment(defaults) if shared else gateway.public_environment)
    document = ProductDescriptorCodec().encode_document(ContainerServerProduct(
        ProductIdentity("test", "bootstrap-gateway", 1),
        OciImageReference("ghcr.io", "test/bootstrap-gateway", "sha256:" + "b" * 64), contract))
    product_reference = ProductReference.from_document(document)
    gateway = replace(gateway, configuration_artifacts=tuple(selected.values()),
        public_environment=contract.public_environment, metadata={
        **gateway.metadata, "product_identity": product_reference.identity.key,
        "product_descriptor_digest": product_reference.descriptor_sha256.value})
    desired = validate_graph(replace(graph, nodes={**graph.nodes, "gateway": gateway}))
    current = validate_graph(DeploymentGraph("empty" if receiver else graph.name))
    current.require_valid()
    desired.require_valid()
    plan = compile_graph_activity_plan(current, desired)
    activities = tuple(item for item in plan.activities
        if type(item.operation) is ObserveManagementBootstrap and item.operation.stage is stage)
    test.assertEqual(len(activities), 1)
    activity, = activities
    try:
        projected = project_management_health_target(plan, activity.activity_id, activity.operation, current, desired)
    except ManagementHealthTargetProjectionError:
        test.fail("original signed bootstrap stage must project the gateway's own health target")
    # Binding identity is (product, purpose), so the same registered product
    # supplies both families without overwriting either selected artifact.
    documents = {family: document for family in PURPOSES}
    return (plan, activity, current, desired, projected), documents, selected


def register_products(test, documents):
    service = ProductRegistrationService(test.unit_of_work)
    return {family: service.import_descriptor(ImportProductDescriptorCommand(
        workspace_id="workspace-a", descriptor_document=document, source=InlineDescriptorSource(),
        imported_by="operator-a", imported_at="2026-08-01T10:00:00Z"))
        for family, document in documents.items()}


def wrapper_environment(artifacts):
    chosen = next(value for value in artifacts if value.artifact_id == "test-workload")
    return (PublicStaticEnvironmentBinding(WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT, chosen.target_path),)


def bindings(contract, documents, decoder):
    return tuple(contract.HealthReceiverDecoderBinding(
        product_reference=ProductReference.from_document(document), purpose=PURPOSES[family],
        configuration_profile=PROFILE, artifact_id="test-" + family,
        target_path="/etc/test/" + family + ".json", media_type=ConfigurationMediaType.JSON,
        file_mode=ConfigurationFileMode.READ_ONLY, decoder=decoder)
        for family, document in documents.items() if family == "transit")


def selection(contract, document, chosen, family="transit"):
    return contract.HealthReceiverSelection(workspace_id="workspace-a", authored_graph_id="health-desired",
        realized_projection_id="projection-desired", graph_side=PlanGraphSide.DESIRED_GRAPH,
        receiver_node_id=NODES[family], runtime_id="docker", provider_socket_name="http",
        product_reference=ProductReference.from_document(document), descriptor_document=document,
        artifact=chosen)
