from __future__ import annotations

import importlib
import os
import unittest

import psycopg

from tests.graph_lineage_fixture import seed_identity_graphs

from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.operations import (
    ActivityEventKind,
    ActivityRunStatus,
    EffectAttemptFence,
    EffectAttemptIdentity,
    EffectAttemptState,
    EffectAttemptStatus,
    FailureCategory,
    RecoveryScope,
    RunId,
    fold_effect_attempt,
)
from control_plane_kit_core.planning import (
    ActivityDependency,
    ActivityId,
    ActivityPlan,
    NodeTarget,
    PlannedActivity,
    RiskLevel,
    RuntimeTarget,
    StartNode,
    StartRuntime,
    WaitForHealthy,
)
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectIntent,
    RuntimeEffectIntentSource,
    runtime_effect_intent_fingerprint,
)
from control_plane_kit_core.runtime_effects import RuntimeEffectKind
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_effects import RuntimeEffectResult
from control_plane_kit_operations.effect_attempts import (
    EffectAttemptEventEvidence,
    EffectAttemptRecord,
    effect_attempt_state_fingerprint,
)
from control_plane_kit_operations.effect_attempt_intent_evidence import (
    EffectAttemptIntentRecord,
)
from control_plane_kit_operations.effect_outcome_evidence import (
    EffectAttemptOutcomeRecord,
    ExecutionEffectOutcome,
    effect_outcome_transition,
)
from control_plane_kit_operations.execution_lease_recovery import RecoveryAuthority
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityPlanStatus,
    ActivityRunRecord,
    AdmittedRun,
    ApprovalDecisionKind,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    BoundedEvidence,
    ClaimIdentity,
    ExecutionIdempotency,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    ExecutionRequestStatus,
    FailureEvidence,
    OperationSessionRecord,
    OperationSessionStatus,
    RetryIdentity,
)
from control_plane_kit_operations.workflows import IdempotencyKey


TARGET_MODULE = "control_plane_kit_operations.failed_run_compensation"

try:
    compensation_module = importlib.import_module(TARGET_MODULE)
except ModuleNotFoundError as error:
    if error.name != TARGET_MODULE:
        raise
    compensation_module = None


class Sequence:
    def __init__(self, *values: str) -> None:
        self.values = list(values)
        self.calls: list[str] = []

    def __call__(self) -> str:
        value = self.values.pop(0)
        self.calls.append(value)
        return value


