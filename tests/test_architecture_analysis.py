from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tempfile
import unittest

from tests.architecture import (
    ExpressionShape,
    PolicyFinding,
    SourceAnalysisError,
    SourceFacts,
    analyze_file,
    analyze_source,
    evaluate_policies,
)


class ArchitectureAnalysisTests(unittest.TestCase):
    def test_call_keyword_shapes_are_recorded_without_values(self) -> None:
        facts = analyze_source(
            "build(mapping={'secret': 'not-retained'}, values=(item,), source=config)\n",
            path="sample.py",
            module="sample",
        )

        arguments = {
            value.name: value.shape for value in facts.calls[0].keyword_arguments
        }

        self.assertEqual(
            arguments,
            {
                "mapping": ExpressionShape.DICTIONARY,
                "source": ExpressionShape.NAME,
                "values": ExpressionShape.TUPLE,
            },
        )
        mapping = next(value for value in facts.calls[0].keyword_arguments if value.name == "mapping")
        self.assertEqual(mapping.literal_mapping_keys, ("secret",))

    def test_import_aliases_calls_decorators_and_locations_are_resolved(self) -> None:
        facts = analyze_source(
            '''"""requests in prose is not an import."""
import os as operating_system
from unittest import skipUnless as optional

@optional(True, "available")
def read_value():
    return operating_system.environ.get("VALUE")
''',
            path="sample.py",
            module="sample",
        )

        self.assertEqual(
            tuple(value.qualified_name for value in facts.imports),
            ("os", "unittest.skipUnless"),
        )
        self.assertIn("os.environ.get", tuple(value.qualified_name for value in facts.calls))
        self.assertEqual(
            tuple(value.qualified_name for value in facts.decorators),
            ("unittest.skipUnless",),
        )
        self.assertEqual(facts.functions[0].qualified_name, "read_value")
        self.assertEqual(facts.functions[0].location.line, 6)

    def test_relative_import_and_nested_method_names_are_preserved(self) -> None:
        facts = analyze_source(
            "from .helpers import run as execute\n"
            "class Service:\n"
            "    def call(self):\n"
            "        return execute()\n",
            path="package/service.py",
            module="package.service",
        )

        self.assertEqual(facts.imports[0].qualified_name, ".helpers.run")
        self.assertEqual(facts.calls[0].qualified_name, ".helpers.run")
        self.assertEqual(facts.functions[0].qualified_name, "Service.call")
        self.assertEqual(facts.classes[0].qualified_name, "Service")

    def test_nested_class_names_and_decorators_are_explicit(self) -> None:
        facts = analyze_source(
            "from dataclasses import dataclass as product\n"
            "@product(frozen=True)\n"
            "class Outer:\n"
            "    class Inner:\n"
            "        pass\n",
            path="classes.py",
            module="classes",
        )

        self.assertEqual(
            tuple(value.qualified_name for value in facts.classes),
            ("Outer", "Outer.Inner"),
        )
        self.assertEqual(
            facts.classes[0].decorators[0].qualified_name,
            "dataclasses.dataclass",
        )

    def test_dotted_import_binding_matches_python_semantics(self) -> None:
        facts = analyze_source(
            "import package.module\n"
            "import another.module as selected\n"
            "package.module.run()\n"
            "selected.run()\n",
            path="imports.py",
            module="imports",
        )

        aliases = {value.local_name: value.qualified_name for value in facts.aliases}
        self.assertEqual(aliases["package"], "package")
        self.assertEqual(aliases["selected"], "another.module")
        self.assertEqual(
            tuple(value.qualified_name for value in facts.calls),
            ("another.module.run", "package.module.run"),
        )

    def test_comments_strings_and_docstrings_do_not_become_code_facts(self) -> None:
        facts = analyze_source(
            '''"""import requests and call subprocess.run()"""
# import httpx
VALUE = "os.environ"
''',
            path="prose.py",
            module="prose",
        )

        self.assertEqual(facts.imports, ())
        self.assertEqual(facts.calls, ())
        self.assertFalse(any("requests" in value.qualified_name for value in facts.references))

    def test_literal_all_exports_resolve_imported_and_local_provenance(self) -> None:
        facts = analyze_source(
            "from package.service import Service as PublicService\n"
            "class LocalValue:\n"
            "    pass\n"
            "__all__ = ('PublicService',)\n"
            "__all__ += ['LocalValue']\n",
            path="package/__init__.py",
            module="package",
        )

        self.assertEqual(
            tuple((value.name, value.qualified_name) for value in facts.exports),
            (
                ("LocalValue", "package.LocalValue"),
                ("PublicService", "package.service.Service"),
            ),
        )

    def test_prose_does_not_become_export_provenance(self) -> None:
        facts = analyze_source(
            '"""__all__ = ["Hidden"]"""\n# __all__ = ["Commented"]\n',
            path="package/__init__.py",
            module="package",
        )

        self.assertEqual(facts.exports, ())
        self.assertEqual(facts.unsupported_exports, ())

    def test_computed_all_is_explicitly_unsupported(self) -> None:
        facts = analyze_source(
            "PUBLIC = ('Value',)\n__all__ += PUBLIC\n",
            path="package/__init__.py",
            module="package",
        )

        self.assertEqual(facts.exports, ())
        self.assertEqual(len(facts.unsupported_exports), 1)

    def test_malformed_source_fails_without_echoing_source(self) -> None:
        secret_source = "TOKEN = 'do-not-echo'\ndef broken(:\n"
        with self.assertRaises(SourceAnalysisError) as raised:
            analyze_source(secret_source, path="broken.py", module="broken")

        self.assertIn("broken.py", str(raised.exception))
        self.assertNotIn("do-not-echo", str(raised.exception))

    def test_file_analysis_derives_module_and_rejects_files_outside_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "sample"
            package.mkdir()
            source = package / "module.py"
            source.write_text("import os\n", encoding="utf-8")

            facts = analyze_file(source, root=root)

            self.assertEqual(facts.path, "sample/module.py")
            self.assertEqual(facts.module, "sample.module")
            with self.assertRaises(SourceAnalysisError):
                analyze_file(Path(__file__), root=root)

    def test_policy_evaluation_is_deterministic_and_location_backed(self) -> None:
        first = analyze_source("import os\n", path="b.py", module="b")
        second = analyze_source("import sys\n", path="a.py", module="a")

        @dataclass(frozen=True)
        class RejectImports:
            def evaluate(self, facts: SourceFacts) -> tuple[PolicyFinding, ...]:
                return tuple(
                    PolicyFinding(
                        "no-imports",
                        f"{facts.module} imports {value.qualified_name}",
                        value.location,
                    )
                    for value in facts.imports
                )

        findings = evaluate_policies((first, second), (RejectImports(),))

        self.assertEqual(tuple(value.location.path for value in findings), ("a.py", "b.py"))

    def test_pass_only_functions_and_exception_handlers_are_explicit(self) -> None:
        facts = analyze_source(
            "def empty():\n"
            "    pass\n"
            "try:\n"
            "    work()\n"
            "except RuntimeError:\n"
            "    pass\n",
            path="empty.py",
            module="empty",
        )

        self.assertTrue(facts.functions[0].pass_only)
        self.assertTrue(facts.except_handlers[0].pass_only)
        self.assertEqual(facts.except_handlers[0].exception_name, "RuntimeError")

    def test_boolean_arguments_assertions_and_empty_test_bodies_are_explicit(self) -> None:
        facts = analyze_source(
            "class Tests:\n"
            "    def test_placeholder(self):\n"
            "        self.assertTrue(True)\n"
            "        assert True\n"
            "    def test_empty(self):\n"
            "        'documentation only'\n",
            path="test_placeholder.py",
            module="test_placeholder",
        )

        placeholder = next(
            value for value in facts.calls if value.qualified_name == "self.assertTrue"
        )
        self.assertEqual(placeholder.boolean_arguments[0].value, True)
        self.assertEqual(facts.boolean_assertions[0].value, True)
        empty = next(value for value in facts.functions if value.qualified_name.endswith("test_empty"))
        self.assertTrue(empty.empty_body)

    def test_bare_and_named_exception_handlers_have_total_ordering(self) -> None:
        facts = analyze_source(
            "try:\n"
            "    first()\n"
            "except RuntimeError:\n"
            "    recover()\n"
            "try:\n"
            "    second()\n"
            "except:\n"
            "    recover()\n",
            path="handlers.py",
            module="handlers",
        )

        self.assertEqual(
            tuple(value.exception_name for value in facts.except_handlers),
            ("RuntimeError", None),
        )


if __name__ == "__main__":
    unittest.main()
