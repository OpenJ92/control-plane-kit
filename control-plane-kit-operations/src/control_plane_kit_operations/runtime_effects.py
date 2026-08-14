"""Translate operations-owned realization context into core runtime effects."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Mapping

from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.operations import RunId
from control_plane_kit_core.planning.activity_plan import (
    AddSocketConnection,
    NodeTarget,
    ReconcileNode,
    RemoveNodeResource,
    RemoveRuntimeResource,
    RemoveSocketConnection,
    StartNode,
    StopNode,
    StopRuntime,
    SwitchSocketConnection,
    WaitForHealthy,
)
from control_plane_kit_core.products import (
    ProductDescriptorDigest,
    ProductIdentity,
    ProductReference,
)
from control_plane_kit_core.probe_intents import (
    EndpointContext,
    LiteralEndpointMaterial,
    RuntimeEndpointObservation,
)
from control_plane_kit_core.public_ingress import PublicIngressExposure
from control_plane_kit_core.runtime_authority import (
    RuntimeAuthorityAccessDelivery,
    RuntimeAuthorityReference,
)
from control_plane_kit_core.runtime_effects import (
    GatewayHttpTarget,
    GatewayPostgresTarget,
    GatewayTarget,
    GatewayTargetId,
    GatewayTargetMap,
    ImagePullAuthority,
    RuntimeEffectKind,
    RuntimeEffectRequest,
    RuntimeEffectSource,
    RuntimeProductMaterial,
)
from control_plane_kit_core.secrets import (
    SecretDelivery,
    SecretEnvironmentDelivery,
    SecretFileDelivery,
    SecretReference,
    SecretUseIntent,
    secret_delivery_sort_key,
)
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph, Node
from control_plane_kit_core.types import Protocol, RuntimeKind
from control_plane_kit_core.verification import PostgresQueryCheck
from control_plane_kit_operations.coordinator import ActivityRealizationContext
from control_plane_kit_operations.ingress_authorities import (
    CloudflareOwnedIngressResource,
    GeneratedIngressSecretReference,
    GeneratedSecretPurpose,
    OwnedIngressResourceStatus,
    RegisteredIngressAuthority,
    cloudflare_tunnel_token_delivery_plan,
)
from control_plane_kit_operations.products import (
    RegisteredImagePullAuthority,
    RegisteredProduct,
)
from control_plane_kit_operations.runtime_authorities import (
    RegisteredRuntimeAuthority,
    RegisteredRuntimeAuthorityDelivery,
    RemoteDockerTlsAuthority,
)
from control_plane_kit_operations.workflows import InvalidOperationCommand

_GATEWAY_TARGETS_ENVIRONMENT = "CPK_GATEWAY_TARGETS_JSON"


def runtime_effect_request_for_context(
    context: ActivityRealizationContext,
) -> RuntimeEffectRequest:
    """Interpret pinned operations material as a pure runtime-effect request."""

    if not isinstance(context, ActivityRealizationContext):
        raise InvalidOperationCommand(
            "runtime effect translation requires ActivityRealizationContext"
        )
    try:
        run_id = RunId(context.run.run_id)
    except ValueError:
        run_id = None
    if run_id is None:
        raise InvalidOperationCommand("runtime effect run_id is malformed")
    graph = _material_graph(context)
    runtime_id = _runtime_id_for_context(context, graph)
    authority_ref = _runtime_authority_ref_for_context(graph, runtime_id)
    return RuntimeEffectRequest(
        effect_id=context.intent_event.event_id,
        kind=RuntimeEffectKind.REALIZE_ACTIVITY,
        runtime_kind=_runtime_kind_for_context(context, graph, runtime_id),
        authority_ref=authority_ref,
        authority_deliveries=_runtime_authority_deliveries_for_context(
            context.runtime_authority_deliveries,
            authority_ref,
        ),
        source=RuntimeEffectSource(
            workspace_id=context.request.identity.workspace_id,
            request_id=context.request.identity.request_id,
            run_id=run_id,
            plan_id=context.plan_record.plan_id,
            base_graph_id=context.plan_record.base_graph_id,
            desired_graph_id=context.plan_record.desired_graph_id,
            intent_event_id=context.intent_event.event_id,
        ),
        activity_id=context.activity.activity_id,
        operation=context.activity.operation,
        products=_products_for_context(context, graph, runtime_id),
    )


def required_secret_uses_for_runtime_effect(
    request: RuntimeEffectRequest,
    authority: RegisteredRuntimeAuthority | None,
) -> tuple[tuple[SecretReference, SecretUseIntent], ...]:
    """Enumerate every exact reference/intent pair an interpreter may resolve."""

    if not isinstance(request, RuntimeEffectRequest):
        raise InvalidOperationCommand(
            "secret-use enumeration requires RuntimeEffectRequest"
        )
    uses: set[tuple[SecretReference, SecretUseIntent]] = set()
    for material in request.products:
        contract = material.product.runtime_contract
        for delivery in contract.secret_deliveries:
            if isinstance(
                delivery,
                (SecretEnvironmentDelivery, SecretFileDelivery),
            ):
                uses.add((delivery.reference, delivery.intent))
        if material.pull_authority is not None:
            uses.add(
                (
                    material.pull_authority.credential_reference,
                    SecretUseIntent.OCI_PULL_CREDENTIAL,
                )
            )
        for check in contract.verification.checks:
            if (
                isinstance(check, PostgresQueryCheck)
                and check.authentication is not None
            ):
                uses.add(
                    (
                        check.authentication.password_reference,
                        SecretUseIntent.POSTGRES_PASSWORD,
                    )
                )
    if authority is not None and isinstance(
        authority.authority,
        RemoteDockerTlsAuthority,
    ):
        uses.update(
            (
                (
                    authority.authority.ca_certificate,
                    SecretUseIntent.DOCKER_REMOTE_TLS_CA_CERTIFICATE,
                ),
                (
                    authority.authority.client_certificate,
                    SecretUseIntent.DOCKER_REMOTE_TLS_CLIENT_CERTIFICATE,
                ),
                (
                    authority.authority.client_key,
                    SecretUseIntent.DOCKER_REMOTE_TLS_CLIENT_KEY,
                ),
            )
        )
    return tuple(
        sorted(
            uses,
            key=lambda value: (value[0].reference_id, value[1].value),
        )
    )


def _material_graph(context: ActivityRealizationContext) -> DeploymentGraph:
    if isinstance(
        context.activity.operation,
        (
            StopNode,
            RemoveNodeResource,
            RemoveSocketConnection,
            StopRuntime,
            RemoveRuntimeResource,
        ),
    ):
        return DEFAULT_GRAPH_CODEC.decode(context.base_graph.graph_descriptor)
    return DEFAULT_GRAPH_CODEC.decode(context.desired_graph.graph_descriptor)


def _runtime_id_for_context(
    context: ActivityRealizationContext,
    graph: DeploymentGraph,
) -> str:
    node_id = _node_target(context)
    if node_id is not None:
        try:
            return graph.nodes[node_id].runtime_id
        except KeyError as error:
            raise InvalidOperationCommand("runtime effect node target is missing") from error
    operation = context.activity.operation
    target = getattr(operation, "target", None)
    runtime_id = getattr(target, "runtime_id", None)
    if isinstance(runtime_id, str) and runtime_id:
        return runtime_id
    raise InvalidOperationCommand("runtime effect target is not a runtime operation")


def _runtime_kind_for_context(
    context: ActivityRealizationContext,
    graph: DeploymentGraph,
    runtime_id: str,
) -> RuntimeKind:
    del context
    try:
        return graph.runtimes[runtime_id].kind
    except KeyError as error:
        raise InvalidOperationCommand("runtime effect runtime target is missing") from error


def _runtime_authority_ref_for_context(
    graph: DeploymentGraph,
    runtime_id: str,
) -> RuntimeAuthorityReference | None:
    try:
        return graph.runtimes[runtime_id].authority_ref
    except KeyError as error:
        raise InvalidOperationCommand("runtime effect runtime target is missing") from error


def _runtime_authority_deliveries_for_context(
    deliveries: tuple[RegisteredRuntimeAuthorityDelivery, ...],
    authority_ref: RuntimeAuthorityReference | None,
) -> tuple[RuntimeAuthorityAccessDelivery, ...]:
    if authority_ref is None:
        return ()
    return tuple(
        delivery.delivery
        for delivery in sorted(deliveries, key=lambda value: value.delivery_id)
        if delivery.authority_ref == authority_ref
    )


def _products_for_context(
    context: ActivityRealizationContext,
    graph: DeploymentGraph,
    runtime_id: str,
) -> tuple[RuntimeProductMaterial, ...]:
    node_id = _node_target(context)
    if node_id is None:
        return ()
    try:
        node = graph.nodes[node_id]
    except KeyError as error:
        raise InvalidOperationCommand("runtime effect node target is missing") from error
    product = _registered_product_for_node(context.registered_products, node.metadata)
    public_environment = _public_environment_for_node(
        graph=graph,
        node_id=node_id,
        node=node,
        product=product,
        registered_products=context.registered_products,
    )
    return (
        RuntimeProductMaterial(
            node_id=node_id,
            runtime_id=runtime_id,
            reference=product.reference,
            product=_product_material_for_node(context, graph, product, node),
            public_environment=public_environment,
            socket_environment=node.socket_environment,
            pull_authority=_pull_authority_for_product(
                context.image_pull_authorities,
                product.descriptor_document.product.image,
            ),
        ),
    )


def _product_material_for_node(
    context: ActivityRealizationContext,
    graph: DeploymentGraph,
    product: RegisteredProduct,
    node: Node,
):
    descriptor_product = product.descriptor_document.product
    runtime_contract = descriptor_product.runtime_contract
    return replace(
        descriptor_product,
        runtime_contract=replace(
            runtime_contract,
            verification=node.block_spec.verification,
            secret_deliveries=_secret_deliveries_for_node(
                context=context,
                graph=graph,
                node=node,
                descriptor_deliveries=runtime_contract.secret_deliveries,
            ),
        ),
    )


def _secret_deliveries_for_node(
    *,
    context: ActivityRealizationContext,
    graph: DeploymentGraph,
    node: Node,
    descriptor_deliveries: tuple[SecretDelivery, ...],
) -> tuple[SecretDelivery, ...]:
    deliveries = tuple(descriptor_deliveries) + tuple(node.secret_deliveries)
    if _has_tunnel_token_delivery(deliveries):
        return tuple(sorted(deliveries, key=secret_delivery_sort_key))
    ingress = _connector_ingress_for_node(graph, node.node_id)
    if ingress is None:
        return tuple(sorted(deliveries, key=secret_delivery_sort_key))
    resource = _ingress_resource_for(context.ingress_resources, ingress.ingress_id)
    generated = _generated_ingress_secret_for(
        context.generated_ingress_secrets,
        resource,
    )
    authority = _ingress_authority_for(
        context.ingress_authorities,
        resource,
    )
    plan = cloudflare_tunnel_token_delivery_plan(
        authority=authority.authority,
        resource=resource,
        connector_node_id=node.node_id,
        tunnel_token_ref=generated.secret_ref,
    )
    return tuple(
        sorted(
            deliveries + (plan.secret_delivery,),
            key=secret_delivery_sort_key,
        )
    )


def _has_tunnel_token_delivery(deliveries: tuple[SecretDelivery, ...]) -> bool:
    return any(
        isinstance(delivery, SecretEnvironmentDelivery)
        and delivery.environment_name == "TUNNEL_TOKEN"
        for delivery in deliveries
    )


def _connector_ingress_for_node(
    graph: DeploymentGraph,
    node_id: str,
):
    matches = tuple(
        ingress
        for ingress in graph.public_ingresses
        if ingress.connector_node_id == node_id
    )
    if len(matches) > 1:
        raise InvalidOperationCommand(
            "public ingress connector token delivery is ambiguous"
        )
    return None if not matches else matches[0]


def _ingress_resource_for(
    resources: tuple[CloudflareOwnedIngressResource, ...],
    ingress_id: str,
) -> CloudflareOwnedIngressResource:
    matches = tuple(
        resource
        for resource in resources
        if resource.ingress_id == ingress_id
        and resource.status is OwnedIngressResourceStatus.ACTIVE
    )
    if len(matches) != 1:
        raise InvalidOperationCommand(
            "public ingress connector token delivery requires owned resource evidence"
        )
    return matches[0]


def _generated_ingress_secret_for(
    secrets: tuple[GeneratedIngressSecretReference, ...],
    resource: CloudflareOwnedIngressResource,
) -> GeneratedIngressSecretReference:
    matches = tuple(
        secret
        for secret in secrets
        if secret.purpose is GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN
        and secret.source_run_id == resource.source_run_id
        and secret.source_activity_id == resource.source_activity_id
        and secret.source_event_id == resource.source_event_id
    )
    if len(matches) != 1:
        raise InvalidOperationCommand(
            "public ingress connector token delivery requires generated token evidence"
        )
    return matches[0]


def _ingress_authority_for(
    authorities: tuple[RegisteredIngressAuthority, ...],
    resource: CloudflareOwnedIngressResource,
) -> RegisteredIngressAuthority:
    matches = tuple(
        authority
        for authority in authorities
        if authority.authority_ref == resource.authority_ref
    )
    if len(matches) != 1:
        raise InvalidOperationCommand(
            "public ingress connector token delivery requires active authority evidence"
        )
    return matches[0]


def _public_environment_for_node(
    *,
    graph: DeploymentGraph,
    node_id: str,
    node: object,
    product: RegisteredProduct,
    registered_products: tuple[RegisteredProduct, ...],
) -> tuple[PublicStaticEnvironmentBinding, ...]:
    del product
    public_environment = tuple(node.public_environment)
    if not any(binding.name == _GATEWAY_TARGETS_ENVIRONMENT for binding in public_environment):
        return public_environment
    target_map = gateway_target_map_for_node(
        graph,
        node_id=node_id,
        registered_products=registered_products,
    )
    target_map_json = json.dumps(
        _gateway_process_target_map_descriptor(target_map),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return tuple(
        PublicStaticEnvironmentBinding(binding.name, target_map_json)
        if binding.name == _GATEWAY_TARGETS_ENVIRONMENT
        else binding
        for binding in public_environment
    )


def gateway_target_map_for_node(
    graph: DeploymentGraph,
    *,
    node_id: str,
    registered_products: tuple[RegisteredProduct, ...],
) -> GatewayTargetMap:
    try:
        gateway_node = graph.nodes[node_id]
    except KeyError as error:
        raise InvalidOperationCommand("gateway target map node is missing") from error
    targets: dict[GatewayTargetId, GatewayTarget] = {}
    source_edges: dict[GatewayTargetId, list[str]] = {}
    for edge in sorted(graph.edges.values(), key=lambda value: value.edge_id):
        try:
            provider_node = graph.nodes[edge.provider_role]
        except KeyError as error:
            raise InvalidOperationCommand(
                "gateway target map provider node is missing"
            ) from error
        if provider_node.runtime_id != gateway_node.runtime_id:
            continue
        target_id = GatewayTargetId(f"{edge.provider_role}.{edge.provider_socket}")
        source_edges.setdefault(target_id, []).append(edge.edge_id)
        if target_id in targets:
            continue
        product = _registered_product_for_node(
            registered_products,
            provider_node.metadata,
        )
        port = _provider_port_for_socket(product, edge.provider_socket)
        if edge.protocol == Protocol.HTTP:
            targets[target_id] = GatewayHttpTarget(
                target_id=target_id,
                node_id=edge.provider_role,
                provider_socket=edge.provider_socket,
                url=f"http://{edge.provider_role}:{port}",
                source_edges=(),
            )
        elif edge.protocol == Protocol.POSTGRES:
            postgres_target = _postgres_target_details(provider_node)
            targets[target_id] = GatewayPostgresTarget(
                target_id=target_id,
                node_id=edge.provider_role,
                provider_socket=edge.provider_socket,
                host=edge.provider_role,
                port=port,
                database=postgres_target.get("database"),
                username=postgres_target.get("username"),
                password_environment=postgres_target.get("password_environment"),
                source_edges=(),
            )
        else:
            raise InvalidOperationCommand(
                "gateway target map protocol is unsupported"
            )
    return GatewayTargetMap(
        tuple(
            _with_source_edges(target, tuple(source_edges[target.target_id]))
            for target in targets.values()
        )
    )


def gateway_control_endpoint_for_node(
    graph: DeploymentGraph,
    *,
    graph_id: str,
    node_id: str,
    registered_products: tuple[RegisteredProduct, ...],
) -> RuntimeEndpointObservation:
    """Return the graph-derived private control endpoint for one gateway node."""

    if not isinstance(graph_id, str) or not graph_id.strip():
        raise InvalidOperationCommand("gateway endpoint graph identity is missing")
    try:
        gateway_node = graph.nodes[node_id]
    except KeyError as error:
        raise InvalidOperationCommand("gateway control node is missing") from error
    try:
        provider = gateway_node.provider_socket("control")
    except KeyError as error:
        raise InvalidOperationCommand(
            "gateway node must declare an HTTP control provider socket"
        ) from error
    if provider.protocol != Protocol.HTTP:
        raise InvalidOperationCommand(
            "gateway node must declare an HTTP control provider socket"
        )
    product = _registered_product_for_node(
        registered_products,
        gateway_node.metadata,
    )
    port = _provider_port_for_socket(product, "control")
    return RuntimeEndpointObservation(
        subject_id=node_id,
        socket_name="control",
        graph_id=graph_id,
        protocol=Protocol.HTTP,
        context=EndpointContext.RUNTIME_PRIVATE,
        address=LiteralEndpointMaterial(f"http://{node_id}:{port}"),
    )


def named_public_gateway_endpoint_for_node(
    graph: DeploymentGraph,
    *,
    graph_id: str,
    node_id: str,
) -> RuntimeEndpointObservation:
    """Return the unique graph-declared public endpoint for gateway control."""

    if not isinstance(graph_id, str) or not graph_id.strip():
        raise InvalidOperationCommand("gateway endpoint graph identity is missing")
    matches = tuple(
        ingress
        for ingress in graph.public_ingresses
        if ingress.target.node_id == node_id
        and ingress.target.provider_socket == "control"
    )
    if not matches:
        raise InvalidOperationCommand(
            "gateway control node has no named public ingress"
        )
    if len(matches) != 1:
        raise InvalidOperationCommand(
            "gateway control named public ingress is ambiguous"
        )
    ingress = matches[0]
    if ingress.exposure is not PublicIngressExposure.HTTPS:
        raise InvalidOperationCommand(
            "gateway control named public ingress must use HTTPS"
        )
    return RuntimeEndpointObservation(
        subject_id=node_id,
        socket_name="control",
        graph_id=graph_id,
        protocol=Protocol.HTTP,
        context=EndpointContext.PUBLIC,
        address=LiteralEndpointMaterial(f"https://{ingress.hostname}:443"),
    )


def _with_source_edges(
    target: GatewayTarget,
    source_edges: tuple[str, ...],
) -> GatewayTarget:
    if isinstance(target, GatewayHttpTarget):
        return GatewayHttpTarget(
            target_id=target.target_id,
            node_id=target.node_id,
            provider_socket=target.provider_socket,
            url=target.url,
            source_edges=source_edges,
        )
    return GatewayPostgresTarget(
        target_id=target.target_id,
        node_id=target.node_id,
        provider_socket=target.provider_socket,
        host=target.host,
        port=target.port,
        database=target.database,
        username=target.username,
        password_environment=target.password_environment,
        source_edges=source_edges,
    )


def _postgres_target_details(provider_node: object) -> dict[str, str]:
    public_environment = {
        binding.name: binding.value
        for binding in getattr(provider_node, "public_environment", ())
    }
    secret_environment_names = {
        delivery.environment_name
        for delivery in getattr(provider_node, "secret_deliveries", ())
        if isinstance(delivery, SecretEnvironmentDelivery)
    }
    details: dict[str, str] = {}
    if "POSTGRES_DB" in public_environment:
        details["database"] = public_environment["POSTGRES_DB"]
    if "POSTGRES_USER" in public_environment:
        details["username"] = public_environment["POSTGRES_USER"]
    if "POSTGRES_PASSWORD" in secret_environment_names:
        details["password_environment"] = "POSTGRES_PASSWORD"
    return details


def _provider_port_for_socket(product: RegisteredProduct, provider_socket: str) -> int:
    for port in product.descriptor_document.product.runtime_contract.provider_ports:
        if port.provider_socket == provider_socket:
            return port.container_port
    raise InvalidOperationCommand("gateway target provider port is missing")


def _gateway_process_target_map_descriptor(
    target_map: GatewayTargetMap,
) -> dict[str, object]:
    descriptor: dict[str, object] = {}
    for target in target_map.targets:
        if isinstance(target, GatewayHttpTarget):
            descriptor[target.target_id.value] = {
                "protocol": "http",
                "url": target.url,
            }
        else:
            postgres_descriptor = {
                "protocol": "postgres",
                "host": target.host,
                "port": target.port,
            }
            if target.database is not None:
                postgres_descriptor["database"] = target.database
            if target.username is not None:
                postgres_descriptor["username"] = target.username
            if target.password_environment is not None:
                postgres_descriptor["password_environment"] = target.password_environment
            descriptor[target.target_id.value] = postgres_descriptor
    return descriptor


def _node_target(context: ActivityRealizationContext) -> str | None:
    operation = context.activity.operation
    match operation:
        case (
            StartNode(target=NodeTarget(node_id=node_id))
            | StopNode(target=NodeTarget(node_id=node_id))
            | RemoveNodeResource(target=NodeTarget(node_id=node_id))
            | WaitForHealthy(target=NodeTarget(node_id=node_id))
            | ReconcileNode(target=NodeTarget(node_id=node_id))
        ):
            return node_id
        case (
            AddSocketConnection()
            | SwitchSocketConnection()
            | RemoveSocketConnection()
        ):
            return None
        case _:
            return None


def _registered_product_for_node(
    products: tuple[RegisteredProduct, ...],
    metadata: Mapping[str, object],
) -> RegisteredProduct:
    identity = _product_identity(metadata.get("product_identity"))
    digest = _descriptor_digest(metadata.get("product_descriptor_digest"))
    reference = ProductReference(identity, digest)
    for product in products:
        if product.reference == reference:
            return product
    raise InvalidOperationCommand("runtime effect product reference is not registered")


def _pull_authority_for_product(
    authorities: tuple[RegisteredImagePullAuthority, ...],
    image: object,
) -> ImagePullAuthority | None:
    if not hasattr(image, "registry") or not hasattr(image, "repository"):
        return None
    matches = tuple(
        authority.authority
        for authority in authorities
        if authority.authority.permits(image)
    )
    if not matches:
        return None
    return sorted(
        matches,
        key=lambda authority: (
            0 if authority.repository is None else len(authority.repository),
            authority.registry,
            authority.repository or "",
            authority.credential_reference.reference_id,
        ),
        reverse=True,
    )[0]


def _product_identity(value: object) -> ProductIdentity:
    if not isinstance(value, str):
        raise InvalidOperationCommand("runtime effect product identity is malformed")
    parts = value.split("/")
    if len(parts) != 3:
        raise InvalidOperationCommand("runtime effect product identity is malformed")
    try:
        revision = int(parts[2])
    except ValueError as error:
        raise InvalidOperationCommand(
            "runtime effect product identity is malformed"
        ) from error
    return ProductIdentity(parts[0], parts[1], revision)


def _descriptor_digest(value: object) -> ProductDescriptorDigest:
    if not isinstance(value, str):
        raise InvalidOperationCommand("runtime effect product descriptor is malformed")
    return ProductDescriptorDigest(value)
