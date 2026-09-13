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
