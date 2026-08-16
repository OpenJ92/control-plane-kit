from __future__ import annotations

from contextlib import contextmanager
from unittest import mock

from control_plane_kit_core.operations import RecoveryDecisionKind
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
)
from control_plane_kit_operations.activity_run_retry_interpreter import (
    ActivityRunRetryCommandService,
)
from control_plane_kit_operations.effect_attempt_start_interpreter import (
    EffectAttemptStartService,
)
from control_plane_kit_operations.postgres import PostgresExecutionStore
from control_plane_kit_operations.records import ActivityEventRecord
from tests.activity_run_retry_interpreter_fixture import (
    PostgresActivityRunRetryFixture,
)
from tests.effect_attempt_record_fixture import EffectAttemptRecord
from tests.effect_attempt_start_fixture import EffectAttemptStartFixture
from tests.execution_lease_recovery_fixture import Sequence
from tests.postgres_effect_attempt_store_fixture import (
    PostgresEffectAttemptStoreFixture,
)


AUTHORITY_ERROR = "effect attempt start authority is invalid"
ELIGIBILITY_ERROR = "effect attempt start is not eligible"
INVALID_TRUTH_ERROR = "effect attempt start truth is invalid"
NOT_FOUND_ERROR = "effect attempt start truth was not found"
REPLAY_ERROR = "effect attempt replay is incongruent"
SERIALIZATION_ERROR = "effect attempt start changed concurrently"


