"""N8 actual Operations coordinator, with V2 values at a recording port only."""

from dataclasses import replace
from unittest import mock

from control_plane_kit_core.node_health_reads import NodeHealthReadRequest
from control_plane_kit_core.node_health_read_results import NodeHealthReadOutcome, NodeHealthReadResult
from control_plane_kit_core.node_control import NodeControlTarget
from control_plane_kit_core.operations import EffectAttemptIdentity
from control_plane_kit_core.planning import ActivityId, ObserveManagementBootstrap, ManagementBootstrapStage
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_core.receiver_identity import NodeControlAuthorityContext
from control_plane_kit_operations.effect_outcome_evidence import NativeConnectionOutcome
from control_plane_kit_operations.delegation_signing_keys import DelegationSigningKeyNotFound
from control_plane_kit_operations.coordinator import ExecutionCoordinator, ExecutionCoordinatorDenied
from control_plane_kit_operations.effect_attempt_fold import EffectAttemptFoldDenied
from control_plane_kit_operations.runtime_authorities import RemoteDockerTlsAuthority
from control_plane_kit_operations.runtime_management_targets import is_signed_management_health_operation
from tests.health_receiver_trust_fixture import reference
from tests.receiver_health_execution_fixture import ReceiverHealthExecutionFixture
from tests.test_managed_application_chain import now


