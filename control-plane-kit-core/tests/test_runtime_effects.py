from __future__ import annotations

import unittest

from control_plane_kit_core.algebra import BlockSockets, ProviderSocket
from control_plane_kit_core.environment import (
    PublicStaticEnvironmentBinding,
    SocketDerivedEnvironmentBinding,
)
from control_plane_kit_core.operations.execution import EffectResultKind
from control_plane_kit_core.planning import (
    ActivityId,
    ChangeTarget,
    NodeTarget,
    ReviewChange,
    ReviewReason,
    StartNode,
)
from control_plane_kit_core.probe_intents import (
    EndpointContext,
    LiteralEndpointMaterial,
    RuntimeEndpointObservation,
)
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    ProductDescriptorDigest,
    ProductIdentity,
    ProductReference,
    ProductRuntimeContract,
    ProviderRuntimePort,
)
from control_plane_kit_core.runtime_effects import (
    GatewayHttpTarget,
    GatewayPostgresTarget,
    GatewayTargetId,
    GatewayTargetMap,
    GatewayTargetMapCodec,
    ImagePullAuthority,
    ImagePullAuthorityCodec,
    RuntimeAuthorityAccessDelivery,
    RuntimeAuthorityAccessDeliveryCodec,
    RuntimeAuthorityAccessDeliveryKind,
    RuntimeAuthorityDeliverySecretReference,
    RuntimeAuthorityDeliverySecretReferenceCodec,
    RuntimeAuthorityReference,
    RuntimeAuthorityReferenceCodec,
    RuntimeEffectContractError,
    RuntimeEffectFailure,
    RuntimeEffectKind,
    RuntimeEffectRequest,
    RuntimeEffectResult,
    RuntimeEffectSource,
    RuntimeProductMaterial,
)
from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference,
    SecretReference,
    SecretResolutionGrant,
    SecretUseIntent,
)
from control_plane_kit_core.types import Protocol, RuntimeKind
from control_plane_kit_core.topology import GraphSubject


