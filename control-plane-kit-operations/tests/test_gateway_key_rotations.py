from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import itertools
import os
import unittest

import psycopg

import control_plane_kit_operations.postgres as postgres
from gateway_rotation_overlap_fixture import GatewayRotationOverlapFixture
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_operations.approvals import (
    ApprovalCommandService,
    DecideApproval,
    RequestGatewayKeyRotationApproval,
)
from control_plane_kit_operations.gateway_key_rotations import (
    AdvanceGatewayKeyRotation,
    AdvanceGatewayKeyRotationDeployment,
    GatewayKeyRotationAuthorizationDenied,
    GatewayKeyRotationConflict,
    GatewayKeyRotationDeploymentCheckpoint,
    GatewayKeyRotationDeploymentPhase,
    GatewayKeyRotationDeploymentStatus,
    GatewayKeyRotationRevocationCheckpoint,
    GatewayKeyRotationService,
    GatewayKeyRotationStatus,
    GatewayKeyRotationTransition,
    RequestGatewayKeyRotation,
)
from control_plane_kit_operations.gateway_key_rotation_overlap_program import (
    GatewayKeyRotationOverlapPreparationProgram,
    PrepareGatewayKeyRotationOverlap,
)
from control_plane_kit_operations.lifecycle import (
    ExecutionLeaseDuration,
    ExecutionWorkerAuthority,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.postgres.gateway_key_rotation_store import (
    GatewayKeyRotationStore,
)
from control_plane_kit_operations.records import (
    ApprovalDecisionKind,
    OperationSessionRecord,
    OperationSessionStatus,
)
from control_plane_kit_operations.workflows import IdempotencyKey


class GatewayKeyRotationTests(GatewayRotationOverlapFixture, unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError("run through control-plane-kit-operations/test.sh")
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.connection.execute(
            "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
            "VALUES ('workspace-a', 'Workspace A', 'created')"
        )
        self.now = 1_800_000_000
        self.ids = itertools.count(1)
        self.approval_requests = {}
        self.approval_decisions = {}

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def service(self) -> GatewayKeyRotationService:
        return GatewayKeyRotationService(self.unit_of_work, clock=lambda: self.now)

    def prepare_fenced_overlap(self):
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.seed_graph_and_keys()
        self.seed_rotation_approval()
        timestamps = iter(
            f"2026-08-02T02:{minute:02d}:00Z" for minute in range(30)
        )
        identifiers = iter(
            f"direct-overlap-{index}" for index in range(1, 30)
        )
        return GatewayKeyRotationOverlapPreparationProgram(
            self.unit_of_work,
            clock=lambda: next(timestamps),
            trusted_epoch_clock=lambda: self.now,
            id_factory=lambda: next(identifiers),
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

    def accept_fenced_overlap(self, prepared):
        return self.accept_prepared_overlap(
            prepared,
            prefix="direct-overlap-execution",
        ).rotation

    def test_request_is_idempotent_and_one_nonterminal_binding_wins(self) -> None:
        service = self.service()
        first = service.request(self.request())

        self.assertEqual(service.request(self.request()), first)
        self.assertEqual(first.status, GatewayKeyRotationStatus.REQUESTED)
        self.assertEqual(first.version, 1)
        with self.assertRaises(GatewayKeyRotationConflict):
            service.request(replace(self.request(), correlation_id="rotation-other"))
        with self.assertRaises(GatewayKeyRotationConflict):
            service.request(replace(self.request(), new_secret_reference=SecretReference(
                "secret://workspace-secrets/keys/other"
            )))

    def test_surface_read_purpose_rotation_and_approval_are_current_truth(self) -> None:
        purpose = getattr(
            DelegationKeyPurpose,
            "WORKLOAD_NODE_CONTROL_SURFACE_READ",
            None,
        )
        self.assertIsNotNone(purpose)
        rotation = self.service().request(replace(self.request(), purpose=purpose))
        approval = self.request_approval(rotation)

        self.assertIs(rotation.purpose, purpose)
        self.assertEqual(
            approval.request.subject.descriptor()["purpose"],
            "workload-node-control-surface-read",
        )
        stored_subject, stored_digest = self.connection.execute(
            "SELECT subject_payload, review_digest FROM cpk_approval_requests "
            "WHERE request_id = %s",
            (approval.request.request_id,),
        ).fetchone()
        self.assertEqual(stored_subject, approval.request.subject.descriptor())
        self.assertEqual(stored_digest, approval.request.subject.review_digest)
        postgres.install_schema(self.connection)

    def test_permissions_and_optimistic_version_are_enforced(self) -> None:
        service = self.service()
        with self.assertRaises(GatewayKeyRotationAuthorizationDenied):
            service.request(replace(
                self.request(), actor_scopes=(PolicyScope.DELEGATION_KEY_GENERATE,)
            ))
        rotation = service.request(self.request())
        with self.assertRaises(GatewayKeyRotationConflict):
            service.advance(AdvanceGatewayKeyRotation(
                rotation_id=rotation.rotation_id,
                transition_id="request-approval-stale",
                expected_status=GatewayKeyRotationStatus.REQUESTED,
                expected_version=2,
                target_status=GatewayKeyRotationStatus.AWAITING_APPROVAL,
                advanced_by="operator-a",
                advanced_at="2026-08-02T01:01:00Z",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                approval_request_id="approval-request-a",
            ))

    def test_transition_id_is_exactly_idempotent_and_semantic_reuse_conflicts(self) -> None:
        service = self.service()
        rotation = service.request(self.request())
        request = self.request_approval(rotation)
        command = AdvanceGatewayKeyRotation(
            rotation_id=rotation.rotation_id,
            transition_id="request-approval",
            expected_status=rotation.status,
            expected_version=rotation.version,
            target_status=GatewayKeyRotationStatus.AWAITING_APPROVAL,
            advanced_by="operator-a",
            advanced_at="2026-08-02T01:01:00Z",
            actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
            approval_request_id=request.request.request_id,
        )

        first = service.advance(command)
        self.assertEqual(service.advance(command), first)
        self.assertEqual(len(service.transitions(rotation.rotation_id)), 1)
        with self.assertRaises(GatewayKeyRotationConflict):
            service.advance(replace(
                command, approval_request_id="approval-request-other"))

    def test_concurrent_requests_elect_one_nonterminal_rotation(self) -> None:
        def request(correlation_id):
            try:
                return self.service().request(replace(
                    self.request(), correlation_id=correlation_id,
                    key_generation_correlation=f"generate-{correlation_id}",
                    new_secret_reference=SecretReference(
                        f"secret://workspace-secrets/keys/{correlation_id}"),
                ))
            except GatewayKeyRotationConflict:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(request, ("rotation-a", "rotation-b")))

        winners = tuple(result for result in results if result is not None)
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0].status, GatewayKeyRotationStatus.REQUESTED)

    def test_fenced_overlap_preserves_durable_drain_deadline_without_sleep(
        self,
    ) -> None:
        prepared = self.prepare_fenced_overlap()
        rotation = self.accept_fenced_overlap(prepared)
        rotation = self.advance(rotation, GatewayKeyRotationStatus.NEW_KEY_ACTIVE,
            new_key_activated_at="2026-08-02T01:06:00Z")
        self.assertEqual(rotation.drain_deadline_epoch, self.now + 65)
        rotation = self.advance(rotation, GatewayKeyRotationStatus.DRAINING_OLD_GRANTS)
        self.assertEqual(
            rotation.status,
            GatewayKeyRotationStatus.DRAINING_OLD_GRANTS,
        )
        self.assertEqual(self.service().get(rotation.rotation_id), rotation)
        transitions = self.service().transitions(rotation.rotation_id)
        self.assertEqual(transitions[0].from_status, GatewayKeyRotationStatus.REQUESTED)
        self.assertEqual(
            transitions[2].to_status,
            GatewayKeyRotationStatus.GENERATION_PREPARED,
        )
        self.assertEqual(
            transitions[-1].to_status,
            GatewayKeyRotationStatus.DRAINING_OLD_GRANTS,
        )
        read = self.service().read(rotation.rotation_id)
        self.assertNotIn("secret", repr(read).lower())
        self.assertEqual(read.new_key_id, "key-b")

    def test_blocked_retains_child_identity_and_rejects_guessing_success(self) -> None:
        prepared = self.prepare_fenced_overlap()
        blocked = self.service().advance_deployment(
            AdvanceGatewayKeyRotationDeployment(
                transition=AdvanceGatewayKeyRotation(
                    rotation_id=self.rotation_id,
                    transition_id="direct-overlap-blocked",
                    expected_status=GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
                    expected_version=prepared.rotation.version,
                    target_status=GatewayKeyRotationStatus.BLOCKED,
                    advanced_by="operator-a",
                    advanced_at="2026-08-02T03:00:00Z",
                    actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                    failure_code="child-effect-uncertain",
                ),
                handoff=prepared.handoff,
            )
        )

        self.assertEqual(blocked.overlap_deployment, prepared.checkpoint)
        self.assertEqual(blocked.failure_code, "child-effect-uncertain")
        with self.assertRaises(GatewayKeyRotationConflict):
            self.service().advance(AdvanceGatewayKeyRotation(
                rotation_id=self.rotation_id,
                transition_id="guess-overlap-success",
                expected_status=GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
                expected_version=prepared.rotation.version,
                target_status=GatewayKeyRotationStatus.OVERLAP_READY,
                advanced_by="operator-a",
                advanced_at="2026-08-02T03:01:00Z",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                deployment=replace(prepared.checkpoint,
                    status=GatewayKeyRotationDeploymentStatus.ACCEPTED,
                    accepted_current_graph_id="graph-a",
                    accepted_current_projection_id="projection-a-b",
                    accepted_at="2026-08-02T01:05:00Z"),
            ))

    def test_restart_reconstructs_identity_and_database_rejects_corruption(self) -> None:
        prepared = self.prepare_fenced_overlap()
        rotation = prepared.rotation
        checkpoint = prepared.checkpoint

        recovered = self.service().get(rotation.rotation_id)
        self.assertEqual(recovered.status, GatewayKeyRotationStatus.OVERLAP_DEPLOYING)
        self.assertEqual(recovered.overlap_deployment, checkpoint)
        self.assertEqual(
            self.service().transitions(rotation.rotation_id)[-1].to_status,
            GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
        )
        with self.assertRaises(psycopg.errors.CheckViolation) as captured:
            self.connection.execute(
                "UPDATE cpk_gateway_key_rotation_deployments "
                "SET run_id = 'run/bad' WHERE rotation_id = %s",
                (rotation.rotation_id,),
            )
        self.assertEqual(
            captured.exception.diag.constraint_name,
            "cpk_gateway_key_rotation_deployments_run_id_check",
        )
        self.assertEqual(
            self.service().get(rotation.rotation_id).overlap_deployment,
            checkpoint,
        )

    def test_service_admits_all_supplied_times_before_uow_or_replay_access(self) -> None:
        invalid = "2027-02-30T08:00:00Z"
        accesses = 0

        def forbidden_uow():
            nonlocal accesses
            accesses += 1
            raise AssertionError("UoW opened before timestamp admission")

        service = GatewayKeyRotationService(forbidden_uow, clock=lambda: self.now)
        requested = replace(self.request(), requested_at=invalid)
        prepared = self.checkpoint(GatewayKeyRotationDeploymentPhase.OVERLAP)
        accepted = replace(
            prepared,
            status=GatewayKeyRotationDeploymentStatus.ACCEPTED,
            accepted_current_graph_id="graph-a",
            accepted_current_projection_id="projection-a-b",
            accepted_at=invalid,
        )
        revocation = GatewayKeyRotationRevocationCheckpoint(
            provider_registration_id="provider-a",
            secret_reference=SecretReference("secret://workspace-secrets/keys/key-a"),
            provider_version_id="version-a",
            provider_version_number=1,
            revocation_id="srevoke_" + "a" * 64,
            correlation_id="rotation-a:revoke-old-version",
            action_digest="b" * 64,
            prepared_at=invalid,
        )
        base = AdvanceGatewayKeyRotation(
            rotation_id="rotation-a",
            transition_id="transition-a",
            expected_status=GatewayKeyRotationStatus.REQUESTED,
            expected_version=1,
            target_status=GatewayKeyRotationStatus.AWAITING_APPROVAL,
            advanced_by="operator-a",
            advanced_at="2027-01-15T08:00:00Z",
            actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
            approval_request_id="approval-request-a",
        )
        cases = (
            ("request", lambda: service.request(requested)),
            ("advanced", lambda: service.advance(replace(base, advanced_at=invalid))),
            (
                "activation",
                lambda: service.advance(replace(base, new_key_activated_at=invalid)),
            ),
            (
                "retirement",
                lambda: service.advance(replace(base, old_key_retired_at=invalid)),
            ),
            (
                "secret-revocation",
                lambda: service.advance(replace(base, old_secret_revoked_at=invalid)),
            ),
            (
                "deployment-prepared",
                lambda: service.advance(
                    replace(base, deployment=replace(prepared, prepared_at=invalid))
                ),
            ),
            ("deployment-accepted", lambda: service.advance(replace(base, deployment=accepted))),
            ("revocation-prepared", lambda: service.advance(replace(base, revocation=revocation))),
        )
        for identity, operation in cases:
            with self.subTest(identity=identity):
                with self.assertRaisesRegex(ValueError, "canonical UTC text"):
                    operation()
        self.assertEqual(accesses, 0)

    def test_direct_store_encodes_main_and_nested_times_before_first_access(self) -> None:
        invalid = "2027-02-30T08:00:00Z"
        current = self.service().request(self.request())

        class FailOnAccessConnection:
            def __init__(self) -> None:
                self.accesses = 0

            def execute(self, *_args: object, **_kwargs: object) -> object:
                self.accesses += 1
                raise AssertionError("database access occurred before timestamp admission")

        prepared = self.checkpoint(GatewayKeyRotationDeploymentPhase.OVERLAP)
        accepted = replace(
            prepared,
            status=GatewayKeyRotationDeploymentStatus.ACCEPTED,
            accepted_current_graph_id="graph-a",
            accepted_current_projection_id="projection-a-b",
            accepted_at=invalid,
        )
        revocation = GatewayKeyRotationRevocationCheckpoint(
            provider_registration_id="provider-a",
            secret_reference=SecretReference("secret://workspace-secrets/keys/key-a"),
            provider_version_id="version-a",
            provider_version_number=1,
            revocation_id="srevoke_" + "a" * 64,
            correlation_id="rotation-a:revoke-old-version",
            action_digest="b" * 64,
            prepared_at=invalid,
        )
        transition = GatewayKeyRotationTransition(
            rotation_id=current.rotation_id,
            transition_id="transition-direct",
            from_status=GatewayKeyRotationStatus.REQUESTED,
            to_status=GatewayKeyRotationStatus.AWAITING_APPROVAL,
            from_version=1,
            to_version=2,
            transition_fingerprint="c" * 64,
            advanced_by="operator-a",
            advanced_at=invalid,
        )
        cases = (
            ("add-requested", lambda store: store.add(replace(current, requested_at=invalid))),
            ("add-updated", lambda store: store.add(replace(current, updated_at=invalid))),
            (
                "cas-updated",
                lambda store: store.compare_and_set(
                    current, replace(current, updated_at=invalid)
                ),
            ),
            (
                "cas-activation",
                lambda store: store.compare_and_set(
                    current, replace(current, new_key_activated_at=invalid, drain_deadline_epoch=1)
                ),
            ),
            (
                "cas-retirement",
                lambda store: store.compare_and_set(current, replace(current, old_key_retired_at=invalid)),
            ),
            (
                "cas-secret-revocation",
                lambda store: store.compare_and_set(
                    current,
                    replace(
                        current,
                        old_key_retired_at="2027-01-15T08:00:00Z",
                        old_secret_revoked_at=invalid,
                        revocation=replace(revocation, prepared_at="2027-01-15T08:00:00Z"),
                    ),
                ),
            ),
            (
                "cas-deployment",
                lambda store: store.compare_and_set(
                    current,
                    replace(current, overlap_deployment=replace(prepared, prepared_at=invalid)),
                ),
            ),
            (
                "cas-deployment-accepted",
                lambda store: store.compare_and_set(
                    current, replace(current, overlap_deployment=accepted)
                ),
            ),
            (
                "cas-revocation",
                lambda store: store.compare_and_set(
                    current,
                    replace(
                        current,
                        old_key_retired_at="2027-01-15T08:00:00Z",
                        revocation=revocation,
                    ),
                ),
            ),
            ("transition", lambda store: store.add_transition(transition)),
        )
        for identity, operation in cases:
            with self.subTest(identity=identity):
                connection = FailOnAccessConnection()
                with self.assertRaisesRegex(ValueError, "canonical UTC text"):
                    operation(GatewayKeyRotationStore(connection))
                self.assertEqual(connection.accesses, 0)

    def test_invalid_duplicate_request_and_transition_cannot_bypass_admission(self) -> None:
        service = self.service()
        original = service.request(self.request())
        invalid = "2027-02-30T08:00:00Z"

        with self.assertRaisesRegex(ValueError, "canonical UTC text"):
            service.request(replace(self.request(), requested_at=invalid))
        self.assertEqual(service.get(original.rotation_id), original)

        approval = self.request_approval(original)
        command = AdvanceGatewayKeyRotation(
            rotation_id=original.rotation_id,
            transition_id="request-approval-replay",
            expected_status=original.status,
            expected_version=original.version,
            target_status=GatewayKeyRotationStatus.AWAITING_APPROVAL,
            advanced_by="operator-a",
            advanced_at="2027-01-15T08:00:00Z",
            actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
            approval_request_id=approval.request.request_id,
        )
        advanced = service.advance(command)
        with self.assertRaisesRegex(ValueError, "canonical UTC text"):
            service.advance(replace(command, advanced_at=invalid))
        self.assertEqual(service.get(original.rotation_id), advanced)

    def test_exact_transition_replay_does_not_consult_the_epoch_clock(self) -> None:
        original = self.service().request(self.request())
        approval = self.request_approval(original)
        command = AdvanceGatewayKeyRotation(
            rotation_id=original.rotation_id,
            transition_id="request-approval-no-clock",
            expected_status=original.status,
            expected_version=original.version,
            target_status=GatewayKeyRotationStatus.AWAITING_APPROVAL,
            advanced_by="operator-a",
            advanced_at="2027-01-15T08:00:00Z",
            actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
            approval_request_id=approval.request.request_id,
        )
        clock_calls = 0

        def counted_clock() -> int:
            nonlocal clock_calls
            clock_calls += 1
            return self.now

        first = GatewayKeyRotationService(
            self.unit_of_work, clock=counted_clock
        ).advance(command)
        self.assertEqual(clock_calls, 1)

        def unexpected_clock() -> int:
            raise AssertionError("exact replay consulted the epoch clock")

        replay = GatewayKeyRotationService(
            self.unit_of_work, clock=unexpected_clock
        ).advance(command)
        self.assertEqual(replay, first)

    def test_timestamp_admission_rejects_string_subclasses_without_invocation(self) -> None:
        calls = 0
        marker = "hostile-timestamp-material"

        class HostileString(str):
            def _called(self) -> None:
                nonlocal calls
                calls += 1
                raise AssertionError("hostile timestamp method was invoked")

            def encode(self, *_args: object, **_kwargs: object) -> bytes:
                self._called()

            def __str__(self) -> str:
                self._called()

            def __repr__(self) -> str:
                self._called()

            def __eq__(self, _other: object) -> bool:
                self._called()

            def __hash__(self) -> int:
                self._called()

            def __format__(self, _format_spec: str) -> str:
                self._called()

        command = replace(
            self.request(), requested_at=HostileString(marker)
        )
        with self.assertRaises(ValueError) as raised:
            self.service().request(command)
        self.assertEqual(str(raised.exception), "timestamp must be canonical UTC text")
        self.assertLessEqual(len(str(raised.exception)), 128)
        self.assertNotIn(marker, str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)
        self.assertEqual(calls, 0)

    def prepare_generation(self, rotation):
        prepared = self.advance(
            rotation,
            GatewayKeyRotationStatus.GENERATION_PREPARED,
            generation_provider_registration_id="provider-registration-a",
            generation_action_digest="a" * 64,
        )
        self.assertEqual(
            prepared.generation_provider_registration_id,
            "provider-registration-a",
        )
        self.assertEqual(prepared.generation_action_digest, "a" * 64)
        return prepared

    def request(self) -> RequestGatewayKeyRotation:
        return RequestGatewayKeyRotation(
            workspace_id="workspace-a", gateway_node_id="gateway-a",
            purpose=DelegationKeyPurpose.GATEWAY_PROBE, issuer="cpk-server",
            old_key_id="gateway-key-a",
            new_secret_reference=SecretReference(
                "secret://workspace-secrets/keys/gateway-key-b"),
            key_generation_correlation="generate-gateway-key-b",
            maximum_grant_lifetime_seconds=60, clock_skew_seconds=5,
            correlation_id="rotation-a", requested_by="operator-a",
            requested_at="2026-08-02T01:00:00Z",
            actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,))

    def advance(self, rotation, target, **kwargs):
        if target is GatewayKeyRotationStatus.AWAITING_APPROVAL:
            request = self.request_approval(rotation)
            kwargs["approval_request_id"] = request.request.request_id
        elif target in {
            GatewayKeyRotationStatus.APPROVED,
            GatewayKeyRotationStatus.REJECTED,
        }:
            decision = self.decide_approval(rotation, target)
            kwargs["approval_decision_id"] = decision.decision.decision_id
        return self.service().advance(AdvanceGatewayKeyRotation(
            rotation_id=rotation.rotation_id,
            transition_id=f"{rotation.version}-{target.value}",
            expected_status=rotation.status,
            expected_version=rotation.version, target_status=target,
            advanced_by="operator-a", advanced_at="2026-08-02T01:01:00Z",
            actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,), **kwargs))

    def request_approval(self, rotation):
        existing = self.approval_requests.get(rotation.rotation_id)
        if existing is not None:
            return existing
        session_id = f"rotation-session-{next(self.ids)}"
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.activity_history.add_session(
                OperationSessionRecord(
                    session_id=session_id,
                    workspace_id=rotation.workspace_id,
                    actor_id="operator-a",
                    title="Review gateway key rotation",
                    status=OperationSessionStatus.OPEN,
                    created_at="2026-08-02T01:00:30Z",
                )
            )
            unit_of_work.commit()
        service = ApprovalCommandService(
            self.unit_of_work,
            clock=lambda: "2026-08-02T01:00:31Z",
            id_factory=lambda: f"approval-{next(self.ids)}",
        )
        result = service.execute(
            RequestGatewayKeyRotationApproval(
                session_id=session_id,
                rotation_id=rotation.rotation_id,
                actor_id="operator-a",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                idempotency_key=IdempotencyKey(
                    f"request-{rotation.rotation_id}"
                ),
            )
        )
        self.approval_requests[rotation.rotation_id] = result
        return result

    def decide_approval(self, rotation, target):
        existing = self.approval_decisions.get(rotation.rotation_id)
        if existing is not None:
            return existing
        request = self.request_approval(rotation)
        decision_kind = (
            ApprovalDecisionKind.APPROVED
            if target is GatewayKeyRotationStatus.APPROVED
            else ApprovalDecisionKind.REJECTED
        )
        service = ApprovalCommandService(
            self.unit_of_work,
            clock=lambda: "2026-08-02T01:00:32Z",
            id_factory=lambda: f"approval-{next(self.ids)}",
        )
        result = service.execute(
            DecideApproval(
                session_id=request.request.session_id,
                request_id=request.request.request_id,
                actor_id="manager-a",
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE_APPROVE,),
                decision=decision_kind,
                idempotency_key=IdempotencyKey(
                    f"decide-{rotation.rotation_id}"
                ),
            )
        )
        self.approval_decisions[rotation.rotation_id] = result
        return result

    def checkpoint(self, phase):
        suffix = "overlap" if phase is GatewayKeyRotationDeploymentPhase.OVERLAP else "retire"
        return GatewayKeyRotationDeploymentCheckpoint(
            phase=phase, status=GatewayKeyRotationDeploymentStatus.PREPARED,
            session_id=f"session-{suffix}", plan_id=f"plan-{suffix}",
            approval_request_id=f"approval-request-{suffix}",
            approval_decision_id=f"approval-decision-{suffix}",
            execution_request_id=f"execution-{suffix}", run_id=f"run-{suffix}",
            base_authored_graph_id="graph-a",
            base_realized_projection_id=(
                "projection-a"
                if phase is GatewayKeyRotationDeploymentPhase.OVERLAP
                else "projection-a-b"
            ),
            desired_authored_graph_id="graph-a",
            desired_realized_projection_id=(
                "projection-a-b"
                if phase is GatewayKeyRotationDeploymentPhase.OVERLAP
                else "projection-b"
            ),
            desired_revision=(
                2 if phase is GatewayKeyRotationDeploymentPhase.OVERLAP else 3
            ),
            prepared_at="2026-08-02T01:04:00Z")


if __name__ == "__main__":
    unittest.main()
