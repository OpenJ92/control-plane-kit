"""Complete static graph/product coverage; no future installation or SQL result."""
from dataclasses import replace
import unittest

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
from control_plane_kit_core.products import ProductDescriptorCodec, ProductIdentity, ProductRuntimeContract, ProductInstanceConfiguration, instantiate_product
from control_plane_kit_core.receiver_configuration import ReceiverNodeControlConfigurationCodec
from control_plane_kit_core.receiver_management_routes import ReceiverManagementRouteBinding, ReceiverManagementRoutesCodec
from control_plane_kit_core.secret_selections import SecretVersionSelection, UnselectedSecretUse
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, compile_topology
from control_plane_kit_operations.managed_verification_execution import require_managed_verification_plan_support, ManagedVerificationSupportUnavailable
from control_plane_kit_operations.products import RegisteredProduct
from control_plane_kit_operations.runtime_authorities import RegisteredRuntimeAuthority, LocalDockerSocketAuthority, RemoteDockerTlsAuthority
from control_plane_kit_operations.runtime_management_admission import runtime_management_execution_is_unsupported
from control_plane_kit_core.secrets import SecretReference
from tests import test_managed_verification_execution as fixtures
from tests import test_postgres_readiness_binding as original_fixtures


def mixed_plan(test):
    values, support = fixtures.ManagedVerificationRouteTests().inputs()
    graph = DEFAULT_GRAPH_CODEC.decode(values["desired"].graph_descriptor)
    original = original_fixtures.OriginalPostgresReadinessRecordTests().supplied_records()
    source = original["product"]
    contract = source.descriptor_document.product.runtime_contract
    artifact, = contract.configuration_artifacts
    codec = ReceiverNodeControlConfigurationCodec()
    receiver = codec.decode_bytes(artifact.content.encode())
    receiver = replace(receiver, target=replace(receiver.target,
        node_id=replace(receiver.target.node_id, value="postgres"),
        runtime_id=replace(receiver.target.runtime_id, value="docker"), receiver_id="c" * 32))
    artifact = replace(artifact, content=codec.encode_bytes(receiver).decode())
    product = replace(source.descriptor_document.product, identity=ProductIdentity("test", "qualified-postgres", 1),
        runtime_contract=replace(contract, configuration_artifacts=(artifact,)))
    document = ProductDescriptorCodec().encode_document(product)
    registered = RegisteredProduct.from_document(workspace_id="workspace-a", descriptor_document=document,
        source=source.source, imported_by="operator-a", imported_at="2026-11-08T12:00:00Z")
    block = instantiate_product(product, "postgres", ProductInstanceConfiguration.from_contract(product.runtime_contract))
    compiled = compile_topology(DeploymentTopology("postgres", DockerRuntime(runtime_id="docker", children=(block,))))
    check, = product.runtime_contract.verification.checks
    node = replace(compiled.node("postgres"), secret_selections=(SecretVersionSelection(
        check.authentication.password_reference, contract.secret_deliveries[0].intent,
        "selected-version-1"),))
    gateway = graph.node("gateway")
    routes_codec = ReceiverManagementRoutesCodec()
    artifacts = []
    for selected in gateway.configuration_artifacts:
        if selected.artifact_id == "gateway-health-targets":
            routes = routes_codec.decode_bytes(selected.content.encode())
            selected = replace(selected, content=routes_codec.encode_bytes(replace(routes, targets=(*routes.targets,
                ReceiverManagementRouteBinding("postgres", receiver.target, receiver.declaration, "http://postgres:8000")))).decode())
        artifacts.append(selected)
    graph = graph.update_node(replace(gateway, configuration_artifacts=tuple(artifacts)))
    runtime = graph.runtimes["docker"]
    graph = replace(graph, nodes={**graph.nodes, "postgres": node}, runtimes={**graph.runtimes,
        "docker": replace(runtime, children=tuple(sorted((*runtime.children, "postgres"))))})
    connector = graph.node("connector")
    connector_document = ProductDescriptorCodec().encode_document(replace(product,
        identity=ProductIdentity("test", "static-connector", 1), runtime_contract=ProductRuntimeContract(sockets=connector.sockets)))
    connector_product = RegisteredProduct.from_document(workspace_id="workspace-a", descriptor_document=connector_document,
        source=source.source, imported_by="operator-a", imported_at="2026-11-08T12:00:00Z")
    graph = graph.update_node(replace(connector, metadata={**connector.metadata,
        "product_identity": connector_product.reference.identity.key,
        "product_descriptor_digest": connector_product.reference.descriptor_sha256.value}))
    values = fixtures.with_desired(values, graph)
    values.pop("node_id")
    values["products"] = (*values["products"], registered, connector_product)
    authority = RegisteredRuntimeAuthority.from_authority(workspace_id="workspace-a", authority_ref=runtime.authority_ref,
        runtime_kind=runtime.kind, authority=LocalDockerSocketAuthority(), admitted_by="operator-a", admitted_at="2026-11-08T12:00:00Z")
    observer = replace(original["observer"], product=registered.reference, image=product.image)
    support = replace(support, postgres_observers=(observer,))
    return values | dict(runtime_authorities=(authority,), support=support)


