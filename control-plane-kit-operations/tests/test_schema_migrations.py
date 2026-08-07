from __future__ import annotations

from dataclasses import FrozenInstanceError
from hashlib import sha256
import os
import subprocess
import sys
import unittest

import control_plane_kit_operations.postgres as postgres


_V1_SCHEMA_SHA256 = (
    "fc9b5547fc51ec681130c41facea785dbd24649049417455b184ea05886beed8"
)
_V2_SCHEMA_SHA256 = (
    "95c7782cf66875a3f70c6354b86054ec4ca86f45dca7d2ccb4d971920162c329"
)
_V3_SCHEMA_SHA256 = (
    "1f4cf8704affd90ab2ceb17d2a00a62a91e265d2c8c1f49a77c9a6e446cdbdfa"
)
_V4_SCHEMA_SHA256 = (
    "523fb7528d544ce9214181b9886adb5d96130341561c44613f038caee42b99c1"
)


class SchemaMigrationLanguageTests(unittest.TestCase):
    def test_migration_identity_uses_exact_utf8_sql_bytes(self) -> None:
        migration_type = self._required("SchemaMigration")

        migration = migration_type(
            version=1,
            name="operations-baseline",
            sql="SELECT 1;\n",
        )

        self.assertEqual(migration.version, 1)
        self.assertEqual(migration.name, "operations-baseline")
        self.assertEqual(migration.sql, "SELECT 1;\n")
        self.assertNotIn("SELECT 1", repr(migration))
        self.assertEqual(
            migration.checksum_sha256,
            sha256(b"SELECT 1;\n").hexdigest(),
        )
        self.assertNotEqual(
            migration.checksum_sha256,
            migration_type(
                version=1,
                name="operations-baseline",
                sql="SELECT 1;",
            ).checksum_sha256,
        )
        with self.assertRaises(FrozenInstanceError):
            migration.name = "changed"

    def test_migration_and_applied_identity_reject_malformed_values(self) -> None:
        migration_type = self._required("SchemaMigration")
        applied_type = self._required("AppliedSchemaMigration")
        error_type = self._required("SchemaMigrationError")

        invalid_migrations = (
            {"version": 0, "name": "baseline", "sql": "SELECT 1"},
            {"version": -1, "name": "baseline", "sql": "SELECT 1"},
            {"version": True, "name": "baseline", "sql": "SELECT 1"},
            {"version": 1.0, "name": "baseline", "sql": "SELECT 1"},
            {"version": 1, "name": "", "sql": "SELECT 1"},
            {"version": 1, "name": "Uppercase", "sql": "SELECT 1"},
            {"version": 1, "name": "a" * 129, "sql": "SELECT 1"},
            {"version": 1, "name": "baseline", "sql": ""},
            {"version": 1, "name": "baseline", "sql": "   \n"},
            {"version": 1, "name": "baseline", "sql": b"SELECT 1"},
            {"version": 1, "name": "baseline", "sql": "SELECT \x00"},
        )
        for values in invalid_migrations:
            with self.subTest(values=values):
                with self.assertRaises(error_type):
                    migration_type(**values)

        invalid_applied = (
            {"version": 0, "name": "baseline", "checksum_sha256": "a" * 64},
            {"version": 1, "name": "", "checksum_sha256": "a" * 64},
            {"version": 1, "name": "baseline", "checksum_sha256": "A" * 64},
            {"version": 1, "name": "baseline", "checksum_sha256": "a" * 63},
            {"version": 1, "name": "baseline", "checksum_sha256": 1},
        )
        for values in invalid_applied:
            with self.subTest(values=values):
                with self.assertRaises(error_type):
                    applied_type(**values)

    def test_registry_requires_an_exact_contiguous_ordered_tuple(self) -> None:
        migration_type = self._required("SchemaMigration")
        registry_type = self._required("SchemaMigrationRegistry")
        error_type = self._required("SchemaMigrationError")
        first = migration_type(version=1, name="baseline", sql="SELECT 1")
        second = migration_type(version=2, name="next", sql="SELECT 2")
        third = migration_type(version=3, name="third", sql="SELECT 3")
        duplicate_name = migration_type(
            version=2,
            name="baseline",
            sql="SELECT 2",
        )

        registry = registry_type((first, second, third))

        self.assertEqual(registry.migrations, (first, second, third))
        self.assertEqual(registry.target_version, 3)
        invalid_registries = (
            (),
            [first],
            (second,),
            (first, first),
            (first, duplicate_name),
            (first, third),
            (second, first),
            (first, object()),
        )
        for migrations in invalid_registries:
            with self.subTest(migrations=migrations):
                with self.assertRaises(error_type):
                    registry_type(migrations)

    def test_plan_constructor_rejects_incoherent_actions(self) -> None:
        registry, first, second = self._registry()
        applied_type = self._required("AppliedSchemaMigration")
        observed_type = self._required("ObservedSchemaState")
        observed_kind = self._required("ObservedSchemaKind")
        action_type = self._required("SchemaMigrationAction")
        action_kind = self._required("SchemaMigrationActionKind")
        plan_type = self._required("SchemaMigrationPlan")
        error_type = self._required("SchemaMigrationError")
        empty = observed_type(kind=observed_kind.EMPTY)
        baseline = observed_type(kind=observed_kind.CURRENT_BASELINE)
        versioned = observed_type(
            kind=observed_kind.VERSIONED,
            applied_migrations=(
                applied_type(
                    version=first.version,
                    name=first.name,
                    checksum_sha256=first.checksum_sha256,
                ),
            ),
        )
        apply_first = action_type(action_kind.APPLY, first)
        apply_second = action_type(action_kind.APPLY, second)
        record_first = action_type(action_kind.RECORD_BASELINE, first)

        invalid = (
            {"observed": empty, "actions": (), "target_version": 2},
            {
                "observed": empty,
                "actions": (record_first, apply_second),
                "target_version": 2,
            },
            {
                "observed": baseline,
                "actions": (apply_first, apply_second),
                "target_version": 2,
            },
            {
                "observed": versioned,
                "actions": (apply_first,),
                "target_version": 1,
            },
            {
                "observed": versioned,
                "actions": (apply_second,),
                "target_version": 3,
            },
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(error_type):
                    plan_type(**values)

        self.assertEqual(
            plan_type(
                observed=empty,
                actions=(apply_first, apply_second),
                target_version=2,
            ),
            registry.plan(empty),
        )

    def test_empty_schema_plans_every_migration_as_apply(self) -> None:
        registry, first, second = self._registry()
        observed_type = self._required("ObservedSchemaState")
        observed_kind = self._required("ObservedSchemaKind")
        action_kind = self._required("SchemaMigrationActionKind")

        observed = observed_type(kind=observed_kind.EMPTY)
        plan = registry.plan(observed)

        self.assertEqual(plan.observed, observed)
        self.assertEqual(plan.target_version, 2)
        self.assertEqual(
            tuple((action.kind, action.migration) for action in plan.actions),
            (
                (action_kind.APPLY, first),
                (action_kind.APPLY, second),
            ),
        )
        self.assertEqual(plan, registry.plan(observed))

    def test_current_unversioned_baseline_is_recorded_before_later_apply(self) -> None:
        registry, first, second = self._registry()
        observed_type = self._required("ObservedSchemaState")
        observed_kind = self._required("ObservedSchemaKind")
        action_kind = self._required("SchemaMigrationActionKind")

        plan = registry.plan(
            observed_type(kind=observed_kind.CURRENT_BASELINE)
        )

        self.assertEqual(
            tuple((action.kind, action.migration) for action in plan.actions),
            (
                (action_kind.RECORD_BASELINE, first),
                (action_kind.APPLY, second),
            ),
        )

    def test_versioned_history_must_be_an_exact_registry_prefix(self) -> None:
        registry, first, second = self._registry()
        applied_type = self._required("AppliedSchemaMigration")
        observed_type = self._required("ObservedSchemaState")
        observed_kind = self._required("ObservedSchemaKind")
        action_kind = self._required("SchemaMigrationActionKind")
        error_type = self._required("SchemaMigrationError")

        applied_first = applied_type(
            version=first.version,
            name=first.name,
            checksum_sha256=first.checksum_sha256,
        )
        pending_plan = registry.plan(
            observed_type(
                kind=observed_kind.VERSIONED,
                applied_migrations=(applied_first,),
            )
        )
        self.assertEqual(
            tuple((action.kind, action.migration) for action in pending_plan.actions),
            ((action_kind.APPLY, second),),
        )
        applied_second = applied_type(
            version=second.version,
            name=second.name,
            checksum_sha256=second.checksum_sha256,
        )
        current_plan = registry.plan(
            observed_type(
                kind=observed_kind.VERSIONED,
                applied_migrations=(applied_first, applied_second),
            )
        )
        self.assertEqual(current_plan.actions, ())

        invalid_histories = (
            (
                applied_type(
                    version=1,
                    name="renamed",
                    checksum_sha256=first.checksum_sha256,
                ),
            ),
            (
                applied_type(
                    version=1,
                    name=first.name,
                    checksum_sha256="f" * 64,
                ),
            ),
            (applied_second,),
            (applied_second, applied_first),
            (applied_first, applied_first),
            (
                applied_first,
                applied_second,
                applied_type(
                    version=3,
                    name="newer-than-package",
                    checksum_sha256="e" * 64,
                ),
            ),
        )
        for applied in invalid_histories:
            with self.subTest(applied=applied):
                with self.assertRaises(error_type):
                    registry.plan(
                        observed_type(
                            kind=observed_kind.VERSIONED,
                            applied_migrations=applied,
                        )
                    )

    def test_observed_state_rejects_incoherent_kind_and_history(self) -> None:
        applied_type = self._required("AppliedSchemaMigration")
        observed_type = self._required("ObservedSchemaState")
        observed_kind = self._required("ObservedSchemaKind")
        error_type = self._required("SchemaMigrationError")
        applied = applied_type(
            version=1,
            name="baseline",
            checksum_sha256="a" * 64,
        )

        for kind in (observed_kind.EMPTY, observed_kind.CURRENT_BASELINE):
            with self.subTest(kind=kind):
                with self.assertRaises(error_type):
                    observed_type(kind=kind, applied_migrations=(applied,))
        with self.assertRaises(error_type):
            observed_type(kind=observed_kind.VERSIONED)
        with self.assertRaises(error_type):
            observed_type(kind="versioned", applied_migrations=(applied,))
        with self.assertRaises(error_type):
            observed_type(
                kind=observed_kind.VERSIONED,
                applied_migrations=[applied],
            )

    def test_v1_baseline_checksum_and_package_exports_are_frozen(self) -> None:
        registry = self._required("POSTGRES_SCHEMA_MIGRATIONS")
        pinned_checksum = self._required("POSTGRES_SCHEMA_V1_SHA256")

        self.assertEqual(pinned_checksum, _V1_SCHEMA_SHA256)
        self.assertEqual(registry.target_version, 4)
        self.assertEqual(len(registry.migrations), 4)
        baseline = registry.migrations[0]
        self.assertEqual(baseline.version, 1)
        self.assertEqual(baseline.name, "operations-baseline")
        self.assertEqual(baseline.sql, postgres.POSTGRES_SCHEMA)
        self.assertEqual(baseline.checksum_sha256, _V1_SCHEMA_SHA256)
        self.assertEqual(registry.migrations[1].version, 2)
        self.assertEqual(registry.migrations[1].name, "coordination-timestamps")
        self.assertEqual(registry.migrations[1].checksum_sha256, _V2_SCHEMA_SHA256)
        self.assertEqual(registry.migrations[2].version, 3)
        self.assertEqual(
            registry.migrations[2].name,
            "graph-product-authority-timestamps",
        )
        self.assertEqual(registry.migrations[2].checksum_sha256, _V3_SCHEMA_SHA256)
        self.assertEqual(registry.migrations[3].version, 4)
        self.assertEqual(
            registry.migrations[3].name,
            "secret-registration-timestamps",
        )
        self.assertEqual(registry.migrations[3].checksum_sha256, _V4_SCHEMA_SHA256)
        for name in (
            "AppliedSchemaMigration",
            "ObservedSchemaKind",
            "ObservedSchemaState",
            "SchemaMigration",
            "SchemaMigrationAction",
            "SchemaMigrationActionKind",
            "SchemaMigrationError",
            "SchemaMigrationPlan",
            "SchemaMigrationRegistry",
        ):
            with self.subTest(name=name):
                self.assertIsNotNone(self._required(name))

    def test_v1_schema_rendering_is_hash_seed_independent(self) -> None:
        command = (
            "from control_plane_kit_operations.postgres import "
            "POSTGRES_SCHEMA_V1_SHA256; print(POSTGRES_SCHEMA_V1_SHA256)"
        )

        for seed in ("1", "2", "8675309"):
            with self.subTest(seed=seed):
                result = subprocess.run(
                    [sys.executable, "-c", command],
                    check=True,
                    capture_output=True,
                    text=True,
                    env={**os.environ, "PYTHONHASHSEED": seed},
                )
                self.assertEqual(result.stdout.strip(), _V1_SCHEMA_SHA256)

    def test_history_errors_do_not_echo_sql_content(self) -> None:
        migration_type = self._required("SchemaMigration")
        applied_type = self._required("AppliedSchemaMigration")
        registry_type = self._required("SchemaMigrationRegistry")
        observed_type = self._required("ObservedSchemaState")
        observed_kind = self._required("ObservedSchemaKind")
        error_type = self._required("SchemaMigrationError")
        migration = migration_type(
            version=1,
            name="baseline",
            sql="SELECT 'private-schema-material'",
        )
        registry = registry_type((migration,))
        observed = observed_type(
            kind=observed_kind.VERSIONED,
            applied_migrations=(
                applied_type(
                    version=1,
                    name="baseline",
                    checksum_sha256="a" * 64,
                ),
            ),
        )

        with self.assertRaises(error_type) as raised:
            registry.plan(observed)

        self.assertNotIn("private-schema-material", str(raised.exception))
        self.assertNotIn("SELECT", str(raised.exception))

    def _registry(self):
        migration_type = self._required("SchemaMigration")
        registry_type = self._required("SchemaMigrationRegistry")
        first = migration_type(version=1, name="baseline", sql="SELECT 1")
        second = migration_type(version=2, name="next", sql="SELECT 2")
        return registry_type((first, second)), first, second

    def _required(self, name: str):
        value = getattr(postgres, name, None)
        if value is None:
            self.fail(f"{name} is not implemented")
        return value


if __name__ == "__main__":
    unittest.main()
