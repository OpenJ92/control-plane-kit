from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from psycopg.errors import CheckViolation, ForeignKeyViolation

from control_plane_kit_core.operations import RecoveryDecisionKind
from control_plane_kit_operations.postgres import SchemaInstallationError, install_schema
from control_plane_kit_operations.postgres.current_schema_contract import (
    CURRENT_POSTGRES_SCHEMA_CONTRACT,
)
from control_plane_kit_operations.records import OperationsRecordError
from tests.postgres_effect_outcome_store_fixture import (
    PostgresEffectOutcomeStoreFixture,
)


OUTCOME = "cpk_effect_attempt_outcomes"
MEMBERSHIP = "cpk_effect_attempt_outcome_observations"

OUTCOME_COLUMNS = (
    "run_id",
    "activity_id",
    "attempt",
    "workspace_id",
    "request_id",
    "profile",
    "preimage",
    "request_fingerprint",
    "fence_worker_id",
    "fence_generation",
    "status",
    "outcome_fingerprint",
    "prior_run_id",
    "prior_activity_id",
    "prior_attempt",
    "original_event_id",
    "original_event_run_id",
    "original_event_ordinal",
    "direct_event_id",
    "direct_event_run_id",
    "direct_event_ordinal",
    "observation_count",
)
MEMBERSHIP_COLUMNS = (
    "run_id",
    "activity_id",
    "attempt",
    "workspace_id",
    "observation_count",
    "position",
    "observation_id",
)

EXPECTED_FOREIGN_KEYS = {
    "cpk_effect_attempt_outcomes_attempt_fk": (
        OUTCOME,
        ("run_id", "activity_id", "attempt"),
        "cpk_effect_attempts",
        ("run_id", "activity_id", "attempt"),
    ),
    "cpk_effect_attempt_outcomes_run_request_fk": (
        OUTCOME,
        ("run_id", "request_id"),
        "cpk_activity_runs",
        ("run_id", "request_id"),
    ),
    "cpk_effect_attempt_outcomes_request_workspace_fk": (
        OUTCOME,
        ("request_id", "workspace_id"),
        "cpk_execution_requests",
        ("request_id", "workspace_id"),
    ),
    "cpk_effect_attempt_outcomes_original_event_fk": (
        OUTCOME,
        ("original_event_id", "original_event_run_id", "original_event_ordinal"),
        "cpk_activity_events",
        ("event_id", "run_id", "ordinal"),
    ),
    "cpk_effect_attempt_outcomes_direct_event_fk": (
        OUTCOME,
        ("direct_event_id", "direct_event_run_id", "direct_event_ordinal"),
        "cpk_activity_events",
        ("event_id", "run_id", "ordinal"),
    ),
    "cpk_effect_attempt_outcome_observations_outcome_fk": (
        MEMBERSHIP,
        ("run_id", "activity_id", "attempt", "workspace_id", "observation_count"),
        OUTCOME,
        ("run_id", "activity_id", "attempt", "workspace_id", "observation_count"),
    ),
    "cpk_effect_attempt_outcome_observations_observation_fk": (
        MEMBERSHIP,
        ("observation_id", "workspace_id"),
        "cpk_observations",
        ("observation_id", "workspace_id"),
    ),
}


