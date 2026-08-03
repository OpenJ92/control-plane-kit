from __future__ import annotations

from dataclasses import replace
import io
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
import unittest

from current_backend.runner import (
    BackendGateError,
    GatePlan,
    GateStage,
    STAGE_ORDER,
    build_gate_plan,
    execute_gate,
    _stage_environment,
)
from current_backend.source_lock import (
    ResolvedBackend,
    ResolvedRepository,
    load_backend_lock,
)
from current_backend.tests.test_contracts import _backend_fixture


class BackendGateRunnerTests(unittest.TestCase):
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
