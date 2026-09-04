from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
from threading import Barrier
import unittest

import psycopg

from gateway_rotation_overlap_fixture import (
    CrashAfterCommitUnitOfWork,
    CrashControl,
    GatewayRotationOverlapFixture,
    SimulatedProcessLoss,
)
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference,
    SecretProviderId,
    SecretReference,
    SecretUseIntent,
)
from control_plane_kit_operations.delegation_signing_keys import (
    DelegationSigningKeyRegistrationService,
    RegisteredDelegationSigningKeyStatus,
    RevokeDelegationSigningKeyCommand,
)
from control_plane_kit_operations.gateway_key_rotation_activation import (
    GatewayKeyRotationActivationAuthorizationDenied,
    GatewayKeyRotationActivationConflict,
    GatewayKeyRotationActivationOutcome,
    GatewayKeyRotationActivationProgram,
    ProgressGatewayKeyRotationActivation,
)
from control_plane_kit_operations.gateway_key_rotation_overlap_program import (
    GatewayKeyRotationOverlapPreparationProgram,
    PrepareGatewayKeyRotationOverlap,
)
from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotationService,
    GatewayKeyRotationStatus,
)
from control_plane_kit_operations.lifecycle import (
    ExecutionLeaseDuration,
    ExecutionWorkerAuthority,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.secret_providers import (
    RegisterSecretProviderCommand,
    RegisterSecretReferenceCommand,
    RevokeSecretReferenceCommand,
    SecretProviderKind,
    SecretProviderRegistrationService,
)


class GatewayKeyRotationActivationTests(
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
        self.epoch = 1_000
        self.reset_truth()

    def tearDown(self) -> None:
        self.connection.close()

    def reset_truth(self) -> None:
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.seed_graph_and_keys()
        self._admit_key_references()
        self.seed_rotation_approval()
        self._accept_overlap()

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def crashing_unit_of_work(self, control: CrashControl):
        return CrashAfterCommitUnitOfWork(self.unit_of_work(), control)

    def program(self, *, unit_of_work_factory=None):
        return GatewayKeyRotationActivationProgram(
            unit_of_work_factory or self.unit_of_work,
            clock=lambda: "2026-08-02T04:00:00Z",
            trusted_epoch_clock=lambda: self.epoch,
        )

    def command(
        self,
        *,
        expected_version: int | None = None,
        scopes: tuple[PolicyScope, ...] | None = None,
    ) -> ProgressGatewayKeyRotationActivation:
        return ProgressGatewayKeyRotationActivation(
            rotation_id=self.rotation_id,
            expected_overlap_version=(
                self.overlap_version if expected_version is None else expected_version
            ),
            actor_id="operator-a",
            actor_scopes=scopes
            or (
                PolicyScope.DELEGATION_KEY_ROTATE,
                PolicyScope.DELEGATION_KEY_ACTIVATE,
            ),
        )

    def test_activates_b_and_enforces_typed_drain_barrier(self) -> None:
        result = self.program().progress(self.command())

        self.assertIs(result.outcome, GatewayKeyRotationActivationOutcome.WAITING)
        self.assertIs(
            result.rotation.status,
            GatewayKeyRotationStatus.DRAINING_OLD_GRANTS,
        )
        self.assertEqual(result.observed_at_epoch, 1_000)
        self.assertEqual(result.drain_deadline_epoch, 1_065)
        self.assertEqual(
            self._key_statuses(),
            {
                "key-a": RegisteredDelegationSigningKeyStatus.VERIFY_ONLY,
                "key-b": RegisteredDelegationSigningKeyStatus.ACTIVE,
            },
        )
        self.assertEqual(
            self._transition_targets(),
            [
                GatewayKeyRotationStatus.NEW_KEY_ACTIVE,
                GatewayKeyRotationStatus.DRAINING_OLD_GRANTS,
            ],
        )
        write_counts = self._write_counts()

        self.epoch = 1_064
        waiting = self.program().progress(self.command())
        self.assertIs(waiting.outcome, GatewayKeyRotationActivationOutcome.WAITING)
        self.assertEqual(self._write_counts(), write_counts)

        self.epoch = 1_065
        ready = self.program().progress(self.command())
        self.assertIs(
            ready.outcome,
            GatewayKeyRotationActivationOutcome.READY_FOR_RETIREMENT,
        )
        self.assertEqual(ready.drain_deadline_epoch, 1_065)
        self.assertEqual(self._write_counts(), write_counts)

    def test_restart_after_activation_and_each_fold_converges(self) -> None:
        # Snapshot read, activation, NEW_KEY_ACTIVE fold, DRAINING fold.
        for crash_after_commit in (2, 3, 4):
            with self.subTest(crash_after_commit=crash_after_commit):
                self.reset_truth()
                control = CrashControl(crash_after_commit)
                with self.assertRaises(SimulatedProcessLoss):
                    self.program(
                        unit_of_work_factory=lambda: self.crashing_unit_of_work(
                            control
                        )
                    ).progress(self.command())

                recovered = self.program().progress(self.command())
                self.assertIs(
                    recovered.outcome,
                    GatewayKeyRotationActivationOutcome.WAITING,
                )
                self.assertEqual(recovered.drain_deadline_epoch, 1_065)
                self.assertEqual(
                    self._key_statuses(),
                    {
                        "key-a": RegisteredDelegationSigningKeyStatus.VERIFY_ONLY,
                        "key-b": RegisteredDelegationSigningKeyStatus.ACTIVE,
                    },
                )
                self.assertEqual(len(self._transition_targets()), 2)

    def test_permissions_and_stale_lineage_fail_closed(self) -> None:
        with self.assertRaises(GatewayKeyRotationActivationAuthorizationDenied):
            self.program().progress(
                self.command(scopes=(PolicyScope.PLAN_EXECUTE,))
            )
        self.assertEqual(self._write_counts(), (0, 2))

        with self.assertRaises(GatewayKeyRotationActivationConflict):
            self.program().progress(
                self.command(expected_version=self.overlap_version + 1)
            )
        self.assertEqual(self._write_counts(), (0, 2))

    def test_revoked_reference_fails_before_activation(self) -> None:
        SecretProviderRegistrationService(self.unit_of_work).revoke_reference(
            RevokeSecretReferenceCommand(
                workspace_id="workspace-a",
                registration_id=self.reference_b_registration_id,
                revoked_by="operator-a",
                revoked_at="2026-08-02T03:59:00Z",
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REVOKE,),
            )
        )
        with self.assertRaises(GatewayKeyRotationActivationConflict):
            self.program().progress(self.command())
        self.assertEqual(
            self._key_statuses(),
            {
                "key-a": RegisteredDelegationSigningKeyStatus.ACTIVE,
                "key-b": RegisteredDelegationSigningKeyStatus.VERIFY_ONLY,
            },
        )

    def test_stale_key_status_fails_before_rotation_fold(self) -> None:
        DelegationSigningKeyRegistrationService(self.unit_of_work).revoke(
            RevokeDelegationSigningKeyCommand(
                workspace_id="workspace-a",
                purpose=DelegationKeyPurpose.GATEWAY_PROBE,
                issuer="cpk-server",
                key_id="key-b",
                revoked_by="operator-a",
                revoked_at="2026-08-02T03:59:00Z",
                actor_scopes=(PolicyScope.DELEGATION_KEY_REVOKE,),
            )
        )
        with self.assertRaises(GatewayKeyRotationActivationConflict):
            self.program().progress(self.command())
        self.assertEqual(self._write_counts(), (0, 2))

    def test_concurrent_progress_converges_without_extra_transitions(self) -> None:
        barrier = Barrier(2)

        def progress():
            barrier.wait()
            return self.program().progress(self.command())

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(pool.map(lambda _: progress(), range(2)))

        self.assertEqual(results[0].rotation, results[1].rotation)
        self.assertEqual(
            {result.outcome for result in results},
            {GatewayKeyRotationActivationOutcome.WAITING},
        )
        self.assertEqual(self._write_counts(), (2, 2))

    def _accept_overlap(self) -> None:
        ids = iter(f"activation-overlap-{index}" for index in range(1, 30))
        timestamps = iter(
            f"2026-08-02T02:{minute:02d}:00Z" for minute in range(30)
        )
        prepared = GatewayKeyRotationOverlapPreparationProgram(
            self.unit_of_work,
            clock=lambda: next(timestamps),
            trusted_epoch_clock=lambda: 2_000,
            id_factory=lambda: next(ids),
        ).prepare(
            PrepareGatewayKeyRotationOverlap(
                rotation_id=self.rotation_id,
                expected_rotation_version=self.rotation_version,
                expected_authored_graph_id="graph-a",
                expected_current_realized_projection_id="projection-a",
                expected_desired_realized_projection_id="projection-a",
                expected_desired_graph_revision=1,
                actor_id="operator-a",
                actor_scopes=(
                    PolicyScope.DELEGATION_KEY_ROTATE,
                    PolicyScope.PLAN_EXECUTE,
                    PolicyScope.EXECUTION_OPERATE,
                ),
                worker_authority=ExecutionWorkerAuthority(
                    "worker-a",
                    (PolicyScope.EXECUTION_OPERATE,),
                ),
                lease_duration=ExecutionLeaseDuration(1800),
            )
        )
        accepted = self.accept_prepared_overlap(
            prepared,
            prefix="activation-overlap-execution",
            accepted_at="2026-08-02T03:30:00Z",
        )
        self.overlap_version = accepted.rotation.version

    def _admit_key_references(self) -> None:
        service = SecretProviderRegistrationService(self.unit_of_work)
        provider = service.register_provider(
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
                admitted_at="2026-08-02T02:00:00Z",
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,),
            )
        )
        for key_id in ("key-a", "key-b"):
            reference = service.register_reference(
                RegisterSecretReferenceCommand(
                    workspace_id="workspace-a",
                    reference=SecretReference(
                        f"secret://workspace-secrets/keys/{key_id}"
                    ),
                    provider_registration_id=provider.registration_id,
                    allowed_intents=(SecretUseIntent.GATEWAY_PROBE_SIGNING_KEY,),
                    admitted_by="operator-a",
                    admitted_at="2026-08-02T02:01:00Z",
                    actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,),
                )
            )
            if key_id == "key-b":
                self.reference_b_registration_id = reference.registration_id

    def _key_statuses(self):
        with self.unit_of_work() as unit_of_work:
            keys = unit_of_work.stores.delegation_signing_keys.list_for_verification(
                "workspace-a",
                DelegationKeyPurpose.GATEWAY_PROBE,
                "cpk-server",
            )
            unit_of_work.commit()
        return {item.key_id: item.status for item in keys}

    def _transition_targets(self):
        return [
            transition.to_status
            for transition in GatewayKeyRotationService(
                self.unit_of_work,
                clock=lambda: self.epoch,
            ).transitions(self.rotation_id)
            if transition.to_status
            in {
                GatewayKeyRotationStatus.NEW_KEY_ACTIVE,
                GatewayKeyRotationStatus.DRAINING_OLD_GRANTS,
            }
        ]

    def _write_counts(self) -> tuple[int, int]:
        transitions = self.connection.execute(
            "SELECT count(*) FROM cpk_gateway_key_rotation_transitions "
            "WHERE rotation_id=%s AND to_status IN "
            "('new-key-active', 'draining-old-grants')",
            (self.rotation_id,),
        ).fetchone()[0]
        keys = self.connection.execute(
            "SELECT count(*) FROM cpk_delegation_signing_keys "
            "WHERE workspace_id='workspace-a'",
        ).fetchone()[0]
        return transitions, keys


if __name__ == "__main__":
    unittest.main()
