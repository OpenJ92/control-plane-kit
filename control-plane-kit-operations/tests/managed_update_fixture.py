"""Exact registered A/B/C values for managed-update Operations laws."""

from dataclasses import replace
import json

from control_plane_kit_core.configuration import ConfigurationArtifact, ConfigurationMediaType
from control_plane_kit_core.node_control import NodeHealthReadKind
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductDescriptorCodec,
    ProductIdentity,
    ProductRuntimeContract,
    ProviderRuntimePort,
)
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.topology import validate_graph
from control_plane_kit_operations.products import InlineDescriptorSource, RegisteredProduct
from tests.runtime_management_fixtures import bootstrap_management_graph


def _artifact(artifact_id, content):
    return ConfigurationArtifact(
        artifact_id,
        f"/etc/cpk/gateway/{artifact_id.removeprefix('gateway-')}.json",
        ConfigurationMediaType.JSON,
        json.dumps(content, sort_keys=True, separators=(",", ":")),
    )


def managed_update_graphs(test_case):
    """Return accepted A/B/C projections plus their exact active products."""

    graph = bootstrap_management_graph(test_case)
    artifacts_a = (
        _artifact("gateway-health-transit", {"protocol": "receiver-health-read-v2"}),
        _artifact("gateway-health-targets", {"targets": ["api-x"]}),
        _artifact("gateway-control", {"protocol": "node-control-v1"}),
    )
    renamed = {}
    for old, new in (("api", "api-x"), ("gateway", "gateway"), ("connector", "connector")):
        node = graph.node(old)
        block_spec = node.block_spec
        if new == "api-x":
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
            block_spec=replace(block_spec, role_id=new),
            configuration_artifacts=artifacts_a if new == "gateway" else (),
        )
    runtime = replace(
        graph.runtimes["docker"],
        children=("gateway", "connector", "api-x"),
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
            ProductIdentity("test", name, 1),
            OciImageReference("ghcr.io", f"test/{name}", "sha256:" + "a" * 64),
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
        graph_a.node("api-x"),
        node_id="api-y",
        block_spec=replace(graph_a.node("api-x").block_spec, role_id="api-y"),
    )
    artifacts_b = tuple(
        _artifact("gateway-health-targets", {"targets": ["api-x", "api-y"]})
        if value.artifact_id == "gateway-health-targets" else value
        for value in graph_a.node("gateway").configuration_artifacts
    )
    graph_b = replace(
        graph_a,
        nodes={
            **graph_a.nodes,
            "gateway": replace(graph_a.node("gateway"), configuration_artifacts=artifacts_b),
            "api-y": api_y,
        },
        runtimes={"docker": replace(runtime, children=runtime.children + ("api-y",))},
    )
    graph_c = graph_a
    for value in (graph_a, graph_b, graph_c):
        validate_graph(value).require_valid()
    return graph_a, graph_b, graph_c, tuple(products)
