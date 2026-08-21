from __future__ import annotations

import ast
from dataclasses import fields
import inspect
import os
from pathlib import Path
import unittest

from control_plane_kit_core.operations import EffectAttemptIdentity
from control_plane_kit_operations.effect_attempt_intent_evidence import (
    EffectAttemptIntentRecord,
)
from control_plane_kit_operations.postgres.stores import PostgresStoreBundle
from control_plane_kit_operations.records import OperationsRecordError
from tests.effect_attempt_intent_fixture import forge_exact, subclass_copy
from tests.postgres_effect_attempt_intent_store_fixture import (
    EffectAttemptIntentStore,
    MODULE_NAME,
    PostgresEffectAttemptIntentStoreFixture,
    _validate_current_rows,
    store_module,
)


INPUT_ERROR = "effect attempt intent store input is invalid"
ROW_ERROR = "effect attempt intent row is invalid"
MISS_ERROR = "effect attempt intent evidence was not found"


class _EmptyCursor:
    def fetchone(self):
        return None


class _RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def execute(self, *arguments):
        self.calls.append(arguments)
        return _EmptyCursor()


class _NoSqlConnection(_RecordingConnection):
    def execute(self, *arguments):
        self.calls.append(arguments)
        raise AssertionError("invalid intent-store input reached SQL")


class _FailingConnection:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    def execute(self, *_arguments):
        raise self.error


def _subclass_copy_without_constructor(value):
    hostile_type = type(f"Hostile{type(value).__name__}", (type(value),), {})
    hostile = object.__new__(hostile_type)
    for item in fields(value):
        object.__setattr__(hostile, item.name, getattr(value, item.name))
    return hostile