class PostgresEffectOutcomeSchemaTests(
    PostgresEffectOutcomeStoreFixture,
    unittest.TestCase,
):
    def test_exact_current_contract_adds_two_relations_and_closed_columns(self) -> None:
        relations = tuple(value.name for value in CURRENT_POSTGRES_SCHEMA_CONTRACT.relations)
        self.assertEqual(len(relations), 32)
        self.assertIn(OUTCOME, relations)
        self.assertIn(MEMBERSHIP, relations)
        columns = {}
        for value in CURRENT_POSTGRES_SCHEMA_CONTRACT.columns:
            columns.setdefault(value.relation, []).append(value.name)
        self.assertEqual(tuple(columns[OUTCOME]), OUTCOME_COLUMNS)
        self.assertEqual(tuple(columns[MEMBERSHIP]), MEMBERSHIP_COLUMNS)
        self.assertEqual(len(CURRENT_POSTGRES_SCHEMA_CONTRACT.columns), 431)
        self.assertEqual(len(CURRENT_POSTGRES_SCHEMA_CONTRACT.constraints), 314)
        self.assertEqual(len(CURRENT_POSTGRES_SCHEMA_CONTRACT.indexes), 106)

    def test_exact_candidate_keys_and_restrictive_composite_foreign_keys(self) -> None:
        constraints = {
            value.name: value
            for value in CURRENT_POSTGRES_SCHEMA_CONTRACT.constraints
        }
        expected_keys = {
            "cpk_activity_runs_run_id_request_id_key": (
                "cpk_activity_runs",
                ("run_id", "request_id"),
            ),
            "cpk_execution_requests_request_id_workspace_id_key": (
                "cpk_execution_requests",
                ("request_id", "workspace_id"),
            ),
            "cpk_observations_observation_id_workspace_id_key": (
                "cpk_observations",
                ("observation_id", "workspace_id"),
            ),
            "cpk_effect_attempt_outcomes_pkey": (
                OUTCOME,
                ("run_id", "activity_id", "attempt"),
            ),
            "cpk_effect_attempt_outcomes_direct_event_key": (
                OUTCOME,
                ("direct_event_id", "direct_event_run_id", "direct_event_ordinal"),
            ),
            "cpk_effect_attempt_outcomes_membership_key": (
                OUTCOME,
                ("run_id", "activity_id", "attempt", "workspace_id", "observation_count"),
            ),
            "cpk_effect_attempt_outcome_observations_pkey": (
                MEMBERSHIP,
                ("run_id", "activity_id", "attempt", "position"),
            ),
            "cpk_effect_attempt_outcome_observations_observation_key": (
                MEMBERSHIP,
                ("run_id", "activity_id", "attempt", "observation_id"),
            ),
        }
        self.assertEqual(set(expected_keys) - set(constraints), set())
        self.assertEqual(set(EXPECTED_FOREIGN_KEYS) - set(constraints), set())
        for name, (relation, columns) in expected_keys.items():
            with self.subTest(name=name):
                value = constraints[name]
                self.assertIn(value.kind, ("p", "u"))
                self.assertEqual(value.relation, relation)
                self.assertEqual(value.local_columns, columns)

        for name, expected in EXPECTED_FOREIGN_KEYS.items():
            with self.subTest(name=name):
                value = constraints[name]
                self.assertEqual(
                    (
                        value.relation,
                        value.local_columns,
                        value.referenced_relation,
                        value.referenced_columns,
                    ),
                    expected,
                )
                self.assertEqual(value.kind, "f")
                self.assertEqual(value.update_action, "a")
                self.assertEqual(value.delete_action, "a")

        checks = {
            value.name: value.check_expression
            for value in constraints.values()
            if value.kind == "c"
        }
        expected_fragments = {
            "cpk_effect_attempt_outcomes_identity_check": (
                "attempt > 0",
                "run_id COLLATE \"C\"",
                "activity_id COLLATE \"C\"",
            ),
            "cpk_effect_attempt_outcomes_fence_check": (
                "fence_generation > 0",
                "char_length(fence_worker_id) <= 256",
            ),
            "cpk_effect_attempt_outcomes_profile_check": (
                "execution-result",
                "provider-observation",
            ),
            "cpk_effect_attempt_outcomes_preimage_check": (
                "octet_length(preimage) >= 1",
                "octet_length(preimage) <= 8192",
            ),
            "cpk_effect_attempt_outcomes_fingerprint_check": (
                "request_fingerprint",
                "outcome_fingerprint",
                "^[0-9a-f]{64}$",
            ),
            "cpk_effect_attempt_outcomes_prior_check": (
                "attempt = 1",
                "prior_attempt = (attempt - 1)",
            ),
            "cpk_effect_attempt_outcomes_state_check": (
                "succeeded",
                "failed",
                "unsupported",
                "uncertain",
            ),
            "cpk_effect_attempt_outcomes_event_progression_check": (
                "original_event_run_id = run_id",
                "direct_event_run_id = run_id",
                "direct_event_ordinal > original_event_ordinal",
            ),
            "cpk_effect_attempt_outcomes_observation_count_check": (
                "observation_count >= 0",
                "observation_count <= 8192",
            ),
            "cpk_effect_attempt_outcome_observations_position_check": (
                "position >= 0",
                "position < observation_count",
            ),
        }
        self.assertEqual(set(expected_fragments) - set(checks), set())
        for name, fragments in expected_fragments.items():
            for fragment in fragments:
                with self.subTest(check=name, fragment=fragment):
                    self.assertIn(fragment, checks[name])

    def test_raw_schema_checks_preimage_count_position_and_direct_state(self) -> None:
        self.require_store()
        record = self.record_for(self.story_named("execution-succeeded"))
        self.persist_outcome(record)
        mutations = (
            (
                OUTCOME,
                "preimage='\\x'::bytea",
                "cpk_effect_attempt_outcomes_preimage_check",
            ),
            (
                OUTCOME,
                "preimage=decode(repeat('00', 8193), 'hex')",
                "cpk_effect_attempt_outcomes_preimage_check",
            ),
            (
                OUTCOME,
                "observation_count=8193",
                "cpk_effect_attempt_outcomes_observation_count_check",
            ),
            (
                OUTCOME,
                "status='started'",
                "cpk_effect_attempt_outcomes_state_check",
            ),
            (
                MEMBERSHIP,
                "position=observation_count",
                "cpk_effect_attempt_outcome_observations_position_check",
            ),
        )
        for relation, assignment, constraint in mutations:
            with self.subTest(constraint=constraint):
                with self.assertRaises(CheckViolation) as caught:
                    self.connection.execute(f"UPDATE {relation} SET {assignment}")
                self.assertEqual(caught.exception.diag.constraint_name, constraint)

    def test_raw_composite_ownership_drift_names_exact_foreign_key(self) -> None:
        self.require_store()
        record = self.record_for(self.story_named("execution-succeeded"))
        cases = (
            ("request_id", "request-foreign", "cpk_effect_attempt_outcomes_run_request_fk"),
            ("workspace_id", "workspace-foreign", "cpk_effect_attempt_outcomes_request_workspace_fk"),
            ("original_event_id", "event-foreign", "cpk_effect_attempt_outcomes_original_event_fk"),
            ("direct_event_id", "event-foreign", "cpk_effect_attempt_outcomes_direct_event_fk"),
        )
        for column, value, constraint in cases:
            with self.subTest(constraint=constraint):
                self.reset_truth(
                    RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                    history="active-empty",
                )
                self.persist_outcome(record)
                with self.assertRaises(ForeignKeyViolation) as caught:
                    self.connection.execute(
                        f"UPDATE {OUTCOME} SET {column}=%s",
                        (value,),
                    )
                self.assertEqual(caught.exception.diag.constraint_name, constraint)

    def test_current_verifier_rejects_late_direct_outcome_drift_without_repair(self) -> None:
        self.require_store()
        record = self.record_for(self.story_named("observed-absent"))
        self.persist_outcome(record)
        before = self.connection.execute(
            f"SELECT preimage FROM {OUTCOME}"
        ).fetchone()
        self.connection.execute(
            f"UPDATE {OUTCOME} SET preimage=%s",
            (b'{"candidate":"late-drift"}',),
        )
        drifted = self.connection.execute(
            f"SELECT preimage FROM {OUTCOME}"
        ).fetchone()
        self.assertNotEqual(before, drifted)
        with self.assertRaises(SchemaInstallationError) as caught:
            install_schema(self.connection)
        self.assertEqual(str(caught.exception), "operations schema reset is required")
        self.assert_safe_error(caught.exception, "late-drift")
        self.assertEqual(
            self.connection.execute(f"SELECT preimage FROM {OUTCOME}").fetchone(),
            drifted,
        )

    def test_atlas_and_read_accounting_publish_exact_current_ownership(self) -> None:
        package_root = Path(__file__).resolve().parents[1]
        atlas = (package_root / "OPERATIONS_TABLE_ATLAS.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("### `cpk_effect_attempt_outcomes`", atlas)
        self.assertIn("### `cpk_effect_attempt_outcome_observations`", atlas)
        for name in EXPECTED_FOREIGN_KEYS:
            self.assertIn(name, atlas)
        self.assertIn(
            "cpk_effect_attempt_outcomes,cpk_effect_attempt_outcome_observations",
            atlas,
        )
        self.assertIn("direct post-transition", atlas)
        self.assertNotIn("direct terminal outcome", atlas)

        read_inventory = (package_root / "POSTGRES_READ_CARDINALITY.toml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(read_inventory.count("effect_outcome_store"), 2)
        self.assertIn('selector = "EffectAttemptOutcomeStore.get"', read_inventory)
        self.assertIn('selector = "_validate_current_rows"', read_inventory)
        self.assertIn("LIMIT observation_count plus one", read_inventory)
        self.assertIn("identity keyset batches bounded by fixed batch size", read_inventory)


if __name__ == "__main__":
    unittest.main()
