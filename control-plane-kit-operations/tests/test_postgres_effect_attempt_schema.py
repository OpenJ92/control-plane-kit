from __future__ import annotations

import dataclasses
from pathlib import Path
import unittest
import uuid

import psycopg
from psycopg.errors import CheckViolation

from control_plane_kit_core.operations import FailureCategory
from control_plane_kit_operations.postgres import SchemaInstallationError, install_schema
from control_plane_kit_operations.postgres import execution as execution_module
from control_plane_kit_operations.postgres.current_schema_contract import (
    CURRENT_POSTGRES_SCHEMA_CONTRACT,
)
from control_plane_kit_operations.postgres.execution import PostgresExecutionStore
from control_plane_kit_operations.records import (
    BoundedEvidence,
    FailureEvidence,
    OperationsRecordError,
)
from tests.postgres_effect_attempt_store_fixture import (
    PostgresEffectAttemptStoreFixture,
)


RELATION = "cpk_effect_attempts"

EXPECTED_CHECKS = {
    "cpk_effect_attempts_identity_check": (
        "((attempt > 0) AND ((run_id COLLATE \"C\") ~ "
        "'^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'::text) AND "
        "((activity_id COLLATE \"C\") ~ "
        "'^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'::text))"
    ),
    "cpk_effect_attempts_fence_check": (
        "((fence_generation > 0) AND (char_length(fence_worker_id) >= 1) "
        "AND (char_length(fence_worker_id) <= 256))"
    ),
    "cpk_effect_attempts_fingerprint_check": (
        "((request_fingerprint ~ '^[0-9a-f]{64}$'::text) AND "
        "((outcome_fingerprint IS NULL) OR (outcome_fingerprint ~ "
        "'^[0-9a-f]{64}$'::text)) AND "
        "((recovery_uncertain_fingerprint IS NULL) OR "
        "(recovery_uncertain_fingerprint ~ '^[0-9a-f]{64}$'::text)) AND "
        "((recovery_evidence_fingerprint IS NULL) OR "
        "(recovery_evidence_fingerprint ~ '^[0-9a-f]{64}$'::text)))"
    ),
    "cpk_effect_attempts_prior_check": (
        "(((prior_run_id IS NULL) AND (prior_activity_id IS NULL) AND "
        "(prior_attempt IS NULL) AND (attempt = 1)) OR "
        "((prior_run_id IS NOT NULL) AND (prior_activity_id IS NOT NULL) AND "
        "(prior_attempt IS NOT NULL) AND (prior_run_id = run_id) AND "
        "(prior_activity_id = activity_id) AND "
        "(prior_attempt = (attempt - 1)) AND (attempt > 1)))"
    ),
    "cpk_effect_attempts_state_check": (
        "((status = ANY (ARRAY['started'::text, 'succeeded'::text, "
        "'failed'::text, 'unsupported'::text, 'uncertain'::text, "
        "'abandoned'::text])) AND (((status = 'started'::text) AND "
        "(outcome_fingerprint IS NULL)) OR ((status <> 'started'::text) "
        "AND (outcome_fingerprint IS NOT NULL))))"
    ),
    "cpk_effect_attempts_recovery_check": (
        "(((recovery_decision_id IS NULL) AND (recovery_resolution IS NULL) "
        "AND (recovery_uncertain_fingerprint IS NULL) AND "
        "(recovery_evidence_fingerprint IS NULL) AND "
        "(status <> 'abandoned'::text)) OR "
        "((recovery_decision_id IS NOT NULL) AND "
        "(recovery_resolution IS NOT NULL) AND "
        "(char_length(recovery_decision_id) >= 1) AND "
        "(char_length(recovery_decision_id) <= 256) AND "
        "(((recovery_resolution = 'succeeded'::text) AND "
        "(status = 'succeeded'::text)) OR ((recovery_resolution = "
        "'failed'::text) AND (status = 'failed'::text)) OR "
        "((recovery_resolution = 'abandoned'::text) AND "
        "(status = 'abandoned'::text))) AND "
        "(recovery_uncertain_fingerprint IS NOT NULL) AND "
        "(recovery_evidence_fingerprint IS NOT NULL) AND "
        "(outcome_fingerprint = recovery_evidence_fingerprint)))"
    ),
    "cpk_effect_attempts_event_progression_check": (
        "((original_event_run_id = run_id) AND (latest_event_run_id = run_id) "
        "AND (original_event_ordinal > 0) AND (latest_event_ordinal > 0) AND "
        "(char_length(original_event_id) >= 1) AND "
        "(char_length(original_event_id) <= 512) AND "
        "(char_length(latest_event_id) >= 1) AND "
        "(char_length(latest_event_id) <= 512) AND "
        "(((status = 'started'::text) AND "
        "((latest_event_id = original_event_id) AND "
        "(latest_event_run_id = original_event_run_id) AND "
        "(latest_event_ordinal = original_event_ordinal))) OR "
        "((status <> 'started'::text) AND "
        "(latest_event_ordinal > original_event_ordinal))))"
    ),
}


