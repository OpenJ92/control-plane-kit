from __future__ import annotations

from dataclasses import fields, replace
import hashlib
import importlib
import unittest

import rfc8785

from control_plane_kit_core.algebra import BlockSockets, ProviderSocket, RequirementSocket
from control_plane_kit_core.environment import (
    PublicStaticEnvironmentBinding,
    SocketDerivedEnvironmentBinding,
)
from control_plane_kit_core.operations.run_identity import RunId
from control_plane_kit_core.planning import ActivityId, NodeTarget, StartNode, StopNode
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
    OciPlatform,
    ProductDescriptorDigest,
    ProductIdentity,
    ProductReference,
    ProductRuntimeContract,
    ProviderRuntimePort,
)
from control_plane_kit_core.runtime_authority import (
    RuntimeAuthorityAccessDelivery,
    RuntimeAuthorityAccessDeliveryKind,
    RuntimeAuthorityDeliverySecretReference,
    RuntimeAuthorityReference,
    RuntimeEffectContractError,
)
from control_plane_kit_core.runtime_effects import (
    ImagePullAuthority,
    RuntimeEffectKind,
    RuntimeEffectRequest,
    RuntimeEffectSource,
    RuntimeProductMaterial,
)
from control_plane_kit_core.secrets import (
    CredentialReference,
    SecretEnvironmentDelivery,
    SecretReference,
    SecretUseIntent,
)
from control_plane_kit_core.types import Protocol, RuntimeKind
from control_plane_kit_core.verification import (
    PostgresPasswordAuthentication,
    PostgresQueryCheck,
    VerificationContract,
)


MODULE = "control_plane_kit_core.runtime_effect_observation"
INTENT_DOMAIN = b"control-plane-kit.runtime-effect-intent.v1\x00"
PRODUCT_MATERIAL_CANONICAL = b'{"node_id":"api","product":{"description":null,"display_name":null,"identity":{"contract_revision":1,"name":"hello-server","namespace":"openj92"},"image":{"digest":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","platforms":[{"architecture":"amd64","os":"linux","variant":null}],"provenance":{"build":"build-a"},"registry":"ghcr.io","repository":"openj92/control-plane-kit-servers/hello-server","tag":"stable"},"kind":"container-server","product_family":"server","runtime_contract":{"capabilities":[],"configuration_artifacts":[],"lifecycle":{"compute":"ephemeral","data":[],"ownership":"owned"},"provider_ports":[{"container_port":5432,"provider_socket":"database"},{"container_port":5433,"provider_socket":"database-alt"},{"container_port":8000,"provider_socket":"http"}],"public_environment":[],"retained_data_mounts":[],"secret_deliveries":[{"environment_name":"APP_CONTROL_TOKEN","intent":"application.control-token","kind":"environment","reference_id":"secret://local/workspace-a/app/token"}],"sockets":{"providers":{"database":{"protocol":{"application":"postgres","transport":"tcp"}},"database-alt":{"protocol":{"application":"postgres","transport":"tcp"}},"http":{"protocol":{"application":"http","transport":"tcp"}}},"requirements":{}},"verification":{"checks":[{"authentication":{"database":"app","kind":"password","password_reference_id":"secret://local/workspace-a/postgres/password","username":"app"},"check_id":"database-ready","kind":"postgres-query","operation":"select-one","policy":{"interval_seconds":1,"maximum_attempts":1,"maximum_evidence_bytes":16384,"timeout_seconds":5},"provider_socket":"database"}]}}},"public_environment":[{"kind":"public-static","name":"HELLO_MESSAGE","value":"Hello from graph"}],"pull_authority":{"credential_reference":"secret://local/workspace-a/oci/pull","registry":"ghcr.io","repository":"openj92"},"reference":{"descriptor_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","identity":{"contract_revision":1,"name":"hello-server","namespace":"openj92"}},"runtime_id":"docker","socket_environment":[{"edge_id":"upstream.internal->api.upstream","kind":"socket-derived","name":"UPSTREAM_URL","value":"http://upstream:8080"}]}'


