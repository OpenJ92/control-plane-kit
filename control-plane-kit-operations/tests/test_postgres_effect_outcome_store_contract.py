from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
import unittest

from control_plane_kit_core.operations import EffectAttemptIdentity, EffectAttemptState
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.effect_outcome_evidence import (
    EffectAttemptOutcomeRecord,
)
from control_plane_kit_operations.postgres.stores import PostgresStoreBundle
from control_plane_kit_operations.records import OperationsRecordError
from tests.effect_outcome_evidence_fixture import (
    EffectOutcomeEvidenceFixture,
    HostileStr,
    forge_exact,
)
from tests.postgres_effect_outcome_store_fixture import (
    EffectAttemptOutcomeStore,
    MODULE_NAME,
    store_module,
)


class _NoSqlConnection:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, *args):
        self.calls.append(args)
        raise AssertionError("invalid effect outcome input reached SQL")


class _EmptyCursor:
    def fetchone(self):
        return None

    def fetchall(self):
        return ()


class _RecordingConnection:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, *args):
        self.calls.append(args)
        return _EmptyCursor()


class _FailingConnection:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    def execute(self, *_args):
        raise self.error


class _HostileIdentity(EffectAttemptIdentity):
    pass


class _HostileRecord(EffectAttemptOutcomeRecord):
    pass


