from __future__ import annotations

import concurrent.futures
import json
import os
import queue
import time
import unittest
from dataclasses import replace
from typing import Any

import psycopg

from tests.lifecycle_lock_fixture import (
    LifecycleLockFixture, LIFECYCLE_LOCK, REQUEST_LOCK, RUN_LOCK,
    SESSION_LOCK, WORKSPACE_LOCK,
)

from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.operations import RunId
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
    LifecycleOperationKind,
)
from control_plane_kit_core.planning import (
    ActivityDependency, ActivityId, ActivityPlan, NodeTarget, RuntimeTarget, StartRuntime,
)
from control_plane_kit_core.planning import PlannedActivity, StartNode
from control_plane_kit_core.planning import RiskLevel
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_operations.advancement import (
    AdvanceCurrentGraph,
    CurrentGraphAdvancementCommandService,
    CurrentGraphAdvancementConflict,
    CurrentGraphAdvancementDenied,
    CurrentGraphAdvancementError,
    CurrentGraphAdvancementIdempotencyConflict,
    CurrentGraphAdvancementIncomplete,
    CurrentGraphAdvancementNotFound,
    CurrentGraphAdvancementResult,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.workspaces import CreateWorkspace, WorkspaceCommandService
from tests.accepted_graph_origin_fixture import accept_selected_fixture_origin
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityPlanStatus,
    ApprovalDecisionKind,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    ActivityRunRecord,
    AdmittedRun,
    BoundedEvidence,
    ClaimIdentity,
    ExecutionIdempotency,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    GraphVersionRecord,
    OperationActionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
    RealizedGraphProjectionRecord,
    RealizedGraphProjectionKind,
    RetryIdentity,
)
from control_plane_kit_operations.workflows import (
    CloseOperationSession,
    IdempotencyKey,
    InvalidOperationCommand,
    OperationCommandService,
)


class _RunTextSubclass(str):
    pass


INVALID_RUN_IDS = (
    (object(), ()),
    (True, ("True",)),
    (_RunTextSubclass("subclass-canary"), ("subclass-canary",)),
    ("", ()),
    (" ", ()),
    ("-leading-canary", ("leading-canary",)),
    (".leading-canary", ("leading-canary",)),
    ("_leading-canary", ("leading-canary",)),
    (":leading-canary", ("leading-canary",)),
    ("slash/canary", ("slash/canary",)),
    ("space canary", ("space canary",)),
    *tuple(
        (f"a{chr(code)}control-canary", ("control-canary",))
        for code in (*range(32), 127)
    ),
    ("a" * 201, ("a" * 32,)),
)


def _contract_authority() -> ExecutionWorkerAuthority:
    return ExecutionWorkerAuthority(
        "worker-a",
        (PolicyScope.EXECUTION_OPERATE,),
    )


def _contract_command(run_id: object) -> AdvanceCurrentGraph:
    return AdvanceCurrentGraph(
        workspace_id="workspace-a",
        run_id=run_id,
        plan_id="plan-a",
        expected_current_graph_id="graph-current",
        expected_current_realized_projection_id="projection-current",
        desired_graph_id="graph-desired",
        desired_realized_projection_id="projection-desired",
        expected_desired_graph_revision=1,
        authority=_contract_authority(),
        fence=ExecutionLeaseFence("worker-a", 1),
        idempotency_key=IdempotencyKey("advance-a"),
    )


def _contract_result(
    run_id: object,
    *,
    replayed: bool,
) -> CurrentGraphAdvancementResult:
    try:
        evidence_run_id = RunId(run_id).value
    except ValueError:
        evidence_run_id = "run-a"
    evidence = BoundedEvidence.from_mapping(
        {
            "workspace_id": "workspace-a",
            "plan_id": "plan-a",
            "run_id": evidence_run_id,
            "from_authored_graph_id": "graph-current",
            "from_realized_projection_id": "projection-current",
            "to_authored_graph_id": "graph-desired",
            "to_realized_projection_id": "projection-desired",
            "to_realized_projection_digest": "a" * 64,
            "desired_graph_revision": 1,
        }
    )
    event = ActivityEventRecord(
        "event-a",
        evidence_run_id,
        1,
        ActivityEventKind.CURRENT_GRAPH_ADVANCED,
        "2026-08-14T12:00:00Z",
        evidence=evidence,
    )
    action = OperationActionRecord(
        "action-a",
        "session-a",
        1,
        LifecycleOperationKind.ADVANCE_CURRENT_GRAPH,
        "worker-a",
        payload={
            **evidence.descriptor(),
            "execution_request_id": "request-a",
            "claim_generation": 1,
            "event_id": "event-a",
        },
        created_at="2026-08-14T12:00:00Z",
    )
    return CurrentGraphAdvancementResult(
        workspace_id="workspace-a",
        from_authored_graph_id="graph-current",
        from_realized_projection_id="projection-current",
        to_authored_graph_id="graph-desired",
        to_realized_projection_id="projection-desired",
        to_realized_projection_digest="a" * 64,
        desired_graph_revision=1,
        run_id=run_id,
        plan_id="plan-a",
        event=event,
        action=action,
        replayed=replayed,
    )


class CurrentGraphAdvancementContractTests(unittest.TestCase):
    def assert_invalid_run(self, callback, canaries: tuple[str, ...]) -> None:
        with self.assertRaises(Exception) as captured:
            callback()
        error = captured.exception
        self.assertIs(type(error), InvalidOperationCommand)
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = f"{error!s} {error!r}"
        self.assertLessEqual(len(rendered), 256)
        for canary in canaries:
            self.assertNotIn(canary, rendered)

    def test_command_run_identity_is_canonical_before_unit_of_work(self) -> None:
        for run_id in ("a", "a._:-0", "a" * 200):
            with self.subTest(run_id=run_id, boundary="valid"):
                self.assertEqual(_contract_command(run_id).run_id, run_id)
        for run_id, canaries in INVALID_RUN_IDS:
            with self.subTest(run_type=type(run_id).__name__):
                self.assert_invalid_run(
                    lambda run_id=run_id: _contract_command(run_id),
                    canaries,
                )

    def test_command_requires_exact_congruent_lease_fence(self) -> None:
        command = _contract_command("run-a")

        class HostileExecutionLeaseFence(ExecutionLeaseFence):
            pass

        self.assertEqual(command.fence, ExecutionLeaseFence("worker-a", 1))
        for fence in (
            object(),
            HostileExecutionLeaseFence("worker-a", 1),
            ExecutionLeaseFence("worker-b", 1),
        ):
            with self.subTest(fence_type=type(fence).__name__):
                with self.assertRaises(InvalidOperationCommand) as captured:
                    replace(command, fence=fence)
                self.assertIsNone(captured.exception.__cause__)
                self.assertIsNone(captured.exception.__context__)
                self.assertLessEqual(len(repr(captured.exception)), 256)

    def test_direct_and_replayed_results_share_canonical_run_admission(self) -> None:
        for replayed in (False, True):
            for run_id in ("a", "a._:-0", "a" * 200):
                with self.subTest(replayed=replayed, run_id=run_id):
                    result = _contract_result(run_id, replayed=replayed)
                    self.assertEqual(result.run_id, run_id)
                    self.assertEqual(result.descriptor()["run_id"], run_id)
            for run_id, canaries in INVALID_RUN_IDS:
                with self.subTest(replayed=replayed, run_type=type(run_id).__name__):
                    self.assert_invalid_run(
                        lambda run_id=run_id, replayed=replayed: _contract_result(
                            run_id,
                            replayed=replayed,
                        ),
                        canaries,
                    )


