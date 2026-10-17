"""O2 R2/N2-N4/N6-N7 over real owners; downstream branches require R2 green."""

from dataclasses import replace
from unittest import mock

from control_plane_kit_core.planning import ManagementBootstrapStage
from control_plane_kit_core.receiver_identity import NodeControlAuthorityContext
from control_plane_kit_core.receiver_health_reads import ReceiverHealthReadRequest
from control_plane_kit_operations.effect_attempt_fold import ExistingFold
from control_plane_kit_operations.effect_attempt_start import EffectAttemptStartError
from control_plane_kit_operations.delegation_signing_keys import DelegationSigningKeyNotFound
from control_plane_kit_operations.health_effect_preparations import HealthEffectPreparationCodec
from control_plane_kit_operations.health_receiver_trust import HealthReceiverDecoders
from control_plane_kit_operations.health_signing_authority import HealthSigningAuthorityUnavailable
from control_plane_kit_operations.postgres import PostgresExecutionStore
from control_plane_kit_operations.postgres.runtime_authority_store import RuntimeAuthorityStore
from tests.execution_lease_recovery_fixture import Sequence
from tests.health_effect_start_fixture import HEALTH_SCOPES, trusted_health_context
from tests.receiver_health_execution_fixture import ReceiverHealthExecutionFixture