def _language():
    try:
        module = importlib.import_module(MODULE)
    except ModuleNotFoundError as error:
        if error.name != MODULE:
            raise
        raise AssertionError("missing #1693 runtime effect intent language") from error
    required = {
        "RuntimeEffectIntent",
        "RuntimeEffectIntentSource",
        "runtime_effect_intent_fingerprint",
        "runtime_effect_intent_for_request",
        "runtime_effect_request_for_intent",
    }
    missing = sorted(required - set(vars(module)))
    if missing:
        raise AssertionError(f"missing #1693 intent capability: {', '.join(missing)}")
    return module


def _delivery() -> RuntimeAuthorityAccessDelivery:
    authority = RuntimeAuthorityReference("remote-docker")
    return RuntimeAuthorityAccessDelivery(
        authority_ref=authority,
        delivery_kind=RuntimeAuthorityAccessDeliveryKind.REMOTE_DOCKER_TLS_SECRET_FILES,
        secret_references=(
            RuntimeAuthorityDeliverySecretReference(
                "ca-cert", "secret://local/workspace-a/docker/ca-cert"
            ),
            RuntimeAuthorityDeliverySecretReference(
                "client-cert", "secret://local/workspace-a/docker/client-cert"
            ),
            RuntimeAuthorityDeliverySecretReference(
                "client-key", "secret://local/workspace-a/docker/client-key"
            ),
        ),
    )


def _product_material(
    *,
    node_id: str = "api",
    runtime_id: str = "docker",
    product_namespace: str = "openj92",
    product_name: str = "hello-server",
    contract_revision: int = 1,
    descriptor_digest: str = "b" * 64,
    image_registry: str = "ghcr.io",
    image_repository: str = "openj92/control-plane-kit-servers/hello-server",
    image_digest: str = "sha256:" + "a" * 64,
    image_tag: str | None = "stable",
    image_architecture: str = "amd64",
    image_provenance: str = "build-a",
    provider_socket_name: str = "http",
    provider_socket_protocol: Protocol = Protocol.HTTP,
    provider_port: int = 8000,
    secret_environment_name: str = "APP_CONTROL_TOKEN",
    secret_use_intent: SecretUseIntent = SecretUseIntent.APPLICATION_CONTROL_TOKEN,
    public_name: str = "HELLO_MESSAGE",
    public_value: str = "Hello from graph",
    socket_name: str = "UPSTREAM_URL",
    socket_value: str = "http://upstream:8080",
    socket_edge: str = "upstream.internal->api.upstream",
    delivery_reference: str = "secret://local/workspace-a/app/token",
    verification_check_id: str = "database-ready",
    verification_socket: str = "database",
    verification_database: str = "app",
    verification_username: str = "app",
    verification_reference: str = "secret://local/workspace-a/postgres/password",
    pull_registry: str = "ghcr.io",
    pull_repository: str | None = "openj92",
    pull_reference: str = "secret://local/workspace-a/oci/pull",
    runtime_sockets: BlockSockets | None = None,
) -> RuntimeProductMaterial:
    identity = ProductIdentity(product_namespace, product_name, contract_revision)
    product = ContainerServerProduct(
        identity=identity,
        image=OciImageReference(
            registry=image_registry,
            repository=image_repository,
            digest=image_digest,
            tag=image_tag,
            platforms=(OciPlatform("linux", image_architecture),),
            provenance={"build": image_provenance},
        ),
        runtime_contract=ProductRuntimeContract(
            sockets=runtime_sockets
            if runtime_sockets is not None
            else BlockSockets(
                providers=(
                    ProviderSocket(provider_socket_name, provider_socket_protocol),
                    ProviderSocket("database", Protocol.POSTGRES),
                    ProviderSocket("database-alt", Protocol.POSTGRES),
                )
            ),
            provider_ports=()
            if runtime_sockets is not None
            else (
                ProviderRuntimePort(provider_socket_name, provider_port),
                ProviderRuntimePort("database", 5432),
                ProviderRuntimePort("database-alt", 5433),
            ),
            secret_deliveries=(
                SecretEnvironmentDelivery(
                    secret_environment_name,
                    SecretReference(delivery_reference),
                    secret_use_intent,
                ),
            ),
            verification=VerificationContract()
            if runtime_sockets is not None
            else VerificationContract(
                (
                    PostgresQueryCheck(
                        check_id=verification_check_id,
                        provider_socket=verification_socket,
                        authentication=PostgresPasswordAuthentication(
                            database=verification_database,
                            username=verification_username,
                            password_reference=SecretReference(
                                verification_reference
                            ),
                        ),
                    ),
                )
            ),
        ),
    )
    return RuntimeProductMaterial(
        node_id=node_id,
        runtime_id=runtime_id,
        reference=ProductReference(identity, ProductDescriptorDigest(descriptor_digest)),
        product=product,
        public_environment=(
            PublicStaticEnvironmentBinding(public_name, public_value),
        ),
        socket_environment=(
            SocketDerivedEnvironmentBinding(socket_name, socket_value, socket_edge),
        ),
        pull_authority=ImagePullAuthority(
            registry=pull_registry,
            repository=pull_repository,
            credential_reference=CredentialReference(pull_reference),
        ),
    )


