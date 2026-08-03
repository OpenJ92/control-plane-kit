"""Closed evidence and validation for retiring the mutable legacy package."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Iterable


SCHEMA = "cpk.legacy-retirement-manifest"
FUTURE_REFRESH_SCHEMA = "cpk.legacy-retirement-future-owner-refresh"
EVIDENCE_SCHEMA = "cpk.harden-tests-parity.legacy-retirement-evidence"
BASELINE_COMMIT = "5bc3e7debb2aa7f65eeaf3d4c29f030da55d99a8"
REFERENCE_TAG = "pre-server-product-extraction-2026-07-20"
REFERENCE_COMMIT = "20129959d3b0f8e8bd5dbdafdf51c0a5d592a9ec"

DELETION_DISPOSITIONS = frozenset(
    {
        "delete-legacy-package",
        "delete-legacy-tests",
        "delete-legacy-examples",
        "delete-legacy-live-shell",
        "delete-legacy-demo-helper",
        "delete-legacy-packaging",
    }
)

POST_BASELINE_ADDITIONS = (
    "artifacts/extraction/harden-tests-parity-1318-completed-owner-promotion.json",
    "artifacts/extraction/harden-tests-parity-1318-evidence.json",
    "artifacts/extraction/harden-tests-parity-1318-future-owner-refresh.json",
    "artifacts/extraction/harden-tests-parity-1318-retirement-manifest.json",
    "build-legacy-retirement-manifest.sh",
    "extraction_parity/retirement.py",
    "extraction_parity/tests/test_legacy_retirement.py",
    "validate-legacy-retirement.sh",
)

PROMOTION_EVIDENCE_ID = "harden-tests-parity-1318-completed-owner-promotion"


def _parity_test(module: str, method: str) -> str:
    return f"control-plane-kit-parity:{module}.PackageIntegrityTests.{method}"


INTEGRITY_TESTS = {
    "approved": _parity_test(
        "test_support.tests.test_package_integrity",
        "test_approved_dynamic_conditional_skip_is_accepted",
    ),
    "blank-reason": _parity_test(
        "test_support.tests.test_package_integrity", "test_blank_skip_reason_is_rejected"
    ),
    "duplicate": _parity_test(
        "test_support.tests.test_package_integrity",
        "test_duplicate_skip_approval_is_rejected",
    ),
    "literal": _parity_test(
        "test_support.tests.test_package_integrity",
        "test_literal_skip_condition_is_rejected",
    ),
    "mocks": _parity_test(
        "test_support.tests.test_package_integrity",
        "test_mocks_are_reported_without_becoming_findings",
    ),
    "pass": _parity_test(
        "test_support.tests.test_package_integrity", "test_pass_only_test_is_rejected"
    ),
    "ellipsis": _parity_test(
        "test_support.tests.test_package_integrity",
        "test_ellipsis_only_test_is_rejected",
    ),
    "swallowed": _parity_test(
        "test_support.tests.test_package_integrity",
        "test_swallowed_exception_is_rejected",
    ),
    "unapproved": _parity_test(
        "test_support.tests.test_package_integrity",
        "test_unapproved_conditional_skip_is_rejected",
    ),
    "unconditional": _parity_test(
        "test_support.tests.test_package_integrity",
        "test_unconditional_skip_is_rejected",
    ),
    "proof-option": _parity_test(
        "test_support.tests.test_package_integrity",
        "test_proof_changing_gate_option_is_rejected",
    ),
}


def _backend_test(module: str, class_name: str, method: str) -> str:
    return f"control-plane-kit-parity:{module}.{class_name}.{method}"


BACKEND_TESTS = {
    "closed": _backend_test(
        "current_backend.tests.test_contracts",
        "BackendContractTests",
        "test_closed_contract_fixture_is_coherent",
    ),
    "reverse-cycle": _backend_test(
        "current_backend.tests.test_contracts",
        "BackendContractTests",
        "test_direct_reverse_and_cyclic_dependencies_fail_closed",
    ),
    "transitive": _backend_test(
        "current_backend.tests.test_contracts",
        "BackendContractTests",
        "test_transitive_forbidden_path_is_rejected_at_its_illegal_edge",
    ),
    "inventory": _backend_test(
        "current_backend.tests.test_contracts",
        "BackendContractTests",
        "test_unowned_and_duplicate_source_inventory_fail_closed",
    ),
    "adapter": _backend_test(
        "current_backend.tests.test_contracts",
        "BackendContractTests",
        "test_concrete_interpreter_and_transport_imports_in_operations_fail_closed",
    ),
    "scenario": _backend_test(
        "current_backend.tests.test_contracts",
        "BackendContractTests",
        "test_source_live_bypass_and_application_mock_fail_closed",
    ),
    "coordinates": _backend_test(
        "current_backend.tests.test_source_lock",
        "BackendSourceLockTests",
        "test_one_server_products_lock_derives_all_upstream_commits",
    ),
    "order": _backend_test(
        "current_backend.tests.test_runner",
        "BackendGateRunnerTests",
        "test_stages_execute_in_exact_order_and_emit_bounded_report",
    ),
}


COMPLETED_OWNER_PROMOTIONS: dict[str, tuple[str, ...]] = {
    "tests.test_architecture_test_integrity.ArchitectureTestIntegrityTests.test_allowed_skip_requires_a_reviewable_reason": (
        INTEGRITY_TESTS["approved"],
        INTEGRITY_TESTS["blank-reason"],
    ),
    "tests.test_architecture_test_integrity.ArchitectureTestIntegrityTests.test_duplicate_and_literal_false_approved_skips_fail_closed": (
        INTEGRITY_TESTS["duplicate"],
        INTEGRITY_TESTS["literal"],
    ),
    "tests.test_architecture_test_integrity.ArchitectureTestIntegrityTests.test_mocks_are_reported_without_becoming_automatic_failures": (
        INTEGRITY_TESTS["mocks"],
    ),
    "tests.test_architecture_test_integrity.ArchitectureTestIntegrityTests.test_placeholders_empty_tests_and_swallowed_exceptions_fail": (
        INTEGRITY_TESTS["pass"],
        INTEGRITY_TESTS["ellipsis"],
        INTEGRITY_TESTS["swallowed"],
    ),
    "tests.test_architecture_test_integrity.ArchitectureTestIntegrityTests.test_repository_has_only_explicit_optional_dependency_skips": (
        INTEGRITY_TESTS["approved"],
        INTEGRITY_TESTS["unapproved"],
        INTEGRITY_TESTS["proof-option"],
    ),
    "tests.test_architecture_test_integrity.ArchitectureTestIntegrityTests.test_unconditional_and_unapproved_skips_fail_with_locations": (
        INTEGRITY_TESTS["unconditional"],
        INTEGRITY_TESTS["unapproved"],
    ),
    "tests.test_architecture_dependencies.ArchitectureDependencyTests.test_process_and_http_clients_are_adapter_owned": (
        BACKEND_TESTS["adapter"],
    ),
    "tests.test_architecture_dependencies.ArchitectureDependencyTests.test_repository_obeys_declared_dependency_and_transport_ownership": (
        BACKEND_TESTS["closed"],
        BACKEND_TESTS["reverse-cycle"],
        BACKEND_TESTS["adapter"],
    ),
    "tests.test_architecture_dependencies.ArchitectureDependencyTests.test_reverse_dependency_is_rejected_for_import_and_from_import": (
        BACKEND_TESTS["reverse-cycle"],
    ),
    "tests.test_architecture_scenarios.ScenarioArchitectureTests.test_atomic_contract_suites_remain_independent_of_scenario_runner": (
        BACKEND_TESTS["closed"],
        BACKEND_TESTS["order"],
    ),
    "tests.test_architecture_scenarios.ScenarioArchitectureTests.test_fake_effect_is_a_typed_capability_provider_not_an_application_mock": (
        BACKEND_TESTS["scenario"],
    ),
    "tests.test_architecture_scenarios.ScenarioArchitectureTests.test_policy_rejects_store_scheduler_mock_and_private_coordinator_bypass": (
        BACKEND_TESTS["scenario"],
    ),
    "tests.test_architecture_scenarios.ScenarioArchitectureTests.test_scenario_modules_obey_application_and_persistence_boundaries": (
        BACKEND_TESTS["scenario"],
        BACKEND_TESTS["order"],
    ),
    "tests.test_package_inventory.PackageModuleInventoryTests.test_domain_inventory_is_exactly_the_admitted_closed_languages": (
        BACKEND_TESTS["inventory"],
    ),
    "tests.test_package_inventory.PackageModuleInventoryTests.test_inventory_distinguishes_completed_and_deferred_movement": (
        BACKEND_TESTS["coordinates"],
    ),
    "tests.test_package_inventory.PackageModuleInventoryTests.test_inventory_is_exhaustive_and_assigns_one_owner": (
        BACKEND_TESTS["inventory"],
    ),
    "tests.test_package_topology.CurrentPackageTopologyTests.test_current_package_graph_is_acyclic": (
        BACKEND_TESTS["closed"],
        BACKEND_TESTS["reverse-cycle"],
    ),
    "tests.test_package_topology.CurrentPackageTopologyTests.test_current_source_has_one_way_recovery_policy_planning_edges": (
        BACKEND_TESTS["transitive"],
    ),
    "tests.test_package_topology.PackageTopologyPolicyTests.test_declared_edges_cannot_hide_direct_or_multi_node_cycles": (
        BACKEND_TESTS["reverse-cycle"],
    ),
    "tests.test_package_topology.PackageTopologyPolicyTests.test_domain_language_rejects_operations_products_interpreters_and_entrypoints": (
        BACKEND_TESTS["reverse-cycle"],
        BACKEND_TESTS["adapter"],
    ),
    "tests.test_package_topology.PackageTopologyPolicyTests.test_legal_acyclic_sibling_dependencies_are_representable": (
        BACKEND_TESTS["closed"],
    ),
    "tests.test_package_topology.PackageTopologyPolicyTests.test_product_may_project_domain_but_not_registry_or_process": (
        BACKEND_TESTS["closed"],
    ),
    "tests.test_package_topology.PackageTopologyPolicyTests.test_product_to_domain_projection_does_not_authorize_reverse_edge": (
        BACKEND_TESTS["reverse-cycle"],
    ),
    "tests.test_package_topology.PackageTopologyPolicyTests.test_transitive_product_to_process_path_is_rejected": (
        BACKEND_TESTS["transitive"],
    ),
}

RETIREMENT_TEST_IDS = tuple(
    f"control-plane-kit-parity:extraction_parity.tests.test_legacy_retirement."
    f"LegacyRetirementTests.{method}"
    for method in (
        "test_path_policy_is_closed_and_product_independent",
        "test_future_owner_refresh_accepts_only_two_implemented_transitions",
        "test_manifest_is_exhaustive_and_validates_pre_and_post_deletion",
        "test_live_artifact_manifest_covers_every_baseline_path_once",
        "test_completed_owner_promotions_are_exact_and_current",
        "test_current_instructions_reject_retired_commands",
    )
)

CURRENT_INSTRUCTION_PATHS = (
    "README.md",
    "AGENTS.md",
    "DESIGN.md",
    "SERVER_PRODUCT_ROLLOUT.md",
    "docs/OPERATING_MODEL.md",
    "docs/CONTROL_PLANE_LANGUAGE.md",
    "docs/CONTROL_PLANE_LANGUAGE_STUDY_GUIDE.md",
    "docs/DEPLOY_PROGRAM.md",
    "docs/READ_INTERFACES.md",
    "docs/POSTGRES_UNIT_OF_WORK.md",
    "docs/templates/child-issue.md",
    "docs/templates/roadmap-closeout.md",
)


class RetirementError(ValueError):
    """Raised when deletion evidence is incomplete or inconsistent."""


def _digest(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _read(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RetirementError(f"cannot read retirement input: {path}") from error
    if not isinstance(value, dict):
        raise RetirementError(f"retirement input must be an object: {path}")
    return value


def _write(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _git(root: Path, *arguments: str) -> str:
    process = subprocess.run(
        ("git", "-C", str(root), *arguments),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if process.returncode != 0:
        detail = process.stderr.strip() or "git command failed"
        raise RetirementError(detail)
    return process.stdout


def baseline_entries(root: Path, commit: str) -> tuple[tuple[str, str, str], ...]:
    output = _git(root, "ls-tree", "-r", "--full-tree", commit)
    entries: list[tuple[str, str, str]] = []
    for line in output.splitlines():
        metadata, separator, path = line.partition("\t")
        if not separator:
            raise RetirementError("baseline tree output is malformed")
        mode, kind, object_id = metadata.split()
        if kind != "blob":
            raise RetirementError(f"baseline contains unsupported object: {path}")
        entries.append((path, mode, object_id))
    if not entries:
        raise RetirementError("baseline tree is empty")
    return tuple(entries)


def classify_path(path: str, legacy_live_shells: frozenset[str]) -> str:
    if path.startswith("control_plane_kit/"):
        return "delete-legacy-package"
    if path.startswith("tests/"):
        return "delete-legacy-tests"
    if path.startswith("examples/"):
        return "delete-legacy-examples"
    if path in legacy_live_shells:
        return "delete-legacy-live-shell"
    if path in {"scripts/read-demo-up.sh", "scripts/read-demo-down.sh"}:
        return "delete-legacy-demo-helper"
    if path in {
        ".dockerignore",
        "Dockerfile",
        "packaging-test.sh",
        "pyproject.toml",
        "test.sh",
    }:
        return "delete-legacy-packaging"
    if path.startswith(("control-plane-kit-core/", "control-plane-kit-operations/")):
        return "retain-current-distribution"
    if path.startswith("current_backend/") or path in {
        "current-backend-test.sh",
        "current-backend.contracts.json",
        "current-backend.lock.json",
    }:
        return "retain-current-backend"
    if path.startswith("test_support/"):
        return "retain-current-test-support"
    if path.startswith("extraction_parity/") or path in {
        "build-parity-manifest.sh",
        "build-semantic-test-migration-inventory.sh",
        "classify-reference-laws.sh",
        "reference-inventory.sh",
        "validate-parity.sh",
        "validate-reference-demos.sh",
        "validate-semantic-test-migration-inventory.sh",
    }:
        return "retain-parity-tooling"
    if path == "reference-test.sh":
        return "retain-immutable-reference-runner"
    if path.startswith("artifacts/extraction/"):
        return "retain-extraction-evidence"
    if path.startswith("docs/") or path in {
        "AGENTS.md",
        "DESIGN.md",
        "README.md",
        "SERVER_PRODUCT_ROLLOUT.md",
    }:
        return "retain-documentation"
    if path.startswith(".github/"):
        return "retain-ci"
    if path in {".gitignore", "LICENSE"}:
        return "retain-repository"
    raise RetirementError(f"baseline path has no retirement disposition: {path}")


def build_manifest(
    *,
    root: Path,
    baseline_commit: str,
    live_script_dispositions: dict[str, object],
    completion_report: dict[str, object],
    future_owner_refresh: dict[str, object],
) -> dict[str, object]:
    scripts = live_script_dispositions.get("scripts")
    if not isinstance(scripts, list):
        raise RetirementError("live-script dispositions are missing")
    legacy_shells = frozenset(str(value["legacy_script"]) for value in scripts)
    if len(legacy_shells) != len(scripts):
        raise RetirementError("legacy live-script dispositions are not unique")
    if completion_report.get("valid") is not True or completion_report.get(
        "zero_unowned"
    ) is not True:
        raise RetirementError("semantic completion does not prove zero unowned laws")
    validate_future_owner_refresh(future_owner_refresh)

    entries = [
        {
            "path": path,
            "mode": mode,
            "blob": object_id,
            "disposition": classify_path(path, legacy_shells),
        }
        for path, mode, object_id in baseline_entries(root, baseline_commit)
    ]
    counts: dict[str, int] = {}
    for entry in entries:
        disposition = str(entry["disposition"])
        counts[disposition] = counts.get(disposition, 0) + 1
    counts["baseline-files"] = len(entries)
    counts["delete-files"] = sum(
        value for key, value in counts.items() if key in DELETION_DISPOSITIONS
    )
    counts["retain-files"] = len(entries) - counts["delete-files"]
    return {
        "schema": SCHEMA,
        "issue": 1318,
        "baseline": {
            "commit": baseline_commit,
            "tree": _git(root, "rev-parse", f"{baseline_commit}^{{tree}}").strip(),
        },
        "reference": {"tag": REFERENCE_TAG, "commit": REFERENCE_COMMIT},
        "inputs": {
            "semantic_completion": _digest(completion_report),
            "future_owner_refresh": _digest(future_owner_refresh),
            "live_script_dispositions": _digest(live_script_dispositions),
        },
        "post_baseline_additions": list(POST_BASELINE_ADDITIONS),
        "entries": entries,
        "counts": dict(sorted(counts.items())),
    }


def validate_future_owner_refresh(document: dict[str, object]) -> None:
    if document.get("schema") != FUTURE_REFRESH_SCHEMA or document.get("issue") != 1318:
        raise RetirementError("future-owner refresh identity is invalid")
    owners = document.get("owners")
    if not isinstance(owners, list) or len(owners) != 12:
        raise RetirementError("future-owner refresh must contain exactly 12 owners")
    identities: set[tuple[str, int]] = set()
    completed = set()
    for owner in owners:
        if not isinstance(owner, dict):
            raise RetirementError("future-owner refresh entry must be an object")
        repository = owner.get("repository")
        number = owner.get("number")
        state = owner.get("state")
        disposition = owner.get("disposition")
        if not isinstance(repository, str) or not repository:
            raise RetirementError("future-owner repository is invalid")
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            raise RetirementError("future-owner number is invalid")
        identity = (repository, number)
        if identity in identities:
            raise RetirementError("future-owner identities must be unique")
        identities.add(identity)
        if state == "open" and disposition == "future-owned":
            continue
        if state == "closed" and disposition == "implemented-current":
            evidence = owner.get("evidence")
            if not isinstance(evidence, dict) or not evidence.get("merge_commits"):
                raise RetirementError("completed future owner lacks merged evidence")
            completed.add(number)
            continue
        raise RetirementError("future-owner state and disposition disagree")
    if completed != {1316, 1317}:
        raise RetirementError("only completed #1316 and #1317 may be promoted here")


def validate_no_live_legacy_references(
    root: Path, legacy_live_shells: frozenset[str]
) -> None:
    forbidden_text = {
        *legacy_live_shells,
        "scripts/read-demo-up.sh",
        "scripts/read-demo-down.sh",
        "pip install control-plane-kit",
        "python3 -m compileall control_plane_kit tests",
    }
    root_test = re.compile(r"(?m)^\s*\./test\.sh\s*$")
    for relative in CURRENT_INSTRUCTION_PATHS:
        path = root / relative
        if not path.is_file():
            raise RetirementError(f"current instruction document is missing: {relative}")
        text = path.read_text(encoding="utf-8")
        for forbidden in forbidden_text:
            if forbidden in text:
                raise RetirementError(
                    f"current instruction references retired surface: {relative}: {forbidden}"
                )
        if root_test.search(text):
            raise RetirementError(
                f"current instruction references retired root test gate: {relative}"
            )

    source_roots = (
        root / "control-plane-kit-core/src",
        root / "control-plane-kit-operations/src",
        root / "current_backend",
        root / "extraction_parity",
    )
    for source_root in source_roots:
        for path in source_root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                module = None
                if isinstance(node, ast.ImportFrom):
                    module = node.module
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "control_plane_kit" or alias.name.startswith(
                            "control_plane_kit."
                        ):
                            raise RetirementError(
                                f"current source imports mutable aggregate: {path}"
                            )
                if module == "control_plane_kit" or (
                    isinstance(module, str) and module.startswith("control_plane_kit.")
                ):
                    raise RetirementError(
                        f"current source imports mutable aggregate: {path}"
                    )


def _source_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _additional_test_record(root: Path, identity: str) -> dict[str, object]:
    value = identity.split(":", maxsplit=1)[1]
    if value.startswith("test_support.tests.test_package_integrity."):
        path = "test_support/tests/test_package_integrity.py"
        gate = "PYTHONPATH=test_support python3 -m unittest discover -s test_support/tests"
    elif value.startswith("current_backend.tests."):
        module = value.rsplit(".", maxsplit=2)[0]
        path = module.replace(".", "/") + ".py"
        gate = "python3 -m unittest discover -s current_backend/tests -t ."
    elif value.startswith("extraction_parity.tests."):
        module = value.rsplit(".", maxsplit=2)[0]
        path = module.replace(".", "/") + ".py"
        gate = "python3 -m unittest discover -s extraction_parity/tests -t ."
    else:
        raise RetirementError(f"promotion names unsupported current test: {identity}")
    source = root / path
    if not source.is_file():
        raise RetirementError(f"promotion current test source is missing: {path}")
    return {
        "id": identity,
        "distribution": "control-plane-kit-parity",
        "path": path,
        "source_digest": _source_digest(source),
        "gate": gate,
    }


def promote_completed_owners(
    *,
    root: Path,
    promotion_evidence: dict[str, object],
) -> dict[str, object]:
    """Promote completed #1316/#1317 laws from future to exact current proof."""
    from extraction_parity.completion import validate_semantic_completion

    artifact_root = root / "artifacts/extraction"
    paths = {
        "manifest": artifact_root / "parity-manifest.json",
        "reconciliation": artifact_root / "semantic-test-reconciliation.json",
        "inventory": artifact_root / "semantic-test-migration-inventory.json",
        "evidence": artifact_root / "successor-evidence.json",
        "aggregate": artifact_root / "harden-tests-parity-1348-evidence.json",
        "closeout": artifact_root / "semantic-migration-closeout.json",
        "report": artifact_root / "semantic-migration-completion-report.json",
    }
    documents = {name: _read(path) for name, path in paths.items() if name != "report"}
    if promotion_evidence.get("schema") != (
        "cpk.harden-tests-parity.completed-owner-promotion"
    ) or promotion_evidence.get("issue") != 1318:
        raise RetirementError("completed-owner promotion evidence is invalid")
    if set(promotion_evidence.get("completed_issues", [])) != {1316, 1317}:
        raise RetirementError("promotion evidence must close exactly #1316 and #1317")

    manifest = documents["manifest"]
    reconciliation = documents["reconciliation"]
    evidence = documents["evidence"]
    aggregate = documents["aggregate"]
    closeout = documents["closeout"]
    entries = {str(value["reference"]): value for value in manifest["entries"]}
    reviews = {str(value["reference"]): value for value in reconciliation["reviews"]}
    if set(COMPLETED_OWNER_PROMOTIONS) - set(entries) or set(
        COMPLETED_OWNER_PROMOTIONS
    ) - set(reviews):
        raise RetirementError("promotion references are missing from semantic evidence")
    for reference in COMPLETED_OWNER_PROMOTIONS:
        review = reviews[reference]
        future = review.get("future_issue")
        if review.get("disposition") == "future-issue":
            if not isinstance(future, dict) or future.get("number") not in {1316, 1317}:
                raise RetirementError(f"promotion reference has wrong future owner: {reference}")
        elif review.get("reviewed_by_issue") != 1318:
            raise RetirementError(f"promotion reference is not future-owned: {reference}")

    current_ids = sorted(
        {
            identity
            for values in COMPLETED_OWNER_PROMOTIONS.values()
            for identity in values
        }
        | set(RETIREMENT_TEST_IDS)
    )
    additional_by_id = {
        str(value["id"]): value for value in closeout["additional_current_tests"]
    }
    for identity in current_ids:
        additional_by_id[identity] = _additional_test_record(root, identity)
    for value in additional_by_id.values():
        source = root / str(value["path"])
        if not source.is_file():
            raise RetirementError(
                f"additional current test source is missing: {value['path']}"
            )
        value["source_digest"] = _source_digest(source)
    closeout["additional_current_tests"] = sorted(
        additional_by_id.values(), key=lambda value: str(value["id"])
    )

    for reference, current_tests in COMPLETED_OWNER_PROMOTIONS.items():
        entry = entries[reference]
        entry["successors"] = [
            {
                "id": identity,
                "status": "passing",
                "evidence": PROMOTION_EVIDENCE_ID,
            }
            for identity in current_tests
        ]
        entry["supersession"] = None
        review = reviews[reference]
        review.update(
            {
                "reviewed_by_issue": 1318,
                "owner": "control-plane-kit-parity",
                "disposition": "current-strengthened",
                "current_tests": list(current_tests),
                "future_issue": None,
                "rationale": (
                    "Completed #1316/#1317 current test infrastructure now proves "
                    "this law without restoring the mutable aggregate analyzer."
                ),
                "obsolete_assumption_disposition": (
                    "The mutable root package, root tests, and aggregate analyzer are retired."
                ),
            }
        )

    inventory = documents["inventory"]
    test_entries = [value for value in manifest["entries"] if value["kind"] == "test"]
    inventory["parity_manifest_digest"] = _digest(manifest)
    inventory["recorded_parity_counts"] = {
        "with_successors": sum(bool(value["successors"]) for value in test_entries),
        "with_supersession": sum(
            value["supersession"] is not None for value in test_entries
        ),
        "without_completion_record": sum(
            not value["successors"] and value["supersession"] is None
            for value in test_entries
        ),
    }

    evidence["evidence"] = [
        value
        for value in evidence["evidence"]
        if value["id"] != PROMOTION_EVIDENCE_ID
    ] + [
        {
            "id": PROMOTION_EVIDENCE_ID,
            "status": "passing",
            "digest": _digest(promotion_evidence),
        }
    ]
    evidence["evidence"].sort(key=lambda value: str(value["id"]))
    source_evidence = promotion_evidence["merged_evidence"]
    package_tests = promotion_evidence["package_tests"]
    package_commits = {
        "control-plane-kit-core": source_evidence["1316"]["coordination"],
        "control-plane-kit-operations": source_evidence["1316"]["coordination"],
        "control-plane-kit-interpreters": source_evidence["1316"]["interpreters"],
        "control-plane-kit-secrets": source_evidence["1316"]["secrets"],
        "control-plane-kit-servers": source_evidence["1316"]["server_products"],
    }
    aggregate["package_gate_evidence"] = {
        distribution: {
            "commit": commit,
            "evidence_digest": _digest(promotion_evidence),
            "evidence_issue": 1316,
            "tests": package_tests[distribution],
        }
        for distribution, commit in sorted(package_commits.items())
    }
    closeout["future_issues"] = [
        value for value in closeout["future_issues"] if value["number"] not in {1316, 1317}
    ]
    closeout["input_digests"] = {
        "manifest": _digest(manifest),
        "reconciliation": _digest(reconciliation),
        "inventory": _digest(inventory),
        "evidence": _digest(evidence),
        "aggregate": _digest(aggregate),
    }
    future_references = {
        str(value["reference"])
        for value in reconciliation["reviews"]
        if value["disposition"] == "future-issue"
    }
    required_future = {
        str(value["reference"])
        for value in manifest["entries"]
        if value["migration_state"] == "required"
        and str(value["reference"]) in future_references
    }
    inventory_ids = {str(value["id"]) for value in inventory["current_tests"]}
    additional_ids = {str(value["id"]) for value in closeout["additional_current_tests"]}
    closeout["expected_counts"] = {
        "manifest_entries": len(manifest["entries"]),
        "test_reviews": len(reconciliation["reviews"]),
        "demo_reviews": len(closeout["demo_reviews"]),
        "mutable_only_reviews": len(reconciliation["mutable_only_reviews"]),
        "current_test_identities": len(inventory_ids | additional_ids),
        "additional_current_tests": len(additional_ids),
        "current_live_laws": len(closeout["current_live_laws"]),
        "future_owned": len(future_references)
        + sum(
            value["disposition"] == "future-issue"
            for value in closeout["demo_reviews"]
        ),
        "required_future_owned": len(required_future)
        + sum(
            value["disposition"] == "future-issue"
            and entries[str(value["reference"])]["migration_state"] == "required"
            for value in closeout["demo_reviews"]
        ),
        "unowned": 0,
        "stale_successors": 0,
    }
    report = validate_semantic_completion(
        manifest,
        reconciliation,
        inventory,
        evidence,
        aggregate,
        closeout,
    )
    for name, value in (
        ("manifest", manifest),
        ("reconciliation", reconciliation),
        ("inventory", inventory),
        ("evidence", evidence),
        ("aggregate", aggregate),
        ("closeout", closeout),
        ("report", report),
    ):
        _write(paths[name], value)
    return report