class PostgresEffectAttemptStartFixture(
    EffectAttemptStartFixture,
    PostgresEffectAttemptStoreFixture,
):
    """Lifecycle-coherent PostgreSQL worlds for effect-attempt start."""

    def setUp(self) -> None:
        PostgresEffectAttemptStoreFixture.setUp(self)
        self.reset_start_truth()

    def tearDown(self) -> None:
        PostgresEffectAttemptStoreFixture.tearDown(self)

    def reset_start_truth(self, *, compensation: bool = False) -> None:
        history = "compensation-requested" if compensation else "active-empty"
        self.reset_truth(
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            history=history,
        )
        status = (
            ActivityRunStatus.COMPENSATING
            if compensation
            else ActivityRunStatus.RUNNING
        )
        self.connection.execute(
            "UPDATE cpk_activity_runs SET status=%s, "
            "started_at='2026-08-15T03:59:21Z' WHERE run_id='run-a'",
            (status.value,),
        )
        if not compensation:
            with self.unit_of_work() as unit_of_work:
                unit_of_work.stores.execution.add_event(
                    ActivityEventRecord(
                        "seed-run-started",
                        "run-a",
                        2,
                        ActivityEventKind.RUN_STARTED,
                        "2026-08-15T03:59:22Z",
                    )
                )
                unit_of_work.commit()

    def start_command(self, **changes):
        transition = changes.pop(
            "transition",
            self.transition(
                identity=self.identity(activity_id="start-runtime"),
            ),
        )
        return self.command(transition=transition, **changes)

    def start_service_with_sequence(self, *ids: str):
        sequence = Sequence(*ids)
        return (
            EffectAttemptStartService(
                self.unit_of_work,
                id_factory=sequence,
            ),
            sequence,
        )

    def start_service(self, *ids: str):
        return self.start_service_with_sequence(*ids)[0]

    def start_service_with_id_factory(self, id_factory):
        return EffectAttemptStartService(
            self.unit_of_work,
            id_factory=id_factory,
        )

    def attempt_snapshot(self) -> tuple[object, ...]:
        return (
            self.snapshot(),
            tuple(
                self.connection.execute(
                    "SELECT run_id, activity_id, attempt, request_fingerprint, "
                    "fence_worker_id, fence_generation, status, "
                    "outcome_fingerprint, prior_run_id, prior_activity_id, "
                    "prior_attempt, recovery_decision_id, recovery_resolution, "
                    "recovery_uncertain_fingerprint, "
                    "recovery_evidence_fingerprint, original_event_id, "
                    "original_event_run_id, original_event_ordinal, "
                    "latest_event_id, latest_event_run_id, latest_event_ordinal "
                    "FROM cpk_effect_attempts ORDER BY run_id, activity_id, attempt"
                ).fetchall()
            ),
        )

    def persisted_started(
        self,
        *,
        compensation: bool = False,
        event_id: str = "effect-a-start",
    ) -> EffectAttemptRecord:
        record = self.record(
            "started",
            compensation=compensation,
            run_id="run-a",
            activity_id="start-runtime",
            event_prefix=event_id.removesuffix("-start"),
            original_ordinal=7 if compensation else 3,
            original_time="2030-01-01T00:00:00Z",
        )
        self.assertEqual(record.original_start_event.event_id, event_id)
        self.assertEqual(self.persist(record), record)
        return record

    def fold_persisted_attempt(
        self,
        current: EffectAttemptRecord,
        *,
        story: str = "succeeded",
    ) -> EffectAttemptRecord:
        replacement = self.transition_record(
            current,
            story,
            event_id=f"effect-{story}-a",
            ordinal=current.latest_transition_event.ordinal + 1,
        )
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.execution.add_event(replacement.latest_transition_event)
            self.assertEqual(
                stores.effect_attempts.compare_and_set(current, replacement),
                replacement,
            )
            unit_of_work.commit()
        return replacement

    def transition_record(
        self,
        current: EffectAttemptRecord,
        story: str,
        *,
        event_id: str,
        ordinal: int,
    ) -> EffectAttemptRecord:
        return PostgresEffectAttemptStoreFixture.transition(
            self,
            current,
            story,
            event_id=event_id,
            ordinal=ordinal,
        )

    def expire_claim(self) -> None:
        self.connection.execute(
            "UPDATE cpk_execution_requests SET lease_expires_at="
            "'2000-01-01T00:00:00Z' WHERE request_id='request-a'"
        )

    def replace_claim(
        self,
        *,
        worker_id: str = "worker-b",
        generation: int = 8,
    ) -> None:
        self.connection.execute(
            "UPDATE cpk_execution_requests SET claim_worker_id=%s, "
            "claim_generation=%s, claimed_at='2098-01-02T00:00:00Z', "
            "lease_expires_at='2099-01-02T00:00:00Z' "
            "WHERE request_id='request-a'",
            (worker_id, generation),
        )

    def add_lawful_linked_retry(self) -> None:
        ordinal = self.connection.execute(
            "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM cpk_activity_events "
            "WHERE run_id='run-a'"
        ).fetchone()[0]
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.execution.add_event(
                ActivityEventRecord(
                    "seed-step-failed",
                    "run-a",
                    ordinal,
                    ActivityEventKind.STEP_FAILED,
                    "2030-01-01T00:00:01Z",
                    activity_id="start-runtime",
                )
            )
            stores.execution.add_event(
                ActivityEventRecord(
                    "seed-run-failed",
                    "run-a",
                    ordinal + 1,
                    ActivityEventKind.RUN_FAILED,
                    "2030-01-01T00:00:02Z",
                )
            )
            unit_of_work.commit()
        self.connection.execute(
            "UPDATE cpk_activity_runs SET status='failed' WHERE run_id='run-a'"
        )
        fixture = PostgresActivityRunRetryFixture()
        fixture.database_url = self.database_url
        fixture.connection = self.connection
        ActivityRunRetryCommandService(
            self.unit_of_work,
            id_factory=Sequence(
                "run-b",
                "retry-decision-a",
                "run-b-opened",
                "retry-action-a",
            ),
        ).execute(fixture.retry_command())

    def seed_foreign_run(self) -> None:
        fixture = PostgresActivityRunRetryFixture()
        fixture.database_url = self.database_url
        fixture.connection = self.connection
        fixture.unit_of_work = self.unit_of_work
        fixture.seed_foreign_run()

    def seed_foreign_attempt(self) -> EffectAttemptRecord:
        self.connection.execute(
            "UPDATE cpk_activity_runs SET status='running', "
            "started_at='2026-08-15T04:20:01Z' WHERE run_id='run-foreign'"
        )
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.execution.add_event(
                ActivityEventRecord(
                    "foreign-run-opened",
                    "run-foreign",
                    1,
                    ActivityEventKind.RUN_OPENED,
                    "2026-08-15T04:20:00Z",
                )
            )
            stores.execution.add_event(
                ActivityEventRecord(
                    "foreign-run-started",
                    "run-foreign",
                    2,
                    ActivityEventKind.RUN_STARTED,
                    "2026-08-15T04:20:01Z",
                )
            )
            unit_of_work.commit()
        record = self.record(
            "started",
            run_id="run-foreign",
            activity_id="start-runtime",
            event_prefix="foreign-effect",
            original_ordinal=3,
            original_time="2026-08-15T04:20:02Z",
        )
        self.assertEqual(self.persist(record), record)
        return record

    @contextmanager
    def reject_database_observation(self, message: str):
        with mock.patch.object(
            PostgresExecutionStore,
            "observe_request_lease_for_update",
            side_effect=AssertionError(message),
        ):
            yield


__all__ = [
    "AUTHORITY_ERROR",
    "ELIGIBILITY_ERROR",
    "INVALID_TRUTH_ERROR",
    "NOT_FOUND_ERROR",
    "PostgresEffectAttemptStartFixture",
    "REPLAY_ERROR",
    "SERIALIZATION_ERROR",
]