class PostgresEffectOutcomeStoreContractTests(
    EffectOutcomeEvidenceFixture,
    unittest.TestCase,
):
    def require_store(self) -> None:
        self.assertIsNotNone(
            EffectAttemptOutcomeStore,
            "effect-attempt outcome store is missing",
        )

    def assert_input_error(self, operation, *canaries: str) -> None:
        with self.assertRaises(OperationsRecordError) as caught:
            operation()
        self.assertEqual(
            str(caught.exception),
            "effect attempt outcome store input is invalid",
        )
        self.assert_safe_error(caught.exception, *canaries)

    def record_for(self, story):
        outcome = self.outcome_for(story)
        observations = self.expected_observation_records(story)
        return EffectAttemptOutcomeRecord(
            "workspace-a",
            outcome,
            story.attempt,
            observations,
        )

    def test_predecessor_shape_a_values_cover_twenty_direct_post_transitions(self) -> None:
        stories = self.stories()
        self.assertEqual(len(stories), 20)
        self.assertEqual(
            {(story.name, story.compensation) for story in stories},
            {
                (name, compensation)
                for name in (
                    "execution-succeeded",
                    "execution-failed",
                    "execution-unsupported",
                    "execution-uncertain",
                    "observed-succeeded",
                    "observed-failed",
                    "observed-absent",
                    "observed-conflict",
                    "observed-indeterminate",
                    "observer-unsupported",
                )
                for compensation in (False, True)
            },
        )
        for story in stories:
            with self.subTest(story=story.name, compensation=story.compensation):
                record = self.record_for(story)
                self.assertIs(type(record), EffectAttemptOutcomeRecord)
                self.assertIsNone(record.attempt.state.recovery_decision)

    def test_store_surface_is_private_bundle_owned_and_exact(self) -> None:
        self.require_store()
        self.assertEqual(
            frozenset(
                name
                for name, value in vars(EffectAttemptOutcomeStore).items()
                if callable(value) and not name.startswith("_")
            ),
            frozenset(("insert", "get")),
        )
        self.assertEqual(
            tuple(inspect.signature(EffectAttemptOutcomeStore.insert).parameters),
            ("self", "record"),
        )
        self.assertEqual(
            tuple(inspect.signature(EffectAttemptOutcomeStore.get).parameters),
            ("self", "identity", "transition_event_id"),
        )
        self.assertIn("effect_outcomes", PostgresStoreBundle.__dataclass_fields__)
        connection = _RecordingConnection()
        bundle = PostgresStoreBundle(connection)
        self.assertIs(type(bundle.effect_outcomes), EffectAttemptOutcomeStore)
        with self.assertRaises(KeyError) as caught:
            bundle.effect_outcomes.get(self.identity(), "event-direct")
        self.assertEqual(str(caught.exception), "'effect attempt outcome was not found'")
        self.assertEqual(len(connection.calls), 1)

        import control_plane_kit_operations as operations_root
        import control_plane_kit_operations.postgres as postgres_root

        self.assertFalse(hasattr(operations_root, "EffectAttemptOutcomeStore"))
        self.assertFalse(hasattr(postgres_root, "EffectAttemptOutcomeStore"))

    def test_invalid_inputs_fail_before_sql_with_fixed_redaction(self) -> None:
        self.require_store()
        story = self.stories()[0]
        record = self.record_for(story)
        identity = record.attempt.state.identity
        forged_record = forge_exact(
            EffectAttemptOutcomeRecord,
            workspace_id=HostileStr(record.workspace_id),
            outcome=record.outcome,
            attempt=record.attempt,
            endpoint_observations=record.endpoint_observations,
        )
        forged_identity = forge_exact(
            EffectAttemptIdentity,
            run_id=identity.run_id,
            activity_id=HostileStr(identity.activity_id),
            attempt=identity.attempt,
        )
        missing_identity = forge_exact(
            EffectAttemptIdentity,
            run_id=identity.run_id,
            attempt=identity.attempt,
        )
        state = record.attempt.state
        forged_state = forge_exact(
            EffectAttemptState,
            identity=forged_identity,
            request_fingerprint=state.request_fingerprint,
            fence=state.fence,
            status=state.status,
            outcome_fingerprint=state.outcome_fingerprint,
            prior_attempt=state.prior_attempt,
            recovery_decision=state.recovery_decision,
        )
        forged_attempt = forge_exact(
            EffectAttemptRecord,
            state=forged_state,
            original_start_event=record.attempt.original_start_event,
            latest_transition_event=record.attempt.latest_transition_event,
        )
        forged_nested = forge_exact(
            EffectAttemptOutcomeRecord,
            workspace_id=record.workspace_id,
            outcome=record.outcome,
            attempt=forged_attempt,
            endpoint_observations=record.endpoint_observations,
        )
        missing_record = forge_exact(
            EffectAttemptOutcomeRecord,
            outcome=record.outcome,
            attempt=record.attempt,
            endpoint_observations=record.endpoint_observations,
        )
        cases = (
            ("insert-object", lambda store: store.insert(object())),
            (
                "insert-hostile",
                lambda store: store.insert(
                    _HostileRecord(
                        record.workspace_id,
                        record.outcome,
                        record.attempt,
                        record.endpoint_observations,
                    )
                ),
            ),
            ("insert-exact-forged-record", lambda store: store.insert(forged_record)),
            ("insert-missing-record-field", lambda store: store.insert(missing_record)),
            ("insert-exact-forged-identity", lambda store: store.insert(forged_nested)),
            ("get-object", lambda store: store.get(object(), "event-direct")),
            (
                "get-exact-forged-identity",
                lambda store: store.get(forged_identity, "event-direct"),
            ),
            (
                "get-missing-identity-field",
                lambda store: store.get(missing_identity, "event-direct"),
            ),
            (
                "get-hostile-identity",
                lambda store: store.get(
                    _HostileIdentity(
                        identity.run_id,
                        identity.activity_id,
                        identity.attempt,
                    ),
                    "event-direct",
                ),
            ),
            ("get-empty-event", lambda store: store.get(identity, "")),
            ("get-long-event", lambda store: store.get(identity, "x" * 513)),
            ("get-control-event", lambda store: store.get(identity, "event\x00x")),
        )
        for label, operation in cases:
            with self.subTest(label=label):
                connection = _NoSqlConnection()
                self.assert_input_error(
                    lambda operation=operation: operation(
                        EffectAttemptOutcomeStore(connection)
                    ),
                    "event-direct",
                    "x" * 513,
                )
                self.assertEqual(connection.calls, [])

    def test_exact_lookup_is_identity_targeted_and_hard_bounded(self) -> None:
        self.require_store()
        connection = _RecordingConnection()
        with self.assertRaises(KeyError):
            EffectAttemptOutcomeStore(connection).get(
                self.identity(),
                "event-direct",
            )
        self.assertEqual(len(connection.calls), 1)
        query, parameters = connection.calls[0]
        normalized = " ".join(str(query).split())
        for predicate in (
            "run_id = %s",
            "activity_id = %s",
            "attempt = %s",
            "direct_event_id = %s",
        ):
            self.assertIn(predicate, normalized)
        self.assertNotIn("FOR UPDATE", normalized)
        self.assertEqual(
            parameters,
            ("run-a", "activity-a", 1, "event-direct"),
        )

    def test_read_queries_bound_large_values_before_python_transport(self) -> None:
        self.require_store()
        connection = _RecordingConnection()
        with self.assertRaises(KeyError):
            EffectAttemptOutcomeStore(connection).get(
                self.identity(),
                "event-direct",
            )
        get_query = " ".join(str(connection.calls[0][0]).split())

        connection = _RecordingConnection()
        store_module._validate_current_rows(connection)
        page_query = " ".join(str(connection.calls[0][0]).split())
        membership_query = " ".join(store_module._MEMBERSHIP_QUERY.split())
        for label, query, selected in (
            ("get-preimage", get_query, "preimage"),
            ("current-preimage", page_query, "preimage"),
            ("membership-evidence", membership_query, "observation.evidence"),
        ):
            with self.subTest(label=label):
                self.assertIn("octet_length", query)
                self.assertIn(selected, query)
                self.assertIn("8192", query)

    def test_miss_is_redacted_and_unexpected_driver_faults_remain_raw(self) -> None:
        self.require_store()
        identity = self.identity()
        connection = _RecordingConnection()
        with self.assertRaises(KeyError) as caught:
            EffectAttemptOutcomeStore(connection).get(identity, "event-canary")
        self.assertEqual(str(caught.exception), "'effect attempt outcome was not found'")
        self.assert_safe_error(
            caught.exception,
            "run-a",
            "activity-a",
            "event-canary",
        )

        for error in (
            TypeError("driver-type-canary"),
            RuntimeError("driver-runtime-canary"),
        ):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(type(error)) as raw:
                    EffectAttemptOutcomeStore(_FailingConnection(error)).get(
                        identity,
                        "event-direct",
                    )
                self.assertIs(raw.exception, error)

    def test_package_inventory_owns_exact_private_store_module(self) -> None:
        inventory_path = os.environ.get("CPK_PACKAGE_MODULE_INVENTORY")
        self.assertIsNotNone(inventory_path)
        inventory = json.loads(Path(inventory_path).read_text(encoding="utf-8"))
        rows = tuple(row for row in inventory["modules"] if row["module"] == MODULE_NAME)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["owner"], "operation")
        self.assertEqual(row["destination"], MODULE_NAME)
        self.assertEqual(
            row["source"],
            "control-plane-kit-operations/src/"
            "control_plane_kit_operations/postgres/effect_outcome_store.py",
        )
        self.assertEqual(
            tuple(row["protecting_tests"]),
            (
                "tests/test_postgres_effect_outcome_store_contract.py",
                "tests/test_postgres_effect_outcome_store.py",
                "tests/test_postgres_effect_outcome_schema.py",
            ),
        )
        self.assertEqual(
            tuple(row["internal_dependencies"]),
            (
                "control_plane_kit_core.operations",
                "control_plane_kit_core.probe_intents",
                "control_plane_kit_core.runtime_effect_observation",
                "control_plane_kit_core.runtime_effects",
                "control_plane_kit_core.types",
                "control_plane_kit_operations.effect_attempts",
                "control_plane_kit_operations.effect_outcome_evidence",
                "control_plane_kit_operations.postgres.execution",
                "control_plane_kit_operations.postgres.observed_state",
                "control_plane_kit_operations.postgres.schema",
                "control_plane_kit_operations.records",
            ),
        )
        self.assertNotIn("dependencies", row)
        self.assertEqual(tuple(row["optional_external_dependencies"]), ("rfc8785",))


if __name__ == "__main__":
    unittest.main()
