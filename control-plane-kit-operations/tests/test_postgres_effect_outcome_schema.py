from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tomllib
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


class PostgresEffectOutcomeSchemaTests(
    PostgresEffectOutcomeStoreFixture,
    unittest.TestCase,
):
    def test_exact_current_contract_adds_two_relations_and_closed_columns(self) -> None:
        relations = tuple(value.name for value in CURRENT_POSTGRES_SCHEMA_CONTRACT.relations)
        self.assertEqual(len(relations), 33)
        self.assertIn(OUTCOME, relations)
        self.assertIn(MEMBERSHIP, relations)
        columns = {}
        for value in CURRENT_POSTGRES_SCHEMA_CONTRACT.columns:
            columns.setdefault(value.relation, []).append(value.name)
        self.assertEqual(tuple(columns[OUTCOME]), OUTCOME_COLUMNS)
        self.assertEqual(tuple(columns[MEMBERSHIP]), MEMBERSHIP_COLUMNS)
        self.assertEqual(len(CURRENT_POSTGRES_SCHEMA_CONTRACT.columns), 441)
        self.assertEqual(len(CURRENT_POSTGRES_SCHEMA_CONTRACT.constraints), 325)
        self.assertEqual(len(CURRENT_POSTGRES_SCHEMA_CONTRACT.indexes), 109)

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
                ("observation_id",),
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

        self.reset_truth(
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            history="active-empty",
        )
        self.persist_outcome(record)
        with self.assertRaises(ForeignKeyViolation) as caught:
            self.connection.execute(
                f"UPDATE {MEMBERSHIP} SET observation_count=3"
            )
        self.assertEqual(
            caught.exception.diag.constraint_name,
            "cpk_effect_attempt_outcome_observations_outcome_fk",
        )

    def test_one_member_position_must_be_zero(self) -> None:
        self.require_store()
        record = self.record_for(self.story_named("execution-succeeded"))
        self.persist_outcome(record)

        with self.assertRaises(CheckViolation) as caught:
            with self.connection.transaction():
                self.connection.execute(
                    f"DELETE FROM {MEMBERSHIP} WHERE position=1"
                )
                self.connection.execute(
                    f"UPDATE {OUTCOME} SET observation_count=1"
                )
                self.connection.execute(
                    f"UPDATE {MEMBERSHIP} SET observation_count=1 "
                    "WHERE position=0"
                )
                self.connection.execute(
                    f"UPDATE {MEMBERSHIP} SET position=1 WHERE position=0"
                )
        self.assertEqual(
            caught.exception.diag.constraint_name,
            "cpk_effect_attempt_outcome_observations_position_check",
        )

    def test_current_verifier_rejects_late_direct_outcome_drift_without_repair(self) -> None:
        self.require_store()

        def outcome_scans(trace):
            return tuple(
                (" ".join(str(query).split()), parameters)
                for query, parameters in trace.calls
                if "FROM cpk_effect_attempt_outcomes" in str(query)
                and "ORDER BY" in str(query)
            )

        def assert_three_pk_seek_pages(scans):
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

        records = tuple(
            self.indexed_record(
                index,
                story_name=(
                    "execution-succeeded" if index == 64 else "observed-absent"
                ),
            )
            for index in range(129)
        )
        with self.unit_of_work() as unit_of_work:
            stores = unit_of_work.stores
            for record in records:
                stores.execution.add_event(record.attempt.original_start_event)
                stores.execution.add_event(record.attempt.latest_transition_event)
                self.add_record_intent(stores, record.attempt)
                self.assertEqual(
                    stores.effect_attempts.insert_absent(record.attempt),
                    record.attempt,
                )
                for observation in record.endpoint_observations:
                    self.assertEqual(stores.observed_state.put(observation), observation)
                self.assertEqual(stores.effect_outcomes.insert(record), record)
            unit_of_work.commit()

        valid_trace = _TracingConnection(self.connection)
        install_schema(valid_trace)
        assert_three_pk_seek_pages(outcome_scans(valid_trace))
        membership_scans = tuple(
            (" ".join(str(query).split()), parameters)
            for query, parameters in valid_trace.calls
            if "FROM cpk_effect_attempt_outcome_observations" in str(query)
        )
        self.assertEqual(len(membership_scans), 129)
        limits = []
        for query, parameters in membership_scans:
            self.assertIn("LIMIT %s", query)
            self.assertNotIn("FOR UPDATE", query)
            limits.append(parameters[-1])
        self.assertEqual(limits.count(1), 128)
        self.assertEqual(limits.count(3), 1)

        record = records[-1]
        before = self.connection.execute(
            f"SELECT preimage FROM {OUTCOME} WHERE activity_id=%s",
            (record.attempt.state.identity.activity_id,),
        ).fetchone()
        self.connection.execute(
            f"UPDATE {OUTCOME} SET preimage=%s WHERE activity_id=%s",
            (
                b'{"candidate":"late-drift"}',
                record.attempt.state.identity.activity_id,
            ),
        )
        drifted = self.connection.execute(
            f"SELECT preimage FROM {OUTCOME} WHERE activity_id=%s",
            (record.attempt.state.identity.activity_id,),
        ).fetchone()
        self.assertNotEqual(before, drifted)
        traced = _TracingConnection(self.connection)
        with self.assertRaises(SchemaInstallationError) as caught:
            install_schema(traced)
        self.assertEqual(str(caught.exception), "operations schema reset is required")
        self.assert_safe_error(caught.exception, "late-drift")
        assert_three_pk_seek_pages(outcome_scans(traced))
        self.assertEqual(
            self.connection.execute(
                f"SELECT preimage FROM {OUTCOME} WHERE activity_id=%s",
                (record.attempt.state.identity.activity_id,),
            ).fetchone(),
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
        outcome_reads = tuple(
            row
            for row in tomllib.loads(read_inventory)["read"]
            if row["module"]
            == "control_plane_kit_operations.postgres.effect_outcome_store"
        )
        self.assertEqual(
            tuple(
                (row["selector"], row.get("occurrence"))
                for row in outcome_reads
            ),
            (
                ("EffectAttemptOutcomeStore.get", None),
                ("_validate_current_rows", 1),
                ("_validate_current_rows", 2),
            ),
        )
        self.assertTrue(
            all("LIMIT observation_count plus one" in row["sql"] for row in outcome_reads)
        )
        self.assertIn(
            "identity keyset batches bounded by fixed batch size",
            outcome_reads[1]["sql"],
        )


if __name__ == "__main__":
    unittest.main()
