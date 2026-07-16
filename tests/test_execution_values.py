import unittest

from control_plane_kit.execution import (
    DEFAULT_EXECUTION_CODEC,
    EXECUTION_VERSION,
    ActivityEventKind,
    ActivityEventRecord,
    ActivityRunRecord,
    ActivityRunStatus,
    BoundedEvidence,
    ClaimIdentity,
    ExecutionIdempotency,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    ExecutionRequestStatus,
    ExecutionValueError,
    FailureCategory,
    FailureEvidence,
    LossyExecutionDescriptor,
    MAX_EVIDENCE_BYTES,
    MalformedExecutionDescriptor,
    ObservationFreshness,
    ObservationRecord,
    ObservationStatus,
    RetryIdentity,
    UnknownExecutionVariant,
)


class ExecutionValueTests(unittest.TestCase):
    def test_codec_round_trips_every_closed_request_status(self):
        for status in ExecutionRequestStatus:
            with self.subTest(status=status):
                self._round_trip(
                    ExecutionRequestRecord(
                        identity=ExecutionRequestIdentity(
                            "request-a", "workspace-a", "session-a", "plan-a"
                        ),
                        status=status,
                        requested_by="operator",
                        requested_at="2026-07-16T00:00:00Z",
                        approval_request_id="approval-request-a",
                        approval_decision_id="approval-decision-a",
                        idempotency=ExecutionIdempotency("execute-a", "fingerprint-a"),
                    )
                )

    def test_codec_round_trips_every_run_status(self):
        for status in ActivityRunStatus:
            with self.subTest(status=status):
                self._round_trip(
                    ActivityRunRecord(
                        run_id="run-a",
                        plan_id="plan-a",
                        status=status,
                        started_at="2026-07-16T00:00:00Z",
                        metadata=BoundedEvidence.from_mapping({"worker": "agent-a"}),
                    )
                )

    def test_codec_round_trips_every_event_kind(self):
        for kind in ActivityEventKind:
            with self.subTest(kind=kind):
                self._round_trip(
                    ActivityEventRecord(
                        event_id="event-a",
                        run_id="run-a",
                        ordinal=1,
                        kind=kind,
                        occurred_at="2026-07-16T00:00:00Z",
                        activity_id="start-api",
                        evidence=BoundedEvidence.from_mapping({"target": "api"}),
                        failure=(
                            FailureEvidence(
                                FailureCategory.UNCERTAIN,
                                "effect-result-missing",
                                "Effect may have completed without durable result evidence.",
                            )
                            if kind is ActivityEventKind.STEP_UNCERTAIN
                            else None
                        ),
                    )
                )

    def test_codec_round_trips_every_observation_status_and_freshness(self):
        for status in ObservationStatus:
            for freshness in ObservationFreshness:
                with self.subTest(status=status, freshness=freshness):
                    self._round_trip(
                        ObservationRecord(
                            observation_id="observation-a",
                            workspace_id="workspace-a",
                            subject_id="api",
                            status=status,
                            observed_at="2026-07-16T00:00:00Z",
                            freshness=freshness,
                        )
                    )

    def test_codec_round_trips_claim_retry_and_every_failure_category(self):
        values = [
            ClaimIdentity(
                "worker-a",
                "2026-07-16T00:00:00Z",
                "2026-07-16T00:01:00Z",
            ),
            RetryIdentity(2, "run-a"),
        ]
        values.extend(
            FailureEvidence(
                category,
                "health-timeout",
                "Health did not become ready before the bounded timeout.",
                BoundedEvidence.from_mapping({"attempts": 3}),
            )
            for category in FailureCategory
        )
        for value in values:
            with self.subTest(value=type(value).__name__):
                self._round_trip(value)

    def test_open_string_lifecycle_values_are_rejected(self):
        with self.assertRaisesRegex(TypeError, "ExecutionRequestStatus"):
            ExecutionRequestRecord(
                identity=ExecutionRequestIdentity(
                    "request-a", "workspace-a", "session-a", "plan-a"
                ),
                status="queued",  # type: ignore[arg-type]
                requested_by="operator",
                requested_at="2026-07-16T00:00:00Z",
                approval_request_id="approval-request-a",
                approval_decision_id="approval-decision-a",
                idempotency=ExecutionIdempotency("execute-a", "fingerprint-a"),
            )
        with self.assertRaisesRegex(TypeError, "ActivityRunStatus"):
            ActivityRunRecord(
                run_id="run-a",
                plan_id="plan-a",
                status="running",  # type: ignore[arg-type]
                started_at="2026-07-16T00:00:00Z",
            )
        with self.assertRaisesRegex(TypeError, "ObservationStatus"):
            ObservationRecord(
                observation_id="observation-a",
                workspace_id="workspace-a",
                subject_id="api",
                status="healthy",  # type: ignore[arg-type]
                observed_at="2026-07-16T00:00:00Z",
            )

    def test_descriptor_rejects_unknown_version_variant_and_extra_fields(self):
        value = RetryIdentity(1)
        descriptor = DEFAULT_EXECUTION_CODEC.encode(value)
        descriptor["version"] = EXECUTION_VERSION + 1
        with self.assertRaises(UnknownExecutionVariant):
            DEFAULT_EXECUTION_CODEC.decode(descriptor)

        descriptor = DEFAULT_EXECUTION_CODEC.encode(value)
        descriptor["value"]["kind"] = "invented"
        with self.assertRaises(UnknownExecutionVariant):
            DEFAULT_EXECUTION_CODEC.decode(descriptor)

        descriptor = DEFAULT_EXECUTION_CODEC.encode(value)
        descriptor["value"]["extra"] = True
        with self.assertRaises(LossyExecutionDescriptor):
            DEFAULT_EXECUTION_CODEC.decode(descriptor)

        with self.assertRaises(MalformedExecutionDescriptor):
            DEFAULT_EXECUTION_CODEC.decode({"schema": "control-plane-kit.execution"})

    def test_evidence_is_copied_bounded_and_rejects_secret_shaped_fields(self):
        source = {"target": "api", "nested": {"attempt": 1}}
        evidence = BoundedEvidence.from_mapping(source)
        source["target"] = "mutated"
        self.assertEqual(evidence.descriptor()["target"], "api")

        with self.assertRaisesRegex(ExecutionValueError, "secret-shaped"):
            BoundedEvidence.from_mapping({"access_token": "not-allowed"})
        with self.assertRaisesRegex(ExecutionValueError, "finite number"):
            BoundedEvidence.from_mapping({"latency": float("nan")})
        with self.assertRaisesRegex(ExecutionValueError, "encoded bytes"):
            BoundedEvidence.from_mapping(
                {f"value_{index}": "x" * 1_024 for index in range(9)}
            )

    def test_retry_identity_rejects_impossible_lineage(self):
        with self.assertRaisesRegex(ExecutionValueError, "first attempt"):
            RetryIdentity(1, "run-before-first")
        with self.assertRaisesRegex(ExecutionValueError, "prior run"):
            RetryIdentity(2)

    def _round_trip(self, value: object) -> None:
        descriptor = DEFAULT_EXECUTION_CODEC.encode(value)
        self.assertEqual(DEFAULT_EXECUTION_CODEC.decode(descriptor), value)
        self.assertEqual(DEFAULT_EXECUTION_CODEC.dumps(value), DEFAULT_EXECUTION_CODEC.dumps(value))


if __name__ == "__main__":
    unittest.main()