class PostgresEffectAttemptIntentStoreContractTests(
    PostgresEffectAttemptIntentStoreFixture,
    unittest.TestCase,
):
    def assert_store_error(self, operation, *canaries: object) -> None:
        with self.assertRaises(OperationsRecordError) as caught:
            operation()
        self.assertEqual(str(caught.exception), INPUT_ERROR)
        self.assert_safe_error(caught.exception, *(str(value) for value in canaries))

    def test_store_surface_is_exact_private_and_bundle_owned(self) -> None:
        self.require_intent_store()
        self.assertEqual(
            frozenset(
                name
                for name, value in vars(EffectAttemptIntentStore).items()
                if callable(value) and not name.startswith("_")
            ),
            frozenset(("insert", "get")),
        )
        self.assertEqual(
            tuple(inspect.signature(EffectAttemptIntentStore).parameters),
            ("connection",),
        )
        self.assertEqual(
            tuple(inspect.signature(EffectAttemptIntentStore.insert).parameters),
            ("self", "record"),
        )
        self.assertEqual(
            tuple(inspect.signature(EffectAttemptIntentStore.get).parameters),
            ("self", "identity"),
        )
        self.assertIn(
            "effect_attempt_intents",
            PostgresStoreBundle.__dataclass_fields__,
        )
        connection = _RecordingConnection()
        bundle = PostgresStoreBundle(connection)
        self.assertIs(type(bundle.effect_attempt_intents), EffectAttemptIntentStore)
        with self.assertRaises(KeyError) as caught:
            bundle.effect_attempt_intents.get(self.identity())
        self.assertEqual(str(caught.exception).strip("'"), MISS_ERROR)
        self.assertEqual(len(connection.calls), 1)

        import control_plane_kit_operations as operations_root
        import control_plane_kit_operations.postgres as postgres_root

        self.assertFalse(hasattr(operations_root, "EffectAttemptIntentStore"))
        self.assertFalse(hasattr(postgres_root, "EffectAttemptIntentStore"))

    def test_exact_nominal_inputs_reject_before_sql(self) -> None:
        self.require_intent_store()
        attempt, record = self.intent_attempt()
        identity = attempt.state.identity
        candidates = (
            ("insert-object", "insert", object()),
            (
                "insert-subclass",
                "insert",
                _subclass_copy_without_constructor(record),
            ),
            (
                "insert-forged-identity",
                "insert",
                forge_exact(
                    EffectAttemptIntentRecord,
                    identity=forge_exact(
                        EffectAttemptIdentity,
                        run_id=identity.run_id,
                        activity_id=identity.activity_id,
                        attempt=None,
                    ),
                    original_start_event=record.original_start_event,
                    intent=record.intent,
                ),
            ),
            ("get-object", "get", object()),
            ("get-subclass", "get", subclass_copy(identity)),
            (
                "get-forged",
                "get",
                forge_exact(
                    EffectAttemptIdentity,
                    run_id=identity.run_id,
                    activity_id=identity.activity_id,
                ),
            ),
        )
        for label, method, candidate in candidates:
            with self.subTest(label=label):
                connection = _NoSqlConnection()
                store = EffectAttemptIntentStore(connection)
                self.assert_store_error(
                    lambda: getattr(store, method)(candidate),
                    label,
                )
                self.assertEqual(connection.calls, [])

    def test_queries_are_bounded_exact_and_caller_connection_only(self) -> None:
        self.require_intent_store()
        _attempt, record = self.intent_attempt()
        connection = _RecordingConnection()
        store = EffectAttemptIntentStore(connection)

        self.assertEqual(store.insert(record), record)
        insert_query = " ".join(str(connection.calls[0][0]).split())
        self.assertIn("INSERT INTO cpk_effect_attempt_intents", insert_query)
        self.assertNotIn("ON CONFLICT", insert_query)
        self.assertNotIn("COMMIT", insert_query.upper())

        with self.assertRaises(KeyError):
            store.get(record.identity)
        get_query = " ".join(str(connection.calls[1][0]).split())
        self.assertIn("octet_length(intent.preimage)", get_query)
        self.assertIn("<= 1048576", get_query)
        self.assertIn(
            "WHERE intent.run_id = %s AND intent.activity_id = %s "
            "AND intent.attempt = %s",
            get_query,
        )
        self.assertNotIn("FOR UPDATE", get_query)
        self.assertNotIn("OFFSET", get_query.upper())

    def test_expected_errors_are_fixed_and_unexpected_faults_escape(self) -> None:
        self.require_intent_store()
        attempt, record = self.intent_attempt()
        internal = RuntimeError("intent-store-driver-canary")
        operations = (
            lambda store: store.insert(record),
            lambda store: store.get(attempt.state.identity),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaises(RuntimeError) as caught:
                    operation(EffectAttemptIntentStore(_FailingConnection(internal)))
                self.assertIs(caught.exception, internal)

    def test_current_validator_surface_is_private_bounded_and_effect_free(self) -> None:
        self.require_intent_store()
        self.assertIsNotNone(_validate_current_rows)
        self.assertEqual(
            tuple(inspect.signature(_validate_current_rows).parameters),
            ("connection",),
        )
        source_path = Path(inspect.getsourcefile(store_module))
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        self.assertEqual(
            imports,
            {
                "__future__",
                "control_plane_kit_core.operations",
                "control_plane_kit_operations.effect_attempt_intent_evidence",
                "control_plane_kit_operations.records",
            },
        )
        forbidden = {
            "commit",
            "rollback",
            "clock_timestamp",
            "uuid",
            "random",
            "open",
            "unlink",
            "update",
            "delete",
        }
        rendered = source_path.read_text(encoding="utf-8").lower()
        for value in forbidden:
            with self.subTest(value=value):
                self.assertNotIn(value, rendered)

        inventory = os.environ.get("CPK_PACKAGE_MODULE_INVENTORY")
        self.assertIsNotNone(inventory)
        rows = __import__("json").loads(
            Path(inventory).read_text(encoding="utf-8")
        )["modules"]
        row = next(value for value in rows if value["module"] == MODULE_NAME)
        self.assertEqual(row["owner"], "operation")
        self.assertEqual(row["destination"], MODULE_NAME)
        self.assertEqual(
            tuple(row["protecting_tests"]),
            (
                "tests/test_postgres_effect_attempt_intent_store_contract.py",
                "tests/test_postgres_effect_attempt_intent_store.py",
                "tests/test_postgres_effect_attempt_intent_schema.py",
                "tests/test_postgres_effect_attempt_start_intent.py",
            ),
        )


if __name__ == "__main__":
    unittest.main()
