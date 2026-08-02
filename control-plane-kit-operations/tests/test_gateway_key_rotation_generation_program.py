from __future__ import annotations

from dataclasses import replace
import itertools
import os
import unittest

import psycopg

from control_plane_kit_core.delegation_keys import (
    DelegationKeyAlgorithm,
    DelegationKeyPurpose,
    DelegationPublicKey,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference,
    SecretProviderId,
    SecretReference,
    SecretUseIntent,
)
from control_plane_kit_operations.approvals import (
    ApprovalCommandService,
    DecideApproval,
    RequestGatewayKeyRotationApproval,
)
from control_plane_kit_operations.delegation_key_generation import (
    AdmitGeneratedDelegationSigningKey,
    DelegationKeyGenerationEvidence,
    DelegationKeyGenerationService,
)
from control_plane_kit_operations.gateway_key_rotation_program import (
    GatewayKeyGenerationOutcome,
    GatewayKeyGenerationResult,
    GatewayKeyRotationGenerationProgram,
    GatewayKeyRotationGenerationProgramConflict,
    PrepareGatewayKeyRotationGeneration,
    SubmitGatewayKeyRotationGeneration,
)
from control_plane_kit_operations.gateway_key_rotations import (
    AdvanceGatewayKeyRotation,
    GatewayKeyRotationService,
    GatewayKeyRotationStatus,
    RequestGatewayKeyRotation,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import ApprovalDecisionKind, WorkspaceRecord
from control_plane_kit_operations.secret_providers import (
    RegisterSecretProviderCommand,
    RevokeSecretProviderCommand,
    SecretProviderKind,
    SecretProviderRegistrationService,
)
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    OperationCommandService,
    StartOperationSession,
)


