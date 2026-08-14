from __future__ import annotations

import concurrent.futures
import os
import unittest
from dataclasses import replace
from typing import Any

import psycopg

from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.operations import RunId
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
    LifecycleOperationKind,
)
from control_plane_kit_core.planning import ActivityId, ActivityPlan, NodeTarget
from control_plane_kit_core.planning import PlannedActivity, StartNode
from control_plane_kit_core.planning import RiskLevel
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_operations.advancement import (
    AdvanceCurrentGraph,
    CurrentGraphAdvancementCommandService,
    CurrentGraphAdvancementConflict,
    CurrentGraphAdvancementDenied,
    CurrentGraphAdvancementIdempotencyConflict,
    CurrentGraphAdvancementIncomplete,
    CurrentGraphAdvancementNotFound,
    CurrentGraphAdvancementResult,
)
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
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
    WorkspaceRecord,
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
        payload={"event_id": "event-a"},
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


class CurrentGraphAdvancementTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required. Run "
                "./control-plane-kit-operations/test.sh so Docker starts Postgres."
            )
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.seed_truth()

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def service(self, *ids: str) -> CurrentGraphAdvancementCommandService:
        return CurrentGraphAdvancementCommandService(
            self.unit_of_work,
            clock=lambda: "2026-07-22T13:05:00Z",
            id_factory=Sequence(*ids),
        )

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
                self.assertEqual(actions, ())

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

        with self.assertRaises(CurrentGraphAdvancementNotFound):
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
                    idempotency_key=IdempotencyKey("wrong-workspace"),
                )
            )
        with self.assertRaises(CurrentGraphAdvancementNotFound):
            self.service("unused-event", "unused-action").execute(
                replace(
                    self.command(key="wrong-run"),
                    run_id="run-missing",
                )
            )
        with self.assertRaises(CurrentGraphAdvancementNotFound):
            self.service("unused-event", "unused-action").execute(
                replace(
                    self.command(key="wrong-plan"),
                    plan_id="plan-missing",
                )
            )

    def test_stable_authored_graph_advances_a_to_overlap_to_b(self) -> None:
        plan = ActivityPlan(
            (PlannedActivity(ActivityId("start-api"), StartNode(NodeTarget("api"))),)
        )
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.workspaces.create(
                WorkspaceRecord("workspace-rotation", "Rotation")
            )
            authored = stores.graphs.save(
                GraphVersionRecord.from_graph(
                    graph_id="graph-stable",
                    workspace_id="workspace-rotation",
                    version=1,
                    graph=DeploymentGraph("stable-authored"),
                    created_by="operator-a",
                    created_at="2026-07-22T12:00:00Z",
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
                        graph=DeploymentGraph(f"realized-{key}"),
                        created_by="rotation-program",
                        created_at="2026-07-22T12:00:30Z",
                    )
                )
                for key in ("a", "a-plus-b", "b")
            )
            stores.workspaces.set_current_graph(
                "workspace-rotation",
                authored.graph_id,
                projections[0].projection_id,
            )
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
                    1,
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

    def seed_truth(self) -> None:
        plan = ActivityPlan(
            (PlannedActivity(ActivityId("start-api"), StartNode(NodeTarget("api"))),)
        )
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.workspaces.create(
                WorkspaceRecord("workspace-a", "Workspace A")
            )
            stores.graphs.save(
                GraphVersionRecord.from_graph(
                    graph_id="graph-current",
                    workspace_id="workspace-a",
                    version=1,
                    graph=DeploymentGraph("current"),
                    created_by="operator-a",
                    created_at="2026-07-22T12:00:00Z",
                )
            )
            stores.graphs.save(
                GraphVersionRecord.from_graph(
                    graph_id="graph-desired",
                    workspace_id="workspace-a",
                    version=2,
                    graph=DeploymentGraph("desired"),
                    created_by="operator-a",
                    created_at="2026-07-22T12:00:30Z",
                )
            )
            stores.workspaces.set_current_graph("workspace-a", "graph-current")
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
            stores.execution.add_request(
                ExecutionRequestRecord(
                    ExecutionRequestIdentity(
                        "request-a",
                        "workspace-a",
                        "session-a",
                        "plan-a",
                    ),
                    ExecutionRequestStatus.CLAIMED,
                    "operator-a",
                    "2026-07-22T12:04:00Z",
                    "approval-request-a",
                    "approval-decision-a",
                    ExecutionIdempotency("execute-a", "fingerprint-a"),
                    ClaimIdentity(
                        "worker-a",
                        "2026-07-22T12:04:30Z",
                        "2026-07-22T12:14:30Z",
                    ),
                )
            )
            unit_of_work.commit()

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
            stores.execution.add_run(
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
        stores.execution.add_request(
            ExecutionRequestRecord(
                ExecutionRequestIdentity(
                    request_id,
                    workspace_id,
                    session_id,
                    plan_id,
                ),
                ExecutionRequestStatus.CLAIMED,
                "operator-a",
                "2026-07-22T12:04:00Z",
                f"approval-request-{suffix}",
                f"approval-decision-{suffix}",
                ExecutionIdempotency(f"execute-{suffix}", f"fingerprint-{suffix}"),
                ClaimIdentity(
                    "worker-a",
                    "2026-07-22T12:04:30Z",
                    "2026-07-22T12:14:30Z",
                ),
            )
        )
        stores.execution.add_run(
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


if __name__ == "__main__":
    unittest.main()