class _TracingConnection:
    def __init__(self, connection) -> None:
        self.connection = connection
        self.calls = []

    def execute(self, query, parameters=None):
        self.calls.append((query, parameters))
        if parameters is None:
            return self.connection.execute(query)
        return self.connection.execute(query, parameters)

    def __getattr__(self, name):
        return getattr(self.connection, name)


class PostgresEffectAttemptSchemaTests(
    PostgresEffectAttemptStoreFixture,
    unittest.TestCase,
):
    def test_current_validation_is_bounded_pk_seek_and_rejects_late_drift(self) -> None:
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
        identity = self.identity(activity_id="activity-129")
        with self.unit_of_work() as unit_of_work:
            with self.assertRaises(OperationsRecordError) as direct:
                unit_of_work.stores.effect_attempts.get(identity)
        self.assertEqual(str(direct.exception), "effect attempt row is invalid")
        self.assert_safe_error(direct.exception, target, "f" * 64)

        before_attempts = self.connection.execute(
            "SELECT count(*) FROM cpk_effect_attempts"
        ).fetchone()
        before_events = self.connection.execute(
            "SELECT count(*) FROM cpk_activity_events"
        ).fetchone()
        before_payload = self.connection.execute(
            "SELECT payload FROM cpk_activity_events WHERE event_id=%s",
            (target,),
        ).fetchone()
        traced = _TracingConnection(self.connection)
        with self.assertRaises(SchemaInstallationError) as caught:
            install_schema(traced)
        self.assertEqual(str(caught.exception), "operations schema reset is required")
        self.assert_safe_error(caught.exception, target, "f" * 64)

        scans = tuple(
            (" ".join(str(query).split()), parameters)
            for query, parameters in traced.calls
            if "FROM cpk_effect_attempts" in str(query)
        )
        self.assertEqual(len(scans), 3)
        for query, parameters in scans:
            self.assertRegex(
                query,
                r"ORDER BY (?:[a-z_]+\.)?run_id, "
                r"(?:[a-z_]+\.)?activity_id, (?:[a-z_]+\.)?attempt",
            )
            self.assertIn("LIMIT %s", query)
            self.assertNotIn("FOR UPDATE", query)
            self.assertEqual(parameters[-1], 64)
        self.assertNotRegex(
            scans[0][0],
            r"WHERE \((?:[a-z_]+\.)?run_id, (?:[a-z_]+\.)?activity_id, "
            r"(?:[a-z_]+\.)?attempt\) >",
        )
        for query, parameters in scans[1:]:
            self.assertRegex(
                query,
                r"WHERE \((?:[a-z_]+\.)?run_id, (?:[a-z_]+\.)?activity_id, "
                r"(?:[a-z_]+\.)?attempt\) > \(%s, %s, %s\)",
            )
            self.assertEqual(len(parameters), 4)

        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_effect_attempts"
            ).fetchone(),
            before_attempts,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM cpk_activity_events"
            ).fetchone(),
            before_events,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT payload FROM cpk_activity_events WHERE event_id=%s",
                (target,),
            ).fetchone(),
            before_payload,
        )

    def test_missing_failure_keys_are_categorical_current_row_drift(self) -> None:
        self.require_store()
        record_without_failure = self.record(
            "failed",
            event_prefix="missing-failure",
            original_ordinal=10,
            latest_ordinal=20,
        )
        record = dataclasses.replace(
            record_without_failure,
            latest_transition_event=dataclasses.replace(
                record_without_failure.latest_transition_event,
                failure=FailureEvidence(
                    FailureCategory.TERMINAL,
                    "failure-code",
                    "failure-message",
                    BoundedEvidence(),
                ),
            ),
        )
        self.persist(record)
        target = record.latest_transition_event.event_id

        internal = KeyError("internal-failure-canary")
        real_failure_evidence = execution_module.FailureEvidence

        def fail_if_called(**_kwargs):
            raise internal

        execution_module.FailureEvidence = fail_if_called
        try:
            with self.assertRaises(KeyError) as caught:
                PostgresExecutionStore(self.connection).get_event(target)
            self.assertIs(caught.exception, internal)
        finally:
            execution_module.FailureEvidence = real_failure_evidence

        self.connection.execute(
            """
            UPDATE cpk_activity_events
            SET payload = jsonb_set(
              payload,
              '{failure}',
              '{}'::jsonb
            )
            WHERE event_id=%s
            """,
            (target,),
        )

        with self.assertRaises(ValueError) as decoded:
            PostgresExecutionStore(self.connection).get_event(target)
        self.assertEqual(
            str(decoded.exception),
            "persisted activity failure is malformed",
        )
        self.assert_safe_error(
            decoded.exception,
            target,
            "category",
            "code",
            "message",
        )

        for method in ("get", "get_for_update"):
            with self.subTest(method=method):
                with self.unit_of_work() as unit_of_work:
                    with self.assertRaises(OperationsRecordError) as caught:
                        getattr(unit_of_work.stores.effect_attempts, method)(
                            record.state.identity
                        )
                self.assertEqual(
                    str(caught.exception),
                    "effect attempt row is invalid",
                )
                self.assert_safe_error(caught.exception, target, "category")

        before_payload = self.connection.execute(
            "SELECT payload FROM cpk_activity_events WHERE event_id=%s",
            (target,),
        ).fetchone()
        with self.assertRaises(SchemaInstallationError) as caught:
            install_schema(self.connection)
        self.assertEqual(str(caught.exception), "operations schema reset is required")
        self.assert_safe_error(caught.exception, target, "category")
        self.assertEqual(
            self.connection.execute(
                "SELECT payload FROM cpk_activity_events WHERE event_id=%s",
                (target,),
            ).fetchone(),
            before_payload,
        )

    def test_current_schema_contract_freezes_columns_constraints_and_indexes(self) -> None:
        relation_names = tuple(
            value.name for value in CURRENT_POSTGRES_SCHEMA_CONTRACT.relations
        )
        self.assertIn(RELATION, relation_names)
        columns = {
            value.name: value
            for value in CURRENT_POSTGRES_SCHEMA_CONTRACT.columns
            if value.relation == RELATION
        }
        expected_columns = {
            "activity_id": ("text", True),
            "attempt": ("integer", True),
            "fence_generation": ("bigint", True),
            "fence_worker_id": ("text", True),
            "latest_event_id": ("text", True),
            "latest_event_ordinal": ("integer", True),
            "latest_event_run_id": ("text", True),
            "original_event_id": ("text", True),
            "original_event_ordinal": ("integer", True),
            "original_event_run_id": ("text", True),
            "outcome_fingerprint": ("text", False),
            "prior_activity_id": ("text", False),
            "prior_attempt": ("integer", False),
            "prior_run_id": ("text", False),
            "recovery_decision_id": ("text", False),
            "recovery_evidence_fingerprint": ("text", False),
            "recovery_resolution": ("text", False),
            "recovery_uncertain_fingerprint": ("text", False),
            "request_fingerprint": ("text", True),
            "run_id": ("text", True),
            "status": ("text", True),
        }
        physical_columns = (
            "run_id",
            "activity_id",
            "attempt",
            "request_fingerprint",
            "fence_worker_id",
            "fence_generation",
            "status",
            "outcome_fingerprint",
            "prior_run_id",
            "prior_activity_id",
            "prior_attempt",
            "recovery_decision_id",
            "recovery_resolution",
            "recovery_uncertain_fingerprint",
            "recovery_evidence_fingerprint",
            "original_event_id",
            "original_event_run_id",
            "original_event_ordinal",
            "latest_event_id",
            "latest_event_run_id",
            "latest_event_ordinal",
        )
        self.assertEqual(
            {
                name: (column.formatted_type, column.not_null)
                for name, column in columns.items()
            },
            expected_columns,
        )
        self.assertEqual(tuple(columns), physical_columns)
        self.assertTrue(
            all(
                column.identity == ""
                and column.generated == ""
                and column.default_expression is None
                for column in columns.values()
            )
        )

        constraints = {
            value.name: value
            for value in CURRENT_POSTGRES_SCHEMA_CONTRACT.constraints
            if value.relation == RELATION
        }
        expected_keys = {
            "cpk_effect_attempts_pkey": (
                "p",
                ("run_id", "activity_id", "attempt"),
                None,
                None,
            ),
            "cpk_effect_attempts_run_id_fkey": (
                "f",
                ("run_id",),
                "cpk_activity_runs",
                ("run_id",),
            ),
            "cpk_effect_attempts_prior_fkey": (
                "f",
                ("prior_run_id", "prior_activity_id", "prior_attempt"),
                RELATION,
                ("run_id", "activity_id", "attempt"),
            ),
            "cpk_effect_attempts_original_event_fk": (
                "f",
                ("original_event_id", "original_event_run_id", "original_event_ordinal"),
                "cpk_activity_events",
                ("event_id", "run_id", "ordinal"),
            ),
            "cpk_effect_attempts_latest_event_fk": (
                "f",
                ("latest_event_id", "latest_event_run_id", "latest_event_ordinal"),
                "cpk_activity_events",
                ("event_id", "run_id", "ordinal"),
            ),
            "cpk_effect_attempts_original_event_key": (
                "u",
                ("original_event_id", "original_event_run_id", "original_event_ordinal"),
                None,
                None,
            ),
            "cpk_effect_attempts_latest_event_key": (
                "u",
                ("latest_event_id", "latest_event_run_id", "latest_event_ordinal"),
                None,
                None,
            ),
        }
        self.assertEqual(set(constraints), set(expected_keys) | set(EXPECTED_CHECKS))
        for name, (kind, local, referenced, remote) in expected_keys.items():
            contract = constraints[name]
            self.assertEqual(
                (
                    contract.kind,
                    contract.local_columns,
                    contract.referenced_relation,
                    contract.referenced_columns,
                ),
                (kind, local, referenced, remote),
            )
            if kind == "f":
                self.assertEqual(
                    (contract.update_action, contract.delete_action, contract.match_type),
                    ("a", "a", "s"),
                )
        for name, expression in EXPECTED_CHECKS.items():
            self.assertEqual(constraints[name].check_expression, expression)

        event_unique = tuple(
            value
            for value in CURRENT_POSTGRES_SCHEMA_CONTRACT.constraints
            if value.name == "cpk_activity_events_event_id_run_id_ordinal_key"
        )
        self.assertEqual(len(event_unique), 1)
        self.assertEqual(event_unique[0].kind, "u")
        self.assertEqual(
            event_unique[0].local_columns,
            ("event_id", "run_id", "ordinal"),
        )

        indexes = {
            value.name: value
            for value in CURRENT_POSTGRES_SCHEMA_CONTRACT.indexes
            if value.relation == RELATION
        }
        self.assertEqual(
            set(indexes),
            {
                "cpk_effect_attempts_pkey",
                "cpk_effect_attempts_original_event_key",
                "cpk_effect_attempts_latest_event_key",
            },
        )
        self.assertTrue(all(value.unique for value in indexes.values()))
        self.assertEqual(
            {
                name: (
                    value.owning_constraint,
                    value.primary,
                    value.key_entries,
                    value.include_entries,
                )
                for name, value in indexes.items()
            },
            {
                "cpk_effect_attempts_pkey": (
                    "cpk_effect_attempts_pkey",
                    True,
                    ("run_id", "activity_id", "attempt"),
                    (),
                ),
                "cpk_effect_attempts_original_event_key": (
                    "cpk_effect_attempts_original_event_key",
                    False,
                    ("original_event_id", "original_event_run_id", "original_event_ordinal"),
                    (),
                ),
                "cpk_effect_attempts_latest_event_key": (
                    "cpk_effect_attempts_latest_event_key",
                    False,
                    ("latest_event_id", "latest_event_run_id", "latest_event_ordinal"),
                    (),
                ),
            },
        )
        event_index = tuple(
            value
            for value in CURRENT_POSTGRES_SCHEMA_CONTRACT.indexes
            if value.name == "cpk_activity_events_event_id_run_id_ordinal_key"
        )
        self.assertEqual(len(event_index), 1)
        self.assertEqual(event_index[0].owning_constraint, event_unique[0].name)
        self.assertEqual(
            event_index[0].key_entries,
            ("event_id", "run_id", "ordinal"),
        )

        atlas = (
            Path(__file__).resolve().parents[1] / "OPERATIONS_TABLE_ATLAS.md"
        ).read_text(encoding="utf-8")
        section = atlas.split("### `cpk_effect_attempts`", 1)[1].split("### `", 1)[0]
        self.assertNotIn("migration", section.lower())

    def test_named_check_violation_remains_raw_postgres_integrity(self) -> None:
        self.require_store()
        record = self.record(event_prefix="check", original_ordinal=10)
        self.persist(record)
        with self.assertRaises(CheckViolation) as caught:
            self.connection.execute(
                "UPDATE cpk_effect_attempts SET fence_generation=0 "
                "WHERE run_id='run-a' AND activity_id='activity-a' AND attempt=1"
            )
        self.assertEqual(
            caught.exception.diag.constraint_name,
            "cpk_effect_attempts_fence_check",
        )

    def test_partial_prior_coordinate_is_rejected(self) -> None:
        self.require_store()
        self.persist(self.record(event_prefix="prior-one", original_ordinal=10))
        self.persist(
            self.record(
                attempt=2,
                event_prefix="prior-two",
                original_ordinal=11,
            )
        )

        with self.assertRaises(CheckViolation) as caught:
            self.connection.execute(
                "UPDATE cpk_effect_attempts SET prior_activity_id=NULL "
                "WHERE run_id='run-a' AND activity_id='activity-a' AND attempt=2"
            )

        self.assertEqual(
            caught.exception.diag.constraint_name,
            "cpk_effect_attempts_prior_check",
        )

    def test_partial_recovery_sum_is_rejected(self) -> None:
        self.require_store()
        self.persist(
            self.record(
                "recovered-failed",
                event_prefix="partial-recovery",
                original_ordinal=10,
                latest_ordinal=11,
            )
        )

        with self.assertRaises(CheckViolation) as caught:
            self.connection.execute(
                "UPDATE cpk_effect_attempts SET recovery_resolution=NULL "
                "WHERE run_id='run-a' AND activity_id='activity-a' AND attempt=1"
            )

        self.assertEqual(
            caught.exception.diag.constraint_name,
            "cpk_effect_attempts_recovery_check",
        )

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
            self.assertEqual(str(caught.exception), "operations schema reset is required")
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
