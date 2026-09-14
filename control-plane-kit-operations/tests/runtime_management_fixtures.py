"""Small valid SDK topology values for management-admission laws."""

from dataclasses import replace
import control_plane_kit_core as core
from control_plane_kit_core.algebra import BlockSockets, BlockSpec, ProviderSocket
from control_plane_kit_core.capabilities import CapabilityName
from control_plane_kit_core.node_control import (
    NodeControlGraphReference, NodeControlGraphReferenceRole, NodeHealthReadKind,
    WorkloadNodeControlSurfaceDescriptor,
)
from control_plane_kit_core.topology import DeploymentGraph, Node, RuntimeRecord, validate_graph
from control_plane_kit_core.topology.graph import Endpoint, LiteralAddress
from control_plane_kit_core.types import BlockFamily, Protocol, RuntimeKind
from control_plane_kit_core.public_ingress import IngressAuthorityReference, NamedPublicIngress, PublicIngressTarget
from control_plane_kit_core.products import (
    ContainerServerProduct, OciImageReference, ProductDescriptorCodec,
    ProductIdentity, ProductRuntimeContract, ProviderRuntimePort,
)
from control_plane_kit_operations.products import InlineDescriptorSource, RegisteredProduct


def registered_management_product(*, transit=False):
    sdk = sdk_health_graph().node("api")
    contract = ProductRuntimeContract(
        sockets=sdk.sockets,
        provider_ports=(ProviderRuntimePort("http", 8000),),
        capabilities=() if transit else sdk.block_spec.capabilities,
        control_surfaces=() if transit else sdk.block_spec.control_surfaces,
        gateway_transit=core.GatewayTransitDeclaration(
            "http", core.GatewayTransitProtocol.NODE_HEALTH_READ_V1,
        ) if transit else None,
    )
    product = ContainerServerProduct(
        ProductIdentity("test", "managed-contract", 1),
        OciImageReference("ghcr.io", "test/managed-contract", "sha256:" + "b" * 64),
        contract,
    )
    return RegisteredProduct.from_document(
        workspace_id="workspace-a",
        descriptor_document=ProductDescriptorCodec().encode_document(product),
        source=InlineDescriptorSource(), imported_by="operator-a",
        imported_at="2026-07-22T09:00:00Z",
    )


def omitted_management_graph(product):
    """Canonical node omits declarations but pins the registered implementation."""
    graph = sdk_health_graph(metadata={
        "product_identity": product.reference.identity.key,
        "product_descriptor_digest": product.reference.descriptor_sha256.value,
    })
    graph = replace(graph, nodes={"api": replace(graph.node("api"), block_spec=BlockSpec("api"))})
    validate_graph(graph).require_valid()
    return graph


def management_graph(test_case, *, selected=True, metadata=None):
    """Complete explicit management topology with no SDK workload surfaces."""
    declaration = getattr(core, "GatewayTransitDeclaration", None)
    protocol = getattr(core, "GatewayTransitProtocol", None)
    management = getattr(core, "RuntimeManagement", None)
    for name, value in (("GatewayTransitDeclaration", declaration), ("GatewayTransitProtocol", protocol), ("RuntimeManagement", management)):
        test_case.assertIsNotNone(value, f"public runtime management contract {name} is missing")
    sdk = sdk_health_graph(metadata=metadata)
    workload = replace(sdk.node("api"), block_spec=BlockSpec("api"))
    gateway = Node(
        "gateway", BlockFamily.APPLICATION,
        BlockSpec("gateway", gateway_transit=declaration("http", protocol.NODE_HEALTH_READ_V1)),
        "container-server", "docker", BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),)),
        endpoints={"http": Endpoint(LiteralAddress("http://gateway:8000"), Protocol.HTTP)},
    )
    connector = Node("connector", BlockFamily.APPLICATION, BlockSpec("connector"), "container-server", "docker", BlockSockets())
    ingress = NamedPublicIngress("management", IngressAuthorityReference("authority"), PublicIngressTarget("gateway", "http"), "connector", "management.example.invalid")
    graph = DeploymentGraph(
        "explicit-management", nodes={"api": workload, "gateway": gateway, "connector": connector},
        runtimes={"docker": RuntimeRecord("docker", RuntimeKind.DOCKER, ("api", "gateway", "connector"), management=management("gateway", "management") if selected else None)},
        public_ingresses=(ingress,),
    )
    validate_graph(graph).require_valid()
    return graph


