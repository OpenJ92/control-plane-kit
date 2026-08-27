from __future__ import annotations

from dataclasses import dataclass
import os
import unittest

import psycopg

from gateway_rotation_overlap_fixture import (
    GatewayRotationOverlapFixture,
    PUBLIC_KEY_B,
    effect_attempt_execution_coordinator,
    runtime_result_for_outcome,
)
from gateway_rotation_retirement_fixture import CountingIds
from control_plane_kit_core.delegation_keys import (
    DelegationKeyAlgorithm,
    DelegationKeyPurpose,
    DelegationPublicKey,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference,
    SecretProviderId,
    SecretReference,
    SecretUseIntent,
    SecretVersionRevocationReceipt,
)
from control_plane_kit_operations.approvals import (
    ApprovalCommandService,
    DecideApproval,
    RequestGatewayKeyRotationApproval,
)
from control_plane_kit_operations.coordinator import (
    ActivityExecutionOutcome,
    ActivityRealizationContext,
)
from control_plane_kit_operations.gateway_key_rotation_activation import (
    GatewayKeyRotationActivationOutcome,
    GatewayKeyRotationActivationProgram,
    ProgressGatewayKeyRotationActivation,
)
from control_plane_kit_operations.gateway_key_rotation_completion_program import (
    CompleteGatewayKeyRotation,
    GatewayKeyRotationCompletionOutcome,
    GatewayKeyRotationCompletionProgram,
    GatewayKeyRotationRevocationEffectOutcome,
    GatewayKeyRotationRevocationEffectResult,
)
from control_plane_kit_operations.gateway_key_rotation_overlap_execution import (
    GatewayKeyRotationOverlapExecutionProgram,
    ProgressGatewayKeyRotationOverlap,
)
from control_plane_kit_operations.gateway_key_rotation_overlap_program import (
    GatewayKeyRotationOverlapPreparationProgram,
    PrepareGatewayKeyRotationOverlap,
)
from control_plane_kit_operations.gateway_key_rotation_program import (
    GatewayKeyGenerationResult,
    GatewayKeyRotationGenerationProgram,
    PrepareGatewayKeyRotationGeneration,
    SubmitGatewayKeyRotationGeneration,
)
from control_plane_kit_operations.gateway_key_rotation_retirement_execution import (
    GatewayKeyRotationRetirementExecutionProgram,
    ProgressGatewayKeyRotationRetirement,
)
from control_plane_kit_operations.gateway_key_rotation_retirement_program import (
    GatewayKeyRotationRetirementPreparationProgram,
    PrepareGatewayKeyRotationRetirement,
)
from control_plane_kit_operations.gateway_key_rotations import (
    AdvanceGatewayKeyRotation,
    GatewayKeyRotationService,
    GatewayKeyRotationStatus,
    RequestGatewayKeyRotation,
)
from control_plane_kit_operations.delegation_key_generation import (
    DelegationKeyGenerationEvidence,
)
from control_plane_kit_operations.delegation_signing_keys import (
    RegisteredDelegationSigningKeyStatus,
)
from control_plane_kit_operations.lifecycle import (
    ExecutionLeaseDuration,
    ExecutionWorkerAuthority,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import ApprovalDecisionKind
from control_plane_kit_operations.secret_providers import (
    RegisterSecretProviderCommand,
    RegisterSecretReferenceCommand,
    SecretProviderKind,
    SecretProviderRegistrationService,
)
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    OperationCommandService,
    StartOperationSession,
)


@dataclass(frozen=True)
class RotationPhaseEvidence:
    status: GatewayKeyRotationStatus
    version: int
    transition_id: str


class _TrackingUnitOfWork:
    def __init__(self, inner, owner) -> None:
        self._inner = inner
        self._owner = owner

    def __enter__(self):
        self._inner.__enter__()
        self._owner.active += 1
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            return self._inner.__exit__(exc_type, exc, traceback)
        finally:
            self._owner.active -= 1

    @property
    def stores(self):
        return self._inner.stores

    def commit(self) -> None:
        self._inner.commit()

    def rollback(self) -> None:
        self._inner.rollback()


