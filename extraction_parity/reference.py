"""Bounded evidence values for an immutable frozen-reference run."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class ReferenceEvidenceError(ValueError):
    """Raised when frozen-reference evidence is incomplete or unsafe."""


@dataclass(frozen=True)
class TestSummary:
    tests_run: int
    skipped: int
    successful: bool


def parse_unittest_summary(output: str, *, maximum_bytes: int) -> TestSummary:
    if maximum_bytes <= 0:
        raise ReferenceEvidenceError("maximum_bytes must be positive")
    if len(output.encode("utf-8")) > maximum_bytes:
        raise ReferenceEvidenceError("reference test output exceeds the evidence bound")
    match = re.search(r"^Ran (?P<count>\d+) tests? in .+$", output, re.MULTILINE)
    if match is None:
        raise ReferenceEvidenceError("reference output has no unittest run summary")
    result = re.search(r"^OK(?: \(skipped=(?P<skipped>\d+)\))?$", output, re.MULTILINE)
    if result is None:
        raise ReferenceEvidenceError("reference test suite did not finish successfully")
    return TestSummary(
        tests_run=int(match.group("count")),
        skipped=int(result.group("skipped") or 0),
        successful=True,
    )


def added_resource_ids(before: Iterable[str], after: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(after) - set(before)))


def build_reference_evidence(
    *,
    reference_tag: str,
    reference_commit: str,
    expected_commit: str,
    python_image: str,
    python_image_id: str,
    postgres_image_id: str,
    test_image_id: str,
    dependency_inputs: dict[str, str],
    test_command: tuple[str, ...],
    compile_command: tuple[str, ...],
    summary: TestSummary,
    added_containers: tuple[str, ...],
    added_networks: tuple[str, ...],
    added_volumes: tuple[str, ...],
    cleaned_owned_volumes: tuple[str, ...] = (),
) -> dict[str, object]:
    if not reference_tag or not reference_commit or not expected_commit:
        raise ReferenceEvidenceError("reference identity must be non-empty")
    if reference_commit != expected_commit:
        raise ReferenceEvidenceError("reference tag does not resolve to the expected commit")
    if not summary.successful:
        raise ReferenceEvidenceError("unsuccessful reference runs cannot become baseline evidence")
    return {
        "schema": "cpk.frozen-reference-evidence",
        "reference": {"tag": reference_tag, "commit": reference_commit},
        "runtime": {
            "python_image": {"reference": python_image, "id": python_image_id},
            "postgres_image": {"reference": "postgres:16-alpine", "id": postgres_image_id},
            "test_image": {"id": test_image_id},
        },
        "dependency_inputs": dict(sorted(dependency_inputs.items())),
        "commands": {
            "test": list(test_command),
            "compile": list(compile_command),
        },
        "tests": {
            "run": summary.tests_run,
            "skipped": summary.skipped,
            "successful": summary.successful,
        },
        "unexpected_resource_additions": {
            "containers": list(added_containers),
            "networks": list(added_networks),
            "volumes": list(added_volumes),
        },
        "owned_cleanup": {"volumes": list(cleaned_owned_volumes)},
    }


def write_evidence(path: Path, evidence: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _lines(path: Path) -> tuple[str, ...]:
    return tuple(line for line in path.read_text(encoding="utf-8").splitlines() if line)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-tag", required=True)
    parser.add_argument("--reference-commit", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--python-image", required=True)
    parser.add_argument("--python-image-id", required=True)
    parser.add_argument("--postgres-image-id", required=True)
    parser.add_argument("--test-image-id", required=True)
    parser.add_argument("--dependency-input", type=Path, action="append", required=True)
    parser.add_argument("--test-output", type=Path, required=True)
    parser.add_argument("--maximum-output-bytes", type=int, required=True)
    parser.add_argument("--containers-before", type=Path, required=True)
    parser.add_argument("--containers-after", type=Path, required=True)
    parser.add_argument("--networks-before", type=Path, required=True)
    parser.add_argument("--networks-after", type=Path, required=True)
    parser.add_argument("--volumes-before", type=Path, required=True)
    parser.add_argument("--volumes-after", type=Path, required=True)
    parser.add_argument("--volumes-observed", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()

    output = args.test_output.read_text(encoding="utf-8")
    evidence = build_reference_evidence(
        reference_tag=args.reference_tag,
        reference_commit=args.reference_commit,
        expected_commit=args.expected_commit,
        python_image=args.python_image,
        python_image_id=args.python_image_id,
        postgres_image_id=args.postgres_image_id,
        test_image_id=args.test_image_id,
        dependency_inputs={path.name: sha256_file(path) for path in args.dependency_input},
        test_command=("./test.sh",),
        compile_command=(
            "python",
            "-m",
            "compileall",
            "-q",
            "control_plane_kit",
            "tests",
            "examples",
        ),
        summary=parse_unittest_summary(output, maximum_bytes=args.maximum_output_bytes),
        added_containers=added_resource_ids(_lines(args.containers_before), _lines(args.containers_after)),
        added_networks=added_resource_ids(_lines(args.networks_before), _lines(args.networks_after)),
        added_volumes=added_resource_ids(_lines(args.volumes_before), _lines(args.volumes_after)),
        cleaned_owned_volumes=added_resource_ids(
            _lines(args.volumes_after), _lines(args.volumes_observed)
        ),
    )
    write_evidence(args.evidence, evidence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