class ManagedVerificationPlanSupportTests(unittest.TestCase):
    def test_same_ready_plan_requires_every_registered_wait_and_route(self):
        values = mixed_plan(self)
        self.assertTrue(values["plan"].plan.ready_for_execution)
        self.assertFalse(runtime_management_execution_is_unsupported(
            DEFAULT_GRAPH_CODEC.decode(values["current"].graph_descriptor),
            DEFAULT_GRAPH_CODEC.decode(values["desired"].graph_descriptor), values["plan"].plan,
            registered_products=values["products"], derivation_profile=values["plan"].derivation_profile))
        self.assertIsNone(require_managed_verification_plan_support(**values))
        support = values["support"]
        observer, = support.postgres_observers
        for change in (dict(support=None),
                dict(products=tuple(value for value in values["products"] if value.reference != observer.product)),
                dict(support=replace(support, postgres_observers=(replace(observer,
                    image=replace(observer.image, digest="sha256:" + "f" * 64)),))),
                dict(support=replace(support, health_receiver_decoders=type(support.health_receiver_decoders)(())))):
            with self.subTest(fields=tuple(change)), self.assertRaises(ManagedVerificationSupportUnavailable):
                require_managed_verification_plan_support(**(values | change))

    def test_sql_does_not_authorize_remote_daemon_or_unselected_credential(self):
        values = mixed_plan(self)
        authority, = values["runtime_authorities"]
        remote = RegisteredRuntimeAuthority.from_authority(workspace_id=authority.workspace_id,
            authority_ref=authority.authority_ref, runtime_kind=authority.runtime_kind,
            authority=RemoteDockerTlsAuthority("tcp://daemon.example.test:2376", *(SecretReference(
                "secret://fixture/tls/" + name) for name in ("ca", "certificate", "key"))),
            admitted_by=authority.admitted_by, admitted_at=authority.admitted_at)
        with self.assertRaises(ManagedVerificationSupportUnavailable):
            require_managed_verification_plan_support(**(values | dict(runtime_authorities=(remote,))))
        graph = DEFAULT_GRAPH_CODEC.decode(values["desired"].graph_descriptor)
        node = graph.node("postgres")
        selection, = node.secret_selections
        changed = fixtures.with_desired({**values, "node_id": "postgres"}, graph.update_node(replace(node,
            secret_selections=(UnselectedSecretUse(selection.reference, selection.intent),))))
        changed.pop("node_id")
        with self.assertRaises(ManagedVerificationSupportUnavailable):
            require_managed_verification_plan_support(**changed)
