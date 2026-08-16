from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from unittest import mock

from control_plane_kit_core.operations import (
    EffectAttemptTransition,
    EffectAttemptTransitionKind,
    EffectRecoveryDecision,
    EffectRecoveryResolution,
    fold_effect_attempt,
)
from control_plane_kit_operations.effect_attempt_fold import FoldEffectAttempt
from control_plane_kit_operations.effect_attempt_fold_interpreter import (
    EffectAttemptFoldService,
)
from control_plane_kit_operations.effect_attempts import (
    EffectAttemptEventEvidence,
    EffectAttemptRecord,
    effect_attempt_state_fingerprint,
)
from control_plane_kit_operations.postgres import PostgresExecutionStore
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    BoundedEvidence,
    FailureCategory,
    FailureEvidence,
)
from tests.effect_attempt_fold_fixture import FAILURE_STORIES
from tests.execution_lease_recovery_fixture import Sequence
from tests.postgres_effect_attempt_start_fixture import (
    PostgresEffectAttemptStartFixture,
)


AUTHORITY_ERROR = "effect attempt fold authority is invalid"
INVALID_TRUTH_ERROR = "effect attempt fold truth is invalid"
NOT_FOUND_ERROR = "effect attempt fold truth was not found"
REPLAY_ERROR = "effect attempt fold is incongruent"
SERIALIZATION_ERROR = "effect attempt fold changed concurrently"

OUTCOME_FINGERPRINT = "b" * 64
UNCERTAIN_FINGERPRINT = "c" * 64
RECOVERY_FINGERPRINT = "d" * 64
DIRECT_STORIES = ("succeeded", "failed", "unsupported", "uncertain")
RECOVERY_STORIES = ("recovered-succeeded", "recovered-failed", "abandoned")
FOLD_STORIES = DIRECT_STORIES + RECOVERY_STORIES


class _CheckedFoldService:
    def __init__(self, fixture, service: EffectAttemptFoldService) -> None:
        self._fixture = fixture
        self._service = service

    def execute(self, command):
        try:
            return self._service.execute(command)
        except NotImplementedError:
            self._fixture.fail("effect-attempt fold transaction is missing")


