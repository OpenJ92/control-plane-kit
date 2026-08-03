from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
from threading import Barrier
import unittest

import psycopg

from gateway_rotation_overlap_fixture import (
    GatewayRotationOverlapFixture,
    PUBLIC_KEY_B,
)
from gateway_rotation_retirement_fixture import CountingIds
from control_plane_kit_core.delegation_keys import (
    DelegationKeyAlgorithm,
    DelegationKeyPurpose,
    DelegationPublicKey,
)
from control_plane_kit_core.operations.lifecycle import FailureCategory
from control_plane_kit_core.policies import PolicyScope
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
    ExecutionCoordinator,
)
from control_plane_kit_operations.gateway_key_rotation_activation import (
    GatewayKeyRotationActivationConflict,
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
    GatewayKeyRotationOverlapExecutionAuthorizationDenied,
    GatewayKeyRotationOverlapExecutionOutcome,
    GatewayKeyRotationOverlapExecutionProgram,
    ProgressGatewayKeyRotationOverlap,
)
from control_plane_kit_operations.gateway_key_rotation_overlap_program import (
    GatewayKeyRotationOverlapPreparationAuthorizationDenied,
    GatewayKeyRotationOverlapPreparationConflict,
    GatewayKeyRotationOverlapPreparationOutcome,
    GatewayKeyRotationOverlapPreparationProgram,
    PrepareGatewayKeyRotationOverlap,
)
from control_plane_kit_operations.gateway_key_rotation_program import (
    GatewayKeyGenerationOutcome,
    GatewayKeyGenerationResult,
    GatewayKeyRotationGenerationProgram,
    GatewayKeyRotationGenerationProgramAuthorizationDenied,
    GatewayKeyRotationGenerationProgramConflict,
    PrepareGatewayKeyRotationGeneration,
    SubmitGatewayKeyRotationGeneration,
)
from control_plane_kit_operations.gateway_key_rotation_retirement_execution import (
    GatewayKeyRotationRetirementExecutionOutcome,
    GatewayKeyRotationRetirementExecutionProgram,
    ProgressGatewayKeyRotationRetirement,
)
from control_plane_kit_operations.gateway_key_rotation_retirement_program import (
    GatewayKeyRotationRetirementPreparationConflict,
    GatewayKeyRotationRetirementPreparationOutcome,
    GatewayKeyRotationRetirementPreparationProgram,
    PrepareGatewayKeyRotationRetirement,
)
from control_plane_kit_operations.gateway_key_rotations import (
    AdvanceGatewayKeyRotation,
    GatewayKeyRotationConflict,
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
    ExecutionWorkerAuthority,
    RunLifecycleCommandService,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import (
    ApprovalDecisionKind,
    FailureEvidence,
)
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


@dataclass(frozen=True)
class RotationFailureEvidence:
    healthy_phases: tuple[GatewayKeyRotationStatus, ...]
    boundary: str
    outcome_code: str
    status: GatewayKeyRotationStatus
    version: int


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


class ScriptedRuntimeAdapter(RecordingRuntimeAdapter):
    def __init__(
        self,
        transaction_tracker: TrackingUnitOfWorkFactory,
        *outcomes: ActivityExecutionOutcome | BaseException,
    ) -> None:
        super().__init__(transaction_tracker)
        self._outcomes = list(outcomes)

    def execute(
        self,
        context: ActivityRealizationContext,
    ) -> ActivityExecutionOutcome:
        if self._tracker.active:
            raise AssertionError("runtime effect executed inside Postgres transaction")
        self.calls.append(context.activity.activity_id.value)
        if not self._outcomes:
            raise AssertionError("unexpected duplicate runtime effect")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


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


class ScriptedRevocationProvider:
    def __init__(
        self,
        transaction_tracker: TrackingUnitOfWorkFactory,
        result: GatewayKeyRotationRevocationEffectResult,
    ) -> None:
        self._tracker = transaction_tracker
        self._result = result
        self.calls = []

    def revoke_version(self, grant) -> GatewayKeyRotationRevocationEffectResult:
        if self._tracker.active:
            raise AssertionError("provider revocation executed inside transaction")
        self.calls.append(grant)
        return self._result


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
            lease_expires_at="2026-08-02T02:30:00Z",
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
            lease_expires_at="2026-08-02T04:30:00Z",
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
        late_overlap_replay = GatewayKeyRotationOverlapPreparationProgram(
            self.uow,
            clock=lambda: "2026-08-02T07:00:00Z",
            trusted_epoch_clock=lambda: self.epoch,
            id_factory=self.ids,
        ).prepare(overlap_command)
        late_retirement_replay = GatewayKeyRotationRetirementPreparationProgram(
            self.uow,
            clock=lambda: "2026-08-02T07:00:00Z",
            trusted_epoch_clock=lambda: self.epoch,
            id_factory=self.ids,
        ).prepare(retirement_command)
        self.assertIs(
            late_overlap_replay.outcome,
            GatewayKeyRotationOverlapPreparationOutcome.ALREADY_ADVANCED,
        )
        self.assertIs(
            late_retirement_replay.outcome,
            GatewayKeyRotationRetirementPreparationOutcome.ALREADY_ADVANCED,
        )
        self.assertEqual(late_overlap_replay.checkpoint, overlap_ready.checkpoint)
        self.assertEqual(
            late_retirement_replay.checkpoint,
            retirement_ready.checkpoint,
        )
        self.assertEqual(generation_provider.calls, 1)
        self.assertEqual(revocation.calls, 1)
        self.assertEqual(revocation.versions["version-a"], "revoked")
        self.assertEqual(revocation.versions["version-b"], "active")
        self.assertGreater(len(runtime.calls), 0)
        self.assertEqual(self.uow.active, 0)
        self._assert_final_key_truth()
        self._assert_phase_ledger()

    def test_authority_and_stale_lineage_fail_before_downstream_effects(self) -> None:
        runtime = RecordingRuntimeAdapter(self.uow)
        with self.assertRaises(
            GatewayKeyRotationGenerationProgramAuthorizationDenied
        ):
            self._prepare_generation(
                scopes=(PolicyScope.PLAN_EXECUTE,),
            )
        self.assertEqual(runtime.calls, [])
        self.assertIs(
            self._rotation().status,
            GatewayKeyRotationStatus.APPROVED,
        )

        generated, provider = self._drive_to_generated()
        self.assertEqual(provider.calls, 1)
        stale_commands = (
            {"expected_version": generated.rotation.version + 1},
            {"expected_authored_graph_id": "graph-stale"},
            {"expected_realized_projection_id": "projection-stale"},
            {"expected_desired_graph_revision": 2},
        )
        for changes in stale_commands:
            with self.subTest(changes=changes):
                with self.assertRaises(
                    GatewayKeyRotationOverlapPreparationConflict
                ):
                    self._prepare_overlap(generated.rotation, **changes)
        with self.assertRaises(
            GatewayKeyRotationOverlapPreparationAuthorizationDenied
        ):
            self._prepare_overlap(
                generated.rotation,
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
            )

        overlap = self._prepare_overlap(generated.rotation)
        unauthorized = ProgressGatewayKeyRotationOverlap(
            rotation_id=overlap.rotation.rotation_id,
            expected_prepared_rotation_version=overlap.rotation.version,
            actor_id="operator-a",
            actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
            worker_authority=ExecutionWorkerAuthority("worker-a", ()),
        )
        with self.assertRaises(GatewayKeyRotationOverlapExecutionAuthorizationDenied):
            self._overlap_execution_program(runtime).progress(unauthorized)

        self.assertEqual(runtime.calls, [])
        self._assert_diagnostic_safe(
            self._failure_evidence(
                boundary="overlap-execution-authorization",
                outcome_code="execution-operate-required",
                rotation=self._rotation(),
            )
        )

    def test_missing_and_rejected_approval_dispatch_no_effects(self) -> None:
        rotations = GatewayKeyRotationService(self.uow, clock=lambda: self.epoch)
        fresh = rotations.request(
            RequestGatewayKeyRotation(
                workspace_id="workspace-a",
                gateway_node_id="gateway-b",
                purpose=DelegationKeyPurpose.GATEWAY_PROBE,
                issuer="cpk-server",
                old_key_id="key-a",
                new_secret_reference=SecretReference(
                    "secret://workspace-secrets/keys/key-c"
                ),
                key_generation_correlation="generate-key-c",
                maximum_grant_lifetime_seconds=60,
                clock_skew_seconds=5,
                correlation_id="rotation-rejected",
                requested_by="operator-a",
                requested_at="2026-08-02T01:20:00Z",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
            )
        )
        provider = RecordingGenerationProvider(self.uow)
        runtime = RecordingRuntimeAdapter(self.uow)
        with self.assertRaises(GatewayKeyRotationGenerationProgramConflict):
            self._prepare_generation_for(fresh)

        approvals = ApprovalCommandService(
            self.uow,
            clock=lambda: "2026-08-02T01:21:00Z",
            id_factory=self.ids,
        )
        approval = approvals.execute(
            RequestGatewayKeyRotationApproval(
                session_id="program-1",
                rotation_id=fresh.rotation_id,
                actor_id="operator-a",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                idempotency_key=IdempotencyKey("program-rejected-approval"),
            )
        )
        awaiting = rotations.advance(
            AdvanceGatewayKeyRotation(
                fresh.rotation_id,
                "program-rejected-awaiting",
                fresh.status,
                fresh.version,
                GatewayKeyRotationStatus.AWAITING_APPROVAL,
                "operator-a",
                "2026-08-02T01:22:00Z",
                (PolicyScope.DELEGATION_KEY_ROTATE,),
                approval_request_id=approval.request.request_id,
            )
        )
        rejected = approvals.execute(
            DecideApproval(
                session_id="program-1",
                request_id=approval.request.request_id,
                actor_id="manager-a",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE_APPROVE,),
                decision=ApprovalDecisionKind.REJECTED,
                idempotency_key=IdempotencyKey("program-reject-approval"),
            )
        )
        with self.assertRaises(GatewayKeyRotationConflict):
            rotations.advance(
                AdvanceGatewayKeyRotation(
                    awaiting.rotation_id,
                    "program-rejected-as-approved",
                    awaiting.status,
                    awaiting.version,
                    GatewayKeyRotationStatus.APPROVED,
                    "operator-a",
                    "2026-08-02T01:23:00Z",
                    (PolicyScope.DELEGATION_KEY_ROTATE,),
                    approval_decision_id=rejected.decision.decision_id,
                )
            )
        with self.assertRaises(GatewayKeyRotationGenerationProgramConflict):
            self._prepare_generation_for(awaiting)
        self.assertEqual(provider.calls, 0)
        self.assertEqual(runtime.calls, [])
        self._assert_diagnostic_safe(
            self._failure_evidence(
                boundary="rotation-approval",
                outcome_code="approval-rejected",
                rotation=awaiting,
            )
        )

    def test_generation_definite_failure_retries_but_uncertainty_blocks(self) -> None:
        action = self._prepare_generation()
        definite = GatewayKeyRotationGenerationProgram(
            self.uow,
            clock=lambda: self.epoch,
        ).submit(
            self._generation_submit(
                action,
                GatewayKeyGenerationResult.definite_failure(
                    "provider-unavailable"
                ),
            )
        )
        self.assertIs(definite.outcome, GatewayKeyGenerationOutcome.DEFINITE_FAILURE)
        self.assertEqual(definite.next_action, action)
        self.assertIs(
            definite.rotation.status,
            GatewayKeyRotationStatus.GENERATION_PREPARED,
        )

        uncertain = GatewayKeyRotationGenerationProgram(
            self.uow,
            clock=lambda: self.epoch,
        ).submit(
            self._generation_submit(
                action,
                GatewayKeyGenerationResult.uncertain(
                    "provider-response-uncertain"
                ),
            )
        )
        self.assertIs(uncertain.rotation.status, GatewayKeyRotationStatus.BLOCKED)
        self.assertIsNone(uncertain.next_action)
        with self.assertRaises(GatewayKeyRotationOverlapPreparationConflict):
            self._prepare_overlap(uncertain.rotation)
        self.assertEqual(
            self._phase_statuses(),
            (
                GatewayKeyRotationStatus.REQUESTED,
                GatewayKeyRotationStatus.AWAITING_APPROVAL,
                GatewayKeyRotationStatus.APPROVED,
                GatewayKeyRotationStatus.GENERATION_PREPARED,
                GatewayKeyRotationStatus.BLOCKED,
            ),
        )
        self._assert_diagnostic_safe(
            self._failure_evidence(
                boundary="generation-provider-result",
                outcome_code=uncertain.rotation.failure_code,
                rotation=uncertain.rotation,
            )
        )

    def test_overlap_failure_preserves_generation_and_prevents_activation(self) -> None:
        generated, provider = self._drive_to_generated()
        overlap = self._prepare_overlap(generated.rotation)
        runtime = ScriptedRuntimeAdapter(
            self.uow,
            ActivityExecutionOutcome.failed(
                FailureEvidence(
                    FailureCategory.TERMINAL,
                    "test-effect-failed",
                    "test effect failed",
                )
            ),
        )
        command = self._overlap_execution_command(overlap.rotation)
        blocked = self._overlap_execution_program(runtime).progress(command)

        self.assertIs(
            blocked.outcome,
            GatewayKeyRotationOverlapExecutionOutcome.BLOCKED,
        )
        self.assertEqual(blocked.failure_code, "overlap-effect-failed")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(len(runtime.calls), 1)
        with self.assertRaises(GatewayKeyRotationActivationConflict):
            self._activation_program().progress(
                self._activation_command(blocked.rotation)
            )
        self.assertEqual(len(runtime.calls), 1)
        self.assertEqual(
            self._phase_statuses(),
            (
                GatewayKeyRotationStatus.REQUESTED,
                GatewayKeyRotationStatus.AWAITING_APPROVAL,
                GatewayKeyRotationStatus.APPROVED,
                GatewayKeyRotationStatus.GENERATION_PREPARED,
                GatewayKeyRotationStatus.KEY_GENERATED,
                GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
                GatewayKeyRotationStatus.BLOCKED,
            ),
        )
        self._assert_diagnostic_safe(
            self._failure_evidence(
                boundary="overlap-runtime-effect",
                outcome_code=blocked.failure_code,
                rotation=blocked.rotation,
            )
        )

    def test_ambiguous_overlap_effect_blocks_without_redispatch(self) -> None:
        generated, _provider = self._drive_to_generated()
        overlap = self._prepare_overlap(generated.rotation)
        crashing = ScriptedRuntimeAdapter(
            self.uow,
            RuntimeError("runtime result lost"),
        )
        command = self._overlap_execution_command(overlap.rotation)
        blocked = self._overlap_execution_program(crashing).progress(command)
        self.assertIs(
            blocked.outcome,
            GatewayKeyRotationOverlapExecutionOutcome.BLOCKED,
        )
        self.assertEqual(blocked.failure_code, "overlap-effect-uncertain")
        self.assertEqual(len(crashing.calls), 1)

        forbidden_replay = RecordingRuntimeAdapter(self.uow)
        replay = self._overlap_execution_program(forbidden_replay).progress(command)
        self.assertIs(
            replay.outcome,
            GatewayKeyRotationOverlapExecutionOutcome.BLOCKED,
        )
        self.assertEqual(forbidden_replay.calls, [])
        self._assert_diagnostic_safe(
            self._failure_evidence(
                boundary="overlap-runtime-result",
                outcome_code=replay.failure_code,
                rotation=replay.rotation,
            )
        )

    def test_premature_drain_and_retirement_failure_prevent_completion(self) -> None:
        overlap_ready, runtime = self._drive_to_overlap_ready()
        activation_command = self._activation_command(overlap_ready.rotation)
        waiting = self._activation_program().progress(activation_command)
        self.assertIs(waiting.outcome, GatewayKeyRotationActivationOutcome.WAITING)
        with self.assertRaises(GatewayKeyRotationRetirementPreparationConflict):
            self._prepare_retirement(waiting.rotation)

        self.epoch = waiting.drain_deadline_epoch
        ready = self._activation_program().progress(activation_command)
        retirement = self._prepare_retirement(ready.rotation)
        failing_runtime = ScriptedRuntimeAdapter(
            self.uow,
            ActivityExecutionOutcome.failed(
                FailureEvidence(
                    FailureCategory.TERMINAL,
                    "test-effect-failed",
                    "test effect failed",
                )
            ),
        )
        blocked = self._retirement_execution_program(failing_runtime).progress(
            self._retirement_execution_command(retirement.rotation)
        )
        self.assertIs(
            blocked.outcome,
            GatewayKeyRotationRetirementExecutionOutcome.BLOCKED,
        )
        self.assertEqual(blocked.failure_code, "retirement-effect-failed")
        revocation = RecordingRevocationProvider(self.uow)
        completion = GatewayKeyRotationCompletionProgram(
            self.uow,
            revocation_adapter=revocation,
            clock=lambda: "2026-08-02T06:00:00Z",
            trusted_epoch_clock=lambda: self.epoch,
        ).progress(self._completion_command(blocked.rotation))
        self.assertIs(
            completion.outcome,
            GatewayKeyRotationCompletionOutcome.BLOCKED,
        )
        self.assertEqual(revocation.calls, 0)
        self.assertGreater(len(runtime.calls), 0)
        self.assertEqual(len(failing_runtime.calls), 1)
        self._assert_diagnostic_safe(
            self._failure_evidence(
                boundary="retirement-runtime-effect",
                outcome_code=blocked.failure_code,
                rotation=blocked.rotation,
            )
        )

    def test_revocation_uncertainty_blocks_without_replaying_provider_io(self) -> None:
        retirement_ready, runtime = self._drive_to_retirement_ready()
        revocation = ScriptedRevocationProvider(
            self.uow,
            GatewayKeyRotationRevocationEffectResult(
                GatewayKeyRotationRevocationEffectOutcome.UNCERTAIN,
                failure_code="provider-outcome-uncertain",
            ),
        )
        command = self._completion_command(retirement_ready.rotation)
        blocked = GatewayKeyRotationCompletionProgram(
            self.uow,
            revocation_adapter=revocation,
            clock=lambda: "2026-08-02T06:00:00Z",
            trusted_epoch_clock=lambda: self.epoch,
        ).progress(command)
        self.assertIs(
            blocked.outcome,
            GatewayKeyRotationCompletionOutcome.BLOCKED,
        )
        self.assertEqual(len(revocation.calls), 1)
        replay = GatewayKeyRotationCompletionProgram(
            self.uow,
            revocation_adapter=revocation,
            clock=lambda: "2026-08-02T06:00:00Z",
            trusted_epoch_clock=lambda: self.epoch,
        ).progress(command)
        self.assertIs(
            replay.outcome,
            GatewayKeyRotationCompletionOutcome.BLOCKED,
        )
        self.assertEqual(len(revocation.calls), 1)
        self.assertGreater(len(runtime.calls), 0)
        self._assert_diagnostic_safe(
            self._failure_evidence(
                boundary="revocation-provider-result",
                outcome_code=blocked.failure_code,
                rotation=blocked.rotation,
            )
        )

    def test_concurrent_exact_activation_has_one_durable_transition_sequence(self) -> None:
        overlap_ready, runtime = self._drive_to_overlap_ready()
        command = self._activation_command(overlap_ready.rotation)
        barrier = Barrier(2)

        def progress():
            barrier.wait()
            return self._activation_program().progress(command)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(pool.map(lambda _: progress(), range(2)))

        self.assertEqual(results[0].rotation, results[1].rotation)
        self.assertEqual(
            {result.outcome for result in results},
            {GatewayKeyRotationActivationOutcome.WAITING},
        )
        statuses = self._phase_statuses()
        self.assertEqual(statuses.count(GatewayKeyRotationStatus.NEW_KEY_ACTIVE), 1)
        self.assertEqual(
            statuses.count(GatewayKeyRotationStatus.DRAINING_OLD_GRANTS),
            1,
        )
        self.assertGreater(len(runtime.calls), 0)
        self.assertEqual(self.uow.active, 0)

    def _prepare_generation(
        self,
        *,
        scopes: tuple[PolicyScope, ...] | None = None,
    ):
        return GatewayKeyRotationGenerationProgram(
            self.uow,
            clock=lambda: self.epoch,
        ).prepare(
            PrepareGatewayKeyRotationGeneration(
                rotation_id=self.approved.rotation_id,
                expected_version=self.approved.version,
                actor_subject="operator-a",
                prepared_by="operator-a",
                prepared_at="2026-08-02T01:10:00Z",
                actor_scopes=(
                    (
                        PolicyScope.DELEGATION_KEY_ROTATE,
                        PolicyScope.DELEGATION_KEY_GENERATE,
                    )
                    if scopes is None
                    else scopes
                ),
            )
        )

    def _prepare_generation_for(self, rotation):
        return GatewayKeyRotationGenerationProgram(
            self.uow,
            clock=lambda: self.epoch,
        ).prepare(
            PrepareGatewayKeyRotationGeneration(
                rotation_id=rotation.rotation_id,
                expected_version=rotation.version,
                actor_subject="operator-a",
                prepared_by="operator-a",
                prepared_at="2026-08-02T01:24:00Z",
                actor_scopes=(
                    PolicyScope.DELEGATION_KEY_ROTATE,
                    PolicyScope.DELEGATION_KEY_GENERATE,
                ),
            )
        )

    @staticmethod
    def _generation_submit(action, result):
        return SubmitGatewayKeyRotationGeneration(
            action=action,
            result=result,
            submitted_by="operator-a",
            submitted_at="2026-08-02T01:11:00Z",
            actor_scopes=(
                PolicyScope.DELEGATION_KEY_ROTATE,
                PolicyScope.DELEGATION_KEY_REGISTER,
            ),
        )

    def _drive_to_generated(self):
        action = self._prepare_generation()
        provider = RecordingGenerationProvider(self.uow)
        evidence = provider.generate(action)
        result = GatewayKeyRotationGenerationProgram(
            self.uow,
            clock=lambda: self.epoch,
        ).submit(
            self._generation_submit(
                action,
                GatewayKeyGenerationResult.generated(evidence),
            )
        )
        return result, provider

    def _prepare_overlap(
        self,
        rotation,
        *,
        expected_version: int | None = None,
        expected_authored_graph_id: str = "graph-a",
        expected_realized_projection_id: str = "projection-a",
        expected_desired_graph_revision: int = 1,
        actor_scopes: tuple[PolicyScope, ...] | None = None,
    ):
        return GatewayKeyRotationOverlapPreparationProgram(
            self.uow,
            clock=lambda: "2026-08-02T02:00:00Z",
            trusted_epoch_clock=lambda: self.epoch,
            id_factory=self.ids,
        ).prepare(
            PrepareGatewayKeyRotationOverlap(
                rotation_id=rotation.rotation_id,
                expected_rotation_version=(
                    rotation.version
                    if expected_version is None
                    else expected_version
                ),
                expected_authored_graph_id=expected_authored_graph_id,
                expected_current_realized_projection_id=(
                    expected_realized_projection_id
                ),
                expected_desired_realized_projection_id=(
                    expected_realized_projection_id
                ),
                expected_desired_graph_revision=expected_desired_graph_revision,
                actor_id="operator-a",
                actor_scopes=(
                    self._deployment_scopes()
                    if actor_scopes is None
                    else actor_scopes
                ),
                worker_authority=self._worker(),
                lease_expires_at="2026-08-02T02:30:00Z",
            )
        )

    def _overlap_execution_command(self, rotation):
        return ProgressGatewayKeyRotationOverlap(
            rotation_id=rotation.rotation_id,
            expected_prepared_rotation_version=rotation.version,
            actor_id="operator-a",
            actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
            worker_authority=self._worker(),
        )

    def _drive_to_overlap_ready(self):
        generated, _provider = self._drive_to_generated()
        overlap = self._prepare_overlap(generated.rotation)
        runtime = RecordingRuntimeAdapter(self.uow)
        return (
            self._execute_overlap(
                self._overlap_execution_command(overlap.rotation),
                runtime,
            ),
            runtime,
        )

    @staticmethod
    def _activation_command(rotation):
        return ProgressGatewayKeyRotationActivation(
            rotation_id=rotation.rotation_id,
            expected_overlap_version=rotation.version,
            actor_id="operator-a",
            actor_scopes=(
                PolicyScope.DELEGATION_KEY_ROTATE,
                PolicyScope.DELEGATION_KEY_ACTIVATE,
            ),
        )

    def _prepare_retirement(self, rotation):
        workspace = self._workspace()
        return GatewayKeyRotationRetirementPreparationProgram(
            self.uow,
            clock=lambda: "2026-08-02T04:00:00Z",
            trusted_epoch_clock=lambda: self.epoch,
            id_factory=self.ids,
        ).prepare(
            PrepareGatewayKeyRotationRetirement(
                rotation_id=rotation.rotation_id,
                expected_rotation_version=rotation.version,
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
                lease_expires_at="2026-08-02T04:30:00Z",
            )
        )

    def _drive_to_retirement_ready(self):
        overlap_ready, runtime = self._drive_to_overlap_ready()
        activation_command = self._activation_command(overlap_ready.rotation)
        waiting = self._activation_program().progress(activation_command)
        self.epoch = waiting.drain_deadline_epoch
        ready = self._activation_program().progress(activation_command)
        retirement = self._prepare_retirement(ready.rotation)
        return (
            self._execute_retirement(
                self._retirement_execution_command(retirement.rotation),
                runtime,
            ),
            runtime,
        )

    def _retirement_execution_command(self, rotation):
        return ProgressGatewayKeyRotationRetirement(
            rotation_id=rotation.rotation_id,
            expected_prepared_rotation_version=rotation.version,
            actor_id="operator-a",
            actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
            worker_authority=self._worker(),
        )

    @staticmethod
    def _completion_command(rotation):
        return CompleteGatewayKeyRotation(
            rotation_id=rotation.rotation_id,
            expected_retirement_ready_version=rotation.version,
            actor_id="operator-a",
            actor_scopes=(
                PolicyScope.DELEGATION_KEY_ROTATE,
                PolicyScope.DELEGATION_KEY_RETIRE,
                PolicyScope.DELEGATION_KEY_REVOKE,
                PolicyScope.SECRET_PROVIDER_REVOKE,
            ),
        )

    def _rotation(self):
        return GatewayKeyRotationService(
            self.uow,
            clock=lambda: self.epoch,
        ).get(self.approved.rotation_id)

    def _phase_statuses(self):
        transitions = GatewayKeyRotationService(
            self.uow,
            clock=lambda: self.epoch,
        ).transitions(self.approved.rotation_id)
        return (
            GatewayKeyRotationStatus.REQUESTED,
            *(transition.to_status for transition in transitions),
        )

    def _failure_evidence(
        self,
        *,
        boundary: str,
        outcome_code: str,
        rotation,
    ) -> RotationFailureEvidence:
        transitions = GatewayKeyRotationService(
            self.uow,
            clock=lambda: self.epoch,
        ).transitions(rotation.rotation_id)
        phases = (
            GatewayKeyRotationStatus.REQUESTED,
            *(transition.to_status for transition in transitions),
        )
        if phases[-1] is GatewayKeyRotationStatus.BLOCKED:
            phases = phases[:-1]
        return RotationFailureEvidence(
            healthy_phases=phases,
            boundary=boundary,
            outcome_code=outcome_code,
            status=rotation.status,
            version=rotation.version,
        )

    def _assert_diagnostic_safe(self, evidence: RotationFailureEvidence) -> None:
        self.assertTrue(evidence.healthy_phases)
        self.assertIs(
            evidence.healthy_phases[0],
            GatewayKeyRotationStatus.REQUESTED,
        )
        self.assertNotIn(GatewayKeyRotationStatus.BLOCKED, evidence.healthy_phases)
        self.assertIsInstance(evidence.status, GatewayKeyRotationStatus)
        self.assertGreater(evidence.version, 0)
        self.assertTrue(evidence.outcome_code)
        rendered = repr(evidence).lower()
        for forbidden in (
            "secret://",
            "version-a",
            "version-b",
            "public key",
            "-----begin public key-----",
            "private",
            "compact",
        ):
            self.assertNotIn(forbidden, rendered)

    def _execute_overlap(self, command, adapter):
        for _ in range(32):
            result = self._overlap_execution_program(adapter).progress(command)
            if result.rotation.status is GatewayKeyRotationStatus.OVERLAP_READY:
                return result
        self.fail(
            "overlap execution did not converge after 32 steps; "
            f"last status was {result.rotation.status.value}"
        )

    def _execute_retirement(self, command, adapter):
        for _ in range(32):
            result = self._retirement_execution_program(adapter).progress(command)
            if result.rotation.status is GatewayKeyRotationStatus.RETIREMENT_READY:
                return result
        self.fail(
            "retirement execution did not converge after 32 steps; "
            f"last status was {result.rotation.status.value}"
        )

    def _overlap_execution_program(self, adapter):
        lifecycle = RunLifecycleCommandService(
            self.uow,
            clock=lambda: "2026-08-02T03:00:00Z",
            id_factory=self.ids,
        )
        coordinator = ExecutionCoordinator(
            self.uow,
            lifecycle=lifecycle,
            adapter=adapter,
            clock=lambda: "2026-08-02T03:00:00Z",
            id_factory=self.ids,
        )
        return GatewayKeyRotationOverlapExecutionProgram(
            self.uow,
            coordinator=coordinator,
            clock=lambda: "2026-08-02T03:00:00Z",
            trusted_epoch_clock=lambda: self.epoch,
            id_factory=self.ids,
        )

    def _retirement_execution_program(self, adapter):
        lifecycle = RunLifecycleCommandService(
            self.uow,
            clock=lambda: "2026-08-02T05:00:00Z",
            id_factory=self.ids,
        )
        coordinator = ExecutionCoordinator(
            self.uow,
            lifecycle=lifecycle,
            adapter=adapter,
            clock=lambda: "2026-08-02T05:00:00Z",
            id_factory=self.ids,
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
