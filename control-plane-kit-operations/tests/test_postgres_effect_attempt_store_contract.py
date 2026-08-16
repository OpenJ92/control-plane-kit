from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
import json
import os
from pathlib import Path
import unittest

from psycopg.errors import UniqueViolation

from control_plane_kit_core.operations import (
    EffectAttemptFence,
    EffectAttemptIdentity,
)
from control_plane_kit_operations.postgres.stores import PostgresStoreBundle
from control_plane_kit_operations.records import OperationsRecordError
from tests.effect_attempt_record_fixture import (
    EffectAttemptRecord,
    EffectAttemptRecordFixture,
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


class _NoSqlConnection:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, *args):
        self.calls.append(args)
        raise AssertionError("invalid effect-attempt input reached SQL")


class _EmptyCursor:
    def fetchone(self):
        return None


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


class _HostileRecord(EffectAttemptRecord):
    pass


class PostgresEffectAttemptStoreContractTests(
    EffectAttemptRecordFixture,
    unittest.TestCase,
):
    def require_store(self) -> None:
        self.assertIsNotNone(EffectAttemptStore, "effect-attempt store is missing")

    def assert_store_error(self, operation, *canaries: str) -> None:
        with self.assertRaises(OperationsRecordError) as caught:
            operation()
        self.assertEqual(
            str(caught.exception),
            "effect attempt store input is invalid",
        )
        self.assert_safe_error(caught.exception, *canaries)

    def test_store_surface_is_private_bundle_owned_and_exact(self) -> None:
        self.require_store()
        self.assertEqual(
            frozenset(
                name
                for name, value in vars(EffectAttemptStore).items()
                if callable(value) and not name.startswith("_")
            ),
            frozenset(("get", "get_for_update", "insert_absent", "compare_and_set")),
        )
        signatures = {
            name: tuple(inspect.signature(getattr(EffectAttemptStore, name)).parameters)
            for name in ("get", "get_for_update", "insert_absent", "compare_and_set")
        }
        self.assertEqual(
            signatures,
            {
                "get": ("self", "identity"),
                "get_for_update": ("self", "identity"),
                "insert_absent": ("self", "record"),
                "compare_and_set": ("self", "current", "replacement"),
            },
        )
        self.assertIn("effect_attempts", PostgresStoreBundle.__dataclass_fields__)
        connection = _RecordingConnection()
        bundle = PostgresStoreBundle(connection)
        self.assertIs(type(bundle.effect_attempts), EffectAttemptStore)
        with self.assertRaises(KeyError):
            bundle.effect_attempts.get(self.identity())
        self.assertEqual(len(connection.calls), 1)
        import control_plane_kit_operations as operations_root
        import control_plane_kit_operations.postgres as postgres_root

        self.assertFalse(hasattr(operations_root, "EffectAttemptStore"))
        self.assertFalse(hasattr(postgres_root, "EffectAttemptStore"))

        inventory_path = os.environ.get("CPK_PACKAGE_MODULE_INVENTORY")
        self.assertIsNotNone(inventory_path)
        inventory = json.loads(Path(inventory_path).read_text(encoding="utf-8"))
        rows = tuple(
            row for row in inventory["modules"] if row["module"] == MODULE_NAME
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["owner"], "operation")
        self.assertEqual(rows[0]["destination"], MODULE_NAME)
        self.assertEqual(
            rows[0]["source"],
            "control-plane-kit-operations/src/"
            "control_plane_kit_operations/postgres/effect_attempt_store.py",
        )
        self.assertEqual(
            tuple(rows[0]["protecting_tests"]),
            (
                "tests/test_postgres_effect_attempt_store_contract.py",
                "tests/test_postgres_effect_attempt_store.py",
                "tests/test_postgres_effect_attempt_schema.py",
            ),
        )

    def test_get_inputs_are_exact_and_rejected_before_sql(self) -> None:
        self.require_store()
        identity = self.identity()
        candidates = (
            object(),
            _HostileIdentity(**identity.__dict__),
        )
        for candidate in candidates:
            for method in ("get", "get_for_update"):
                with self.subTest(method=method, candidate=type(candidate).__name__):
                    connection = _NoSqlConnection()
                    store = EffectAttemptStore(connection)
                    self.assert_store_error(
                        lambda: getattr(store, method)(candidate),
                        "canary",
                    )
                    self.assertEqual(connection.calls, [])

    def test_mutation_inputs_and_immutable_coordinates_fail_before_sql(self) -> None:
        self.require_store()
        current = self.record()
        changed_identity = self.record("succeeded", attempt=2)
        changed_fence_state = dataclasses.replace(
            current.state,
            fence=EffectAttemptFence("worker-b", 8),
        )
        changed_fence = type(current)(
            changed_fence_state,
            self.event(
                self.started_state(changed_fence_state),
                current.original_start_event.kind,
                event_id="fence-start",
                ordinal=3,
                occurred_at=current.original_start_event.occurred_at,
            ),
            self.event(
                self.started_state(changed_fence_state),
                current.latest_transition_event.kind,
                event_id="fence-start",
                ordinal=3,
                occurred_at=current.latest_transition_event.occurred_at,
            ),
        )
        changed_original = dataclasses.replace(
            current,
            original_start_event=dataclasses.replace(
                current.original_start_event,
                event_id="other-start",
            ),
            latest_transition_event=dataclasses.replace(
                current.latest_transition_event,
                event_id="other-start",
            ),
        )
        hostile_record = _HostileRecord(**current.__dict__)

        uncertain = self.record(
            "uncertain",
            event_prefix="regression",
            original_ordinal=3,
            latest_ordinal=20,
        )
        recovered_state = self.state("recovered-succeeded")
        regressed_latest = self.event(
            recovered_state,
            self.event_kind("recovered-succeeded", compensation=False),
            event_id="regression-recovered",
            ordinal=15,
            occurred_at="2030-01-01T00:00:00.000000Z",
        )
        ordinal_regression = EffectAttemptRecord(
            recovered_state,
            uncertain.original_start_event,
            regressed_latest,
        )
        cases = (
            ("insert", lambda store: store.insert_absent(object())),
            ("insert-hostile", lambda store: store.insert_absent(hostile_record)),
            ("cas-current", lambda store: store.compare_and_set(object(), current)),
            ("cas-replacement", lambda store: store.compare_and_set(current, object())),
            (
                "cas-identity",
                lambda store: store.compare_and_set(current, changed_identity),
            ),
            (
                "cas-fence",
                lambda store: store.compare_and_set(current, changed_fence),
            ),
            (
                "cas-original",
                lambda store: store.compare_and_set(current, changed_original),
            ),
            (
                "cas-latest-regression",
                lambda store: store.compare_and_set(uncertain, ordinal_regression),
            ),
        )
        for label, operation in cases:
            with self.subTest(label=label):
                connection = _NoSqlConnection()
                self.assert_store_error(lambda: operation(EffectAttemptStore(connection)))
                self.assertEqual(connection.calls, [])

    def test_compare_and_set_matches_every_physical_prior_column_null_safely(self) -> None:
        self.require_store()
        current = self.record(
            "uncertain",
            attempt=2,
            event_prefix="complete-prior",
            original_ordinal=10,
            latest_ordinal=20,
        )
        replacement_state = self.state("recovered-succeeded", attempt=2)
        replacement_event = self.event(
            replacement_state,
            self.event_kind("recovered-succeeded", compensation=False),
            event_id="complete-prior-recovered",
            ordinal=30,
            occurred_at="2030-01-01T00:00:01.000000Z",
        )
        replacement = EffectAttemptRecord(
            replacement_state,
            current.original_start_event,
            replacement_event,
        )
        connection = _RecordingConnection()
        self.assertIsNone(
            EffectAttemptStore(connection).compare_and_set(current, replacement)
        )
        self.assertEqual(len(connection.calls), 1)
        query = " ".join(str(connection.calls[0][0]).split())
        prior_columns = (
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
        for column in prior_columns:
            with self.subTest(column=column):
                self.assertIn(f"{column} IS NOT DISTINCT FROM %s", query)
        for predicate in (
            "run_id = %s",
            "activity_id = %s",
            "attempt = %s",
        ):
            with self.subTest(identity_predicate=predicate):
                self.assertIn(predicate, query)

    def test_read_miss_is_fixed_candidate_free_and_lock_is_explicit(self) -> None:
        self.require_store()
        for method, lock_fragment in (
            ("get", ""),
            ("get_for_update", "FOR UPDATE"),
        ):
            with self.subTest(method=method):
                connection = _RecordingConnection()
                store = EffectAttemptStore(connection)
                with self.assertRaises(KeyError) as caught:
                    getattr(store, method)(self.identity())
                self.assertEqual(str(caught.exception), "'effect attempt was not found'")
                self.assert_safe_error(caught.exception, "run-a", "activity-a")
                self.assertEqual(len(connection.calls), 1)
                query, parameters = connection.calls[0]
                normalized = " ".join(str(query).split())
                self.assertIn("run_id = %s AND activity_id = %s AND attempt = %s", normalized)
                self.assertEqual("FOR UPDATE" in normalized, bool(lock_fragment))
                self.assertEqual(parameters, ("run-a", "activity-a", 1))

    def test_only_primary_identity_collision_is_an_absent_insert(self) -> None:
        self.require_store()
        record = self.record()
        connection = _RecordingConnection()
        self.assertIsNone(EffectAttemptStore(connection).insert_absent(record))
        query = " ".join(str(connection.calls[0][0]).split())
        self.assertIn(
            "ON CONFLICT (run_id, activity_id, attempt) DO NOTHING",
            query,
        )

        integrity = UniqueViolation("event-role-canary")
        with self.assertRaises(UniqueViolation) as caught:
            EffectAttemptStore(_FailingConnection(integrity)).insert_absent(record)
        self.assertIs(caught.exception, integrity)

    def test_unexpected_sql_errors_escape_with_identity(self) -> None:
        self.require_store()
        canary = RuntimeError("effect-store-sql-canary")
        current = self.record(event_prefix="sql-current")
        replacement_state = self.state("succeeded")
        replacement_event = self.event(
            replacement_state,
            self.event_kind("succeeded", compensation=False),
            event_id="sql-replacement-latest",
            ordinal=8,
            occurred_at="2030-01-01T00:00:01.000000Z",
        )
        replacement = EffectAttemptRecord(
            replacement_state,
            current.original_start_event,
            replacement_event,
        )
        operations = (
            lambda store: store.get(self.identity()),
            lambda store: store.get_for_update(self.identity()),
            lambda store: store.insert_absent(self.record()),
            lambda store: store.compare_and_set(current, replacement),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(RuntimeError) as caught:
                    operation(EffectAttemptStore(_FailingConnection(canary)))
                self.assertIs(caught.exception, canary)

    def test_store_source_has_no_outer_effect_or_transaction_authority(self) -> None:
        self.require_store()
        source = Path(store_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden_calls = {
            "commit",
            "rollback",
            "clock",
            "id_factory",
            "next_event_ordinal",
            "add_event",
            "transaction",
        }
        called = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertEqual(called & forbidden_calls, set())
        forbidden_import_roots = {
            "docker",
            "requests",
            "httpx",
            "control_plane_kit_operations.policies",
            "control_plane_kit_operations.effects",
            "control_plane_kit_operations.postgres",
            "control_plane_kit_operations.postgres.unit_of_work",
            "control_plane_kit_core.operations.recovery",
        }
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)
        self.assertEqual(imported & forbidden_import_roots, set())
        forbidden_names = {
            "PostgresUnitOfWork",
            "transaction",
            "fold_effect_attempt",
            "EffectAttemptTransition",
        }
        used_names = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        } | {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        imported_names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertEqual((used_names | imported_names) & forbidden_names, set())


if __name__ == "__main__":
    unittest.main()
