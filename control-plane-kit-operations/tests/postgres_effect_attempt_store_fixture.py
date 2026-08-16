from __future__ import annotations

import importlib

from control_plane_kit_core.operations import RecoveryDecisionKind
from tests.effect_attempt_record_fixture import (
    EffectAttemptRecord,
    EffectAttemptRecordFixture,
)
from tests.execution_lease_recovery_fixture import (
    PostgresExecutionLeaseRecoveryFixture,
)


MODULE_NAME = "control_plane_kit_operations.postgres.effect_attempt_store"


def _load_module(import_module=importlib.import_module):
    try:
        return import_module(MODULE_NAME)
    except ModuleNotFoundError as error:
        if error.name != MODULE_NAME:
            raise
        return None


store_module = _load_module()
EffectAttemptStore = getattr(store_module, "EffectAttemptStore", None)


class PostgresEffectAttemptStoreFixture(
    EffectAttemptRecordFixture,
    PostgresExecutionLeaseRecoveryFixture,
):
    def setUp(self) -> None:
        PostgresExecutionLeaseRecoveryFixture.setUp(self)
        self.reset_truth(
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            history="active-empty",
        )

    def tearDown(self) -> None:
        PostgresExecutionLeaseRecoveryFixture.tearDown(self)

    def require_store(self) -> None:
        self.assertIsNotNone(EffectAttemptStore, "effect-attempt store is missing")

    def add_record_events(self, stores, record: EffectAttemptRecord) -> None:
        stores.execution.add_event(record.original_start_event)
        if record.latest_transition_event != record.original_start_event:
            stores.execution.add_event(record.latest_transition_event)

    def persist(self, record: EffectAttemptRecord):
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            self.add_record_events(stores, record)
            inserted = stores.effect_attempts.insert_absent(record)
            unit_of_work.commit()
        return inserted

    def transition(
        self,
        current: EffectAttemptRecord,
        story: str,
        *,
        event_id: str,
        ordinal: int,
    ) -> EffectAttemptRecord:
        state = self.state(
            story,
            attempt=current.state.identity.attempt,
            run_id=current.state.identity.run_id.value,
            activity_id=current.state.identity.activity_id,
        )
        latest = self.event(
            state,
            self.event_kind(
                story,
                compensation=current.original_start_event.kind.value.startswith(
                    "step_compensation"
                ),
            ),
            event_id=event_id,
            ordinal=ordinal,
            occurred_at="2030-01-01T00:00:01.000000Z",
        )
        return EffectAttemptRecord(state, current.original_start_event, latest)


__all__ = [
    "EffectAttemptStore",
    "PostgresEffectAttemptStoreFixture",
    "store_module",
]
