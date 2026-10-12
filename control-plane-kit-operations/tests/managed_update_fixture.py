"""Exact registered A/B/C values for managed-update Operations laws."""

from dataclasses import replace
import json

from control_plane_kit_core.configuration import ConfigurationArtifact, ConfigurationMediaType
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.node_control import NodeHealthReadKind
from control_plane_kit_core.node_control_surface_reads import (
    WorkloadNodeControlSurfaceDeclaration,
    WorkloadNodeControlSurfaceDeclarationProfile,
)
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductDescriptorCodec,
    ProductIdentity,
    ProductRuntimeContract,
    ProviderRuntimePort,
)
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.receiver_configuration import ReceiverNodeControlConfigurationCodec
from control_plane_kit_core.receiver_management_routes import (
    ReceiverManagementRouteBinding,
    ReceiverManagementRoutes,
    ReceiverManagementRoutesCodec,
)
from control_plane_kit_core.topology import validate_graph
from control_plane_kit_core.wrapper_configuration import WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT
from control_plane_kit_operations.products import InlineDescriptorSource, RegisteredProduct
from tests.health_receiver_trust_fixture import artifact as receiver_artifact
from tests.runtime_management_fixtures import bootstrap_management_graph


def _artifact(artifact_id, content):
    return ConfigurationArtifact(
        artifact_id,
        f"/etc/cpk/gateway/{artifact_id.removeprefix('gateway-')}.json",
        ConfigurationMediaType.JSON,
        json.dumps(content, sort_keys=True, separators=(",", ":")),
    )


