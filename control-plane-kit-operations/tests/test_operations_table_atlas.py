from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import sys
import types
import unittest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ATLAS_PATH = PACKAGE_ROOT / "OPERATIONS_TABLE_ATLAS.md"
CONTRACT_PATH = (
    PACKAGE_ROOT
    / "src"
    / "control_plane_kit_operations"
    / "postgres"
    / "current_schema_contract.py"
)

_REQUIRED_TABLE_FIELDS = (
    "Durable meaning and owner",
    "Identity and cardinality",
    "Outgoing foreign keys",
    "Inbound dependents",
    "Writers and transactions",
    "Readers and projections",
    "Mutation, locks, retries, and idempotency",
    "Lifecycle, retention, deletion, and restore",
    "JSON boundary",
    "Sensitive material",
    "Future impact",
)
_RETIRED_SCHEMA_SYMBOLS = (
    "cpk_schema_migrations",
    "POSTGRES_SCHEMA_MIGRATIONS",
    "SchemaMigrationPlan",
    "SqlMigrationStep",
    "schema-migration-program",
)
_FUTURE_ISSUES = ("1553", "1554", "1555", "1556", "1243", "1244")

_HEADER_PATTERN = re.compile(
    r"<!-- current-schema-contract: "
    r"sha256=(?P<sha256>[0-9a-f]{64}) "
    r"relations=(?P<relations>[0-9]+) "
    r"columns=(?P<columns>[0-9]+) "
    r"constraints=(?P<constraints>[0-9]+) "
    r"indexes=(?P<indexes>[0-9]+) "
    r"foreign-keys=(?P<foreign_keys>[0-9]+) -->"
)
_TABLE_HEADING_PATTERN = re.compile(r"^### `(cpk_[a-z0-9_]+)`$", re.MULTILINE)
_FIELD_PATTERN = re.compile(r"^- \*\*(?P<label>[^*]+):\*\*\s+(?P<value>.+)$")
_FK_ROW_PATTERN = re.compile(
    r"^\| `(?P<name>cpk_[a-z0-9_]+)` "
    r"\| `(?P<local_relation>cpk_[a-z0-9_]+)` "
    r"\| `(?P<local_columns>[a-z0-9_, ]+)` "
    r"\| `(?P<referenced_relation>cpk_[a-z0-9_]+)` "
    r"\| `(?P<referenced_columns>[a-z0-9_, ]+)` "
    r"\| (?P<meaning>[^|]+) \|$"
)
_GRAPH_EDGE_PATTERN = re.compile(
    r"^(?P<local_relation>cpk_[a-z0-9_]+) "
    r"-->\|(?P<name>cpk_[a-z0-9_]+)\| "
    r"(?P<referenced_relation>cpk_[a-z0-9_]+)$"
)

_PARSER_WITNESS = """\
<!-- current-schema-contract: sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa relations=1 columns=2 constraints=3 indexes=4 foreign-keys=1 -->
<!-- foreign-key-graph:start -->
```mermaid
cpk_child -->|cpk_child_parent_fk| cpk_parent
```
<!-- foreign-key-graph:end -->
<!-- foreign-key-ledger:start -->
| `cpk_child_parent_fk` | `cpk_child` | `parent_id` | `cpk_parent` | `parent_id` | Proves exact parent ownership. |
<!-- foreign-key-ledger:end -->
<!-- multi-table-scc: cpk_child,cpk_parent -->
<!-- self-reference: cpk_child -->
<!-- future-impact: 1553,1554,1555,1556,1243,1244 -->
### `cpk_child`
- **Durable meaning and owner:** Witness child truth.
- **Identity and cardinality:** One child identity.
- **Outgoing foreign keys:** Exact parent ownership.
- **Inbound dependents:** None.
- **Writers and transactions:** One caller-owned transaction.
- **Readers and projections:** Internal witness reader.
- **Mutation, locks, retries, and idempotency:** Immutable and idempotent.
- **Lifecycle, retention, deletion, and restore:** Parent before child.
- **JSON boundary:** None.
- **Sensitive material:** None.
- **Future impact:** None.
"""