class RuntimeEffectContractTests(unittest.TestCase):
    def test_runtime_authority_reference_is_secret_free_named_authority(self) -> None:
        reference = RuntimeAuthorityReference("mac-mini-docker")

        descriptor = RuntimeAuthorityReferenceCodec().encode(reference)

        self.assertEqual(descriptor, {"reference_id": "mac-mini-docker"})
        self.assertEqual(RuntimeAuthorityReferenceCodec().decode(descriptor), reference)
        self.assertNotIn("docker.sock", repr(descriptor))

    def test_runtime_authority_reference_fails_closed_on_material_or_secrets(self) -> None:
        invalid = (
            "",
            "MacMiniDocker",
            "mac mini docker",
            "remote/docker",
            "tcp://mac-mini.local:2376",
            "/var/run/docker.sock",
            '{"auths": {"ghcr.io": "do-not-store"}}',
            "password=do-not-store",
            "token=do-not-store",
            "secret=do-not-store",
            "begin-private-key",
            "dockerconfigjson",
        )

        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(RuntimeEffectContractError):
                    RuntimeAuthorityReference(value)

        descriptor = RuntimeAuthorityReference("mac-mini-docker").descriptor()
        with self.assertRaisesRegex(RuntimeEffectContractError, "unknown keys"):
            RuntimeAuthorityReferenceCodec().decode(
                {**descriptor, "endpoint": "tcp://mac-mini.local:2376"}
            )

    def test_runtime_authority_access_delivery_is_secret_free_contract(self) -> None:
        delivery = RuntimeAuthorityAccessDelivery(
            authority_ref=RuntimeAuthorityReference("mac-mini-docker"),
            delivery_kind=RuntimeAuthorityAccessDeliveryKind.LOCAL_DOCKER_SOCKET_MOUNT,
        )

        descriptor = RuntimeAuthorityAccessDeliveryCodec().encode(delivery)

        self.assertEqual(
            descriptor,
            {
                "authority_ref": {"reference_id": "mac-mini-docker"},
                "delivery_kind": "local-docker-socket-mount",
                "secret_references": [],
            },
        )
        self.assertEqual(
            RuntimeAuthorityAccessDeliveryCodec().decode(descriptor),
            delivery,
        )
        self.assertNotIn("/var/run/docker.sock", repr(descriptor))
        self.assertNotIn("tcp://", repr(descriptor))

    def test_runtime_authority_access_delivery_can_reference_tls_secrets(self) -> None:
        delivery = RuntimeAuthorityAccessDelivery(
            authority_ref=RuntimeAuthorityReference("mac-mini-docker"),
            delivery_kind=(
                RuntimeAuthorityAccessDeliveryKind.REMOTE_DOCKER_TLS_SECRET_FILES
            ),
            secret_references=(
                RuntimeAuthorityDeliverySecretReference(
                    "ca-cert",
                    "secret://local/workspace-a/docker/ca-cert",
                ),
                RuntimeAuthorityDeliverySecretReference(
                    "client-cert",
                    "secret://local/workspace-a/docker/client-cert",
                ),
                RuntimeAuthorityDeliverySecretReference(
                    "client-key",
                    "secret://local/workspace-a/docker/client-key",
                ),
            ),
        )

        descriptor = RuntimeAuthorityAccessDeliveryCodec().encode(delivery)

        self.assertEqual(
            tuple(value["label"] for value in descriptor["secret_references"]),
            ("ca-cert", "client-cert", "client-key"),
        )
        self.assertEqual(
            RuntimeAuthorityAccessDeliveryCodec().decode(descriptor),
            delivery,
        )
        self.assertNotIn("BEGIN", repr(descriptor))
        self.assertNotIn("PRIVATE KEY", repr(descriptor))

    def test_runtime_authority_access_delivery_fails_closed_on_material(self) -> None:
        descriptor = RuntimeAuthorityAccessDelivery(
            authority_ref=RuntimeAuthorityReference("mac-mini-docker"),
            delivery_kind=RuntimeAuthorityAccessDeliveryKind.LOCAL_DOCKER_SOCKET_MOUNT,
        ).descriptor()

        invalid_descriptors = (
            {**descriptor, "host_path": "/var/run/docker.sock"},
            {**descriptor, "endpoint": "tcp://docker.local:2376"},
            {**descriptor, "token": "do-not-store"},
            {
                **descriptor,
                "delivery_kind": "ambient-env",
            },
            {
                **descriptor,
                "secret_references": [
                    {
                        "label": "client-key",
                        "reference_id": "secret://local/workspace-a/docker/key",
                        "target_path": "/run/secrets/docker-key",
                    }
                ],
            },
        )

        for value in invalid_descriptors:
            with self.subTest(value=value):
                with self.assertRaises(RuntimeEffectContractError):
                    RuntimeAuthorityAccessDeliveryCodec().decode(value)

        with self.assertRaises(RuntimeEffectContractError):
            RuntimeAuthorityAccessDelivery(
                authority_ref=RuntimeAuthorityReference("mac-mini-docker"),
                delivery_kind=(
                    RuntimeAuthorityAccessDeliveryKind.LOCAL_DOCKER_SOCKET_MOUNT
                ),
                secret_references=(
                    RuntimeAuthorityDeliverySecretReference(
                        "client-key",
                        "secret://local/workspace-a/docker/client-key",
                    ),
                ),
            )

    def test_runtime_authority_delivery_secret_reference_codec_is_strict(self) -> None:
        reference = RuntimeAuthorityDeliverySecretReference(
            "client-cert",
            "secret://local/workspace-a/docker/client-cert",
        )

        descriptor = RuntimeAuthorityDeliverySecretReferenceCodec().encode(reference)

        self.assertEqual(
            descriptor,
            {
                "label": "client-cert",
                "reference_id": "secret://local/workspace-a/docker/client-cert",
            },
        )
        self.assertEqual(
            RuntimeAuthorityDeliverySecretReferenceCodec().decode(descriptor),
            reference,
        )
        with self.assertRaises(RuntimeEffectContractError):
            RuntimeAuthorityDeliverySecretReference(
                "token",
                "secret://local/workspace-a/docker/token",
            )

    def test_request_descriptor_carries_pinned_runtime_material_without_docker(self) -> None:
        request = RuntimeEffectRequest(
            effect_id="effect-a",
            kind=RuntimeEffectKind.REALIZE_ACTIVITY,
            runtime_kind=RuntimeKind.DOCKER,
            source=_source(),
            activity_id=ActivityId("activity-a"),
            operation=StartNode(NodeTarget("api")),
            products=(_product_material(),),
        )

        descriptor = request.descriptor()

        self.assertEqual(descriptor["kind"], "realize-activity")
        self.assertEqual(descriptor["runtime_kind"], "docker")
        self.assertIsNone(descriptor["authority_ref"])
        self.assertEqual(descriptor["authority_deliveries"], [])
        self.assertEqual(descriptor["secret_resolution_grants"], [])
        self.assertEqual(
            descriptor["source"],
            {
                "workspace_id": "workspace-a",
                "request_id": "request-a",
                "run_id": "run-a",
                "plan_id": "plan-a",
                "base_graph_id": "graph-base",
                "desired_graph_id": "graph-desired",
                "intent_event_id": "event-started",
            },
        )
        self.assertEqual(
            descriptor["operation"],
            {
                "kind": "start-node",
                "target": {"kind": "node", "node_id": "api"},
            },
        )
        product = descriptor["products"][0]
        self.assertEqual(product["node_id"], "api")
        self.assertEqual(product["runtime_id"], "docker")
        self.assertEqual(
            product["public_environment"],
            [
                {
                    "kind": "public-static",
                    "name": "HELLO_MESSAGE",
                    "value": "Hello from graph",
                }
            ],
        )
        self.assertEqual(
            product["socket_environment"],
            [
                {
                    "kind": "socket-derived",
                    "name": "UPSTREAM_URL",
                    "value": "http://upstream:8080",
                    "edge_id": "upstream.internal->api.upstream",
                }
            ],
        )
        self.assertEqual(
            product["product"]["image"]["digest"],
            "sha256:" + "a" * 64,
        )
        self.assertEqual(
            RuntimeProductMaterial.from_descriptor(product).socket_environment,
            (
                SocketDerivedEnvironmentBinding(
                    "UPSTREAM_URL",
                    "http://upstream:8080",
                    "upstream.internal->api.upstream",
                ),
            ),
        )
        self.assertEqual(
            RuntimeProductMaterial.from_descriptor(product).public_environment,
            (PublicStaticEnvironmentBinding("HELLO_MESSAGE", "Hello from graph"),),
        )

    def test_request_carries_explicit_authority_delivery_without_socket_material(self) -> None:
        delivery = RuntimeAuthorityAccessDelivery(
            RuntimeAuthorityReference("local-docker"),
            RuntimeAuthorityAccessDeliveryKind.LOCAL_DOCKER_SOCKET_MOUNT,
        )
        request = RuntimeEffectRequest(
            effect_id="effect-a",
            kind=RuntimeEffectKind.REALIZE_ACTIVITY,
            runtime_kind=RuntimeKind.DOCKER,
            source=_source(),
            activity_id=ActivityId("activity-a"),
            operation=StartNode(NodeTarget("api")),
            authority_ref=RuntimeAuthorityReference("local-docker"),
            authority_deliveries=(delivery,),
            products=(_product_material(),),
        )

        descriptor = request.descriptor()

        self.assertEqual(
            descriptor["authority_deliveries"],
            [
                {
                    "authority_ref": {"reference_id": "local-docker"},
                    "delivery_kind": "local-docker-socket-mount",
                    "secret_references": [],
                }
            ],
        )
        self.assertNotIn("/var/run/docker.sock", repr(descriptor))
        self.assertNotIn("unix://", repr(descriptor))

    def test_request_rejects_delivery_without_matching_authority_reference(self) -> None:
        delivery = RuntimeAuthorityAccessDelivery(
            RuntimeAuthorityReference("local-docker"),
            RuntimeAuthorityAccessDeliveryKind.LOCAL_DOCKER_SOCKET_MOUNT,
        )

        with self.assertRaises(RuntimeEffectContractError):
            RuntimeEffectRequest(
                effect_id="effect-a",
                kind=RuntimeEffectKind.REALIZE_ACTIVITY,
                runtime_kind=RuntimeKind.DOCKER,
                source=_source(),
                activity_id=ActivityId("activity-a"),
                operation=StartNode(NodeTarget("api")),
                authority_deliveries=(delivery,),
                products=(_product_material(),),
            )

        with self.assertRaises(RuntimeEffectContractError):
            RuntimeEffectRequest(
                effect_id="effect-a",
                kind=RuntimeEffectKind.REALIZE_ACTIVITY,
                runtime_kind=RuntimeKind.DOCKER,
                source=_source(),
                activity_id=ActivityId("activity-a"),
                operation=StartNode(NodeTarget("api")),
                authority_ref=RuntimeAuthorityReference("other-docker"),
                authority_deliveries=(delivery,),
                products=(_product_material(),),
            )

    def test_request_descriptor_carries_runtime_authority_reference_not_material(self) -> None:
        request = RuntimeEffectRequest(
            effect_id="effect-a",
            kind=RuntimeEffectKind.REALIZE_ACTIVITY,
            runtime_kind=RuntimeKind.DOCKER,
            authority_ref=RuntimeAuthorityReference("mac-mini-docker"),
            source=_source(),
            activity_id=ActivityId("activity-a"),
            operation=StartNode(NodeTarget("api")),
        )

        descriptor = request.descriptor()

        self.assertEqual(
            descriptor["authority_ref"],
            {"reference_id": "mac-mini-docker"},
        )
        self.assertNotIn("tcp://", repr(descriptor))
        self.assertNotIn("docker.sock", repr(descriptor))
        self.assertNotIn("token=", repr(descriptor))

    def test_request_carries_only_matching_committed_secret_grants(self) -> None:
        grant = SecretResolutionGrant(
            authorization_id="suse_" + "a" * 64,
            workspace_id="workspace-a",
            reference_registration_id="sref_" + "b" * 64,
            provider_registration_id="sprov_" + "c" * 64,
            endpoint_reference=SecretProviderEndpointReference("provider-a"),
            credential_reference=SecretReference(
                "secret://bootstrap/provider-a-token"
            ),
            reference=SecretReference("secret://provider-a/app/token"),
            intent=SecretUseIntent.APPLICATION_CONTROL_TOKEN,
            actor_subject="worker-a",
            correlation_id="secret-use-" + "d" * 64,
            intent_fingerprint="e" * 64,
            effect_id="effect-a",
        )
        request = RuntimeEffectRequest(
            effect_id="effect-a",
            kind=RuntimeEffectKind.REALIZE_ACTIVITY,
            runtime_kind=RuntimeKind.DOCKER,
            source=_source(),
            activity_id=ActivityId("activity-a"),
            operation=StartNode(NodeTarget("api")),
            secret_resolution_grants=(grant,),
            products=(_product_material(),),
        )

        self.assertEqual(request.secret_resolution_grants, (grant,))
        self.assertEqual(
            request.descriptor()["secret_resolution_grants"],
            [grant.descriptor()],
        )
        with self.assertRaises(RuntimeEffectContractError):
            RuntimeEffectRequest(
                effect_id="other-effect",
                kind=RuntimeEffectKind.REALIZE_ACTIVITY,
                runtime_kind=RuntimeKind.DOCKER,
                source=_source(),
                activity_id=ActivityId("activity-a"),
                operation=StartNode(NodeTarget("api")),
                secret_resolution_grants=(grant,),
            )

    def test_request_rejects_duplicate_secret_grants(self) -> None:
        grant = SecretResolutionGrant(
            authorization_id="suse_" + "a" * 64,
            workspace_id="workspace-a",
            reference_registration_id="sref_" + "b" * 64,
            provider_registration_id="sprov_" + "c" * 64,
            endpoint_reference=SecretProviderEndpointReference("provider-a"),
            credential_reference=SecretReference(
                "secret://bootstrap/provider-a-token"
            ),
            reference=SecretReference("secret://provider-a/app/token"),
            intent=SecretUseIntent.APPLICATION_CONTROL_TOKEN,
            actor_subject="worker-a",
            correlation_id="secret-use-" + "d" * 64,
            intent_fingerprint="e" * 64,
            effect_id="effect-a",
        )

        with self.assertRaisesRegex(RuntimeEffectContractError, "unique"):
            RuntimeEffectRequest(
                effect_id="effect-a",
                kind=RuntimeEffectKind.REALIZE_ACTIVITY,
                runtime_kind=RuntimeKind.DOCKER,
                source=_source(),
                activity_id=ActivityId("activity-a"),
                operation=StartNode(NodeTarget("api")),
                secret_resolution_grants=(grant, grant),
            )

    def test_request_rejects_open_kind_and_review_work(self) -> None:
        common = {
            "effect_id": "effect-a",
            "runtime_kind": RuntimeKind.DOCKER,
            "source": _source(),
            "activity_id": ActivityId("activity-a"),
        }
        with self.assertRaisesRegex(RuntimeEffectContractError, "kind must be closed"):
            RuntimeEffectRequest(
                **common,
                kind="realize-activity",  # type: ignore[arg-type]
                operation=StartNode(NodeTarget("api")),
            )
        with self.assertRaisesRegex(RuntimeEffectContractError, "review work"):
            RuntimeEffectRequest(
                **common,
                kind=RuntimeEffectKind.REALIZE_ACTIVITY,
                operation=ReviewChange(
                    ChangeTarget(GraphSubject()),
                    ReviewReason.UNSUPPORTED_CHANGE,
                ),
            )

    def test_product_material_rejects_identity_mismatch(self) -> None:
        identity = ProductIdentity("openj92", "hello-server", 1)
        other = ProductIdentity("openj92", "router", 1)

        with self.assertRaises(RuntimeEffectContractError):
            RuntimeProductMaterial(
                node_id="api",
                runtime_id="docker",
                reference=ProductReference(other, ProductDescriptorDigest("b" * 64)),
                product=_product(identity),
            )

    def test_product_material_rejects_wrong_or_duplicate_environment_material(self) -> None:
        identity = ProductIdentity("openj92", "hello-server", 1)
        with self.assertRaises(RuntimeEffectContractError):
            RuntimeProductMaterial(
                node_id="api",
                runtime_id="docker",
                reference=ProductReference(identity, ProductDescriptorDigest("b" * 64)),
                product=_product(identity),
                public_environment=(
                    SocketDerivedEnvironmentBinding(
                        "HELLO_MESSAGE",
                        "http://api:8080",
                        "api.internal->router.active",
                    ),
                ),
            )
        with self.assertRaises(RuntimeEffectContractError):
            RuntimeProductMaterial(
                node_id="api",
                runtime_id="docker",
                reference=ProductReference(identity, ProductDescriptorDigest("b" * 64)),
                product=_product(identity),
                public_environment=(
                    PublicStaticEnvironmentBinding("HELLO_MESSAGE", "a"),
                    PublicStaticEnvironmentBinding("HELLO_MESSAGE", "b"),
                ),
            )
        with self.assertRaises(RuntimeEffectContractError):
            RuntimeProductMaterial(
                node_id="api",
                runtime_id="docker",
                reference=ProductReference(identity, ProductDescriptorDigest("b" * 64)),
                product=_product(identity),
                socket_environment=(
                    PublicStaticEnvironmentBinding("UPSTREAM_URL", "http://api:8080"),
                ),
            )
        with self.assertRaises(RuntimeEffectContractError):
            RuntimeProductMaterial(
                node_id="api",
                runtime_id="docker",
                reference=ProductReference(identity, ProductDescriptorDigest("b" * 64)),
                product=_product(identity),
                socket_environment=(
                    SocketDerivedEnvironmentBinding(
                        "UPSTREAM_URL",
                        "http://a:8080",
                        "a.internal->api.upstream",
                    ),
                    SocketDerivedEnvironmentBinding(
                        "UPSTREAM_URL",
                        "http://b:8080",
                        "b.internal->api.upstream",
                    ),
                ),
            )

    def test_image_pull_authority_is_secret_free_runtime_material(self) -> None:
        authority = ImagePullAuthority(
            registry="ghcr.io",
            repository="openj92/control-plane-kit-servers",
            credential_reference="secret://local/workspace-a/ghcr-read-token",
        )

        descriptor = ImagePullAuthorityCodec().encode(authority)

        self.assertEqual(
            descriptor,
            {
                "registry": "ghcr.io",
                "repository": "openj92/control-plane-kit-servers",
                "credential_reference": "secret://local/workspace-a/ghcr-read-token",
            },
        )
        self.assertEqual(ImagePullAuthorityCodec().decode(descriptor), authority)
        self.assertTrue(
            authority.permits(
                OciImageReference(
                    registry="ghcr.io",
                    repository="openj92/control-plane-kit-servers/hello-server",
                    digest="sha256:" + "a" * 64,
                )
            )
        )
        self.assertFalse(
            authority.permits(
                OciImageReference(
                    registry="docker.io",
                    repository="library/postgres",
                    digest="sha256:" + "a" * 64,
                )
            )
        )

    def test_image_pull_authority_fails_closed_on_unknown_or_secret_material(self) -> None:
        authority = ImagePullAuthority(
            registry="ghcr.io",
            repository=None,
            credential_reference="secret://local/workspace-a/ghcr-read-token",
        )
        descriptor = ImagePullAuthorityCodec().encode(authority)

        with self.assertRaisesRegex(RuntimeEffectContractError, "unknown keys"):
            ImagePullAuthorityCodec().decode({**descriptor, "token": "do-not-store"})
        with self.assertRaises(RuntimeEffectContractError):
            ImagePullAuthority(
                registry="ghcr.io",
                repository=None,
                credential_reference="ghp_do-not-store",
            )

    def test_product_material_carries_pull_authority_reference_not_credentials(self) -> None:
        material = RuntimeProductMaterial(
            node_id="api",
            runtime_id="docker",
            reference=_product_material().reference,
            product=_product_material().product,
            pull_authority=ImagePullAuthority(
                registry="ghcr.io",
                repository="openj92/control-plane-kit-servers",
                credential_reference="secret://local/workspace-a/ghcr-read-token",
            ),
        )

        descriptor = material.descriptor()

        self.assertEqual(
            descriptor["pull_authority"]["credential_reference"],
            "secret://local/workspace-a/ghcr-read-token",
        )
        self.assertNotIn("token=", repr(descriptor))
        self.assertEqual(RuntimeProductMaterial.from_descriptor(descriptor), material)

    def test_result_descriptor_preserves_observations_as_pure_evidence(self) -> None:
        result = RuntimeEffectResult.succeeded(
            "effect-a",
            evidence={"container": "cpk-api"},
            observations=(
                RuntimeEndpointObservation(
                    subject_id="api",
                    socket_name="http",
                    graph_id="graph-desired",
                    protocol=Protocol.HTTP,
                    context=EndpointContext.RUNTIME_PRIVATE,
                    address=LiteralEndpointMaterial("http://api:8000"),
                ),
            ),
        )

        self.assertEqual(result.kind, EffectResultKind.SUCCEEDED)
        self.assertEqual(
            result.descriptor()["observations"],
            [
                {
                    "subject_id": "api",
                    "socket_name": "http",
                    "graph_id": "graph-desired",
                    "protocol": {"transport": "tcp", "application": "http"},
                    "context": "runtime-private",
                    "address": {"kind": "literal", "value": "http://api:8000"},
                }
            ],
        )

    def test_failure_and_evidence_reject_secret_shaped_text(self) -> None:
        with self.assertRaises(RuntimeEffectContractError):
            RuntimeEffectResult.succeeded(
                "effect-a",
                evidence={"reason": "token=do-not-store"},
            )

        with self.assertRaises(RuntimeEffectContractError):
            RuntimeEffectFailure(
                "runtime.failure",
                "password=do-not-store",
            )

    def test_gateway_target_map_encodes_http_and_postgres_targets(self) -> None:
        target_map = GatewayTargetMap(
            targets=(
                GatewayPostgresTarget(
                    target_id=GatewayTargetId("postgres.postgres"),
                    node_id="postgres",
                    provider_socket="postgres",
                    host="postgres",
                    port=5432,
                    database="cpk",
                    username="cpk",
                    password_environment="POSTGRES_PASSWORD",
                    source_edges=("api.store->postgres.postgres",),
                ),
                GatewayHttpTarget(
                    target_id=GatewayTargetId("router.internal"),
                    node_id="router",
                    provider_socket="internal",
                    url="http://router:8000",
                    source_edges=("client.http->router.internal",),
                ),
            )
        )

        descriptor = GatewayTargetMapCodec().encode(target_map)

        self.assertEqual(
            descriptor,
            {
                "targets": [
                    {
                        "kind": "postgres",
                        "target_id": "postgres.postgres",
                        "node_id": "postgres",
                        "provider_socket": "postgres",
                        "protocol": {
                            "transport": "tcp",
                            "application": "postgres",
                        },
                        "host": "postgres",
                        "port": 5432,
                        "database": "cpk",
                        "username": "cpk",
                        "password_environment": "POSTGRES_PASSWORD",
                        "source_edges": ["api.store->postgres.postgres"],
                    },
                    {
                        "kind": "http",
                        "target_id": "router.internal",
                        "node_id": "router",
                        "provider_socket": "internal",
                        "protocol": {
                            "transport": "tcp",
                            "application": "http",
                        },
                        "url": "http://router:8000",
                        "source_edges": ["client.http->router.internal"],
                    },
                ]
            },
        )
        self.assertEqual(GatewayTargetMapCodec().decode(descriptor), target_map)
        self.assertNotIn("cpk-local-gateway", repr(descriptor))

    def test_gateway_target_map_fails_closed_on_duplicate_targets(self) -> None:
        with self.assertRaisesRegex(RuntimeEffectContractError, "unique"):
            GatewayTargetMap(
                targets=(
                    GatewayHttpTarget(
                        target_id=GatewayTargetId("router.internal"),
                        node_id="router",
                        provider_socket="internal",
                        url="http://router:8000",
                    ),
                    GatewayHttpTarget(
                        target_id=GatewayTargetId("router.internal"),
                        node_id="router",
                        provider_socket="internal",
                        url="http://router:9000",
                    ),
                )
            )

    def test_gateway_target_map_codec_fails_closed_on_unknown_protocol(self) -> None:
        descriptor = GatewayTargetMapCodec().encode(
            GatewayTargetMap(
                targets=(
                    GatewayHttpTarget(
                        target_id=GatewayTargetId("router.internal"),
                        node_id="router",
                        provider_socket="internal",
                        url="http://router:8000",
                    ),
                )
            )
        )
        descriptor["targets"][0]["protocol"] = {
            "transport": "tcp",
            "application": "redis",
        }

        with self.assertRaisesRegex(RuntimeEffectContractError, "protocol"):
            GatewayTargetMapCodec().decode(descriptor)

    def test_gateway_target_map_rejects_credentials_and_secret_shaped_values(self) -> None:
        with self.assertRaisesRegex(RuntimeEffectContractError, "credentials"):
            GatewayHttpTarget(
                target_id=GatewayTargetId("router.internal"),
                node_id="router",
                provider_socket="internal",
                url="http://user:pass@router:8000",
            )

        with self.assertRaises(RuntimeEffectContractError):
            GatewayPostgresTarget(
                target_id=GatewayTargetId("postgres.postgres"),
                node_id="postgres",
                provider_socket="postgres",
                host="password=do-not-store",
                port=5432,
            )

        descriptor = {
            "targets": [
                {
                    "kind": "postgres",
                    "target_id": "postgres.postgres",
                    "node_id": "postgres",
                    "provider_socket": "postgres",
                    "protocol": {
                        "transport": "tcp",
                        "application": "postgres",
                    },
                    "host": "postgres",
                    "port": 5432,
                    "database": None,
                    "username": None,
                    "password_environment": None,
                    "source_edges": [],
                    "password": "do-not-store",
                }
            ]
        }
        with self.assertRaisesRegex(RuntimeEffectContractError, "unknown"):
            GatewayTargetMapCodec().decode(descriptor)

        with self.assertRaisesRegex(RuntimeEffectContractError, "environment"):
            GatewayPostgresTarget(
                target_id=GatewayTargetId("postgres.postgres"),
                node_id="postgres",
                provider_socket="postgres",
                host="postgres",
                port=5432,
                password_environment="password=do-not-store",
            )


