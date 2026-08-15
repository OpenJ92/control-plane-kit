from __future__ import annotations

import importlib

from control_plane_kit_core.operations import RunId
from control_plane_kit_core.operations.lifecycle import (
    RecoveryDecisionKind,
    RecoveryScope,
)
from control_plane_kit_operations.activity_run_retry import RetryFailedActivityRun
from control_plane_kit_operations.execution_lease_recovery import RecoveryAuthority
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.records import (
    ActivityRunRecord,
    AdmittedRun,
    RetryIdentity,
)
from control_plane_kit_core.operations.lifecycle import ActivityRunStatus
from control_plane_kit_operations.workflows import IdempotencyKey

from tests.execution_lease_recovery_fixture import (
    PostgresExecutionLeaseRecoveryFixture,
    Sequence,
)


TARGET_MODULE = "control_plane_kit_operations.activity_run_retry_interpreter"

try:
    retry_interpreter = importlib.import_module(TARGET_MODULE)
except ModuleNotFoundError as error:
    if error.name != TARGET_MODULE:
        raise
    retry_interpreter = None

ActivityRunRetryCommandService = getattr(
    retry_interpreter,
    "ActivityRunRetryCommandService",
    None,
)


class PostgresActivityRunRetryFixture(PostgresExecutionLeaseRecoveryFixture):
    def require_retry_service(self) -> None:
        self.assertIsNotNone(
            ActivityRunRetryCommandService,
            "activity-run retry interpreter is missing",
        )

    def retry_service_with_sequence(self, *ids: str):
        self.require_retry_service()
        sequence = Sequence(*ids)
        return (
            ActivityRunRetryCommandService(
                self.unit_of_work,
                id_factory=sequence,
            ),
            sequence,
        )

    def retry_service(self, *ids: str):
        return self.retry_service_with_sequence(*ids)[0]

    def retry_command(
        self,
        *,
        key: str = "retry-a",
        request_id: str = "request-a",
        prior_run_id: str = "run-a",
        expected_fence: ExecutionLeaseFence | None = None,
        actor_id: str = "operator-a",
        authority_reference: str = "authority-reference-a",
        scopes: tuple[RecoveryScope, ...] = (RecoveryScope.OPERATE,),
    ) -> RetryFailedActivityRun:
        return RetryFailedActivityRun(
            request_id,
            RunId(prior_run_id),
            expected_fence or ExecutionLeaseFence("worker-a", 7),
            RecoveryAuthority(actor_id, authority_reference, scopes),
            IdempotencyKey(key),
        )

    def reset_retry_truth(
        self,
        *,
        history: str = "failed",
        approval_subject: str = "activity-plan",
    ) -> None:
        self.reset_truth(
            RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
            history=history,
            approval_subject=approval_subject,
        )
        self.connection.execute(
            "UPDATE cpk_execution_requests SET claimed_at = %s, "
            "lease_expires_at = %s WHERE request_id = 'request-a'",
            ("2098-01-01T00:00:00Z", "2099-01-01T00:00:00Z"),
        )

    def seed_foreign_run(self, run_id: str = "run-foreign") -> None:
        self.connection.execute(
            "INSERT INTO cpk_activity_plans "
            "(plan_id, session_id, base_graph_id, desired_graph_id, "
            "base_realized_projection_id, desired_realized_projection_id, "
            "desired_graph_revision, status, created_at, payload) "
            "SELECT 'plan-b', session_id, base_graph_id, desired_graph_id, "
            "base_realized_projection_id, desired_realized_projection_id, "
            "desired_graph_revision, status, created_at, payload "
            "FROM cpk_activity_plans WHERE plan_id = 'plan-a'"
        )
        self.connection.execute(
            "INSERT INTO cpk_execution_requests "
            "(request_id, workspace_id, session_id, plan_id, status, "
            "requested_by, requested_at, approval_request_id, "
            "approval_decision_id, idempotency_key, intent_fingerprint, "
            "claim_worker_id, claim_generation, claimed_at, lease_expires_at) "
            "SELECT 'request-b', workspace_id, session_id, 'plan-b', status, "
            "requested_by, requested_at, approval_request_id, "
            "approval_decision_id, 'execute-b', intent_fingerprint, "
            "claim_worker_id, claim_generation, claimed_at, lease_expires_at "
            "FROM cpk_execution_requests WHERE request_id = 'request-a'"
        )
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.execution.add_run(
                ActivityRunRecord(
                    run_id,
                    "plan-b",
                    AdmittedRun("request-b"),
                    RetryIdentity(1),
                    ActivityRunStatus.CLAIMED,
                    "2026-08-15T04:20:00Z",
                )
            )
            unit_of_work.commit()


__all__ = [
    "ActivityRunRetryCommandService",
    "PostgresActivityRunRetryFixture",
    "TARGET_MODULE",
]
