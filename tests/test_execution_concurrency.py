from __future__ import annotations

import concurrent.futures
import os
from dataclasses import replace

import psycopg

from control_plane_kit import (
    ActivityEventKind,
    ActivityEventRecord,
    ActivityRunRecord,
    ActivityRunStatus,
    AdmittedRun,
    ExecutionRequestStatus,
    RetryIdentity,
)
from control_plane_kit.stores import PostgresUnitOfWork
from tests.postgres_case import PostgresStoreTestCase
from tests.test_execution_store import ExecutionStoreTests


class ExecutionConcurrencyTests(PostgresStoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        ExecutionStoreTests._seed_admission_truth(self.stores)

    def unit_of_work(self) -> PostgresUnitOfWork:
        database_url = os.environ["CPK_TEST_DATABASE_URL"]
        return PostgresUnitOfWork(lambda: psycopg.connect(database_url))

    def test_competing_workers_have_one_claim_winner(self):
        self.stores.execution.add_request(
            replace(
                ExecutionStoreTests._request(),
                status=ExecutionRequestStatus.QUEUED,
                claim=None,
            )
        )

        def claim(worker_id: str) -> str | None:
            with self.unit_of_work() as unit_of_work:
                result = unit_of_work.stores.execution.claim_request(
                    "execution-request-a",
                    worker_id,
                    "2026-07-16T00:04:00Z",
                    "2026-07-16T00:05:00Z",
                )
                unit_of_work.commit()
                return None if result is None else result.claim.worker_id

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            winners = tuple(executor.map(claim, ("worker-a", "worker-b")))

        self.assertEqual(sum(value is not None for value in winners), 1)
        winner = next(value for value in winners if value is not None)
        self.assertEqual(
            self.stores.execution.get_request("execution-request-a").claim.worker_id,
            winner,
        )

    def test_identical_worker_claims_converge_and_rollback_releases_lock(self):
        self.stores.execution.add_request(
            replace(
                ExecutionStoreTests._request(),
                status=ExecutionRequestStatus.QUEUED,
                claim=None,
            )
        )
        with self.unit_of_work() as unit_of_work:
            first = unit_of_work.stores.execution.claim_request(
                "execution-request-a",
                "worker-a",
                "2026-07-16T00:04:00Z",
                "2026-07-16T00:05:00Z",
            )
            self.assertIsNotNone(first)

        with self.unit_of_work() as unit_of_work:
            committed = unit_of_work.stores.execution.claim_request(
                "execution-request-a",
                "worker-a",
                "2026-07-16T00:04:00Z",
                "2026-07-16T00:05:00Z",
            )
            replay = unit_of_work.stores.execution.claim_request(
                "execution-request-a",
                "worker-a",
                "2026-07-16T00:04:30Z",
                "2026-07-16T00:05:30Z",
            )
            unit_of_work.commit()

        self.assertEqual(replay, committed)

    def test_event_ordinals_serialize_across_connections(self):
        self._seed_claimed_run()

        def append(event_id: str) -> int:
            with self.unit_of_work() as unit_of_work:
                ordinal = unit_of_work.stores.execution.next_event_ordinal("run-a")
                unit_of_work.stores.execution.add_event(
                    ActivityEventRecord(
                        event_id=event_id,
                        run_id="run-a",
                        ordinal=ordinal,
                        kind=ActivityEventKind.STEP_STARTED,
                        occurred_at=f"2026-07-16T00:05:0{ordinal}Z",
                    )
                )
                unit_of_work.commit()
                return ordinal

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            ordinals = tuple(executor.map(append, ("event-a", "event-b")))

        self.assertEqual(sorted(ordinals), [1, 2])

    def test_expired_claim_remains_durable_and_cannot_be_reclaimed(self):
        self._seed_claimed_run()
        original_claim = self.stores.execution.get_request(
            "execution-request-a"
        ).claim
        with self.unit_of_work() as unit_of_work:
            transitioned = unit_of_work.stores.execution.compare_and_set_run_status(
                "run-a",
                expected=ActivityRunStatus.CLAIMED,
                replacement=ActivityRunStatus.RUNNING,
                started_at="2026-07-16T00:05:00Z",
            )
            stale = unit_of_work.stores.execution.compare_and_set_run_status(
                "run-a",
                expected=ActivityRunStatus.CLAIMED,
                replacement=ActivityRunStatus.RUNNING,
            )
            unit_of_work.commit()

        with self.unit_of_work() as unit_of_work:
            competing_claim = unit_of_work.stores.execution.claim_request(
                "execution-request-a",
                "worker-b",
                "2026-07-16T00:06:00Z",
                "2026-07-16T00:07:00Z",
            )
            unit_of_work.commit()

        request = self.stores.execution.get_request("execution-request-a")
        run = self.stores.execution.get_run("run-a")
        self.assertIs(transitioned.status, ActivityRunStatus.RUNNING)
        self.assertIsNone(stale)
        self.assertIsNone(competing_claim)
        self.assertIs(request.status, ExecutionRequestStatus.CLAIMED)
        self.assertEqual(request.claim, original_claim)
        self.assertEqual(run.admission.request_id, request.identity.request_id)
        self.assertIs(run.status, ActivityRunStatus.RUNNING)

    def test_run_settlement_is_write_once(self):
        self._seed_claimed_run()
        with self.unit_of_work() as unit_of_work:
            running = unit_of_work.stores.execution.compare_and_set_run_status(
                "run-a",
                expected=ActivityRunStatus.CLAIMED,
                replacement=ActivityRunStatus.RUNNING,
                started_at="2026-07-16T00:05:00Z",
            )
            settled = unit_of_work.stores.execution.compare_and_set_run_status(
                "run-a",
                expected=ActivityRunStatus.RUNNING,
                replacement=ActivityRunStatus.SUCCEEDED,
                settled_at="2026-07-16T00:06:00Z",
            )
            overwritten = unit_of_work.stores.execution.compare_and_set_run_status(
                "run-a",
                expected=ActivityRunStatus.SUCCEEDED,
                replacement=ActivityRunStatus.COMPENSATED,
                settled_at="2026-07-16T00:07:00Z",
            )
            unit_of_work.commit()

        self.assertIs(running.status, ActivityRunStatus.RUNNING)
        self.assertEqual(settled.settled_at, "2026-07-16T00:06:00Z")
        self.assertIsNone(overwritten)
        self.assertEqual(
            self.stores.execution.get_run("run-a").settled_at,
            "2026-07-16T00:06:00Z",
        )

    def _seed_claimed_run(self) -> None:
        self.stores.execution.add_request(ExecutionStoreTests._request())
        self.stores.execution.add_run(
            ActivityRunRecord(
                run_id="run-a",
                plan_id="plan-a",
                admission=AdmittedRun("execution-request-a"),
                retry=RetryIdentity(1),
                status=ActivityRunStatus.CLAIMED,
                created_at="2026-07-16T00:04:00Z",
            )
        )
