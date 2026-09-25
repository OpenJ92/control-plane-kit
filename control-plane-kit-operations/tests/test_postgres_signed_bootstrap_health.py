"""Original signed bootstrap operations through real start/store/reload owners.

Predecessor journal facts are fixture setup for these focused owner laws only.
They do not prove creation execution, native connection or public reachability.
The separate managed application-chain tests own that composition evidence.
"""
from dataclasses import replace
import unittest
from unittest import mock

from control_plane_kit_core.node_control import NodeHealthReadKind, workload_node_control_audience
from control_plane_kit_core.node_control_surface_reads import (
    WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationProfile,
)
from control_plane_kit_core.planning import ManagementBootstrapStage, ObserveManagementBootstrap
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, validate_graph
from control_plane_kit_operations.effect_attempt_start import (
    EffectAttemptStartDenied, EffectAttemptStartError, ExistingAttempt, NewlyStarted,
)
from control_plane_kit_operations.effect_attempt_start_interpreter import EffectAttemptStartService
from control_plane_kit_operations.health_effect_preparations import (
    HealthEffectPreparationError, health_effect_attempt_wire_id,
)
from control_plane_kit_operations.health_receiver_trust import HealthReceiverDecoders
from control_plane_kit_operations.health_signing_authority import (
    HealthSigningAuthorityReloadService, HealthSigningAuthorityUnavailable,
    ReloadHealthSigningAuthority,
)
from control_plane_kit_operations.records import OperationsRecordError
from control_plane_kit_operations.runtime_management_targets import (
    ManagementHealthTargetProjectionError, project_management_health_target,
)
from control_plane_kit_operations.secret_providers import authorized_secret_use_for
from control_plane_kit_operations.workflows import InvalidOperationCommand
from control_plane_kit_operations import health_receiver_trust
from tests.execution_lease_recovery_fixture import Sequence
from tests.health_effect_start_fixture import trusted_health_context
from tests.health_receiver_trust_fixture import ByteDecoder, bindings
from tests.health_signing_authority_fixture import timestamp
from tests.postgres_health_effect_start_fixture import PostgresHealthEffectStartFixture


STAGES = (
    ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH,
    ManagementBootstrapStage.GATEWAY_INGRESS_READY,
)


