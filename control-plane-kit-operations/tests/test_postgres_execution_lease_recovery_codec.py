from __future__ import annotations

import json
import os
import unittest

import psycopg

from tests.graph_lineage_fixture import seed_identity_graphs

from control_plane_kit_core.operations import RunId
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    RecoveryDecisionKind,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.postgres import (
    PostgresExecutionStore,
    PostgresUnitOfWork,
    install_schema,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ExecutionLeaseRecoveryEvidence,
    OperationsRecordError,
)


class PostgresExecutionLeaseRecoveryCodecTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required; run through Docker"
            )
        self.database_url = database_url
        self.connection = self.connect()
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")

    def tearDown(self) -> None:
        if not self.connection.closed:
            self.connection.close()

    def connect(self):
        return psycopg.connect(self.database_url, autocommit=True)

    def unit_of_work(self) -> PostgresUnitOfWork:
        return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url))

    def evidence(self, decision: RecoveryDecisionKind):
        prior = ExecutionLeaseFence("worker-a", 7)
        if decision in (
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
        ):
            replacement = ExecutionLeaseFence("worker-a", 8)
        elif decision is RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM:
            replacement = ExecutionLeaseFence("worker-b", 8)
        else:
            replacement = None
        return ExecutionLeaseRecoveryEvidence(
            decision, RunId("run-a"), prior, replacement
        )

    def event(self, event_id: str, decision: RecoveryDecisionKind):
        return ActivityEventRecord(
            event_id,
            "run-a",
            1,
            ActivityEventKind.RECOVERY_DECISION_RECORDED,
            "2026-08-15T04:00:00Z",
            recovery=self.evidence(decision),
        )

    def assert_safe_record_error(
        self, error: BaseException, *canaries: str
    ) -> None:
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = f"{error!s} {error!r}"
        self.assertLessEqual(len(rendered), 512)
        for canary in canaries:
            self.assertNotIn(canary, rendered)

    def test_current_named_check_already_rejects_impossible_recovery_shapes(self) -> None:
        self.seed_run()
        cases = (
            (
                "missing",
                "recovery_decision_recorded",
                {"activity_id": None, "evidence": {}, "failure": None},
            ),
            (
                "null",
                "recovery_decision_recorded",
                {
                    "activity_id": None,
                    "evidence": {},
                    "failure": None,
                    "recovery": None,
                },
            ),
            (
                "list",
                "recovery_decision_recorded",
                {
                    "activity_id": None,
                    "evidence": {},
                    "failure": None,
                    "recovery": [],
                },
            ),
            (
                "scalar",
                "recovery_decision_recorded",
                {
                    "activity_id": None,
                    "evidence": {},
                    "failure": None,
                    "recovery": "candidate",
                },
            ),
            (
                "wrong-event",
                "run_opened",
                {
                    "activity_id": None,
                    "evidence": {},
                    "failure": None,
                    "recovery": {},
                },
            ),
        )
        for index, (name, event_type, payload) in enumerate(cases, start=1):
            with self.subTest(case=name):
                with self.assertRaises(psycopg.errors.CheckViolation) as captured:
                    self.insert_raw_event(
                        f"check-event-{index}", event_type, payload
                    )
                self.assertEqual(
                    captured.exception.diag.constraint_name,
                    "cpk_activity_events_shape_check",
                )

    def test_typed_recovery_round_trips_after_connection_restart(self) -> None:
        decisions = (
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
            RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM,
            RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM,
        )
        for decision in decisions:
            with self.subTest(decision=decision):
                self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
                self.seed_run(decision)
                event = self.event(f"event-{decision.value}", decision)
                store = PostgresExecutionStore(self.connection)
                self.assertIs(store.add_event(event), event)

                self.connection.close()
                self.connection = self.connect()
                restarted = PostgresExecutionStore(self.connection)
                self.assertEqual(restarted.get_event(event.event_id), event)
                self.assertEqual(restarted.events_for_run("run-a"), (event,))

    def test_schema_admissible_malformed_objects_fail_bounded_and_candidate_free(
        self,
    ) -> None:
        self.seed_run()
        valid = self.evidence(
            RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM
        ).descriptor()
        oversized = "oversized-fixed-key-canary-" + "x" * 200
        nested = "nested-secret-canary"
        corruptions = (
            (
                "missing",
                {
                    key: value
                    for key, value in valid.items()
                    if key != "decision"
                },
                (),
            ),
            ("extra", {**valid, "extra": "extra-canary"}, ("extra-canary",)),
            (
                "unknown",
                {**valid, "decision": "unknown-decision-canary"},
                ("unknown-decision-canary",),
            ),
            (
                "run",
                {**valid, "retained_run_id": "../run-canary"},
                ("../run-canary",),
            ),
            (
                "prior-fence",
                {
                    **valid,
                    "prior_fence": {"worker_id": "", "generation": 7},
                },
                (),
            ),
            (
                "prior-fence-null",
                {**valid, "prior_fence": None},
                (),
            ),
            (
                "prior-fence-list",
                {**valid, "prior_fence": ["prior-list-canary"]},
                ("prior-list-canary",),
            ),
            (
                "prior-fence-scalar",
                {**valid, "prior_fence": "prior-scalar-canary"},
                ("prior-scalar-canary",),
            ),
            (
                "replacement-fence",
                {
                    **valid,
                    "replacement_fence": {
                        "worker_id": "worker-b",
                        "generation": True,
                    },
                },
                (),
            ),
            (
                "replacement-fence-null",
                {**valid, "replacement_fence": None},
                (),
            ),
            (
                "replacement-fence-list",
                {
                    **valid,
                    "replacement_fence": ["replacement-list-canary"],
                },
                ("replacement-list-canary",),
            ),
            (
                "replacement-fence-scalar",
                {
                    **valid,
                    "replacement_fence": "replacement-scalar-canary",
                },
                ("replacement-scalar-canary",),
            ),
            (
                "takeover-same-worker",
                {
                    **valid,
                    "replacement_fence": {
                        "worker_id": "worker-a",
                        "generation": 8,
                    },
                },
                (),
            ),
            (
                "renew-missing-replacement",
                {
                    **valid,
                    "decision": RecoveryDecisionKind.RENEW_ACTIVE_CLAIM.value,
                    "replacement_fence": None,
                },
                (),
            ),
            (
                "renew-wrong-worker",
                {
                    **valid,
                    "decision": RecoveryDecisionKind.RENEW_ACTIVE_CLAIM.value,
                    "replacement_fence": {
                        "worker_id": "worker-b",
                        "generation": 8,
                    },
                },
                (),
            ),
            (
                "renew-wrong-generation",
                {
                    **valid,
                    "decision": RecoveryDecisionKind.RENEW_ACTIVE_CLAIM.value,
                    "replacement_fence": {
                        "worker_id": "worker-a",
                        "generation": 9,
                    },
                },
                (),
            ),
            (
                "abandon-with-replacement",
                {
                    **valid,
                    "decision": RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM.value,
                },
                (),
            ),
            (
                "oversized",
                {**valid, "retained_run_id": oversized},
                (oversized[:32],),
            ),
            (
                "nested-extra",
                {
                    **valid,
                    "prior_fence": {
                        **valid["prior_fence"],
                        "secret_token": nested,
                    },
                },
                (nested,),
            ),
        )
        for index, (name, recovery, canaries) in enumerate(corruptions, start=1):
            event_id = f"corrupt-event-{index}"
            self.insert_raw_event(
                event_id,
                "recovery_decision_recorded",
                {
                    "activity_id": None,
                    "evidence": {},
                    "failure": None,
                    "recovery": recovery,
                },
                ordinal=index,
            )
            self.connection.close()
            self.connection = self.connect()
            with self.subTest(case=name):
                with self.assertRaises(OperationsRecordError) as captured:
                    PostgresExecutionStore(self.connection).get_event(event_id)
                self.assert_safe_record_error(captured.exception, *canaries)

    def insert_raw_event(
        self,
        event_id: str,
        event_type: str,
        payload: object,
        *,
        ordinal: int = 1,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO cpk_activity_events
              (event_id, run_id, ordinal, event_type, occurred_at, payload)
            VALUES (%s, 'run-a', %s, %s, '2026-08-15T04:00:00Z', %s::jsonb)
            """,
            (event_id, ordinal, event_type, json.dumps(payload)),
        )

    def seed_run(self, decision: RecoveryDecisionKind | None = None) -> None:
        abandoned = decision is RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM
        if decision is None:
            request_status = "claimed"
            claim_worker_id = "worker-a"
            claim_generation = 7
        elif abandoned:
            request_status = "abandoned"
            claim_worker_id = None
            claim_generation = None
        else:
            replacement = self.evidence(decision).replacement_fence
            assert replacement is not None
            request_status = "claimed"
            claim_worker_id = replacement.worker_id
            claim_generation = replacement.generation
        claimed_at = None if abandoned else "2026-08-15T03:59:10Z"
        lease_expires_at = None if abandoned else "2026-08-15T04:30:00Z"
        active = decision in (None, RecoveryDecisionKind.RENEW_ACTIVE_CLAIM)
        run_status = "claimed" if active else "failed"
        started_at = None if active else "2026-08-15T03:59:40Z"

        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created')
            """
        )
        with self.unit_of_work() as unit_of_work:
            lineage = seed_identity_graphs(
                unit_of_work.stores,
                workspace_id="workspace-a",
                graph_ids=("graph-current", "graph-desired"),
            )
            unit_of_work.commit()
        self.connection.execute(
            """
            INSERT INTO cpk_operation_sessions
              (session_id, workspace_id, actor_id, title, status, created_at)
            VALUES ('session-a', 'workspace-a', 'operator-a', 'Deploy', 'open',
                    '2026-08-15T03:55:00Z')
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_activity_plans
              (plan_id, session_id, base_graph_id, desired_graph_id,
               base_realized_projection_id, desired_realized_projection_id,
               status, created_at, payload)
            VALUES ('plan-a', 'session-a', 'graph-current', 'graph-desired',
                    %s, %s, 'planned', '2026-08-15T03:56:00Z', '{}'::jsonb)
            """,
            (lineage["graph-current"], lineage["graph-desired"]),
        )
        self.connection.execute(
            """
            INSERT INTO cpk_approval_requests
              (request_id, session_id, plan_id, subject_kind, subject_payload,
               review_digest, requested_by, requested_at,
               required_scope, max_risk, destructive)
            VALUES ('approval-request-a', 'session-a', 'plan-a', 'activity-plan',
                    '{"kind":"activity-plan","plan_id":"plan-a"}'::jsonb,
                    encode(sha256(convert_to('activity-plan:plan-a', 'UTF8')), 'hex'),
                    'operator-a', '2026-08-15T03:57:00Z',
                    'plan:approve', 'low', false)
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_approval_decisions
              (decision_id, request_id, actor_id, decision, scope, decided_at)
            VALUES ('approval-decision-a', 'approval-request-a', 'manager-a',
                    'approved', 'plan:approve', '2026-08-15T03:58:00Z')
            """
        )
        self.connection.execute(
            """
            INSERT INTO cpk_execution_requests
              (request_id, workspace_id, session_id, plan_id, status,
               requested_by, requested_at, approval_request_id,
               approval_decision_id, idempotency_key, intent_fingerprint,
               claim_worker_id, claim_generation, claimed_at, lease_expires_at)
            VALUES ('request-a', 'workspace-a', 'session-a', 'plan-a', %s,
                    'operator-a', '2026-08-15T03:59:00Z', 'approval-request-a',
                    'approval-decision-a', 'execute-a', 'fingerprint-a',
                    %s, %s, %s, %s)
            """,
            (
                request_status,
                claim_worker_id,
                claim_generation,
                claimed_at,
                lease_expires_at,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO cpk_activity_runs
              (run_id, plan_id, request_id, attempt, status, created_at,
               started_at, metadata)
            VALUES ('run-a', 'plan-a', 'request-a', 1, %s,
                    '2026-08-15T03:59:30Z', %s, '{}'::jsonb)
            """,
            (run_status, started_at),
        )


if __name__ == "__main__":
    unittest.main()
