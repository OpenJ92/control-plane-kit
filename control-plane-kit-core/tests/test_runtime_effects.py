from __future__ import annotations

import unittest

from control_plane_kit_core.algebra import BlockSockets, ProviderSocket
from control_plane_kit_core.environment import (
    PublicStaticEnvironmentBinding,
    SocketDerivedEnvironmentBinding,
)
from control_plane_kit_core.operations.execution import EffectResultKind
from control_plane_kit_core.planning import ActivityId, NodeTarget, StartNode
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
    ImagePullAuthority,
    ImagePullAuthorityCodec,
    RuntimeEffectContractError,
    RuntimeEffectFailure,
    RuntimeEffectKind,
    RuntimeEffectRequest,
    RuntimeEffectResult,
    RuntimeEffectSource,
    RuntimeProductMaterial,
)
from control_plane_kit_core.types import Protocol, RuntimeKind


class RuntimeEffectContractTests(unittest.TestCase):
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