def managed_update_graphs(
    test_case,
    *,
    product_prefix="test",
    workload_ids=("api-x", "api-y"),
    route_aliases=None,
    application_artifacts=(),
):
    """Return accepted A/B/C projections plus their exact active products."""

    x_id, y_id = workload_ids
    route_aliases = route_aliases or {
        "gateway": "self",
        x_id: x_id,
        y_id: y_id,
    }
    graph = bootstrap_management_graph(test_case)
    renamed = {}
    for old, new in (("api", x_id), ("gateway", "gateway"), ("connector", "connector")):
        node = graph.node(old)
        block_spec = node.block_spec
        if new == x_id:
            block_spec = replace(
                block_spec,
                role_id=new,
                control_surfaces=(replace(
                    block_spec.control_surfaces[0],
                    health_reads=(NodeHealthReadKind.READINESS,),
                ),),
            )
        renamed[new] = replace(
            node,
            node_id=new,
            block_spec=replace(block_spec, role_id=block_spec.role_id),
        )
    configured = {}
    for node_id, receiver_id in (("gateway", "b" * 32), (x_id, "a" * 32)):
        node = renamed[node_id]
        declaration = WorkloadNodeControlSurfaceDeclaration(
            node.block_spec.control_surfaces[0],
            WorkloadNodeControlSurfaceDeclarationProfile.V2,
        )
        configured[node_id] = receiver_artifact(
            "workload", declaration, node=node_id, receiver=True,
            receiver_id=receiver_id,
        )
    gateway_control = replace(
        configured["gateway"], artifact_id="gateway-control",
        target_path="/etc/cpk/gateway/control.json",
    )
    configured["gateway"] = gateway_control
    gateway_value = ReceiverNodeControlConfigurationCodec().decode_bytes(
        gateway_control.content.encode("utf-8")
    )
    api_x_value = ReceiverNodeControlConfigurationCodec().decode_bytes(
        configured[x_id].content.encode("utf-8")
    )
    routes_a = ReceiverManagementRoutes(gateway_value.target, (
        ReceiverManagementRouteBinding(
            route_aliases["gateway"], gateway_value.target, gateway_value.declaration,
            "http://gateway:8000",
        ),
        ReceiverManagementRouteBinding(
            route_aliases[x_id], api_x_value.target, api_x_value.declaration,
            f"http://{x_id}:8000",
        ),
    ))
    artifacts_a = (
        _artifact("gateway-health-transit", {"protocol": "receiver-health-read-v2"}),
        ConfigurationArtifact(
            "gateway-health-targets", "/etc/cpk/gateway/health-targets.json",
            ConfigurationMediaType.JSON,
            ReceiverManagementRoutesCodec().encode_bytes(routes_a).decode("utf-8"),
        ),
        gateway_control,
    )
    renamed["gateway"] = replace(
        renamed["gateway"],
        configuration_artifacts=artifacts_a,
        public_environment=(*renamed["gateway"].public_environment,
            PublicStaticEnvironmentBinding(
                WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT,
                gateway_control.target_path,
            )),
    )
    renamed[x_id] = replace(
        renamed[x_id],
        configuration_artifacts=(configured[x_id], *application_artifacts),
        public_environment=(*renamed[x_id].public_environment,
            PublicStaticEnvironmentBinding(
                WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT,
                configured[x_id].target_path,
            )),
    )
    runtime = replace(
        graph.runtimes["docker"],
        children=("gateway", "connector", x_id),
        authority_ref=RuntimeAuthorityReference("local-docker"),
    )
    graph_a = replace(graph, nodes=renamed, runtimes={"docker": runtime})

    products = []
    selected = {}
    for name, node in graph_a.nodes.items():
        contract = ProductRuntimeContract(
            sockets=node.sockets,
            provider_ports=tuple(
                ProviderRuntimePort(socket.name, 8000) for socket in node.sockets.providers
            ),
            public_environment=node.public_environment,
            configuration_artifacts=node.configuration_artifacts,
            capabilities=node.block_spec.capabilities,
            verification=node.block_spec.verification,
            lifecycle=node.lifecycle,
            control_surfaces=node.block_spec.control_surfaces,
            gateway_transit=node.block_spec.gateway_transit,
        )
        product = ContainerServerProduct(
            ProductIdentity("test", f"{product_prefix}-{name}", 1),
            OciImageReference("ghcr.io", f"test/{product_prefix}-{name}", "sha256:" + "a" * 64),
            contract,
        )
        registered = RegisteredProduct.from_document(
            workspace_id="workspace-a",
            descriptor_document=ProductDescriptorCodec().encode_document(product),
            source=InlineDescriptorSource(),
            imported_by="operator-a",
            imported_at="2026-10-09T12:00:00Z",
        )
        products.append(registered)
        selected[name] = replace(
            node,
            metadata={
                "product_identity": registered.reference.identity.key,
                "product_descriptor_digest": registered.reference.descriptor_sha256.value,
            },
        )
    graph_a = replace(graph_a, nodes=selected)

    api_y = replace(
        graph_a.node(x_id),
        node_id=y_id,
        block_spec=replace(graph_a.node(x_id).block_spec, role_id=y_id),
    )
    declaration_y = WorkloadNodeControlSurfaceDeclaration(
        api_y.block_spec.control_surfaces[0],
        WorkloadNodeControlSurfaceDeclarationProfile.V2,
    )
    control_y = receiver_artifact(
        "workload", declaration_y, node=y_id, receiver=True,
        receiver_id="c" * 32,
    )
    api_y = replace(api_y, configuration_artifacts=(control_y, *application_artifacts))
    api_y_value = ReceiverNodeControlConfigurationCodec().decode_bytes(
        control_y.content.encode("utf-8")
    )
    routes_b = ReceiverManagementRoutes(gateway_value.target, (
        *routes_a.targets,
        ReceiverManagementRouteBinding(
            route_aliases[y_id], api_y_value.target, api_y_value.declaration,
            f"http://{y_id}:8000",
        ),
    ))
    artifacts_b = tuple(
        replace(value, content=ReceiverManagementRoutesCodec().encode_bytes(routes_b).decode("utf-8"))
        if value.artifact_id == "gateway-health-targets" else value
        for value in graph_a.node("gateway").configuration_artifacts
    )
    graph_b = replace(
        graph_a,
        nodes={
            **graph_a.nodes,
            "gateway": replace(graph_a.node("gateway"), configuration_artifacts=artifacts_b),
            y_id: api_y,
        },
        runtimes={"docker": replace(runtime, children=runtime.children + (y_id,))},
    )
    graph_c = graph_a
    for value in (graph_a, graph_b, graph_c):
        validated = validate_graph(value)
        test_case.assertTrue(validated.valid, validated.errors)
        validated.require_valid()
    return graph_a, graph_b, graph_c, tuple(products)
