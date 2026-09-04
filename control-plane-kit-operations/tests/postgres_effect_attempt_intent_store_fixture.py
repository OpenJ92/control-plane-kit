from __future__ import annotations

from dataclasses import replace
import importlib

from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.runtime_effect_observation import (
    runtime_effect_intent_fingerprint,
)
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.effect_attempt_intent_evidence import (
    EffectAttemptIntentRecord,
)
from tests.effect_attempt_intent_fixture import EffectAttemptIntentFixture
from tests.postgres_effect_attempt_start_fixture import (
    PostgresEffectAttemptStartFixture,
)


MODULE_NAME = "control_plane_kit_operations.postgres.effect_attempt_intent_store"
RELATION = "cpk_effect_attempt_intents"


def _load_module(import_module=importlib.import_module):
    try:
        return import_module(MODULE_NAME)
    except ModuleNotFoundError as error:
        if error.name != MODULE_NAME:
            raise
        return None


store_module = _load_module()
EffectAttemptIntentStore = getattr(store_module, "EffectAttemptIntentStore", None)
_validate_current_rows = getattr(store_module, "_validate_current_rows", None)


class PostgresEffectAttemptIntentStoreFixture(
    PostgresEffectAttemptStartFixture,
):
    def require_intent_store(self) -> None:
        self.assertIsNotNone(
            EffectAttemptIntentStore,
            "effect-attempt intent store is missing",
        )

    def largest_lawful_intent(self):
        return EffectAttemptIntentFixture.largest_lawful_intent(self)

    def require_intent_schema(self) -> None:
        relations = {
            value.name
            for value in self.current_contract().relations
        }
        self.assertIn(RELATION, relations, "effect-attempt intent relation is missing")

    @staticmethod
    def current_contract():
        from control_plane_kit_operations.postgres.current_schema_contract import (
            CURRENT_POSTGRES_SCHEMA_CONTRACT,
        )

        return CURRENT_POSTGRES_SCHEMA_CONTRACT

    def intent_attempt(
        self,
        *,
        activity_id: str = "start-runtime",
        event_id: str = "effect-intent-start",
        ordinal: int = 3,
        compensation: bool = False,
        request_id: str = "request-a",
        run_id: str = "run-a",
        intent=None,
    ) -> tuple[EffectAttemptRecord, EffectAttemptIntentRecord]:
        value = intent or self.intent(
            compensation=compensation,
            request_id=request_id,
            run_id=run_id,
            activity_id=activity_id,
        )
        attempt = self.record(
            "started",
            compensation=compensation,
            run_id=run_id,
            activity_id=activity_id,
            event_prefix="intent-candidate",
            original_ordinal=ordinal,
            original_time="2030-01-01T00:00:00Z",
        )
        state = replace(
            attempt.state,
            request_fingerprint=runtime_effect_intent_fingerprint(value),
        )
        event = replace(
            attempt.original_start_event,
            event_id=event_id,
            ordinal=ordinal,
            evidence=self.evidence_for(state),
        )
        attempt = EffectAttemptRecord(state, event, event)
        evidence = EffectAttemptIntentRecord(state.identity, event, value)
        return attempt, evidence

    def indexed_intent_attempt(
        self,
        index: int,
    ) -> tuple[EffectAttemptRecord, EffectAttemptIntentRecord]:
        return self.intent_attempt(
            activity_id=f"intent-activity-{index:03d}",
            event_id=f"intent-event-{index:03d}",
            ordinal=3 + index,
        )

    def persist_evidence_chain(
        self,
        attempt: EffectAttemptRecord,
        evidence: EffectAttemptIntentRecord,
    ) -> None:
        self.require_intent_store()
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            self.assertEqual(
                stores.execution.add_event(attempt.original_start_event),
                attempt.original_start_event,
            )
            self.assertEqual(
                stores.effect_attempt_intents.insert(evidence),
                evidence,
            )
            self.assertEqual(
                stores.effect_attempts.insert_absent(attempt),
                attempt,
            )
            unit_of_work.commit()

    def intent_snapshot(self) -> tuple[tuple[object, ...], ...]:
        self.require_intent_schema()
        return tuple(
            self.connection.execute(
                "SELECT run_id, activity_id, attempt, workspace_id, request_id, "
                "request_fingerprint, original_event_id, original_event_run_id, "
                "original_event_ordinal, preimage FROM cpk_effect_attempt_intents "
                "ORDER BY run_id, activity_id, attempt"
            ).fetchall()
        )


__all__ = [
    "EffectAttemptIdentity",
    "EffectAttemptIntentStore",
    "MODULE_NAME",
    "PostgresEffectAttemptIntentStoreFixture",
    "RELATION",
    "RunId",
    "_validate_current_rows",
    "store_module",
]