def _source() -> RuntimeEffectSource:
    return RuntimeEffectSource(
        workspace_id="workspace-a",
        request_id="request-a",
        run_id="run-a",
        plan_id="plan-a",
        base_graph_id="graph-base",
        desired_graph_id="graph-desired",
        intent_event_id="event-started",
    )


def _product_material() -> RuntimeProductMaterial:
    identity = ProductIdentity("openj92", "hello-server", 1)
    return RuntimeProductMaterial(
        node_id="api",
        runtime_id="docker",
        reference=ProductReference(identity, ProductDescriptorDigest("b" * 64)),
        product=_product(identity),
        public_environment=(
            PublicStaticEnvironmentBinding("HELLO_MESSAGE", "Hello from graph"),
        ),
        socket_environment=(
            SocketDerivedEnvironmentBinding(
                "UPSTREAM_URL",
                "http://upstream:8080",
                "upstream.internal->api.upstream",
            ),
        ),
    )


def _product(identity: ProductIdentity) -> ContainerServerProduct:
    return ContainerServerProduct(
        identity=identity,
        image=OciImageReference(
            registry="ghcr.io",
            repository="openj92/control-plane-kit-servers/hello-server",
            digest="sha256:" + "a" * 64,
        ),
        runtime_contract=ProductRuntimeContract(
            sockets=BlockSockets(providers=(ProviderSocket("http", Protocol.HTTP),)),
            provider_ports=(ProviderRuntimePort("http", 8000),),
        ),
    )


if __name__ == "__main__":
    unittest.main()
