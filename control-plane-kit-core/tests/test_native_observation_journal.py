"""#1860: completed native reads wait without replaying deployment steps."""

import unittest
from dataclasses import replace

from control_plane_kit_core.planning import (
    ActivityDependency, ActivityId, ActivityPlan, ManagementBootstrapStage,
    ManagementObservationTarget, ObserveManagementBootstrap, PlanGraphSide,
    PlannedActivity, RuntimeTarget, StartRuntime,
)
from control_plane_kit_core.planning.saga import (
    ActivityJournalEvent, ActivityJournalEventKind, SagaStatus, SagaStepId,
    derive_schedule, project_activity_journal,
)


class NativeObservationJournalTests(unittest.TestCase):
    def plan(self, stage=ManagementBootstrapStage.CONNECTOR_CONNECTED):
        target = ManagementObservationTarget(
            runtime_id="runtime-a", graph_side=PlanGraphSide.DESIRED_GRAPH,
            graph_digest="a" * 64, relation_digest="b" * 64,
        )
        return ActivityPlan((
            PlannedActivity(ActivityId("created"), StartRuntime(RuntimeTarget("runtime-a"))),
            PlannedActivity(ActivityId("connection"), ObserveManagementBootstrap(target, stage),
                (ActivityDependency(ActivityId("created")),)),
            PlannedActivity(ActivityId("dependent"), StartRuntime(RuntimeTarget("runtime-b")),
                (ActivityDependency(ActivityId("connection")),)),
        ))

    def event(self, ordinal, kind, activity="connection", attempt=None):
        selected = getattr(ActivityJournalEventKind, kind, None)
        self.assertIsNotNone(selected, "completed native observation journal law is missing")
        arguments = dict(event_id=f"event-{ordinal}", run_id="run-a", ordinal=ordinal,
            kind=selected, activity_id=activity)
        if attempt is not None:
            arguments["attempt"] = attempt
        try:
            return ActivityJournalEvent(**arguments)
        except TypeError as error:
            self.fail(f"journal cannot retain the native attempt coordinate: {error}")

    def waiting_history(self):
        return (
            self.event(1, "STEP_STARTED", "created"),
            self.event(2, "STEP_SUCCEEDED", "created"),
            self.event(3, "STEP_STARTED", attempt=1),
            self.event(4, "STEP_OBSERVATION_NOT_READY", attempt=1),
        )

    def test_completed_not_ready_preserves_creation_and_blocks_automatic_execution(self):
        plan = self.plan()
        projection = project_activity_journal(plan, self.waiting_history())
        self.assertIs(projection.state.status, SagaStatus.ACTIVE)
        self.assertEqual(projection.state.step(SagaStepId("connection")).status.value, "waiting")
        self.assertEqual(projection.state.completion_order, (SagaStepId("created"),))
        self.assertEqual(projection.state.failed_steps, ())
        self.assertEqual((projection.in_flight, projection.uncertain), ((), ()))
        schedule = derive_schedule(plan, projection.state)
        self.assertEqual(schedule.ready, ())
        self.assertEqual(schedule.running, ())
        self.assertFalse(schedule.successful)

    def test_final_waiting_obligation_cannot_make_the_plan_successful(self):
        plan = self.plan()
        plan = replace(plan, activities=plan.activities[:2])
        projection = project_activity_journal(plan, self.waiting_history())
        schedule = derive_schedule(plan, projection.state)
        self.assertIs(projection.state.status, SagaStatus.ACTIVE)
        self.assertEqual(projection.state.step(SagaStepId("connection")).status.value, "waiting")
        self.assertFalse(schedule.successful)
        self.assertEqual((schedule.ready, schedule.running), ((), ()))
        self.assertFalse(schedule.terminal)
        self.assertIn("connection", tuple(value.activity_id.value for value in schedule.waiting)
            + tuple(value.activity.activity_id.value for value in schedule.blocked))

    def test_only_explicit_sequential_read_releases_the_same_obligation_once(self):
        plan = self.plan()
        history = self.waiting_history() + (
            self.event(5, "STEP_OBSERVATION_RESTARTED", attempt=2),
            self.event(6, "STEP_OBSERVATION_NOT_READY", attempt=2),
            self.event(7, "STEP_OBSERVATION_RESTARTED", attempt=3),
            self.event(8, "STEP_SUCCEEDED", attempt=3),
        )
        projection = project_activity_journal(plan, history)
        self.assertEqual(projection.state.completion_order,
            (SagaStepId("created"), SagaStepId("connection")))
        self.assertEqual(tuple(value.activity_id.value for value in derive_schedule(plan, projection.state).ready),
            ("dependent",))
        self.assertEqual(project_activity_journal(plan, history), projection)
        with self.assertRaises(ValueError):
            project_activity_journal(plan, history + (self.event(9, "STEP_SUCCEEDED", attempt=3),))

    def test_native_waiting_cannot_restart_with_generic_start_or_wrong_lineage(self):
        plan, history = self.plan(), self.waiting_history()
        for kind, attempt in (("STEP_STARTED", 2), ("STEP_OBSERVATION_RESTARTED", 1),
                ("STEP_OBSERVATION_RESTARTED", 3), ("STEP_OBSERVATION_RESTARTED", None)):
            with self.subTest(kind=kind, attempt=attempt), self.assertRaises(ValueError):
                project_activity_journal(plan, history + (self.event(5, kind, attempt=attempt),))

    def test_not_ready_is_native_only_and_terminal_result_matches_active_attempt(self):
        history = self.waiting_history()
        for stage in ManagementBootstrapStage:
            if stage is ManagementBootstrapStage.CONNECTOR_CONNECTED:
                continue
            with self.subTest(stage=stage), self.assertRaises(ValueError):
                project_activity_journal(self.plan(stage), history)
        mutation_plan = self.plan()
        mutation_plan = replace(mutation_plan, activities=tuple(
            replace(value, operation=StartRuntime(RuntimeTarget("other")))
            if value.activity_id.value == "connection" else value for value in mutation_plan.activities))
        with self.assertRaises(ValueError):
            project_activity_journal(mutation_plan, history)
        with self.assertRaises(ValueError):
            project_activity_journal(self.plan(), history[:-1] +
                (self.event(4, "STEP_OBSERVATION_NOT_READY", attempt=2),))
        with self.assertRaises(ValueError):
            project_activity_journal(self.plan(), history + (
                self.event(5, "STEP_OBSERVATION_RESTARTED", attempt=2),
                self.event(6, "STEP_SUCCEEDED", attempt=1),
            ))

    def test_legacy_native_start_cannot_supply_explicit_attempt_lineage(self):
        history = self.waiting_history()
        legacy = (*history[:2], self.event(3, "STEP_STARTED"))
        for kind in ("STEP_OBSERVATION_NOT_READY", "STEP_OBSERVATION_RESTARTED"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                project_activity_journal(self.plan(), legacy + (self.event(4, kind, attempt=1),))

    def test_unknown_dispatch_and_terminal_failure_cannot_be_reobserved(self):
        prefix = self.waiting_history()[:-1]
        for terminal in ("STEP_UNCERTAIN", "STEP_FAILED", "STEP_UNSUPPORTED"):
            with self.subTest(terminal=terminal), self.assertRaises(ValueError):
                project_activity_journal(self.plan(), prefix + (
                    self.event(4, terminal, attempt=1),
                    self.event(5, "STEP_OBSERVATION_RESTARTED", attempt=2),
                ))
