from __future__ import annotations

from dataclasses import dataclass, field
import unittest

from control_plane_kit.effects import (
    EffectCapability,
    EffectDispatchError,
    EffectFailed,
    EffectRequest,
    EffectSucceeded,
    EffectUnsupported,
    dispatch_effect,
    effect_request_for_activity,
)
from control_plane_kit.execution import BoundedEvidence, FailureCategory, FailureEvidence
from control_plane_kit.planning import compile_activity_plan
from control_plane_kit.planning import (
    ActivityId,
    AddSocketConnection,
    NodeTarget,
    PlannedActivity,
    ReconcileNode,
    ReconcileRuntime,
    RemoveSocketConnection,
    RuntimeTarget,
    SocketConnectionTarget,
    StartNode,
    StartRuntime,
    StopNode,
    StopRuntime,
    SwitchSocketConnection,
    WaitForHealthy,
)
from control_plane_kit.saga import SagaState, SagaStepId, SagaStepState
from control_plane_kit.scheduling import derive_schedule
from control_plane_kit.topology import diff_graphs, validate_graph
from examples.scenarios import planning_scenarios


@dataclass
class FakeEffectInterpreter:
    capabilities: frozenset[EffectCapability]
    fail: bool = False
    requests: list[EffectRequest] = field(default_factory=list)

    def execute(self, request: EffectRequest) -> EffectSucceeded | EffectFailed:
        self.requests.append(request)
        if self.fail:
            return EffectFailed(
                request.identity,
                FailureEvidence(
                    FailureCategory.RETRYABLE,
                    "fake-failure",
                    "the fake interpreter attempted and failed the effect",
                ),
            )
        return EffectSucceeded(
            request.identity,
            BoundedEvidence.from_mapping({"fake": True}),
        )


class EffectDispatchTests(unittest.TestCase):
    def test_fake_interpreter_attempts_every_safe_activity_operation(self) -> None:
        node = NodeTarget("api")
        runtime = RuntimeTarget("docker")
        edge = SocketConnectionTarget("auth-api")
        operations = (
            StartNode(node),
            StopNode(node),
            StartRuntime(runtime),
            StopRuntime(runtime),
            WaitForHealthy(node),
            AddSocketConnection(edge),
            SwitchSocketConnection(edge),
            RemoveSocketConnection(edge),
            ReconcileNode(node),
            ReconcileRuntime(runtime),
        )

        for ordinal, operation in enumerate(operations, start=1):
            with self.subTest(operation=type(operation).__name__):
                activity = PlannedActivity(ActivityId(f"effect-{ordinal}"), operation)
                request = effect_request_for_activity(
                    activity,
                    run_id="run-all-safe-operations",
                    attempt=1,
                    idempotency_key=f"all-safe:{ordinal}:1",
                )
                interpreter = FakeEffectInterpreter(frozenset({request.capability}))

                self.assertIsInstance(
                    dispatch_effect(request, interpreter),
                    EffectSucceeded,
                )
                self.assertEqual(interpreter.requests, [request])

    def test_unsupported_capability_is_rejected_before_attempt(self) -> None:
        request = self._first_executable_request()
        interpreter = FakeEffectInterpreter(frozenset())

        result = dispatch_effect(request, interpreter)

        self.assertEqual(
            result,
            EffectUnsupported(request.identity, request.capability),
        )
        self.assertEqual(interpreter.requests, [])

    def test_attempted_failure_is_distinct_from_unsupported(self) -> None:
        request = self._first_executable_request()
        interpreter = FakeEffectInterpreter(
            frozenset({request.capability}),
            fail=True,
        )

        result = dispatch_effect(request, interpreter)

        self.assertIsInstance(result, EffectFailed)
        self.assertNotIsInstance(result, EffectUnsupported)
        self.assertEqual(interpreter.requests, [request])

    def test_fake_interpreter_executes_every_initially_ready_scenario_effect(self) -> None:
        executed = 0
        for scenario in planning_scenarios():
            current = validate_graph(scenario.current_graph)
            desired = validate_graph(scenario.desired_graph)
            current.require_valid()
            desired.require_valid()
            plan = compile_activity_plan(diff_graphs(current, desired))
            evidence = SagaState(
                tuple(
                    SagaStepState(SagaStepId(value.activity_id.value))
                    for value in plan.activities
                )
            )
            schedule = derive_schedule(plan, evidence)
            for activity in schedule.ready:
                request = effect_request_for_activity(
                    activity,
                    run_id=f"run-{scenario.scenario_id}",
                    attempt=1,
                    idempotency_key=f"{scenario.scenario_id}:{activity.activity_id.value}:1",
                )
                interpreter = FakeEffectInterpreter(frozenset({request.capability}))
                self.assertIsInstance(dispatch_effect(request, interpreter), EffectSucceeded)
                executed += 1

        self.assertGreater(executed, 0)

    def test_interpreter_must_return_the_request_identity(self) -> None:
        request = self._first_executable_request()

        class WrongIdentityInterpreter(FakeEffectInterpreter):
            def execute(self, value: EffectRequest) -> EffectSucceeded:
                other = self._different_request(value)
                return EffectSucceeded(other.identity)

            @staticmethod
            def _different_request(value: EffectRequest) -> EffectRequest:
                first = next(iter(planning_scenarios()))
                plan = compile_activity_plan(
                    diff_graphs(validate_graph(first.current_graph), validate_graph(first.desired_graph))
                )
                return effect_request_for_activity(
                    plan.activities[0],
                    run_id="different-run",
                    attempt=1,
                    idempotency_key="different-key",
                )

        interpreter = WrongIdentityInterpreter(frozenset({request.capability}))
        with self.assertRaisesRegex(EffectDispatchError, "identity"):
            dispatch_effect(request, interpreter)

    def test_interpreter_capabilities_must_be_a_typed_frozenset(self) -> None:
        request = self._first_executable_request()
        interpreter = FakeEffectInterpreter(frozenset({request.capability}))
        interpreter.capabilities = {request.capability}  # type: ignore[assignment]
        with self.assertRaisesRegex(EffectDispatchError, "typed frozenset"):
            dispatch_effect(request, interpreter)

    @staticmethod
    def _first_executable_request() -> EffectRequest:
        for scenario in planning_scenarios():
            plan = compile_activity_plan(
                diff_graphs(
                    validate_graph(scenario.current_graph),
                    validate_graph(scenario.desired_graph),
                )
            )
            if plan.ready_for_execution and plan.activities:
                return effect_request_for_activity(
                    plan.activities[0],
                    run_id="run-1",
                    attempt=1,
                    idempotency_key="run-1:first:1",
                )
        raise AssertionError("scenario corpus has no executable activity")


if __name__ == "__main__":
    unittest.main()
