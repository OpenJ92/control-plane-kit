from __future__ import annotations

import itertools
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

import psycopg

from control_plane_kit_core.delegation_keys import (
    DelegationKeyAlgorithm,
    DelegationKeyPurpose,
    DelegationPublicKey,
)
from control_plane_kit_core.identity import (
    AuthenticatedPrincipal,
    PrincipalIdentity,
    PrincipalKind,
    WorkspaceGrant,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference,
    SecretProviderId,
    SecretReference,
    SecretUseIntent,
)
from control_plane_kit_operations.delegation_key_generation import (
    DelegationKeyGenerationEvidence,
)
from control_plane_kit_operations.coordinator import ExecutionCoordinator
from control_plane_kit_operations.gateway_key_rotation_application import (
    AdvanceGatewayKeyRotationProgram,
    DecideGatewayKeyRotationProgram,
    GatewayKeyRotationApplicationConflict,
    GatewayKeyRotationApplicationError,
    GatewayKeyRotationApplicationService,
    GatewayKeyRotationProgramView,
    RequestGatewayKeyRotationProgram,
    RequestGatewayKeyRotationProgramApproval,
)
from control_plane_kit_operations.gateway_key_rotation_application_program import (
    GatewayKeyRotationProgramExecutor,
)
from control_plane_kit_operations.gateway_key_rotation_program import (
    GatewayKeyGenerationResult,
)
from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotationStatus,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.lifecycle import RunLifecycleCommandService
from control_plane_kit_operations.records import ApprovalDecisionKind, WorkspaceRecord
from control_plane_kit_operations.secret_providers import (
    RegisterSecretProviderCommand,
    SecretProviderKind,
    SecretProviderRegistrationService,
)
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    OperationCommandService,
    StartOperationSession,
)


class RecordingPhaseExecutor:
    def __init__(self) -> None:
        self.calls = []

    def advance(
        self,
        rotation,
        *,
        expected_version,
        actor_id,
        idempotency_key,
    ):
        self.calls.append(
            (rotation, expected_version, actor_id, idempotency_key)
        )
        return GatewayKeyRotationProgramView(
            rotation=GatewayKeyRotationApplicationService._view(rotation),
            phase="generation",
            outcome="prepared",
        )


class GeneratedKeyAdapter:
    def __init__(self) -> None:
        self.calls = []

    def generate(self, grant):
        self.calls.append(grant)
        return generated_key_result(grant)


def generated_key_result(grant):
    return GatewayKeyGenerationResult.generated(
        DelegationKeyGenerationEvidence(
            workspace_id=grant.workspace_id,
            reference=grant.reference,
            purpose=grant.purpose,
            issuer=grant.issuer,
            correlation_id=grant.correlation_id,
            version_id="version-b",
            version_number=1,
            public_key=DelegationPublicKey(
                "gateway-key-b",
                DelegationKeyAlgorithm.ED25519,
                """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb=
-----END PUBLIC KEY-----
""",
            ),
            replayed=False,
        )
    )


class SequencedKeyAdapter:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def generate(self, grant):
        self.calls.append(grant)
        result = self.results.pop(0)
        return generated_key_result(grant) if result == "generated" else result


class BlockingGeneratedKeyAdapter:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = []

    def generate(self, grant):
        self.calls.append(grant)
        self.entered.set()
        if not self.release.wait(timeout=10):
            raise AssertionError("generation test did not release provider effect")
        return generated_key_result(grant)


class UnusedActivityAdapter:
    def execute(self, context):
        raise AssertionError("generation phase must not dispatch runtime activity")


class GatewayKeyRotationApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError("run through control-plane-kit-operations/test.sh")
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.ids = itertools.count(1)
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord("workspace-a", "Workspace A")
            )
            unit_of_work.stores.workspaces.create(
                WorkspaceRecord("workspace-b", "Workspace B")
            )
            unit_of_work.commit()
        session = OperationCommandService(
            self.unit_of_work,
            clock=lambda: "2026-08-04T00:00:00Z",
            id_factory=self.id_factory,
        ).execute(
            StartOperationSession(
                "workspace-a",
                "operator-a",
                "Rotate gateway key",
                IdempotencyKey("start-rotation-session"),
            )
        )
        self.session_id = session.session.session_id
        SecretProviderRegistrationService(self.unit_of_work).register_provider(
            RegisterSecretProviderCommand(
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
                admitted_at="2026-08-04T00:00:00Z",
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,),
            )
        )
        self.phases = RecordingPhaseExecutor()
        self.application = GatewayKeyRotationApplicationService(
            self.unit_of_work,
            clock=lambda: "2026-08-04T00:00:01Z",
            trusted_epoch_clock=lambda: 1_000,
            id_factory=self.id_factory,
            phase_executor=self.phases,
        )

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def id_factory(self) -> str:
        return f"generated-{next(self.ids)}"

    @staticmethod
    def context(*scopes: PolicyScope, workspace_id: str = "workspace-a"):
        principal = AuthenticatedPrincipal(
            PrincipalIdentity("test-issuer", "operator-a", PrincipalKind.OPERATOR),
            (WorkspaceGrant(workspace_id, scopes),),
        )
        return principal.command_context(workspace_id)

    @staticmethod
    def request_command() -> RequestGatewayKeyRotationProgram:
        return RequestGatewayKeyRotationProgram(
            workspace_id="workspace-a",
            gateway_node_id="gateway-a",
            purpose=DelegationKeyPurpose.GATEWAY_PROBE,
            issuer="cpk-server",
            old_key_id="gateway-key-a",
            new_secret_reference=SecretReference(
                "secret://workspace-secrets/keys/gateway-key-b"
            ),
            key_generation_correlation="generate-gateway-key-b",
            maximum_grant_lifetime_seconds=60,
            clock_skew_seconds=5,
            idempotency_key="request-rotation-a",
            requested_at="2026-08-04T00:00:02Z",
        )

    def requested(self):
        return self.application.request(
            self.request_command(),
            self.context(PolicyScope.DELEGATION_KEY_ROTATE),
        )

    def approved(self):
        requested = self.requested()
        approval = self.application.request_approval(
            RequestGatewayKeyRotationProgramApproval(
                "workspace-a",
                self.session_id,
                requested.rotation_id,
                "request-approval-for-generation",
            ),
            self.context(PolicyScope.DELEGATION_KEY_ROTATE),
        )
        return self.application.decide(
            DecideGatewayKeyRotationProgram(
                "workspace-a",
                self.session_id,
                requested.rotation_id,
                approval.approval_request_id,
                ApprovalDecisionKind.APPROVED,
                "approve-generation",
            ),
            self.context(PolicyScope.DELEGATION_KEY_ROTATE_APPROVE),
        ).rotation

    def application_with(self, generation_adapter):
        executor = GatewayKeyRotationProgramExecutor(
            self.unit_of_work,
            generation_adapter=generation_adapter,
            revocation_adapter=object(),
            coordinator=ExecutionCoordinator(
                self.unit_of_work,
                lifecycle=RunLifecycleCommandService(
                    self.unit_of_work,
                    clock=lambda: "2026-08-04T00:01:00Z",
                    id_factory=self.id_factory,
                ),
                adapter=UnusedActivityAdapter(),
                clock=lambda: "2026-08-04T00:01:00Z",
                id_factory=self.id_factory,
            ),
            clock=lambda: "2026-08-04T00:01:00Z",
            trusted_epoch_clock=lambda: 1_000,
            lease_expiry_clock=lambda: "2026-08-04T00:06:00Z",
            id_factory=self.id_factory,
        )
        return GatewayKeyRotationApplicationService(
            self.unit_of_work,
            clock=lambda: "2026-08-04T00:01:00Z",
            trusted_epoch_clock=lambda: 1_000,
            id_factory=self.id_factory,
            phase_executor=executor,
        )

    def test_public_approval_request_and_decision_are_atomic_and_replay(self) -> None:
        requested = self.requested()
        request_command = RequestGatewayKeyRotationProgramApproval(
            workspace_id="workspace-a",
            session_id=self.session_id,
            rotation_id=requested.rotation_id,
            idempotency_key="request-rotation-approval",
        )
        approval = self.application.request_approval(
            request_command,
            self.context(PolicyScope.DELEGATION_KEY_ROTATE),
        )
        replay = self.application.request_approval(
            request_command,
            self.context(PolicyScope.DELEGATION_KEY_ROTATE),
        )
        self.assertEqual(approval.rotation.status, GatewayKeyRotationStatus.AWAITING_APPROVAL)
        self.assertEqual(replay.rotation, approval.rotation)
        self.assertTrue(replay.replayed)

        decision_command = DecideGatewayKeyRotationProgram(
            workspace_id="workspace-a",
            session_id=self.session_id,
            rotation_id=requested.rotation_id,
            approval_request_id=approval.approval_request_id,
            decision=ApprovalDecisionKind.APPROVED,
            idempotency_key="approve-rotation",
        )
        decision = self.application.decide(
            decision_command,
            self.context(PolicyScope.DELEGATION_KEY_ROTATE_APPROVE),
        )
        decision_replay = self.application.decide(
            decision_command,
            self.context(PolicyScope.DELEGATION_KEY_ROTATE_APPROVE),
        )
        self.assertEqual(decision.rotation.status, GatewayKeyRotationStatus.APPROVED)
        self.assertEqual(decision_replay.rotation, decision.rotation)
        self.assertTrue(decision_replay.replayed)

        rows = self.connection.execute(
            "SELECT from_status,to_status FROM cpk_gateway_key_rotation_transitions "
            "WHERE rotation_id=%s ORDER BY to_version",
            (requested.rotation_id,),
        ).fetchall()
        self.assertEqual(
            rows,
            [("requested", "awaiting-approval"), ("awaiting-approval", "approved")],
        )

    def test_scope_and_workspace_are_derived_from_trusted_context(self) -> None:
        with self.assertRaises(GatewayKeyRotationApplicationError):
            self.application.request(
                self.request_command(),
                self.context(PolicyScope.DELEGATION_KEY_READ),
            )
        requested = self.requested()
        self.assertEqual(
            self.application.detail(
                "workspace-a",
                requested.rotation_id,
                self.context(PolicyScope.DELEGATION_KEY_READ),
            ),
            requested,
        )
        self.assertEqual(
            self.application.list(
                "workspace-a",
                self.context(PolicyScope.DELEGATION_KEY_READ),
            ),
            (requested,),
        )
        with self.assertRaises(GatewayKeyRotationApplicationError):
            self.application.list(
                "workspace-a",
                self.context(PolicyScope.DELEGATION_KEY_ROTATE),
            )

    def test_advance_delegates_one_bounded_phase_without_public_internal_scopes(self) -> None:
        requested = self.requested()
        result = self.application.advance(
            AdvanceGatewayKeyRotationProgram(
                workspace_id="workspace-a",
                rotation_id=requested.rotation_id,
                expected_version=requested.version,
                idempotency_key="advance-rotation-a",
            ),
            self.context(PolicyScope.DELEGATION_KEY_ROTATE),
        )
        self.assertEqual(result.phase, "generation")
        self.assertEqual(len(self.phases.calls), 1)
        durable, version, actor, idempotency_key = self.phases.calls[0]
        self.assertEqual(durable.rotation_id, requested.rotation_id)
        self.assertEqual(version, requested.version)
        self.assertEqual(actor, "operator-a")
        self.assertEqual(idempotency_key, "advance-rotation-a")
        self.assertNotIn("private", repr(result).lower())

    def test_completed_public_advance_replays_receipt_without_provider_io(self) -> None:
        requested = self.requested()
        approval = self.application.request_approval(
            RequestGatewayKeyRotationProgramApproval(
                "workspace-a",
                self.session_id,
                requested.rotation_id,
                "request-approval-for-generation",
            ),
            self.context(PolicyScope.DELEGATION_KEY_ROTATE),
        )
        approved = self.application.decide(
            DecideGatewayKeyRotationProgram(
                "workspace-a",
                self.session_id,
                requested.rotation_id,
                approval.approval_request_id,
                ApprovalDecisionKind.APPROVED,
                "approve-generation",
            ),
            self.context(PolicyScope.DELEGATION_KEY_ROTATE_APPROVE),
        )
        generator = GeneratedKeyAdapter()
        executor = GatewayKeyRotationProgramExecutor(
            self.unit_of_work,
            generation_adapter=generator,
            revocation_adapter=object(),
            coordinator=ExecutionCoordinator(
                self.unit_of_work,
                lifecycle=RunLifecycleCommandService(
                    self.unit_of_work,
                    clock=lambda: "2026-08-04T00:01:00Z",
                    id_factory=self.id_factory,
                ),
                adapter=UnusedActivityAdapter(),
                clock=lambda: "2026-08-04T00:01:00Z",
                id_factory=self.id_factory,
            ),
            clock=lambda: "2026-08-04T00:01:00Z",
            trusted_epoch_clock=lambda: 1_000,
            lease_expiry_clock=lambda: "2026-08-04T00:06:00Z",
            id_factory=self.id_factory,
        )
        application = GatewayKeyRotationApplicationService(
            self.unit_of_work,
            clock=lambda: "2026-08-04T00:01:00Z",
            trusted_epoch_clock=lambda: 1_000,
            id_factory=self.id_factory,
            phase_executor=executor,
        )
        command = AdvanceGatewayKeyRotationProgram(
            "workspace-a",
            requested.rotation_id,
            approved.rotation.version,
            "generate-key-b",
        )

        first = application.advance(
            command,
            self.context(PolicyScope.DELEGATION_KEY_ROTATE),
        )
        replay = application.advance(
            command,
            self.context(PolicyScope.DELEGATION_KEY_ROTATE),
        )

        self.assertEqual(first.outcome, "generated")
        self.assertFalse(first.replayed)
        self.assertEqual(replay.rotation, first.rotation)
        self.assertTrue(replay.replayed)
        self.assertEqual(len(generator.calls), 1)

    def test_definite_generation_failure_resumes_exact_action_after_restart(self) -> None:
        approved = self.approved()
        failed_adapter = SequencedKeyAdapter(
            GatewayKeyGenerationResult.definite_failure("provider-unavailable")
        )
        first_application = self.application_with(failed_adapter)
        first_command = AdvanceGatewayKeyRotationProgram(
            "workspace-a",
            approved.rotation_id,
            approved.version,
            "generate-key-b-first-attempt",
        )

        failed = first_application.advance(
            first_command,
            self.context(PolicyScope.DELEGATION_KEY_ROTATE),
        )
        replay = first_application.advance(
            first_command,
            self.context(PolicyScope.DELEGATION_KEY_ROTATE),
        )

        self.assertEqual(failed.outcome, "definite-failure")
        self.assertEqual(failed.failure_code, "provider-unavailable")
        self.assertNotIn("secret", str(failed.descriptor()).lower())
        with self.assertRaises(ValueError):
            GatewayKeyRotationProgramView(
                failed.rotation,
                "generation",
                "definite-failure",
                failure_code="provider-token\nleak",
            )
        self.assertEqual(
            failed.rotation.status,
            GatewayKeyRotationStatus.GENERATION_PREPARED,
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(len(failed_adapter.calls), 1)

        resumed_adapter = SequencedKeyAdapter("generated")
        restarted_application = self.application_with(resumed_adapter)
        generated = restarted_application.advance(
            AdvanceGatewayKeyRotationProgram(
                "workspace-a",
                approved.rotation_id,
                failed.rotation.version,
                "generate-key-b-retry",
            ),
            self.context(PolicyScope.DELEGATION_KEY_ROTATE),
        )

        self.assertEqual(generated.outcome, "generated")
        self.assertEqual(
            generated.rotation.status,
            GatewayKeyRotationStatus.KEY_GENERATED,
        )
        self.assertEqual(len(resumed_adapter.calls), 1)
        self.assertEqual(resumed_adapter.calls[0], failed_adapter.calls[0])
        transitions = self.connection.execute(
            "SELECT from_status,to_status FROM cpk_gateway_key_rotation_transitions "
            "WHERE rotation_id=%s ORDER BY to_version",
            (approved.rotation_id,),
        ).fetchall()
        self.assertEqual(
            transitions.count(("approved", "generation-prepared")),
            1,
        )
        self.assertEqual(
            transitions.count(("generation-prepared", "key-generated")),
            1,
        )

    def test_uncertain_generation_blocks_public_retry(self) -> None:
        approved = self.approved()
        adapter = SequencedKeyAdapter(
            GatewayKeyGenerationResult.uncertain("provider-response-uncertain")
        )
        application = self.application_with(adapter)
        blocked = application.advance(
            AdvanceGatewayKeyRotationProgram(
                "workspace-a",
                approved.rotation_id,
                approved.version,
                "generate-key-b-uncertain",
            ),
            self.context(PolicyScope.DELEGATION_KEY_ROTATE),
        )

        self.assertEqual(blocked.outcome, "uncertain")
        self.assertEqual(blocked.failure_code, "provider-response-uncertain")
        self.assertEqual(blocked.rotation.status, GatewayKeyRotationStatus.BLOCKED)
        with self.assertRaises(GatewayKeyRotationApplicationConflict):
            application.advance(
                AdvanceGatewayKeyRotationProgram(
                    "workspace-a",
                    approved.rotation_id,
                    blocked.rotation.version,
                    "do-not-retry-uncertain-generation",
                ),
                self.context(PolicyScope.DELEGATION_KEY_ROTATE),
            )
        self.assertEqual(len(adapter.calls), 1)

    def test_concurrent_public_generation_does_not_duplicate_provider_effect(self) -> None:
        approved = self.approved()
        adapter = BlockingGeneratedKeyAdapter()
        first_application = self.application_with(adapter)
        competing_application = self.application_with(adapter)
        context = self.context(PolicyScope.DELEGATION_KEY_ROTATE)

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                first_application.advance,
                AdvanceGatewayKeyRotationProgram(
                    "workspace-a",
                    approved.rotation_id,
                    approved.version,
                    "generate-key-b-owner",
                ),
                context,
            )
            self.assertTrue(adapter.entered.wait(timeout=10))
            prepared = competing_application.detail(
                "workspace-a",
                approved.rotation_id,
                self.context(PolicyScope.DELEGATION_KEY_READ),
            )
            with self.assertRaises(GatewayKeyRotationApplicationConflict):
                competing_application.advance(
                    AdvanceGatewayKeyRotationProgram(
                        "workspace-a",
                        approved.rotation_id,
                        prepared.version,
                        "generate-key-b-competing",
                    ),
                    context,
                )
            adapter.release.set()
            generated = first.result(timeout=10)

        self.assertEqual(generated.outcome, "generated")
        self.assertEqual(len(adapter.calls), 1)


if __name__ == "__main__":
    unittest.main()
