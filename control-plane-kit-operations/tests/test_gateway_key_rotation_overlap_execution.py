from __future__ import annotations

from dataclasses import replace
import os
import unittest

import psycopg

from gateway_rotation_overlap_fixture import (
    CrashAfterCommitUnitOfWork,
    CrashControl,
    GatewayRotationOverlapFixture,
    SimulatedProcessLoss,
    effect_attempt_execution_coordinator,
    runtime_result_for_outcome,
)
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    FailureCategory,
    LifecycleOperationKind,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.coordinator import (
    ActivityExecutionOutcome,
    ActivityRealizationContext,
)
from control_plane_kit_operations.gateway_key_rotation_overlap_execution import (
    GatewayKeyRotationOverlapExecutionAuthorizationDenied,
    GatewayKeyRotationOverlapExecutionConflict,
    GatewayKeyRotationOverlapExecutionOutcome,
    GatewayKeyRotationOverlapExecutionProgram,
    ProgressGatewayKeyRotationOverlap,
)
from control_plane_kit_operations.gateway_key_rotation_overlap_program import (
    GatewayKeyRotationOverlapPreparationProgram,
    PrepareGatewayKeyRotationOverlap,
)
from control_plane_kit_operations.gateway_key_rotations import (
    AdvanceGatewayKeyRotationDeployment,
    AdvanceGatewayKeyRotation,
    GatewayKeyRotationConflict,
    GatewayKeyRotationDeploymentHandoff,
    GatewayKeyRotationDeploymentPhase,
    GatewayKeyRotationDeploymentStatus,
    ReadGatewayKeyRotationDeploymentHandoff,
    GatewayKeyRotationService,
    GatewayKeyRotationStatus,
)
from control_plane_kit_operations.lifecycle import (
    ExecutionLeaseDuration,
    ExecutionWorkerAuthority,
)
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import BoundedEvidence, FailureEvidence
from control_plane_kit_operations.workflows import IdempotencyKey


class CountingIds:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.count = 0

    def __call__(self) -> str:
        self.count += 1
        return f"{self.prefix}-{self.count}"


POST_EFFECT_CRASH_BOUNDARIES = (
    ("effect-fold", 5),
    ("run-complete", 6),
    ("receipt-complete", 7),
    ("current-graph-advance", 8),
    ("rotation-fold", 9),
)


