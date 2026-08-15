from __future__ import annotations

import importlib

from control_plane_kit_core.operations import RunId
from control_plane_kit_core.operations.lifecycle import (
    RecoveryDecisionKind,
    RecoveryScope,
)
from control_plane_kit_operations.activity_run_retry import RetryFailedActivityRun
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
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
            self.authority(
                RecoveryScope.OPERATE,
                actor_id=actor_id,
                authority_reference=authority_reference,
                extra_scopes=tuple(
                    scope for scope in scopes if scope is not RecoveryScope.OPERATE
                ),
            )
            if RecoveryScope.OPERATE in scopes
            else self.authority(
                scopes[0],
                actor_id=actor_id,
                authority_reference=authority_reference,
                extra_scopes=scopes[1:],
            ),
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


__all__ = [
    "ActivityRunRetryCommandService",
    "PostgresActivityRunRetryFixture",
    "TARGET_MODULE",
]
