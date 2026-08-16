from __future__ import annotations

import ast
import dataclasses
import importlib
from pathlib import Path
import unittest

from psycopg.errors import UniqueViolation

from control_plane_kit_core.operations import (
    EffectAttemptFence,
    EffectAttemptIdentity,
)
from control_plane_kit_operations.postgres.stores import PostgresStoreBundle
from control_plane_kit_operations.records import OperationsRecordError
from tests.effect_attempt_record_fixture import EffectAttemptRecordFixture


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
            tuple(
                name
                for name in ("get", "get_for_update", "insert_absent", "compare_and_set")
                if hasattr(EffectAttemptStore, name)
            ),
            ("get", "get_for_update", "insert_absent", "compare_and_set"),
        )
        self.assertIn("effect_attempts", PostgresStoreBundle.__dataclass_fields__)
        import control_plane_kit_operations as operations_root
        import control_plane_kit_operations.postgres as postgres_root

        self.assertFalse(hasattr(operations_root, "EffectAttemptStore"))
        self.assertFalse(hasattr(postgres_root, "EffectAttemptStore"))

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
        cases = (
            ("insert", lambda store: store.insert_absent(object())),
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
        )
        for label, operation in cases:
            with self.subTest(label=label):
                connection = _NoSqlConnection()
                self.assert_store_error(lambda: operation(EffectAttemptStore(connection)))
                self.assertEqual(connection.calls, [])

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
        operations = (
            lambda store: store.get(self.identity()),
            lambda store: store.get_for_update(self.identity()),
            lambda store: store.insert_absent(self.record()),
            lambda store: store.compare_and_set(self.record(), self.record()),
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
        }
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)
        self.assertEqual(imported & forbidden_import_roots, set())


if __name__ == "__main__":
    unittest.main()