class RecordingAdapter:
    def __init__(
        self,
        *outcomes: ActivityExecutionOutcome | BaseException,
    ) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[str] = []

    def execute(
        self,
        context: ActivityRealizationContext,
    ) -> ActivityExecutionOutcome:
        return self._next(context)

    def execute_runtime(self, context, request):
        return runtime_result_for_outcome(
            self._next(context),
            request.effect_id,
        )

    def _next(
        self,
        context: ActivityRealizationContext,
    ) -> ActivityExecutionOutcome:
        self.calls.append(context.activity.activity_id.value)
        if not self.outcomes:
            raise AssertionError("unexpected duplicate runtime effect")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class GatewayKeyRotationOverlapExecutionTests(
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
        self.reset_truth()

    def tearDown(self) -> None:
        self.connection.close()

    def reset_truth(self) -> None:
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.seed_graph_and_keys()
        self.seed_rotation_approval()
        prepared = self.preparation_program().prepare(self.preparation_command())
        assert prepared.checkpoint is not None
        self.prepared_version = prepared.rotation.version
        self.checkpoint = prepared.checkpoint

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def preparation_program(self) -> GatewayKeyRotationOverlapPreparationProgram:
        timestamps = iter(
            f"2026-08-02T02:{minute:02d}:00Z" for minute in range(30)
        )
        return GatewayKeyRotationOverlapPreparationProgram(
            self.unit_of_work,
            clock=lambda: next(timestamps),
            trusted_epoch_clock=lambda: 2_000,
            id_factory=CountingIds("prepare"),
        )

    def preparation_command(self) -> PrepareGatewayKeyRotationOverlap:
        scopes = (
            PolicyScope.DELEGATION_KEY_ROTATE,
            PolicyScope.PLAN_EXECUTE,
            PolicyScope.EXECUTION_OPERATE,
        )
        return PrepareGatewayKeyRotationOverlap(
            rotation_id=self.rotation_id,
            expected_rotation_version=self.rotation_version,
            expected_authored_graph_id="graph-a",
            expected_current_realized_projection_id="projection-a",
            expected_desired_realized_projection_id="projection-a",
            expected_desired_graph_revision=1,
            actor_id="operator-a",
            actor_scopes=scopes,
            worker_authority=ExecutionWorkerAuthority(
                "worker-a",
                (PolicyScope.EXECUTION_OPERATE,),
            ),
            lease_duration=ExecutionLeaseDuration(1800),
        )

    def command(
        self,
        *,
        expected_version: int | None = None,
        actor_scopes: tuple[PolicyScope, ...] = (
            PolicyScope.DELEGATION_KEY_ROTATE,
        ),
        worker_scopes: tuple[PolicyScope, ...] = (
            PolicyScope.EXECUTION_OPERATE,
        ),
        idempotency_key: str = "overlap-execute-a",
    ) -> ProgressGatewayKeyRotationOverlap:
        return ProgressGatewayKeyRotationOverlap(
            rotation_id=self.rotation_id,
            expected_prepared_rotation_version=(
                self.prepared_version
                if expected_version is None
                else expected_version
            ),
            actor_id="operator-a",
            actor_scopes=actor_scopes,
            worker_authority=ExecutionWorkerAuthority(
                "worker-a",
                worker_scopes,
            ),
            fence=ExecutionLeaseFence("worker-a", 1),
            idempotency_key=IdempotencyKey(idempotency_key),
        )

    def program(
        self,
        adapter: RecordingAdapter,
        *,
        unit_of_work_factory=None,
        prefix: str = "execute",
    ) -> GatewayKeyRotationOverlapExecutionProgram:
        factory = unit_of_work_factory or self.unit_of_work
        ids = CountingIds(prefix)
        clock = lambda: "2026-08-02T03:00:00Z"
        coordinator = effect_attempt_execution_coordinator(
            factory,
            adapter,
            clock=clock,
            prefix=prefix,
        )
        return GatewayKeyRotationOverlapExecutionProgram(
            factory,
            coordinator=coordinator,
            clock=clock,
            trusted_epoch_clock=lambda: 3_000,
            id_factory=ids,
        )

    def test_dispatches_accepts_advances_and_replays_without_duplicate_effect(self) -> None:
        activity_count = self._plan_activity_count()
        accepted_outcome = ActivityExecutionOutcome.succeeded(
            BoundedEvidence.from_mapping({"runtime": "accepted"})
        )
        adapter = RecordingAdapter(*(accepted_outcome,) * activity_count)
        program = self.program(adapter)
        intermediate = [
            program.progress(
                self.command(idempotency_key=f"overlap-step-{position}")
            )
            for position in range(1, activity_count)
        ]
        command = self.command(idempotency_key=f"overlap-step-{activity_count}")
        result = program.progress(command)

        self.assertTrue(intermediate)
        self.assertTrue(
            all(
                value.outcome
                is GatewayKeyRotationOverlapExecutionOutcome.DISPATCHED
                for value in intermediate
            )
        )
        self.assertIs(
            result.outcome,
            GatewayKeyRotationOverlapExecutionOutcome.ACCEPTED,
        )
        self.assertIs(result.rotation.status, GatewayKeyRotationStatus.OVERLAP_READY)
        self.assertIs(
            result.checkpoint.status,
            GatewayKeyRotationDeploymentStatus.ACCEPTED,
        )
        self.assertEqual(len(adapter.calls), activity_count)
        self.assertEqual(result.effects_attempted, 1)
        self.assertIsNotNone(result.advancement)
        self.assertEqual(
            result.advancement.action.payload["claim_generation"],
            command.fence.generation,
        )
        workspace = self._workspace()
        self.assertEqual(workspace.current_graph_id, "graph-a")
        self.assertEqual(
            workspace.current_realized_projection_id,
            self.checkpoint.desired_realized_projection_id,
        )
        self.assertEqual(workspace.desired_graph_id, "graph-a")
        self.assertEqual(self._authored_graph_count(), 1)

        replay = self.program(adapter, prefix="replay").progress(command)

        self.assertIs(
            replay.outcome,
            GatewayKeyRotationOverlapExecutionOutcome.ACCEPTED_REPLAY,
        )
        self.assertEqual(len(adapter.calls), activity_count)
        self.assertEqual(self._current_advancement_count(), 1)

    def test_stale_fence_translation_is_bounded_and_cause_free(self) -> None:
        adapter = RecordingAdapter(ActivityExecutionOutcome.succeeded())
        command = replace(
            self.command(),
            fence=ExecutionLeaseFence("worker-a", 2),
        )

        with self.assertRaises(
            GatewayKeyRotationOverlapExecutionConflict
        ) as captured:
            self.program(adapter).progress(command)

        self.assertEqual(
            str(captured.exception),
            "overlap checkpoint does not match durable child truth",
        )
        self.assertIsNone(captured.exception.__cause__)
        self.assertIsNone(captured.exception.__context__)
        self.assertEqual(adapter.calls, [])

    def test_failed_and_uncertain_effects_block_with_bounded_codes(self) -> None:
        cases = (
            (
                ActivityExecutionOutcome.failed(
                    FailureEvidence(
                        FailureCategory.TERMINAL,
                        "test-effect-failed",
                        "test effect failed",
                    )
                ),
                "overlap-effect-failed",
            ),
            (
                ActivityExecutionOutcome.uncertain(
                    FailureEvidence(
                        FailureCategory.UNCERTAIN,
                        "test-effect-uncertain",
                        "test effect result is unknown",
                    )
                ),
                "overlap-effect-uncertain",
            ),
        )
        for outcome, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                self.reset_truth()
                adapter = RecordingAdapter(outcome)

                result = self.program(adapter).progress(self.command())

                self.assertIs(
                    result.outcome,
                    GatewayKeyRotationOverlapExecutionOutcome.BLOCKED,
                )
                self.assertEqual(result.failure_code, expected_code)
                self.assertEqual(result.rotation.updated_at, "2026-08-02T03:00:00Z")
                self.assertEqual(len(adapter.calls), 1)
                self.assertEqual(
                    self._workspace().current_realized_projection_id,
                    "projection-a",
                )

    def test_process_loss_after_intent_blocks_without_redispatch(self) -> None:
        # Provider entry occurs only after the named effect-start commit.
        crashing = RecordingAdapter(SimulatedProcessLoss("lost during effect"))

        with self.assertRaises(SimulatedProcessLoss):
            self.program(crashing).progress(self.command())

        recovered_adapter = RecordingAdapter()
        recovered = self.program(recovered_adapter, prefix="recover").progress(
            self.command()
        )
        self.assertIs(
            recovered.outcome,
            GatewayKeyRotationOverlapExecutionOutcome.BLOCKED,
        )
        self.assertEqual(recovered.failure_code, "overlap-effect-uncertain")
        self.assertEqual(recovered_adapter.calls, [])
        self.assertEqual(
            self._attempt_event_kinds().count(ActivityEventKind.STEP_STARTED),
            1,
        )
        self.assertEqual(self._current_advancement_count(), 0)
        self.assertEqual(
            self._workspace().current_realized_projection_id,
            "projection-a",
        )
        self.connection.execute(
            "UPDATE cpk_execution_requests SET claim_generation=2 "
            "WHERE request_id=%s",
            (self.checkpoint.execution_request_id,),
        )
        stale_adapter = RecordingAdapter()
        with self.assertRaises(
            GatewayKeyRotationOverlapExecutionConflict
        ) as captured:
            self.program(stale_adapter, prefix="stale-replay").progress(
                self.command()
            )
        self.assertEqual(
            str(captured.exception),
            "gateway deployment execution authority is stale",
        )
        self.assertIsNone(captured.exception.__cause__)
        self.assertIsNone(captured.exception.__context__)
        self.assertEqual(stale_adapter.calls, [])

    def test_restarts_after_each_post_effect_commit_without_duplicate_effect(self) -> None:
        for boundary, crash_after_commit in POST_EFFECT_CRASH_BOUNDARIES:
            with self.subTest(boundary=boundary):
                self.reset_truth()
                activity_count = self._plan_activity_count()
                prior_adapter = RecordingAdapter(
                    *(ActivityExecutionOutcome.succeeded(),)
                    * (activity_count - 1)
                )
                prior_program = self.program(
                    prior_adapter,
                    prefix=f"prior-{crash_after_commit}",
                )
                for position in range(1, activity_count):
                    prior = prior_program.progress(
                        self.command(
                            idempotency_key=f"{boundary}-prior-{position}"
                        )
                    )
                    self.assertIs(
                        prior.outcome,
                        GatewayKeyRotationOverlapExecutionOutcome.DISPATCHED,
                    )
                control = CrashControl(crash_after_commit)
                crashing_factory = lambda: CrashAfterCommitUnitOfWork(
                    self.unit_of_work(),
                    control,
                )
                adapter = RecordingAdapter(ActivityExecutionOutcome.succeeded())
                crash_command = self.command(
                    idempotency_key=f"{boundary}-terminal"
                )

                with self.assertRaises(SimulatedProcessLoss):
                    self.program(
                        adapter,
                        unit_of_work_factory=crashing_factory,
                        prefix=f"crash-{crash_after_commit}",
                    ).progress(crash_command)

                durable_kinds = self._attempt_event_kinds()
                self.assertEqual(
                    durable_kinds.count(ActivityEventKind.STEP_STARTED),
                    activity_count,
                )
                self.assertEqual(
                    durable_kinds.count(ActivityEventKind.STEP_SUCCEEDED),
                    activity_count,
                )
                if boundary == "current-graph-advance":
                    rotations = GatewayKeyRotationService(
                        self.unit_of_work,
                        clock=lambda: 3_000,
                    )
                    rotation_before_stale = rotations.get(self.rotation_id)
                    transitions_before_stale = rotations.transitions(
                        self.rotation_id
                    )
                    events_before_stale = tuple(self._event_kinds())
                    actions_before_stale = self._current_advancement_action_count()
                    self.assertIs(
                        rotation_before_stale.status,
                        GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
                    )
                    self.assertEqual(
                        rotation_before_stale.overlap_deployment,
                        self.checkpoint,
                    )
                    self.assertEqual(
                        self._workspace().current_realized_projection_id,
                        self.checkpoint.desired_realized_projection_id,
                    )
                    self.connection.execute(
                        "UPDATE cpk_execution_requests SET claim_generation=2 "
                        "WHERE request_id=%s",
                        (self.checkpoint.execution_request_id,),
                    )
                    stale_adapter = RecordingAdapter()
                    with self.assertRaises(
                        GatewayKeyRotationOverlapExecutionConflict
                    ) as captured:
                        self.program(
                            stale_adapter,
                            prefix="stale-after-graph-advance",
                        ).progress(crash_command)
                    self.assertEqual(
                        str(captured.exception),
                        "overlap checkpoint does not match durable child truth",
                    )
                    self.assertEqual(stale_adapter.calls, [])
                    self.assertEqual(
                        rotations.get(self.rotation_id),
                        rotation_before_stale,
                    )
                    self.assertEqual(
                        rotations.transitions(self.rotation_id),
                        transitions_before_stale,
                    )
                    self.assertEqual(tuple(self._event_kinds()), events_before_stale)
                    self.assertEqual(self._current_advancement_count(), 1)
                    self.assertEqual(
                        self._current_advancement_action_count(),
                        actions_before_stale,
                    )
                    replacement_handoff = rotations.deployment_handoff(
                        ReadGatewayKeyRotationDeploymentHandoff(
                            self.rotation_id,
                            GatewayKeyRotationDeploymentPhase.OVERLAP,
                            self.command().worker_authority,
                        )
                    )
                    accepted_checkpoint = replace(
                        self.checkpoint,
                        status=GatewayKeyRotationDeploymentStatus.ACCEPTED,
                        accepted_current_graph_id=(
                            self.checkpoint.desired_authored_graph_id
                        ),
                        accepted_current_projection_id=(
                            self.checkpoint.desired_realized_projection_id
                        ),
                        accepted_at="2026-08-02T03:00:00Z",
                    )
                    recovered_rotation = rotations.advance_deployment(
                        AdvanceGatewayKeyRotationDeployment(
                            transition=AdvanceGatewayKeyRotation(
                                rotation_id=self.rotation_id,
                                transition_id="replacement-overlap-fold",
                                expected_status=(
                                    GatewayKeyRotationStatus.OVERLAP_DEPLOYING
                                ),
                                expected_version=rotation_before_stale.version,
                                target_status=GatewayKeyRotationStatus.OVERLAP_READY,
                                advanced_by="operator-a",
                                advanced_at=accepted_checkpoint.accepted_at,
                                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                                deployment=accepted_checkpoint,
                            ),
                            handoff=GatewayKeyRotationDeploymentHandoff(
                                self.rotation_id,
                                accepted_checkpoint,
                                replacement_handoff.fence,
                            ),
                        )
                    )
                    recovered_adapter = RecordingAdapter()
                    self.assertIs(
                        recovered_rotation.status,
                        GatewayKeyRotationStatus.OVERLAP_READY,
                    )
                else:
                    recovered_adapter = RecordingAdapter()
                    recovered = self.program(
                        recovered_adapter,
                        prefix=f"recover-{crash_after_commit}",
                    ).progress(crash_command)

                if boundary != "current-graph-advance":
                    self.assertIn(
                        recovered.outcome,
                        {
                            GatewayKeyRotationOverlapExecutionOutcome.ACCEPTED,
                            GatewayKeyRotationOverlapExecutionOutcome.ACCEPTED_REPLAY,
                        },
                        recovered.failure_code,
                    )
                self.assertEqual(len(prior_adapter.calls), activity_count - 1)
                self.assertEqual(len(adapter.calls), 1)
                self.assertEqual(recovered_adapter.calls, [])
                self.assertEqual(self._current_advancement_count(), 1)
                self.assertEqual(
                    self._workspace().current_realized_projection_id,
                    self.checkpoint.desired_realized_projection_id,
                )

    def test_accepted_fold_requires_exact_advancement_action_and_event(
        self,
    ) -> None:
        cases = (
            (
                "missing-action",
                lambda action, event, accepted: (
                    self.connection.execute(
                        "DELETE FROM cpk_operation_actions WHERE action_id=%s",
                        (action.action_id,),
                    ),
                    accepted,
                )[1],
            ),
            (
                "missing-event",
                lambda action, event, accepted: (
                    self.connection.execute(
                        "DELETE FROM cpk_activity_events WHERE event_id=%s",
                        (event.event_id,),
                    ),
                    accepted,
                )[1],
            ),
            (
                "foreign-request",
                lambda action, event, accepted: (
                    self.connection.execute(
                        "UPDATE cpk_operation_actions "
                        "SET payload=jsonb_set(payload, "
                        "'{execution_request_id}', to_jsonb(%s::text)) "
                        "WHERE action_id=%s",
                        ("foreign-request", action.action_id),
                    ),
                    accepted,
                )[1],
            ),
            (
                "foreign-event-link",
                lambda action, event, accepted: (
                    self.connection.execute(
                        "UPDATE cpk_operation_actions "
                        "SET payload=jsonb_set(payload, "
                        "'{event_id}', to_jsonb(%s::text)) "
                        "WHERE action_id=%s",
                        ("foreign-event", action.action_id),
                    ),
                    accepted,
                )[1],
            ),
            (
                "event-transition-drift",
                lambda action, event, accepted: (
                    self.connection.execute(
                        "UPDATE cpk_activity_events "
                        "SET payload=jsonb_set(payload, "
                        "'{evidence,to_authored_graph_id}', to_jsonb(%s::text)) "
                        "WHERE event_id=%s",
                        ("graph-drift", event.event_id),
                    ),
                    accepted,
                )[1],
            ),
            (
                "accepted-graph-drift",
                lambda _action, _event, accepted: replace(
                    accepted,
                    accepted_current_graph_id="graph-drift",
                ),
            ),
            (
                "accepted-projection-drift",
                lambda _action, _event, accepted: replace(
                    accepted,
                    accepted_current_projection_id="projection-drift",
                ),
            ),
            (
                "accepted-time-drift",
                lambda _action, _event, accepted: replace(
                    accepted,
                    accepted_at="2026-08-02T03:00:01Z",
                ),
            ),
        )
        for identity, mutate in cases:
            with self.subTest(identity=identity):
                self.reset_truth()
                rotations, rotation = self._advance_graph_without_rotation_fold()
                self.connection.execute(
                    "UPDATE cpk_execution_requests SET claim_generation=2 "
                    "WHERE request_id=%s",
                    (self.checkpoint.execution_request_id,),
                )
                handoff = rotations.deployment_handoff(
                    ReadGatewayKeyRotationDeploymentHandoff(
                        self.rotation_id,
                        GatewayKeyRotationDeploymentPhase.OVERLAP,
                        self.command().worker_authority,
                    )
                )
                accepted, action, event = self._accepted_from_advancement()
                candidate = mutate(action, event, accepted)
                snapshot = self._durable_acceptance_snapshot(rotations)

                with self.assertRaisesRegex(
                    GatewayKeyRotationConflict,
                    "^gateway deployment acceptance evidence is incongruent$",
                ) as captured:
                    rotations.advance_deployment(
                        self._accepted_fold(rotation, handoff.fence, candidate)
                    )

                self.assertIsNone(captured.exception.__cause__)
                self.assertIsNone(captured.exception.__context__)
                self.assertEqual(
                    self._durable_acceptance_snapshot(rotations),
                    snapshot,
                )

    def test_stale_checkpoint_and_missing_scopes_fail_before_dispatch(self) -> None:
        for command, expected_error in (
            (
                self.command(expected_version=self.prepared_version + 1),
                GatewayKeyRotationOverlapExecutionConflict,
            ),
            (
                self.command(actor_scopes=()),
                GatewayKeyRotationOverlapExecutionAuthorizationDenied,
            ),
            (
                self.command(worker_scopes=()),
                GatewayKeyRotationOverlapExecutionAuthorizationDenied,
            ),
        ):
            with self.subTest(error=expected_error.__name__):
                adapter = RecordingAdapter()
                with self.assertRaises(expected_error):
                    self.program(adapter).progress(command)
                self.assertEqual(adapter.calls, [])

        self.connection.execute(
            "UPDATE cpk_workspaces SET desired_graph_revision=99 "
            "WHERE workspace_id='workspace-a'"
        )
        adapter = RecordingAdapter()
        with self.assertRaises(GatewayKeyRotationOverlapExecutionConflict):
            self.program(adapter).progress(self.command())
        self.assertEqual(adapter.calls, [])

    def _workspace(self):
        with self.unit_of_work() as unit_of_work:
            return unit_of_work.stores.workspaces.get("workspace-a")

    def _plan_activity_count(self) -> int:
        with self.unit_of_work() as unit_of_work:
            plan = unit_of_work.stores.activity_history.get_plan(
                self.checkpoint.plan_id
            )
        count = len(plan.plan.activities)
        self.assertGreater(count, 1)
        return count

    def _event_kinds(self) -> list[ActivityEventKind]:
        with self.unit_of_work() as unit_of_work:
            events = unit_of_work.stores.execution.events_for_run(
                self.checkpoint.run_id
            )
        return [event.kind for event in events]

    def _attempt_event_kinds(self) -> list[ActivityEventKind]:
        with self.unit_of_work() as unit_of_work:
            events = unit_of_work.stores.execution.events_for_run(
                self.checkpoint.run_id
            )
        return [
            event.kind
            for event in events
            if set(event.evidence.descriptor()) == {"effect_attempt"}
        ]

    def _advance_graph_without_rotation_fold(self):
        activity_count = self._plan_activity_count()
        prior_adapter = RecordingAdapter(
            *(ActivityExecutionOutcome.succeeded(),) * (activity_count - 1)
        )
        prior_program = self.program(prior_adapter, prefix="evidence-prior")
        for position in range(1, activity_count):
            prior_program.progress(
                self.command(idempotency_key=f"evidence-prior-{position}")
            )
        graph_advance_commit = dict(POST_EFFECT_CRASH_BOUNDARIES)[
            "current-graph-advance"
        ]
        control = CrashControl(graph_advance_commit)
        with self.assertRaises(SimulatedProcessLoss):
            self.program(
                RecordingAdapter(ActivityExecutionOutcome.succeeded()),
                unit_of_work_factory=lambda: CrashAfterCommitUnitOfWork(
                    self.unit_of_work(),
                    control,
                ),
                prefix="evidence-crash",
            ).progress(self.command(idempotency_key="evidence-terminal"))
        rotations = GatewayKeyRotationService(
            self.unit_of_work,
            clock=lambda: 3_000,
        )
        rotation = rotations.get(self.rotation_id)
        self.assertIs(
            rotation.status,
            GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
        )
        self.assertEqual(self._current_advancement_count(), 1)
        return rotations, rotation

    def _accepted_from_advancement(self):
        with self.unit_of_work() as unit_of_work:
            actions = tuple(
                action
                for action in unit_of_work.stores.activity_history.actions_for_session(
                    self.checkpoint.session_id
                )
                if action.action_type
                is LifecycleOperationKind.ADVANCE_CURRENT_GRAPH
            )
            self.assertEqual(len(actions), 1)
            action = actions[0]
            event = unit_of_work.stores.execution.get_event(
                action.payload["event_id"]
            )
        return (
            replace(
                self.checkpoint,
                status=GatewayKeyRotationDeploymentStatus.ACCEPTED,
                accepted_current_graph_id=action.payload["to_authored_graph_id"],
                accepted_current_projection_id=(
                    action.payload["to_realized_projection_id"]
                ),
                accepted_at=event.occurred_at,
            ),
            action,
            event,
        )

    def _accepted_fold(self, rotation, fence, checkpoint):
        return AdvanceGatewayKeyRotationDeployment(
            transition=AdvanceGatewayKeyRotation(
                rotation_id=self.rotation_id,
                transition_id="evidence-overlap-fold",
                expected_status=GatewayKeyRotationStatus.OVERLAP_DEPLOYING,
                expected_version=rotation.version,
                target_status=GatewayKeyRotationStatus.OVERLAP_READY,
                advanced_by="operator-a",
                advanced_at=checkpoint.accepted_at,
                actor_scopes=(PolicyScope.DELEGATION_KEY_ROTATE,),
                deployment=checkpoint,
            ),
            handoff=GatewayKeyRotationDeploymentHandoff(
                self.rotation_id,
                checkpoint,
                fence,
            ),
        )

    def _durable_acceptance_snapshot(self, rotations):
        return (
            self._workspace(),
            rotations.get(self.rotation_id),
            rotations.transitions(self.rotation_id),
            tuple(
                self.connection.execute(
                    "SELECT action_id, payload FROM cpk_operation_actions "
                    "ORDER BY ordinal, action_id"
                ).fetchall()
            ),
            tuple(
                self.connection.execute(
                    "SELECT event_id, run_id, event_type, occurred_at, payload "
                    "FROM cpk_activity_events ORDER BY ordinal, event_id"
                ).fetchall()
            ),
        )

    def _authored_graph_count(self) -> int:
        return self.connection.execute(
            "SELECT count(*) FROM cpk_graph_versions"
        ).fetchone()[0]

    def _current_advancement_count(self) -> int:
        return self._event_kinds().count(ActivityEventKind.CURRENT_GRAPH_ADVANCED)

    def _current_advancement_action_count(self) -> int:
        return self.connection.execute(
            "SELECT count(*) FROM cpk_operation_actions WHERE action_type=%s",
            (LifecycleOperationKind.ADVANCE_CURRENT_GRAPH.value,),
        ).fetchone()[0]


if __name__ == "__main__":
    unittest.main()
