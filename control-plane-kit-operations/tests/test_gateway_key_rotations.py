from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import itertools
import os
import unittest

import psycopg

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
    GatewayKeyRotationAuthorizationDenied,
    GatewayKeyRotationConflict,
    GatewayKeyRotationDeploymentCheckpoint,
    GatewayKeyRotationDeploymentPhase,
    GatewayKeyRotationDeploymentStatus,
    GatewayKeyRotationService,
    GatewayKeyRotationStatus,
    RequestGatewayKeyRotation,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import (
    ApprovalDecisionKind,
    OperationSessionRecord,
    OperationSessionStatus,
)
from control_plane_kit_operations.workflows import IdempotencyKey


class GatewayKeyRotationTests(unittest.TestCase):
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

    def test_full_state_machine_uses_durable_drain_deadline_without_sleep(self) -> None:
        service = self.service()
        rotation = service.request(self.request())
        rotation = self.advance(rotation, GatewayKeyRotationStatus.AWAITING_APPROVAL,
            approval_request_id="approval-request-a")
        rotation = self.advance(rotation, GatewayKeyRotationStatus.APPROVED,
            approval_decision_id="approval-decision-a")
        rotation = self.prepare_generation(rotation)
        rotation = self.advance(rotation, GatewayKeyRotationStatus.KEY_GENERATED,
            new_key_id="gateway-key-b", new_secret_version_id="version-b",
            new_secret_version_number=1)
        overlap = self.checkpoint(GatewayKeyRotationDeploymentPhase.OVERLAP)
        rotation = self.advance(rotation, GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
            deployment=overlap)
        rotation = self.advance(rotation, GatewayKeyRotationStatus.OVERLAP_READY,
            deployment=replace(overlap,
                status=GatewayKeyRotationDeploymentStatus.ACCEPTED,
                accepted_current_graph_id="graph-a",
                accepted_current_projection_id="projection-a-b",
                accepted_at="2026-08-02T01:05:00Z"))
        rotation = self.advance(rotation, GatewayKeyRotationStatus.NEW_KEY_ACTIVE,
            new_key_activated_at="2026-08-02T01:06:00Z")
        self.assertEqual(rotation.drain_deadline_epoch, self.now + 65)
        rotation = self.advance(rotation, GatewayKeyRotationStatus.DRAINING_OLD_GRANTS)
        retirement = self.checkpoint(GatewayKeyRotationDeploymentPhase.RETIREMENT)
        with self.assertRaises(GatewayKeyRotationConflict):
            self.advance(rotation, GatewayKeyRotationStatus.RETIREMENT_DEPLOYING,
                deployment=retirement)
        self.now += 65
        rotation = self.advance(rotation, GatewayKeyRotationStatus.RETIREMENT_DEPLOYING,
            deployment=retirement)
        rotation = self.advance(rotation, GatewayKeyRotationStatus.COMPLETED,
            deployment=replace(retirement,
                status=GatewayKeyRotationDeploymentStatus.ACCEPTED,
                accepted_current_graph_id="graph-a",
                accepted_current_projection_id="projection-b",
                accepted_at="2026-08-02T01:08:00Z"),
            old_key_retired_at="2026-08-02T01:09:00Z",
            old_secret_revoked_at="2026-08-02T01:09:01Z")

        self.assertEqual(rotation.status, GatewayKeyRotationStatus.COMPLETED)
        self.assertEqual(self.service().get(rotation.rotation_id), rotation)
        transitions = self.service().transitions(rotation.rotation_id)
        self.assertEqual(len(transitions), 10)
        self.assertEqual(transitions[0].from_status, GatewayKeyRotationStatus.REQUESTED)
        self.assertEqual(
            transitions[2].to_status,
            GatewayKeyRotationStatus.GENERATION_PREPARED,
        )
        self.assertEqual(transitions[-1].to_status, GatewayKeyRotationStatus.COMPLETED)
        read = self.service().read(rotation.rotation_id)
        self.assertNotIn("secret", repr(read).lower())
        self.assertEqual(read.new_key_id, "gateway-key-b")

    def test_blocked_retains_child_identity_and_rejects_guessing_success(self) -> None:
        service = self.service()
        rotation = service.request(self.request())
        rotation = self.advance(rotation, GatewayKeyRotationStatus.AWAITING_APPROVAL,
            approval_request_id="approval-request-a")
        rotation = self.advance(rotation, GatewayKeyRotationStatus.APPROVED,
            approval_decision_id="approval-decision-a")
        rotation = self.prepare_generation(rotation)
        rotation = self.advance(rotation, GatewayKeyRotationStatus.KEY_GENERATED,
            new_key_id="gateway-key-b", new_secret_version_id="version-b",
            new_secret_version_number=1)
        checkpoint = self.checkpoint(GatewayKeyRotationDeploymentPhase.OVERLAP)
        rotation = self.advance(rotation, GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
            deployment=checkpoint)
        blocked = self.advance(rotation, GatewayKeyRotationStatus.BLOCKED,
            failure_code="child-effect-uncertain")

        self.assertEqual(blocked.overlap_deployment, checkpoint)
        self.assertEqual(blocked.failure_code, "child-effect-uncertain")
        with self.assertRaises(GatewayKeyRotationConflict):
            self.advance(blocked, GatewayKeyRotationStatus.OVERLAP_READY,
                deployment=replace(checkpoint,
                    status=GatewayKeyRotationDeploymentStatus.ACCEPTED,
                    accepted_current_graph_id="graph-a",
                    accepted_current_projection_id="projection-a-b",
                    accepted_at="2026-08-02T01:05:00Z"))

    def test_restart_reconstructs_predeclared_child_identity_before_effect_fold(self) -> None:
        rotation = self.service().request(self.request())
        rotation = self.advance(rotation, GatewayKeyRotationStatus.AWAITING_APPROVAL,
            approval_request_id="approval-request-a")
        rotation = self.advance(rotation, GatewayKeyRotationStatus.APPROVED,
            approval_decision_id="approval-decision-a")
        rotation = self.prepare_generation(rotation)
        rotation = self.advance(rotation, GatewayKeyRotationStatus.KEY_GENERATED,
            new_key_id="gateway-key-b", new_secret_version_id="version-b",
            new_secret_version_number=1)
        checkpoint = self.checkpoint(GatewayKeyRotationDeploymentPhase.OVERLAP)
        rotation = self.advance(rotation, GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
            deployment=checkpoint)

        recovered = self.service().get(rotation.rotation_id)
        self.assertEqual(recovered.status, GatewayKeyRotationStatus.OVERLAP_DEPLOYING)
        self.assertEqual(recovered.overlap_deployment, checkpoint)
        self.assertEqual(
            self.service().transitions(rotation.rotation_id)[-1].to_status,
            GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
        )

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
