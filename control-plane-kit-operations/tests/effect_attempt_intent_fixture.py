from __future__ import annotations

from dataclasses import fields, replace
import importlib

import rfc8785

from control_plane_kit_core.algebra import BlockSockets, ProviderSocket
from control_plane_kit_core.environment import (
    PublicStaticEnvironmentBinding,
    SocketDerivedEnvironmentBinding,
)
from control_plane_kit_core.operations import (
    ActivityEventKind,
    EffectAttemptIdentity,
    RunId,
)
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
)
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectIntent,
    RuntimeEffectIntentSource,
    runtime_effect_intent_fingerprint,
    runtime_effect_intent_for_request,
    runtime_effect_request_for_intent,
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
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    BoundedEvidence,
    OperationsRecordError,
)


INTENT_MODULE = "control_plane_kit_operations.effect_attempt_intent_evidence"
INTENT_SOURCE_PATH = (
    "control-plane-kit-operations/src/control_plane_kit_operations/"
    "effect_attempt_intent_evidence.py"
)
INTENT_MAX_BYTES = 1_048_576
INTENT_ERROR = "effect attempt intent evidence is invalid"
EVENT_ID = "effect-start-event-a"


def _load_optional(module_name: str, import_module=importlib.import_module):
    try:
        return import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name != module_name:
            raise
        return None


intent_module = _load_optional(INTENT_MODULE)
EffectAttemptIntentRecord = getattr(
    intent_module,
    "EffectAttemptIntentRecord",
    None,
)
_encode_runtime_effect_intent = getattr(
    intent_module,
    "_encode_runtime_effect_intent",
    None,
)
_decode_runtime_effect_intent = getattr(
    intent_module,
    "_decode_runtime_effect_intent",
    None,
)


def forge_exact(cls, **values):
    forged = object.__new__(cls)
    for name, value in values.items():
        object.__setattr__(forged, name, value)
    return forged


def subclass_copy(value):
    hostile_type = type(f"Hostile{type(value).__name__}", (type(value),), {})
    arguments = {
        item.name: getattr(value, item.name)
        for item in fields(value)
        if item.init
    }
    return hostile_type(**arguments)


def class_access_hostile_copy(value, dispatches: list[str]):
    value_type = type(value)

    class HostileValue(value_type):
        def __getattribute__(self, name):
            if name == "__class__":
                dispatches.append("class")
                raise AssertionError("hostile class access dispatched")
            return super().__getattribute__(name)

    hostile = object.__new__(HostileValue)
    for item in fields(value):
        object.__setattr__(hostile, item.name, getattr(value, item.name))
    return hostile


class ClassAccessHostileBytes(bytes):
    dispatches: list[str] = []

    def __getattribute__(self, name):
        if name == "__class__":
            type(self).dispatches.append("class")
            raise AssertionError("hostile class access dispatched")
        return super().__getattribute__(name)

    def __len__(self):
        type(self).dispatches.append("len")
        raise AssertionError("hostile length dispatched")

    def decode(self, *_args, **_kwargs):
        type(self).dispatches.append("decode")
        raise AssertionError("hostile decode dispatched")


def authority_delivery() -> RuntimeAuthorityAccessDelivery:
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


def product_material(
    *,
    node_id: str = "api",
    public_value: str = "Hello from graph",
    socket_value: str = "http://upstream.internal:8080",
) -> RuntimeProductMaterial:
    identity = ProductIdentity("openj92", "hello-server", 1)
    product = ContainerServerProduct(
        identity=identity,
        image=OciImageReference(
            registry="ghcr.io",
            repository="openj92/control-plane-kit-servers/hello-server",
            digest="sha256:" + "a" * 64,
            tag="stable",
            platforms=(OciPlatform("linux", "amd64"),),
            provenance={"build": "build-a"},
        ),
        runtime_contract=ProductRuntimeContract(
            sockets=BlockSockets(
                providers=(
                    ProviderSocket("http", Protocol.HTTP),
                    ProviderSocket("database", Protocol.POSTGRES),
                )
            ),
            provider_ports=(
                ProviderRuntimePort("http", 8000),
                ProviderRuntimePort("database", 5432),
            ),
            secret_deliveries=(
                SecretEnvironmentDelivery(
                    "APP_CONTROL_TOKEN",
                    SecretReference("secret://local/workspace-a/app/token"),
                    SecretUseIntent.APPLICATION_CONTROL_TOKEN,
                ),
            ),
            verification=VerificationContract(
                (
                    PostgresQueryCheck(
                        check_id="database-ready",
                        provider_socket="database",
                        authentication=PostgresPasswordAuthentication(
                            database="app",
                            username="app",
                            password_reference=SecretReference(
                                "secret://local/workspace-a/postgres/password"
                            ),
                        ),
                    ),
                )
            ),
        ),
    )
    return RuntimeProductMaterial(
        node_id=node_id,
        runtime_id="docker",
        reference=ProductReference(identity, ProductDescriptorDigest("b" * 64)),
        product=product,
        public_environment=(
            PublicStaticEnvironmentBinding("HELLO_MESSAGE", public_value),
        ),
        socket_environment=(
            SocketDerivedEnvironmentBinding(
                "UPSTREAM_URL",
                socket_value,
                "upstream.internal->api.upstream",
            ),
        ),
        pull_authority=ImagePullAuthority(
            registry="ghcr.io",
            repository="openj92",
            credential_reference=CredentialReference(
                "secret://local/workspace-a/oci/pull"
            ),
        ),
    )


