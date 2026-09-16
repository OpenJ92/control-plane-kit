"""#1857 real admission/reload owners consume actual selected fixture bytes."""
from dataclasses import replace
import unittest
from unittest import mock

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.node_control import NodeHealthReadKind
from control_plane_kit_core.node_control_surface_reads import WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationProfile
from control_plane_kit_core.planning import PlanGraphSide
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_operations.effect_attempt_start import EffectAttemptStartDenied, ExistingAttempt
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.health_signing_authority import HealthSigningAuthorityReloadService, HealthSigningAuthorityUnavailable, ReloadHealthSigningAuthority
from control_plane_kit_operations.postgres.product_store import RegisteredProductStore
from control_plane_kit_operations.products import ProductRegistrationError
from tests.execution_lease_recovery_fixture import Sequence
from tests.health_effect_start_fixture import trusted_health_context
from tests.health_receiver_trust_fixture import api, bindings, ByteDecoder, context, NODES, PEMS, PURPOSES, PUBLIC_KEY_C, register_products
from tests.postgres_health_effect_start_fixture import PostgresHealthEffectStartFixture


class PostgresHealthReceiverTrustTests(PostgresHealthEffectStartFixture, unittest.TestCase):
    def setUp(self):
        self.receiver_changes = {}
        self.receiver_slots = {}
        super().setUp()
        # Real approved/ready graph and active registrations precede the guard.
        self.assertEqual(self.health_counts(), (0, 0, 0, 0))
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.activity_history.get_plan("plan-a").plan, self.health_plan)
            for family, key in self.keys.items():
                self.assertEqual(uow.stores.delegation_signing_keys.require_unambiguous_active(
                    "workspace-a", PURPOSES[family]), key)
                self.assertEqual(uow.stores.registered_products.get("workspace-a",
                    self.receiver_products[family].reference), self.receiver_products[family])
        self.contract = api(self)

    def health_context(self, side=PlanGraphSide.DESIRED_GRAPH):
        world, documents, selected = context(self, side=side,
            changes=self.receiver_changes, slot_changes=self.receiver_slots)
        self.receiver_documents, self.receiver_artifacts = documents, selected
        self.receiver_products = register_products(self, documents)
        return world

    def reset_receiver(self, *, changes=None, slots=None, side=PlanGraphSide.DESIRED_GRAPH):
        self.receiver_changes, self.receiver_slots = changes or {}, slots or {}
        self.reset_health(side=side)

    def registry(self, decoder=None):
        decoder = ByteDecoder(self.contract) if decoder is None else decoder
        return self.contract.HealthReceiverDecoders(bindings(self.contract, self.receiver_documents, decoder)), decoder

    def start(self, registry, *, ids=None):
        ids = Sequence("health-original", "health-request", "health-transit-jti", "health-workload-jti") if ids is None else ids
        service = EffectAttemptStartService(self.unit_of_work, id_factory=ids, health_receiver_decoders=registry)
        return service.execute_health(self.start_health_command()), ids

    def reload(self, preparation, registry):
        command = ReloadHealthSigningAuthority(request_id="request-a", identity=preparation.identity,
            context=trusted_health_context(), authority=self.start_value.authority, fence=self.start_value.fence)
        return HealthSigningAuthorityReloadService(self.unit_of_work,
            health_receiver_decoders=registry).execute(command)

    def refused_start(self, registry):
        ids = Sequence("must-not-allocate")
        with self.observed_time("2030-01-01T00:00:00Z"):
            before = self.health_snapshot()
            with self.assertRaises(EffectAttemptStartDenied) as caught:
                self.start(registry, ids=ids)
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.health_snapshot(), before)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn("BEGIN PUBLIC KEY", repr(caught.exception))
        self.assertNotIn("other-issuer", str(caught.exception))

    def test_selected_bytes_not_descriptor_defaults_feed_both_receivers_before_preparation(self):
        registry, decoder = self.registry()
        with self.observed_time("2030-01-01T00:00:00Z"):
            result, _ = self.start(registry)
        self.assertEqual(self.health_counts(), (1, 1, 2, 1))
        self.assertEqual(len(decoder.calls), 2)
        for family in PURPOSES:
            selected = next(call for call in decoder.calls if call.receiver_node_id == NODES[family])
            self.assertEqual(selected.artifact, self.receiver_artifacts[family])
            self.assertEqual(selected.product_reference, self.receiver_products[family].reference)
            self.assertEqual(selected.descriptor_document, self.receiver_documents[family])
            self.assertEqual(selected.realized_projection_id, result.preparation.desired_realized_projection_id)
            self.assertEqual(selected.authored_graph_id, "health-desired")
            self.assertIs(selected.graph_side, PlanGraphSide.DESIRED_GRAPH)
            self.assertNotEqual(selected.artifact.content_digest,
                selected.descriptor_document.product.runtime_contract.configuration_artifacts[0].content_digest)

    def test_fresh_valid_artifact_with_different_key_refuses_but_overlap_accepts(self):
        for family in PURPOSES:
            original = self.receiver_artifacts[family]
            replacement = dict(key_id="health-" + family, pem=PUBLIC_KEY_C)
            self.reset_receiver(changes={family: dict(keys=[replacement])})
            self.assertNotEqual(self.receiver_artifacts[family].content_digest, original.content_digest)
            registry, decoder = self.registry()
            self.refused_start(registry)
            self.assertTrue(any(call.artifact == self.receiver_artifacts[family] for call in decoder.calls))
            self.reset_receiver(changes={family: dict(keys=[
                dict(key_id="health-" + family, pem=PEMS[family]),
                dict(key_id="overlap", pem=PUBLIC_KEY_C)])})
            registry, _ = self.registry()
            with self.observed_time("2030-01-01T00:00:00Z"):
                result, _ = self.start(registry)
            self.assertEqual(getattr(result.preparation, family + "_key_registration_id"), self.keys[family].registration_id)
            self.reset_receiver()

    def test_configured_identity_issuer_and_purpose_cannot_be_filled_from_expected_context(self):
        cases = (("transit", dict(issuer="other-issuer")),
            ("transit", dict(node="other-gateway")), ("transit", dict(workspace="foreign-workspace")),
            ("transit", dict(runtime="other-runtime")),
            ("workload", dict(revision="health-base")), ("workload", dict(socket="other-control")),
            ("workload", dict(keys=[dict(key_id="other-key-id", pem=PEMS["workload"])])),
            ("workload", dict(purpose=DelegationKeyPurpose.WORKLOAD_NODE_CONTROL_SURFACE_READ.value)))
        for family, change in cases:
            with self.subTest(family=family, field=next(iter(change))):
                self.reset_receiver(changes={family: change})
                registry, _ = self.registry()
                self.refused_start(registry)
        graph = DEFAULT_GRAPH_CODEC.decode(self.projections["health-desired"].graph_descriptor)
        declaration = WorkloadNodeControlSurfaceDeclaration(replace(
            graph.node("api").block_spec.control_surfaces[0], health_reads=(NodeHealthReadKind.READINESS,)),
            WorkloadNodeControlSurfaceDeclarationProfile.V2)
        self.reset_receiver(changes={"workload": dict(declaration=declaration.descriptor())})
        registry, _ = self.registry()
        self.refused_start(registry)

    def test_missing_or_wrong_binding_and_reassigned_slot_refuse_without_default_fallback(self):
        empty = self.contract.HealthReceiverDecoders(())
        self.refused_start(empty)
        self.reset_receiver(changes={"transit": dict(profile="unsupported-test-profile")})
        registry, _ = self.registry()
        self.refused_start(registry)
        self.reset_receiver()
        decoder = ByteDecoder(self.contract)
        admitted = bindings(self.contract, self.receiver_documents, decoder)
        wrong = replace(admitted[0], product_reference=admitted[1].product_reference)
        self.refused_start(self.contract.HealthReceiverDecoders((wrong, admitted[1])))
        for change in (dict(artifact_id="other-slot"), dict(target_path="/etc/test/other.json")):
            self.reset_receiver(slots={"transit": change})
            registry, _ = self.registry()
            self.refused_start(registry)

    def test_reload_rechecks_original_base_pins_and_bytes_without_new_history_or_interval(self):
        self.reset_receiver(side=PlanGraphSide.BASE_GRAPH)
        registry, decoder = self.registry()
        with self.observed_time("2030-01-01T00:00:00Z"):
            result, _ = self.start(registry)
            original = result.preparation
            before = self.health_snapshot()
            decoder.calls.clear()
            first = self.reload(original, registry)
            second = self.reload(original, registry)
        self.assertEqual(first, second)
        self.assertEqual(first.preparation, original)
        self.assertEqual(self.health_snapshot(), before)
        self.assertEqual(len(decoder.calls), 4)
        for selected in decoder.calls:
            self.assertIs(selected.graph_side, PlanGraphSide.BASE_GRAPH)
            self.assertEqual(selected.realized_projection_id, original.base_realized_projection_id)
            self.assertEqual(selected.authored_graph_id, "health-base")
            family = next(name for name in PURPOSES if NODES[name] == selected.receiver_node_id)
            self.assertEqual(selected.artifact, self.receiver_artifacts[family])

    def test_reload_missing_support_refuses_while_both_replay_entrances_stay_observational(self):
        registry, _ = self.registry()
        with self.observed_time("2030-01-01T00:00:00Z"):
            result, _ = self.start(registry)
            before = self.health_snapshot()
            empty = self.contract.HealthReceiverDecoders(())
            with self.assertRaises(HealthSigningAuthorityUnavailable):
                self.reload(result.preparation, empty)
        decoder = ByteDecoder(self.contract)
        registry, _ = self.registry(decoder)
        with mock.patch.object(decoder, "decode", side_effect=AssertionError("replay decoded receiver trust")):
            ids = Sequence("must-not-allocate")
            service = EffectAttemptStartService(self.unit_of_work, id_factory=ids, health_receiver_decoders=registry)
            with self.forbid_fresh_health():
                dedicated = service.execute_health(self.start_health_command())
                generic = service.execute(self.start_value)
        self.assertIs(type(dedicated.start), ExistingAttempt)
        self.assertEqual(dedicated.preparation, result.preparation)
        self.assertEqual(generic, ExistingAttempt(result.start.attempt))
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.health_snapshot(), before)

    def test_contract_refusal_is_bounded_but_unexpected_decoder_and_owner_errors_keep_identity(self):
        registry, decoder = self.registry()
        failure = self.contract.HealthReceiverTrustError("health receiver trust is unavailable")
        with mock.patch.object(decoder, "decode", side_effect=failure):
            self.refused_start(registry)
        for owner, method in ((decoder, "decode"), (RegisteredProductStore, "get")):
            failure = ProductRegistrationError("unexpected-owner-canary")
            before, ids = self.health_snapshot(), Sequence("must-not-allocate")
            with mock.patch.object(owner, method, side_effect=failure):
                with self.assertRaises(ProductRegistrationError) as caught:
                    self.start(registry, ids=ids)
            self.assertIs(caught.exception, failure)
            self.assertEqual(ids.calls, [])
            self.assertEqual(self.health_snapshot(), before)

    def test_reload_rejects_wrong_decoded_key_and_preserves_owner_failure_identity(self):
        registry, decoder = self.registry()
        with self.observed_time("2030-01-01T00:00:00Z"):
            result, _ = self.start(registry)
            before = self.health_snapshot()
            decode = decoder.decode
            def wrong_key(selected):
                value = decode(selected)
                return replace(value, public_keys=(replace(value.public_keys[0], public_key_pem=PUBLIC_KEY_C),))
            with mock.patch.object(decoder, "decode", side_effect=wrong_key):
                with self.assertRaises(HealthSigningAuthorityUnavailable):
                    self.reload(result.preparation, registry)
            failure = ProductRegistrationError("registered-product-read-failure")
            with mock.patch.object(RegisteredProductStore, "get", side_effect=failure):
                with self.assertRaises(ProductRegistrationError) as caught:
                    self.reload(result.preparation, registry)
            self.assertIs(caught.exception, failure)
        self.assertEqual(self.health_snapshot(), before)
