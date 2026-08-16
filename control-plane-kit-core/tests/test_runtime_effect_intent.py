from __future__ import annotations

from dataclasses import replace
import hashlib
import importlib
import unittest

import rfc8785

from control_plane_kit_core.algebra import BlockSockets, ProviderSocket
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.operations.run_identity import RunId
from control_plane_kit_core.planning import ActivityId, NodeTarget, StartNode, StopNode
from control_plane_kit_core.products import (
    ContainerServerProduct,
    OciImageReference,
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
    RuntimeEffectKind,
    RuntimeEffectRequest,
    RuntimeEffectSource,
    RuntimeProductMaterial,
)
from control_plane_kit_core.types import Protocol, RuntimeKind


MODULE = "control_plane_kit_core.runtime_effect_observation"
INTENT_DOMAIN = b"control-plane-kit.runtime-effect-intent.v1\x00"


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


def _product_material(*, node_id: str = "api") -> RuntimeProductMaterial:
    identity = ProductIdentity("openj92", "hello-server", 1)
    product = ContainerServerProduct(
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
    return RuntimeProductMaterial(
        node_id=node_id,
        runtime_id="docker",
        reference=ProductReference(identity, ProductDescriptorDigest("b" * 64)),
        product=product,
        public_environment=(
            PublicStaticEnvironmentBinding("HELLO_MESSAGE", "Hello from graph"),
        ),
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
        hostile_request = HostileRequest(**request.__dict__)
        hostile_source = HostileSource(**request.source.__dict__)

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
                HostileIntent(**intent.__dict__)
            )

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