def _subclass_copy(value):
    hostile_type = type(f"Hostile{type(value).__name__}", (type(value),), {})
    arguments = {
        item.name: getattr(value, item.name)
        for item in fields(value)
        if item.init
    }
    return hostile_type(**arguments)


def _intent_product_sockets(
    *,
    reverse_requirements: bool,
    reverse_providers: bool,
) -> BlockSockets:
    requirements = (
        RequirementSocket("alpha-input", Protocol.HTTP, ("ALPHA_URL",)),
        RequirementSocket("zeta-input", Protocol.HTTP, ("ZETA_URL",)),
    )
    providers = (
        ProviderSocket("alpha-output", Protocol.HTTP),
        ProviderSocket("zeta-output", Protocol.HTTP),
    )
    return BlockSockets(
        requirements=tuple(reversed(requirements)) if reverse_requirements else requirements,
        providers=tuple(reversed(providers)) if reverse_providers else providers,
    )


def _request(
    *,
    effect_id: str = "event-started-a",
    intent_event_id: str | None = None,
    complete: bool = True,
) -> RuntimeEffectRequest:
    delivery = _delivery()
    return RuntimeEffectRequest(
        effect_id=effect_id,
        kind=RuntimeEffectKind.REALIZE_ACTIVITY,
        runtime_kind=RuntimeKind.DOCKER,
        source=RuntimeEffectSource(
            workspace_id="workspace-a",
            request_id="request-a",
            run_id=RunId("run-a"),
            plan_id="plan-a",
            base_graph_id="graph-base",
            desired_graph_id="graph-desired",
            intent_event_id=effect_id if intent_event_id is None else intent_event_id,
        ),
        activity_id=ActivityId("activity-a"),
        operation=StartNode(NodeTarget("api")),
        authority_ref=delivery.authority_ref if complete else None,
        authority_deliveries=(delivery,) if complete else (),
        products=(_product_material(),) if complete else (),
    )


