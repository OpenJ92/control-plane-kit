"""Authored managed values shared by teardown owner tests; no provider effects."""
from dataclasses import replace

from control_plane_kit_core.products import (
    ContainerServerProduct, OciImageReference, ProductDescriptorCodec,
    ProductIdentity, ProductRuntimeContract, ProviderRuntimePort,
)
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.topology import DeploymentGraph, validate_graph
from control_plane_kit_operations.deployment_transitions import Deploy
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile, derive_activity_plan
from control_plane_kit_operations.products import InlineDescriptorSource, RegisteredProduct
from tests.runtime_management_fixtures import bootstrap_management_graph


PROFILE = PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1


def managed_teardown(test_case):
    graph = bootstrap_management_graph(test_case)
    nodes, products = {}, []
    for name, node in graph.nodes.items():
        product = ContainerServerProduct(ProductIdentity("test", name, 1),
            OciImageReference("ghcr.io", "test/" + name, "sha256:" + "a" * 64),
            ProductRuntimeContract(sockets=node.sockets,
                provider_ports=tuple(ProviderRuntimePort(socket.name, 8000) for socket in node.sockets.providers),
                capabilities=node.block_spec.capabilities,
                gateway_transit=node.block_spec.gateway_transit,
                control_surfaces=node.block_spec.control_surfaces))
        registered = RegisteredProduct.from_document(workspace_id="workspace-a",
            descriptor_document=ProductDescriptorCodec().encode_document(product),
            source=InlineDescriptorSource(), imported_by="operator-a", imported_at="2026-07-22T12:00:00Z")
        products.append(registered)
        nodes[name] = replace(node, metadata={"product_identity": registered.reference.identity.key,
            "product_descriptor_digest": registered.reference.descriptor_sha256.value})
    graph = replace(graph, nodes=nodes, runtimes={"docker": replace(graph.runtimes["docker"],
        authority_ref=RuntimeAuthorityReference("local-docker"))}, public_ingresses=(replace(
            graph.public_ingresses[0], hostname="cpk-gateway-001.openj92.dev"),))
    empty = DeploymentGraph("empty")
    plan = derive_activity_plan(Deploy(validate_graph(graph), validate_graph(empty)), profile=PROFILE)
    return graph, empty, plan, tuple(products)


def seed_owned_ingress(stores, graph):
    from control_plane_kit_core.secrets import SecretProviderId, SecretProviderEndpointReference, SecretReference, SecretUseIntent
    from control_plane_kit_operations.secret_providers import RegisteredSecretProvider, RegisteredSecretReference, SecretProviderKind
    from tests.test_runtime_effect_translation import _registered_ingress_authority, _cloudflare_resource, _generated_ingress_secret
    selected = graph.public_ingresses[0]
    authority = _registered_ingress_authority()
    generated = _generated_ingress_secret()
    stores.secret_providers.register(RegisteredSecretProvider(
        registration_id=generated.provider_registration_id, workspace_id="workspace-a",
        provider_id=SecretProviderId("generated"), provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS,
        display_name="Fixture custody", endpoint_reference=SecretProviderEndpointReference("workspace-secrets"),
        credential_reference=SecretReference("secret://bootstrap/provider-token"),
        allowed_reference_prefixes=(SecretReference("secret://generated/ingress"),),
        allowed_intents=(SecretUseIntent.CLOUDFLARE_TUNNEL_TOKEN,),
        admitted_by="operator-a", admitted_at="2026-07-22T12:00:00Z"))
    stores.ingress_authorities.register(workspace_id="workspace-a", authority_ref=selected.authority_ref,
        authority=authority.authority, admitted_by="operator-a", admitted_at="2026-07-22T12:00:00Z")
    stores.secret_references.register(RegisteredSecretReference(
        registration_id=generated.reference_registration_id, workspace_id="workspace-a",
        reference=generated.secret_ref, provider_registration_id=generated.provider_registration_id,
        allowed_intents=(SecretUseIntent.CLOUDFLARE_TUNNEL_TOKEN,),
        admitted_by="operator-a", admitted_at="2026-07-22T12:00:00Z"))
    stores.ingress_resources.record_cloudflare(replace(_cloudflare_resource(),
        ingress_id=selected.ingress_id, authority_ref=selected.authority_ref, lifecycle=selected.lifecycle))
    stores.generated_ingress_secrets.record(generated)