def _load_contract_module() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_operations_table_atlas_current_schema_contract",
        CONTRACT_PATH,
    )
    if spec is None or spec.loader is None:
        raise AssertionError("current schema contract is not importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_CONTRACT_MODULE = _load_contract_module()
_CONTRACT = _CONTRACT_MODULE.CURRENT_POSTGRES_SCHEMA_CONTRACT
_CONTRACT_SHA256 = _CONTRACT_MODULE.CURRENT_POSTGRES_SCHEMA_CONTRACT_SHA256


def _between(text: str, start: str, end: str) -> str:
    try:
        return text.split(start, 1)[1].split(end, 1)[0]
    except IndexError as error:
        raise AssertionError(f"atlas is missing structured boundary {start}") from error


def _parse_header(text: str) -> dict[str, str | int]:
    match = _HEADER_PATTERN.search(text)
    if match is None:
        raise AssertionError("atlas is missing the exact current-contract header")
    values: dict[str, str | int] = {"sha256": match.group("sha256")}
    for name in ("relations", "columns", "constraints", "indexes", "foreign_keys"):
        values[name] = int(match.group(name))
    return values


def _parse_table_sections(text: str) -> dict[str, dict[str, str]]:
    matches = tuple(_TABLE_HEADING_PATTERN.finditer(text))
    sections: dict[str, dict[str, str]] = {}
    for index, match in enumerate(matches):
        name = match.group(1)
        if name in sections:
            raise AssertionError(f"atlas table section is duplicated: {name}")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        fields: dict[str, str] = {}
        for line in text[match.end() : end].splitlines():
            field_match = _FIELD_PATTERN.fullmatch(line)
            if field_match is not None:
                label = field_match.group("label")
                if label in fields:
                    raise AssertionError(
                        f"atlas table field is duplicated: {name}.{label}"
                    )
                fields[label] = field_match.group("value").strip()
        sections[name] = fields
    return sections


def _columns(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _parse_fk_ledger(
    text: str,
) -> tuple[tuple[str, str, tuple[str, ...], str, tuple[str, ...]], ...]:
    body = _between(
        text,
        "<!-- foreign-key-ledger:start -->",
        "<!-- foreign-key-ledger:end -->",
    )
    rows = []
    for line in body.splitlines():
        match = _FK_ROW_PATTERN.fullmatch(line)
        if match is None:
            continue
        if not match.group("meaning").strip():
            raise AssertionError("atlas foreign-key meaning must not be empty")
        rows.append(
            (
                match.group("name"),
                match.group("local_relation"),
                _columns(match.group("local_columns")),
                match.group("referenced_relation"),
                _columns(match.group("referenced_columns")),
            )
        )
    return tuple(rows)


def _parse_graph_edges(text: str) -> tuple[tuple[str, str, str], ...]:
    body = _between(
        text,
        "<!-- foreign-key-graph:start -->",
        "<!-- foreign-key-graph:end -->",
    )
    edges = []
    for line in body.splitlines():
        stripped = line.strip()
        match = _GRAPH_EDGE_PATTERN.fullmatch(stripped)
        if match is not None:
            edges.append(
                (
                    match.group("name"),
                    match.group("local_relation"),
                    match.group("referenced_relation"),
                )
            )
        elif "-->" in stripped:
            raise AssertionError(f"atlas graph edge is malformed: {stripped}")
    return tuple(edges)


def _parse_marker(text: str, name: str) -> tuple[str, ...]:
    match = re.search(rf"<!-- {re.escape(name)}: ([a-z0-9_,]+) -->", text)
    if match is None:
        raise AssertionError(f"atlas is missing {name} declaration")
    return tuple(part for part in match.group(1).split(",") if part)


def _schema_graph() -> tuple[dict[str, set[str]], set[str]]:
    relations = {relation.name for relation in _CONTRACT.relations}
    graph = {relation: set() for relation in relations}
    self_references: set[str] = set()
    for constraint in _CONTRACT.constraints:
        if constraint.kind != "f":
            continue
        referenced = constraint.referenced_relation
        if referenced is None:
            raise AssertionError("foreign key is missing referenced relation")
        graph[constraint.relation].add(referenced)
        if constraint.relation == referenced:
            self_references.add(constraint.relation)
    return graph, self_references


def _multi_table_sccs(graph: dict[str, set[str]]) -> tuple[tuple[str, ...], ...]:
    index = 0
    stack: list[str] = []
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    on_stack: set[str] = set()
    components: list[tuple[str, ...]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for adjacent in sorted(graph[node]):
            if adjacent not in indices:
                visit(adjacent)
                lowlinks[node] = min(lowlinks[node], lowlinks[adjacent])
            elif adjacent in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[adjacent])
        if lowlinks[node] != indices[node]:
            return
        component = []
        while True:
            adjacent = stack.pop()
            on_stack.remove(adjacent)
            component.append(adjacent)
            if adjacent == node:
                break
        if len(component) > 1:
            components.append(tuple(sorted(component)))

    for relation in sorted(graph):
        if relation not in indices:
            visit(relation)
    return tuple(sorted(components))


class OperationsTableAtlasTests(unittest.TestCase):
    def _atlas_text(self) -> str:
        self.assertTrue(
            ATLAS_PATH.is_file(),
            "operations table atlas is not published",
        )
        return ATLAS_PATH.read_text(encoding="utf-8")

    def test_package_local_atlas_pins_the_current_contract(self) -> None:
        self.assertEqual(_parse_header(_PARSER_WITNESS)["relations"], 1)
        text = self._atlas_text()
        foreign_keys = tuple(
            constraint for constraint in _CONTRACT.constraints if constraint.kind == "f"
        )

        self.assertEqual(
            _parse_header(text),
            {
                "sha256": _CONTRACT_SHA256,
                "relations": len(_CONTRACT.relations),
                "columns": len(_CONTRACT.columns),
                "constraints": len(_CONTRACT.constraints),
                "indexes": len(_CONTRACT.indexes),
                "foreign_keys": len(foreign_keys),
            },
        )

    def test_each_current_table_has_one_complete_semantic_section(self) -> None:
        self.assertEqual(tuple(_parse_table_sections(_PARSER_WITNESS)), ("cpk_child",))
        text = self._atlas_text()
        sections = _parse_table_sections(text)
        expected_relations = tuple(relation.name for relation in _CONTRACT.relations)

        self.assertEqual(tuple(sections), expected_relations)
        for relation, fields in sections.items():
            with self.subTest(relation=relation):
                self.assertEqual(tuple(fields), _REQUIRED_TABLE_FIELDS)
                self.assertTrue(all(fields.values()))

    def test_foreign_key_ledger_is_exact_and_explained(self) -> None:
        self.assertEqual(len(_parse_fk_ledger(_PARSER_WITNESS)), 1)
        text = self._atlas_text()
        expected = tuple(
            (
                constraint.name,
                constraint.relation,
                constraint.local_columns,
                constraint.referenced_relation,
                constraint.referenced_columns,
            )
            for constraint in _CONTRACT.constraints
            if constraint.kind == "f"
        )
        observed = _parse_fk_ledger(text)

        self.assertEqual(observed, expected)
        self.assertEqual(len(observed), len(set(observed)))

    def test_dependency_graph_and_scc_declarations_match_current_truth(self) -> None:
        self.assertEqual(len(_parse_graph_edges(_PARSER_WITNESS)), 1)
        self.assertEqual(
            _parse_marker(_PARSER_WITNESS, "multi-table-scc"),
            ("cpk_child", "cpk_parent"),
        )
        text = self._atlas_text()
        expected_edges = tuple(
            (
                constraint.name,
                constraint.relation,
                constraint.referenced_relation,
            )
            for constraint in _CONTRACT.constraints
            if constraint.kind == "f"
        )
        graph, self_references = _schema_graph()
        multi_table_sccs = _multi_table_sccs(graph)

        self.assertEqual(_parse_graph_edges(text), expected_edges)
        self.assertEqual(len(multi_table_sccs), 1)
        self.assertEqual(
            _parse_marker(text, "multi-table-scc"),
            multi_table_sccs[0],
        )
        self.assertEqual(
            _parse_marker(text, "self-reference"),
            tuple(sorted(self_references)),
        )

    def test_atlas_states_only_current_policy_and_exact_future_handoffs(self) -> None:
        self.assertEqual(
            _parse_marker(_PARSER_WITNESS, "future-impact"),
            _FUTURE_ISSUES,
        )
        text = self._atlas_text()

        self.assertEqual(_parse_marker(text, "future-impact"), _FUTURE_ISSUES)
        for phrase in (
            "object-free owned namespace",
            "exact current schema",
            "reset-required",
            "no runtime behavior changes",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)
        for symbol in _RETIRED_SCHEMA_SYMBOLS:
            with self.subTest(symbol=symbol):
                self.assertNotIn(symbol, text)


if __name__ == "__main__":
    unittest.main()