class EffectAttemptIntentFixture:
    maxDiff = None

    def require_intent_language(self) -> None:
        required = {
            "EffectAttemptIntentRecord": EffectAttemptIntentRecord,
            "_encode_runtime_effect_intent": _encode_runtime_effect_intent,
            "_decode_runtime_effect_intent": _decode_runtime_effect_intent,
        }
        self.assertEqual(
            [name for name, value in required.items() if value is None],
            [],
            "effect-attempt intent evidence language is missing",
        )

    def assert_intent_error(self, construct, *canaries: object) -> None:
        with self.assertRaises(OperationsRecordError) as caught:
            construct()
        self.assertEqual(str(caught.exception), INTENT_ERROR)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        rendered = f"{caught.exception!s} {caught.exception!r}"
        self.assertLessEqual(len(rendered), 256)
        for canary in canaries:
            self.assertNotIn(str(canary), rendered)

    def identity(
        self,
        *,
        run_id: str = "run-a",
        activity_id: str = "start-runtime",
    ) -> EffectAttemptIdentity:
        return EffectAttemptIdentity(RunId(run_id), activity_id, 1)

    def intent(
        self,
        *,
        compensation: bool = False,
        request_id: str = "request-a",
        run_id: str = "run-a",
        activity_id: str = "start-runtime",
        products: tuple[RuntimeProductMaterial, ...] | None = None,
    ) -> RuntimeEffectIntent:
        delivery = authority_delivery()
        request = RuntimeEffectRequest(
            effect_id=EVENT_ID,
            kind=RuntimeEffectKind.REALIZE_ACTIVITY,
            runtime_kind=RuntimeKind.DOCKER,
            source=RuntimeEffectSource(
                workspace_id="workspace-a",
                request_id=request_id,
                run_id=RunId(run_id),
                plan_id="plan-a",
                base_graph_id="graph-base",
                desired_graph_id="graph-desired",
                intent_event_id=EVENT_ID,
            ),
            activity_id=ActivityId(activity_id),
            operation=(
                StopNode(NodeTarget("api"))
                if compensation
                else StartNode(NodeTarget("api"))
            ),
            authority_ref=delivery.authority_ref,
            authority_deliveries=(delivery,),
            products=(product_material(),) if products is None else products,
        )
        return runtime_effect_intent_for_request(request)

    def original_event(
        self,
        intent: RuntimeEffectIntent | None = None,
        *,
        compensation: bool = False,
        event_id: str = EVENT_ID,
    ) -> ActivityEventRecord:
        value = intent or self.intent(compensation=compensation)
        return ActivityEventRecord(
            event_id,
            value.source.run_id.value,
            3,
            (
                ActivityEventKind.STEP_COMPENSATION_STARTED
                if compensation
                else ActivityEventKind.STEP_STARTED
            ),
            "2030-01-01T00:00:00Z",
            activity_id=value.activity_id.value,
            evidence=BoundedEvidence.from_mapping(
                {"effect_attempt": {"attempt": 1, "state_fingerprint": "a" * 64}}
            ),
        )

    def record(
        self,
        *,
        compensation: bool = False,
        identity: EffectAttemptIdentity | None = None,
        original_start_event: ActivityEventRecord | None = None,
        intent: RuntimeEffectIntent | None = None,
    ):
        self.require_intent_language()
        value = intent or self.intent(compensation=compensation)
        return EffectAttemptIntentRecord(
            identity or self.identity(),
            original_start_event
            or self.original_event(value, compensation=compensation),
            value,
        )

    def canonical_bytes(self, intent: RuntimeEffectIntent | None = None) -> bytes:
        return rfc8785.dumps((intent or self.intent()).descriptor())

    def largest_lawful_intent(self) -> RuntimeEffectIntent:
        products = tuple(
            product_material(node_id=f"node-{index:04d}-" + "x" * 430)
            for index in range(2_048)
        )
        lower = 1
        upper = len(products)
        accepted = self.intent(products=(products[0],))
        while lower <= upper:
            middle = (lower + upper) // 2
            candidate = self.intent(products=products[:middle])
            if len(rfc8785.dumps(candidate.descriptor())) <= INTENT_MAX_BYTES:
                accepted = candidate
                lower = middle + 1
            else:
                upper = middle - 1
        return accepted

    def public_round_trip(
        self,
        intent: RuntimeEffectIntent | None = None,
        *,
        effect_id: str = EVENT_ID,
    ) -> RuntimeEffectIntent:
        value = intent or self.intent()
        request = runtime_effect_request_for_intent(
            value,
            effect_id=effect_id,
            secret_resolution_grants=(),
        )
        self.assertEqual(request.secret_resolution_grants, ())
        self.assertEqual(request.effect_id, effect_id)
        self.assertEqual(request.source.intent_event_id, effect_id)
        return runtime_effect_intent_for_request(request)


__all__ = [
    "EVENT_ID",
    "EffectAttemptIntentFixture",
    "EffectAttemptIntentRecord",
    "INTENT_ERROR",
    "INTENT_MAX_BYTES",
    "INTENT_MODULE",
    "INTENT_SOURCE_PATH",
    "RuntimeEffectIntent",
    "RuntimeEffectIntentSource",
    "ClassAccessHostileBytes",
    "_decode_runtime_effect_intent",
    "_encode_runtime_effect_intent",
    "_load_optional",
    "authority_delivery",
    "class_access_hostile_copy",
    "forge_exact",
    "intent_module",
    "product_material",
    "replace",
    "runtime_effect_intent_fingerprint",
    "subclass_copy",
]
