from __future__ import annotations

import importlib
import os

from control_plane_kit_core.operations import RunId
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectIntent,
    RuntimeEffectIntentSource,
)
from control_plane_kit_core.runtime_effects import RuntimeEffectKind
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from tests.failed_run_compensation_fixture import (
    FailedRunCompensationFixture,
    Sequence,
)


TARGET_MODULE = "control_plane_kit_operations.failed_run_compensation_attempt"

try:
    attempt_module = importlib.import_module(TARGET_MODULE)
except ModuleNotFoundError as error:
    if error.name != TARGET_MODULE:
        raise
    attempt_module = None


class FailedRunCompensationAttemptFixture(FailedRunCompensationFixture):
    def require_attempt_contract(self):
        self.assertIsNotNone(
            attempt_module,
            "failed-run compensation attempt binding is missing",
        )
        return attempt_module

    def seed_admitted_program(self) -> None:
        self.seed_truth()
        self.service(
            Sequence("program-a", "compensation-started", "action-a")
        ).execute(self.command())
        self.connection.execute(
            "UPDATE cpk_execution_requests SET "
            "claimed_at='2098-01-01T00:00:00Z', "
            "lease_expires_at='2099-01-01T00:00:00Z' "
            "WHERE request_id='request-a'"
        )

    def intent(self, position: int = 1, **changes):
        with self.unit_of_work() as unit_of_work:
            _, program = unit_of_work.stores.failed_run_compensations.get(
                "program-a"
            )
        step = program.steps[position - 1]
        values = {
            "kind": RuntimeEffectKind.REALIZE_ACTIVITY,
            "runtime_kind": RuntimeKind.DOCKER,
            "source": RuntimeEffectIntentSource(
                "workspace-a",
                "request-a",
                RunId("run-a"),
                "plan-a",
                "graph-current",
                "graph-desired",
            ),
            "activity_id": __import__(
                "control_plane_kit_core.planning",
                fromlist=("ActivityId",),
            ).ActivityId(step.source_effect.attempt_identity.activity_id),
            "operation": step.operation,
            "authority_ref": None,
            "authority_deliveries": (),
            "products": (),
        }
        values.update(changes)
        return RuntimeEffectIntent(**values)

    def start_command(self, position: int = 1, **changes):
        module = self.require_attempt_contract()
        values = {
            "program_id": "program-a",
            "position": position,
            "intent": self.intent(position),
            "authority": ExecutionWorkerAuthority(
                "worker-a",
                (__import__(
                    "control_plane_kit_core.policies",
                    fromlist=("PolicyScope",),
                ).PolicyScope.EXECUTION_OPERATE,),
            ),
            "fence": ExecutionLeaseFence("worker-a", 1),
        }
        values.update(changes)
        return module.StartFailedRunCompensationAttempt(**values)

    def attempt_service(self, *ids: str, unit_of_work=None):
        module = self.require_attempt_contract()
        return module.FailedRunCompensationAttemptStartService(
            unit_of_work or self.unit_of_work,
            id_factory=Sequence(*ids),
        )

    def binding_snapshot(self):
        return (
            tuple(
                self.connection.execute(
                    "SELECT * FROM cpk_failed_run_compensation_attempt_bindings "
                    "ORDER BY program_id, position"
                ).fetchall()
            ),
            tuple(
                self.connection.execute(
                    "SELECT event_id, run_id, ordinal, event_type, activity_id, "
                    "evidence FROM cpk_activity_events "
                    "WHERE event_type='step_compensation_started' "
                    "ORDER BY run_id, ordinal"
                ).fetchall()
            ),
            tuple(
                self.connection.execute(
                    "SELECT run_id, activity_id, attempt, status, prior_run_id, "
                    "prior_activity_id, prior_attempt, original_event_id, "
                    "latest_event_id FROM cpk_effect_attempts WHERE attempt > 1 "
                    "ORDER BY run_id, activity_id, attempt"
                ).fetchall()
            ),
            tuple(
                self.connection.execute(
                    "SELECT run_id, activity_id, attempt, request_fingerprint, "
                    "original_event_id, preimage "
                    "FROM cpk_effect_attempt_intents WHERE attempt > 1 "
                    "ORDER BY run_id, activity_id, attempt"
                ).fetchall()
            ),
        )

    def source_truth_snapshot(self):
        return (
            tuple(
                self.connection.execute(
                    "SELECT run_id, activity_id, attempt, status, "
                    "outcome_fingerprint, original_event_id, latest_event_id "
                    "FROM cpk_effect_attempts WHERE attempt=1 "
                    "ORDER BY run_id, activity_id"
                ).fetchall()
            ),
            tuple(
                self.connection.execute(
                    "SELECT run_id, activity_id, attempt, status, "
                    "outcome_fingerprint, direct_event_id, direct_event_ordinal, "
                    "preimage FROM cpk_effect_attempt_outcomes WHERE attempt=1 "
                    "ORDER BY run_id, activity_id"
                ).fetchall()
            ),
        )


__all__ = [
    "FailedRunCompensationAttemptFixture",
    "TARGET_MODULE",
    "attempt_module",
]
