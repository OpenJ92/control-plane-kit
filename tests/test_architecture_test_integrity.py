from __future__ import annotations

from pathlib import Path
import unittest

from tests.architecture import (
    AllowedSkip,
    IntegrityEvidenceKind,
    TestIntegrityPolicy,
    analyze_file,
    analyze_source,
    audit_test_integrity,
)


APPROVED_SKIPS = (
    AllowedSkip(
        "tests.test_block_control_fastapi",
        "unittest.skipUnless",
        "FastAPI is an optional package dependency outside the canonical Docker test image",
    ),
    AllowedSkip(
        "tests.test_instance_read_fastapi",
        "unittest.skipUnless",
        "FastAPI is an optional package dependency outside the canonical Docker test image",
    ),
    AllowedSkip(
        "tests.test_read_interface_demo_server",
        "unittest.skipUnless",
        "FastAPI is an optional package dependency outside the canonical Docker test image",
    ),
)
INTEGRITY_POLICY = TestIntegrityPolicy(APPROVED_SKIPS)


class ArchitectureTestIntegrityTests(unittest.TestCase):
    def test_repository_has_only_explicit_optional_dependency_skips(self) -> None:
        root = Path(__file__).parents[1]
        facts = tuple(
            analyze_file(path, root=root)
            for path in sorted((root / "tests").rglob("*.py"))
        )

        report = audit_test_integrity(facts, INTEGRITY_POLICY)

        self.assertEqual(report.violations, ())
        approved = tuple(
            value
            for value in report.evidence
            if value.kind is IntegrityEvidenceKind.APPROVED_SKIP
        )
        self.assertEqual(len(approved), 3)
        self.assertEqual(
            {value.location.path for value in approved},
            {
                "tests/test_block_control_fastapi.py",
                "tests/test_instance_read_fastapi.py",
                "tests/test_read_interface_demo_server.py",
            },
        )

    def test_unconditional_and_unapproved_skips_fail_with_locations(self) -> None:
        facts = analyze_source(
            "from unittest import skip\n"
            "import pytest\n"
            "@skip('later')\n"
            "def test_first():\n"
            "    work()\n"
            "@pytest.mark.xfail\n"
            "def test_second():\n"
            "    work()\n"
            "def test_third(self):\n"
            "    self.skipTest('later')\n",
            path="tests/test_skips.py",
            module="tests.test_skips",
        )

        report = audit_test_integrity((facts,), INTEGRITY_POLICY)

        self.assertEqual(len(report.violations), 3)
        self.assertEqual(
            {value.rule_id for value in report.violations},
            {"runtime-test-skip", "unapproved-test-skip"},
        )

    def test_placeholders_empty_tests_and_swallowed_exceptions_fail(self) -> None:
        facts = analyze_source(
            "def test_empty():\n"
            "    'documentation only'\n"
            "def test_placeholders(self):\n"
            "    self.assertTrue(True)\n"
            "    self.assertFalse(False)\n"
            "    self.assertEqual('same', 'same')\n"
            "    assert True\n"
            "    try:\n"
            "        work()\n"
            "    except RuntimeError:\n"
            "        pass\n",
            path="tests/test_placeholders.py",
            module="tests.test_placeholders",
        )

        report = audit_test_integrity((facts,), INTEGRITY_POLICY)

        self.assertEqual(len(report.violations), 6)
        self.assertEqual(
            {value.rule_id for value in report.violations},
            {"empty-test", "placeholder-assertion", "swallowed-exception"},
        )

    def test_mocks_are_reported_without_becoming_automatic_failures(self) -> None:
        facts = analyze_source(
            "from unittest.mock import patch as replace\n"
            "def test_adapter():\n"
            "    with replace('package.target'):\n"
            "        assert adapter_result() == expected_result()\n",
            path="tests/test_adapter.py",
            module="tests.test_adapter",
        )

        report = audit_test_integrity((facts,), INTEGRITY_POLICY)

        self.assertEqual(report.violations, ())
        self.assertEqual(len(report.evidence), 1)
        self.assertIs(report.evidence[0].kind, IntegrityEvidenceKind.TEST_DOUBLE)
        self.assertEqual(report.evidence[0].name, "unittest.mock.patch")

    def test_allowed_skip_requires_a_reviewable_reason(self) -> None:
        with self.assertRaises(ValueError):
            AllowedSkip("tests.test_optional", "unittest.skipUnless", "  ")

    def test_duplicate_and_literal_false_approved_skips_fail_closed(self) -> None:
        declaration = AllowedSkip(
            "tests.test_optional",
            "unittest.skipUnless",
            "optional dependency",
        )
        with self.assertRaises(ValueError):
            TestIntegrityPolicy((declaration, declaration))

        facts = analyze_source(
            "from unittest import skipUnless\n"
            "@skipUnless(False, 'disabled')\n"
            "def test_optional():\n"
            "    work()\n",
            path="tests/test_optional.py",
            module="tests.test_optional",
        )

        report = audit_test_integrity(
            (facts,),
            TestIntegrityPolicy((declaration,)),
        )

        self.assertEqual(len(report.violations), 1)
        self.assertEqual(report.violations[0].rule_id, "unapproved-test-skip")


if __name__ == "__main__":
    unittest.main()
