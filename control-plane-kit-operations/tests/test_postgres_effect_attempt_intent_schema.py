from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tomllib
import unittest

import psycopg
from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation

from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.runtime_effect_observation import (
    runtime_effect_intent_fingerprint,
)
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.postgres import SchemaInstallationError, install_schema
from control_plane_kit_operations.postgres.current_data_validation import (
    CurrentRowDrift,
    validate_current_rows,
)
from control_plane_kit_operations.postgres.current_schema_contract import (
    CURRENT_POSTGRES_SCHEMA_CONTRACT,
)
from tests.postgres_effect_attempt_intent_store_fixture import (
    PostgresEffectAttemptIntentStoreFixture,
    RELATION,
)


INTENT_COLUMNS = (
    "run_id",
    "activity_id",
    "attempt",
    "workspace_id",
    "request_id",
    "request_fingerprint",
    "original_event_id",
    "original_event_run_id",
    "original_event_ordinal",
    "preimage",
)

EXPECTED_KEYS = {
    "cpk_effect_attempt_intents_pkey": (
        RELATION,
        ("run_id", "activity_id", "attempt"),
    ),
    "cpk_effect_attempt_intents_original_event_key": (
        RELATION,
        ("original_event_id", "original_event_run_id", "original_event_ordinal"),
    ),
    "cpk_effect_attempt_intents_commitment_key": (
        RELATION,
        (
            "run_id",
            "activity_id",
            "attempt",
            "request_fingerprint",
            "original_event_id",
        ),
    ),
}

EXPECTED_FOREIGN_KEYS = {
    "cpk_effect_attempt_intents_run_request_fk": (
        RELATION,
        ("run_id", "request_id"),
        "cpk_activity_runs",
        ("run_id", "request_id"),
    ),
    "cpk_effect_attempt_intents_request_workspace_fk": (
        RELATION,
        ("request_id", "workspace_id"),
        "cpk_execution_requests",
        ("request_id", "workspace_id"),
    ),
    "cpk_effect_attempt_intents_original_event_fk": (
        RELATION,
        ("original_event_id", "original_event_run_id", "original_event_ordinal"),
        "cpk_activity_events",
        ("event_id", "run_id", "ordinal"),
    ),
    "cpk_effect_attempts_intent_evidence_fk": (
        "cpk_effect_attempts",
        (
            "run_id",
            "activity_id",
            "attempt",
            "request_fingerprint",
            "original_event_id",
        ),
        RELATION,
        (
            "run_id",
            "activity_id",
            "attempt",
            "request_fingerprint",
            "original_event_id",
        ),
    ),
}