PUBLIC_KEY_B = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb=
-----END PUBLIC KEY-----
"""


class GatewayKeyRotationGenerationProgramTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; run the operations test script"
            )
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self._ids = itertools.count(1)
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord("workspace-a", "Workspace A")
            )
            unit_of_work.commit()
        self.provider = SecretProviderRegistrationService(
            self.unit_of_work
        ).register_provider(self.provider_command())
        OperationCommandService(
            self.unit_of_work,
            clock=lambda: "2026-08-02T00:00:00Z",
            id_factory=self.id_factory,
        ).execute(
            StartOperationSession(
                "workspace-a",
                "operator-a",
                "Rotate gateway key",
                IdempotencyKey("start-rotation-session"),
            )
        )
        self.rotations = GatewayKeyRotationService(
            self.unit_of_work,
            clock=lambda: 1_000,
        )
        requested = self.rotations.request(self.rotation_command())
        approvals = ApprovalCommandService(
            self.unit_of_work,
            clock=lambda: "2026-08-02T00:00:02Z",
            id_factory=self.id_factory,
        )
        approval = approvals.execute(
            RequestGatewayKeyRotationApproval(
                session_id="generated-1",
                rotation_id=requested.rotation_id,
                actor_id="operator-a",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                idempotency_key=IdempotencyKey("request-rotation-approval"),
            )
        )
        awaiting = self.rotations.advance(
            AdvanceGatewayKeyRotation(
                rotation_id=requested.rotation_id,
                transition_id="request-approval",
                expected_status=requested.status,
                expected_version=requested.version,
                target_status=GatewayKeyRotationStatus.AWAITING_APPROVAL,
                advanced_by="operator-a",
                advanced_at="2026-08-02T00:00:03Z",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                approval_request_id=approval.request.request_id,
            )
        )
        decision = approvals.execute(
            DecideApproval(
                session_id="generated-1",
                request_id=approval.request.request_id,
                actor_id="manager-a",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE_APPROVE,),
                decision=ApprovalDecisionKind.APPROVED,
                idempotency_key=IdempotencyKey("approve-rotation"),
            )
        )
        self.approved = self.rotations.advance(
            AdvanceGatewayKeyRotation(
                rotation_id=requested.rotation_id,
                transition_id="approve-rotation",
                expected_status=awaiting.status,
                expected_version=awaiting.version,
                target_status=GatewayKeyRotationStatus.APPROVED,
                advanced_by="operator-a",
                advanced_at="2026-08-02T00:00:04Z",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                approval_decision_id=decision.decision.decision_id,
            )
        )

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def id_factory(self) -> str:
        return f"generated-{next(self._ids)}"

    def program(self) -> GatewayKeyRotationGenerationProgram:
        return GatewayKeyRotationGenerationProgram(
            self.unit_of_work,
            clock=lambda: 1_000,
        )

    def prepare_command(self) -> PrepareGatewayKeyRotationGeneration:
        return PrepareGatewayKeyRotationGeneration(
            rotation_id=self.approved.rotation_id,
            expected_version=self.approved.version,
            actor_subject="operator-a",
            prepared_by="operator-a",
            prepared_at="2026-08-02T00:00:05Z",
            actor_scopes=(
                PolicyScope.DELEGATION_KEY_ROTATE,
                PolicyScope.DELEGATION_KEY_GENERATE,
            ),
        )

    def test_prepare_persists_exact_action_before_provider_io_and_restarts(self) -> None:
        command = self.prepare_command()
        action = self.program().prepare(command)
        restarted = self.program().prepare(
            replace(command, prepared_at="2026-08-02T12:34:56Z")
        )
        rotation = self.rotations.get(self.approved.rotation_id)

        self.assertEqual(action, restarted)
        self.assertEqual(rotation.status, GatewayKeyRotationStatus.GENERATION_PREPARED)
        self.assertEqual(
            rotation.generation_provider_registration_id,
            self.provider.registration_id,
        )
        self.assertEqual(rotation.generation_action_digest, action.action_digest)
        self.assertEqual(action.grant.reference, self.approved.new_secret_reference)
        self.assertEqual(
            action.grant.correlation_id,
            self.approved.key_generation_correlation,
        )
        self.assertNotIn("private", repr(action).lower())

    def test_success_folds_key_and_advances_rotation_exactly_once(self) -> None:
        action = self.program().prepare(self.prepare_command())
        result = GatewayKeyGenerationResult.generated(self.evidence(action))

        first = self.program().submit(self.submit_command(action, result))
        replay = self.program().submit(self.submit_command(action, result))

        self.assertEqual(first.rotation.status, GatewayKeyRotationStatus.KEY_GENERATED)
        self.assertEqual(replay.rotation, first.rotation)
        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(first.rotation.new_key_id, "gateway-key-b")
        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                len(unit_of_work.stores.secret_references.list_active("workspace-a")),
                1,
            )
            keys = unit_of_work.stores.delegation_signing_keys.list_for_verification(
                "workspace-a",
                DelegationKeyPurpose.GATEWAY_PROBE,
                "cpk-server",
            )
        self.assertEqual(tuple(key.key_id for key in keys), ("gateway-key-b",))

    def test_provider_success_before_fold_replays_same_action_after_restart(self) -> None:
        action = self.program().prepare(self.prepare_command())
        provider_evidence = self.evidence(action)

        restarted_action = self.program().prepare(self.prepare_command())
        completed = self.program().submit(
            self.submit_command(
                restarted_action,
                GatewayKeyGenerationResult.generated(
                    replace(provider_evidence, replayed=True)
                ),
            )
        )

        self.assertEqual(restarted_action, action)
        self.assertEqual(completed.rotation.status, GatewayKeyRotationStatus.KEY_GENERATED)

    def test_admission_success_before_rotation_fold_is_idempotent(self) -> None:
        action = self.program().prepare(self.prepare_command())
        evidence = self.evidence(action)
        DelegationKeyGenerationService(self.unit_of_work).admit_generated(
            AdmitGeneratedDelegationSigningKey(
                grant=action.grant,
                evidence=evidence,
                admitted_by="operator-a",
                admitted_at="2026-08-02T00:00:06Z",
                actor_scopes=(PolicyScope.DELEGATION_KEY_REGISTER,),
            )
        )

        completed = self.program().submit(
            self.submit_command(
                action,
                GatewayKeyGenerationResult.generated(
                    replace(evidence, replayed=True)
                ),
            )
        )

        self.assertEqual(completed.rotation.status, GatewayKeyRotationStatus.KEY_GENERATED)

    def test_definite_failure_retries_exact_action_but_uncertainty_blocks(self) -> None:
        action = self.program().prepare(self.prepare_command())
        definite = self.program().submit(
            self.submit_command(
                action,
                GatewayKeyGenerationResult.definite_failure("provider-unavailable"),
            )
        )

        self.assertEqual(definite.outcome, GatewayKeyGenerationOutcome.DEFINITE_FAILURE)
        self.assertEqual(
            definite.rotation.status,
            GatewayKeyRotationStatus.GENERATION_PREPARED,
        )
        self.assertEqual(definite.next_action, action)

        uncertain = self.program().submit(
            self.submit_command(
                action,
                GatewayKeyGenerationResult.uncertain("provider-response-uncertain"),
            )
        )
        self.assertEqual(uncertain.rotation.status, GatewayKeyRotationStatus.BLOCKED)
        self.assertIsNone(uncertain.next_action)

        blocked_replay = self.program().submit(
            self.submit_command(
                action,
                GatewayKeyGenerationResult.uncertain(
                    "provider-response-uncertain"
                ),
            )
        )
        self.assertEqual(blocked_replay.rotation, uncertain.rotation)
        with self.assertRaises(GatewayKeyRotationGenerationProgramConflict):
            self.program().submit(
                self.submit_command(
                    action,
                    GatewayKeyGenerationResult.uncertain(
                        "different-uncertain-effect"
                    ),
                )
            )

    def test_replay_rejects_changed_action_or_provider_evidence(self) -> None:
        action = self.program().prepare(self.prepare_command())
        result = GatewayKeyGenerationResult.generated(self.evidence(action))
        self.program().submit(self.submit_command(action, result))

        changed_lineage = replace(
            action,
            expected_rotation_version=action.expected_rotation_version + 1,
            prepared_rotation_version=action.prepared_rotation_version + 1,
        )
        with self.assertRaises(GatewayKeyRotationGenerationProgramConflict):
            self.program().submit(self.submit_command(changed_lineage, result))

        changed_evidence = replace(
            result.evidence,
            reference=SecretReference(
                "secret://workspace-secrets/keys/different-key"
            ),
        )
        with self.assertRaises(GatewayKeyRotationGenerationProgramConflict):
            self.program().submit(
                self.submit_command(
                    action,
                    GatewayKeyGenerationResult.generated(changed_evidence),
                )
            )

    def test_prepare_rejects_stale_unapproved_and_inactive_provider_truth(self) -> None:
        with self.assertRaises(GatewayKeyRotationGenerationProgramConflict):
            self.program().prepare(
                replace(
                    self.prepare_command(),
                    expected_version=self.approved.version + 1,
                )
            )

        fresh = self.rotations.request(
            replace(
                self.rotation_command(),
                gateway_node_id="gateway-b",
                correlation_id="rotation-b",
                key_generation_correlation="generate-b",
            )
        )
        with self.assertRaises(GatewayKeyRotationGenerationProgramConflict):
            self.program().prepare(
                replace(
                    self.prepare_command(),
                    rotation_id=fresh.rotation_id,
                    expected_version=fresh.version,
                )
            )

        SecretProviderRegistrationService(self.unit_of_work).revoke_provider(
            RevokeSecretProviderCommand(
                workspace_id="workspace-a",
                provider_id=SecretProviderId("workspace-secrets"),
                revoked_by="operator-a",
                revoked_at="2026-08-02T00:00:06Z",
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REVOKE,),
            )
        )
        with self.assertRaises(GatewayKeyRotationGenerationProgramConflict):
            self.program().prepare(self.prepare_command())

    def submit_command(
        self,
        action,
        result: GatewayKeyGenerationResult,
    ) -> SubmitGatewayKeyRotationGeneration:
        return SubmitGatewayKeyRotationGeneration(
            action=action,
            result=result,
            submitted_by="operator-a",
            submitted_at="2026-08-02T00:00:06Z",
            actor_scopes=(
                PolicyScope.DELEGATION_KEY_ROTATE,
                PolicyScope.DELEGATION_KEY_REGISTER,
            ),
        )

    def evidence(self, action) -> DelegationKeyGenerationEvidence:
        return DelegationKeyGenerationEvidence(
            workspace_id="workspace-a",
            reference=action.grant.reference,
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="cpk-server",
            correlation_id=action.grant.correlation_id,
            version_id="version-b",
            version_number=1,
            public_key=DelegationPublicKey(
                key_id="gateway-key-b",
                algorithm=DelegationKeyAlgorithm.ED25519,
                public_key_pem=PUBLIC_KEY_B,
            ),
            replayed=False,
        )

    def provider_command(self) -> RegisterSecretProviderCommand:
        return RegisterSecretProviderCommand(
            workspace_id="workspace-a",
            provider_id=SecretProviderId("workspace-secrets"),
            provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS,
            display_name="Workspace secrets",
            endpoint_reference=SecretProviderEndpointReference("secrets-endpoint"),
            credential_reference=SecretReference(
                "secret://workspace-secrets/provider-token"
            ),
            allowed_reference_prefixes=(
                SecretReference("secret://workspace-secrets/keys"),
            ),
            allowed_intents=(SecretUseIntent.GATEWAY_PROBE_SIGNING_KEY,),
            admitted_by="operator-a",
            admitted_at="2026-08-02T00:00:00Z",
            actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,),
        )

    def rotation_command(self) -> RequestGatewayKeyRotation:
        return RequestGatewayKeyRotation(
            workspace_id="workspace-a",
            gateway_node_id="gateway-a",
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="cpk-server",
            old_key_id="gateway-key-a",
            new_secret_reference=SecretReference(
                "secret://workspace-secrets/keys/gateway-key-b"
            ),
            key_generation_correlation="generate-gateway-key-b",
            maximum_grant_lifetime_seconds=120,
            clock_skew_seconds=10,
            correlation_id="rotation-a",
            requested_by="operator-a",
            requested_at="2026-08-02T00:00:01Z",
            actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
        )


if __name__ == "__main__":
    unittest.main()
