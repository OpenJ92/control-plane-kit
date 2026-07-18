from __future__ import annotations

import unittest

from control_plane_kit.effects import (
    MAX_EFFECT_TIMEOUT_SECONDS,
    ActivateTarget,
    DrainTarget,
    EffectCapability,
    EffectIdentity,
    EffectPurpose,
    EffectRequest,
    EffectSecretReference,
    EffectValueError,
    EndpointReference,
    ObservationKind,
    ObserveSubject,
    RegisterTarget,
    RegisterObserver,
    TimeoutPolicy,
    UnsupportedEffectOperation,
    effect_request_for_activity,
    required_capability,
)
from control_plane_kit.planning import (
    ActivityId,
    AddSocketConnection,
    ChangeTarget,
    DataResourceTarget,
    DestroyDataResource,
    PlannedActivity,
    ReconcileNode,
    ReconcileRuntime,
    RemoveNodeResource,
    RemoveRuntimeResource,
    RemoveSocketConnection,
    ReviewChange,
    ReviewReason,
    NodeTarget,
    RuntimeTarget,
    SocketConnectionTarget,
    StartNode,
    StartRuntime,
    StopNode,
    StopRuntime,
    SwitchSocketConnection,
    WaitForHealthy,
)
from control_plane_kit.topology import GraphSubject


class EffectValueTests(unittest.TestCase):
    def test_every_safe_activity_operation_has_one_closed_capability(self) -> None:
        node = NodeTarget("api")
        runtime = RuntimeTarget("docker")
        edge = SocketConnectionTarget("auth-api")
        cases = (
            (StartNode(node), EffectCapability.NODE_LIFECYCLE),
            (StopNode(node), EffectCapability.NODE_LIFECYCLE),
            (RemoveNodeResource(node), EffectCapability.NODE_LIFECYCLE),
            (StartRuntime(runtime), EffectCapability.RUNTIME_LIFECYCLE),
            (StopRuntime(runtime), EffectCapability.RUNTIME_LIFECYCLE),
            (RemoveRuntimeResource(runtime), EffectCapability.RUNTIME_LIFECYCLE),
            (
                DestroyDataResource(DataResourceTarget("postgres", "postgres-data")),
                EffectCapability.DATA_DESTRUCTION,
            ),
            (WaitForHealthy(node), EffectCapability.HEALTH_PROBE),
            (AddSocketConnection(edge), EffectCapability.SOCKET_RECONCILIATION),
            (SwitchSocketConnection(edge), EffectCapability.SOCKET_RECONCILIATION),
            (RemoveSocketConnection(edge), EffectCapability.SOCKET_RECONCILIATION),
            (ReconcileNode(node), EffectCapability.NODE_RECONCILIATION),
            (ReconcileRuntime(runtime), EffectCapability.RUNTIME_RECONCILIATION),
        )

        for action, expected in cases:
            with self.subTest(action=type(action).__name__):
                self.assertIs(required_capability(action), expected)

    def test_control_actions_are_provider_neutral_and_typed(self) -> None:
        cases = (
            (
                RegisterTarget("router", "api-v2", EndpointReference("api-v2-internal")),
                EffectCapability.TARGET_REGISTRATION,
            ),
            (ActivateTarget("router", "api-v2"), EffectCapability.TARGET_SWITCHING),
            (DrainTarget("router", "api-v1"), EffectCapability.TARGET_DRAIN),
            (
                RegisterObserver("mux", "logger", EndpointReference("logger-internal")),
                EffectCapability.OBSERVER_REGISTRATION,
            ),
            (
                ObserveSubject("api-v2", ObservationKind.HEALTH),
                EffectCapability.OBSERVATION,
            ),
        )

        for action, expected in cases:
            with self.subTest(action=type(action).__name__):
                self.assertIs(required_capability(action), expected)

    def test_review_work_cannot_cross_the_effect_boundary(self) -> None:
        review = ReviewChange(
            ChangeTarget(GraphSubject()),
            ReviewReason.UNSUPPORTED_CHANGE,
        )
        identity = EffectIdentity("run-1", ActivityId("review"), 1, "review-1")

        with self.assertRaisesRegex(UnsupportedEffectOperation, "review-required"):
            EffectRequest(identity, review)

    def test_activity_factory_preserves_identity_policy_and_secret_references(self) -> None:
        activity = PlannedActivity(ActivityId("start-api"), StartNode(NodeTarget("api")))
        request = effect_request_for_activity(
            activity,
            run_id="run-1",
            attempt=2,
            idempotency_key="run-1:start-api:2",
            timeout=TimeoutPolicy(total_seconds=45),
            secret_references=(EffectSecretReference("control-token"),),
        )

        self.assertEqual(request.identity.run_id, "run-1")
        self.assertEqual(request.identity.activity_id, activity.activity_id)
        self.assertEqual(request.identity.attempt, 2)
        self.assertEqual(request.timeout.total_seconds, 45)
        self.assertEqual(request.secret_references[0].reference_id, "control-token")
        self.assertIs(request.purpose, EffectPurpose.FORWARD)
        self.assertIs(request.capability, EffectCapability.NODE_LIFECYCLE)

    def test_request_rejects_open_effect_purpose(self) -> None:
        identity = EffectIdentity("run-1", ActivityId("start-api"), 1, "key-1")

        with self.assertRaisesRegex(TypeError, "purpose"):
            EffectRequest(
                identity,
                StartNode(NodeTarget("api")),
                purpose="forward",  # type: ignore[arg-type]
            )

    def test_timeout_policy_is_bounded(self) -> None:
        with self.assertRaises(EffectValueError):
            TimeoutPolicy(total_seconds=0)
        with self.assertRaises(EffectValueError):
            TimeoutPolicy(total_seconds=MAX_EFFECT_TIMEOUT_SECONDS + 1)
        with self.assertRaises(EffectValueError):
            TimeoutPolicy(total_seconds=5, interval_seconds=6)

    def test_secret_and_endpoint_values_are_opaque_references(self) -> None:
        for unsafe in (
            "postgresql://user:secret@db/app",
            "TOKEN=secret",
            "https://api.example.test",
            "contains spaces",
        ):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(EffectValueError):
                    EffectSecretReference(unsafe)
                with self.assertRaises(EffectValueError):
                    EndpointReference(unsafe)

    def test_request_rejects_duplicate_secret_references(self) -> None:
        identity = EffectIdentity("run-1", ActivityId("start-api"), 1, "key-1")
        secret = EffectSecretReference("control-token")
        with self.assertRaisesRegex(EffectValueError, "repeats"):
            EffectRequest(
                identity,
                StartNode(NodeTarget("api")),
                secret_references=(secret, secret),
            )


if __name__ == "__main__":
    unittest.main()
