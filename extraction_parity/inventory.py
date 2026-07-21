"""Semantic inventory of tests collected from the frozen reference."""

from __future__ import annotations

import ast
import argparse
import inspect
import json
import os
import re
import textwrap
import unittest
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class InventoryError(ValueError):
    """Raised when a frozen test cannot receive one stable law identity."""


@dataclass(frozen=True)
class CollectedTest:
    reference: str
    law: str
    dimensions: tuple[str, ...]
    skip_reason: str | None
    collection_occurrences: int = 1


def canonical_reference(reference: str) -> str:
    if reference.startswith("tests."):
        return reference
    if reference.startswith("test_"):
        return f"tests.{reference}"
    raise InventoryError(f"collected test is outside the tests package: {reference}")


def semantic_law_id(
    reference: str,
    *,
    overrides: dict[str, str] | None = None,
    ambiguous_method_names: frozenset[str] = frozenset(),
) -> str:
    overrides = overrides or {}
    if reference in overrides:
        law = overrides[reference]
        if not re.fullmatch(r"behavior(?:\.[a-z0-9]+(?:-[a-z0-9]+)*)+", law):
            raise InventoryError(f"invalid law override for {reference}")
        return law
    method_name = reference.rsplit(".", 1)[-1]
    if not method_name.startswith("test_") or len(method_name) == len("test_"):
        raise InventoryError(f"reference is not a semantic test identity: {reference}")
    if method_name in ambiguous_method_names:
        raise InventoryError(f"ambiguous test name requires a semantic override: {reference}")
    return f"behavior.{method_name.removeprefix('test_').replace('_', '-')}"


def subtest_dimensions(source: str) -> tuple[str, ...]:
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError as error:
        raise InventoryError("test source cannot be parsed") from error
    return _subtest_dimensions_from_node(tree)


def _subtest_dimensions_from_node(node: ast.AST) -> tuple[str, ...]:
    names: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if not isinstance(child.func, ast.Attribute) or child.func.attr != "subTest":
            continue
        for keyword in child.keywords:
            if keyword.arg is None:
                raise InventoryError("subTest **kwargs dimensions are not statically inspectable")
            names.add(keyword.arg)
    return tuple(sorted(names))


def _method_dimensions(method: object, method_name: str) -> tuple[str, ...]:
    source_file = inspect.getsourcefile(method)
    if source_file is None:
        return ()
    tree = ast.parse(Path(source_file).read_text(encoding="utf-8"))
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name
    ]
    if len(matches) != 1:
        raise InventoryError(
            f"expected one source definition for {method_name}, found {len(matches)}"
        )
    return _subtest_dimensions_from_node(matches[0])


def validate_inventory(tests: tuple[CollectedTest, ...]) -> None:
    if not tests:
        raise InventoryError("test inventory cannot be empty")
    references = [test.reference for test in tests]
    laws = [test.law for test in tests]
    if len(references) != len(set(references)):
        raise InventoryError("test inventory contains duplicate references")
    if len(laws) != len(set(laws)):
        raise InventoryError("test inventory contains duplicate law identities")
    for test in tests:
        if not test.reference or not test.law:
            raise InventoryError("test reference and law must be non-empty")
        if test.skip_reason is not None and not test.skip_reason.strip():
            raise InventoryError("skipped tests require a non-empty rationale")
        if test.collection_occurrences <= 0:
            raise InventoryError("collection occurrences must be positive")


def inventory_descriptor(
    *, reference_tag: str, reference_commit: str, tests: tuple[CollectedTest, ...]
) -> dict[str, object]:
    if not reference_tag or not reference_commit:
        raise InventoryError("reference identity must be non-empty")
    validate_inventory(tests)
    entries = [
        {
            "reference": test.reference,
            "law": test.law,
            "dimensions": list(test.dimensions),
            "skip": None
            if test.skip_reason is None
            else {"reason": test.skip_reason},
            "collection_occurrences": test.collection_occurrences,
        }
        for test in sorted(tests, key=lambda item: item.reference)
    ]
    return {
        "schema": "cpk.reference-test-inventory",
        "reference": {"tag": reference_tag, "commit": reference_commit},
        "count": sum(test.collection_occurrences for test in tests),
        "law_count": len(entries),
        "skipped": sum(entry["skip"] is not None for entry in entries),
        "tests": entries,
    }


def _flatten(suite: unittest.TestSuite) -> Iterable[unittest.TestCase]:
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


def collect_tests(*, overrides: dict[str, str]) -> tuple[CollectedTest, ...]:
    suite = unittest.defaultTestLoader.discover("tests")
    cases = tuple(_flatten(suite))
    references = tuple(canonical_reference(case.id()) for case in cases)
    occurrences = Counter(references)
    unique_cases: dict[str, unittest.TestCase] = {}
    for case, reference in zip(cases, references, strict=True):
        unique_cases.setdefault(reference, case)
    method_names = tuple(reference.rsplit(".", 1)[-1] for reference in unique_cases)
    ambiguous = frozenset(name for name, count in Counter(method_names).items() if count > 1)
    unresolved = {
        name: sorted(
            reference
            for reference in unique_cases
            if reference.rsplit(".", 1)[-1] == name and reference not in overrides
        )
        for name in sorted(ambiguous)
    }
    unresolved = {name: references for name, references in unresolved.items() if references}
    if unresolved:
        raise InventoryError(f"ambiguous tests require semantic overrides: {unresolved}")
    collected: list[CollectedTest] = []
    for reference, case in unique_cases.items():
        method_name = reference.rsplit(".", 1)[-1]
        method = getattr(type(case), method_name)
        skipped = bool(getattr(method, "__unittest_skip__", False))
        reason = str(getattr(method, "__unittest_skip_why__", "")) if skipped else None
        collected.append(
            CollectedTest(
                reference=reference,
                law=semantic_law_id(
                    reference,
                    overrides=overrides,
                    ambiguous_method_names=ambiguous,
                ),
                dimensions=_method_dimensions(method, method_name),
                skip_reason=reason,
                collection_occurrences=occurrences[reference],
            )
        )
    result = tuple(collected)
    validate_inventory(result)
    return result


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-tag", required=True)
    parser.add_argument("--reference-commit", required=True)
    parser.add_argument("--overrides", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    overrides = json.loads(args.overrides.read_text(encoding="utf-8"))
    if not isinstance(overrides, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in overrides.items()
    ):
        raise InventoryError("law overrides must be a string-to-string object")
    descriptor = inventory_descriptor(
        reference_tag=args.reference_tag,
        reference_commit=args.reference_commit,
        tests=collect_tests(overrides=overrides),
    )
    _write_json(args.output, descriptor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
