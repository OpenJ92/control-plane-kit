from __future__ import annotations

from dataclasses import replace
import io
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
import subprocess
import unittest
from unittest.mock import patch

from current_backend.runner import (
    BackendGateError,
    GatePlan,
    GateStage,
    STAGE_ORDER,
    build_gate_plan,
    execute_gate,
    _stage_environment,
    CommandOutcome,
    PackageIntegrityEvidence,
    main,
    execute_command,
    _stage_cleanup_status,
    SERVERS_ACCEPTANCE_EVIDENCE,
)
from current_backend.source_lock import (
    ResolvedBackend,
    ResolvedRepository,
    load_backend_lock,
)
from current_backend.tests.test_contracts import _backend_fixture


class BackendGateRunnerTests(unittest.TestCase):
    def test_covered_evidence_requires_executed_normal_path_not_disabled_source_tokens(self) -> None:
        for mode in ("normal", "disabled", "early-exit", "missing", "out-of-order", "nonzero"):
            with self.subTest(mode=mode), _runner_fixture() as fixture:
                fixture.backend_fixture.independent()
                servers = fixture.backend.path_for("control-plane-kit-servers")
                gate = servers / "test.sh"
                body = gate.read_text()
                if mode == "disabled":
                    body = "#!/bin/sh\nif false; then\n" + "\n".join(body.splitlines()[1:-1]) + "\nfi\n" + body.splitlines()[-1] + "\n"
                elif mode == "early-exit":
                    body = "#!/bin/sh\nexit 0\n" + "\n".join(body.splitlines()[1:]) + "\n"
                # These are the existing owner's small command fixtures, not
                # application behavior. Actual subprocess output earns coverage.
                body = body.replace("#!/bin/sh\n", "#!/bin/sh\nset -eu\nIMAGE=unit-controller\necho 'package-integrity contract=1 tests=17 mocks=0 approved_skips=0'\n", 1)
                gate.write_text(body)
                gate.chmod(0o700)
                smoke = servers / "scripts/cpk_server_image_smoke.sh"
                emitted = list(SERVERS_ACCEPTANCE_EVIDENCE[:2])
                if mode == "missing":
                    emitted = emitted[1:]
                elif mode == "out-of-order":
                    emitted.reverse()
                smoke.write_text(smoke.read_text() + "\n" + "\n".join("echo '" + line + "'" for line in emitted) + "\n")
                if mode == "nonzero":
                    gate.write_text(gate.read_text() + "exit 7\n")
                (servers / "scripts/docker_residue_audit.sh").write_text("#!/bin/sh\necho '" + SERVERS_ACCEPTANCE_EVIDENCE[2] + "'\n")
                def execute(stage, cwd, output):
                    if stage.identity == "server-products":
                        return execute_command(stage, cwd, output)
                    return CommandOutcome(0, PackageIntegrityEvidence(17, 0, 0), 1)
                report = execute_gate(runner_root=fixture.root, resolved=fixture._resolved(), backend=fixture.backend, contracts=fixture.contracts, report_path=fixture.report_path, output=io.StringIO(), runner_commit="9" * 40, command_executor=execute, cleanup_observer=lambda stage, outcome: "verified-absent")
                self.assertEqual(report.status, "passed" if mode == "normal" else "failed")
                if mode != "normal":
                    self.assertEqual(report.first_failed_stage, "server-products")
                    self.assertEqual(report.stages[-1].detail, "command exited with status 7" if mode == "nonzero" else "normal Servers acceptance terminal evidence is missing")

    def test_successful_secrets_and_sdk_commands_cannot_hide_uncertain_cleanup(self) -> None:
        for owner in ("secrets", "server-sdk"):
            for status in ("uncertain", "residue"):
                with self.subTest(owner=owner, status=status), _runner_fixture() as fixture:
                    fixture.backend_fixture.independent()
                    def execute(stage, cwd, output):
                        return CommandOutcome(0, PackageIntegrityEvidence(17, 0, 0), 1)
                    report = execute_gate(runner_root=fixture.root, resolved=fixture._resolved(), backend=fixture.backend, contracts=fixture.contracts, report_path=fixture.report_path, output=io.StringIO(), runner_commit="9" * 40, command_executor=execute, cleanup_observer=lambda stage, outcome: status if stage.identity == owner else "verified-absent")
                    self.assertEqual(report.first_failed_stage, owner)
                    self.assertEqual(report.stages[-1].cleanup_status, status)

    def test_cleanup_observer_uses_exact_names_and_refuses_unknown_sdk_pid(self) -> None:
        with _runner_fixture() as fixture:
            fixture.backend_fixture.independent()
            plan = build_gate_plan(fixture.root, fixture.backend, fixture.contracts)
            sdk = next(stage for stage in plan.stages if stage.identity == "server-sdk")
            secrets = next(stage for stage in plan.stages if stage.identity == "secrets")
            unknown = CommandOutcome(0, None, 0)
            self.assertEqual(_stage_cleanup_status(sdk, unknown), "uncertain")
            outcome = replace(unknown, process_id=12345)
            calls = []
            def observe(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, "")
            with patch("current_backend.runner.subprocess.run", side_effect=observe):
                self.assertEqual(_stage_cleanup_status(sdk, outcome), "verified-absent")
            self.assertEqual(len(calls), 4)
            self.assertIn("reference=control-plane-kit-server-sdk-test:12345", calls[-1])
            self.assertIn("name=^/cpk-server-sdk-base-12345$", calls[0])
            def image_residue(command, **kwargs):
                return subprocess.CompletedProcess(command, 0, "control-plane-kit-server-sdk-test:12345\n" if command[1] == "image" else "")
            with patch("current_backend.runner.subprocess.run", side_effect=image_residue):
                self.assertEqual(_stage_cleanup_status(sdk, outcome), "residue")
            name = dict(secrets.environment)["CPK_SECRETS_TEST_CONTAINER"]
            with patch("current_backend.runner.subprocess.run", return_value=subprocess.CompletedProcess([], 0, name + "\n")):
                self.assertEqual(_stage_cleanup_status(secrets, unknown), "residue")
            with patch("current_backend.runner.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "")):
                self.assertEqual(_stage_cleanup_status(secrets, unknown), "uncertain")
            with patch("current_backend.runner.subprocess.run", side_effect=OSError("daemon unavailable")):
                self.assertEqual(_stage_cleanup_status(secrets, unknown), "uncertain")

    def test_modern_plan_uses_selected_packages_and_one_normal_servers_invocation(self) -> None:
        with _runner_fixture() as fixture:
            fixture.backend_fixture.independent()
            plan = build_gate_plan(fixture.root, fixture.backend, fixture.contracts)
            selected = {stage.identity: stage for stage in plan.stages}
            self.assertEqual(selected["core"].repository, "control-plane-kit-core")
            self.assertEqual(selected["operations"].repository, "control-plane-kit-operations")
            self.assertIn("CPK_ARCHITECTURE_TESTING_ROOT", dict(selected["operations"].environment))
            self.assertEqual(dict(selected["server-sdk"].environment)["CPK_CORE_REPO"], fixture.backend.path_for("control-plane-kit-core").as_posix())
            trace = []
            def execute(stage, cwd, output):
                trace.append(stage.identity)
                return CommandOutcome(0, PackageIntegrityEvidence(17, 0, 0), 1, acceptance_evidence=SERVERS_ACCEPTANCE_EVIDENCE)
            report = execute_gate(runner_root=fixture.root, resolved=fixture._resolved(), backend=fixture.backend, contracts=fixture.contracts, report_path=fixture.report_path, output=io.StringIO(), runner_commit="9" * 40, command_executor=execute, cleanup_observer=lambda stage, outcome: "verified-absent")
            self.assertEqual(report.status, "passed")
            self.assertEqual(trace.count("server-products"), 1)
            self.assertNotIn("cpk-server-http-mcp-source-live", trace)
            self.assertNotIn("docker-residue", trace)
            self.assertEqual(report.stages[-1].covered_by, "server-products")
            self.assertIsNone(report.stages[-1].integrity)

    def test_coverage_rejects_early_return_environment_and_unknown_owner(self) -> None:
        with _runner_fixture() as fixture:
            fixture.backend_fixture.independent()
            plan = build_gate_plan(fixture.root, fixture.backend, fixture.contracts)
            owner_index = next(i for i, stage in enumerate(plan.stages) if stage.identity == "server-products")
            cases = [
                (owner_index, replace(plan.stages[owner_index], command=("./test.sh", "--mcp-source-tests"))),
                (owner_index, replace(plan.stages[owner_index], environment=(*plan.stages[owner_index].environment, ("CPK_ROOT_ARTIFACT_MODE", "published-digest")))),
                (-2, replace(plan.stages[-2], covered_by="docker-residue")),
                (-1, replace(plan.stages[-1], covered_by="unknown")),
            ]
            for index, stage in cases:
                with self.subTest(index=index, stage=stage):
                    stages = list(plan.stages)
                    stages[index] = stage
                    with self.assertRaises(BackendGateError):
                        replace(plan, stages=tuple(stages)).validate()

    def test_operations_cleanup_uncertainty_preserves_primary_failure(self) -> None:
        with _runner_fixture() as fixture:
            fixture.backend_fixture.independent()
            def execute(stage, cwd, output):
                return CommandOutcome(7 if stage.identity == "operations" else 0, PackageIntegrityEvidence(17, 0, 0), 1)
            report = execute_gate(runner_root=fixture.root, resolved=fixture._resolved(), backend=fixture.backend, contracts=fixture.contracts, report_path=fixture.report_path, output=io.StringIO(), runner_commit="9" * 40, command_executor=execute, cleanup_observer=lambda stage, outcome: "uncertain")
            self.assertEqual(report.first_failed_stage, "operations")
            self.assertEqual(report.stages[-1].detail, "command exited with status 7")
            self.assertEqual(report.stages[-1].cleanup_status, "uncertain")

    def test_resolution_failure_emits_failed_report_before_any_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / "lock.json"
            lock.write_text('{"schema":"unsupported"}')
            report = root / "report.json"
            self.assertEqual(main(["--lock", str(lock), "--report", str(report)]), 1)
            document = json.loads(report.read_text())
            self.assertEqual(document["status"], "failed")
            self.assertEqual(document["first_failed_stage"], "source-preflight")
            self.assertEqual(document["stages"], [])
            self.assertEqual(document["sources"], {})

    def test_unit_only_refuses_full_report_arguments(self) -> None:
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "not-a-backend-report.json"
            result = subprocess.run((str(root / "current-backend-test.sh"), "--unit-only", "--report", str(report)), capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(report.exists())

    def test_stages_execute_in_exact_order_and_emit_bounded_report(self) -> None:
        with _runner_fixture() as fixture:
            plan = fixture.plan()
            report = fixture.execute(plan)

            self.assertEqual(report.status, "passed")
            self.assertEqual(
                tuple(result.identity for result in report.stages),
                STAGE_ORDER,
            )
            self.assertEqual(
                fixture.trace(),
                tuple(value for value in STAGE_ORDER if value != "cross-repository-contracts"),
            )
            document = fixture.report_document()

        self.assertEqual(document["first_failed_stage"], None)
        self.assertEqual(document["runner_commit"], "9" * 40)
        self.assertEqual(
            document["source_live"],
            {
                "acceptance_level": "source-built",
                "authoritative_caller": "cpk-server",
                "identity": "cpk-server-http-mcp-source-live",
                "provider_mutating": False,
                "published_digest": False,
            },
        )
        self.assertNotIn("fixture-secret-value", json.dumps(document))

    def test_first_failure_stops_later_live_and_mutating_stages(self) -> None:
        with _runner_fixture(fail_stage="operations") as fixture:
            report = fixture.execute(fixture.plan())

            self.assertEqual(report.status, "failed")
            self.assertEqual(report.first_failed_stage, "operations")
            self.assertEqual(
                tuple(result.identity for result in report.stages),
                STAGE_ORDER[:4],
            )
            self.assertNotIn("cpk-server-http-mcp-source-live", fixture.trace())
            self.assertNotIn("docker-residue", fixture.trace())
            self.assertEqual(
                fixture.report_document()["first_failed_stage"],
                "operations",
            )

    def test_counts_are_accepted_only_from_exact_package_integrity_evidence(self) -> None:
        with _runner_fixture() as fixture:
            report = fixture.execute(fixture.plan())
            package = next(result for result in report.stages if result.identity == "core")

        self.assertIsNotNone(package.integrity)
        assert package.integrity is not None
        self.assertEqual(package.integrity.tests, 17)
        self.assertEqual(package.integrity.mocks, 2)
        self.assertEqual(package.integrity.approved_skips, 0)

    def test_missing_or_duplicate_integrity_evidence_fails_package_stage(self) -> None:
        for mode in ("missing", "duplicate"):
            with self.subTest(mode=mode), _runner_fixture(integrity_mode=mode) as fixture:
                report = fixture.execute(fixture.plan())

                self.assertEqual(report.status, "failed")
                self.assertEqual(report.first_failed_stage, "core")
                self.assertEqual(
                    report.stages[-1].detail,
                    "package integrity evidence is missing or ambiguous",
                )

    def test_residue_failure_fails_gate_after_source_live(self) -> None:
        with _runner_fixture(fail_stage="docker-residue") as fixture:
            report = fixture.execute(fixture.plan())

            self.assertEqual(report.status, "failed")
            self.assertEqual(report.first_failed_stage, "docker-residue")
            self.assertIn("cpk-server-http-mcp-source-live", fixture.trace())
            self.assertEqual(fixture.trace()[-1], "docker-residue")

    def test_source_live_cannot_overclaim_or_bypass_cpk_server(self) -> None:
        with _runner_fixture() as fixture:
            plan = fixture.plan()
            source = plan.stages[-2]
            cases = (
                replace(source, provider_mutating=True),
                replace(source, published_digest=True),
                replace(source, acceptance_level="published-digest"),
                replace(source, authoritative_caller="host-script"),
            )
            for changed in cases:
                with self.subTest(changed=changed):
                    stages = (*plan.stages[:-2], changed, plan.stages[-1])
                    with self.assertRaises(BackendGateError):
                        GatePlan(stages=stages).validate()

    def test_mutable_root_test_command_is_rejected(self) -> None:
        with _runner_fixture() as fixture:
            plan = fixture.plan()
            core = replace(
                plan.stages[2],
                working_directory=PurePosixPath("."),
                command=("./test.sh",),
            )
            stages = (*plan.stages[:2], core, *plan.stages[3:])

            with self.assertRaisesRegex(BackendGateError, "mutable root test"):
                GatePlan(stages=stages).validate()

    def test_command_start_failure_writes_failed_report(self) -> None:
        with _runner_fixture() as fixture:
            plan = fixture.plan()
            broken = replace(plan.stages[0], command=("/definitely/missing/command",))
            report = fixture.execute(
                GatePlan(stages=(broken, *plan.stages[1:])),
            )

            self.assertEqual(report.status, "failed")
            self.assertEqual(report.first_failed_stage, "current-backend-unit")
            self.assertEqual(
                fixture.report_document()["first_failed_stage"],
                "current-backend-unit",
            )

    def test_ambient_proof_overrides_and_provider_credentials_are_removed(self) -> None:
        names = {
            "CPK_SERVER_BUILD_IMAGE": "0",
            "CPK_SOME_PROOF_OVERRIDE": "unsafe",
            "OPENJ92_CLOUDFLARE_API_TOKEN": "provider-secret",
            "TUNNEL_TOKEN": "connector-secret",
        }
        previous = {key: os.environ.get(key) for key in names}
        try:
            os.environ.update(names)
            with _runner_fixture() as fixture:
                server_stage = build_gate_plan(
                    fixture.root,
                    fixture.backend,
                    fixture.contracts,
                ).stages[6]
                environment = _stage_environment(server_stage)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertEqual(environment["CPK_SERVER_BUILD_IMAGE"], "1")
        self.assertNotIn("CPK_SOME_PROOF_OVERRIDE", environment)
        self.assertNotIn("OPENJ92_CLOUDFLARE_API_TOKEN", environment)
        self.assertNotIn("TUNNEL_TOKEN", environment)

    def test_shell_and_ci_invoke_only_the_named_current_backend_gate(self) -> None:
        root = Path(__file__).resolve().parents[2]
        shell = (root / "current-backend-test.sh").read_text(encoding="utf-8")
        workflow = (root / ".github/workflows/current-backend.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("python3 -m current_backend.runner", shell)
        self.assertNotIn("./test.sh", shell)
        self.assertIn("./current-backend-test.sh --report", workflow)
        self.assertIn("roadmap/extract-c-external-product-language", workflow)
        self.assertNotIn("cloudflare", workflow.lower())

    def test_docker_resources_are_owned_by_the_stage_that_uses_them(self) -> None:
        with _runner_fixture() as fixture:
            plan = build_gate_plan(
                fixture.root,
                fixture.backend,
                fixture.contracts,
            )

        core = plan.stages[2]
        operations = plan.stages[3]
        server_products = plan.stages[6]
        source_live = plan.stages[7]
        operations_environment = dict(operations.environment)

        self.assertEqual(core.environment, ())
        self.assertIn("CPK_OPERATIONS_TEST_NETWORK_NAME", operations_environment)
        self.assertIn("CPK_OPERATIONS_TEST_POSTGRES_CONTAINER", operations_environment)
        self.assertNotEqual(
            dict(server_products.environment)["CPK_SERVER_IMAGE"],
            dict(source_live.environment)["CPK_SERVER_IMAGE"],
        )
        self.assertTrue(
            dict(source_live.environment)["CPK_SERVER_IMAGE"].endswith("-source-live")
        )


class _RunnerFixture:
    def __init__(
        self,
        directory: str,
        backend_fixture: object,
        *,
        fail_stage: str | None,
        integrity_mode: str,
    ) -> None:
        self.root = Path(directory)
        self.backend_fixture = backend_fixture
        self.fail_stage = fail_stage
        self.integrity_mode = integrity_mode
        self.trace_path = self.root / "trace.txt"
        self.report_path = self.root / "report.json"
        self.script = self.root / "gate.sh"
        self.script.write_text(_gate_script(), encoding="utf-8")
        self.script.chmod(0o700)

    @property
    def backend(self):
        return self.backend_fixture.backend

    @property
    def contracts(self):
        return self.backend_fixture.contracts

    def plan(self) -> GatePlan:
        original = build_gate_plan(self.root, self.backend, self.contracts)
        stages = []
        for stage in original.stages:
            if stage.kind == "contract":
                stages.append(stage)
                continue
            stages.append(
                replace(
                    stage,
                    command=(
                        self.script.as_posix(),
                        stage.identity,
                        self.trace_path.as_posix(),
                        self.fail_stage or "-",
                        self.integrity_mode,
                    ),
                    environment=(),
                )
            )
        plan = GatePlan(stages=tuple(stages))
        plan.validate()
        return plan

    def execute(self, plan: GatePlan):
        return execute_gate(
            runner_root=self.root,
            resolved=self._resolved(),
            backend=self.backend,
            contracts=self.contracts,
            report_path=self.report_path,
            output=io.StringIO(),
            runner_commit="9" * 40,
            plan=plan,
        )

    def trace(self) -> tuple[str, ...]:
        if not self.trace_path.exists():
            return ()
        return tuple(self.trace_path.read_text(encoding="utf-8").splitlines())

    def report_document(self) -> dict[str, object]:
        value = json.loads(self.report_path.read_text(encoding="utf-8"))
        assert isinstance(value, dict)
        return value

    def _resolved(self) -> ResolvedBackend:
        lock = load_backend_lock(Path(__file__).resolve().parents[2] / "current-backend.lock.json")
        repositories = tuple(
            ResolvedRepository(
                name=name,
                identity=f"OpenJ92/{name}",
                url=f"https://github.com/OpenJ92/{name}.git",
                commit=str(index) * 40,
                object_store=path,
            )
            for index, (name, path) in enumerate(
                sorted(self.backend.repositories.items()),
                start=1,
            )
        )
        return ResolvedBackend(lock=lock, repositories=repositories)


class _RunnerFixtureContext:
    def __init__(self, *, fail_stage: str | None, integrity_mode: str) -> None:
        self.fail_stage = fail_stage
        self.integrity_mode = integrity_mode
        self.directory = tempfile.TemporaryDirectory()
        self.backend_context = _backend_fixture()

    def __enter__(self) -> _RunnerFixture:
        backend_fixture = self.backend_context.__enter__()
        return _RunnerFixture(
            self.directory.name,
            backend_fixture,
            fail_stage=self.fail_stage,
            integrity_mode=self.integrity_mode,
        )

    def __exit__(self, *arguments: object) -> None:
        self.backend_context.__exit__(*arguments)
        self.directory.cleanup()


def _runner_fixture(
    *,
    fail_stage: str | None = None,
    integrity_mode: str = "exact",
) -> _RunnerFixtureContext:
    return _RunnerFixtureContext(
        fail_stage=fail_stage,
        integrity_mode=integrity_mode,
    )


def _gate_script() -> str:
    return """#!/bin/sh
set -eu
identity="$1"
trace="$2"
fail="$3"
integrity="$4"
printf '%s\\n' "$identity" >> "$trace"
printf 'fixture-secret-value\\n'
printf 'Ran 999 tests\\n'
if [ "$identity" = "$fail" ]; then
  exit 17
fi
case "$identity" in
  core|operations|interpreters|secrets|server-products)
    if [ "$integrity" != "missing" ]; then
      printf 'package-integrity contract=1 tests=17 mocks=2 approved_skips=0\\n'
    fi
    if [ "$integrity" = "duplicate" ]; then
      printf 'package-integrity contract=1 tests=17 mocks=2 approved_skips=0\\n'
    fi
    ;;
esac
"""


if __name__ == "__main__":
    unittest.main()
