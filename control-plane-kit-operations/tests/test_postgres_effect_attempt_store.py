from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import dataclasses
import importlib
from pathlib import Path
import threading
import unittest
import uuid

import psycopg
from psycopg.errors import ForeignKeyViolation, LockNotAvailable, UniqueViolation

from control_plane_kit_core.operations import RecoveryDecisionKind
from control_plane_kit_operations.postgres import (
    PostgresExecutionStore,
    PostgresUnitOfWork,
    SchemaInstallationError,
    install_schema,
)
from control_plane_kit_operations.postgres.current_schema_contract import (
    CURRENT_POSTGRES_SCHEMA_CONTRACT,
)
from control_plane_kit_operations.records import OperationsRecordError
from tests.effect_attempt_record_fixture import (
    EffectAttemptRecord,
    EffectAttemptRecordFixture,
    STORIES,
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


class PostgresEffectAttemptStoreTests(
    EffectAttemptRecordFixture,
    PostgresExecutionLeaseRecoveryFixture,
    unittest.TestCase,
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

    def add_record_events(self, stores, record) -> None:
        stores.execution.add_event(record.original_start_event)
        if record.latest_transition_event != record.original_start_event:
            stores.execution.add_event(record.latest_transition_event)

    def persist(self, record):
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            self.add_record_events(stores, record)
            inserted = stores.effect_attempts.insert_absent(record)
            unit_of_work.commit()
        return inserted

    def transition(self, current, story: str, *, event_id: str, ordinal: int):
        state = self.state(
            story,
            attempt=current.state.identity.attempt,
            run_id=current.state.identity.run_id.value,
            activity_id=current.state.identity.activity_id,
        )
        latest = self.event(
            state,
            self.event_kind(story, compensation=(
                current.original_start_event.kind.value.startswith("step_compensation")
            )),
            event_id=event_id,
            ordinal=ordinal,
            occurred_at="2030-01-01T00:00:01.000000Z",
        )
        return EffectAttemptRecord(state, current.original_start_event, latest)

    def test_every_state_and_phase_reconstructs_after_connection_restart(self) -> None:
        self.require_store()
        for compensation in (False, True):
            for index, story in enumerate(STORIES, start=1):
                with self.subTest(compensation=compensation, story=story):
                    self.reset_truth(
                        RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                        history="active-empty",
                    )
                    record = self.record(
                        story,
                        compensation=compensation,
                        event_prefix=f"attempt-{int(compensation)}-{index}",
                        original_ordinal=10,
                        latest_ordinal=20,
                    )
                    self.assertEqual(self.persist(record), record)
                    with self.unit_of_work() as unit_of_work:
                        self.assertEqual(
                            unit_of_work.stores.effect_attempts.get(
                                record.state.identity
                            ),
                            record,
                        )

    def test_insert_duplicate_rollback_and_fixed_read_miss_are_distinct(self) -> None:
        self.require_store()
        record = self.record(event_prefix="rollback", original_ordinal=10)
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            self.add_record_events(stores, record)
            self.assertEqual(stores.effect_attempts.insert_absent(record), record)

        with self.unit_of_work() as unit_of_work:
            with self.assertRaises(KeyError) as caught:
                unit_of_work.stores.effect_attempts.get(record.state.identity)
            self.assertEqual(str(caught.exception), "'effect attempt was not found'")

        self.assertEqual(self.persist(record), record)
        with self.unit_of_work() as unit_of_work:
            self.assertIsNone(unit_of_work.stores.effect_attempts.insert_absent(record))
            unit_of_work.commit()

    def test_predecessor_and_event_role_constraints_are_not_duplicate_identity(self) -> None:
        self.require_store()
        retry = self.record(
            "started",
            attempt=2,
            event_prefix="retry",
            original_ordinal=30,
        )
        with self.assertRaises(ForeignKeyViolation):
            with self.unit_of_work() as unit_of_work:
                stores = unit_of_work.stores
                self.add_record_events(stores, retry)
                stores.effect_attempts.insert_absent(retry)
                unit_of_work.commit()

        predecessor = self.record(event_prefix="predecessor", original_ordinal=20)
        self.persist(predecessor)
        self.assertEqual(self.persist(retry), retry)

        column_names = tuple(
            row[0]
            for row in self.connection.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema=current_schema()
                  AND table_name='cpk_effect_attempts'
                ORDER BY ordinal_position
                """
            ).fetchall()
        )
        quoted = ", ".join(column_names)
        projection = ", ".join(
            "'activity-role-canary'" if name == "activity_id" else name
            for name in column_names
        )
        with self.assertRaises(UniqueViolation):
            self.connection.execute(
                f"INSERT INTO cpk_effect_attempts ({quoted}) "
                f"SELECT {projection} FROM cpk_effect_attempts "
                "WHERE run_id='run-a' AND activity_id='activity-a' AND attempt=1"
            )

    def test_get_for_update_owns_the_exact_attempt_row(self) -> None:
        self.require_store()
        record = self.record(event_prefix="lock", original_ordinal=10)
        self.persist(record)
        with self.unit_of_work() as first:
            self.assertEqual(
                first.stores.effect_attempts.get_for_update(record.state.identity),
                record,
            )
            second = psycopg.connect(self.database_url)
            try:
                with self.assertRaises(LockNotAvailable):
                    second.execute(
                        "SELECT run_id FROM cpk_effect_attempts "
                        "WHERE run_id=%s AND activity_id=%s AND attempt=%s "
                        "FOR UPDATE NOWAIT",
                        ("run-a", "activity-a", 1),
                    )
            finally:
                second.rollback()
                second.close()

    def test_complete_prior_cas_has_one_winner_and_stale_prior_misses(self) -> None:
        self.require_store()
        current = self.record(event_prefix="cas", original_ordinal=10)
        self.persist(current)
        succeeded = self.transition(current, "succeeded", event_id="cas-success", ordinal=20)
        failed = self.transition(current, "failed", event_id="cas-failed", ordinal=21)
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.execution.add_event(succeeded.latest_transition_event)
            unit_of_work.stores.execution.add_event(failed.latest_transition_event)
            unit_of_work.commit()

        barrier = threading.Barrier(2)

        def advance(replacement):
            with self.unit_of_work() as unit_of_work:
                barrier.wait()
                result = unit_of_work.stores.effect_attempts.compare_and_set(
                    current,
                    replacement,
                )
                unit_of_work.commit()
                return result

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(pool.map(advance, (succeeded, failed)))
        winners = tuple(result for result in results if result is not None)
        self.assertEqual(len(winners), 1)
        self.assertIn(winners[0], (succeeded, failed))

        loser = failed if winners[0] == succeeded else succeeded
        with self.unit_of_work() as unit_of_work:
            self.assertIsNone(
                unit_of_work.stores.effect_attempts.compare_and_set(current, loser)
            )
            self.assertEqual(
                unit_of_work.stores.effect_attempts.get(current.state.identity),
                winners[0],
            )
            unit_of_work.commit()

    def test_decoder_translation_is_narrow_symmetric_and_candidate_free(self) -> None:
        self.require_store()
        current = self.record(event_prefix="decode", original_ordinal=10)
        settled = self.transition(current, "succeeded", event_id="decode-latest", ordinal=20)
        self.persist(current)
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.execution.add_event(settled.latest_transition_event)
            self.assertEqual(
                unit_of_work.stores.effect_attempts.compare_and_set(current, settled),
                settled,
            )
            unit_of_work.commit()

        real_state = store_module.EffectAttemptState
        real_get_event = PostgresExecutionStore.get_event

        def assert_translated(setup, error):
            setup(error)
            try:
                with self.unit_of_work() as unit_of_work:
                    with self.assertRaises(OperationsRecordError) as caught:
                        unit_of_work.stores.effect_attempts.get(settled.state.identity)
                self.assertEqual(str(caught.exception), "effect attempt row is invalid")
                self.assert_safe_error(caught.exception, "decoder-canary")
            finally:
                store_module.EffectAttemptState = real_state
                PostgresExecutionStore.get_event = real_get_event

        def scalar(error):
            def fail(*_args, **_kwargs):
                raise error
            store_module.EffectAttemptState = fail

        def event(role):
            target = (
                settled.original_start_event.event_id
                if role == "original"
                else settled.latest_transition_event.event_id
            )

            def setup(error):
                def decode(instance, event_id):
                    if event_id == target:
                        raise error
                    return real_get_event(instance, event_id)
                PostgresExecutionStore.get_event = decode
            return setup

        for boundary, setup in (
            ("scalar", scalar),
            ("original", event("original")),
            ("latest", event("latest")),
        ):
            for error_type in (ValueError, OperationsRecordError):
                with self.subTest(boundary=boundary, error=error_type.__name__):
                    assert_translated(setup, error_type("decoder-canary"))

        for boundary, setup in (
            ("scalar", scalar),
            ("original", event("original")),
            ("latest", event("latest")),
        ):
            for error_type in (TypeError, RuntimeError):
                with self.subTest(boundary=boundary, error=error_type.__name__):
                    canary = error_type("decoder-canary")
                    setup(canary)
                    try:
                        with self.unit_of_work() as unit_of_work:
                            with self.assertRaises(error_type) as caught:
                                unit_of_work.stores.effect_attempts.get(
                                    settled.state.identity
                                )
                        self.assertIs(caught.exception, canary)
                    finally:
                        store_module.EffectAttemptState = real_state
                        PostgresExecutionStore.get_event = real_get_event

    def test_current_validation_scans_beyond_two_pages_and_rejects_late_drift(self) -> None:
        self.require_store()
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            for index in range(130):
                record = self.record(
                    activity_id=f"activity-{index:03d}",
                    event_prefix=f"page-{index:03d}",
                    original_ordinal=100 + index,
                )
                self.add_record_events(stores, record)
                self.assertEqual(stores.effect_attempts.insert_absent(record), record)
            unit_of_work.commit()

        target = "page-129-start"
        self.connection.execute(
            """
            UPDATE cpk_activity_events
            SET payload = jsonb_set(
              payload,
              '{evidence,effect_attempt,state_fingerprint}',
              to_jsonb(%s::text)
            )
            WHERE event_id=%s
            """,
            ("f" * 64, target),
        )
        before = self.connection.execute(
            "SELECT count(*) FROM cpk_effect_attempts"
        ).fetchone()
        with self.assertRaises(SchemaInstallationError) as caught:
            install_schema(self.connection)
        self.assertEqual(str(caught.exception), "operations schema reset is required")
        self.assert_safe_error(caught.exception, target, "f" * 64)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_effect_attempts"
            ).fetchone(),
            before,
        )

    def test_current_schema_contract_and_atlas_own_exact_relation(self) -> None:
        relation = "cpk_effect_attempts"
        relation_names = tuple(value.name for value in CURRENT_POSTGRES_SCHEMA_CONTRACT.relations)
        self.assertIn(relation, relation_names)
        columns = tuple(
            value.name
            for value in CURRENT_POSTGRES_SCHEMA_CONTRACT.columns
            if value.relation == relation
        )
        self.assertEqual(
            columns,
            (
                "activity_id",
                "attempt",
                "fence_generation",
                "fence_worker_id",
                "latest_event_id",
                "latest_event_ordinal",
                "latest_event_run_id",
                "original_event_id",
                "original_event_ordinal",
                "original_event_run_id",
                "outcome_fingerprint",
                "prior_activity_id",
                "prior_attempt",
                "prior_run_id",
                "recovery_decision_id",
                "recovery_evidence_fingerprint",
                "recovery_resolution",
                "recovery_uncertain_fingerprint",
                "request_fingerprint",
                "run_id",
                "status",
            ),
        )
        constraints = {
            value.name: value
            for value in CURRENT_POSTGRES_SCHEMA_CONTRACT.constraints
            if value.relation == relation
        }
        required = {
            "cpk_effect_attempts_pkey",
            "cpk_effect_attempts_run_id_fkey",
            "cpk_effect_attempts_prior_fkey",
            "cpk_effect_attempts_original_event_fk",
            "cpk_effect_attempts_latest_event_fk",
            "cpk_effect_attempts_original_event_key",
            "cpk_effect_attempts_latest_event_key",
            "cpk_effect_attempts_identity_check",
            "cpk_effect_attempts_state_check",
            "cpk_effect_attempts_recovery_check",
            "cpk_effect_attempts_event_progression_check",
        }
        self.assertEqual(set(constraints), required)
        atlas = (
            Path(__file__).resolve().parents[1] / "OPERATIONS_TABLE_ATLAS.md"
        ).read_text(encoding="utf-8")
        self.assertIn("### `cpk_effect_attempts`", atlas)
        self.assertNotIn("migration", atlas.split("### `cpk_effect_attempts`", 1)[1].split("### `", 1)[0].lower())

    def test_nonempty_prior_shape_requires_reset_instead_of_table_creation(self) -> None:
        schema = f"cpk_effect_prior_{uuid.uuid4().hex}"
        connection = psycopg.connect(self.database_url, autocommit=True)
        try:
            connection.execute(f'CREATE SCHEMA "{schema}"')
            connection.execute(f'SET search_path TO "{schema}"')
            connection.execute(
                "CREATE TABLE cpk_workspaces (workspace_id text PRIMARY KEY)"
            )
            with self.assertRaises(SchemaInstallationError) as caught:
                install_schema(connection)
            self.assertEqual(
                str(caught.exception),
                "operations schema reset is required",
            )
            self.assertIsNone(caught.exception.__cause__)
            self.assertIsNone(caught.exception.__context__)
            self.assertIsNone(
                connection.execute(
                    "SELECT to_regclass('cpk_effect_attempts')"
                ).fetchone()[0]
            )
        finally:
            connection.close()
            self.connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


if __name__ == "__main__":
    unittest.main()