class TrackingUnitOfWorkFactory:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.active = 0

    def __call__(self):
        return _TrackingUnitOfWork(
            PostgresUnitOfWork(lambda: psycopg.connect(self.database_url)),
            self,
        )


class RecordingRuntimeAdapter:
    def __init__(self, transaction_tracker: TrackingUnitOfWorkFactory) -> None:
        self._tracker = transaction_tracker
        self.calls: list[str] = []

    def execute(
        self,
        context: ActivityRealizationContext,
    ) -> ActivityExecutionOutcome:
        if self._tracker.active:
            raise AssertionError("runtime effect executed inside Postgres transaction")
        self.calls.append(context.activity.activity_id.value)
        return ActivityExecutionOutcome.succeeded()

    def execute_runtime(self, context, request):
        return runtime_result_for_outcome(
            self.execute(context),
            request.effect_id,
        )


class RecordingGenerationProvider:
    def __init__(self, transaction_tracker: TrackingUnitOfWorkFactory) -> None:
        self._tracker = transaction_tracker
        self.calls = 0

    def generate(self, action) -> DelegationKeyGenerationEvidence:
        if self._tracker.active:
            raise AssertionError("provider generation executed inside transaction")
        self.calls += 1
        return DelegationKeyGenerationEvidence(
            workspace_id="workspace-a",
            reference=action.grant.reference,
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="cpk-server",
            correlation_id=action.grant.correlation_id,
            version_id="version-b",
            version_number=1,
            public_key=DelegationPublicKey(
                "key-b",
                DelegationKeyAlgorithm.ED25519,
                PUBLIC_KEY_B,
            ),
            replayed=False,
        )


class RecordingRevocationProvider:
    def __init__(self, transaction_tracker: TrackingUnitOfWorkFactory) -> None:
        self._tracker = transaction_tracker
        self.calls = 0
        self.versions = {"version-a": "active", "version-b": "active"}

    def revoke_version(self, grant) -> GatewayKeyRotationRevocationEffectResult:
        if self._tracker.active:
            raise AssertionError("provider revocation executed inside transaction")
        self.calls += 1
        if self.versions[grant.version_id] == "active":
            self.versions[grant.version_id] = "revoked"
        return GatewayKeyRotationRevocationEffectResult(
            GatewayKeyRotationRevocationEffectOutcome.REVOKED,
            receipt=SecretVersionRevocationReceipt(
                revocation_id=grant.revocation_id,
                provider_registration_id=grant.provider_registration_id,
                reference=grant.reference,
                version_id=grant.version_id,
                version_number=grant.version_number,
            ),
        )


