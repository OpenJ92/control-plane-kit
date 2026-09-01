from __future__ import annotations

from dataclasses import replace
import unittest

from gateway_rotation_overlap_fixture import (
    CrashAfterCommitUnitOfWork,
    CrashControl,
    SimulatedProcessLoss,
)
from gateway_rotation_retirement_fixture import (
    GatewayRotationRetirementFixture,
    RecordingAdapter,
)
from control_plane_kit_core.operations.lifecycle import ActivityEventKind
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference,
    SecretProviderId,
    SecretReference,
    SecretUseIntent,
    SecretVersionRevocationReceipt,
)
from control_plane_kit_operations.coordinator import ActivityExecutionOutcome
from control_plane_kit_operations.delegation_signing_keys import (
    RegisteredDelegationSigningKeyStatus,
)
from control_plane_kit_operations.gateway_key_rotation_completion_program import (
    CompleteGatewayKeyRotation,
    GatewayKeyRotationCompletionAuthorizationDenied,
    GatewayKeyRotationCompletionConflict,
    GatewayKeyRotationCompletionOutcome,
    GatewayKeyRotationCompletionProgram,
    GatewayKeyRotationRevocationEffectOutcome,
    GatewayKeyRotationRevocationEffectResult,
)
from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotationService,
    GatewayKeyRotationStatus,
)
from control_plane_kit_operations.secret_providers import (
    RegisterSecretProviderCommand,
    RegisterSecretReferenceCommand,
    SecretProviderRegistrationService,
    SecretProviderKind,
)


class ReplaySafeRevocationAdapter:
    def __init__(
        self,
        *outcomes: GatewayKeyRotationRevocationEffectResult | BaseException,
    ) -> None:
        self.outcomes = list(outcomes)
        self.calls = []
        self.mutations = 0
        self._receipts = {}
        self.versions = {"version-a": "active", "version-b": "active"}

    def revoke_version(self, grant):
        self.calls.append(grant)
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        receipt = self._receipts.get(grant.correlation_id)
        if receipt is None:
            self.mutations += 1
            if self.versions.get(grant.version_id) != "active":
                raise AssertionError("unexpected duplicate provider mutation")
            self.versions[grant.version_id] = "revoked"
            receipt = SecretVersionRevocationReceipt(
                revocation_id=grant.revocation_id,
                provider_registration_id=grant.provider_registration_id,
                reference=grant.reference,
                version_id=grant.version_id,
                version_number=grant.version_number,
            )
            self._receipts[grant.correlation_id] = receipt
        return GatewayKeyRotationRevocationEffectResult(
            GatewayKeyRotationRevocationEffectOutcome.REVOKED,
            receipt=receipt,
        )


class GatewayKeyRotationCompletionFixture(GatewayRotationRetirementFixture):
    def reset_truth(self) -> None:
        super().reset_truth()
        self._seed_old_key_custody()
        self.prepare_retirement_execution()
        activity_count = self.retirement_activity_count()
        adapter = RecordingAdapter(
            *(ActivityExecutionOutcome.succeeded(),) * activity_count
        )
        program = self.execution_program(adapter, prefix="accept-retirement")
        for position in range(1, activity_count + 1):
            result = program.progress(
                self.execution_command(
                    idempotency_key=f"accept-retirement-{position}"
                )
            )
        self.assertEqual(
            self.retirement_event_kinds().count(
                ActivityEventKind.CURRENT_GRAPH_ADVANCED
            ),
            1,
        )
        self.assertIs(result.rotation.status, GatewayKeyRotationStatus.RETIREMENT_READY)
        self.retirement_ready_version = result.rotation.version

    def _seed_old_key_custody(self) -> None:
        registrations = SecretProviderRegistrationService(self.unit_of_work)
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

    def completion_command(
        self,
        *,
        expected_version: int | None = None,
        scopes: tuple[PolicyScope, ...] | None = None,
    ) -> CompleteGatewayKeyRotation:
        return CompleteGatewayKeyRotation(
            rotation_id=self.rotation_id,
            expected_retirement_ready_version=(
                self.retirement_ready_version
                if expected_version is None
                else expected_version
            ),
            actor_id="operator-a",
            actor_scopes=scopes or self.completion_scopes(),
        )

    def completion_program(
        self,
        adapter,
        *,
        unit_of_work_factory=None,
    ) -> GatewayKeyRotationCompletionProgram:
        return GatewayKeyRotationCompletionProgram(
            unit_of_work_factory or self.unit_of_work,
            revocation_adapter=adapter,
            clock=lambda: "2026-08-02T06:00:00Z",
            trusted_epoch_clock=lambda: 6_000,
        )

    @staticmethod
    def completion_scopes() -> tuple[PolicyScope, ...]:
        return (
            PolicyScope.DELEGATION_KEY_ROTATE,
            PolicyScope.DELEGATION_KEY_RETIRE,
            PolicyScope.DELEGATION_KEY_REVOKE,
            PolicyScope.SECRET_PROVIDER_REVOKE,
        )