class PostgresEffectAttemptFoldFixture(PostgresEffectAttemptStartFixture):
    """Lifecycle-coherent PostgreSQL worlds for one effect-attempt fold."""

    def fold_transition(self, story: str):
        identity = self.identity(activity_id="start-runtime")
        if story in DIRECT_STORIES:
            kind = {
                "succeeded": EffectAttemptTransitionKind.SUCCEEDED,
                "failed": EffectAttemptTransitionKind.FAILED,
                "unsupported": EffectAttemptTransitionKind.UNSUPPORTED,
                "uncertain": EffectAttemptTransitionKind.UNCERTAIN,
            }[story]
            return EffectAttemptTransition(
                kind,
                identity,
                outcome_fingerprint=(
                    UNCERTAIN_FINGERPRINT
                    if story == "uncertain"
                    else OUTCOME_FINGERPRINT
                ),
            )

        resolution = {
            "recovered-succeeded": EffectRecoveryResolution.SUCCEEDED,
            "recovered-failed": EffectRecoveryResolution.FAILED,
            "abandoned": EffectRecoveryResolution.ABANDONED,
        }[story]
        decision = EffectRecoveryDecision(
            "decision-a",
            identity,
            resolution,
            UNCERTAIN_FINGERPRINT,
            RECOVERY_FINGERPRINT,
        )
        return EffectAttemptTransition(
            EffectAttemptTransitionKind.ABANDONED
            if story == "abandoned"
            else EffectAttemptTransitionKind.RECONCILED,
            identity,
            recovery_decision=decision,
        )

    def failure(self, marker: str = "bounded") -> FailureEvidence:
        return FailureEvidence(
            FailureCategory.TERMINAL,
            f"failure-{marker}",
            f"safe failure {marker}",
        )

    def fold_command(self, story: str = "succeeded", **changes):
        values = {
            "request_id": "request-a",
            "transition": self.fold_transition(story),
            "authority": self.authority(),
            "fence": self.fence(),
            "failure": self.failure(story) if story in FAILURE_STORIES else None,
        }
        values.update(changes)
        return FoldEffectAttempt(**values)

    def fold_service_with_sequence(self, *ids: str):
        sequence = Sequence(*ids)
        return (
            self.checked_fold_service(
                EffectAttemptFoldService(self.unit_of_work, id_factory=sequence)
            ),
            sequence,
        )

    def fold_service(self, *ids: str):
        return self.fold_service_with_sequence(*ids)[0]

    def fold_service_with_id_factory(self, id_factory):
        return self.checked_fold_service(
            EffectAttemptFoldService(
                self.unit_of_work,
                id_factory=id_factory,
            )
        )

    def checked_fold_service(self, service: EffectAttemptFoldService):
        return _CheckedFoldService(self, service)

    def seed_fold_source(
        self,
        story: str,
        *,
        compensation: bool = False,
    ) -> EffectAttemptRecord:
        self.reset_start_truth(compensation=compensation)
        started = self.persisted_started(
            compensation=compensation,
            event_id=f"effect-{int(compensation)}-start",
        )
        if story not in RECOVERY_STORIES:
            return started
        return self._persist_uncertain(started, compensation=compensation)

    def _persist_uncertain(
        self,
        current: EffectAttemptRecord,
        *,
        compensation: bool,
    ) -> EffectAttemptRecord:
        state = fold_effect_attempt(
            current.state,
            self.fold_transition("uncertain"),
            fence=current.state.fence,
        )
        evidence = BoundedEvidence.from_mapping(
            {
                "effect_attempt": EffectAttemptEventEvidence(
                    state.identity.attempt,
                    effect_attempt_state_fingerprint(state),
                ).descriptor()
            }
        )
        event = ActivityEventRecord(
            f"effect-{int(compensation)}-uncertain",
            state.identity.run_id.value,
            current.latest_transition_event.ordinal + 1,
            self.event_kind("uncertain", compensation=compensation),
            "2030-01-01T00:00:01Z",
            activity_id=state.identity.activity_id,
            failure=self.failure("uncertain"),
            evidence=evidence,
        )
        replacement = EffectAttemptRecord(
            state,
            current.original_start_event,
            event,
        )
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            self.assertEqual(stores.execution.add_event(event), event)
            self.assertEqual(
                stores.effect_attempts.compare_and_set(current, replacement),
                replacement,
            )
            unit_of_work.commit()
        return replacement

    def expected_fold_state(self, current: EffectAttemptRecord, story: str):
        return fold_effect_attempt(
            current.state,
            self.fold_transition(story),
            fence=current.state.fence,
        )

    def replace_current_claim(
        self,
        *,
        worker_id: str,
        generation: int,
    ) -> None:
        self.replace_claim(worker_id=worker_id, generation=generation)

    def attempt_only_snapshot(self):
        return tuple(
            self.connection.execute(
                "SELECT run_id, activity_id, attempt, status, "
                "outcome_fingerprint, recovery_decision_id, "
                "recovery_resolution, latest_event_id, latest_event_ordinal "
                "FROM cpk_effect_attempts ORDER BY run_id, activity_id, attempt"
            ).fetchall()
        )

    def attempt_snapshot(self) -> tuple[object, ...]:
        return (
            *super().attempt_snapshot(),
            self.connection.execute(
                "SELECT COUNT(*) FROM cpk_graph_versions"
            ).fetchone()[0],
            self.connection.execute(
                "SELECT COUNT(*) FROM cpk_realized_graph_projections"
            ).fetchone()[0],
            self.connection.execute(
                "SELECT COUNT(*) FROM cpk_observations"
            ).fetchone()[0],
        )

    def non_advancement_snapshot(self) -> tuple[object, ...]:
        return (
            tuple(
                self.connection.execute(
                    "SELECT workspace_id, lifecycle, current_graph_id, "
                    "desired_graph_id, current_realized_projection_id, "
                    "desired_realized_projection_id, desired_graph_revision "
                    "FROM cpk_workspaces ORDER BY workspace_id"
                ).fetchall()
            ),
            tuple(
                self.connection.execute(
                    "SELECT graph_id, workspace_id, version, graph_descriptor, "
                    "created_by, created_at, metadata FROM cpk_graph_versions "
                    "ORDER BY graph_id"
                ).fetchall()
            ),
            tuple(
                self.connection.execute(
                    "SELECT projection_id, workspace_id, source_authored_graph_id, "
                    "projection_kind, projection_key, projection_digest, "
                    "graph_descriptor, created_by, created_at "
                    "FROM cpk_realized_graph_projections ORDER BY projection_id"
                ).fetchall()
            ),
            tuple(
                self.connection.execute(
                    "SELECT observation_id, workspace_id, subject_id, status, "
                    "observed_at, evidence, freshness, graph_id, probe_kind, "
                    "probe_outcome, endpoint_context FROM cpk_observations "
                    "ORDER BY observation_id"
                ).fetchall()
            ),
        )

    def persisted_event_count(self) -> int:
        return self.connection.execute(
            "SELECT COUNT(*) FROM cpk_activity_events WHERE run_id='run-a'"
        ).fetchone()[0]

    def current_attempt(self) -> EffectAttemptRecord:
        with self.unit_of_work() as unit_of_work:
            return unit_of_work.stores.effect_attempts.get(
                self.identity(activity_id="start-runtime")
            )

    def changed_observation(self, marker: str):
        original = PostgresExecutionStore.observe_request_lease_for_update

        def changed(store, request_id):
            observation = original(store, request_id)
            return replace(
                observation,
                request=replace(observation.request, requested_by=marker),
            )

        return changed

    @contextmanager
    def reject_fold_database_observation(self, message: str):
        with mock.patch.object(
            PostgresExecutionStore,
            "observe_request_lease_for_update",
            side_effect=AssertionError(message),
        ):
            yield


__all__ = [
    "AUTHORITY_ERROR",
    "DIRECT_STORIES",
    "FOLD_STORIES",
    "INVALID_TRUTH_ERROR",
    "NOT_FOUND_ERROR",
    "PostgresEffectAttemptFoldFixture",
    "RECOVERY_STORIES",
    "REPLAY_ERROR",
    "SERIALIZATION_ERROR",
]