def bootstrap_management_graph(test_case):
    """Explicit topology with gateway readiness and one SDK workload."""
    graph = management_graph(test_case)
    sdk = sdk_health_graph().node("api")
    gateway = graph.node("gateway")
    readiness = replace(sdk.block_spec.control_surfaces[0], health_reads=(NodeHealthReadKind.READINESS,))
    gateway = replace(gateway, block_spec=replace(
        gateway.block_spec, capabilities=sdk.block_spec.capabilities,
        control_surfaces=(readiness,),
    ))
    graph = replace(graph, nodes={**graph.nodes, "api": sdk, "gateway": gateway})
    validate_graph(graph).require_valid()
    return graph


def sdk_health_graph(*, metadata=None, public_environment=()):
    surface = WorkloadNodeControlSurfaceDescriptor(
        NodeControlGraphReference(NodeControlGraphReferenceRole.PROVIDER_SOCKET, "http"),
        (), (NodeHealthReadKind.LIVENESS,),
    )
    graph = DeploymentGraph(
        "sdk-health",
        nodes={"api": Node(
            "api", BlockFamily.APPLICATION,
            BlockSpec(
                "api", capabilities=(CapabilityName.NODE_CONTROLLABLE, CapabilityName.HEALTH_CHECKABLE),
                control_surfaces=(surface,),
            ),
            "container-server", "docker",
            BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),)),
            endpoints={"http": Endpoint(LiteralAddress("http://api:8000"), Protocol.HTTP)},
            metadata={} if metadata is None else metadata,
            public_environment=public_environment,
        )},
        runtimes={"docker": RuntimeRecord("docker", RuntimeKind.DOCKER, ("api",))},
    )
    validate_graph(graph).require_valid()
    return graph


def sdk_variable_graph(*, health_reads=()):
    from control_plane_kit_core.node_control import (
        ControlPlaneCommandCodec, ControlPlaneResultCodec, ControlPlaneStateCodec,
        ControlPlaneVariableDescriptor, ControlPlaneVariableKind,
        ControlPlaneVariableOperationContract, NodeControlOperation,
    )

    graph = sdk_health_graph()
    node = graph.node("api")
    variable = ControlPlaneVariableDescriptor(
        NodeControlGraphReference(NodeControlGraphReferenceRole.VARIABLE, "mode"),
        ControlPlaneVariableKind.SCALAR, ControlPlaneStateCodec.SCALAR_V1,
        (
            ControlPlaneVariableOperationContract(NodeControlOperation.READ_STATE, None, ControlPlaneResultCodec.STATE_V1),
            ControlPlaneVariableOperationContract(NodeControlOperation.APPLY_COMMAND, ControlPlaneCommandCodec.REPLACE_SCALAR_V1, ControlPlaneResultCodec.TRANSITION_V1),
        ),
    )
    surface = replace(node.block_spec.control_surfaces[0], variables=(variable,), health_reads=health_reads)
    capabilities = (CapabilityName.NODE_CONTROLLABLE,)
    if health_reads:
        capabilities += (CapabilityName.HEALTH_CHECKABLE,)
    node = replace(node, block_spec=BlockSpec("api", capabilities=capabilities, control_surfaces=(surface,)))
    graph = replace(graph, nodes={"api": node})
    validate_graph(graph).require_valid()
    return graph


def cyclic_bootstrap_graph(test_case):
    """A real gateway service requirement closes the bootstrap dependency cycle."""
    from control_plane_kit_core.algebra import RequirementSocket
    from control_plane_kit_core.environment import SocketDerivedEnvironmentBinding
    from control_plane_kit_core.topology.graph import Edge
    from control_plane_kit_core.types import SocketBinding

    graph = bootstrap_management_graph(test_case)
    edge_id = "gateway-needs-api"
    address = graph.node("api").endpoint("http").url
    gateway = graph.node("gateway")
    gateway = replace(
        gateway,
        sockets=replace(gateway.sockets, requirements=(RequirementSocket("service", Protocol.HTTP, ("SERVICE_URL",)),)),
        socket_environment=(SocketDerivedEnvironmentBinding("SERVICE_URL", address, edge_id),),
    )
    edge = Edge(edge_id, "api", "http", "gateway", "service", Protocol.HTTP,
                SocketBinding.ENVIRONMENT, {"SERVICE_URL": address})
    graph = replace(graph, nodes={**graph.nodes, "gateway": gateway}, edges={edge_id: edge})
    validate_graph(graph).require_valid()
    return graph