class GatewayKeyRotationCompletionTests(
    GatewayKeyRotationCompletionFixture,
    unittest.TestCase,
):
    def test_retires_a_revokes_exact_version_and_completes(self) -> None:
        adapter = ReplaySafeRevocationAdapter()

        result = self.completion_program(adapter).progress(self.completion_command())

        self.assertIs(result.outcome, GatewayKeyRotationCompletionOutcome.COMPLETED)
        self.assertIs(result.rotation.status, GatewayKeyRotationStatus.COMPLETED)
        self.assertEqual(adapter.mutations, 1)
        self.assertEqual(len(adapter.calls), 1)
        grant = adapter.calls[0]
        self.assertEqual(
            grant.reference,
            SecretReference("secret://workspace-secrets/keys/key-a"),
        )
        self.assertEqual(grant.version_id, "version-a")
        self.assertEqual(grant.version_number, 1)
        self.assertEqual(adapter.versions["version-a"], "revoked")
        self.assertEqual(adapter.versions["version-b"], "active")
        self.assertTrue(result.receipt.matches(grant))
        self.assertIs(
            self.old_key().status,
            RegisteredDelegationSigningKeyStatus.REVOKED,
        )
        with self.unit_of_work() as unit_of_work:
            active = unit_of_work.stores.delegation_signing_keys.require_active(
                "workspace-a",
                result.rotation.purpose,
                result.rotation.issuer,
            )
            read = GatewayKeyRotationService(
                self.unit_of_work,
                clock=lambda: 6_000,
            ).read(self.rotation_id)
            unit_of_work.commit()
        self.assertEqual(active.key_id, "key-b")
        self.assertNotIn("secret", repr(read).lower())

        replay = self.completion_program(adapter).progress(self.completion_command())
        self.assertIs(
            replay.outcome,
            GatewayKeyRotationCompletionOutcome.COMPLETED_REPLAY,
        )
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(adapter.mutations, 1)

    def test_definite_failure_keeps_exact_prepared_action_retryable(self) -> None:
        adapter = ReplaySafeRevocationAdapter(
            GatewayKeyRotationRevocationEffectResult(
                GatewayKeyRotationRevocationEffectOutcome.DEFINITE_FAILURE,
                failure_code="provider-denied-before-mutation",
            )
        )

        failed = self.completion_program(adapter).progress(self.completion_command())

        self.assertIs(failed.outcome, GatewayKeyRotationCompletionOutcome.RETRYABLE)
        self.assertIs(
            failed.rotation.status,
            GatewayKeyRotationStatus.REVOCATION_PREPARED,
        )
        self.assertIs(
            self.old_key().status,
            RegisteredDelegationSigningKeyStatus.RETIRED,
        )
        recovered = self.completion_program(adapter).progress(
            self.completion_command()
        )
        self.assertIs(
            recovered.outcome,
            GatewayKeyRotationCompletionOutcome.COMPLETED,
        )
        self.assertEqual(adapter.calls[0], adapter.calls[1])
        self.assertEqual(adapter.mutations, 1)

    def test_uncertain_and_mismatched_receipts_block_without_public_revoke(self) -> None:
        cases = (
            GatewayKeyRotationRevocationEffectResult(
                GatewayKeyRotationRevocationEffectOutcome.UNCERTAIN,
                failure_code="provider-outcome-uncertain",
            ),
            GatewayKeyRotationRevocationEffectResult(
                GatewayKeyRotationRevocationEffectOutcome.REVOKED,
                receipt=SecretVersionRevocationReceipt(
                    revocation_id="srevoke_" + "f" * 64,
                    provider_registration_id="sprov_" + "f" * 64,
                    reference=SecretReference(
                        "secret://workspace-secrets/keys/key-a"
                    ),
                    version_id="version-a",
                    version_number=1,
                ),
            ),
        )
        for effect in cases:
            with self.subTest(outcome=effect.outcome):
                self.reset_truth()
                adapter = ReplaySafeRevocationAdapter(effect)
                result = self.completion_program(adapter).progress(
                    self.completion_command()
                )
                self.assertIs(
                    result.outcome,
                    GatewayKeyRotationCompletionOutcome.BLOCKED,
                )
                self.assertIs(
                    self.old_key().status,
                    RegisteredDelegationSigningKeyStatus.RETIRED,
                )
                self.assertIsNone(result.rotation.old_secret_revoked_at)

    def test_invalid_metadata_lineage_and_scopes_fail_before_retirement_or_io(
        self,
    ) -> None:
        cases = []
        self.connection.execute(
            "UPDATE cpk_secret_references SET metadata='{}'::jsonb "
            "WHERE workspace_id='workspace-a'"
        )
        cases.append(GatewayKeyRotationCompletionConflict)
        adapter = ReplaySafeRevocationAdapter()
        with self.assertRaises(cases[-1]):
            self.completion_program(adapter).progress(self.completion_command())
        self.assertIs(
            self.old_key().status,
            RegisteredDelegationSigningKeyStatus.VERIFY_ONLY,
        )
        self.assertEqual(adapter.calls, [])

        self.reset_truth()
        adapter = ReplaySafeRevocationAdapter()
        with self.assertRaises(GatewayKeyRotationCompletionConflict):
            self.completion_program(adapter).progress(
                self.completion_command(
                    expected_version=self.retirement_ready_version + 1
                )
            )
        self.assertEqual(adapter.calls, [])

        self.reset_truth()
        adapter = ReplaySafeRevocationAdapter()
        with self.assertRaises(GatewayKeyRotationCompletionAuthorizationDenied):
            self.completion_program(adapter).progress(
                self.completion_command(
                    scopes=(PolicyScope.DELEGATION_KEY_ROTATE,)
                )
            )
        self.assertEqual(adapter.calls, [])

    def test_post_commit_process_loss_recovers_without_duplicate_provider_mutation(
        self,
    ) -> None:
        for crash_after_commit in range(1, 14):
            with self.subTest(crash_after_commit=crash_after_commit):
                self.reset_truth()
                adapter = ReplaySafeRevocationAdapter()
                control = CrashControl(crash_after_commit)
                try:
                    self.completion_program(
                        adapter,
                        unit_of_work_factory=lambda: CrashAfterCommitUnitOfWork(
                            self.unit_of_work(),
                            control,
                        ),
                    ).progress(self.completion_command())
                except SimulatedProcessLoss:
                    recovered = self.completion_program(adapter).progress(
                        self.completion_command()
                    )
                    self.assertIn(
                        recovered.outcome,
                        {
                            GatewayKeyRotationCompletionOutcome.COMPLETED,
                            GatewayKeyRotationCompletionOutcome.COMPLETED_REPLAY,
                        },
                    )
                else:
                    break
                self.assertEqual(adapter.mutations, 1)
                self.assertIs(
                    self.rotation().status,
                    GatewayKeyRotationStatus.COMPLETED,
                )
        self.assertGreater(control.commits, 5)

    def test_process_loss_after_provider_success_replays_exact_correlation(self) -> None:
        class CrashAfterMutation(ReplaySafeRevocationAdapter):
            def __init__(self) -> None:
                super().__init__()
                self.crashed = False

            def revoke_version(self, grant):
                result = super().revoke_version(grant)
                if not self.crashed:
                    self.crashed = True
                    raise SimulatedProcessLoss(
                        "process lost after provider success before fold"
                    )
                return result

        adapter = CrashAfterMutation()
        with self.assertRaises(SimulatedProcessLoss):
            self.completion_program(adapter).progress(self.completion_command())

        recovered = self.completion_program(adapter).progress(
            self.completion_command()
        )

        self.assertIs(
            recovered.outcome,
            GatewayKeyRotationCompletionOutcome.COMPLETED,
        )
        self.assertEqual(adapter.mutations, 1)
        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual(adapter.calls[0], adapter.calls[1])


if __name__ == "__main__":
    unittest.main()
