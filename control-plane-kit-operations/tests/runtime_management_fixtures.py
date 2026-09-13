"""Small valid SDK topology values for management-admission laws."""

from control_plane_kit_core.algebra import BlockSockets, BlockSpec, ProviderSocket
from control_plane_kit_core.capabilities import CapabilityName
from control_plane_kit_core.node_control import (
    NodeControlGraphReference, NodeControlGraphReferenceRole, NodeHealthReadKind,
    WorkloadNodeControlSurfaceDescriptor,
)
from control_plane_kit_core.topology import DeploymentGraph, Node, RuntimeRecord, validate_graph
from control_plane_kit_core.topology.graph import Endpoint, LiteralAddress
from control_plane_kit_core.types import BlockFamily, Protocol, RuntimeKind


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