def validate_manifest(
    *,
    root: Path,
    manifest: dict[str, object],
    live_script_dispositions: dict[str, object],
    completion_report: dict[str, object],
    future_owner_refresh: dict[str, object],
    require_deleted: bool,
    require_post_baseline_additions: bool = True,
) -> dict[str, object]:
    if manifest.get("schema") != SCHEMA or manifest.get("issue") != 1318:
        raise RetirementError("retirement manifest identity is invalid")
    baseline = manifest.get("baseline")
    if not isinstance(baseline, dict) or baseline.get("commit") != BASELINE_COMMIT:
        raise RetirementError("retirement baseline differs from approved merge")
    expected = build_manifest(
        root=root,
        baseline_commit=BASELINE_COMMIT,
        live_script_dispositions=live_script_dispositions,
        completion_report=completion_report,
        future_owner_refresh=future_owner_refresh,
    )
    if manifest != expected:
        raise RetirementError("retirement manifest differs from exact baseline policy")

    deleted: list[str] = []
    retained: list[str] = []
    for entry in manifest["entries"]:
        path = str(entry["path"])
        exists = (root / path).exists()
        if entry["disposition"] in DELETION_DISPOSITIONS:
            deleted.append(path)
            if require_deleted and exists:
                raise RetirementError(f"legacy path remains after retirement: {path}")
            if not require_deleted and not exists:
                raise RetirementError(f"legacy path disappeared before retirement: {path}")
        else:
            retained.append(path)
            if not exists:
                raise RetirementError(f"retained path is missing: {path}")
    if require_post_baseline_additions:
        for path in manifest["post_baseline_additions"]:
            if not (root / str(path)).exists():
                raise RetirementError(f"required retirement evidence is missing: {path}")
    if require_deleted:
        scripts = live_script_dispositions["scripts"]
        validate_no_live_legacy_references(
            root,
            frozenset(str(value["legacy_script"]) for value in scripts),
        )

    return {
        "schema": EVIDENCE_SCHEMA,
        "issue": 1318,
        "baseline_commit": BASELINE_COMMIT,
        "manifest_digest": _digest(manifest),
        "zero_unowned": True,
        "legacy_deleted": require_deleted,
        "delete_files": len(deleted),
        "retain_files": len(retained),
        "post_baseline_additions": len(manifest["post_baseline_additions"]),
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--promote-completed-owners", action="store_true")
    parser.add_argument("--require-deleted", action="store_true")
    parser.add_argument("--evidence", type=Path)
    arguments = parser.parse_args(argv)
    root = arguments.root.resolve()
    live = _read(root / "artifacts/extraction/harden-tests-parity-1347-live-script-dispositions.json")
    future = _read(root / "artifacts/extraction/harden-tests-parity-1318-future-owner-refresh.json")
    if arguments.promote_completed_owners:
        report = promote_completed_owners(
            root=root,
            promotion_evidence=_read(
                root
                / "artifacts/extraction/harden-tests-parity-1318-completed-owner-promotion.json"
            ),
        )
        print(
            "promoted_completed_owners=1316,1317 "
            f"current_tests={report['counts']['current_test_identities']} "
            f"future_owned={report['counts']['future_owned']}"
        )
        if arguments.manifest is None:
            return 0
    if arguments.manifest is None:
        raise RetirementError("--manifest is required unless only promotion runs")
    completion = _read(root / "artifacts/extraction/semantic-migration-completion-report.json")
    manifest_path = arguments.manifest
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    if arguments.build:
        _write(
            manifest_path,
            build_manifest(
                root=root,
                baseline_commit=BASELINE_COMMIT,
                live_script_dispositions=live,
                completion_report=completion,
                future_owner_refresh=future,
            ),
        )
    report = validate_manifest(
        root=root,
        manifest=_read(manifest_path),
        live_script_dispositions=live,
        completion_report=completion,
        future_owner_refresh=future,
        require_deleted=arguments.require_deleted,
        require_post_baseline_additions=not arguments.build,
    )
    if arguments.evidence is not None:
        evidence_path = arguments.evidence
        if not evidence_path.is_absolute():
            evidence_path = root / evidence_path
        _write(evidence_path, report)
    print(
        f"legacy_deleted={str(arguments.require_deleted).lower()} "
        f"delete_files={report['delete_files']} retain_files={report['retain_files']} "
        "zero_unowned=true"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
