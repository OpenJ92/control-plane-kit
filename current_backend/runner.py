"""Execute the exact, non-provider-mutating current backend validation gate."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
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
    PACKAGE_CONTRACT_SCHEMA,
)
from .source_lock import (
    BackendLockError,
    BackendCleanupError,
    PACKAGE_LOCK_SCHEMA,
    ARCHITECTURE_ROLE,
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
PACKAGE_STAGE_ORDER = (*STAGE_ORDER[:4], "server-sdk", *STAGE_ORDER[4:])
SERVERS_ACCEPTANCE_EVIDENCE = (
    "receiver acceptance: normal installed dependency provenance passed",
    "cpk-server image smoke passed",
    "control-plane-kit-servers Docker residue audit passed",
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
    covered_by: str | None = None

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
        if (not self.command and self.covered_by is None) or not all(
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
    independent_packages: bool = False

    def validate(self) -> None:
        identities = tuple(stage.identity for stage in self.stages)
        if identities != (PACKAGE_STAGE_ORDER if self.independent_packages else STAGE_ORDER):
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
        covered = [stage for stage in self.stages if stage.covered_by is not None]
        if self.independent_packages:
            owner = next(stage for stage in self.stages if stage.identity == "server-products")
            if covered != list(self.stages[-2:]) or any(stage.covered_by != owner.identity or stage.command or stage.repository != owner.repository for stage in covered):
                raise BackendGateError("Servers acceptance coverage is inconsistent")
            expected_environment = {"CPK_SERVERS_TEST_IMAGE", "CPK_SERVER_IMAGE", "CPK_SERVER_BUILD_IMAGE"}
            if (owner.command != ("./test.sh",) or owner.repository != "control-plane-kit-servers" or owner.kind != "package" or owner.working_directory != PurePosixPath(".") or set(dict(owner.environment)) != expected_environment or dict(owner.environment)["CPK_SERVER_BUILD_IMAGE"] != "1"):
                raise BackendGateError("coverage requires the normal full Servers gate")
        elif covered:
            raise BackendGateError("legacy stages cannot claim covered evidence")


@dataclass(frozen=True)
class CommandOutcome:
    exit_code: int
    integrity: PackageIntegrityEvidence | None
    integrity_lines: int
    process_id: int | None = None
    acceptance_evidence: tuple[str, ...] = ()


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
    covered_by: str | None = None
    cleanup_status: str | None = None
    process_id: int | None = None
    acceptance_evidence: tuple[str, ...] = ()

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
        if self.covered_by is not None:
            value["covered_by"] = self.covered_by
            value["execution_identity"] = self.covered_by
        else:
            value["execution_identity"] = self.identity
        if self.cleanup_status is not None:
            value["cleanup_status"] = self.cleanup_status
        if self.process_id is not None:
            value["process_id"] = self.process_id
        if self.acceptance_evidence:
            value["acceptance_evidence"] = list(self.acceptance_evidence)
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
    source_details: Mapping[str, object] | None = None

    def to_document(self) -> dict[str, object]:
        return {
            "schema": REPORT_SCHEMA,
            "runner_commit": self.runner_commit,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "first_failed_stage": self.first_failed_stage,
            "sources": dict(sorted(self.sources.items())),
            "source_details": self.source_details,
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
    modern = backend.schema == PACKAGE_LOCK_SCHEMA
    if modern != (contracts.schema == PACKAGE_CONTRACT_SCHEMA):
        raise BackendGateError("lock and contract profiles do not match")
    core_role = "control-plane-kit-core" if modern else "control-plane-kit"
    operations_role = "control-plane-kit-operations" if modern else "control-plane-kit"
    coordination = backend.path_for(core_role)
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
                    "./current-backend-test.sh", "--unit-only",
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
                repository=core_role,
                working_directory=PurePosixPath("control-plane-kit-core"),
                command=("./test.sh",),
            ),
            GateStage(
                identity="operations",
                kind="package",
                repository=operations_role,
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
    if modern:
        prerequisite = backend.path_for(ARCHITECTURE_ROLE)
        sdk = backend.path_for("control-plane-kit-server-sdk")
        if not prerequisite.is_dir() or not sdk.is_dir():
            raise BackendGateError("package prerequisite path is unavailable")
        if acceptance.covered_by != "server-products":
            raise BackendGateError("modern acceptance must use its normal Servers owner")
        stages = list(plan.stages)
        stages[3] = replace(stages[3], environment=(*stages[3].environment, ("CPK_ARCHITECTURE_TESTING_ROOT", prerequisite.as_posix())))
        stages.insert(4, GateStage(identity="server-sdk", kind="package", repository="control-plane-kit-server-sdk", working_directory=PurePosixPath("."), command=("./test.sh",), environment=(("CPK_SERVER_SDK_DEPENDENCY_MODE", "local-core"), ("CPK_CORE_REPO", coordination.as_posix()))))
        stages[-2] = replace(stages[-2], command=(), environment=(), covered_by="server-products")
        stages[-1] = replace(stages[-1], command=(), environment=(), covered_by="server-products")
        plan = GatePlan(stages=tuple(stages), independent_packages=True)
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
    finalize_report: bool = True,
    cleanup_observer: Callable[[GateStage, CommandOutcome], str] | None = None,
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
        if stage.covered_by is not None:
            owner = next((value for value in results if value.identity == stage.covered_by), None)
            if owner is None or owner.status != "passed":
                raise BackendGateError("covering invocation did not pass")
            result = StageResult(identity=stage.identity, kind=stage.kind, repository=stage.repository, working_directory=stage.working_directory.as_posix(), command=(), duration_ms=0, status="passed", covered_by=stage.covered_by, detail="performed by the same normal full Servers invocation")
        elif stage.kind == "contract":
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
            elif plan.independent_packages and stage.identity == "server-products" and outcome.acceptance_evidence != SERVERS_ACCEPTANCE_EVIDENCE:
                status = "failed"
                detail = "normal Servers acceptance terminal evidence is missing"
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
                process_id=outcome.process_id,
                acceptance_evidence=outcome.acceptance_evidence,
            )
            if plan.independent_packages and stage.identity in {"operations", "server-sdk", "secrets"}:
                cleanup = (cleanup_observer or _stage_cleanup_status)(stage, outcome)
                result = replace(result, cleanup_status=cleanup)
                if cleanup != "verified-absent":
                    result = replace(result, status="failed", detail=result.detail or "owning resource cleanup is failed or uncertain")
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
        source_details=resolved.to_document(),
    )
    if finalize_report:
        write_report(report_path, report)
    print(
        f"current-backend {'status' if finalize_report else 'stages-status'}={report.status} {'report=' + str(report_path) if finalize_report else 'source-cleanup=pending'}",
        file=output,
        flush=True,
    )
    return report


def _stage_cleanup_status(stage: GateStage, outcome: CommandOutcome) -> str:
    environment = dict(stage.environment)
    if stage.identity == "operations":
        targets = (("container", environment["CPK_OPERATIONS_TEST_POSTGRES_CONTAINER"]), ("network", environment["CPK_OPERATIONS_TEST_NETWORK_NAME"]))
    elif stage.identity == "secrets":
        targets = (("container", environment["CPK_SECRETS_TEST_CONTAINER"]),)
    elif stage.identity == "server-sdk":
        # Selected SDK's normal executable uses RUN_ID=$$; Popen's exec'd script
        # PID is that shell identity. Do not infer it from a parent PID or scan.
        pid = outcome.process_id
        if type(pid) is not int or pid <= 0:
            return "uncertain"
        targets = tuple(("container", f"cpk-server-sdk-{variant}-{pid}") for variant in ("base", "verification", "fastapi")) + (("image", f"control-plane-kit-server-sdk-test:{pid}"),)
    else:
        return "uncertain"
    for kind, name in targets:
        if kind == "container":
            command = ("docker", "container", "ls", "-a", "--filter", f"name=^/{name}$", "--format", "{{.Names}}")
        elif kind == "network":
            command = ("docker", "network", "ls", "--filter", f"name=^{name}$", "--format", "{{.Name}}")
        else:
            command = ("docker", "image", "ls", "--filter", f"reference={name}", "--format", "{{.Repository}}:{{.Tag}}")
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return "uncertain"
        if result.returncode != 0:
            return "uncertain"
        if name in result.stdout.splitlines():
            return "residue"
    return "verified-absent"


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
    acceptance_phase = 0
    last_line = ""
    with process.stdout:
        for line in process.stdout:
            print(line, end="", file=output, flush=True)
            last_line = line.rstrip("\r\n")
            if stage.identity == "server-products":
                if acceptance_phase == 0 and last_line == SERVERS_ACCEPTANCE_EVIDENCE[0]:
                    acceptance_phase = 1
                elif acceptance_phase == 1 and last_line == SERVERS_ACCEPTANCE_EVIDENCE[1]:
                    acceptance_phase = 2
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
        process_id=process.pid,
        acceptance_evidence=SERVERS_ACCEPTANCE_EVIDENCE if acceptance_phase == 2 and last_line == SERVERS_ACCEPTANCE_EVIDENCE[2] else (),
    )


def write_report(path: Path, report: BackendGateReport) -> None:
    _write_document(path, report.to_document())


def _write_document(path: Path, value: Mapping[str, object]) -> None:
    document = json.dumps(value, indent=2, sort_keys=True) + "\n"
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
        help="read exact Git objects for every declared source role instead of cloning",
    )
    parser.add_argument("--lock", type=Path)
    parser.add_argument("--contracts", type=Path)
    options = parser.parse_args(arguments)
    runner_root = Path(__file__).resolve().parents[1]
    report: BackendGateReport | None = None
    resolved: ResolvedBackend | None = None
    started = _utc_now()
    try:
        lock = load_backend_lock(options.lock or runner_root / "current-backend.lock.json")
        contracts = load_backend_contracts(options.contracts or runner_root / "current-backend.contracts.json")
        if (lock.schema == PACKAGE_LOCK_SCHEMA) != (contracts.schema == PACKAGE_CONTRACT_SCHEMA):
            raise BackendGateError("lock and contract profiles do not match")

        def run(selected: ResolvedBackend) -> BackendGateReport:
            nonlocal resolved, report
            resolved = selected
            with materialize_backend(selected) as backend:
                report = execute_gate(runner_root=runner_root, resolved=selected, backend=backend, contracts=contracts, report_path=options.report, finalize_report=False)
            return report

        if options.local_repository:
            run(resolve_local_backend(lock, _local_repositories(options.local_repository)))
        else:
            with clone_backend(lock) as cloned:
                run(cloned.backend)
        assert report is not None
        document = report.to_document()
        document["source_cleanup"] = "verified-absent"
    except (BackendLockError, BackendContractError, BackendGateError, OSError) as error:
        document = report.to_document() if report is not None else {
            "schema": REPORT_SCHEMA, "runner_commit": _runner_commit(runner_root),
            "started_at": started, "finished_at": _utc_now(), "stages": [],
            "sources": {source.name: source.commit for source in resolved.repositories} if resolved else {},
            "source_details": resolved.to_document() if resolved else None,
            "first_failed_stage": "source-preflight",
        }
        document["status"] = "failed"
        if not document.get("first_failed_stage"):
            document["first_failed_stage"] = "source-cleanup" if isinstance(error, BackendCleanupError) else "source-preflight"
        document["source_cleanup"] = "uncertain" if isinstance(error, BackendCleanupError) else "not-confirmed"
        document["failure_category"] = type(error).__name__
        # Fixed type identities preserve the primary cause without exception bodies.
        chain = []
        cause: BaseException | None = error
        while cause is not None and len(chain) < 8 and id(cause) not in [item[0] for item in chain]:
            chain.append((id(cause), type(cause).__name__))
            cause = cause.__context__
        document["failure_chain"] = [name for _identity, name in chain]
    document["finished_at"] = _utc_now()
    _write_document(options.report, document)
    print(f"current-backend status={document['status']} report={options.report}", flush=True)
    return 0 if document["status"] == "passed" else 1


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