class FailedRunCompensationFixture:
    maxDiff = None

    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; run through Docker"
            )
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")

    def tearDown(self) -> None:
        if not self.connection.closed:
            self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
            self.connection.close()

    def require_contract(self):
        self.assertIsNotNone(
            compensation_module,
            "failed-run compensation Operations service is missing",
        )
        return compensation_module

    def unit_of_work(self):
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def service(self, sequence: Sequence):
        module = self.require_contract()
        return module.FailedRunCompensationCommandService(
            self.unit_of_work,
            clock=lambda: "2026-08-25T12:00:00Z",
            id_factory=sequence,
        )

    def command(self, **changes):
        module = self.require_contract()
        values = {
            "workspace_id": "workspace-a",
            "request_id": "request-a",
            "run_id": RunId("run-a"),
            "plan_id": "plan-a",
            "expected_current_graph_id": "graph-current",
            "desired_graph_id": "graph-desired",
            "expected_desired_graph_revision": 1,
            "execution_intent_fingerprint": "a" * 64,
            "authority": RecoveryAuthority(
                "operator-a",
                "authority-reference-a",
                (RecoveryScope.COMPENSATE,),
            ),
            "reason": module.FailedRunCompensationReason.POST_EFFECT_FAILURE,
            "source_failure": self.failure(),
            "idempotency_key": IdempotencyKey("compensate-a"),
        }
        values.update(changes)
        return module.BeginFailedRunCompensation(**values)

    def failure(self):
        return FailureEvidence(
            FailureCategory.TERMINAL,
            "runtime.effect-failed",
            "runtime effect reported failure",
            BoundedEvidence.from_mapping({"phase": "start"}),
        )

    def seed_truth(self) -> None:
        plan = ActivityPlan(
            (
                PlannedActivity(
                    ActivityId("start-runtime"),
                    StartRuntime(RuntimeTarget("runtime-a")),
                ),
                PlannedActivity(
                    ActivityId("start-node"),
                    StartNode(NodeTarget("node-a")),
                    (ActivityDependency(ActivityId("start-runtime")),),
                ),
                PlannedActivity(
                    ActivityId("wait-node"),
                    WaitForHealthy(NodeTarget("node-a")),
                    (ActivityDependency(ActivityId("start-node")),),
                ),
            )
        )
        self.connection.execute(
            "INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) "
            "VALUES ('workspace-a', 'Workspace A', 'created')"
        )
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            lineage = seed_identity_graphs(
                stores,
                workspace_id="workspace-a",
                graph_ids=("graph-current", "graph-desired"),
            )
            stores.activity_history.add_session(
                OperationSessionRecord(
                    "session-a",
                    "workspace-a",
                    "operator-a",
                    "Deploy",
                    OperationSessionStatus.OPEN,
                    "2026-08-25T11:50:00Z",
                )
            )
            stores.activity_history.add_plan(
                ActivityPlanRecord(
                    "plan-a",
                    "session-a",
                    "graph-current",
                    "graph-desired",
                    ActivityPlanStatus.PLANNED,
                    "2026-08-25T11:51:00Z",
                    plan,
                    base_realized_projection_id=lineage["graph-current"],
                    desired_realized_projection_id=lineage["graph-desired"],
                    desired_graph_revision=1,
                )
            )
            subject = ActivityPlanApprovalSubject("plan-a")
            stores.activity_history.add_approval_request(
                ApprovalRequestRecord(
                    "approval-request-a",
                    "session-a",
                    subject,
                    "operator-a",
                    "2026-08-25T11:52:00Z",
                    PolicyScope.PLAN_APPROVE,
                    RiskLevel.LOW,
                    False,
                )
            )
            stores.activity_history.add_approval_decision(
                ApprovalDecisionRecord(
                    "approval-decision-a",
                    "approval-request-a",
                    "manager-a",
                    ApprovalDecisionKind.APPROVED,
                    PolicyScope.PLAN_APPROVE,
                    "2026-08-25T11:53:00Z",
                )
            )
            stores.execution.add_request(
                ExecutionRequestRecord(
                    ExecutionRequestIdentity(
                        "request-a", "workspace-a", "session-a", "plan-a"
                    ),
                    ExecutionRequestStatus.CLAIMED,
                    "operator-a",
                    "2026-08-25T11:54:00Z",
                    "approval-request-a",
                    "approval-decision-a",
                    ExecutionIdempotency("execute-a", "a" * 64),
                    ClaimIdentity(
                        "worker-a",
                        1,
                        "2026-08-25T11:54:00Z",
                        "2026-08-25T13:54:00Z",
                    ),
                )
            )
            stores.execution.add_run(
                ActivityRunRecord(
                    "run-a",
                    "plan-a",
                    AdmittedRun("request-a"),
                    RetryIdentity(1),
                    ActivityRunStatus.FAILED,
                    "2026-08-25T11:54:10Z",
                    started_at="2026-08-25T11:54:20Z",
                )
            )
            events = (
                self.event("run-opened", 1, ActivityEventKind.RUN_OPENED),
                self.event("run-started", 2, ActivityEventKind.RUN_STARTED),
            )
            for event in events:
                stores.execution.add_event(event)
            self._add_success(stores, "start-runtime", 3, 4)
            self._add_success(stores, "start-node", 5, 6)
            stores.execution.add_event(
                self.event(
                    "wait-node-started",
                    7,
                    ActivityEventKind.STEP_STARTED,
                    "wait-node",
                )
            )
            stores.execution.add_event(
                self.event(
                    "wait-node-failed",
                    8,
                    ActivityEventKind.STEP_FAILED,
                    "wait-node",
                    failure=self.failure(),
                )
            )
            stores.execution.add_event(
                self.event(
                    "run-failed",
                    9,
                    ActivityEventKind.RUN_FAILED,
                    failure=self.failure(),
                )
            )
            unit_of_work.commit()
        self.connection.execute(
            "UPDATE cpk_workspaces SET current_graph_id='graph-current', "
            "desired_graph_id='graph-desired', desired_graph_revision=1, "
            "current_realized_projection_id=(SELECT projection_id FROM "
            "cpk_realized_graph_projections WHERE source_authored_graph_id="
            "'graph-current'), desired_realized_projection_id=(SELECT "
            "projection_id FROM cpk_realized_graph_projections WHERE "
            "source_authored_graph_id='graph-desired') WHERE workspace_id="
            "'workspace-a'"
        )

    def event(
        self,
        event_id: str,
        ordinal: int,
        kind: ActivityEventKind,
        activity_id: str | None = None,
        *,
        evidence: BoundedEvidence | None = None,
        failure: FailureEvidence | None = None,
    ) -> ActivityEventRecord:
        return ActivityEventRecord(
            event_id,
            "run-a",
            ordinal,
            kind,
            f"2026-08-25T11:55:{ordinal:02d}Z",
            activity_id=activity_id,
            evidence=evidence or BoundedEvidence(),
            failure=failure,
        )

    def _add_success(
        self,
        stores,
        activity_id: str,
        start_ordinal: int,
        success_ordinal: int,
    ) -> None:
        operation = (
            StartRuntime(RuntimeTarget("runtime-a"))
            if activity_id == "start-runtime"
            else StartNode(NodeTarget("node-a"))
        )
        intent = RuntimeEffectIntent(
            RuntimeEffectKind.REALIZE_ACTIVITY,
            RuntimeKind.DOCKER,
            RuntimeEffectIntentSource(
                "workspace-a",
                "request-a",
                RunId("run-a"),
                "plan-a",
                "graph-current",
                "graph-desired",
            ),
            ActivityId(activity_id),
            operation,
            None,
            (),
            (),
        )
        request_fingerprint = runtime_effect_intent_fingerprint(intent)
        identity = EffectAttemptIdentity(RunId("run-a"), activity_id, 1)
        fence = EffectAttemptFence("worker-a", 1)
        started = EffectAttemptState(
            identity,
            request_fingerprint,
            fence,
            EffectAttemptStatus.STARTED,
        )
        start_event = self.event(
            f"{activity_id}-started",
            start_ordinal,
            ActivityEventKind.STEP_STARTED,
            activity_id,
            evidence=BoundedEvidence.from_mapping(
                {
                    "effect_attempt": EffectAttemptEventEvidence(
                        1,
                        effect_attempt_state_fingerprint(started),
                    ).descriptor()
                }
            ),
        )
        outcome = ExecutionEffectOutcome(
            identity,
            request_fingerprint,
            RuntimeEffectResult.succeeded(
                start_event.event_id,
                evidence={"resource_fingerprint": activity_id},
            ),
        )
        succeeded = fold_effect_attempt(
            started,
            effect_outcome_transition(outcome),
            fence=fence,
        )
        success_event = self.event(
            f"{activity_id}-succeeded",
            success_ordinal,
            ActivityEventKind.STEP_SUCCEEDED,
            activity_id,
            evidence=BoundedEvidence.from_mapping(
                {
                    "effect_attempt": EffectAttemptEventEvidence(
                        1,
                        effect_attempt_state_fingerprint(succeeded),
                    ).descriptor()
                }
            ),
        )
        stores.execution.add_event(start_event)
        stores.execution.add_event(success_event)
        stores.effect_attempt_intents.insert(
            EffectAttemptIntentRecord(identity, start_event, intent)
        )
        attempt = EffectAttemptRecord(succeeded, start_event, success_event)
        stores.effect_attempts.insert_absent(attempt)
        stores.effect_outcomes.insert(
            EffectAttemptOutcomeRecord("workspace-a", outcome, attempt, ())
        )

    def original_truth(self):
        return (
            tuple(
                self.connection.execute(
                    "SELECT run_id, activity_id, attempt, status, "
                    "outcome_fingerprint, latest_event_id, latest_event_ordinal "
                    "FROM cpk_effect_attempts ORDER BY activity_id"
                ).fetchall()
            ),
            tuple(
                self.connection.execute(
                    "SELECT run_id, activity_id, attempt, status, "
                    "outcome_fingerprint, direct_event_id, direct_event_ordinal, "
                    "preimage FROM cpk_effect_attempt_outcomes ORDER BY activity_id"
                ).fetchall()
            ),
        )


__all__ = [
    "FailedRunCompensationFixture",
    "Sequence",
    "TARGET_MODULE",
    "compensation_module",
]
