from __future__ import annotations

import ast
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
import re
import tomllib
import unittest

from control_plane_kit_operations.gateway_key_rotations import (
    GatewayKeyRotationDeploymentPhase,
    GatewayKeyRotationStatus,
    _LEGAL,
)
from control_plane_kit_operations.postgres.current_schema_contract import (
    CURRENT_POSTGRES_SCHEMA_CONTRACT,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PACKAGE_ROOT / "src" / "control_plane_kit_operations"
POSTGRES_ROOT = SOURCE_ROOT / "postgres"
INVENTORY_PATH = PACKAGE_ROOT / "POSTGRES_READ_CARDINALITY.toml"

_CATEGORIES = frozenset(
    {
        "public-paged",
        "public-unbounded",
        "fixed-cardinality",
        "closed-finite",
        "internal-complete",
        "exact-verifier",
    }
)
_CONSUMER_KINDS = frozenset({"production", "test-only"})
_CATEGORY_COUNTS = {
    "public-paged": 14,
    "fixed-cardinality": 1,
    "closed-finite": 2,
    "internal-complete": 22,
    "exact-verifier": 12,
}
_GENERIC_CONSUMERS = frozenset({"internal", "module", "test", "tests"})
_MODULE = re.compile(r"^control_plane_kit_operations\.postgres(?:\.[a-z][a-z0-9_]*)+$")
_SELECTOR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")


@dataclass(frozen=True, order=True)
class ReadIdentity:
    module: str
    selector: str
    occurrence: int | None = None


class _FetchallVisitor(ast.NodeVisitor):
    def __init__(self, module: str) -> None:
        self.module = module
        self.scope: list[str] = []
        self.calls: list[tuple[str, int]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scope(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scope(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        if any(_is_fetchall(call) for call in ast.walk(node) if isinstance(call, ast.Call)):
            raise AssertionError(f"fetchall cannot be owned by lambda in {self.module}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if _is_fetchall(node):
            if not self.scope:
                raise AssertionError(
                    f"fetchall must have a named selector owner in {self.module}"
                )
            self.calls.append((".".join(self.scope), node.lineno))
        self.generic_visit(node)

    def _visit_scope(
        self,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()


def _is_fetchall(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Attribute) and node.func.attr == "fetchall"


def _discover() -> tuple[ReadIdentity, ...]:
    raw: list[tuple[str, str, int]] = []
    for path in sorted(POSTGRES_ROOT.rglob("*.py")):
        relative = path.relative_to(SOURCE_ROOT).with_suffix("")
        module = "control_plane_kit_operations." + ".".join(relative.parts)
        visitor = _FetchallVisitor(module)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        raw.extend((module, selector, line) for selector, line in visitor.calls)

    grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
    for module, selector, line in raw:
        grouped[(module, selector)].append(line)

    identities = []
    for module, selector, line in raw:
        ordered_lines = sorted(grouped[(module, selector)])
        occurrence = (
            ordered_lines.index(line) + 1 if len(ordered_lines) > 1 else None
        )
        identities.append(ReadIdentity(module, selector, occurrence))
    return tuple(sorted(identities))


def _parse_inventory(text: str) -> tuple[dict[str, object], ...]:
    parsed = tomllib.loads(text)
    if (
        set(parsed) != {"version", "read"}
        or type(parsed["version"]) is not int
        or parsed["version"] != 1
    ):
        raise AssertionError("read inventory header is malformed")
    rows = parsed["read"]
    if not isinstance(rows, list):
        raise AssertionError("read inventory rows are malformed")

    normalized: list[dict[str, object]] = []
    identities: set[ReadIdentity] = set()
    occurrences: dict[tuple[str, str], list[int | None]] = defaultdict(list)
    required = {"module", "selector", "category", "sql", "consumer_kind", "consumer"}
    for raw in rows:
        if not isinstance(raw, dict):
            raise AssertionError("read inventory row is malformed")
        allowed = required | {"occurrence"}
        if set(raw) - allowed or not required <= set(raw):
            raise AssertionError("read inventory row keys are malformed")
        module = raw["module"]
        selector = raw["selector"]
        if not isinstance(module, str) or _MODULE.fullmatch(module) is None:
            raise AssertionError("read inventory module is malformed")
        if not isinstance(selector, str) or _SELECTOR.fullmatch(selector) is None:
            raise AssertionError("read inventory selector is malformed")
        category = raw["category"]
        if not isinstance(category, str) or category not in _CATEGORIES:
            raise AssertionError("read inventory category is unsupported")
        for field in ("sql", "consumer"):
            value = raw[field]
            if not isinstance(value, str) or not value.strip():
                raise AssertionError(f"read inventory {field} is empty")
        consumer_kind = raw["consumer_kind"]
        if not isinstance(consumer_kind, str) or consumer_kind not in _CONSUMER_KINDS:
            raise AssertionError("read inventory consumer kind is unsupported")
        if str(raw["consumer"]).strip().lower() in _GENERIC_CONSUMERS:
            raise AssertionError("read inventory consumer is not concrete")
        occurrence = raw.get("occurrence")
        if occurrence is not None and (
            type(occurrence) is not int or occurrence <= 0
        ):
            raise AssertionError("read inventory occurrence is malformed")
        identity = ReadIdentity(module, selector, occurrence)
        if identity in identities:
            raise AssertionError("read inventory identity is duplicated")
        identities.add(identity)
        occurrences[(module, selector)].append(occurrence)
        normalized.append(dict(raw))

    for values in occurrences.values():
        if len(values) == 1:
            if values != [None]:
                raise AssertionError("unique selector must omit occurrence")
            continue
        if None in values or sorted(values) != list(range(1, len(values) + 1)):
            raise AssertionError("repeated selector occurrences must be contiguous")
    return tuple(normalized)


def _row(
    *,
    module: str = "control_plane_kit_operations.postgres.example",
    selector: str = "ExampleStore.list_values",
    occurrence: int | None = None,
    category: str = "internal-complete",
    consumer_kind: str = "production",
    consumer: str = "ExampleCoordinator reconstructs all admitted values",
) -> str:
    occurrence_line = "" if occurrence is None else f"occurrence = {occurrence}\n"
    return f'''\n[[read]]
module = "{module}"
selector = "{selector}"
{occurrence_line}category = "{category}"
sql = "workspace filter; identity ascending; intentionally complete"
consumer_kind = "{consumer_kind}"
consumer = "{consumer}"
'''


class PostgresReadCardinalityPolicyTests(unittest.TestCase):
    def test_ast_discovery_has_stable_named_occurrence_identities(self) -> None:
        identities = _discover()

        self.assertEqual(len(identities), 51)
        self.assertEqual(len(set(identities)), 51)
        grouped = defaultdict(list)
        for identity in identities:
            self.assertNotRegex(identity.module, r":\d+$")
            self.assertNotRegex(identity.selector, r":\d+$")
            grouped[(identity.module, identity.selector)].append(identity.occurrence)
        repeated = {key: values for key, values in grouped.items() if len(values) > 1}
        self.assertEqual(len(repeated), 2)
        self.assertEqual(set(tuple(values) for values in repeated.values()), {(1, 2)})
        self.assertEqual(
            tuple(
                identity
                for identity in identities
                if identity.module
                == "control_plane_kit_operations.postgres.effect_outcome_store"
            ),
            (
                ReadIdentity(
                    "control_plane_kit_operations.postgres.effect_outcome_store",
                    "EffectAttemptOutcomeStore.get",
                ),
                ReadIdentity(
                    "control_plane_kit_operations.postgres.effect_outcome_store",
                    "_validate_current_rows",
                    1,
                ),
                ReadIdentity(
                    "control_plane_kit_operations.postgres.effect_outcome_store",
                    "_validate_current_rows",
                    2,
                ),
            ),
        )
        self.assertEqual(
            tuple(
                identity
                for identity in identities
                if identity.module
                == "control_plane_kit_operations.postgres.effect_attempt_intent_store"
            ),
            (
                ReadIdentity(
                    "control_plane_kit_operations.postgres.effect_attempt_intent_store",
                    "_validate_current_rows",
                ),
            ),
        )
        for key, values in grouped.items():
            if key not in repeated:
                self.assertEqual(values, [None])

    def test_inventory_parser_closes_shape_identity_and_consumer_kind(self) -> None:
        valid = (
            "version = 1\n"
            + _row(
                selector="ExampleStore.first_query",
                occurrence=1,
                consumer_kind="test-only",
                consumer="test_postgres_example exact store ordering law",
            )
            + _row(
                selector="ExampleStore.first_query",
                occurrence=2,
                consumer_kind="test-only",
                consumer="test_postgres_example exact store ordering law",
            )
            + _row(selector="ExampleStore.second_query")
        )
        self.assertEqual(len(_parse_inventory(valid)), 3)

        invalid = (
            valid.replace("version = 1", "version = true", 1),
            valid.replace('category = "internal-complete"', 'category = "unknown"', 1),
            valid.replace('category = "internal-complete"', "category = []", 1),
            valid.replace('consumer_kind = "test-only"', 'consumer_kind = "maybe"', 1),
            valid.replace('consumer_kind = "test-only"', "consumer_kind = {}", 1),
            valid.replace(
                'consumer = "test_postgres_example exact store ordering law"',
                'consumer = "tests"',
                1,
            ),
            valid.replace("occurrence = 2", "occurrence = 3", 1),
            valid.replace("occurrence = 1", "occurrence = 0", 1),
            valid.replace("selector = \"ExampleStore.second_query\"", "selector = \"<lambda>\"", 1),
            valid + _row(selector="ExampleStore.second_query"),
        )
        for witness in invalid:
            with self.subTest(witness=witness[-120:]):
                with self.assertRaises(AssertionError):
                    _parse_inventory(witness)

    def test_closed_finite_reads_have_executable_finite_bounds(self) -> None:
        terminal = {
            GatewayKeyRotationStatus.COMPLETED,
            GatewayKeyRotationStatus.BLOCKED,
            GatewayKeyRotationStatus.REJECTED,
        }
        self.assertEqual(set(GatewayKeyRotationStatus), set(_LEGAL) | terminal)

        def reachable(status: GatewayKeyRotationStatus, path: frozenset[object]) -> set[object]:
            self.assertNotIn(status, path)
            if status in terminal:
                return {status}
            destinations = _LEGAL[status]
            self.assertTrue(destinations)
            result: set[object] = set()
            for destination in destinations:
                result.update(reachable(destination, path | {status}))
            return result

        for status in GatewayKeyRotationStatus:
            self.assertTrue(reachable(status, frozenset()) <= terminal)

        add_transition_calls: list[tuple[str, int]] = []
        transition_calls: list[tuple[str, int]] = []
        legal_guard_scopes: list[str] = []

        class RotationWriterVisitor(ast.NodeVisitor):
            def __init__(self, module: str) -> None:
                self.module = module
                self.scope: list[str] = []

            def qualified_scope(self) -> str:
                return ".".join((self.module, *self.scope))

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                self.scope.append(node.name)
                self.generic_visit(node)
                self.scope.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self.scope.append(node.name)
                self.generic_visit(node)
                self.scope.pop()

            def visit_Call(self, node: ast.Call) -> None:
                if isinstance(node.func, ast.Attribute) and node.func.attr == "add_transition":
                    add_transition_calls.append((self.qualified_scope(), node.lineno))
                if isinstance(node.func, ast.Name) and node.func.id == "_transition":
                    transition_calls.append((self.qualified_scope(), node.lineno))
                self.generic_visit(node)

            def visit_If(self, node: ast.If) -> None:
                if any(
                    isinstance(value, ast.Name) and value.id == "_LEGAL"
                    for value in ast.walk(node.test)
                ) and any(
                    isinstance(value, ast.Raise) for value in ast.walk(node)
                ):
                    legal_guard_scopes.append(self.qualified_scope())
                self.generic_visit(node)

        for path in sorted(SOURCE_ROOT.rglob("*.py")):
            relative = path.relative_to(SOURCE_ROOT).with_suffix("")
            module = "control_plane_kit_operations." + ".".join(relative.parts)
            RotationWriterVisitor(module).visit(
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            )

        advance_locked_scope = (
            "control_plane_kit_operations.gateway_key_rotations."
            "GatewayKeyRotationService._advance_locked"
        )
        expected_rotation_writers = [advance_locked_scope]
        self.assertEqual(
            [scope for scope, _line in add_transition_calls],
            expected_rotation_writers,
        )
        self.assertEqual(
            [scope for scope, _line in transition_calls],
            expected_rotation_writers,
        )
        self.assertEqual(
            legal_guard_scopes,
            ["control_plane_kit_operations.gateway_key_rotations._transition"],
        )
        for transition_call, add_transition_call in zip(
            transition_calls,
            add_transition_calls,
            strict=True,
        ):
            self.assertLess(transition_call[1], add_transition_call[1])

        constraints = {
            constraint.name: constraint
            for constraint in CURRENT_POSTGRES_SCHEMA_CONTRACT.constraints
        }
        transition_unique = constraints[
            "cpk_gateway_key_rotation_transitions_rotation_id_to_version_key"
        ]
        transition_version = constraints[
            "cpk_gateway_key_rotation_transitions_version_check"
        ]
        deployment_phase = constraints[
            "cpk_gateway_key_rotation_deployments_phase_check"
        ]
        deployment_primary = constraints[
            "cpk_gateway_key_rotation_deployments_pkey"
        ]
        self.assertEqual(transition_unique.kind, "u")
        self.assertEqual(transition_unique.local_columns, ("rotation_id", "to_version"))
        self.assertIn("to_version = (from_version + 1)", transition_version.check_expression)
        self.assertEqual(
            {phase.value for phase in GatewayKeyRotationDeploymentPhase},
            {"overlap", "retirement"},
        )
        self.assertIn("'overlap'", deployment_phase.check_expression)
        self.assertIn("'retirement'", deployment_phase.check_expression)
        self.assertEqual(deployment_primary.kind, "p")
        self.assertEqual(deployment_primary.local_columns, ("rotation_id", "phase"))

    def test_canonical_inventory_exactly_classifies_every_discovered_read(self) -> None:
        if not INVENTORY_PATH.is_file():
            self.fail("POSTGRES_READ_CARDINALITY.toml is missing")
        rows = _parse_inventory(INVENTORY_PATH.read_text(encoding="utf-8"))
        inventory = {
            ReadIdentity(
                str(row["module"]),
                str(row["selector"]),
                row.get("occurrence"),
            )
            for row in rows
        }

        self.assertEqual(inventory, set(_discover()))
        self.assertEqual(Counter(str(row["category"]) for row in rows), _CATEGORY_COUNTS)


if __name__ == "__main__":
    unittest.main()
