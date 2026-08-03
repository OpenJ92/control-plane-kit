"""Execute the exact, non-provider-mutating current backend validation gate."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
from typing import Callable, Mapping, Sequence, TextIO

from .contracts import (
    AcceptanceContract,
    BackendContractError,
    BackendContractReport,
    BackendContracts,
    load_backend_contracts,
    validate_backend,
)
from .source_lock import (
    BackendLockError,
    MaterializedBackend,
    ResolvedBackend,
    clone_backend,
    load_backend_lock,
    materialize_backend,
    resolve_local_backend,
)


REPORT_SCHEMA = "cpk.current-backend-gate-report.v1"
STAGE_ORDER = (
    "current-backend-unit",
    "cross-repository-contracts",
    "core",
    "operations",
    "interpreters",
    "secrets",
    "server-products",
    "cpk-server-http-mcp-source-live",
    "docker-residue",
)
_INTEGRITY = re.compile(
    r"^package-integrity contract=1 tests=(?P<tests>[0-9]+) "
    r"mocks=(?P<mocks>[0-9]+) approved_skips=(?P<skips>[0-9]+)$"
)
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_BLOCKED_ENVIRONMENT_NAMES = frozenset(
    {
        "CLOUDFLARE_API_TOKEN",
        "OPENJ92_CLOUDFLARE_API_TOKEN",
        "OPENJ92_CLOUDFLARE_ACCOUNT_ID",
        "OPENJ92_CLOUDFLARE_ZONE_ID",
        "TUNNEL_TOKEN",
    }
)


class BackendGateError(RuntimeError):
    """The backend gate definition or execution is invalid."""


@dataclass(frozen=True)
class PackageIntegrityEvidence:
    tests: int
    mocks: int
    approved_skips: int

    def to_document(self) -> dict[str, int]:
        return {
            "tests": self.tests,
            "mocks": self.mocks,
            "approved_skips": self.approved_skips,
        }


@dataclass(frozen=True)
class GateStage:
    identity: str
    kind: str
    repository: str
    working_directory: PurePosixPath
    command: tuple[str, ...]
    environment: tuple[tuple[str, str], ...] = ()
    acceptance_level: str | None = None
    authoritative_caller: str | None = None
    provider_mutating: bool = False
    published_digest: bool = False

    def __post_init__(self) -> None:
        if not self.identity or not self.identity.isascii() or len(self.identity) > 120:
            raise BackendGateError("stage identity must be bounded ASCII")
        if self.kind not in {"unit", "contract", "package", "source-live", "residue"}:
            raise BackendGateError(f"stage kind is unsupported: {self.identity}")
        if not self.repository or not self.repository.isascii():
            raise BackendGateError(f"stage repository is malformed: {self.identity}")
        if (
            self.working_directory.is_absolute()
            or ".." in self.working_directory.parts
        ):
            raise BackendGateError(f"stage working directory is unsafe: {self.identity}")
        if not self.command or not all(
            isinstance(value, str) and value and value.isascii() and len(value) <= 300
            for value in self.command
        ):
            raise BackendGateError(f"stage command is malformed: {self.identity}")
        if len(self.command) > 16:
            raise BackendGateError(f"stage command is unbounded: {self.identity}")
        keys = tuple(key for key, _value in self.environment)
        if len(keys) != len(set(keys)):
            raise BackendGateError(f"stage environment has duplicate keys: {self.identity}")
        for key, value in self.environment:
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", key):
                raise BackendGateError(f"stage environment key is malformed: {self.identity}")
            if not value.isascii() or len(value) > 500:
                raise BackendGateError(f"stage environment value is unbounded: {self.identity}")
            if (
                key in _BLOCKED_ENVIRONMENT_NAMES
                or key.startswith("CPK_CLOUDFLARE_")
                or re.search(
                    r"(?:^|_)(?:TOKEN|SECRET|PASSWORD|CREDENTIAL)(?:_|$)",
                    key,
                )
            ):
                raise BackendGateError(f"stage environment cannot carry secret material: {self.identity}")


@dataclass(frozen=True)
class GatePlan:
    stages: tuple[GateStage, ...]

    def validate(self) -> None:
        identities = tuple(stage.identity for stage in self.stages)
        if identities != STAGE_ORDER:
            raise BackendGateError("backend stage order is inconsistent")
        for stage in self.stages:
            if stage.repository == "control-plane-kit" and not stage.working_directory.parts:
                if any(PurePosixPath(value).name == "test.sh" for value in stage.command):
                    raise BackendGateError("mutable root test command is forbidden")
            if stage.provider_mutating:
                raise BackendGateError(f"provider-mutating stage is forbidden: {stage.identity}")
        source_live = self.stages[-2]
        if (
            source_live.kind != "source-live"
            or source_live.acceptance_level != "source-built"
            or source_live.published_digest
        ):
            raise BackendGateError("source-live evidence classification is inconsistent")
        if source_live.authoritative_caller != "cpk-server":
            raise BackendGateError("source-live stage bypasses cpk-server")
        if self.stages[-1].kind != "residue":
            raise BackendGateError("Docker residue must be the final stage")


@dataclass(frozen=True)
class CommandOutcome:
    exit_code: int
    integrity: PackageIntegrityEvidence | None
    integrity_lines: int


@dataclass(frozen=True)
class StageResult:
    identity: str
    kind: str
    repository: str
    working_directory: str
    command: tuple[str, ...]
    duration_ms: int
    status: str
    detail: str | None = None
    integrity: PackageIntegrityEvidence | None = None
    metrics: Mapping[str, int] | None = None

    def to_document(self) -> dict[str, object]:
        value: dict[str, object] = {
            "identity": self.identity,
            "kind": self.kind,
            "repository": self.repository,
            "working_directory": self.working_directory,
            "command": list(self.command),
            "duration_ms": self.duration_ms,
            "status": self.status,
        }
        if self.detail is not None:
            value["detail"] = self.detail
        if self.integrity is not None:
            value["package_integrity"] = self.integrity.to_document()
        if self.metrics is not None:
            value["metrics"] = dict(sorted(self.metrics.items()))
        return value


@dataclass(frozen=True)
class BackendGateReport:
    runner_commit: str
    started_at: str
    finished_at: str
    status: str
    first_failed_stage: str | None
    sources: Mapping[str, str]
    stages: tuple[StageResult, ...]
    source_live_identity: str
    source_live_acceptance_level: str

    def to_document(self) -> dict[str, object]:
        return {
            "schema": REPORT_SCHEMA,
            "runner_commit": self.runner_commit,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "first_failed_stage": self.first_failed_stage,
            "sources": dict(sorted(self.sources.items())),
            "stages": [stage.to_document() for stage in self.stages],
            "source_live": {
                "identity": self.source_live_identity,
                "acceptance_level": self.source_live_acceptance_level,
                "authoritative_caller": "cpk-server",
                "provider_mutating": False,
                "published_digest": False,
            },
        }


CommandExecutor = Callable[[GateStage, Path, TextIO], CommandOutcome]


def build_gate_plan(
    runner_root: Path,
    backend: MaterializedBackend,
    contracts: BackendContracts,
) -> GatePlan:
    del runner_root
    run_token = str(os.getpid())
    coordination = backend.path_for("control-plane-kit")
    interpreters = backend.path_for("control-plane-kit-interpreters")
    secrets = backend.path_for("control-plane-kit-secrets")
    servers = backend.path_for("control-plane-kit-servers")
    acceptance = _source_live_contract(contracts)
    plan = GatePlan(
        stages=(
            GateStage(
                identity="current-backend-unit",
                kind="unit",
                repository="runner",
                working_directory=PurePosixPath("."),
                command=(
                    sys.executable,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "current_backend/tests",
                    "-t",
                    ".",
                ),
            ),
            GateStage(
                identity="cross-repository-contracts",
                kind="contract",
                repository="all",
                working_directory=PurePosixPath("."),
                command=("internal:validate-backend",),
            ),
            GateStage(
                identity="core",
                kind="package",
                repository="control-plane-kit",
                working_directory=PurePosixPath("control-plane-kit-core"),
                command=("./test.sh",),
            ),
            GateStage(
                identity="operations",
                kind="package",
                repository="control-plane-kit",
                working_directory=PurePosixPath("control-plane-kit-operations"),
                command=("./test.sh",),
                environment=(
                    ("CPK_OPERATIONS_TEST_NETWORK_NAME", f"cpk-current-backend-{run_token}-operations"),
                    ("CPK_OPERATIONS_TEST_POSTGRES_CONTAINER", f"cpk-current-backend-{run_token}-postgres"),
                ),
            ),
            GateStage(
                identity="interpreters",
                kind="package",
                repository="control-plane-kit-interpreters",
                working_directory=PurePosixPath("."),
                command=("./test.sh",),
                environment=(
                    ("CPK_INTERPRETERS_DEPENDENCY_MODE", "local-core"),
                    ("CPK_CORE_REPO", coordination.as_posix()),
                    ("CPK_INTERPRETERS_TEST_IMAGE_NAME", f"control-plane-kit-interpreters-test:current-backend-{run_token}"),
                    ("CPK_INTERPRETERS_TEST_CONTAINER", f"cpk-current-backend-{run_token}-interpreters"),
                ),
            ),
            GateStage(
                identity="secrets",
                kind="package",
                repository="control-plane-kit-secrets",
                working_directory=PurePosixPath("."),
                command=("./test.sh",),
                environment=(
                    ("CPK_SECRETS_TEST_IMAGE_NAME", f"control-plane-kit-secrets-test:current-backend-{run_token}"),
                    ("CPK_SECRETS_TEST_CONTAINER", f"cpk-current-backend-{run_token}-secrets"),
                ),
            ),
            GateStage(
                identity="server-products",
                kind="package",
                repository="control-plane-kit-servers",
                working_directory=PurePosixPath("."),
                command=("./test.sh",),
                environment=(
                    ("CPK_SERVERS_TEST_IMAGE", f"control-plane-kit-servers-test:current-backend-{run_token}"),
                    ("CPK_SERVER_IMAGE", f"localhost/control-plane-kit-servers/cpk-server:current-backend-{run_token}"),
                    ("CPK_SERVER_BUILD_IMAGE", "1"),
                ),
            ),
            GateStage(
                identity=acceptance.name,
                kind="source-live",
                repository=acceptance.repository,
                working_directory=PurePosixPath("."),
                command=("sh", acceptance.command_path.as_posix()),
                acceptance_level=acceptance.classification,
                authoritative_caller=acceptance.authoritative_caller,
                provider_mutating=acceptance.provider_mutating,
                published_digest=acceptance.published_digest,
                environment=(
                    ("CPK_SERVER_IMAGE", f"localhost/control-plane-kit-servers/cpk-server:current-backend-{run_token}-source-live"),
                    ("CPK_SERVER_BUILD_IMAGE", "1"),
                ),
            ),
            GateStage(
                identity="docker-residue",
                kind="residue",
                repository=acceptance.repository,
                working_directory=PurePosixPath("."),
                command=("sh", acceptance.residue_command_path.as_posix()),
            ),
        )
    )
    plan.validate()
    for path in (coordination, interpreters, secrets, servers):
        if not path.is_dir():
            raise BackendGateError("materialized backend path is unavailable")
    return plan


def execute_gate(
    *,
    runner_root: Path,
    resolved: ResolvedBackend,
    backend: MaterializedBackend,
    contracts: BackendContracts,
    report_path: Path,
    output: TextIO = sys.stdout,
    command_executor: CommandExecutor | None = None,
    runner_commit: str | None = None,
    plan: GatePlan | None = None,
) -> BackendGateReport:
    plan = plan or build_gate_plan(runner_root, backend, contracts)
    plan.validate()
    executor = command_executor or execute_command
    started_at = _utc_now()
    results: list[StageResult] = []
    first_failed: str | None = None
    contract_report: BackendContractReport | None = None

    for stage in plan.stages:
        print(f"\n=== current-backend stage: {stage.identity} ===", file=output, flush=True)
        started = time.monotonic()
        if stage.kind == "contract":
            try:
                contract_report = validate_backend(backend, contracts)
            except BackendContractError as error:
                print(f"contract validation failed: {error}", file=output, flush=True)
                duration = _duration_ms(started)
                result = StageResult(
                    identity=stage.identity,
                    kind=stage.kind,
                    repository=stage.repository,
                    working_directory=stage.working_directory.as_posix(),
                    command=stage.command,
                    duration_ms=duration,
                    status="failed",
                    detail="cross-repository contract validation failed",
                )
            else:
                result = StageResult(
                    identity=stage.identity,
                    kind=stage.kind,
                    repository=stage.repository,
                    working_directory=stage.working_directory.as_posix(),
                    command=stage.command,
                    duration_ms=_duration_ms(started),
                    status="passed",
                    metrics={
                        "source_files": contract_report.source_files,
                        "products": len(contract_report.products),
                        "protocols": len(contract_report.protocols),
                    },
                )
        else:
            cwd = _stage_cwd(stage, runner_root, backend)
            try:
                outcome = executor(stage, cwd, output)
            except OSError:
                outcome = CommandOutcome(
                    exit_code=127,
                    integrity=None,
                    integrity_lines=0,
                )
            detail: str | None = None
            status = "passed" if outcome.exit_code == 0 else "failed"
            if stage.kind == "package" and outcome.integrity_lines != 1:
                status = "failed"
                detail = "package integrity evidence is missing or ambiguous"
            elif outcome.exit_code != 0:
                detail = f"command exited with status {outcome.exit_code}"
            result = StageResult(
                identity=stage.identity,
                kind=stage.kind,
                repository=stage.repository,
                working_directory=stage.working_directory.as_posix(),
                command=stage.command,
                duration_ms=_duration_ms(started),
                status=status,
                detail=detail,
                integrity=outcome.integrity if stage.kind == "package" else None,
            )
        results.append(result)
        if result.status != "passed":
            first_failed = stage.identity
            break

    source_live = plan.stages[-2]
    report = BackendGateReport(
        runner_commit=runner_commit or _runner_commit(runner_root),
        started_at=started_at,
        finished_at=_utc_now(),
        status="passed" if first_failed is None else "failed",
        first_failed_stage=first_failed,
        sources={value.name: value.commit for value in resolved.repositories},
        stages=tuple(results),
        source_live_identity=source_live.identity,
        source_live_acceptance_level=source_live.acceptance_level or "",
    )
    write_report(report_path, report)
    print(
        f"current-backend status={report.status} report={report_path}",
        file=output,
        flush=True,
    )
    return report


def execute_command(stage: GateStage, cwd: Path, output: TextIO) -> CommandOutcome:
    environment = _stage_environment(stage)
    process = subprocess.Popen(
        stage.command,
        cwd=cwd,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    evidence: list[PackageIntegrityEvidence] = []
    with process.stdout:
        for line in process.stdout:
            print(line, end="", file=output, flush=True)
            match = _INTEGRITY.fullmatch(line.rstrip("\r\n"))
            if match is not None:
                evidence.append(
                    PackageIntegrityEvidence(
                        tests=int(match.group("tests")),
                        mocks=int(match.group("mocks")),
                        approved_skips=int(match.group("skips")),
                    )
                )
    exit_code = process.wait()
    return CommandOutcome(
        exit_code=exit_code,
        integrity=evidence[0] if len(evidence) == 1 else None,
        integrity_lines=len(evidence),
    )


def write_report(path: Path, report: BackendGateReport) -> None:
    document = json.dumps(report.to_document(), indent=2, sort_keys=True) + "\n"
    if len(document.encode("utf-8")) > 128 * 1024:
        raise BackendGateError("backend gate report exceeds bounded size")
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(document, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("/tmp/control-plane-kit-current-backend-report.json"),
    )
    parser.add_argument(
        "--local-repository",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="read exact Git objects from all four local repositories instead of cloning",
    )
    options = parser.parse_args(arguments)
    runner_root = Path(__file__).resolve().parents[1]
    lock = load_backend_lock(runner_root / "current-backend.lock.json")
    contracts = load_backend_contracts(runner_root / "current-backend.contracts.json")

    if options.local_repository:
        repositories = _local_repositories(options.local_repository)
        resolved = resolve_local_backend(lock, repositories)
        with materialize_backend(resolved) as backend:
            report = execute_gate(
                runner_root=runner_root,
                resolved=resolved,
                backend=backend,
                contracts=contracts,
                report_path=options.report,
            )
    else:
        cloned = clone_backend(lock)
        with cloned, materialize_backend(cloned.backend) as backend:
            report = execute_gate(
                runner_root=runner_root,
                resolved=cloned.backend,
                backend=backend,
                contracts=contracts,
                report_path=options.report,
            )
    return 0 if report.status == "passed" else 1


def _source_live_contract(contracts: BackendContracts) -> AcceptanceContract:
    matches = tuple(
        value
        for value in contracts.acceptance
        if value.name == "cpk-server-http-mcp-source-live"
    )
    if len(matches) != 1:
        raise BackendGateError("source-live acceptance contract is not unique")
    return matches[0]


def _stage_cwd(
    stage: GateStage,
    runner_root: Path,
    backend: MaterializedBackend,
) -> Path:
    root = runner_root if stage.repository == "runner" else backend.path_for(stage.repository)
    path = (root / Path(*stage.working_directory.parts)).resolve()
    if root.resolve() not in (path, *path.parents) or not path.is_dir():
        raise BackendGateError(f"stage working directory is unavailable: {stage.identity}")
    return path


def _stage_environment(stage: GateStage) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in _BLOCKED_ENVIRONMENT_NAMES
        and not key.startswith("CPK_CLOUDFLARE_")
        and not key.startswith("CPK_")
        and not key.startswith("OPENJ92_")
    }
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment.update(stage.environment)
    return environment


def _local_repositories(values: Sequence[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or not name or not raw_path or name in result:
            raise BackendGateError("local repository argument is malformed or duplicate")
        result[name] = Path(raw_path)
    return result


def _runner_commit(root: Path) -> str:
    result = subprocess.run(
        ("git", "-C", root.as_posix(), "rev-parse", "HEAD"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    commit = result.stdout.strip()
    if result.returncode != 0 or not _COMMIT.fullmatch(commit):
        raise BackendGateError("runner Git commit is unavailable")
    return commit


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BackendGateError, BackendLockError, BackendContractError) as error:
        print(f"current-backend failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
