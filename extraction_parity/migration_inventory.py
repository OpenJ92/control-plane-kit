"""Closed inventory for semantic migration from the mutable legacy test tree."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable


class MigrationInventoryError(ValueError):
    """Raised when migration evidence is incomplete or ambiguous."""


SCHEMA = "cpk.semantic-test-migration-inventory"
RULES_SCHEMA = "cpk.semantic-test-migration-rules"
MAXIMUM_TEXT_BYTES = 1024
NEGATIVE_NAME_TERMS = frozenset(
    {
        "ambiguous",
        "blocked",
        "conflict",
        "denied",
        "duplicate",
        "error",
        "expired",
        "fail",
        "forbid",
        "invalid",
        "missing",
        "reject",
        "replay",
        "revoke",
        "rollback",
        "stale",
        "timeout",
        "unknown",
        "without",
        "wrong",
    }
)
NEGATIVE_ASSERTIONS = frozenset(
    {
        "assertFalse",
        "assertIsNone",
        "assertNotEqual",
        "assertNotIn",
        "assertRaises",
        "assertRaisesRegex",
    }
)


@dataclass(frozen=True)
class SourceLane:
    distribution: str
    repository: str
    commit: str
    gate: str
    root: Path
    test_roots: tuple[str, ...]
    script_roots: tuple[str, ...] = ()


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MigrationInventoryError(f"{label} must be non-blank text")
    if len(value.encode("utf-8")) > MAXIMUM_TEXT_BYTES:
        raise MigrationInventoryError(f"{label} exceeds the byte bound")
    return value


def _strings(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise MigrationInventoryError(f"{label} must be a string list")
    return tuple(_text(item, label) for item in value)


def decode_rules(document: dict[str, object]) -> dict[str, object]:
    if set(document) != {"schema", "assignments", "legacy_script_issue"}:
        raise MigrationInventoryError("migration rules have unknown or missing fields")
    if document["schema"] != RULES_SCHEMA:
        raise MigrationInventoryError("unsupported migration rules schema")
    assignments = document["assignments"]
    if not isinstance(assignments, list) or not assignments:
        raise MigrationInventoryError("migration assignments must be non-empty")
    modules: set[str] = set()
    issues: set[int] = set()
    for assignment in assignments:
        if not isinstance(assignment, dict) or set(assignment) != {
            "issue",
            "distribution",
            "modules",
        }:
            raise MigrationInventoryError("migration assignment is not closed")
        issue = assignment["issue"]
        if not isinstance(issue, int) or issue <= 0 or issue in issues:
            raise MigrationInventoryError("migration issues must be unique positive integers")
        issues.add(issue)
        _text(assignment["distribution"], "assignment.distribution")
        assigned_modules = _strings(assignment["modules"], "assignment.modules")
        if not assigned_modules:
            raise MigrationInventoryError("migration assignment cannot be empty")
        overlap = modules.intersection(assigned_modules)
        if overlap:
            raise MigrationInventoryError(f"legacy modules have multiple owners: {sorted(overlap)}")
        modules.update(assigned_modules)
    script_issue = document["legacy_script_issue"]
    if not isinstance(script_issue, int) or script_issue <= 0:
        raise MigrationInventoryError("legacy script issue must be a positive integer")
    return document


def _module_name(path: Path, test_root: str) -> str:
    relative = path.relative_to(Path(test_root).parent)
    return ".".join(relative.with_suffix("").parts)


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _imports(tree: ast.Module) -> tuple[str, ...]:
    values: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            values.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            values.add(node.module)
    return tuple(sorted(values))


def _method_record(
    *,
    distribution: str,
    module: str,
    path: str,
    class_name: str | None,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    imports: tuple[str, ...],
) -> dict[str, object]:
    assertions: set[str] = set()
    dimensions: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            assertions.add("assert")
        if not isinstance(child, ast.Call):
            continue
        name = _call_name(child)
        if name is not None and name.startswith("assert"):
            assertions.add(name)
        if name == "subTest":
            for keyword in child.keywords:
                dimensions.add(keyword.arg or "**kwargs")
    words = frozenset(node.name.removeprefix("test_").split("_"))
    matched_negative_terms = {
        term
        for word in words
        for term in NEGATIVE_NAME_TERMS
        if word == term or word.startswith(term)
    }
    negative_hints = sorted(
        {f"name:{term}" for term in matched_negative_terms}
        | {f"assertion:{name}" for name in assertions.intersection(NEGATIVE_ASSERTIONS)}
    )
    suffix = f"{class_name}.{node.name}" if class_name else node.name
    return {
        "id": f"{distribution}:{module}.{suffix}",
        "module": module,
        "class": class_name,
        "method": node.name,
        "path": path,
        "line": node.lineno,
        "assertions": sorted(assertions),
        "negative_case_hints": negative_hints,
        "subtest_dimensions": sorted(dimensions),
        "imports": list(imports),
    }


def scan_test_root(lane: SourceLane) -> dict[str, object]:
    methods: list[dict[str, object]] = []
    helpers: list[str] = []
    for test_root in lane.test_roots:
        root = lane.root / test_root
        if not root.is_dir():
            raise MigrationInventoryError(
                f"missing test root for {lane.distribution}: {test_root}"
            )
        for path in sorted(root.rglob("*.py")):
            relative = path.relative_to(lane.root).as_posix()
            if path.name.startswith("test_"):
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
                except (OSError, UnicodeDecodeError, SyntaxError) as error:
                    raise MigrationInventoryError(f"cannot parse test source: {relative}") from error
                imports = _imports(tree)
                module = _module_name(path.relative_to(lane.root), test_root)
                for item in tree.body:
                    if isinstance(
                        item, (ast.FunctionDef, ast.AsyncFunctionDef)
                    ) and item.name.startswith("test_"):
                        methods.append(
                            _method_record(
                                distribution=lane.distribution,
                                module=module,
                                path=relative,
                                class_name=None,
                                node=item,
                                imports=imports,
                            )
                        )
                    elif isinstance(item, ast.ClassDef):
                        for method in item.body:
                            if isinstance(
                                method, (ast.FunctionDef, ast.AsyncFunctionDef)
                            ) and method.name.startswith("test_"):
                                methods.append(
                                    _method_record(
                                        distribution=lane.distribution,
                                        module=module,
                                        path=relative,
                                        class_name=item.name,
                                        node=method,
                                        imports=imports,
                                    )
                                )
            else:
                relative_parts = path.relative_to(lane.root).parts
                if "tests" in relative_parts or "fixtures" in relative_parts:
                    helpers.append(relative)
    scripts: list[str] = []
    for script_root in lane.script_roots:
        root = lane.root / script_root
        if not root.is_dir():
            raise MigrationInventoryError(
                f"missing script root for {lane.distribution}: {script_root or '.'}"
            )
        paths = root.glob("*.sh") if not script_root else root.rglob("*.sh")
        for path in sorted(paths):
            relative = path.relative_to(lane.root).as_posix()
            scripts.append(relative)
    identities = [str(method["id"]) for method in methods]
    if len(identities) != len(set(identities)):
        raise MigrationInventoryError(f"duplicate test identity in {lane.distribution}")
    return {
        "distribution": lane.distribution,
        "repository": lane.repository,
        "commit": lane.commit,
        "gate": lane.gate,
        "test_roots": list(lane.test_roots),
        "methods": sorted(methods, key=lambda item: str(item["id"])),
        "helpers": sorted(set(helpers)),
        "scripts": sorted(set(scripts)),
    }


def _assignment_index(rules: dict[str, object]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for assignment in rules["assignments"]:
        for module in assignment["modules"]:
            result[module] = {
                "issue": assignment["issue"],
                "distribution": assignment["distribution"],
            }
    return result


def _reference_source(
    reference: str, methods: dict[str, dict[str, object]]
) -> dict[str, object]:
    source = methods.get(f"legacy-reference:{reference}")
    if source is None:
        raise MigrationInventoryError(f"reference test has no source method: {reference}")
    return source


def _manifest_digest(manifest: dict[str, object]) -> str:
    payload = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def build_migration_inventory(
    *,
    reference_tests: dict[str, object],
    manifest: dict[str, object],
    demos: dict[str, object],
    rules: dict[str, object],
    lanes: tuple[dict[str, object], ...],
) -> dict[str, object]:
    decode_rules(rules)
    if reference_tests.get("schema") != "cpk.reference-test-inventory":
        raise MigrationInventoryError("unsupported reference test inventory")
    if manifest.get("schema") != "cpk.parity-manifest":
        raise MigrationInventoryError("unsupported parity manifest")
    if demos.get("schema") != "cpk.reference-demo-inventory":
        raise MigrationInventoryError("unsupported reference demo inventory")
    if not lanes:
        raise MigrationInventoryError("source lanes cannot be empty")
    lane_by_name = {str(lane["distribution"]): lane for lane in lanes}
    if len(lane_by_name) != len(lanes):
        raise MigrationInventoryError("source lane distributions must be unique")
    required_lanes = {
        "legacy-reference",
        "legacy-mutable",
        "control-plane-kit-core",
        "control-plane-kit-operations",
        "control-plane-kit-interpreters",
        "control-plane-kit-servers",
        "control-plane-kit-secrets",
    }
    if set(lane_by_name) != required_lanes:
        raise MigrationInventoryError("source lanes do not match the closed distribution set")
    all_methods = {
        str(method["id"]): method
        for lane in lanes
        for method in lane["methods"]
    }
    if len(all_methods) != sum(len(lane["methods"]) for lane in lanes):
        raise MigrationInventoryError("test identities collide across source lanes")
    assignment_index = _assignment_index(rules)
    legacy_modules = {
        str(method["module"])
        for method in lane_by_name["legacy-mutable"]["methods"]
    }
    missing_modules = legacy_modules - set(assignment_index)
    stale_modules = set(assignment_index) - legacy_modules
    if missing_modules or stale_modules:
        raise MigrationInventoryError(
            f"migration module assignments differ from mutable legacy tests: "
            f"missing={sorted(missing_modules)}, stale={sorted(stale_modules)}"
        )
    current_methods = [
        method
        for lane in lanes
        if lane["distribution"] not in {"legacy-reference", "legacy-mutable"}
        for method in lane["methods"]
    ]
    current_by_method: dict[str, list[str]] = {}
    for method in current_methods:
        current_by_method.setdefault(str(method["method"]), []).append(str(method["id"]))
    manifest_by_reference = {
        str(entry["reference"]): entry
        for entry in manifest["entries"]
        if entry["kind"] == "test"
    }
    reference_assignments: list[dict[str, object]] = []
    for test in reference_tests["tests"]:
        reference = str(test["reference"])
        source = _reference_source(reference, all_methods)
        module = ".".join(reference.split(".")[:2])
        target = assignment_index.get(module)
        if target is None:
            raise MigrationInventoryError(f"reference module has no target: {module}")
        if reference not in manifest_by_reference:
            raise MigrationInventoryError(f"reference test is absent from parity manifest: {reference}")
        reference_assignments.append(
            {
                "reference": reference,
                "law": test["law"],
                "source": {
                    "path": source["path"],
                    "line": source["line"],
                    "assertions": source["assertions"],
                    "negative_case_hints": source["negative_case_hints"],
                    "subtest_dimensions": source["subtest_dimensions"],
                },
                "provisional_target": target,
                "current_successor_candidates": sorted(
                    current_by_method.get(str(source["method"]), [])
                ),
            }
        )
    reference_identities = {
        str(test["reference"]) for test in reference_tests["tests"]
    }
    mutable_only = []
    for method in lane_by_name["legacy-mutable"]["methods"]:
        legacy_reference = str(method["id"]).removeprefix("legacy-mutable:")
        if legacy_reference in reference_identities:
            continue
        mutable_only.append(
            {
                "id": method["id"],
                "path": method["path"],
                "line": method["line"],
                "method": method["method"],
                "provisional_target": assignment_index[str(method["module"])],
                "negative_case_hints": method["negative_case_hints"],
            }
        )
    demo_scripts = {
        script: str(demo["id"])
        for demo in demos["demos"]
        for script in demo["scripts"]
    }
    legacy_scripts = []
    for script in lane_by_name["legacy-mutable"]["scripts"]:
        legacy_scripts.append(
            {
                "path": script,
                "provisional_issue": rules["legacy_script_issue"],
                "reference_demo": demo_scripts.get(script),
            }
        )
    provisional_target_counts = []
    for assignment in rules["assignments"]:
        issue = assignment["issue"]
        provisional_target_counts.append(
            {
                "issue": issue,
                "distribution": assignment["distribution"],
                "reference_laws": sum(
                    entry["provisional_target"]["issue"] == issue
                    for entry in reference_assignments
                ),
                "mutable_only_methods": sum(
                    entry["provisional_target"]["issue"] == issue
                    for entry in mutable_only
                ),
            }
        )
    legacy_module_imports = []
    for module in sorted(legacy_modules):
        imports = sorted(
            {
                value
                for method in lane_by_name["legacy-mutable"]["methods"]
                if method["module"] == module
                for value in method["imports"]
                if str(value).split(".")[0] == "control_plane_kit"
            }
        )
        legacy_module_imports.append({"module": module, "imports": imports})
    current_test_records = [
        {
            field: method[field]
            for field in (
                "id",
                "module",
                "class",
                "method",
                "path",
                "line",
                "assertions",
                "negative_case_hints",
                "subtest_dimensions",
            )
        }
        for method in current_methods
    ]
    parity_entries = list(manifest_by_reference.values())
    inventory = {
        "schema": SCHEMA,
        "reference": reference_tests["reference"],
        "parity_manifest_digest": _manifest_digest(manifest),
        "recorded_parity_counts": {
            "with_successors": sum(bool(entry["successors"]) for entry in parity_entries),
            "with_supersession": sum(entry["supersession"] is not None for entry in parity_entries),
            "without_completion_record": sum(
                not entry["successors"] and entry["supersession"] is None
                for entry in parity_entries
            ),
        },
        "counts": {
            "reference_laws": len(reference_assignments),
            "mutable_only_methods": len(mutable_only),
            "current_methods": len(current_methods),
            "legacy_scripts": len(legacy_scripts),
        "legacy_helpers": len(lane_by_name["legacy-mutable"]["helpers"]),
            "current_helpers": sum(
                len(lane["helpers"])
                for lane in lanes
                if lane["distribution"] not in {"legacy-reference", "legacy-mutable"}
            ),
            "current_scripts": sum(
                len(lane["scripts"])
                for lane in lanes
                if lane["distribution"] not in {"legacy-reference", "legacy-mutable"}
            ),
        },
        "sources": [
            {
                "distribution": lane["distribution"],
                "repository": lane["repository"],
                "commit": lane["commit"],
                "gate": lane["gate"],
                "method_count": len(lane["methods"]),
                "helper_count": len(lane["helpers"]),
                "script_count": len(lane["scripts"]),
            }
            for lane in lanes
        ],
        "provisional_target_counts": provisional_target_counts,
        "legacy_module_imports": legacy_module_imports,
        "reference_assignments": sorted(
            reference_assignments, key=lambda item: str(item["reference"])
        ),
        "mutable_only_methods": sorted(mutable_only, key=lambda item: str(item["id"])),
        "legacy_helpers": lane_by_name["legacy-mutable"]["helpers"],
        "legacy_scripts": legacy_scripts,
        "current_helpers": sorted(
            [
                {"distribution": lane["distribution"], "path": path}
                for lane in lanes
                if lane["distribution"] not in {"legacy-reference", "legacy-mutable"}
                for path in lane["helpers"]
            ],
            key=lambda item: (str(item["distribution"]), str(item["path"])),
        ),
        "current_scripts": sorted(
            [
                {"distribution": lane["distribution"], "path": path}
                for lane in lanes
                if lane["distribution"] not in {"legacy-reference", "legacy-mutable"}
                for path in lane["scripts"]
            ],
            key=lambda item: (str(item["distribution"]), str(item["path"])),
        ),
        "current_tests": sorted(current_test_records, key=lambda item: str(item["id"])),
    }
    return decode_migration_inventory(inventory)


def decode_migration_inventory(document: dict[str, object]) -> dict[str, object]:
    required = {
        "schema",
        "reference",
        "parity_manifest_digest",
        "recorded_parity_counts",
        "counts",
        "sources",
        "provisional_target_counts",
        "legacy_module_imports",
        "reference_assignments",
        "mutable_only_methods",
        "legacy_helpers",
        "legacy_scripts",
        "current_helpers",
        "current_scripts",
        "current_tests",
    }
    if set(document) != required or document["schema"] != SCHEMA:
        raise MigrationInventoryError("migration inventory has unknown or missing fields")
    references = [
        str(entry["reference"]) for entry in document["reference_assignments"]
    ]
    if not references or len(references) != len(set(references)):
        raise MigrationInventoryError("reference assignments must be non-empty and unique")
    current_ids = [str(entry["id"]) for entry in document["current_tests"]]
    if len(current_ids) != len(set(current_ids)):
        raise MigrationInventoryError("current test identities must be unique")
    script_paths = [str(entry["path"]) for entry in document["legacy_scripts"]]
    if len(script_paths) != len(set(script_paths)):
        raise MigrationInventoryError("legacy script paths must be unique")
    counts = document["counts"]
    if not isinstance(counts, dict) or counts.get("reference_laws") != len(references):
        raise MigrationInventoryError("reference law count does not match assignments")
    if counts.get("current_methods") != len(current_ids):
        raise MigrationInventoryError("current method count does not match inventory")
    if counts.get("legacy_scripts") != len(script_paths):
        raise MigrationInventoryError("legacy script count does not match inventory")
    target_issues = [entry["issue"] for entry in document["provisional_target_counts"]]
    if len(target_issues) != len(set(target_issues)):
        raise MigrationInventoryError("provisional target issues must be unique")
    if sum(
        entry["reference_laws"] for entry in document["provisional_target_counts"]
    ) != len(references):
        raise MigrationInventoryError("provisional target law counts do not cover references")
    if sum(
        entry["mutable_only_methods"]
        for entry in document["provisional_target_counts"]
    ) != len(document["mutable_only_methods"]):
        raise MigrationInventoryError("provisional target mutable counts do not cover methods")
    import_modules = [entry["module"] for entry in document["legacy_module_imports"]]
    if len(import_modules) != len(set(import_modules)):
        raise MigrationInventoryError("legacy module import identities must be unique")
    if counts.get("current_helpers") != len(document["current_helpers"]):
        raise MigrationInventoryError("current helper count does not match inventory")
    if counts.get("current_scripts") != len(document["current_scripts"]):
        raise MigrationInventoryError("current script count does not match inventory")
    return document


def _load(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MigrationInventoryError(f"cannot read inventory input: {path.name}") from error
    if not isinstance(value, dict):
        raise MigrationInventoryError(f"inventory input must be an object: {path.name}")
    return value


def _write(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-tests", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--demos", type=Path, required=True)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--coordination-root", type=Path, required=True)
    parser.add_argument("--interpreters-root", type=Path, required=True)
    parser.add_argument("--servers-root", type=Path, required=True)
    parser.add_argument("--secrets-root", type=Path, required=True)
    parser.add_argument("--source-commits", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    commits = _load(args.source_commits)
    if set(commits) != {
        "legacy-reference",
        "legacy-mutable",
        "control-plane-kit-core",
        "control-plane-kit-operations",
        "control-plane-kit-interpreters",
        "control-plane-kit-servers",
        "control-plane-kit-secrets",
    }:
        raise MigrationInventoryError("source commits do not match the closed distribution set")
    specs = (
        SourceLane(
            distribution="legacy-reference",
            repository="OpenJ92/control-plane-kit",
            commit=_text(commits["legacy-reference"], "commit"),
            gate="./reference-test.sh",
            root=args.reference_root,
            test_roots=("tests",),
            script_roots=("", "scripts"),
        ),
        SourceLane(
            distribution="legacy-mutable",
            repository="OpenJ92/control-plane-kit",
            commit=_text(commits["legacy-mutable"], "commit"),
            gate="./test.sh (migration input only)",
            root=args.coordination_root,
            test_roots=("tests",),
            script_roots=("", "scripts"),
        ),
        SourceLane(
            distribution="control-plane-kit-core",
            repository="OpenJ92/control-plane-kit",
            commit=_text(commits["control-plane-kit-core"], "commit"),
            gate="./control-plane-kit-core/test.sh",
            root=args.coordination_root,
            test_roots=("control-plane-kit-core/tests",),
            script_roots=("control-plane-kit-core",),
        ),
        SourceLane(
            distribution="control-plane-kit-operations",
            repository="OpenJ92/control-plane-kit",
            commit=_text(commits["control-plane-kit-operations"], "commit"),
            gate="./control-plane-kit-operations/test.sh",
            root=args.coordination_root,
            test_roots=("control-plane-kit-operations/tests",),
            script_roots=("control-plane-kit-operations",),
        ),
        SourceLane(
            distribution="control-plane-kit-interpreters",
            repository="OpenJ92/control-plane-kit-interpreters",
            commit=_text(commits["control-plane-kit-interpreters"], "commit"),
            gate="./test.sh",
            root=args.interpreters_root,
            test_roots=("tests",),
            script_roots=("",),
        ),
        SourceLane(
            distribution="control-plane-kit-servers",
            repository="OpenJ92/control-plane-kit-servers",
            commit=_text(commits["control-plane-kit-servers"], "commit"),
            gate="./test.sh",
            root=args.servers_root,
            test_roots=("tests", "products"),
            script_roots=("", "scripts"),
        ),
        SourceLane(
            distribution="control-plane-kit-secrets",
            repository="OpenJ92/control-plane-kit-secrets",
            commit=_text(commits["control-plane-kit-secrets"], "commit"),
            gate="./test.sh",
            root=args.secrets_root,
            test_roots=("tests",),
            script_roots=("",),
        ),
    )
    lanes = tuple(scan_test_root(spec) for spec in specs)
    inventory = build_migration_inventory(
        reference_tests=_load(args.reference_tests),
        manifest=_load(args.manifest),
        demos=_load(args.demos),
        rules=_load(args.rules),
        lanes=lanes,
    )
    _write(args.output, inventory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