class PostgresEffectAttemptIntentSchemaTests(
    PostgresEffectAttemptIntentStoreFixture,
    unittest.TestCase,
):
    def test_exact_current_contract_adds_one_relation_and_generated_totals(self) -> None:
        self.require_intent_schema()
        contract = CURRENT_POSTGRES_SCHEMA_CONTRACT
        relations = tuple(value.name for value in contract.relations)
        self.assertEqual(len(relations), 36)
        self.assertEqual(relations.count(RELATION), 1)
        self.assertEqual(len(contract.columns), 489)
        self.assertEqual(len(contract.constraints), 367)
        self.assertEqual(len(contract.indexes), 120)
        self.assertEqual(
            sum(value.kind == "f" for value in contract.constraints),
            80,
        )
        columns = tuple(
            value.name for value in contract.columns if value.relation == RELATION
        )
        self.assertEqual(columns, INTENT_COLUMNS)

    def test_exact_keys_ownership_and_attempt_inbound_fk_are_restrictive(self) -> None:
        self.require_intent_schema()
        constraints = {
            value.name: value for value in CURRENT_POSTGRES_SCHEMA_CONTRACT.constraints
        }
        for name, (relation, columns) in EXPECTED_KEYS.items():
            with self.subTest(name=name):
                value = constraints[name]
                self.assertIn(value.kind, ("p", "u"))
                self.assertEqual(value.relation, relation)
                self.assertEqual(value.local_columns, columns)
        for name, expected in EXPECTED_FOREIGN_KEYS.items():
            with self.subTest(name=name):
                value = constraints[name]
                self.assertEqual(value.kind, "f")
                self.assertEqual(
                    (
                        value.relation,
                        value.local_columns,
                        value.referenced_relation,
                        value.referenced_columns,
                    ),
                    expected,
                )
                self.assertEqual(value.update_action, "a")
                self.assertEqual(value.delete_action, "a")
                self.assertFalse(value.deferrable)
                self.assertFalse(value.deferred)

    def test_checks_bind_identity_event_run_fingerprint_and_preimage(self) -> None:
        self.require_intent_schema()
        checks = {
            value.name: value.check_expression
            for value in CURRENT_POSTGRES_SCHEMA_CONTRACT.constraints
            if value.kind == "c"
        }
        expected = {
            "cpk_effect_attempt_intents_identity_check": (
                "attempt > 0",
                "run_id COLLATE \"C\"",
                "activity_id COLLATE \"C\"",
            ),
            "cpk_effect_attempt_intents_ownership_check": (
                "original_event_run_id = run_id",
            ),
            "cpk_effect_attempt_intents_fingerprint_check": (
                "request_fingerprint",
                "^[0-9a-f]{64}$",
            ),
            "cpk_effect_attempt_intents_preimage_check": (
                "octet_length(preimage) >= 1",
                "octet_length(preimage) <= 1048576",
            ),
        }
        self.assertEqual(set(expected) - set(checks), set())
        for name, fragments in expected.items():
            for fragment in fragments:
                with self.subTest(name=name, fragment=fragment):
                    self.assertIn(fragment, checks[name])

    def test_real_postgres_accepts_lawful_chain_and_rejects_identity_drift(self) -> None:
        self.require_intent_store()
        self.require_intent_schema()
        attempt, evidence = self.intent_attempt()
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
            unit_of_work.commit()
        self.assertEqual(len(self.intent_snapshot()), 1)

        mutations = (
            ("workspace_id", "workspace-foreign", "cpk_effect_attempt_intents_request_workspace_fk"),
            ("request_id", "request-foreign", "cpk_effect_attempt_intents_run_request_fk"),
            ("original_event_id", "event-foreign", "cpk_effect_attempt_intents_original_event_fk"),
        )
        for column, value, constraint in mutations:
            with self.subTest(constraint=constraint):
                with self.assertRaises(ForeignKeyViolation) as caught:
                    self.connection.execute(
                        f"UPDATE {RELATION} SET {column}=%s",
                        (value,),
                    )
                self.assertEqual(caught.exception.diag.constraint_name, constraint)

        with self.assertRaises(CheckViolation) as caught:
            self.connection.execute(
                f"UPDATE {RELATION} SET original_event_run_id='run-foreign'"
            )
        self.assertEqual(
            caught.exception.diag.constraint_name,
            "cpk_effect_attempt_intents_ownership_check",
        )

    def test_reduced_commitment_key_accepts_simultaneous_widths(self) -> None:
        self.require_intent_store()
        self.require_intent_schema()
        request_id = "request-" + "q" * 504
        run_id = "r" * 200
        activity_id = "a" * 200
        event_id = "\U0010ffff" * 512
        plan_id = "intent-max-plan"
        self.connection.execute(
            """
            INSERT INTO cpk_activity_plans
              (plan_id, session_id, base_graph_id, desired_graph_id,
               base_realized_projection_id, desired_realized_projection_id,
               desired_graph_revision, status, created_at, payload)
            SELECT %s, session_id, base_graph_id, desired_graph_id,
                   base_realized_projection_id, desired_realized_projection_id,
                   desired_graph_revision, status, created_at, payload
            FROM cpk_activity_plans WHERE plan_id='plan-a'
            """,
            (plan_id,),
        )
        workspace_id, base_graph_id, desired_graph_id = self.connection.execute(
            "SELECT request.workspace_id, plan.base_graph_id, plan.desired_graph_id "
            "FROM cpk_execution_requests AS request "
            "JOIN cpk_activity_plans AS plan ON plan.plan_id=%s "
            "WHERE request.request_id='request-a'",
            (plan_id,),
        ).fetchone()
        intent = self.intent(
            request_id=request_id,
            run_id=run_id,
            activity_id=activity_id,
        )
        intent = replace(
            intent,
            source=replace(
                intent.source,
                workspace_id=workspace_id,
                request_id=request_id,
                run_id=RunId(run_id),
                plan_id=plan_id,
                base_graph_id=base_graph_id,
                desired_graph_id=desired_graph_id,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO cpk_execution_requests
              (request_id, workspace_id, session_id, plan_id, status,
               requested_by, requested_at, approval_request_id,
               approval_decision_id, idempotency_key, intent_fingerprint,
               claim_worker_id, claim_generation, claimed_at, lease_expires_at)
            SELECT %s, workspace_id, session_id, %s, status,
                   requested_by, requested_at, approval_request_id,
                   approval_decision_id, %s, %s,
                   claim_worker_id, claim_generation, claimed_at, lease_expires_at
            FROM cpk_execution_requests WHERE request_id='request-a'
            """,
            (
                request_id,
                plan_id,
                "intent-max-idempotency",
                runtime_effect_intent_fingerprint(intent),
            ),
        )
        self.connection.execute(
            """
            INSERT INTO cpk_activity_runs
              (run_id, plan_id, request_id, attempt, prior_run_id, status,
               created_at, started_at, settled_at, metadata)
            SELECT %s, %s, %s, 1, NULL, status,
                   created_at, started_at, settled_at, metadata
            FROM cpk_activity_runs WHERE run_id='run-a'
            """,
            (run_id, plan_id, request_id),
        )
        attempt, evidence = self.intent_attempt(
            request_id=request_id,
            run_id=run_id,
            activity_id=activity_id,
            event_id=event_id,
            intent=intent,
        )
        self.persist_evidence_chain(attempt, evidence)
        self.assertEqual(
            self.connection.execute(
                f"SELECT char_length(run_id), char_length(activity_id), "
                f"char_length(original_event_id), octet_length(original_event_id) "
                f"FROM {RELATION} WHERE run_id=%s",
                (run_id,),
            ).fetchone(),
            (200, 200, 512, 2048),
        )

    def test_attempt_without_evidence_is_impossible_and_orphan_is_detected(self) -> None:
        self.require_intent_store()
        self.require_intent_schema()
        attempt, evidence = self.intent_attempt()
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.execution.add_event(attempt.original_start_event)
            with self.assertRaises(ForeignKeyViolation) as caught:
                stores.effect_attempts.insert_absent(attempt)
        self.assertEqual(
            caught.exception.diag.constraint_name,
            "cpk_effect_attempts_intent_evidence_fk",
        )

        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            stores.execution.add_event(attempt.original_start_event)
            stores.effect_attempt_intents.insert(evidence)
            unit_of_work.commit()
        with self.assertRaises(CurrentRowDrift):
            validate_current_rows(self.connection)

    def test_unique_identity_and_event_coordinates_are_independent(self) -> None:
        self.require_intent_store()
        self.require_intent_schema()
        attempt, evidence = self.intent_attempt()
        self.persist_evidence_chain(attempt, evidence)

        distinct_event = replace(
            attempt.original_start_event,
            event_id="intent-distinct-event",
            ordinal=attempt.original_start_event.ordinal + 1,
        )
        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.execution.add_event(distinct_event),
                distinct_event,
            )
            unit_of_work.commit()
        with self.assertRaises(UniqueViolation) as caught:
            self.connection.execute(
                f"INSERT INTO {RELATION} "
                "SELECT run_id, activity_id, attempt, workspace_id, request_id, "
                "request_fingerprint, %s, original_event_run_id, %s, preimage "
                f"FROM {RELATION}",
                (distinct_event.event_id, distinct_event.ordinal),
            )
        self.assertEqual(
            caught.exception.diag.constraint_name,
            "cpk_effect_attempt_intents_pkey",
        )

        with self.assertRaises(UniqueViolation) as caught:
            self.connection.execute(
                f"INSERT INTO {RELATION} "
                "SELECT run_id, activity_id, attempt + 1, workspace_id, request_id, "
                "request_fingerprint, original_event_id, original_event_run_id, "
                f"original_event_ordinal, preimage FROM {RELATION}"
            )
        self.assertEqual(
            caught.exception.diag.constraint_name,
            "cpk_effect_attempt_intents_original_event_key",
        )

    def test_schema_is_fresh_exact_and_reset_required_for_drift(self) -> None:
        self.require_intent_schema()
        connection = psycopg.connect(self.database_url)
        try:
            install_schema(connection)
            before = connection.execute(
                f"SELECT count(*) FROM {RELATION}"
            ).fetchone()
            connection.execute(
                f"ALTER TABLE {RELATION} DROP CONSTRAINT "
                "cpk_effect_attempt_intents_ownership_check"
            )
            with self.assertRaises(SchemaInstallationError) as caught:
                install_schema(connection)
            self.assertEqual(
                str(caught.exception),
                "operations schema reset is required",
            )
            self.assertEqual(
                connection.execute(f"SELECT count(*) FROM {RELATION}").fetchone(),
                before,
            )
        finally:
            connection.rollback()
            connection.close()

    def test_atlas_restore_order_current_validation_and_read_accounting_are_exact(self) -> None:
        package_root = Path(__file__).resolve().parents[1]
        atlas = (package_root / "OPERATIONS_TABLE_ATLAS.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("### `cpk_effect_attempt_intents`", atlas)
        self.assertIn(
            "sha256=335d5d8b07122c80652abbf46bd629cb72dd12b540365c016692caf391f69ecb",
            atlas,
        )
        self.assertIn("foreign-keys=81", atlas)
        for name in (*EXPECTED_KEYS, *EXPECTED_FOREIGN_KEYS):
            with self.subTest(name=name):
                self.assertIn(name, atlas)
        self.assertIn(
            "cpk_activity_events,cpk_effect_attempt_intents,cpk_effect_attempts",
            atlas,
        )
        self.assertIn(
            "cpk_effect_attempts,cpk_effect_attempt_intents,cpk_activity_events",
            atlas,
        )

        validation_source = (
            package_root
            / "src/control_plane_kit_operations/postgres/current_data_validation.py"
        ).read_text(encoding="utf-8")
        self.assertEqual(validation_source.count("effect_attempt_intent_store"), 1)
        self.assertEqual(validation_source.count("_validate_effect_attempt_intent_rows"), 2)

        reads = tomllib.loads(
            (package_root / "POSTGRES_READ_CARDINALITY.toml").read_text(
                encoding="utf-8"
            )
        )["read"]
        rows = tuple(
            value
            for value in reads
            if value["module"]
            == "control_plane_kit_operations.postgres.effect_attempt_intent_store"
        )
        self.assertEqual(
            tuple((value["selector"], value.get("occurrence")) for value in rows),
            (("_validate_current_rows", None),),
        )
        self.assertIn("8-row", rows[0]["sql"])
        self.assertIn("keyset", rows[0]["sql"])


if __name__ == "__main__":
    unittest.main()