class GatewayKeyRotationProgramAcceptanceTests(
    GatewayRotationOverlapFixture,
    unittest.TestCase,
):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError("run through control-plane-kit-operations/test.sh")
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.uow = TrackingUnitOfWorkFactory(database_url)
        self.ids = CountingIds("program")
        self.epoch = 1_000
        self.seed_graph_and_keys(include_replacement_key=False)
        self._admit_provider_and_old_reference()
        self.approved = self._request_and_approve()

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self):
        return self.uow()

    def test_complete_program_reconstructs_every_phase_from_durable_truth(self) -> None:
        generation_provider = RecordingGenerationProvider(self.uow)
        generation = GatewayKeyRotationGenerationProgram(
            self.uow,
            clock=lambda: self.epoch,
        )
        generation_command = PrepareGatewayKeyRotationGeneration(
            rotation_id=self.approved.rotation_id,
            expected_version=self.approved.version,
            actor_subject="operator-a",
            prepared_by="operator-a",
            prepared_at="2026-08-02T01:10:00Z",
            actor_scopes=(
                PolicyScope.DELEGATION_KEY_ROTATE,
                PolicyScope.DELEGATION_KEY_GENERATE,
            ),
        )
        generation_action = generation.prepare(generation_command)
        self.assertEqual(
            GatewayKeyRotationGenerationProgram(
                self.uow,
                clock=lambda: self.epoch,
            ).prepare(generation_command),
            generation_action,
        )
        generated_evidence = generation_provider.generate(generation_action)
        generation_submit = SubmitGatewayKeyRotationGeneration(
            action=generation_action,
            result=GatewayKeyGenerationResult.generated(generated_evidence),
            submitted_by="operator-a",
            submitted_at="2026-08-02T01:11:00Z",
            actor_scopes=(
                PolicyScope.DELEGATION_KEY_ROTATE,
                PolicyScope.DELEGATION_KEY_REGISTER,
            ),
        )
        generated = GatewayKeyRotationGenerationProgram(
            self.uow,
            clock=lambda: self.epoch,
        ).submit(generation_submit)
        self.assertTrue(
            GatewayKeyRotationGenerationProgram(
                self.uow,
                clock=lambda: self.epoch,
            ).submit(generation_submit).replayed
        )

        overlap_command = PrepareGatewayKeyRotationOverlap(
            rotation_id=generated.rotation.rotation_id,
            expected_rotation_version=generated.rotation.version,
            expected_authored_graph_id="graph-a",
            expected_current_realized_projection_id="projection-a",
            expected_desired_realized_projection_id="projection-a",
            expected_desired_graph_revision=1,
            actor_id="operator-a",
            actor_scopes=self._deployment_scopes(),
            worker_authority=self._worker(),
            lease_duration=ExecutionLeaseDuration(1800),
        )
        overlap = GatewayKeyRotationOverlapPreparationProgram(
            self.uow,
            clock=lambda: "2026-08-02T02:00:00Z",
            trusted_epoch_clock=lambda: self.epoch,
            id_factory=self.ids,
        ).prepare(overlap_command)
        overlap_replay = GatewayKeyRotationOverlapPreparationProgram(
            self.uow,
            clock=lambda: "2026-08-02T02:00:00Z",
            trusted_epoch_clock=lambda: self.epoch,
            id_factory=self.ids,
        ).prepare(overlap_command)
        self.assertEqual(overlap_replay.checkpoint, overlap.checkpoint)
        runtime = RecordingRuntimeAdapter(self.uow)
        overlap_execution_command = ProgressGatewayKeyRotationOverlap(
            rotation_id=overlap.rotation.rotation_id,
            expected_prepared_rotation_version=overlap.rotation.version,
            actor_id="operator-a",
            actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
            worker_authority=self._worker(),
            fence=ExecutionLeaseFence("worker-a", 1),
        )
        overlap_ready = self._execute_overlap(overlap_execution_command, runtime)
        overlap_effect_count = len(runtime.calls)
        self._overlap_execution_program(runtime).progress(
            overlap_execution_command
        )
        self.assertEqual(len(runtime.calls), overlap_effect_count)

        activation_command = ProgressGatewayKeyRotationActivation(
            rotation_id=overlap_ready.rotation.rotation_id,
            expected_overlap_version=overlap_ready.rotation.version,
            actor_id="operator-a",
            actor_scopes=(
                PolicyScope.DELEGATION_KEY_ROTATE,
                PolicyScope.DELEGATION_KEY_ACTIVATE,
            ),
        )
        waiting = self._activation_program().progress(activation_command)
        self.assertIs(waiting.outcome, GatewayKeyRotationActivationOutcome.WAITING)
        self.epoch = waiting.drain_deadline_epoch
        ready = self._activation_program().progress(activation_command)
        self.assertIs(
            ready.outcome,
            GatewayKeyRotationActivationOutcome.READY_FOR_RETIREMENT,
        )

        workspace = self._workspace()
        retirement_command = PrepareGatewayKeyRotationRetirement(
            rotation_id=ready.rotation.rotation_id,
            expected_rotation_version=ready.rotation.version,
            expected_authored_graph_id="graph-a",
            expected_current_realized_projection_id=(
                workspace.current_realized_projection_id
            ),
            expected_desired_realized_projection_id=(
                workspace.desired_realized_projection_id
            ),
            expected_desired_graph_revision=workspace.desired_graph_revision,
            actor_id="operator-a",
            actor_scopes=self._deployment_scopes(),
            worker_authority=self._worker(),
            lease_duration=ExecutionLeaseDuration(1800),
        )
        retirement = GatewayKeyRotationRetirementPreparationProgram(
            self.uow,
            clock=lambda: "2026-08-02T04:00:00Z",
            trusted_epoch_clock=lambda: self.epoch,
            id_factory=self.ids,
        ).prepare(retirement_command)
        retirement_replay = GatewayKeyRotationRetirementPreparationProgram(
            self.uow,
            clock=lambda: "2026-08-02T04:00:00Z",
            trusted_epoch_clock=lambda: self.epoch,
            id_factory=self.ids,
        ).prepare(retirement_command)
        self.assertEqual(retirement_replay.checkpoint, retirement.checkpoint)
        retirement_execution_command = ProgressGatewayKeyRotationRetirement(
            rotation_id=retirement.rotation.rotation_id,
            expected_prepared_rotation_version=retirement.rotation.version,
            actor_id="operator-a",
            actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
            worker_authority=self._worker(),
            fence=ExecutionLeaseFence("worker-a", 1),
        )
        retirement_ready = self._execute_retirement(
            retirement_execution_command,
            runtime,
        )
        retirement_effect_count = len(runtime.calls)
        self._retirement_execution_program(runtime).progress(
            retirement_execution_command
        )
        self.assertEqual(len(runtime.calls), retirement_effect_count)

        revocation = RecordingRevocationProvider(self.uow)
        completion_command = CompleteGatewayKeyRotation(
            rotation_id=retirement_ready.rotation.rotation_id,
            expected_retirement_ready_version=retirement_ready.rotation.version,
            actor_id="operator-a",
            actor_scopes=(
                PolicyScope.DELEGATION_KEY_ROTATE,
                PolicyScope.DELEGATION_KEY_RETIRE,
                PolicyScope.DELEGATION_KEY_REVOKE,
                PolicyScope.SECRET_PROVIDER_REVOKE,
            ),
        )
        completed = GatewayKeyRotationCompletionProgram(
            self.uow,
            revocation_adapter=revocation,
            clock=lambda: "2026-08-02T06:00:00Z",
            trusted_epoch_clock=lambda: self.epoch,
        ).progress(completion_command)
        completion_replay = GatewayKeyRotationCompletionProgram(
            self.uow,
            revocation_adapter=revocation,
            clock=lambda: "2026-08-02T06:00:00Z",
            trusted_epoch_clock=lambda: self.epoch,
        ).progress(completion_command)

        self.assertIs(
            completed.outcome,
            GatewayKeyRotationCompletionOutcome.COMPLETED,
        )
        self.assertIs(
            completion_replay.outcome,
            GatewayKeyRotationCompletionOutcome.COMPLETED_REPLAY,
        )
        self.assertEqual(generation_provider.calls, 1)
        self.assertEqual(revocation.calls, 1)
        self.assertEqual(revocation.versions["version-a"], "revoked")
        self.assertEqual(revocation.versions["version-b"], "active")
        self.assertGreater(len(runtime.calls), 0)
        self.assertEqual(self.uow.active, 0)
        self._assert_final_key_truth()
        self._assert_phase_ledger()

    def _execute_overlap(self, command, adapter):
        program = self._overlap_execution_program(adapter)
        for _ in range(32):
            result = program.progress(command)
            if result.rotation.status is GatewayKeyRotationStatus.OVERLAP_READY:
                return result
        self.fail(
            "overlap execution did not converge after 32 steps; "
            f"last status was {result.rotation.status.value}"
        )

    def _execute_retirement(self, command, adapter):
        program = self._retirement_execution_program(adapter)
        for _ in range(32):
            result = program.progress(command)
            if result.rotation.status is GatewayKeyRotationStatus.RETIREMENT_READY:
                return result
        self.fail(
            "retirement execution did not converge after 32 steps; "
            f"last status was {result.rotation.status.value}"
        )

    def _overlap_execution_program(self, adapter):
        coordinator = effect_attempt_execution_coordinator(
            self.uow,
            adapter,
            clock=lambda: "2026-08-02T03:00:00Z",
            prefix="program-overlap",
        )
        return GatewayKeyRotationOverlapExecutionProgram(
            self.uow,
            coordinator=coordinator,
            clock=lambda: "2026-08-02T03:00:00Z",
            trusted_epoch_clock=lambda: self.epoch,
            id_factory=self.ids,
        )

    def _retirement_execution_program(self, adapter):
        coordinator = effect_attempt_execution_coordinator(
            self.uow,
            adapter,
            clock=lambda: "2026-08-02T05:00:00Z",
            prefix="program-retirement",
        )
        return GatewayKeyRotationRetirementExecutionProgram(
            self.uow,
            coordinator=coordinator,
            clock=lambda: "2026-08-02T05:00:00Z",
            trusted_epoch_clock=lambda: self.epoch,
            id_factory=self.ids,
        )

    def _activation_program(self):
        return GatewayKeyRotationActivationProgram(
            self.uow,
            clock=lambda: "2026-08-02T03:30:00Z",
            trusted_epoch_clock=lambda: self.epoch,
        )

    def _request_and_approve(self):
        OperationCommandService(
            self.uow,
            clock=lambda: "2026-08-02T01:00:00Z",
            id_factory=self.ids,
        ).execute(
            StartOperationSession(
                "workspace-a",
                "operator-a",
                "Rotate gateway key",
                IdempotencyKey("program-session"),
            )
        )
        rotations = GatewayKeyRotationService(self.uow, clock=lambda: self.epoch)
        requested = rotations.request(
            RequestGatewayKeyRotation(
                workspace_id="workspace-a",
                gateway_node_id="gateway-a",
                purpose=DelegationKeyPurpose.GATEWAY_PROBE,
                issuer="cpk-server",
                old_key_id="key-a",
                new_secret_reference=SecretReference(
                    "secret://workspace-secrets/keys/key-b"
                ),
                key_generation_correlation="generate-key-b",
                maximum_grant_lifetime_seconds=60,
                clock_skew_seconds=5,
                correlation_id="rotation-program",
                requested_by="operator-a",
                requested_at="2026-08-02T01:01:00Z",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
            )
        )
        approvals = ApprovalCommandService(
            self.uow,
            clock=lambda: "2026-08-02T01:02:00Z",
            id_factory=self.ids,
        )
        approval = approvals.execute(
            RequestGatewayKeyRotationApproval(
                session_id="program-1",
                rotation_id=requested.rotation_id,
                actor_id="operator-a",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                idempotency_key=IdempotencyKey("program-approval"),
            )
        )
        awaiting = rotations.advance(
            AdvanceGatewayKeyRotation(
                requested.rotation_id,
                "program-awaiting-approval",
                requested.status,
                requested.version,
                GatewayKeyRotationStatus.AWAITING_APPROVAL,
                "operator-a",
                "2026-08-02T01:03:00Z",
                (PolicyScope.DELEGATION_KEY_ROTATE,),
                approval_request_id=approval.request.request_id,
            )
        )
        decision = approvals.execute(
            DecideApproval(
                session_id="program-1",
                request_id=approval.request.request_id,
                actor_id="manager-a",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE_APPROVE,),
                decision=ApprovalDecisionKind.APPROVED,
                idempotency_key=IdempotencyKey("program-approve"),
            )
        )
        return GatewayKeyRotationService(self.uow, clock=lambda: self.epoch).advance(
            AdvanceGatewayKeyRotation(
                awaiting.rotation_id,
                "program-approved",
                awaiting.status,
                awaiting.version,
                GatewayKeyRotationStatus.APPROVED,
                "operator-a",
                "2026-08-02T01:04:00Z",
                (PolicyScope.DELEGATION_KEY_ROTATE,),
                approval_decision_id=decision.decision.decision_id,
            )
        )

    def _admit_provider_and_old_reference(self) -> None:
        registrations = SecretProviderRegistrationService(self.uow)
        provider = registrations.register_provider(
            RegisterSecretProviderCommand(
                workspace_id="workspace-a",
                provider_id=SecretProviderId("workspace-secrets"),
                provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS,
                display_name="Workspace secrets",
                endpoint_reference=SecretProviderEndpointReference(
                    "secrets-endpoint"
                ),
                credential_reference=SecretReference(
                    "secret://workspace-secrets/provider-token"
                ),
                allowed_reference_prefixes=(
                    SecretReference("secret://workspace-secrets/keys"),
                ),
                allowed_intents=(SecretUseIntent.GATEWAY_PROBE_SIGNING_KEY,),
                admitted_by="operator-a",
                admitted_at="2026-08-02T01:00:00Z",
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,),
            )
        )
        registrations.register_reference(
            RegisterSecretReferenceCommand(
                workspace_id="workspace-a",
                reference=SecretReference(
                    "secret://workspace-secrets/keys/key-a"
                ),
                provider_registration_id=provider.registration_id,
                allowed_intents=(SecretUseIntent.GATEWAY_PROBE_SIGNING_KEY,),
                admitted_by="operator-a",
                admitted_at="2026-08-02T01:00:00Z",
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,),
                metadata={
                    "provider_version_id": "version-a",
                    "provider_version_number": 1,
                },
            )
        )

    def _workspace(self):
        with self.uow() as unit_of_work:
            value = unit_of_work.stores.workspaces.get("workspace-a")
            unit_of_work.commit()
            return value

    def _assert_final_key_truth(self) -> None:
        rotation = GatewayKeyRotationService(
            self.uow,
            clock=lambda: self.epoch,
        ).get(self.approved.rotation_id)
        with self.uow() as unit_of_work:
            old = unit_of_work.stores.delegation_signing_keys.get(
                "workspace-a", rotation.purpose, rotation.issuer, "key-a"
            )
            active = unit_of_work.stores.delegation_signing_keys.require_active(
                "workspace-a", rotation.purpose, rotation.issuer
            )
            unit_of_work.commit()
        self.assertIs(old.status, RegisteredDelegationSigningKeyStatus.REVOKED)
        self.assertEqual(active.key_id, "key-b")
        self.assertIs(rotation.status, GatewayKeyRotationStatus.COMPLETED)

    def _assert_phase_ledger(self) -> None:
        service = GatewayKeyRotationService(self.uow, clock=lambda: self.epoch)
        transitions = service.transitions(self.approved.rotation_id)
        evidence = (
            RotationPhaseEvidence(
                GatewayKeyRotationStatus.REQUESTED,
                1,
                "rotation-requested",
            ),
            *(
                RotationPhaseEvidence(
                    transition.to_status,
                    transition.to_version,
                    transition.transition_id,
                )
                for transition in transitions
            ),
        )
        self.assertEqual(
            tuple(item.status for item in evidence),
            (
                GatewayKeyRotationStatus.REQUESTED,
                GatewayKeyRotationStatus.AWAITING_APPROVAL,
                GatewayKeyRotationStatus.APPROVED,
                GatewayKeyRotationStatus.GENERATION_PREPARED,
                GatewayKeyRotationStatus.KEY_GENERATED,
                GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
                GatewayKeyRotationStatus.OVERLAP_READY,
                GatewayKeyRotationStatus.NEW_KEY_ACTIVE,
                GatewayKeyRotationStatus.DRAINING_OLD_GRANTS,
                GatewayKeyRotationStatus.RETIREMENT_DEPLOYING,
                GatewayKeyRotationStatus.RETIREMENT_READY,
                GatewayKeyRotationStatus.OLD_KEY_RETIRED,
                GatewayKeyRotationStatus.REVOCATION_PREPARED,
                GatewayKeyRotationStatus.COMPLETED,
            ),
        )
        leaked = repr(evidence).lower()
        for forbidden in (
            "secret://",
            "version-a",
            "version-b",
            "public key",
            "private",
            "compact",
        ):
            self.assertNotIn(forbidden, leaked)

    @staticmethod
    def _deployment_scopes() -> tuple[PolicyScope, ...]:
        return (
            PolicyScope.DELEGATION_KEY_ROTATE,
            PolicyScope.PLAN_EXECUTE,
            PolicyScope.EXECUTION_OPERATE,
        )

    @staticmethod
    def _worker() -> ExecutionWorkerAuthority:
        return ExecutionWorkerAuthority(
            "worker-a",
            (PolicyScope.EXECUTION_OPERATE,),
        )


if __name__ == "__main__":
    unittest.main()
