from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
import importlib
import unittest


IMPORT_SOURCE = """\
import package.module
import another.module as selected
from .helpers import run as execute
from ..shared import *
"""

CALL_SOURCE = """\
import package.module
from .helpers import run as execute

def local():
    return None

package.module.run()
execute()
local()
self.method()
"""

DYNAMIC_CALL_SOURCE = """\
build()()
registry["run"]()
(lambda: None)()
(first if condition else second)()
"""

PROSE_SOURCE = '''\
"""import requests and call subprocess.run("candidate")"""
# from os import system
VALUE = "open('candidate')"
'''


def _python_source_module():
    try:
        return importlib.import_module("python_source")
    except ModuleNotFoundError as error:
        if error.name != "python_source":
            raise
        return None


PYTHON_SOURCE = _python_source_module()


class PythonSourceFactTests(unittest.TestCase):
    def require_python_source(self):
        if PYTHON_SOURCE is None:
            self.fail("python source fact kernel is not implemented")
        return PYTHON_SOURCE

    def test_fixture_sources_are_valid_python(self) -> None:
        for source in (IMPORT_SOURCE, CALL_SOURCE, DYNAMIC_CALL_SOURCE, PROSE_SOURCE):
            with self.subTest(source=source.splitlines()[0]):
                ast.parse(source)

    def test_fact_language_is_closed_frozen_and_minimal(self) -> None:
        source = self.require_python_source()

        self.assertEqual(
            tuple(source.ImportKind),
            (source.ImportKind.IMPORT, source.ImportKind.FROM),
        )
        self.assertEqual(
            tuple(value.value for value in source.ImportKind),
            ("import", "from"),
        )
        self.assertEqual(
            tuple(source.UnresolvedCallKind),
            (
                source.UnresolvedCallKind.CALL_RESULT,
                source.UnresolvedCallKind.SUBSCRIPT,
                source.UnresolvedCallKind.LAMBDA,
                source.UnresolvedCallKind.OTHER,
            ),
        )
        self.assertEqual(
            tuple(value.value for value in source.UnresolvedCallKind),
            ("call_result", "subscript", "lambda", "other"),
        )

        location = source.SourceLocation("sample.py", 3, 4)
        with self.assertRaises(FrozenInstanceError):
            location.line = 5
        for value_type in (
            source.SourceLocation,
            source.AliasBinding,
            source.ImportFact,
            source.CallFact,
            source.UnresolvedCallFact,
            source.SourceAnalysis,
        ):
            with self.subTest(value_type=value_type.__name__):
                self.assertTrue(value_type.__dataclass_params__.frozen)
        self.assertEqual(
            tuple(field.name for field in fields(source.SourceLocation)),
            ("path", "line", "column"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(source.SourceAnalysis)),
            ("path", "module", "aliases", "imports", "calls", "unresolved_calls"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(source.AliasBinding)),
            ("local_name", "qualified_name"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(source.ImportFact)),
            ("kind", "module", "imported_name", "bound_name", "location"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(source.CallFact)),
            ("qualified_name", "location"),
        )
        self.assertEqual(
            tuple(field.name for field in fields(source.UnresolvedCallFact)),
            ("kind", "location"),
        )

    def test_import_forms_and_python_bindings_are_exact(self) -> None:
        source = self.require_python_source()

        analysis = source.analyze_source(
            IMPORT_SOURCE,
            path="package/imports.py",
            module="package.imports",
        )

        self.assertEqual(type(analysis), source.SourceAnalysis)
        self.assertEqual(
            tuple((value.local_name, value.qualified_name) for value in analysis.aliases),
            (
                ("execute", ".helpers.run"),
                ("package", "package"),
                ("selected", "another.module"),
            ),
        )
        self.assertEqual(
            tuple(
                (
                    type(value),
                    value.kind,
                    value.module,
                    value.imported_name,
                    value.bound_name,
                    value.qualified_name,
                    value.location,
                )
                for value in analysis.imports
            ),
            (
                (
                    source.ImportFact,
                    source.ImportKind.IMPORT,
                    "package.module",
                    None,
                    "package",
                    "package.module",
                    source.SourceLocation("package/imports.py", 1, 0),
                ),
                (
                    source.ImportFact,
                    source.ImportKind.IMPORT,
                    "another.module",
                    None,
                    "selected",
                    "another.module",
                    source.SourceLocation("package/imports.py", 2, 0),
                ),
                (
                    source.ImportFact,
                    source.ImportKind.FROM,
                    ".helpers",
                    "run",
                    "execute",
                    ".helpers.run",
                    source.SourceLocation("package/imports.py", 3, 0),
                ),
                (
                    source.ImportFact,
                    source.ImportKind.FROM,
                    "..shared",
                    "*",
                    "*",
                    "..shared.*",
                    source.SourceLocation("package/imports.py", 4, 0),
                ),
            ),
        )
        self.assertNotIn("*", {value.local_name for value in analysis.aliases})

    def test_alias_dotted_and_local_calls_resolve_without_inference(self) -> None:
        source = self.require_python_source()

        analysis = source.analyze_source(
            CALL_SOURCE,
            path="package/calls.py",
            module="package.calls",
        )

        self.assertEqual(
            tuple((type(value), value.qualified_name) for value in analysis.calls),
            (
                (source.CallFact, "package.module.run"),
                (source.CallFact, ".helpers.run"),
                (source.CallFact, "local"),
                (source.CallFact, "self.method"),
            ),
        )
        self.assertEqual(analysis.unresolved_calls, ())

    def test_every_call_is_resolved_or_explicitly_unresolved(self) -> None:
        source = self.require_python_source()

        analysis = source.analyze_source(
            DYNAMIC_CALL_SOURCE,
            path="dynamic.py",
            module="dynamic",
        )
        call_count = sum(
            isinstance(node, ast.Call) for node in ast.walk(ast.parse(DYNAMIC_CALL_SOURCE))
        )

        self.assertEqual(call_count, 5)
        self.assertEqual(len(analysis.calls) + len(analysis.unresolved_calls), call_count)
        self.assertEqual(
            tuple((type(value), value.qualified_name) for value in analysis.calls),
            ((source.CallFact, "build"),),
        )
        self.assertEqual(
            tuple(
                (type(value), value.kind, value.location)
                for value in analysis.unresolved_calls
            ),
            (
                (
                    source.UnresolvedCallFact,
                    source.UnresolvedCallKind.CALL_RESULT,
                    source.SourceLocation("dynamic.py", 1, 0),
                ),
                (
                    source.UnresolvedCallFact,
                    source.UnresolvedCallKind.SUBSCRIPT,
                    source.SourceLocation("dynamic.py", 2, 0),
                ),
                (
                    source.UnresolvedCallFact,
                    source.UnresolvedCallKind.LAMBDA,
                    source.SourceLocation("dynamic.py", 3, 0),
                ),
                (
                    source.UnresolvedCallFact,
                    source.UnresolvedCallKind.OTHER,
                    source.SourceLocation("dynamic.py", 4, 0),
                ),
            ),
        )

    def test_comments_strings_and_docstrings_are_not_facts(self) -> None:
        source = self.require_python_source()

        analysis = source.analyze_source(
            PROSE_SOURCE,
            path="prose.py",
            module="prose",
        )

        self.assertEqual(analysis.aliases, ())
        self.assertEqual(analysis.imports, ())
        self.assertEqual(analysis.calls, ())
        self.assertEqual(analysis.unresolved_calls, ())

    def test_locations_and_output_order_are_stable(self) -> None:
        source = self.require_python_source()
        document = "from zed import run\nfrom alpha import run as first\nfirst()\nrun()\n"

        first = source.analyze_source(document, path="ordered.py", module="ordered")
        second = source.analyze_source(document, path="ordered.py", module="ordered")

        self.assertEqual(first, second)
        self.assertEqual(
            tuple(value.location for value in first.imports),
            (
                source.SourceLocation("ordered.py", 1, 0),
                source.SourceLocation("ordered.py", 2, 0),
            ),
        )
        self.assertEqual(
            tuple((value.qualified_name, value.location) for value in first.calls),
            (
                ("alpha.run", source.SourceLocation("ordered.py", 3, 0)),
                ("zed.run", source.SourceLocation("ordered.py", 4, 0)),
            ),
        )

    def test_syntax_failure_is_bounded_and_does_not_retain_source(self) -> None:
        source = self.require_python_source()
        secret_source = "TOKEN = 'do-not-echo'\ndef broken(:\n"

        captured = None
        try:
            source.analyze_source(secret_source, path="broken.py", module="broken")
        except source.SourceAnalysisError as error:
            captured = error

        self.assertIsNotNone(captured)
        self.assertEqual(type(captured), source.SourceAnalysisError)
        self.assertEqual(str(captured), "invalid Python source at broken.py:2:11")
        self.assertIsNone(captured.__cause__)
        self.assertIsNone(captured.__context__)
        self.assertNotIn("do-not-echo", repr(captured))
        self.assertNotIn("def broken", repr(captured))

    def test_analysis_retains_no_source_or_literal_candidates(self) -> None:
        source = self.require_python_source()
        candidate = "sensitive-literal-candidate"

        analysis = source.analyze_source(
            f'emit("{candidate}")\n',
            path="safe.py",
            module="safe",
        )

        self.assertEqual(
            tuple(field.name for field in fields(type(analysis))),
            ("path", "module", "aliases", "imports", "calls", "unresolved_calls"),
        )
        self.assertNotIn(candidate, repr(analysis))
        self.assertNotIn("Constant", repr(analysis))

    def test_kernel_boundary_contains_no_policy_or_file_traversal_surface(self) -> None:
        source = self.require_python_source()

        for name in (
            "analyze_file",
            "evaluate_policies",
            "AstPolicy",
            "PolicyFinding",
            "ReferenceFact",
            "DecoratorFact",
            "FunctionFact",
            "ExceptHandlerFact",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(source, name))


if __name__ == "__main__":
    unittest.main()
