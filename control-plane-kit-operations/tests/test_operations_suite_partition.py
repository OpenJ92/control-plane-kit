"""The maintained runner conserves discovery occurrences and real outcomes."""

import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import textwrap
import unittest
from uuid import uuid4


class OperationsSuitePartitionTests(unittest.TestCase):
    def setUp(self):
        path = Path(os.environ.get("CPK_OPERATIONS_SUITE_HELPER", "/test-support/operations_suite.py"))
        self.assertTrue(path.is_file(), "owning shard selection/report helper is missing")
        spec = importlib.util.spec_from_file_location("cpk_operations_suite_test_helper", path)
        self.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.helper)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "tests").mkdir()
        (self.root / "tests/approved_skips.json").write_text("[]", encoding="utf-8")
        self.prefix = "test_partition_" + uuid4().hex
        original_path = list(sys.path)
        self.addCleanup(self.restore_imports, original_path)
        self.coordinates = {"source": "a" * 40, "architecture": "b" * 40,
                            "inputs": "c" * 64, "python": "3.14"}

    def restore_imports(self, original_path):
        sys.path[:] = original_path
        for name in tuple(sys.modules):
            if name.startswith(self.prefix):
                del sys.modules[name]

    def write(self, name, source):
        (self.root / "tests" / (name + ".py")).write_text(textwrap.dedent(source), encoding="utf-8")

    def names(self, count):
        found = {}
        for number in range(1000):
            name = self.prefix + "_" + str(number)
            found.setdefault(self.helper.shard_index("tests/" + name + ".py", count), name)
            if len(found) == count:
                return [found[index] for index in range(count)]
        self.fail("fixture names did not cover shards")

    def run_shard(self, index=0, count=1):
        return self.helper.run_shard(self.root, index, count, self.coordinates, stream=io.StringIO())

    def aggregate(self, reports, count=2):
        return self.helper.validate_reports(reports, count, "a" * 40, "b" * 40)

    def test_canonical_path_uses_first_four_sha256_bytes(self):
        self.assertEqual(self.helper.shard_index("tests/test_package_gate.py", 4), 3)
        self.assertEqual(self.helper.shard_index("tests/test_cpk_server_adapters.py", 4), 0)
        for path in ("./tests/test_x.py", "../test_x.py", "/tests/test_x.py", "tests\\test_x.py"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.helper.shard_index(path, 4)

    def test_imported_aliases_and_inherited_methods_keep_origin_and_fixtures(self):
        first, second = self.names(2)
        self.write(first, """
            import unittest
            events = []
            class Original(unittest.TestCase):
                @classmethod
                def setUpClass(cls): events.append('setup:' + cls.__name__)
                @classmethod
                def tearDownClass(cls): events.append('teardown:' + cls.__name__)
                def test_value(self):
                    events.append('body:' + type(self).__name__)
                    self.assertEqual(2 + 2, 4)
        """)
        self.write(second, f"""
            from {first} import Original
            class Derived(Original):
                def test_other(self): self.assertTrue(True)
        """)
        reports = [self.run_shard(index, 2) for index in range(2)]
        summary = self.aggregate(reports)
        self.assertEqual(summary["tests"], 4)
        full = reports[0]["discovered"]
        aliases = [row for row in full if row[2] == first + ".Original.test_value"]
        self.assertEqual({row[0] for row in aliases}, {"tests/" + first + ".py", "tests/" + second + ".py"})
        events = sys.modules[first].events
        self.assertEqual(events.count("setup:Original"), events.count("teardown:Original"))
        self.assertEqual(events.count("setup:Derived"), 1)
        self.assertEqual(events.count("teardown:Derived"), 1)
        self.assertEqual(events.count("body:Original"), 2)

    def test_failed_subtest_cannot_be_accepted_from_planned_membership(self):
        self.write(self.names(1)[0], """
            import unittest
            class Example(unittest.TestCase):
                def test_value(self):
                    with self.subTest(part='first'): self.assertEqual(1, 2)
        """)
        report = self.run_shard()
        self.assertFalse(report["successful"])
        self.assertEqual(report["results"][0]["status"], "failed")
        with self.assertRaises(ValueError):
            self.aggregate([report], 1)

    def test_class_setup_error_never_counts_planned_tests_as_executed(self):
        self.write(self.names(1)[0], """
            import unittest
            class Example(unittest.TestCase):
                @classmethod
                def setUpClass(cls): raise RuntimeError('synthetic setup failure')
                def test_value(self): self.assertTrue(True)
        """)
        report = self.run_shard()
        self.assertFalse(report["successful"])
        self.assertEqual(report["results"], [])
        with self.assertRaises(ValueError):
            self.aggregate([report], 1)

    def test_import_error_and_empty_discovery_fail_closed(self):
        with self.assertRaises(ValueError):
            self.run_shard()
        self.write(self.names(1)[0], "raise ImportError('synthetic collection failure')")
        with self.assertRaises(ValueError):
            self.run_shard()

    def test_unapproved_skip_fails_and_approved_skip_is_distinct(self):
        name = self.names(1)[0]
        self.write(name, """
            import unittest
            class Example(unittest.TestCase):
                @unittest.skipIf(True, 'synthetic approved condition')
                def test_value(self): self.assertTrue(True)
        """)
        refused = self.run_shard()
        self.assertFalse(refused["successful"])
        (self.root / "tests/approved_skips.json").write_text(json.dumps([{
            "identity": f"tests/{name}.py::Example.test_value",
            "reason": "synthetic approved condition",
        }]), encoding="utf-8")
        approved = self.run_shard()
        self.assertTrue(approved["successful"])
        self.assertEqual(approved["results"][0]["status"], "skipped")
        self.assertEqual(self.aggregate([approved], 1)["skipped"], 1)

    def test_missing_duplicate_foreign_or_incomplete_reports_cannot_pass(self):
        for name in self.names(2):
            self.write(name, """
                import unittest
                class Example(unittest.TestCase):
                    def test_value(self): self.assertTrue(True)
            """)
        reports = [self.run_shard(index, 2) for index in range(2)]
        self.assertEqual(self.aggregate(reports)["tests"], 2)
        broken = []
        broken.append(reports[:1])
        broken.append([reports[0], reports[0]])
        foreign = copy.deepcopy(reports)
        foreign[1]["coordinates"]["source"] = "d" * 40
        broken.append(foreign)
        absent = copy.deepcopy(reports)
        absent[1]["results"] = []
        broken.append(absent)
        overlap = copy.deepcopy(reports)
        overlap[1]["results"] = copy.deepcopy(overlap[0]["results"])
        broken.append(overlap)
        changed = copy.deepcopy(reports)
        changed[1]["discovered"] = []
        broken.append(changed)
        for index, candidate in enumerate(broken):
            with self.subTest(case=index), self.assertRaises(ValueError):
                self.aggregate(candidate)

    def test_invalid_worker_and_unexpected_success_fail(self):
        for index, count in ((-1, 4), (4, 4), (0, 0)):
            with self.subTest(index=index, count=count), self.assertRaises(ValueError):
                self.helper.run_shard(self.root, index, count, self.coordinates, stream=io.StringIO())
        self.write(self.names(1)[0], """
            import unittest
            class Example(unittest.TestCase):
                @unittest.expectedFailure
                def test_value(self): self.assertTrue(True)
        """)
        report = self.run_shard()
        self.assertFalse(report["successful"])
        with self.assertRaises(ValueError):
            self.aggregate([report], 1)

    def test_command_line_report_and_aggregate_use_real_terminal_results(self):
        (self.root / "tests/__init__.py").write_text("", encoding="utf-8")
        (self.root / "tests/fixture_value.py").write_text("VALUE = 3\n", encoding="utf-8")
        self.write(self.names(1)[0], """
            import unittest
            from tests.fixture_value import VALUE
            class Example(unittest.TestCase):
                def test_value(self): self.assertEqual(VALUE, 3)
        """)
        report_path = self.root / "shard-0.json"
        helper = self.helper.__file__
        coordinates = ["--source", "a" * 40, "--architecture", "b" * 40]
        ran = subprocess.run([sys.executable, helper, "run", "--root", str(self.root),
            "--index", "0", "--count", "1", "--report", str(report_path),
            "--input", str(self.root), *coordinates], cwd=self.root,
            capture_output=True, text=True)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        report = json.loads(report_path.read_text())
        self.assertEqual(report["results"][0]["status"], "passed")
        command = [sys.executable, helper, "aggregate", "--reports", str(self.root),
                   "--count", "1", *coordinates]
        accepted = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(json.loads(accepted.stdout)["tests"], 1)
        report["results"] = []
        report_path.write_text(json.dumps(report), encoding="utf-8")
        refused = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0)
