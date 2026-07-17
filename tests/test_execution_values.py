import unittest

from control_plane_kit.execution import (
    DEFAULT_EXECUTION_CODEC,
    EXECUTION_VERSION,
    ActivityEventKind,
    ActivityEventRecord,
    ActivityRunRecord,
    ActivityRunStatus,
    AcceptUncompensatedFailure,
    AdmittedRun,
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
    RecoveryAuthority,
    RecoveryDecisionRecord,
    RecoveryScope,
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
                        claim=(
                            ClaimIdentity(
                                "worker-a",
                                "2026-07-16T00:00:00Z",
                                "2026-07-16T00:01:00Z",
                            )
                            if status is ExecutionRequestStatus.CLAIMED
                            else None
                        ),
                    )
                )

    def test_codec_round_trips_every_run_status(self):
        for status in ActivityRunStatus:
            with self.subTest(status=status):
                self._round_trip(self._run(status))

    def test_admitted_run_projection_rejects_impossible_timing(self):
        cases = (
            (ActivityRunStatus.CLAIMED, "started", None, "must not carry started_at"),
            (ActivityRunStatus.RUNNING, None, None, "require started_at"),
            (ActivityRunStatus.FAILED, "started", "settled", "must remain unsettled"),
            (ActivityRunStatus.COMPENSATING, "started", "settled", "must remain unsettled"),
            (ActivityRunStatus.SUCCEEDED, "started", None, "require settled_at"),
            (ActivityRunStatus.CANCELLED, None, None, "require settled_at"),
        )
        for status, started_at, settled_at, message in cases:
            with self.subTest(status=status, started_at=started_at, settled_at=settled_at):
                with self.assertRaisesRegex(ExecutionValueError, message):
                    ActivityRunRecord(
                        run_id="run-a",
                        plan_id="plan-a",
                        admission=AdmittedRun("request-a"),
                        retry=RetryIdentity(1),
                        status=status,
                        created_at="created",
                        started_at=started_at,
                        settled_at=settled_at,
                    )

    def test_stale_finished_at_descriptor_fails_closed(self):
        descriptor = DEFAULT_EXECUTION_CODEC.encode(
            self._run(ActivityRunStatus.SUCCEEDED)
        )
        value = descriptor["value"]
        self.assertIsInstance(value, dict)
        value["finished_at"] = value.pop("settled_at")

        with self.assertRaises(MalformedExecutionDescriptor):
            DEFAULT_EXECUTION_CODEC.decode(descriptor)

    def test_codec_round_trips_every_event_kind(self):
        step_kinds = {
            ActivityEventKind.STEP_STARTED,
            ActivityEventKind.STEP_SUCCEEDED,
            ActivityEventKind.STEP_FAILED,
            ActivityEventKind.STEP_UNSUPPORTED,
            ActivityEventKind.STEP_UNCERTAIN,
            ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_SUCCEEDED,
            ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_FAILED,
            ActivityEventKind.STEP_COMPENSATION_STARTED,
            ActivityEventKind.STEP_COMPENSATION_SUCCEEDED,
            ActivityEventKind.STEP_COMPENSATION_FAILED,
        }
        for kind in ActivityEventKind:
            with self.subTest(kind=kind):
                self._round_trip(
                    ActivityEventRecord(
                        event_id="event-a",
                        run_id="run-a",
                        ordinal=1,
                        kind=kind,
                        occurred_at="2026-07-16T00:00:00Z",
                        activity_id="start-api" if kind in step_kinds else None,
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
                        recovery=(
                            RecoveryDecisionRecord(
                                "decision-a",
                                AcceptUncompensatedFailure(),
                                RecoveryAuthority(
                                    "operator-a",
                                    "grant-a",
                                    (RecoveryScope.ACCEPT_LOSS,),
                                ),
                                "The operator accepts the visible loss.",
                            )
                            if kind is ActivityEventKind.RECOVERY_DECISION_RECORDED
                            else None
                        ),
                    )
                )

    def test_event_scope_is_valid_by_construction(self):
        with self.assertRaisesRegex(ExecutionValueError, "step event requires"):
            ActivityEventRecord(
                "event-a",
                "run-a",
                1,
                ActivityEventKind.STEP_STARTED,
                "2026-07-16T00:00:00Z",
            )
        with self.assertRaisesRegex(ExecutionValueError, "run event must not"):
            ActivityEventRecord(
                "event-a",
                "run-a",
                1,
                ActivityEventKind.RUN_STARTED,
                "2026-07-16T00:00:00Z",
                activity_id="start-api",
            )

    def test_recovery_event_descriptor_is_strict_and_attributable(self):
        event = ActivityEventRecord(
            "event-recovery",
            "run-a",
            1,
            ActivityEventKind.RECOVERY_DECISION_RECORDED,
            "2026-07-16T00:00:00Z",
            recovery=RecoveryDecisionRecord(
                "decision-a",
                AcceptUncompensatedFailure(),
                RecoveryAuthority(
                    "operator-a",
                    "grant-a",
                    (RecoveryScope.ACCEPT_LOSS,),
                ),
                "The original failure cannot be safely compensated.",
            ),
        )
        self._round_trip(event)

        descriptor = DEFAULT_EXECUTION_CODEC.encode(event)
        value = descriptor["value"]
        self.assertIsInstance(value, dict)
        recovery = value["recovery"]
        self.assertIsInstance(recovery, dict)
        recovery["unexpected"] = "must-fail-closed"

        with self.assertRaises(MalformedExecutionDescriptor):
            DEFAULT_EXECUTION_CODEC.decode(descriptor)

    def test_unknown_recovery_members_are_not_malformed_shapes(self):
        event = ActivityEventRecord(
            "event-recovery",
            "run-a",
            1,
            ActivityEventKind.RECOVERY_DECISION_RECORDED,
            "2026-07-16T00:00:00Z",
            recovery=RecoveryDecisionRecord(
                "decision-a",
                AcceptUncompensatedFailure(),
                RecoveryAuthority(
                    "operator-a",
                    "grant-a",
                    (RecoveryScope.ACCEPT_LOSS,),
                ),
                "The original failure cannot be safely compensated.",
            ),
        )
        for path, unknown in (
            (("decision", "kind"), "unknown-decision"),
            (("scopes", 0), "recovery:unknown"),
        ):
            with self.subTest(path=path):
                descriptor = DEFAULT_EXECUTION_CODEC.encode(event)
                value = descriptor["value"]
                self.assertIsInstance(value, dict)
                recovery = value["recovery"]
                self.assertIsInstance(recovery, dict)
                if path[0] == "decision":
                    decision = recovery["decision"]
                    self.assertIsInstance(decision, dict)
                    decision[path[1]] = unknown
                else:
                    scopes = recovery["scopes"]
                    self.assertIsInstance(scopes, list)
                    scopes[path[1]] = unknown

                with self.assertRaises(UnknownExecutionVariant):
                    DEFAULT_EXECUTION_CODEC.decode(descriptor)

    def test_recovery_evidence_cannot_ride_on_an_unrelated_event(self):
        recovery = RecoveryDecisionRecord(
            "decision-a",
            AcceptUncompensatedFailure(),
            RecoveryAuthority(
                "operator-a",
                "grant-a",
                (RecoveryScope.ACCEPT_LOSS,),
            ),
            "The original failure cannot be safely compensated.",
        )
        with self.assertRaisesRegex(ExecutionValueError, "only recovery decision"):
            ActivityEventRecord(
                "event-a",
                "run-a",
                1,
                ActivityEventKind.RUN_FAILED,
                "2026-07-16T00:00:00Z",
                recovery=recovery,
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
            AdmittedRun("request-a"),
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
                admission=AdmittedRun("request-a"),
                retry=RetryIdentity(1),
                status="running",  # type: ignore[arg-type]
                created_at="2026-07-16T00:00:00Z",
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

    @staticmethod
    def _run(status: ActivityRunStatus) -> ActivityRunRecord:
        started_at = None if status is ActivityRunStatus.CLAIMED else "started"
        settled_at = (
            "settled"
            if status
            in {
                ActivityRunStatus.SUCCEEDED,
                ActivityRunStatus.COMPENSATED,
                ActivityRunStatus.PARTIALLY_FAILED,
                ActivityRunStatus.UNCOMPENSATED_FAILURE,
                ActivityRunStatus.CANCELLED,
            }
            else None
        )
        return ActivityRunRecord(
            run_id="run-a",
            plan_id="plan-a",
            admission=AdmittedRun("request-a"),
            retry=RetryIdentity(1),
            status=status,
            created_at="created",
            started_at=started_at,
            settled_at=settled_at,
            metadata=BoundedEvidence.from_mapping({"worker": "agent-a"}),
        )


if __name__ == "__main__":
    unittest.main()