class Sequence:
    def __init__(self, *values: str) -> None:
        self._values = list(values)

    def __call__(self) -> str:
        return self._values.pop(0)


class CurrentGraphAdvancementTests(LifecycleLockFixture, unittest.TestCase):
    def test_advancement_and_lifecycle_retry_recovery_preserve_outcomes_in_both_orders(self):
        from control_plane_kit_core.operations.lifecycle import RecoveryScope
        from control_plane_kit_operations.lifecycle import RunLifecycleCommandService, RunLifecycleError, StartActivityRun, ExecutionLeaseDuration
        from control_plane_kit_operations.activity_run_retry import RetryFailedActivityRun
        from control_plane_kit_operations.activity_run_retry_interpreter import ActivityRunRetryCommandService
        from control_plane_kit_operations.execution_lease_recovery import RecoveryAuthority, RenewExpiredExecutionClaim
        from control_plane_kit_operations.execution_lease_recovery_interpreter import ExecutionLeaseRecoveryCommandService
        for kind in ("lifecycle", "retry", "recovery"):
            for advance_first in (False, True):
                with self.subTest(kind=kind, advance_first=advance_first):
                    self.reset_truth()
                    self.seed_succeeded_run()
                    def advance(uow):
                        return CurrentGraphAdvancementCommandService(uow,
                            clock=lambda: "2026-07-22T13:05:00Z",
                            id_factory=Sequence("advance-event", "advance-action")).execute(self.command())
                    def other(uow):
                        def no_id():
                            self.fail("refused terminal operation allocated identity")
                        fence = ExecutionLeaseFence("worker-a", 1)
                        key = IdempotencyKey("opposing-" + kind)
                        if kind == "lifecycle":
                            return RunLifecycleCommandService(uow, clock=lambda: "2026-07-22T13:05:00Z",
                                id_factory=no_id).execute(StartActivityRun("run-a", self.authority(), fence, key))
                        if kind == "retry":
                            return ActivityRunRetryCommandService(uow, id_factory=no_id).execute(
                                RetryFailedActivityRun("request-a", RunId("run-a"), fence,
                                    RecoveryAuthority("operator-a", "operator-proof", (RecoveryScope.OPERATE,)), key))
                        return ExecutionLeaseRecoveryCommandService(uow, id_factory=no_id).execute(
                            RenewExpiredExecutionClaim("request-a", RunId("run-a"), fence,
                                RecoveryAuthority("operator-a", "operator-proof", (RecoveryScope.RENEW_CLAIM,)),
                                ExecutionLeaseDuration(600), key))
                    ordered = (advance, other) if advance_first else (other, advance)
                    futures = self.opposing_commands(*ordered, pause_after=lambda sql, parameters:
                        "FROM cpk_execution_requests" in sql and "FOR UPDATE" in sql
                        and parameters == ("request-a",))
                    accepted, refused = futures if advance_first else futures[::-1]
                    result = accepted.result(timeout=1)
                    self.assertEqual(result.to_authored_graph_id, "graph-desired")
                    with self.assertRaises(RunLifecycleError):
                        refused.result(timeout=1)
                    self.assertEqual(self.advancement_truth()[2], 1)
                    with self.unit_of_work() as uow:
                        self.assertIs(uow.stores.execution.get_run("run-a").status, ActivityRunStatus.SUCCEEDED)
                        actions = tuple(action for action in uow.stores.activity_history.actions_for_session("session-a")
                            if action.action_type is LifecycleOperationKind.ADVANCE_CURRENT_GRAPH)
                        self.assertEqual(actions, (result.action,))

    def test_selection_and_publication_vs_advancement_preserve_cas_in_both_orders(self):
        from control_plane_kit_operations.desired_topology_drafts import (
            CreateDesiredTopologyDraft, SelectDesiredTopologyDraft, DesiredTopologyDraftCommandService,
        )
        from control_plane_kit_operations.desired_realized_projections import (
            PublishDesiredRealizedProjection, DesiredRealizedProjectionCommandService,
        )
        from tests.draft_catalogue_fixture import principal
        for kind in ("selection", "publication"):
            for advance_first in (False, True):
                with self.subTest(kind=kind, advance_first=advance_first):
                    self.reset_truth()
                    self.seed_succeeded_run()
                    draft_service = lambda uow: DesiredTopologyDraftCommandService(uow,
                        clock=lambda: "2026-07-22T13:05:00Z",
                        id_factory=Sequence("draft-a", "draft-graph", "draft-action", "selected-action"))
                    if kind == "selection":
                        draft = draft_service(self.unit_of_work).execute(CreateDesiredTopologyDraft(
                            principal().command_context("workspace-a"), "session-a", "Next draft",
                            DeploymentGraph("draft"), IdempotencyKey("create-draft")))
                        selected_graph = draft.graph_id
                        command = SelectDesiredTopologyDraft(principal().command_context("workspace-a"),
                            "session-a", draft.draft_id, draft.revision, "graph-desired",
                            self.desired_projection.projection_id, self.desired_graph_revision,
                            IdempotencyKey("select-draft"))
                        def publish(uow):
                            return DesiredTopologyDraftCommandService(uow,
                                clock=lambda: "2026-07-22T13:05:00Z",
                                id_factory=Sequence("selection-action")).execute(command)
                    else:
                        selected_graph = "graph-desired"
                        command = PublishDesiredRealizedProjection("session-a", "workspace-a", "operator-a",
                            "graph-desired", self.desired_projection.projection_id, self.desired_graph_revision,
                            self.desired_projection, "operation-publish", 1, IdempotencyKey("publish-new-generation"))
                        def publish(uow):
                            return DesiredRealizedProjectionCommandService(uow,
                                clock=lambda: "2026-07-22T13:05:00Z",
                                action_id_factory=lambda: "publication-action").execute(command)
                    def advance(uow):
                        return CurrentGraphAdvancementCommandService(uow,
                            clock=lambda: "2026-07-22T13:05:00Z",
                            id_factory=Sequence("advance-event", "advance-action")).execute(self.command())
                    ordered = (advance, publish) if advance_first else (publish, advance)
                    futures = self.opposing_commands(*ordered, pause_after=lambda sql, parameters:
                        "pg_advisory_xact_lock" in sql and parameters == ("receiver-lifecycle:workspace-a",))
                    advancement, publication = futures if advance_first else futures[::-1]
                    self.assertIsNotNone(publication.result(timeout=1))
                    if advance_first:
                        self.assertEqual(advancement.result(timeout=1).to_authored_graph_id, "graph-desired")
                    else:
                        with self.assertRaises(CurrentGraphAdvancementConflict):
                            advancement.result(timeout=1)
                    with self.unit_of_work() as uow:
                        workspace = uow.stores.workspaces.get("workspace-a")
                    self.assertEqual(workspace.desired_graph_id, selected_graph)
                    self.assertEqual(workspace.desired_graph_revision, self.desired_graph_revision + 1)
                    self.assertEqual(workspace.current_graph_id, "graph-desired" if advance_first else "graph-current")

    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required. Run "
                "./control-plane-kit-operations/test.sh so Docker starts Postgres."
            )
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        self.addCleanup(self.connection.close)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.addCleanup(self.connection.execute, "TRUNCATE TABLE cpk_workspaces CASCADE")
        self.seed_truth()

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def service(self, *ids: str) -> CurrentGraphAdvancementCommandService:
        return CurrentGraphAdvancementCommandService(
            self.unit_of_work,
            clock=lambda: "2026-07-22T13:05:00Z",
            id_factory=Sequence(*ids),
        )

    def wait_until_blocked_by(self, worker_pid: int, blocker_pid: int) -> None:
        deadline = time.monotonic() + 5
        while True:
            blocked_by = self.connection.execute(
                "SELECT pg_blocking_pids(%s)",
                (worker_pid,),
            ).fetchone()[0]
            if blocker_pid in blocked_by:
                return
            if time.monotonic() >= deadline:
                self.fail("advancement did not block on the expected row")

    def authority(
        self,
        worker_id: str = "worker-a",
        scopes: tuple[PolicyScope, ...] = (PolicyScope.EXECUTION_OPERATE,),
    ) -> ExecutionWorkerAuthority:
        return ExecutionWorkerAuthority(worker_id, scopes)

    def command(
        self,
        *,
        key: str = "advance-a",
        worker_id: str = "worker-a",
        generation: int = 1,
        scopes: tuple[PolicyScope, ...] = (PolicyScope.EXECUTION_OPERATE,),
        expected_current_graph_id: str = "graph-current",
        expected_current_realized_projection_id: str | None = None,
        desired_graph_id: str = "graph-desired",
        desired_realized_projection_id: str | None = None,
        expected_desired_graph_revision: int | None = None,
    ) -> AdvanceCurrentGraph:
        return AdvanceCurrentGraph(
            workspace_id="workspace-a",
            run_id="run-a",
            plan_id="plan-a",
            expected_current_graph_id=expected_current_graph_id,
            expected_current_realized_projection_id=(
                self.current_projection.projection_id
                if expected_current_realized_projection_id is None
                else expected_current_realized_projection_id
            ),
            desired_graph_id=desired_graph_id,
            desired_realized_projection_id=(
                self.desired_projection.projection_id
                if desired_realized_projection_id is None
                else desired_realized_projection_id
            ),
            expected_desired_graph_revision=(
                self.desired_graph_revision
                if expected_desired_graph_revision is None
                else expected_desired_graph_revision
            ),
            authority=self.authority(worker_id, scopes),
            fence=ExecutionLeaseFence(worker_id, generation),
            idempotency_key=IdempotencyKey(key),
        )

    def test_complete_durable_success_advances_current_graph_once(self) -> None:
        self.seed_succeeded_run()

        result = self.service("event-advance", "action-advance").execute(
            self.command()
        )
        OperationCommandService(
            self.unit_of_work,
            clock=lambda: "2026-07-22T13:06:00Z",
            id_factory=Sequence("action-close"),
        ).execute(
            CloseOperationSession(
                "session-a",
                "operator-a",
                IdempotencyKey("close"),
            )
        )
        replay = self.service("unused-event", "unused-action").execute(self.command())

        with self.unit_of_work() as unit_of_work:
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
            events = unit_of_work.stores.execution.events_for_run("run-a")
        self.assertEqual(workspace.current_graph_id, "graph-desired")
        self.assertEqual(
            workspace.current_realized_projection_id,
            self.desired_projection.projection_id,
        )
        self.assertEqual(
            result.to_realized_projection_digest,
            self.desired_projection.projection_digest,
        )
        self.assertEqual(
            result.event.evidence.descriptor(),
            {
                "workspace_id": "workspace-a",
                "plan_id": "plan-a",
                "run_id": "run-a",
                "from_authored_graph_id": "graph-current",
                "from_realized_projection_id": self.current_projection.projection_id,
                "to_authored_graph_id": "graph-desired",
                "to_realized_projection_id": self.desired_projection.projection_id,
                "to_realized_projection_digest": (
                    self.desired_projection.projection_digest
                ),
                "desired_graph_revision": self.desired_graph_revision,
            },
        )
        self.assertIs(result.event.kind, ActivityEventKind.CURRENT_GRAPH_ADVANCED)
        self.assertIs(result.action.action_type, LifecycleOperationKind.ADVANCE_CURRENT_GRAPH)
        self.assertEqual(result.action.payload["execution_request_id"], "request-a")
        self.assertEqual(result.action.payload["claim_generation"], 1)
        self.assertNotIn("claim_generation", result.event.evidence.descriptor())
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.event, result.event)
        self.assertEqual(replay.action, result.action)
        self.assertEqual(
            [event.kind for event in events],
            [
                ActivityEventKind.RUN_OPENED,
                ActivityEventKind.RUN_STARTED,
                ActivityEventKind.STEP_STARTED,
                ActivityEventKind.STEP_SUCCEEDED,
                ActivityEventKind.RUN_SUCCEEDED,
                ActivityEventKind.CURRENT_GRAPH_ADVANCED,
            ],
        )

    def test_foreign_stale_and_larger_fences_leave_no_advancement(self) -> None:
        cases = (
            ("foreign", "worker-b", 1, None),
            ("larger", "worker-a", 2, None),
            ("stale", "worker-a", 1, 2),
        )
        for label, worker_id, generation, stored_generation in cases:
            with self.subTest(label=label):
                self.reset_truth()
                self.seed_succeeded_run()
                if stored_generation is not None:
                    self.connection.execute(
                        "UPDATE cpk_execution_requests SET claim_generation = %s "
                        "WHERE request_id = 'request-a'",
                        (stored_generation,),
                    )

                with self.assertRaises(CurrentGraphAdvancementDenied):
                    self.service("unused-event", "unused-action").execute(
                        self.command(
                            key=f"advance-{label}",
                            worker_id=worker_id,
                            generation=generation,
                        )
                    )

                with self.unit_of_work() as unit_of_work:
                    stores = unit_of_work.stores
                    workspace = stores.workspaces.get("workspace-a")
                    events = stores.execution.events_for_run("run-a")
                    actions = stores.activity_history.actions_for_session("session-a")
                self.assertEqual(workspace.current_graph_id, "graph-current")
                self.assertEqual(
                    sum(
                        event.kind is ActivityEventKind.CURRENT_GRAPH_ADVANCED
                        for event in events
                    ),
                    0,
                )
                self.assertEqual(
                    sum(
                        action.action_type
                        is LifecycleOperationKind.ADVANCE_CURRENT_GRAPH
                        for action in actions
                    ),
                    0,
                )

    def test_old_same_key_replay_is_stale_after_generation_replacement(self) -> None:
        self.seed_succeeded_run()
        command = self.command()
        accepted = self.service("event-advance", "action-advance").execute(command)
        self.connection.execute(
            "UPDATE cpk_execution_requests SET claim_generation = 2 "
            "WHERE request_id = 'request-a'"
        )

        with self.assertRaises(CurrentGraphAdvancementDenied):
            self.service("unused-event", "unused-action").execute(command)

        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            workspace = stores.workspaces.get("workspace-a")
            events = stores.execution.events_for_run("run-a")
            actions = stores.activity_history.actions_for_session("session-a")
        self.assertEqual(workspace.current_graph_id, "graph-desired")
        self.assertEqual(
            sum(
                event.kind is ActivityEventKind.CURRENT_GRAPH_ADVANCED
                for event in events
            ),
            1,
        )
        self.assertEqual(
            sum(
                action.action_type is LifecycleOperationKind.ADVANCE_CURRENT_GRAPH
                for action in actions
            ),
            1,
        )
        self.assertEqual(actions[-1], accepted.action)

    def test_first_execution_locks_request_before_run(self) -> None:
        self.seed_succeeded_run()
        blocker = psycopg.connect(self.database_url)
        blocker.execute(
            "SELECT request_id FROM cpk_execution_requests "
            "WHERE request_id = 'request-a' FOR UPDATE"
        )
        blocker_pid = blocker.info.backend_pid
        worker_pids: queue.Queue[int] = queue.Queue()

        def connection_factory():
            connection = psycopg.connect(self.database_url)
            worker_pids.put(connection.info.backend_pid)
            return connection

        service = CurrentGraphAdvancementCommandService(
            lambda: PostgresUnitOfWork(connection_factory),
            clock=lambda: "2026-07-22T13:05:00Z",
            id_factory=Sequence("event-advance", "action-advance"),
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            try:
                future = executor.submit(service.execute, self.command())
                worker_pid = worker_pids.get(timeout=5)
                self.wait_until_blocked_by(worker_pid, blocker_pid)
                with psycopg.connect(self.database_url) as probe:
                    probe.execute(
                        "SELECT run_id FROM cpk_activity_runs "
                        "WHERE run_id = 'run-a' FOR UPDATE NOWAIT"
                    )
                blocker.commit()
                result = future.result(timeout=5)
            finally:
                blocker.rollback()
                blocker.close()

        self.assertEqual(result.to_authored_graph_id, "graph-desired")

    def test_first_execution_locks_request_before_session_and_workspace(self) -> None:
        # #1896 A2 strengthened law explicitly supersedes the old
        # workspace-before-request structural assertion; terminal truth survives.
        self.seed_succeeded_run()
        blocker = psycopg.connect(self.database_url)
        blocker.execute(
            "SELECT request_id FROM cpk_execution_requests "
            "WHERE request_id = 'request-a' FOR UPDATE"
        )
        blocker_pid = blocker.info.backend_pid
        worker_pids: queue.Queue[int] = queue.Queue()

        def connection_factory():
            connection = psycopg.connect(self.database_url)
            worker_pids.put(connection.info.backend_pid)
            return connection

        service = CurrentGraphAdvancementCommandService(
            lambda: PostgresUnitOfWork(connection_factory),
            clock=lambda: "2026-07-22T13:05:00Z",
            id_factory=Sequence("event-advance", "action-advance"),
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            try:
                future = executor.submit(service.execute, self.command())
                worker_pid = worker_pids.get(timeout=5)
                self.wait_until_blocked_by(worker_pid, blocker_pid)
                with psycopg.connect(self.database_url) as probe:
                    probe.execute(
                        "SELECT workspace_id FROM cpk_workspaces "
                        "WHERE workspace_id = 'workspace-a' FOR UPDATE NOWAIT"
                    )
                    probe.execute(
                        "SELECT session_id FROM cpk_operation_sessions "
                        "WHERE session_id = 'session-a' FOR UPDATE NOWAIT"
                    )
                    probe.execute(
                        "SELECT run_id FROM cpk_activity_runs "
                        "WHERE run_id = 'run-a' FOR UPDATE NOWAIT"
                    )
                blocker.commit()
                result = future.result(timeout=5)
            finally:
                blocker.rollback()
                blocker.close()

        self.assertEqual(result.to_authored_graph_id, "graph-desired")

    def test_fresh_advancement_takes_exact_guard_before_request_and_all_later_rows(self):
        self.seed_succeeded_run()
        def execute(uow):
            return CurrentGraphAdvancementCommandService(uow,
                clock=lambda: "2026-07-22T13:05:00Z",
                id_factory=Sequence("guard-event", "guard-action")).execute(self.command())
        with self.blocked_command(LIFECYCLE_LOCK, ("receiver-lifecycle:workspace-a",), execute) as future:
            for query, key in ((REQUEST_LOCK, "request-a"), (RUN_LOCK, "run-a"),
                               (SESSION_LOCK, "session-a"), (WORKSPACE_LOCK, "workspace-a")):
                self.assert_row_lockable(query, (key,))
            self.assert_advisory_available("operation-action:session-a:advance-a", available=False)
        self.assertEqual(future.result(timeout=1).to_authored_graph_id, "graph-desired")

    def test_replay_locks_request_before_run_but_changed_intent_locks_neither(self) -> None:
        self.seed_succeeded_run()
        command = self.command()
        self.service("event-advance", "action-advance").execute(command)

        changed_blocker = psycopg.connect(self.database_url)
        changed_blocker.execute(
            "SELECT request_id FROM cpk_execution_requests "
            "WHERE request_id = 'request-a' FOR UPDATE"
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            try:
                changed = executor.submit(
                    self.service("unused-event", "unused-action").execute,
                    replace(command, fence=ExecutionLeaseFence("worker-a", 2)),
                )
                with self.assertRaises(CurrentGraphAdvancementIdempotencyConflict):
                    changed.result(timeout=5)
            finally:
                changed_blocker.rollback()
                changed_blocker.close()

        replay_blocker = psycopg.connect(self.database_url)
        replay_blocker.execute(
            "SELECT request_id FROM cpk_execution_requests "
            "WHERE request_id = 'request-a' FOR UPDATE"
        )
        blocker_pid = replay_blocker.info.backend_pid
        replay_pids: queue.Queue[int] = queue.Queue()

        def connection_factory():
            connection = psycopg.connect(self.database_url)
            replay_pids.put(connection.info.backend_pid)
            return connection

        replay_service = CurrentGraphAdvancementCommandService(
            lambda: PostgresUnitOfWork(connection_factory),
            clock=lambda: "2026-07-22T14:00:00Z",
            id_factory=lambda: self.fail("replay allocated an identity"),
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            try:
                future = executor.submit(replay_service.execute, command)
                replay_pid = replay_pids.get(timeout=5)
                self.wait_until_blocked_by(replay_pid, blocker_pid)
                with psycopg.connect(self.database_url) as probe:
                    probe.execute(
                        "SELECT run_id FROM cpk_activity_runs "
                        "WHERE run_id = 'run-a' FOR UPDATE NOWAIT"
                    )
                replay_blocker.commit()
                replay = future.result(timeout=5)
            finally:
                replay_blocker.rollback()
                replay_blocker.close()

        self.assertTrue(replay.replayed)

    def test_replay_rejects_malformed_or_incongruent_action_evidence(self) -> None:
        self.seed_succeeded_run()
        command = self.command()
        accepted = self.service("event-advance", "action-advance").execute(command)
        original = dict(accepted.action.payload)
        mutations = (
            ("workspace_id", "workspace-canary"),
            ("plan_id", "plan-canary"),
            ("execution_request_id", None),
            ("execution_request_id", "request-canary"),
            ("run_id", "run-canary"),
            ("from_authored_graph_id", "from-canary"),
            ("from_realized_projection_id", "from-projection-canary"),
            ("to_authored_graph_id", "to-canary"),
            ("to_realized_projection_id", "to-projection-canary"),
            ("to_realized_projection_digest", "digest-canary"),
            ("desired_graph_revision", True),
            ("claim_generation", None),
            ("claim_generation", 2),
            ("event_id", "event-canary"),
        )
        for key, candidate in mutations:
            with self.subTest(key=key, candidate=candidate):
                payload = dict(original)
                if candidate is None:
                    payload.pop(key)
                else:
                    payload[key] = candidate
                self.connection.execute(
                    "UPDATE cpk_operation_actions SET payload = %s::jsonb "
                    "WHERE action_id = 'action-advance'",
                    (json.dumps(payload),),
                )

                with self.assertRaises(CurrentGraphAdvancementError) as captured:
                    self.service("unused-event", "unused-action").execute(command)
                self.assertIsNone(captured.exception.__cause__)
                self.assertIsNone(captured.exception.__context__)
                rendered = repr(captured.exception)
                self.assertLessEqual(len(rendered), 256)
                self.assertNotIn("canary", rendered)

                self.connection.execute(
                    "UPDATE cpk_operation_actions SET payload = %s::jsonb "
                    "WHERE action_id = 'action-advance'",
                    (json.dumps(original),),
                )

        self.connection.execute(
            "UPDATE cpk_operation_actions SET actor_id = 'actor-canary' "
            "WHERE action_id = 'action-advance'"
        )
        with self.assertRaises(CurrentGraphAdvancementError) as captured:
            self.service("unused-event", "unused-action").execute(command)
        self.assertIsNone(captured.exception.__cause__)
        self.assertIsNone(captured.exception.__context__)
        self.assertNotIn("actor-canary", repr(captured.exception))
        self.connection.execute(
            "UPDATE cpk_operation_actions SET actor_id = 'worker-a' "
            "WHERE action_id = 'action-advance'"
        )

        event_evidence = accepted.event.evidence.descriptor()
        event_evidence["workspace_id"] = "event-workspace-canary"
        self.connection.execute(
            "UPDATE cpk_activity_events "
            "SET payload = jsonb_set(payload, '{evidence}', %s::jsonb) "
            "WHERE event_id = 'event-advance'",
            (json.dumps(event_evidence),),
        )
        with self.assertRaises(CurrentGraphAdvancementError) as captured:
            self.service("unused-event", "unused-action").execute(command)
        self.assertIsNone(captured.exception.__cause__)
        self.assertIsNone(captured.exception.__context__)
        self.assertNotIn("event-workspace-canary", repr(captured.exception))

    def test_replay_rejects_run_reassigned_to_another_compatible_request(self) -> None:
        self.seed_succeeded_run()
        command = self.command()
        self.service("event-advance", "action-advance").execute(command)
        accepted_truth = self.advancement_truth()
        self.connection.execute(
            "UPDATE cpk_execution_requests "
            "SET status = 'abandoned', claim_worker_id = NULL, "
            "claim_generation = NULL, claimed_at = NULL, lease_expires_at = NULL "
            "WHERE request_id = 'request-a'"
        )
        from tests.receiver_scope_history_fixture import insert_recorded_request
        insert_recorded_request(self.connection, request_id="request-b", status="claimed",
            requested_at="2026-07-22T13:06:00Z", idempotency_key="execute-b", intent_fingerprint="fingerprint-b",
            claim_worker_id="worker-a", claim_generation=1, claimed_at="2026-07-22T13:06:30Z",
            lease_expires_at="2026-07-22T13:16:30Z")
        self.connection.execute(
            "UPDATE cpk_activity_runs SET request_id = 'request-b' "
            "WHERE run_id = 'run-a'"
        )

        with self.assertRaises(CurrentGraphAdvancementError) as captured:
            self.service("unused-event", "unused-action").execute(command)

        self.assertIsNone(captured.exception.__cause__)
        self.assertIsNone(captured.exception.__context__)
        self.assertEqual(self.advancement_truth(), accepted_truth)

    def test_replay_translates_malformed_persisted_action(self) -> None:
        self.seed_succeeded_run()
        command = self.command()
        self.service("event-advance", "action-advance").execute(command)
        self.connection.execute(
            "UPDATE cpk_operation_actions "
            "SET payload = '[\"action-evidence-canary\"]'::jsonb "
            "WHERE action_id = 'action-advance'"
        )

        with self.assertRaises(CurrentGraphAdvancementError) as captured:
            self.service("unused-event", "unused-action").execute(command)

        self.assertIsNone(captured.exception.__cause__)
        self.assertIsNone(captured.exception.__context__)
        rendered = f"{captured.exception!s} {captured.exception!r}"
        self.assertLessEqual(len(rendered), 256)
        self.assertNotIn("action-evidence-canary", rendered)

    def test_replay_rejects_malformed_or_incongruent_persisted_event(self) -> None:
        self.seed_succeeded_run()
        command = self.command()
        accepted = self.service("event-advance", "action-advance").execute(command)
        cases = (
            (
                {"evidence": 1, "event-evidence-canary": True},
                "event-evidence-canary",
            ),
            (
                {
                    "evidence": accepted.event.evidence.descriptor(),
                    "failure": {
                        "category": "terminal",
                        "code": "event-failure-canary",
                        "message": "event failure canary",
                        "details": 1,
                    },
                },
                "event-failure-canary",
            ),
            (
                {
                    "evidence": accepted.event.evidence.descriptor(),
                    "failure": {
                        "category": "terminal",
                        "code": "event-valid-failure-canary",
                        "message": "event valid failure canary",
                        "details": {},
                    },
                },
                "event-valid-failure-canary",
            ),
        )
        for payload, canary in cases:
            with self.subTest(canary=canary):
                self.connection.execute(
                    "UPDATE cpk_activity_events SET payload = %s::jsonb "
                    "WHERE event_id = 'event-advance'",
                    (json.dumps(payload),),
                )

                with self.assertRaises(CurrentGraphAdvancementError) as captured:
                    self.service("unused-event", "unused-action").execute(command)

                self.assertIsNone(captured.exception.__cause__)
                self.assertIsNone(captured.exception.__context__)
                rendered = f"{captured.exception!s} {captured.exception!r}"
                self.assertLessEqual(len(rendered), 256)
                self.assertNotIn(canary, rendered)

    def test_closed_session_cannot_publish_a_new_advancement(self) -> None:
        self.seed_succeeded_run()
        OperationCommandService(
            self.unit_of_work,
            clock=lambda: "2026-07-22T13:04:00Z",
            id_factory=Sequence("action-close"),
        ).execute(
            CloseOperationSession(
                "session-a",
                "operator-a",
                IdempotencyKey("close"),
            )
        )

        with self.assertRaisesRegex(CurrentGraphAdvancementConflict, "open session"):
            self.service("event-advance", "action-advance").execute(self.command())

        with self.unit_of_work() as unit_of_work:
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
            events = unit_of_work.stores.execution.events_for_run("run-a")
            self.assertEqual(workspace.current_graph_id, "graph-current")
            self.assertNotIn(
                ActivityEventKind.CURRENT_GRAPH_ADVANCED,
                tuple(event.kind for event in events),
            )
            unit_of_work.commit()

    def test_incomplete_uncertain_or_failed_evidence_cannot_advance(self) -> None:
        for step_kind in (
            ActivityEventKind.STEP_UNCERTAIN,
            ActivityEventKind.STEP_UNSUPPORTED,
            ActivityEventKind.STEP_FAILED,
        ):
            with self.subTest(step_kind=step_kind):
                self.reset_truth()
                self.seed_succeeded_run(step_kind=step_kind)

                with self.assertRaises(CurrentGraphAdvancementIncomplete):
                    self.service("unused-event", "unused-action").execute(
                        self.command()
                    )

                with self.unit_of_work() as unit_of_work:
                    workspace = unit_of_work.stores.workspaces.get("workspace-a")
                    actions = unit_of_work.stores.activity_history.actions_for_session(
                        "session-a"
                    )
                self.assertEqual(workspace.current_graph_id, "graph-current")
                self.assertEqual(actions, self.admission_actions)

    def test_scope_worker_and_stale_graph_fail_closed(self) -> None:
        self.seed_succeeded_run()

        with self.assertRaises(CurrentGraphAdvancementDenied):
            self.service("unused-event", "unused-action").execute(
                self.command(scopes=())
            )
        with self.assertRaises(CurrentGraphAdvancementDenied):
            self.service("unused-event", "unused-action").execute(
                self.command(worker_id="worker-b", key="advance-worker-b")
            )
        with self.assertRaises(CurrentGraphAdvancementConflict):
            self.service("unused-event", "unused-action").execute(
                self.command(
                    key="advance-stale",
                    expected_current_graph_id="graph-stale",
                )
            )

        with self.unit_of_work() as unit_of_work:
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
        self.assertEqual(workspace.current_graph_id, "graph-current")

    def test_stale_realized_lineage_and_revision_fail_closed(self) -> None:
        self.seed_succeeded_run()

        for index, overrides in enumerate((
            {"expected_current_realized_projection_id": "projection-stale"},
            {"desired_realized_projection_id": "projection-stale"},
            {"expected_desired_graph_revision": self.desired_graph_revision + 1},
        )):
            with self.subTest(overrides=overrides):
                with self.assertRaises(CurrentGraphAdvancementConflict):
                    self.service("unused-event", "unused-action").execute(
                        self.command(key=f"stale-{index}", **overrides)
                    )

        with self.unit_of_work() as unit_of_work:
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
            events = unit_of_work.stores.execution.events_for_run("run-a")
        self.assertEqual(workspace.current_graph_id, "graph-current")
        self.assertEqual(
            workspace.current_realized_projection_id,
            self.current_projection.projection_id,
        )
        self.assertFalse(
            any(event.kind is ActivityEventKind.CURRENT_GRAPH_ADVANCED for event in events)
        )

    def test_changed_idempotent_intent_conflicts_without_second_event(self) -> None:
        self.seed_succeeded_run()
        self.service("event-advance", "action-advance").execute(self.command())

        with self.assertRaises(CurrentGraphAdvancementIdempotencyConflict):
            self.service("unused-event", "unused-action").execute(
                self.command(worker_id="worker-b")
            )

        with self.unit_of_work() as unit_of_work:
            events = unit_of_work.stores.execution.events_for_run("run-a")
        self.assertEqual(
            sum(event.kind is ActivityEventKind.CURRENT_GRAPH_ADVANCED for event in events),
            1,
        )

    def test_wrong_workspace_plan_or_run_fails_closed(self) -> None:
        self.seed_succeeded_run()

        with self.assertRaises(CurrentGraphAdvancementNotFound) as missing_workspace:
            self.service("unused-event", "unused-action").execute(
                AdvanceCurrentGraph(
                    workspace_id="workspace-missing",
                    run_id="run-a",
                    plan_id="plan-a",
                    expected_current_graph_id="graph-current",
                    expected_current_realized_projection_id=(
                        self.current_projection.projection_id
                    ),
                    desired_graph_id="graph-desired",
                    desired_realized_projection_id=(
                        self.desired_projection.projection_id
                    ),
                    expected_desired_graph_revision=self.desired_graph_revision,
                    authority=self.authority(),
                    fence=ExecutionLeaseFence("worker-a", 1),
                    idempotency_key=IdempotencyKey("wrong-workspace"),
                )
            )
        with self.assertRaises(CurrentGraphAdvancementNotFound) as missing_run:
            self.service("unused-event", "unused-action").execute(
                replace(
                    self.command(key="wrong-run"),
                    run_id="run-missing-canary",
                )
            )
        with self.assertRaises(CurrentGraphAdvancementNotFound) as missing_plan:
            self.service("unused-event", "unused-action").execute(
                replace(
                    self.command(key="wrong-plan"),
                    plan_id="plan-missing-canary",
                )
            )
        for captured in (missing_workspace, missing_run, missing_plan):
            self.assertIsNone(captured.exception.__cause__)
            self.assertIsNone(captured.exception.__context__)
            rendered = repr(captured.exception)
            self.assertLessEqual(len(rendered), 256)
            self.assertNotIn("canary", rendered)

    def test_stable_authored_graph_advances_a_to_overlap_to_b(self) -> None:
        plan = ActivityPlan(
            (PlannedActivity(ActivityId("start-api"), StartNode(NodeTarget("api"))),)
        )
        WorkspaceCommandService(self.unit_of_work,
            clock=lambda: "2026-07-22T10:00:00Z", id_factory=lambda: "stable-origin-graph").create(
                CreateWorkspace("workspace-rotation", "Rotation", "operator-a", IdempotencyKey("stable-origin-create")))
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            reference = self.register_origin_product(stores, "workspace-rotation")
            authored = stores.graphs.save(
                GraphVersionRecord.from_graph(
                    graph_id="graph-stable",
                    workspace_id="workspace-rotation",
                    version=2,
                    graph=DeploymentGraph("stable-authored"),
                    created_by="operator-a",
                    created_at="2026-07-22T10:00:00Z",
                )
            )
            projections = tuple(
                stores.realized_graphs.save(
                    RealizedGraphProjectionRecord.from_graph(
                        projection_id=f"projection-{key}",
                        workspace_id="workspace-rotation",
                        source_authored_graph_id=authored.graph_id,
                        projection_kind=(
                            RealizedGraphProjectionKind.DELEGATION_VERIFIER
                        ),
                        projection_key=key,
                        graph=self.origin_graph(f"realized-{key}", reference),
                        created_by="rotation-program",
                        created_at="2026-07-22T10:00:30Z",
                    )
                )
                for key in ("a", "a-plus-b", "b")
            )
            stores.workspaces.set_desired_graph(
                "workspace-rotation",
                authored.graph_id,
                projections[0].projection_id,
            )
            unit_of_work.commit()
        accept_selected_fixture_origin(self, self.origin_plan(), workspace_id="workspace-rotation", prefix="stable-origin")
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            workspace = stores.workspaces.set_desired_graph(
                "workspace-rotation",
                authored.graph_id,
                projections[1].projection_id,
            )
            self._seed_execution(
                stores,
                suffix="overlap",
                workspace_id="workspace-rotation",
                plan=plan,
                base_graph_id=authored.graph_id,
                desired_graph_id=authored.graph_id,
                base_projection_id=projections[0].projection_id,
                desired_projection_id=projections[1].projection_id,
                desired_revision=workspace.desired_graph_revision,
            )
            unit_of_work.commit()

        self._admit_recorded_execution("overlap", "workspace-rotation")
        overlap = self.service("event-overlap-advance", "action-overlap-advance").execute(
            AdvanceCurrentGraph(
                workspace_id="workspace-rotation",
                run_id="run-overlap",
                plan_id="plan-overlap",
                expected_current_graph_id="graph-stable",
                expected_current_realized_projection_id="projection-a",
                desired_graph_id="graph-stable",
                desired_realized_projection_id="projection-a-plus-b",
                expected_desired_graph_revision=workspace.desired_graph_revision,
                authority=self.authority(),
                fence=ExecutionLeaseFence("worker-a", 1),
                idempotency_key=IdempotencyKey("advance-overlap"),
            )
        )
        self.assertEqual(overlap.from_authored_graph_id, "graph-stable")
        self.assertEqual(overlap.to_authored_graph_id, "graph-stable")

        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            current = stores.workspaces.get_for_update("workspace-rotation")
            next_workspace = stores.workspaces.compare_and_set_desired_projection(
                "workspace-rotation",
                expected_authored_graph_id="graph-stable",
                expected_realized_projection_id="projection-a-plus-b",
                expected_revision=current.desired_graph_revision,
                replacement_realized_projection_id="projection-b",
            )
            self.assertIsNotNone(next_workspace)
            self._seed_execution(
                stores,
                suffix="b",
                workspace_id="workspace-rotation",
                plan=plan,
                base_graph_id="graph-stable",
                desired_graph_id="graph-stable",
                base_projection_id="projection-a-plus-b",
                desired_projection_id="projection-b",
                desired_revision=next_workspace.desired_graph_revision,
            )
            unit_of_work.commit()

        self._admit_recorded_execution("b", "workspace-rotation")
        final = self.service("event-b-advance", "action-b-advance").execute(
            AdvanceCurrentGraph(
                workspace_id="workspace-rotation",
                run_id="run-b",
                plan_id="plan-b",
                expected_current_graph_id="graph-stable",
                expected_current_realized_projection_id="projection-a-plus-b",
                desired_graph_id="graph-stable",
                desired_realized_projection_id="projection-b",
                expected_desired_graph_revision=next_workspace.desired_graph_revision,
                authority=self.authority(),
                fence=ExecutionLeaseFence("worker-a", 1),
                idempotency_key=IdempotencyKey("advance-b"),
            )
        )

        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            accepted = stores.workspaces.get("workspace-rotation")
            authored_readback = stores.graphs.get("graph-stable")
        self.assertEqual(accepted.current_graph_id, "graph-stable")
        self.assertEqual(accepted.desired_graph_id, "graph-stable")
        self.assertEqual(accepted.current_realized_projection_id, "projection-b")
        self.assertEqual(
            authored_readback.graph_descriptor,
            authored.graph_descriptor,
        )
        self.assertNotIn(
            "delegation_verifier_projection",
            str(authored_readback.graph_descriptor),
        )
        self.assertEqual(final.to_realized_projection_id, "projection-b")
        self.assertEqual(
            final.to_realized_projection_digest,
            projections[2].projection_digest,
        )

    def test_late_action_failure_rolls_back_pointer_and_event(self) -> None:
        self.seed_succeeded_run()
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.activity_history.add_action(
                OperationActionRecord(
                    "action-duplicate",
                    "session-a",
                    unit_of_work.stores.activity_history.next_action_ordinal("session-a"),
                    LifecycleOperationKind.START_RUN,
                    "worker-a",
                    created_at="2026-07-22T13:04:00Z",
                    idempotency_key="existing",
                    intent_fingerprint="existing",
                )
            )
            unit_of_work.commit()

        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.service("event-advance", "action-duplicate").execute(self.command())

        with self.unit_of_work() as unit_of_work:
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
            events = unit_of_work.stores.execution.events_for_run("run-a")
        self.assertEqual(workspace.current_graph_id, "graph-current")
        self.assertEqual(
            sum(event.kind is ActivityEventKind.CURRENT_GRAPH_ADVANCED for event in events),
            0,
        )

    def test_concurrent_advancement_has_one_winner(self) -> None:
        self.seed_succeeded_run()

        def advance(label: str) -> str:
            try:
                result = self.service(
                    f"event-{label}",
                    f"action-{label}",
                ).execute(self.command(key=f"advance-{label}"))
                return result.action.action_id
            except CurrentGraphAdvancementConflict:
                return "conflict"

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(advance, ("one", "two")))

        with self.unit_of_work() as unit_of_work:
            workspace = unit_of_work.stores.workspaces.get("workspace-a")
            events = unit_of_work.stores.execution.events_for_run("run-a")
        self.assertEqual(sum(result != "conflict" for result in results), 1)
        self.assertEqual(workspace.current_graph_id, "graph-desired")
        self.assertEqual(
            sum(event.kind is ActivityEventKind.CURRENT_GRAPH_ADVANCED for event in events),
            1,
        )

    def origin_plan(self):
        runtime = PlannedActivity(ActivityId("advancement-origin-runtime"), StartRuntime(RuntimeTarget("runtime-a")))
        return ActivityPlan((runtime, PlannedActivity(ActivityId("advancement-origin-api"), StartNode(NodeTarget("api")),
            dependencies=(ActivityDependency(runtime.activity_id),))))

    def register_origin_product(self, stores, workspace_id="workspace-a"):
        from control_plane_kit_core.products import (
            ContainerServerProduct, OciImageReference, ProductDescriptorCodec, ProductIdentity, ProductRuntimeContract,
        )
        from control_plane_kit_operations.products import InlineDescriptorSource
        document = ProductDescriptorCodec().encode_document(ContainerServerProduct(
            identity=ProductIdentity("control-plane-kit", "advancement-fixture", 1),
            image=OciImageReference("ghcr.io", "openj92/advancement-fixture", "sha256:" + "a" * 64),
            runtime_contract=ProductRuntimeContract()))
        return stores.registered_products.register(workspace_id=workspace_id, descriptor_document=document,
            source=InlineDescriptorSource(), imported_by="operator-a", imported_at="2026-07-22T10:00:00Z").reference

    def origin_graph(self, name, reference):
        from tests.graph_lineage_fixture import execution_graph
        graph = execution_graph(name, node_ids=("api",))
        return replace(graph, nodes={"api": replace(graph.nodes["api"], metadata={
            "product_identity": reference.identity.key,
            "product_descriptor_digest": reference.descriptor_sha256.value})})

    def seed_truth(self) -> None:
        from tests.receiver_scope_history_fixture import admit_fixture_plan
        plan = ActivityPlan(
            (PlannedActivity(ActivityId("start-api"), StartNode(NodeTarget("api"))),)
        )
        WorkspaceCommandService(self.unit_of_work,
            clock=lambda: "2026-07-22T10:00:00Z", id_factory=lambda: "advancement-origin-graph").create(
                CreateWorkspace("workspace-a", "Workspace A", "operator-a", IdempotencyKey("advancement-origin-create")))
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            reference = self.register_origin_product(stores)
            stores.graphs.save(
                GraphVersionRecord.from_graph(
                    graph_id="graph-current",
                    workspace_id="workspace-a",
                    version=2,
                    graph=self.origin_graph("current", reference),
                    created_by="operator-a",
                    created_at="2026-07-22T10:00:30Z",
                )
            )
            stores.workspaces.set_desired_graph("workspace-a", "graph-current")
            unit_of_work.commit()
        self.origin_acceptance = accept_selected_fixture_origin(self, self.origin_plan(), prefix="advancement-origin")
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.graphs.save(
                GraphVersionRecord.from_graph(
                    graph_id="graph-desired",
                    workspace_id="workspace-a",
                    version=3,
                    graph=self.origin_graph("desired", reference),
                    created_by="operator-a",
                    created_at="2026-07-22T12:00:30Z",
                )
            )
            stores.workspaces.set_desired_graph("workspace-a", "graph-desired")
            workspace = stores.workspaces.get("workspace-a")
            self.current_projection = stores.realized_graphs.get(
                workspace.current_realized_projection_id
            )
            self.desired_projection = stores.realized_graphs.get(
                workspace.desired_realized_projection_id
            )
            self.desired_graph_revision = workspace.desired_graph_revision
            stores.activity_history.add_session(
                OperationSessionRecord(
                    "session-a",
                    "workspace-a",
                    "operator-a",
                    "Deploy",
                    OperationSessionStatus.OPEN,
                    "2026-07-22T12:01:00Z",
                )
            )
            stores.activity_history.add_plan(
                ActivityPlanRecord(
                    "plan-a",
                    "session-a",
                    "graph-current",
                    "graph-desired",
                    ActivityPlanStatus.PLANNED,
                    "2026-07-22T12:02:00Z",
                    plan,
                    base_realized_projection_id=(
                        self.current_projection.projection_id
                    ),
                    desired_realized_projection_id=(
                        self.desired_projection.projection_id
                    ),
                    desired_graph_revision=self.desired_graph_revision,
                )
            )
            stores.activity_history.add_approval_request(
                ApprovalRequestRecord(
                    "approval-request-a",
                    "session-a",
                    ActivityPlanApprovalSubject("plan-a"),
                    "operator-a",
                    "2026-07-22T12:03:00Z",
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
                    "2026-07-22T12:03:30Z",
                )
            )
            unit_of_work.commit()

        admitted = admit_fixture_plan(self)
        self.admission_actions = (admitted.action,)
        self.connection.execute(
            "UPDATE cpk_execution_requests SET status='claimed', claim_worker_id='worker-a', claim_generation=1, "
            "claimed_at='2026-07-22T12:04:30Z', lease_expires_at='2026-07-22T12:14:30Z' WHERE request_id='request-a'")

    def reset_truth(self) -> None:
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.seed_truth()

    def seed_succeeded_run(
        self,
        *,
        step_kind: ActivityEventKind = ActivityEventKind.STEP_SUCCEEDED,
        activity_id: str = "start-api",
    ) -> None:
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.execution._add_run(
                ActivityRunRecord(
                    "run-a",
                    "plan-a",
                    AdmittedRun("request-a"),
                    RetryIdentity(1),
                    ActivityRunStatus.SUCCEEDED,
                    "2026-07-22T13:00:00Z",
                    started_at="2026-07-22T13:00:30Z",
                    settled_at="2026-07-22T13:04:00Z",
                )
            )
            for ordinal, (kind, event_activity_id) in enumerate(
                (
                    (ActivityEventKind.RUN_OPENED, None),
                    (ActivityEventKind.RUN_STARTED, None),
                    (ActivityEventKind.STEP_STARTED, activity_id),
                    (step_kind, activity_id),
                    (ActivityEventKind.RUN_SUCCEEDED, None),
                ),
                start=1,
            ):
                stores.execution.add_event(
                    ActivityEventRecord(
                        f"event-{ordinal}",
                        "run-a",
                        ordinal,
                        kind,
                        f"2026-07-22T13:00:{ordinal:02d}Z",
                        activity_id=event_activity_id,
                        evidence=BoundedEvidence.from_mapping({"seed": "test"}),
                    )
                )
            unit_of_work.commit()

    def advancement_truth(self) -> tuple[object, ...]:
        return self.connection.execute(
            "SELECT current_graph_id, current_realized_projection_id, "
            "(SELECT COUNT(*) FROM cpk_activity_events "
            " WHERE event_type = 'current_graph_advanced' AND run_id = 'run-a'), "
            "(SELECT COUNT(*) FROM cpk_operation_actions "
            " WHERE action_type = 'advance-current-graph' AND session_id = 'session-a') "
            "FROM cpk_workspaces WHERE workspace_id = 'workspace-a'"
        ).fetchone()

    def _seed_execution(
        self,
        stores: Any,
        *,
        suffix: str,
        workspace_id: str,
        plan: ActivityPlan,
        base_graph_id: str,
        desired_graph_id: str,
        base_projection_id: str,
        desired_projection_id: str,
        desired_revision: int,
    ) -> None:
        session_id = f"session-{suffix}"
        plan_id = f"plan-{suffix}"
        request_id = f"request-{suffix}"
        run_id = f"run-{suffix}"
        stores.activity_history.add_session(
            OperationSessionRecord(
                session_id,
                workspace_id,
                "operator-a",
                "Deploy",
                OperationSessionStatus.OPEN,
                "2026-07-22T12:01:00Z",
            )
        )
        stores.activity_history.add_plan(
            ActivityPlanRecord(
                plan_id,
                session_id,
                base_graph_id,
                desired_graph_id,
                ActivityPlanStatus.PLANNED,
                "2026-07-22T12:02:00Z",
                plan,
                base_realized_projection_id=base_projection_id,
                desired_realized_projection_id=desired_projection_id,
                desired_graph_revision=desired_revision,
            )
        )
        stores.activity_history.add_approval_request(
            ApprovalRequestRecord(
                f"approval-request-{suffix}",
                session_id,
                ActivityPlanApprovalSubject(plan_id),
                "operator-a",
                "2026-07-22T12:03:00Z",
                PolicyScope.PLAN_APPROVE,
                RiskLevel.LOW,
                False,
            )
        )
        stores.activity_history.add_approval_decision(
            ApprovalDecisionRecord(
                f"approval-decision-{suffix}",
                f"approval-request-{suffix}",
                "manager-a",
                ApprovalDecisionKind.APPROVED,
                PolicyScope.PLAN_APPROVE,
                "2026-07-22T12:03:30Z",
            )
        )

    def _admit_recorded_execution(self, suffix, workspace_id):
        from tests.receiver_scope_history_fixture import admit_fixture_plan
        request_id, plan_id, run_id = f"request-{suffix}", f"plan-{suffix}", f"run-{suffix}"
        admit_fixture_plan(self, workspace_id=workspace_id, session_id=f"session-{suffix}",
            plan_id=plan_id, request_id=request_id, approval_request_id=f"approval-request-{suffix}", key=f"execute-{suffix}")
        self.connection.execute(
            "UPDATE cpk_execution_requests SET status='claimed', claim_worker_id='worker-a', claim_generation=1, "
            "claimed_at='2026-07-22T12:04:30Z', lease_expires_at='2026-07-22T12:14:30Z' WHERE request_id=%s", (request_id,))
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.execution._add_run(
                ActivityRunRecord(
                    run_id,
                    plan_id,
                    AdmittedRun(request_id),
                    RetryIdentity(1),
                    ActivityRunStatus.SUCCEEDED,
                    "2026-07-22T13:00:00Z",
                    started_at="2026-07-22T13:00:30Z",
                    settled_at="2026-07-22T13:04:00Z",
                )
            )
            for ordinal, (kind, activity_id) in enumerate(
                (
                    (ActivityEventKind.RUN_OPENED, None),
                    (ActivityEventKind.RUN_STARTED, None),
                    (ActivityEventKind.STEP_STARTED, "start-api"),
                    (ActivityEventKind.STEP_SUCCEEDED, "start-api"),
                    (ActivityEventKind.RUN_SUCCEEDED, None),
                ),
                start=1,
            ):
                stores.execution.add_event(
                    ActivityEventRecord(
                        f"event-{suffix}-{ordinal}",
                        run_id,
                        ordinal,
                        kind,
                        f"2026-07-22T13:00:{ordinal:02d}Z",
                        activity_id=activity_id,
                        evidence=BoundedEvidence.from_mapping({"seed": "rotation"}),
                    )
                )

            unit_of_work.commit()

if __name__ == "__main__":
    unittest.main()
