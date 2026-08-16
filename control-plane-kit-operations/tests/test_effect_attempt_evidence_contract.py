from __future__ import annotations

import ast
import dataclasses
import json
import os
from pathlib import Path
import unittest

from control_plane_kit_operations.records import (
    BoundedEvidence,
    OperationsRecordError,
)
from tests.effect_attempt_record_fixture import (
    EffectAttemptEventEvidence,
    EffectAttemptRecord,
    EffectAttemptRecordFixture,
    HostileEffectAttemptState,
    HostileInt,
    HostileStr,
    STORIES,
    canonical_state_fingerprint,
    effect_attempt_state_fingerprint,
    effect_attempts_module,
    operations_root,
)


class EffectAttemptEvidenceContractTests(
    EffectAttemptRecordFixture,
    unittest.TestCase,
):
    def test_public_shape_is_frozen_exact_and_root_identical(self) -> None:
        self.require_language()
        self.assertIs(
            operations_root.EffectAttemptEventEvidence,
            EffectAttemptEventEvidence,
        )
        self.assertIs(operations_root.EffectAttemptRecord, EffectAttemptRecord)
        self.assertIs(
            operations_root.effect_attempt_state_fingerprint,
            effect_attempt_state_fingerprint,
        )
        self.assertTrue(dataclasses.is_dataclass(EffectAttemptEventEvidence))
        self.assertTrue(dataclasses.is_dataclass(EffectAttemptRecord))
        self.assertTrue(EffectAttemptEventEvidence.__dataclass_params__.frozen)
        self.assertTrue(EffectAttemptRecord.__dataclass_params__.frozen)
        self.assertEqual(
            tuple(
                field.name
                for field in dataclasses.fields(EffectAttemptEventEvidence)
            ),
            ("attempt", "state_fingerprint"),
        )
        self.assertEqual(
            tuple(field.name for field in dataclasses.fields(EffectAttemptRecord)),
            ("state", "original_start_event", "latest_transition_event"),
        )

    def test_state_fingerprint_matches_independent_canonical_commitments(self) -> None:
        self.require_language()
        states = [self.state(story) for story in STORIES]
        states.append(self.state("started", attempt=2))
        actual = []
        for state in states:
            with self.subTest(state=state.descriptor()):
                expected = canonical_state_fingerprint(state)
                self.assertEqual(effect_attempt_state_fingerprint(state), expected)
                actual.append(expected)
        self.assertEqual(len(set(actual)), len(states))
        self.assertNotEqual(actual[1], actual[5])
        self.assertNotEqual(actual[2], actual[6])
        self.assertEqual(
            actual[0],
            "36b2e0735976aab98be59df725ded5332e5709dbb7f60c7244f5a8a3c1f86416",
        )

    def test_event_evidence_is_exact_bounded_and_descriptor_stable(self) -> None:
        self.require_language()
        fingerprint = canonical_state_fingerprint(self.state())
        evidence = EffectAttemptEventEvidence(1, fingerprint)
        self.assertEqual(
            evidence.descriptor(),
            {"attempt": 1, "state_fingerprint": fingerprint},
        )
        self.assertEqual(
            BoundedEvidence.from_mapping(
                {"effect_attempt": evidence.descriptor()}
            ).descriptor(),
            {
                "effect_attempt": {
                    "attempt": 1,
                    "state_fingerprint": fingerprint,
                }
            },
        )
        for values in (
            (True, fingerprint),
            (HostileInt(1), fingerprint),
            (0, fingerprint),
            (2_147_483_648, fingerprint),
            (1, HostileStr(fingerprint)),
            (1, "A" * 64),
            (1, "secret-canary"),
        ):
            with self.subTest(values=values):
                with self.assertRaises(OperationsRecordError) as caught:
                    EffectAttemptEventEvidence(*values)
                self.assertEqual(
                    str(caught.exception),
                    "effect attempt event evidence is invalid",
                )
                self.assert_safe_error(caught.exception, "secret-canary")

    def test_state_fingerprint_requires_exact_nominal_state(self) -> None:
        self.require_language()
        state = self.state()
        hostile_state = HostileEffectAttemptState(**state.__dict__)
        with self.assertRaises(OperationsRecordError) as caught:
            effect_attempt_state_fingerprint(hostile_state)
        self.assertEqual(
            str(caught.exception),
            "effect attempt state must be typed",
        )
        self.assert_safe_error(caught.exception)

    def test_module_is_effect_free_and_has_one_exhaustive_inventory_row(self) -> None:
        self.require_language()
        source = Path(effect_attempts_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        forbidden = (
            "postgres",
            "psycopg",
            "store",
            "unit_of_work",
            "transactions",
        )
        self.assertFalse(
            [name for name in imports if any(part in name for part in forbidden)]
        )

        inventory_path = Path(
            os.environ.get(
                "CPK_PACKAGE_MODULE_INVENTORY",
                Path(__file__).parents[2]
                / "docs"
                / "architecture"
                / "package-module-inventory.json",
            )
        )
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        rows = [
            row
            for row in inventory["modules"]
            if row["module"] == "control_plane_kit_operations.effect_attempts"
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["owner"], "operation")
        self.assertEqual(
            rows[0]["destination"],
            "control_plane_kit_operations.effect_attempts",
        )
        self.assertEqual(rows[0]["optional_external_dependencies"], [])
        self.assertEqual(
            rows[0]["canonical_public_exports"],
            [
                "EffectAttemptEventEvidence",
                "EffectAttemptRecord",
                "effect_attempt_state_fingerprint",
            ],
        )
        self.assertIn(
            "tests/test_effect_attempt_evidence_contract.py",
            rows[0]["protecting_tests"],
        )
        self.assertIn(
            "tests/test_effect_attempt_record_contract.py",
            rows[0]["protecting_tests"],
        )


if __name__ == "__main__":
    unittest.main()