class PostgresSignedBootstrapHealthTests(PostgresHealthEffectStartFixture, unittest.TestCase):
    def registry(self):
        decoder = ByteDecoder(health_receiver_trust)
        return HealthReceiverDecoders(bindings(health_receiver_trust,
            self.receiver_documents, decoder)), decoder

    def bootstrap_command(self, **options):
        try:
            return self.start_health_command(**options)
        except InvalidOperationCommand:
            self.fail("original signed bootstrap operation must enter trusted health admission")

    def start_bootstrap(self, registry, *, ids=None):
        ids = Sequence("health-original", "health-request", "health-transit-jti", "health-workload-jti") if ids is None else ids
        service = EffectAttemptStartService(self.unit_of_work, id_factory=ids,
            health_receiver_decoders=registry)
        command = self.bootstrap_command()
        try:
            return service.execute_health(command)
        except (EffectAttemptStartError, HealthEffectPreparationError, OperationsRecordError):
            self.fail("valid original bootstrap stage must retain its own health preparation")

    def reload_command(self, preparation, **changes):
        values = dict(request_id="request-a", identity=preparation.identity,
            context=trusted_health_context(), authority=self.start_value.authority,
            fence=self.start_value.fence)
        return ReloadHealthSigningAuthority(**(values | changes))

    def reload_service(self, registry):
        return HealthSigningAuthorityReloadService(self.unit_of_work,
            health_receiver_decoders=registry)

    def assert_reloads(self, preparation, registry):
        try:
            pair = self.reload_service(registry).execute(self.reload_command(preparation))
        except HealthSigningAuthorityUnavailable:
            self.fail("current signed bootstrap authority must reload its original gateway target")
        self.assertEqual(pair.preparation, preparation)
        for family in ("transit", "workload"):
            self.assertEqual(getattr(pair, family).public_key, self.keys[family].public_key)
        return pair

    def test_original_stages_commit_both_gateway_receivers_and_reload_after_store_restart(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                self.reset_health(stage=stage)
                self.assertEqual(self.health_counts(), (0, 0, 0, 0))
                self.assertIs(type(self.health_activity.operation), ObserveManagementBootstrap)
                self.assertIs(self.health_activity.operation.stage, stage)
                self.assertEqual(self.receiver_products["transit"], self.receiver_products["workload"])
                registry, decoder = self.registry()
                with self.observed_time("2030-01-01T00:00:00Z"):
                    result = self.start_bootstrap(registry)
                self.assertIs(type(result.start), NewlyStarted)
                self.assertEqual(self.health_counts(), (1, 1, 2, 1))
                preparation, event = result.preparation, result.start.attempt.original_start_event
                graph = DEFAULT_GRAPH_CODEC.decode(self.projections["health-desired"].graph_descriptor)
                declaration = WorkloadNodeControlSurfaceDeclaration(
                    graph.node("gateway").block_spec.control_surfaces[0],
                    WorkloadNodeControlSurfaceDeclarationProfile.V2)
                target = preparation.request.target
                self.assertEqual((target.node_id.value, target.provider_socket_name.value,
                    target.graph_revision.value), ("gateway", "http", "health-desired"))
                self.assertIs(preparation.request.kind, NodeHealthReadKind.READINESS)
                self.assertEqual(preparation.request.declaration_identity, declaration.identity())
                self.assertEqual(preparation.transit_grant.gateway_node_id.value, "gateway")
                self.assertEqual(preparation.transit_grant.target, target)
                self.assertEqual(preparation.workload_grant.target, target)
                self.assertEqual(preparation.workload_grant.audience, workload_node_control_audience(target))
                self.assertEqual(preparation.original_event_id, event.event_id)
                self.assertEqual(preparation.transit_grant.attempt_id,
                    health_effect_attempt_wire_id(self.start_value.transition.identity))
                self.assertEqual(len(decoder.calls), 2)
                self.assertEqual({call.artifact.artifact_id for call in decoder.calls},
                    {"test-transit", "test-workload"})
                for call in decoder.calls:
                    self.assertEqual(call.receiver_node_id, "gateway")
                    self.assertEqual(call.product_reference, self.receiver_products["transit"].reference)
                    declared = next(value for value in call.descriptor_document.product.runtime_contract.configuration_artifacts
                        if value.artifact_id == call.artifact.artifact_id)
                    self.assertNotEqual(call.artifact.content_digest, declared.content_digest)
                # Fresh UoW/connection reconstructs every retained owner value.
                with self.unit_of_work() as uow:
                    intent = uow.stores.effect_attempt_intents.get(preparation.identity)
                    self.assertEqual(intent.intent, self.start_value.intent)
                    self.assertEqual(intent.intent.operation, self.health_activity.operation)
                    self.assertEqual(intent.original_start_event, event)
                    self.assertEqual(preparation.request_fingerprint,
                        self.start_value.transition.request_fingerprint)
                    self.assertEqual(uow.stores.effect_attempts.get(preparation.identity), result.start.attempt)
                    self.assertEqual(uow.stores.health_effect_preparations.get(preparation.identity), preparation)
                    for family in ("transit", "workload"):
                        use = uow.stores.secret_use_authorizations.get("workspace-a",
                            getattr(preparation, family + "_authorization_id"))
                        expected = authorized_secret_use_for(
                            self.expected_use_command(family, requested_at=event.occurred_at),
                            reference=self.references[family], provider=self.provider)
                        self.assertEqual(use, expected)
                        self.assertEqual(use.actor_subject, "health-operator")
                self.assertNotEqual(preparation.transit_authorization_id, preparation.workload_authorization_id)
                before = self.health_snapshot()
                with self.observed_time(timestamp(preparation.transit_grant.not_before)):
                    first = self.assert_reloads(preparation, registry)
                    self.assertEqual(self.assert_reloads(preparation, registry), first)
                self.assertEqual(self.health_snapshot(), before)

    def test_replay_preserves_original_stage_without_receiver_decode_or_current_key_authority(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                self.reset_health(stage=stage)
                registry, _ = self.registry()
                with self.observed_time("2030-01-01T00:00:00Z"):
                    result = self.start_bootstrap(registry)
                self.expire_claim()
                self.connection.execute("UPDATE cpk_delegation_signing_keys SET status='revoked', "
                    "revoked_by='operator-a', revoked_at='2030-01-01T00:10:00Z'")
                before = self.health_snapshot()
                ids = Sequence("replay-must-not-allocate")
                with self.forbid_fresh_health(), mock.patch.object(ByteDecoder, "decode",
                        side_effect=AssertionError("historical replay decoded receiver bytes")):
                    replay = self.start_bootstrap(registry, ids=ids)
                    self.assertEqual(replay.start, ExistingAttempt(result.start.attempt))
                    self.assertEqual(replay.preparation, result.preparation)
                    service = EffectAttemptStartService(self.unit_of_work, id_factory=ids,
                        health_receiver_decoders=registry)
                    with self.assertRaises(EffectAttemptStartError):
                        service.execute_health(self.bootstrap_command(
                            context=trusted_health_context(actor="other-actor")))
                self.assertEqual(ids.calls, [])
                self.assertEqual(self.health_snapshot(), before)

    def test_generic_start_cannot_bypass_signed_bootstrap_admission(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                self.reset_health(stage=stage)
                registry, decoder = self.registry()
                ids = Sequence("generic-start-must-not-allocate")
                service = EffectAttemptStartService(self.unit_of_work, id_factory=ids,
                    health_receiver_decoders=registry)
                before = self.health_snapshot()
                with self.forbid_fresh_health():
                    with self.assertRaises(EffectAttemptStartDenied):
                        service.execute(self.start_value)
                self.assertEqual(ids.calls, [])
                self.assertEqual(decoder.calls, [])
                self.assertEqual(self.health_counts(), (0, 0, 0, 0))
                self.assertEqual(self.health_snapshot(), before)

    def test_gateway_receiver_mismatch_refuses_before_any_start_use_or_preparation(self):
        for stage in STAGES:
            self.reset_health(stage=stage)
            graph = DEFAULT_GRAPH_CODEC.decode(self.projections["health-desired"].graph_descriptor)
            api_declaration = WorkloadNodeControlSurfaceDeclaration(
                graph.node("api").block_spec.control_surfaces[0],
                WorkloadNodeControlSurfaceDeclarationProfile.V2)
            cases = (
                ({"workload": {"node": "api"}}, {}),
                ({"workload": {"declaration": api_declaration.descriptor()}}, {}),
                ({"workload": {"purpose": self.keys["transit"].purpose.value}}, {}),
                ({}, {"transit": {"artifact_id": "missing-transit-slot"}}),
                ({}, {"workload": {"artifact_id": "missing-health-slot"}}),
            )
            for changes, slots in cases:
                with self.subTest(stage=stage, changes=changes, slots=slots):
                    self.reset_health(stage=stage, changes=changes, slot_changes=slots)
                    registry, _ = self.registry()
                    ids = Sequence("refusal-must-not-allocate")
                    service = EffectAttemptStartService(self.unit_of_work, id_factory=ids,
                        health_receiver_decoders=registry)
                    command = self.bootstrap_command()
                    with self.observed_time("2030-01-01T00:00:00Z"):
                        before = self.health_snapshot()
                        with self.assertRaises(EffectAttemptStartDenied):
                            service.execute_health(command)
                    self.assertEqual(ids.calls, [])
                    self.assertEqual(self.health_counts(), (0, 0, 0, 0))
                    self.assertEqual(self.health_snapshot(), before)

    def test_reload_requires_both_purpose_bindings_and_current_actor_workspace_fence(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                self.reset_health(stage=stage)
                registry, decoder = self.registry()
                with self.observed_time("2030-01-01T00:00:00Z"):
                    preparation = self.start_bootstrap(registry).preparation
                before = self.health_snapshot()
                admitted = bindings(health_receiver_trust, self.receiver_documents, decoder)
                with self.observed_time(timestamp(preparation.transit_grant.not_before)):
                    self.assert_reloads(preparation, registry)
                    for partial in ((admitted[0],), (admitted[1],)):
                        with self.assertRaises(HealthSigningAuthorityUnavailable):
                            self.reload_service(HealthReceiverDecoders(partial)).execute(
                                self.reload_command(preparation))
                    for changes in (
                        dict(context=trusted_health_context(actor="other-actor")),
                        dict(context=trusted_health_context(workspace="workspace-other")),
                        dict(context=trusted_health_context(scopes=())),
                        dict(fence=replace(self.start_value.fence, generation=8)),
                    ):
                        with self.subTest(changes=tuple(changes)), self.assertRaises(HealthSigningAuthorityUnavailable):
                            self.reload_service(registry).execute(self.reload_command(preparation, **changes))
                self.assertEqual(self.health_snapshot(), before)

    def test_fractional_original_window_survives_lease_renewal_and_key_revocation_denies_reload(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                self.reset_health(stage=stage)
                registry, _ = self.registry()
                with self.observed_time("2030-01-01T00:00:00.250000Z"):
                    preparation = self.start_bootstrap(registry).preparation
                grant = preparation.transit_grant
                self.assertEqual(timestamp(grant.not_before), "2030-01-01T00:00:01Z")
                self.assertEqual((grant.issued_at, grant.not_before, grant.expires_at),
                    (preparation.workload_grant.issued_at, preparation.workload_grant.not_before,
                     preparation.workload_grant.expires_at))
                edges = ((grant.not_before - 1, 999999, False), (grant.not_before, 0, True),
                    (grant.expires_at - 1, 999999, True), (grant.expires_at, 0, False))
                for seconds, micros, accepted in edges:
                    with self.subTest(seconds=seconds, micros=micros), self.observed_time(
                            timestamp(seconds, micros), expires_at="2030-01-01T01:00:00Z"):
                        # The fixture deliberately renews the persisted lease;
                        # only the reload, not that setup, must be read-only.
                        before = self.health_snapshot()
                        if accepted:
                            self.assert_reloads(preparation, registry)
                        else:
                            with self.assertRaises(HealthSigningAuthorityUnavailable):
                                self.reload_service(registry).execute(self.reload_command(preparation))
                    self.assertEqual(self.health_snapshot(), before)
                    with self.unit_of_work() as uow:
                        self.assertEqual(uow.stores.health_effect_preparations.get(preparation.identity), preparation)
                self.connection.execute("UPDATE cpk_delegation_signing_keys SET status='revoked', "
                    "revoked_by='operator-a', revoked_at='2030-01-01T00:00:02Z' WHERE registration_id=%s",
                    (self.keys["workload"].registration_id,))
                with self.observed_time(timestamp(grant.not_before)):
                    before = self.health_snapshot()
                    with self.assertRaises(HealthSigningAuthorityUnavailable):
                        self.reload_service(registry).execute(self.reload_command(preparation))
                self.assertEqual(self.health_snapshot(), before)
                with self.unit_of_work() as uow:
                    self.assertEqual(uow.stores.health_effect_preparations.get(preparation.identity), preparation)

    def test_native_connector_has_no_signed_target_command_and_missing_attempt_cannot_reload(self):
        self.reset_health(stage=STAGES[0])
        native = next(item for item in self.health_plan.activities
            if type(item.operation) is ObserveManagementBootstrap
            and item.operation.stage is ManagementBootstrapStage.CONNECTOR_CONNECTED)
        current = validate_graph(DEFAULT_GRAPH_CODEC.decode(self.projections["health-base"].graph_descriptor))
        desired = validate_graph(DEFAULT_GRAPH_CODEC.decode(self.projections["health-desired"].graph_descriptor))
        before = self.health_snapshot()
        with self.assertRaises(ManagementHealthTargetProjectionError):
            project_management_health_target(self.health_plan, native.activity_id, native.operation, current, desired)
        native_start = self.health_start_value(activity=native)
        with self.assertRaises(InvalidOperationCommand):
            self.start_health_command(start=native_start)
        self.assertEqual(self.health_snapshot(), before)
        registry, decoder = self.registry()
        command = ReloadHealthSigningAuthority(request_id="request-a", identity=native_start.transition.identity,
            context=trusted_health_context(), authority=native_start.authority, fence=native_start.fence)
        with self.observed_time("2030-01-01T00:00:00Z"):
            # Time setup changes the lease, independently of signing refusal.
            reload_before = self.health_snapshot()
            # This identity has never started: preserve the actual missing-owner
            # exception. This is not evidence about an existing native attempt.
            with self.assertRaises(KeyError) as caught:
                self.reload_service(registry).execute(command)
            self.assertEqual(caught.exception.args, ("effect attempt was not found",))
        self.assertEqual(decoder.calls, [])
        self.assertEqual(self.health_counts(), (0, 0, 0, 0))
        self.assertEqual(self.health_snapshot(), reload_before)