class RuntimeEffectIntentTests(unittest.TestCase):
    def test_exact_projection_and_inverse_bind_generated_event_once(self) -> None:
        language = _language()
        request = _request()

        intent = language.runtime_effect_intent_for_request(request)
        descriptor = intent.descriptor()

        self.assertIs(type(intent), language.RuntimeEffectIntent)
        self.assertIs(type(intent.source), language.RuntimeEffectIntentSource)
        self.assertEqual(
            set(descriptor),
            {
                "kind",
                "runtime_kind",
                "authority_ref",
                "authority_deliveries",
                "source",
                "activity_id",
                "operation",
                "products",
            },
        )
        self.assertEqual(
            descriptor["source"],
            {
                "workspace_id": "workspace-a",
                "request_id": "request-a",
                "run_id": "run-a",
                "plan_id": "plan-a",
                "base_graph_id": "graph-base",
                "desired_graph_id": "graph-desired",
            },
        )
        self.assertEqual(
            descriptor["authority_deliveries"],
            [request.authority_deliveries[0].descriptor()],
        )
        self.assertEqual(descriptor["products"], [request.products[0].descriptor()])
        self.assertNotIn("effect_id", repr(descriptor))
        self.assertNotIn("intent_event_id", repr(descriptor))
        self.assertNotIn("secret_resolution_grants", repr(descriptor))

        reconstructed = language.runtime_effect_request_for_intent(
            intent,
            effect_id="event-started-b",
        )
        self.assertEqual(reconstructed.effect_id, "event-started-b")
        self.assertEqual(reconstructed.source.intent_event_id, "event-started-b")
        self.assertEqual(
            language.runtime_effect_intent_for_request(reconstructed),
            intent,
        )

    def test_generated_event_coordinates_do_not_create_pre_start_cycle(self) -> None:
        language = _language()
        first = _request(effect_id="event-started-a")
        second = _request(effect_id="event-started-b")

        first_intent = language.runtime_effect_intent_for_request(first)
        second_intent = language.runtime_effect_intent_for_request(second)

        self.assertEqual(first_intent, second_intent)
        self.assertEqual(
            language.runtime_effect_intent_fingerprint(first_intent),
            language.runtime_effect_intent_fingerprint(second_intent),
        )

    def test_product_socket_permutations_preserve_intent_and_request_inverses(
        self,
    ) -> None:
        language = _language()
        base_request = _request(complete=False)
        canonical = language.runtime_effect_intent_for_request(
            replace(
                base_request,
                products=(
                    _product_material(
                        runtime_sockets=_intent_product_sockets(
                            reverse_requirements=False,
                            reverse_providers=False,
                        )
                    ),
                ),
            )
        )
        canonical_bytes = rfc8785.dumps(canonical.descriptor())
        cases = (
            ("requirements", True, False),
            ("providers", False, True),
            ("both", True, True),
        )

        for name, reverse_requirements, reverse_providers in cases:
            with self.subTest(name=name):
                intent = language.runtime_effect_intent_for_request(
                    replace(
                        base_request,
                        products=(
                            _product_material(
                                runtime_sockets=_intent_product_sockets(
                                    reverse_requirements=reverse_requirements,
                                    reverse_providers=reverse_providers,
                                )
                            ),
                        ),
                    )
                )

                self.assertEqual(intent.descriptor(), canonical.descriptor())
                self.assertEqual(rfc8785.dumps(intent.descriptor()), canonical_bytes)
                self.assertEqual(
                    language.runtime_effect_intent_fingerprint(intent),
                    language.runtime_effect_intent_fingerprint(canonical),
                )
                self.assertEqual(intent, canonical)
                reconstructed = language.runtime_effect_request_for_intent(
                    intent,
                    effect_id="event-started-permutation",
                )
                self.assertEqual(
                    language.runtime_effect_intent_for_request(reconstructed),
                    intent,
                )

    def test_noncanonical_product_fixture_retains_accepted_literal_golden(self) -> None:
        language = _language()
        intent = language.runtime_effect_intent_for_request(_request())
        descriptor = intent.descriptor()
        product_descriptor = descriptor["products"][0]
        canonical = rfc8785.dumps(descriptor)
        product_canonical = rfc8785.dumps(product_descriptor)

        self.assertEqual(
            product_descriptor["product"]["runtime_contract"]["sockets"],
            {
                "requirements": {},
                "providers": {
                    "database": {
                        "protocol": {
                            "transport": "tcp",
                            "application": "postgres",
                        }
                    },
                    "database-alt": {
                        "protocol": {
                            "transport": "tcp",
                            "application": "postgres",
                        }
                    },
                    "http": {
                        "protocol": {
                            "transport": "tcp",
                            "application": "http",
                        }
                    },
                },
            },
        )
        self.assertEqual(product_canonical, PRODUCT_MATERIAL_CANONICAL)
        self.assertEqual(len(product_canonical), 2_226)
        self.assertEqual(
            hashlib.sha256(product_canonical).hexdigest(),
            "f7da31e6a9d0b4cd5838570253b5d691f1fd1965cc4e26aecf98fea5cd80db81",
        )
        self.assertEqual(len(canonical), 3_005)
        self.assertEqual(
            hashlib.sha256(canonical).hexdigest(),
            "f06647243f28bbe5e0200f9a85c27630fabbf2e42669f85e47cdb4390d3cd48d",
        )
        self.assertEqual(
            language.runtime_effect_intent_fingerprint(intent),
            "fa6e2d2c44210d027421b9f25f5cf0481a8345d9727fd5f0b27fd90aaa9ccda0",
        )
        self.assertEqual(
            hashlib.sha256(INTENT_DOMAIN + canonical).hexdigest(),
            "fa6e2d2c44210d027421b9f25f5cf0481a8345d9727fd5f0b27fd90aaa9ccda0",
        )
        self.assertEqual(
            intent.products[0].product.runtime_contract.sockets.provider_names(),
            ("database", "database-alt", "http"),
        )

    def test_intent_fingerprint_has_exact_rfc8785_golden_and_domain(self) -> None:
        language = _language()
        intent = language.runtime_effect_intent_for_request(_request(complete=False))
        canonical = rfc8785.dumps(intent.descriptor())

        self.assertEqual(
            canonical,
            b'{"activity_id":"activity-a","authority_deliveries":[],"authority_ref":null,"kind":"realize-activity","operation":{"kind":"start-node","target":{"kind":"node","node_id":"api"}},"products":[],"runtime_kind":"docker","source":{"base_graph_id":"graph-base","desired_graph_id":"graph-desired","plan_id":"plan-a","request_id":"request-a","run_id":"run-a","workspace_id":"workspace-a"}}',
        )
        self.assertEqual(
            language.runtime_effect_intent_fingerprint(intent),
            "01524d40ae572390e1ce15c7e201e2555ab5f0a175fec0243a759ba5f8709128",
        )
        self.assertEqual(
            hashlib.sha256(INTENT_DOMAIN + canonical).hexdigest(),
            "01524d40ae572390e1ce15c7e201e2555ab5f0a175fec0243a759ba5f8709128",
        )
        self.assertEqual(len(language.runtime_effect_intent_fingerprint(intent)), 64)

    def test_every_intent_coordinate_changes_the_fingerprint(self) -> None:
        language = _language()
        intent = language.runtime_effect_intent_for_request(_request())
        baseline = language.runtime_effect_intent_fingerprint(intent)
        other_delivery = RuntimeAuthorityAccessDelivery(
            RuntimeAuthorityReference("local-docker"),
            RuntimeAuthorityAccessDeliveryKind.LOCAL_DOCKER_SOCKET_MOUNT,
        )
        other_delivery_kind = RuntimeAuthorityAccessDelivery(
            intent.authority_ref,
            RuntimeAuthorityAccessDeliveryKind.LOCAL_DOCKER_SOCKET_MOUNT,
        )
        mutations = {
            "runtime_kind": replace(intent, runtime_kind=RuntimeKind.EXTERNAL),
            "workspace_id": replace(
                intent, source=replace(intent.source, workspace_id="workspace-b")
            ),
            "request_id": replace(
                intent, source=replace(intent.source, request_id="request-b")
            ),
            "run_id": replace(
                intent, source=replace(intent.source, run_id=RunId("run-b"))
            ),
            "plan_id": replace(
                intent, source=replace(intent.source, plan_id="plan-b")
            ),
            "base_graph_id": replace(
                intent, source=replace(intent.source, base_graph_id="graph-base-b")
            ),
            "desired_graph_id": replace(
                intent,
                source=replace(intent.source, desired_graph_id="graph-desired-b"),
            ),
            "activity_id": replace(intent, activity_id=ActivityId("activity-b")),
            "operation": replace(intent, operation=StopNode(NodeTarget("api"))),
            "authority_ref": replace(
                intent,
                authority_ref=other_delivery.authority_ref,
                authority_deliveries=(other_delivery,),
            ),
            "authority_deliveries": replace(intent, authority_deliveries=()),
            "products": replace(intent, products=(_product_material(node_id="api-b"),)),
            "authority_delivery_kind": replace(
                intent,
                authority_deliveries=(other_delivery_kind,),
            ),
            "authority_delivery_secret_label": replace(
                intent,
                authority_deliveries=(
                    RuntimeAuthorityAccessDelivery(
                        intent.authority_ref,
                        RuntimeAuthorityAccessDeliveryKind.REMOTE_DOCKER_TLS_SECRET_FILES,
                        (
                            RuntimeAuthorityDeliverySecretReference(
                                "ca-chain",
                                "secret://local/workspace-a/docker/ca-cert",
                            ),
                            *intent.authority_deliveries[0].secret_references[1:],
                        ),
                    ),
                ),
            ),
            "authority_delivery_secret_reference": replace(
                intent,
                authority_deliveries=(
                    RuntimeAuthorityAccessDelivery(
                        intent.authority_ref,
                        RuntimeAuthorityAccessDeliveryKind.REMOTE_DOCKER_TLS_SECRET_FILES,
                        (
                            RuntimeAuthorityDeliverySecretReference(
                                "ca-cert",
                                "secret://local/workspace-a/docker/ca-cert-b",
                            ),
                            *intent.authority_deliveries[0].secret_references[1:],
                        ),
                    ),
                ),
            ),
            "product_runtime_id": replace(
                intent, products=(_product_material(runtime_id="docker-b"),)
            ),
            "product_reference_identity": replace(
                intent, products=(_product_material(product_name="hello-worker"),)
            ),
            "product_reference_namespace": replace(
                intent,
                products=(_product_material(product_namespace="example"),),
            ),
            "product_reference_revision": replace(
                intent,
                products=(_product_material(contract_revision=2),),
            ),
            "product_reference_digest": replace(
                intent, products=(_product_material(descriptor_digest="c" * 64),)
            ),
            "product_image_registry": replace(
                intent, products=(_product_material(image_registry="registry.example"),)
            ),
            "product_image_repository": replace(
                intent,
                products=(_product_material(image_repository="openj92/hello-worker"),),
            ),
            "product_image_digest": replace(
                intent,
                products=(_product_material(image_digest="sha256:" + "c" * 64),),
            ),
            "product_image_tag": replace(
                intent, products=(_product_material(image_tag="candidate"),)
            ),
            "product_image_platform": replace(
                intent,
                products=(_product_material(image_architecture="arm64"),),
            ),
            "product_image_provenance": replace(
                intent,
                products=(_product_material(image_provenance="build-b"),),
            ),
            "product_runtime_contract": replace(
                intent, products=(_product_material(provider_port=8001),)
            ),
            "product_provider_socket_identity": replace(
                intent,
                products=(_product_material(provider_socket_name="admin"),),
            ),
            "product_provider_socket_protocol": replace(
                intent,
                products=(_product_material(provider_socket_protocol=Protocol.TCP),),
            ),
            "product_secret_delivery": replace(
                intent,
                products=(
                    _product_material(
                        delivery_reference="secret://local/workspace-a/app/token-b"
                    ),
                ),
            ),
            "product_secret_environment_name": replace(
                intent,
                products=(_product_material(secret_environment_name="CONTROL_TOKEN"),),
            ),
            "product_secret_use_intent": replace(
                intent,
                products=(
                    _product_material(
                        secret_use_intent=SecretUseIntent.WORKLOAD_NODE_CONTROL_SIGNING_KEY
                    ),
                ),
            ),
            "product_verification_material": replace(
                intent,
                products=(
                    _product_material(
                        verification_reference=(
                            "secret://local/workspace-a/postgres/password-b"
                        )
                    ),
                ),
            ),
            "product_verification_check_id": replace(
                intent,
                products=(_product_material(verification_check_id="database-live"),),
            ),
            "product_verification_socket": replace(
                intent,
                products=(_product_material(verification_socket="database-alt"),),
            ),
            "product_verification_database": replace(
                intent,
                products=(_product_material(verification_database="control"),),
            ),
            "product_verification_username": replace(
                intent,
                products=(_product_material(verification_username="control"),),
            ),
            "product_public_environment_name": replace(
                intent, products=(_product_material(public_name="GREETING"),)
            ),
            "product_public_environment_value": replace(
                intent, products=(_product_material(public_value="Hello again"),)
            ),
            "product_socket_environment_name": replace(
                intent, products=(_product_material(socket_name="BACKEND_URL"),)
            ),
            "product_socket_environment_value": replace(
                intent,
                products=(_product_material(socket_value="http://upstream:8081"),),
            ),
            "product_socket_environment_edge": replace(
                intent,
                products=(_product_material(socket_edge="upstream.other->api.upstream"),),
            ),
            "product_pull_registry": replace(
                intent, products=(_product_material(pull_registry="registry.example"),)
            ),
            "product_pull_repository": replace(
                intent, products=(_product_material(pull_repository="openj92/private"),)
            ),
            "product_pull_credential": replace(
                intent,
                products=(
                    _product_material(
                        pull_reference="secret://local/workspace-a/oci/pull-b"
                    ),
                ),
            ),
        }

        for name, candidate in mutations.items():
            with self.subTest(name=name):
                self.assertNotEqual(
                    language.runtime_effect_intent_fingerprint(candidate),
                    baseline,
                )
        self.assertEqual({value.value for value in RuntimeEffectKind}, {"realize-activity"})

    def test_projection_and_fingerprint_require_exact_nominal_values(self) -> None:
        language = _language()

        class HostileRequest(RuntimeEffectRequest):
            pass

        class HostileSource(RuntimeEffectSource):
            pass

        class HostileText(str):
            pass

        request = _request()
        hostile_request = _subclass_copy(request)
        hostile_source = _subclass_copy(request.source)

        with self.assertRaises(RuntimeEffectContractError):
            language.runtime_effect_intent_for_request(hostile_request)
        with self.assertRaises(RuntimeEffectContractError):
            language.runtime_effect_intent_for_request(
                replace(request, source=hostile_source)
            )
        for request_id in (HostileText("request-a"), "request\x00a", "request-\ud800"):
            with self.subTest(request_id_type=type(request_id).__name__):
                with self.assertRaises(RuntimeEffectContractError):
                    language.runtime_effect_intent_for_request(
                        replace(
                            request,
                            source=replace(request.source, request_id=request_id),
                        )
                    )

        intent = language.runtime_effect_intent_for_request(request)

        class HostileIntent(language.RuntimeEffectIntent):
            pass

        with self.assertRaises(RuntimeEffectContractError):
            language.runtime_effect_intent_fingerprint(
                _subclass_copy(intent)
            )
        with self.assertRaises(RuntimeEffectContractError):
            language.runtime_effect_intent_fingerprint(
                replace(intent, source=_subclass_copy(intent.source))
            )

    def test_public_intent_dataclass_fields_are_exact_and_frozen(self) -> None:
        language = _language()

        self.assertEqual(
            tuple((item.name, item.init) for item in fields(language.RuntimeEffectIntentSource)),
            (
                ("workspace_id", True),
                ("request_id", True),
                ("run_id", True),
                ("plan_id", True),
                ("base_graph_id", True),
                ("desired_graph_id", True),
            ),
        )
        self.assertEqual(
            tuple((item.name, item.init) for item in fields(language.RuntimeEffectIntent)),
            (
                ("kind", True),
                ("runtime_kind", True),
                ("source", True),
                ("activity_id", True),
                ("operation", True),
                ("authority_ref", True),
                ("authority_deliveries", True),
                ("products", True),
            ),
        )
        self.assertTrue(language.RuntimeEffectIntentSource.__dataclass_params__.frozen)
        self.assertTrue(language.RuntimeEffectIntent.__dataclass_params__.frozen)

    def test_operation_validation_translates_only_categorical_input_errors(self) -> None:
        language = _language()
        intent = language.runtime_effect_intent_for_request(_request())

        with self.assertRaises(RuntimeEffectContractError) as caught:
            replace(intent, operation=object())
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

        original = language.activity_operation_descriptor
        for injected in (
            RuntimeError("internal operation descriptor canary"),
            TypeError("internal operation descriptor type canary"),
        ):
            with self.subTest(error_type=type(injected).__name__):
                def fail_if_called(_operation, error=injected):
                    raise error

                language.activity_operation_descriptor = fail_if_called
                try:
                    with self.assertRaises(type(injected)) as raised:
                        replace(intent, operation=intent.operation)
                    self.assertIs(raised.exception, injected)
                finally:
                    language.activity_operation_descriptor = original

    def test_canonical_intent_has_a_one_mebibyte_ceiling(self) -> None:
        language = _language()
        request = _request(complete=False)
        products = tuple(
            _product_material(node_id=f"node-{index:04d}-" + "x" * 430)
            for index in range(2_048)
        )
        intent = language.runtime_effect_intent_for_request(
            replace(request, products=products)
        )
        self.assertGreater(len(rfc8785.dumps(intent.descriptor())), 1_048_576)

        with self.assertRaisesRegex(RuntimeEffectContractError, "too large"):
            language.runtime_effect_intent_fingerprint(intent)


if __name__ == "__main__":
    unittest.main()