class ReceiverHealthCoordinatorTests(ReceiverHealthExecutionFixture):
    async def drive_until_signed(self):
        for _ in range(len(self.plan.activities) + 2):
            result = await self.execute_one()
            if self.health.signed_reads:
                return result
            self.assertEqual(result["coordinator_status"], "progressed")
        self.fail("coordinator never reached its signed-health port")

    def signed_attempt(self):
        request, _, _ = self.health.signed_reads[-1]
        identity = EffectAttemptIdentity(request.source.run_id, request.activity_id.value, 1)
        with self.unit_of_work() as uow:
            return uow.stores.effect_attempts.get(identity)

    async def test_actual_coordinator_accepts_v2_path_readiness_and_workload_results(self):
        self.health.responses = [NativeConnectionOutcome.CONNECTED]
        await self.prepare_and_start()
        for _ in range(len(self.plan.activities) + 2):
            result = await self.execute_one()
            if result["run_status"] == "succeeded":
                break
            self.assertEqual(result["coordinator_status"], "progressed")
        self.assertEqual(result["run_status"], "succeeded")
        self.assertEqual(len(self.health.signed_reads), 3)
        self.assertEqual({prepared.request.target.receiver_id for _, prepared, _ in self.health.signed_reads},
            {"a" * 32, "b" * 32})
        for request, preparation, result in self.health.signed_reads:
            self.assertEqual(result.request, preparation.request)
            with self.unit_of_work() as uow:
                attempt = uow.stores.effect_attempts.get(preparation.identity)
                self.assertEqual(attempt.state.status.value, "succeeded")
                self.assertEqual(attempt.original_start_event.event_id, request.effect_id)
        self.assertEqual(self.ingress.create_active_counts, [0])
        self.assertEqual(self.tracker.active, 0)

    async def wrong_result(self, kind):
        self.health.responses = [NativeConnectionOutcome.CONNECTED]
        await self.prepare_and_start()
        original = self.health.observe_signed
        returned = []
        async def changed(realization, request, authority):
            value = await original(realization, request, authority)
            if kind == "context":
                candidate = replace(value, request=replace(value.request,
                    authority_context=NodeControlAuthorityContext("foreign-graph", "foreign-projection")))
            elif kind == "request":
                candidate = replace(value, request=replace(value.request, request_id="foreign-request"))
            else:
                target = value.request.target
                legacy = NodeHealthReadRequest(NodeControlTarget(target.workspace_id,
                    reference("graph-revision", value.request.authority_context.authored_graph_id),
                    target.node_id, target.provider_socket_name), target.runtime_id,
                    value.request.kind, value.request.declaration_identity, value.request.request_id)
                candidate = NodeHealthReadResult(legacy, value.declaration, value.outcome)
            returned.append(candidate)
            return candidate
        with mock.patch.object(self.health, "observe_signed", side_effect=changed) as port:
            await self.drive_until_signed()
        self.assertEqual(port.call_count, 1)
        self.assertEqual(len(returned), 1, "the intended wrong result must actually cross the port")
        candidate, = returned
        if kind == "context":
            self.assertEqual(candidate.request.authority_context,
                NodeControlAuthorityContext("foreign-graph", "foreign-projection"))
        elif kind == "request":
            self.assertEqual(candidate.request.request_id, "foreign-request")
        else:
            self.assertIs(type(candidate), NodeHealthReadResult)
        self.assertEqual(self.signed_attempt().state.status.value, "uncertain")

    async def test_foreign_v2_authority_context_cannot_complete_the_attempt(self):
        await self.wrong_result("context")

    async def test_foreign_v2_request_cannot_complete_the_attempt(self):
        await self.wrong_result("request")

    async def test_old_v1_result_cannot_complete_a_v2_attempt(self):
        await self.wrong_result("profile")

    async def test_real_key_revocation_after_v2_response_prevents_a_success_fold(self):
        self.health.responses = [NativeConnectionOutcome.CONNECTED]
        await self.prepare_and_start()
        original = self.health.observe_signed
        async def revoked(realization, request, authority):
            result = await original(realization, request, authority)
            self.revoke_workload_key()
            return result
        with mock.patch.object(self.health, "observe_signed", side_effect=revoked) as port:
            with self.assertRaises(DelegationSigningKeyNotFound):
                await self.drive_until_signed()
        self.assertEqual(port.call_count, 1)
        attempt = self.signed_attempt()
        self.assertEqual(attempt.state.status.value, "started")
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.effect_attempts.get(attempt.state.identity), attempt)

    def replace_runtime(self):
        reference = self.graph.runtimes["docker"].authority_ref
        with self.unit_of_work() as uow:
            uow.stores.runtime_authorities.revoke("workspace-a", reference)
            replacement = uow.stores.runtime_authorities.register(workspace_id="workspace-a", authority_ref=reference,
                runtime_kind=RuntimeKind.DOCKER, authority=RemoteDockerTlsAuthority(
                    endpoint="tcp://replacement.example.test:2376",
                    ca_certificate=SecretReference("secret://replacement/ca"),
                    client_certificate=SecretReference("secret://replacement/cert"),
                    client_key=SecretReference("secret://replacement/key")),
                admitted_by="operator-a", admitted_at=now())
            uow.commit()
        return replacement

    async def test_signed_dispatch_refuses_runtime_replacement_before_the_port(self):
        self.health.responses = [NativeConnectionOutcome.CONNECTED]
        await self.prepare_and_start()
        original = ExecutionCoordinator._managed_dispatch_authority
        replaced = []
        def changed(owner, command, attempt, runtime):
            operation = self.plan.activity(ActivityId(attempt.state.identity.activity_id)).operation
            if is_signed_management_health_operation(operation):
                replaced.append(self.replace_runtime())
            return original(owner, command, attempt, runtime)
        with mock.patch.object(ExecutionCoordinator, "_managed_dispatch_authority", changed):
            with self.assertRaises(ExecutionCoordinatorDenied):
                await self.drive_until_signed()
        self.assertEqual(len(replaced), 1)
        self.assertEqual(self.health.signed_reads, [])

    async def test_signed_fold_refuses_runtime_replacement_after_a_valid_v2_response(self):
        self.health.responses = [NativeConnectionOutcome.CONNECTED]
        await self.prepare_and_start()
        original = self.health.observe_signed
        replaced = []
        async def changed(realization, request, authority):
            result = await original(realization, request, authority)
            replaced.append(self.replace_runtime())
            return result
        with mock.patch.object(self.health, "observe_signed", side_effect=changed) as port:
            with self.assertRaises(EffectAttemptFoldDenied):
                await self.drive_until_signed()
        self.assertEqual(port.call_count, 1)
        self.assertEqual(len(replaced), 1)
        self.assertEqual(self.signed_attempt().state.status.value, "started")

    async def test_authenticated_path_and_nonhealthy_readiness_keep_distinct_outcome_semantics(self):
        self.health.responses = [NativeConnectionOutcome.CONNECTED]
        await self.prepare_and_start()
        original = self.health.observe_signed
        returned = []
        async def unhealthy(realization, request, authority):
            value = await original(realization, request, authority)
            returned.append(request)
            return replace(value, outcome=NodeHealthReadOutcome.UNHEALTHY)
        with mock.patch.object(self.health, "observe_signed", side_effect=unhealthy):
            for _ in range(len(self.plan.activities) + 2):
                result = await self.execute_one()
                if result["run_status"] == "failed":
                    break
        self.assertEqual(result["run_status"], "failed")
        self.assertEqual(len(returned), 2)
        for request in returned:
            operation = self.plan.activity(request.activity_id).operation
            self.assertIs(type(operation), ObserveManagementBootstrap)
            identity = EffectAttemptIdentity(request.source.run_id, request.activity_id.value, 1)
            with self.unit_of_work() as uow:
                attempt = uow.stores.effect_attempts.get(identity)
            expected = "succeeded" if operation.stage is ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH else "failed"
            self.assertEqual(attempt.state.status.value, expected)