class ReceiverHealthExecutionTests(ReceiverHealthExecutionFixture):
    def assert_selected_preparation(self, preparation, node="api"):
        self.assertIs(type(preparation.request), ReceiverHealthReadRequest)
        self.assertEqual(preparation.request.target, self.targets[node])
        self.assertEqual(preparation.transit_grant.gateway_target, self.targets["gateway"])
        self.assertEqual(preparation.request.authority_context, NodeControlAuthorityContext(*self.selected_context))
        for grant in (preparation.transit_grant, preparation.workload_grant):
            self.assertEqual(grant.target, preparation.request.target)
            self.assertEqual(grant.authority_context, preparation.request.authority_context)
            self.assertEqual(grant.request_digest, preparation.request.canonical_digest())
            self.assertEqual(grant.request_id, preparation.request.request_id)

    async def test_first_health_start_uses_selected_stable_targets_and_authority_context(self):
        await self.prepare_health()
        # R2: all graph/product/introduction/request owners are lawful above.
        # This first call in the body is the missing live V2 boundary. A setup
        # failure or an earlier owner refusal cannot earn target-red credit.
        started = self.start_service().execute_health(self.start_command)
        self.assert_selected_preparation(started.preparation)
        self.assertEqual(self.targets["gateway"].provider_socket_name.value, "control")
        self.assertEqual(self.graph.node("gateway").block_spec.gateway_transit.provider_socket_name, "http")
        self.assertEqual(len(self.rows("cpk_health_effect_preparations")), 1)
        self.assertEqual(len(self.rows("cpk_secret_use_authorizations")), 2)
        # Restart readers and signer are new service/UoW instances over the
        # same original record. No new IDs or interval renewal are possible.
        with self.unit_of_work() as uow:
            restored = uow.stores.health_effect_preparations.get(started.preparation.identity)
        self.assertEqual(restored, started.preparation)
        before = self.durable_snapshot()
        signed = self.reload_service().execute(self.reload_command())
        self.assertEqual(signed.preparation, restored)
        self.assertEqual(self.durable_snapshot(), before)
        self.assertEqual(signed.transit.public_key.key_id, "health-transit")
        self.assertEqual(signed.workload.public_key.key_id, "health-workload")

    async def test_pending_gateway_bootstrap_uses_its_one_own_receiver(self):
        await self.prepare_health(stage=ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH)
        started = self.start_service().execute_health(self.start_command)
        self.assert_selected_preparation(started.preparation, "gateway")
        self.assertEqual(started.preparation.transit_grant.gateway_target, started.preparation.request.target)
        self.assertEqual(self.reload_service().execute(self.reload_command()).preparation, started.preparation)

    async def test_gateway_ingress_readiness_uses_the_same_selected_receiver(self):
        await self.prepare_health(stage=ManagementBootstrapStage.GATEWAY_INGRESS_READY)
        started = self.start_service().execute_health(self.start_command)
        self.assert_selected_preparation(started.preparation, "gateway")
        self.assertEqual(started.preparation.transit_grant.gateway_target, started.preparation.request.target)
        self.assertEqual(self.reload_service().execute(self.reload_command()).preparation, started.preparation)

    async def test_current_actor_and_fence_refusals_do_not_change_original_history(self):
        await self.prepare_health()
        started = self.start_service().execute_health(self.start_command)
        command = self.reload_command()
        self.assertEqual(self.reload_service().execute(command).preparation, started.preparation)
        before = self.durable_snapshot()
        candidates = [replace(command, context=trusted_health_context(actor="different-operator")),
            replace(command, fence=replace(command.fence, generation=command.fence.generation + 1))]
        candidates.extend(replace(command, context=trusted_health_context(
            scopes=tuple(scope for scope in HEALTH_SCOPES if scope is not omitted))) for omitted in HEALTH_SCOPES)
        for candidate in candidates:
            with self.subTest(command=candidate):
                with self.assertRaises(HealthSigningAuthorityUnavailable):
                    self.reload_service().execute(candidate)
                self.assertEqual(self.durable_snapshot(), before)

    async def test_changed_graph_pairs_and_revision_refuse_before_any_new_health_identity(self):
        await self.prepare_health()
        columns = ("current_graph_id", "current_realized_projection_id", "desired_graph_id",
            "desired_realized_projection_id", "desired_graph_revision")
        original = self.connection.execute("SELECT " + ",".join(columns)
            + " FROM cpk_workspaces WHERE workspace_id='workspace-a'").fetchone()
        # Source/projection pairs have immediate composite foreign keys. Keep
        # each pair structurally valid; never disable schema checks to pretend
        # that independently mismatched scalar pins can be retained normally.
        replacements = ((*original[2:4], *original[2:]),
            (*original[:2], *original[:2], original[4]),
            (*original[:4], original[4] + 1))
        update = "UPDATE cpk_workspaces SET " + ",".join(field + "=%s" for field in columns) \
            + " WHERE workspace_id='workspace-a'"
        before = self.durable_snapshot()
        for changed in replacements:
            ids = mock.Mock(side_effect=AssertionError("stale pins allocated health identity"))
            try:
                # Independent, bounded corruption witnesses, not claims that
                # these pointer changes are permitted during an active run.
                self.connection.execute(update, changed)
                with self.subTest(pins=changed), self.assertRaises(EffectAttemptStartError):
                    self.start_service(ids=ids).execute_health(self.start_command)
                self.assertEqual(ids.call_count, 0)
                self.assertEqual(self.durable_snapshot(), before)
            finally:
                self.connection.execute(update, original)
        # Restored positive control must reach the same actual first-start
        # boundary. Until R2 is green, these negatives earn no branch credit.
        preparation = self.start_service().execute_health(self.start_command).preparation
        self.assert_selected_preparation(preparation)

    async def test_wrong_registered_transit_slot_refuses_before_health_identity_allocation(self):
        await self.prepare_health()
        original = self.registry
        # Wrong registry slot remains a valid registration value. The owner
        # must match the actual selected product/slot, never fill from defaults.
        binding, = original.bindings
        self.registry = HealthReceiverDecoders((replace(binding, artifact_id="unselected-transit"),))
        before = self.durable_snapshot()
        ids = mock.Mock(side_effect=AssertionError("wrong slot allocated health identity"))
        try:
            with self.assertRaises(EffectAttemptStartError):
                self.start_service(ids=ids).execute_health(self.start_command)
            self.assertEqual(ids.call_count, 0)
            self.assertEqual(self.durable_snapshot(), before)
        finally:
            self.registry = original
        self.assert_selected_preparation(self.start_service().execute_health(self.start_command).preparation)

    async def test_gateway_own_origin_cannot_borrow_an_unrelated_action(self):
        await self.prepare_health()
        gateway_id = self.targets["gateway"].receiver_id
        original = self.connection.execute("SELECT introducing_action_id,introducing_session_id "
            "FROM cpk_graph_receiver_introductions WHERE workspace_id='workspace-a' AND receiver_id=%s",
            (gateway_id,)).fetchone()
        foreign = self.connection.execute("SELECT action_id FROM cpk_operation_actions "
            "WHERE session_id=%s AND action_id<>%s ORDER BY ordinal LIMIT 1",
            (original[1], original[0])).fetchone()
        self.assertIsNotNone(foreign)
        ids = mock.Mock(side_effect=AssertionError("foreign gateway origin allocated identity"))
        try:
            # Deliberately corrupt only the gateway's action association while
            # retaining a valid FK to an actual action in the same session.
            self.connection.execute("UPDATE cpk_graph_receiver_introductions SET introducing_action_id=%s "
                "WHERE workspace_id='workspace-a' AND receiver_id=%s", (foreign[0], gateway_id))
            before = self.durable_snapshot(), self.receiver_snapshot()
            with self.assertRaises(EffectAttemptStartError):
                self.start_service(ids=ids).execute_health(self.start_command)
            self.assertEqual(ids.call_count, 0)
            self.assertEqual((self.durable_snapshot(), self.receiver_snapshot()), before)
        finally:
            self.connection.execute("UPDATE cpk_graph_receiver_introductions SET introducing_action_id=%s "
                "WHERE workspace_id='workspace-a' AND receiver_id=%s", (original[0], gateway_id))
        self.assert_selected_preparation(self.start_service().execute_health(self.start_command).preparation)

    async def test_original_start_replay_survives_revocation_without_current_decoding(self):
        await self.prepare_health()
        started = self.start_service().execute_health(self.start_command)
        codec = HealthEffectPreparationCodec()
        original_bytes = codec.encode_canonical_bytes(started.preparation)
        self.assertEqual(self.reload_service().execute(self.reload_command()).preparation, started.preparation)
        self.revoke_workload_key()
        before = self.durable_snapshot()
        ids = mock.Mock(side_effect=AssertionError("replay allocated fresh identity"))
        service = self.start_service(ids=ids)
        # No registered transit decoder is available after restart. Pure
        # decoding of ORIGINAL Core V2 configuration remains permitted.
        service = type(service)(self.unit_of_work, id_factory=ids,
            health_receiver_decoders=HealthReceiverDecoders(()))
        with self.forbid_current_health():
            replay = service.execute_health(self.start_command)
        self.assertEqual(ids.call_count, 0)
        self.assertEqual(replay.preparation, started.preparation)
        self.assertEqual(codec.encode_canonical_bytes(replay.preparation), original_bytes)
        self.assertEqual(self.durable_snapshot(), before)
        with self.assertRaises(DelegationSigningKeyNotFound):
            self.reload_service().execute(self.reload_command())
        self.assertEqual(self.durable_snapshot(), before)

    async def test_terminal_fold_replay_is_observational_after_key_revocation(self):
        await self.prepare_health()
        started = self.start_service().execute_health(self.start_command)
        command = self.health_fold(started.preparation)
        folded = self.fold(command)
        self.revoke_workload_key()
        before = self.durable_snapshot()
        ids = mock.Mock(side_effect=AssertionError("terminal replay allocated event"))
        with self.forbid_current_health(), \
                mock.patch.object(PostgresExecutionStore, "get_latest_run_for_request") as latest, \
                mock.patch.object(PostgresExecutionStore, "get_latest_run_for_request_for_update") as latest_locked, \
                mock.patch.object(RuntimeAuthorityStore, "get_active_for_update") as runtime:
            replay = self.fold(command, ids=ids)
        self.assertEqual([ids.call_count, latest.call_count, latest_locked.call_count, runtime.call_count], [0] * 4)
        self.assertEqual(replay, ExistingFold(folded.attempt, folded.outcome_record))
        self.assertEqual(self.durable_snapshot(), before)

    async def test_original_preparation_is_immutable_after_current_signing_refuses(self):
        await self.prepare_health()
        started = self.start_service().execute_health(self.start_command)
        self.assertEqual(self.reload_service().execute(self.reload_command()).preparation, started.preparation)
        self.revoke_workload_key()
        before = self.durable_snapshot()
        with self.assertRaises(DelegationSigningKeyNotFound):
            self.reload_service().execute(self.reload_command())
        with self.unit_of_work() as uow:
            saved = uow.stores.health_effect_preparations.get(started.preparation.identity)
        self.assertEqual(saved, started.preparation)
        self.assertEqual(self.durable_snapshot(), before)

    async def retained_world(self, world):
        self.selected_world = world
        await self.prepare_health()
        started = self.start_service().execute_health(self.start_command)
        self.assert_selected_preparation(started.preparation)
        self.assertEqual(self.reload_service().execute(self.reload_command()).preparation, started.preparation)
        self.assertEqual(len(self.rows("cpk_effect_attempts")), 1)
        self.assertEqual(len(set(self.world_contexts)), ord(world) - ord("A") + 1)
        with self.unit_of_work() as uow:
            restored = uow.stores.health_effect_preparations.get(started.preparation.identity)
            for context in self.world_contexts:
                bound = uow.stores.graphs.receiver_bindings("workspace-a", *context)
                self.assertEqual({item.receiver_id for item in bound}, {"a" * 32, "b" * 32})
            for target in self.targets.values():
                origin = uow.stores.graphs.receiver_introduction("workspace-a", target.receiver_id)
                self.assertEqual(origin.introducing_graph_id, self.world_contexts[0][0])
                self.assertIsNone(origin.first_accepted_action_id)
        self.assertEqual(restored, started.preparation)
        # Pure request comparison isolates the authority-context contribution
        # to the digest. Only the selected world's request was authorized/run.
        requests = [replace(restored.request, authority_context=NodeControlAuthorityContext(*context))
            for context in self.world_contexts]
        self.assertEqual(len({value.canonical_digest().value for value in requests}), len(requests))
        self.assertEqual(restored.request, requests[-1])

    async def test_isolated_world_a_retains_selected_receiver_identity(self):
        await self.retained_world("A")

    async def test_isolated_world_b_retains_identity_after_unrelated_graph_change(self):
        await self.retained_world("B")

    async def test_isolated_world_c_retains_identity_after_declaration_and_trust_change(self):
        await self.retained_world("C")
