from __future__ import annotations

from dataclasses import dataclass, field
import unittest

from control_plane_kit.effects import (
    EffectCapability,
    EffectDispatchError,
    EffectFailed,
    EffectRequest,
    MaterializedEffectRequest,
    PinnedGraphSet,
    RuntimeMaterial,
    EffectSucceeded,
    EffectUnsupported,
    dispatch_effect,
    dispatch_prepared_effect,
    effect_request_for_activity,
    prepare_effect,
)
from control_plane_kit.execution import BoundedEvidence, FailureCategory, FailureEvidence
from control_plane_kit.planning import compile_activity_plan
from control_plane_kit.planning import (
    ActivityId,
    AddSocketConnection,
    DataResourceTarget,
    DestroyDataResource,
    NodeTarget,
    PlannedActivity,
    ReconcileNode,
    ReconcileRuntime,
    RemoveNodeResource,
    RemoveRuntimeResource,
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
from control_plane_kit.core.topology import diff_graphs, validate_graph
from control_plane_kit.core.types import RuntimeKind
from examples.scenarios import planning_scenarios


@dataclass
class FakeEffectInterpreter:
    capabilities: frozenset[EffectCapability]
    fail: bool = False
    requests: list[MaterializedEffectRequest] = field(default_factory=list)

    def execute(self, request: MaterializedEffectRequest) -> EffectSucceeded | EffectFailed:
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
            RemoveNodeResource(node),
            StartRuntime(runtime),
            StopRuntime(runtime),
            RemoveRuntimeResource(runtime),
            DestroyDataResource(DataResourceTarget("postgres", "postgres-data")),
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
                abstract = effect_request_for_activity(
                    activity,
                    run_id="run-all-safe-operations",
                    attempt=1,
                    idempotency_key=f"all-safe:{ordinal}:1",
                )
                request = _materialized(abstract)
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

    def test_prepared_effect_freezes_support_decision_before_attempt(self) -> None:
        request = self._first_executable_request()
        interpreter = FakeEffectInterpreter(frozenset({request.capability}))
        prepared = prepare_effect(request, interpreter)
        interpreter.capabilities = frozenset()

        result = dispatch_prepared_effect(prepared)

        self.assertIsInstance(result, EffectSucceeded)
        self.assertEqual(interpreter.requests, [request])

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
                materialized = _materialized(request)
                interpreter = FakeEffectInterpreter(frozenset({request.capability}))
                self.assertIsInstance(dispatch_effect(materialized, interpreter), EffectSucceeded)
                executed += 1

        self.assertGreater(executed, 0)

    def test_interpreter_must_return_the_request_identity(self) -> None:
        request = self._first_executable_request()

        class WrongIdentityInterpreter(FakeEffectInterpreter):
            def execute(self, value: MaterializedEffectRequest) -> EffectSucceeded:
                other = self._different_request(value)
                return EffectSucceeded(other.identity)

            @staticmethod
            def _different_request(value: MaterializedEffectRequest) -> MaterializedEffectRequest:
                first = next(iter(planning_scenarios()))
                plan = compile_activity_plan(
                    diff_graphs(validate_graph(first.current_graph), validate_graph(first.desired_graph))
                )
                return _materialized(effect_request_for_activity(
                    plan.activities[0],
                    run_id="different-run",
                    attempt=1,
                    idempotency_key="different-key",
                ))

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
    def _first_executable_request() -> MaterializedEffectRequest:
        for scenario in planning_scenarios():
            plan = compile_activity_plan(
                diff_graphs(
                    validate_graph(scenario.current_graph),
                    validate_graph(scenario.desired_graph),
                )
            )
            if plan.ready_for_execution and plan.activities:
                return _materialized(effect_request_for_activity(
                    plan.activities[0],
                    run_id="run-1",
                    attempt=1,
                    idempotency_key="run-1:first:1",
                ))
        raise AssertionError("scenario corpus has no executable activity")


def _materialized(request: EffectRequest) -> MaterializedEffectRequest:
    return MaterializedEffectRequest(
        request,
        PinnedGraphSet("workspace", "plan", "base", "desired"),
        "desired",
        RuntimeMaterial("runtime", RuntimeKind.DOCKER, (), "network"),
    )


if __name__ == "__main__":
    unittest.main()
