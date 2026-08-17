import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PACKAGE_ROOT.parent
ARCHITECTURE_TESTING_COMMIT = "7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef"


def _workflow_step(job: str, name: str) -> str:
    marker = f"      - name: {name}\n"
    start = job.find(marker)
    if start == -1:
        return ""
    end = job.find("\n      - name: ", start + len(marker))
    return job[start:] if end == -1 else job[start:end]


class OperationsPackageGateTests(unittest.TestCase):
    def test_ci_checks_out_exact_architecture_testing_source(self) -> None:
        workflow = (REPOSITORY_ROOT / ".github/workflows/tests.yml").read_text(
            encoding="utf-8"
        )
        core_job, operations_job = workflow.split("\n  operations:\n", 1)
        architecture_checkout = _workflow_step(
            operations_job,
            "Check out architecture testing",
        )
        operations_gate = _workflow_step(
            operations_job,
            "Run authoritative package gate",
        )

        self.assertEqual(
            {
                "repository": (
                    "repository: OpenJ92/control-plane-kit-architecture-testing"
                    in architecture_checkout
                ),
                "ref": f"ref: {ARCHITECTURE_TESTING_COMMIT}" in architecture_checkout,
                "path": "path: architecture-testing" in architecture_checkout,
                "credentials": (
                    "persist-credentials: false" in architecture_checkout
                ),
                "gate_root": (
                    "CPK_ARCHITECTURE_TESTING_ROOT: "
                    "${{ github.workspace }}/architecture-testing"
                    in operations_gate
                ),
                "core_absent": (
                    "control-plane-kit-architecture-testing" not in core_job
                ),
            },
            {
                "repository": True,
                "ref": True,
                "path": True,
                "credentials": True,
                "gate_root": True,
                "core_absent": True,
            },
        )

    def test_behavior_container_alone_receives_architecture_testing(self) -> None:
        gate = (PACKAGE_ROOT / "test.sh").read_text(encoding="utf-8")
        result, events = self._run_gate()
        run_arguments = {
            phase: arguments
            for event in events
            if event.startswith("run:")
            for _, phase, arguments in (event.split(":", 2),)
        }
        phase_exposure = tuple(
            (
                phase,
                ":/architecture-testing:ro" in run_arguments[phase],
                "PYTHONPATH=/architecture-testing/src" in run_arguments[phase],
            )
            for phase in (
                "integrity",
                "postgres",
                "unittest",
                "compile",
                "clean-import",
            )
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (
                ARCHITECTURE_TESTING_COMMIT in gate,
                gate.count("/architecture-testing:ro"),
                gate.count("PYTHONPATH=/architecture-testing/src"),
                "git+https" not in gate,
                "pypi" not in gate.lower(),
                phase_exposure,
            ),
            (
                True,
                1,
                1,
                True,
                True,
                (
                    ("integrity", False, False),
                    ("postgres", False, False),
                    ("unittest", True, True),
                    ("compile", False, False),
                    ("clean-import", False, False),
                ),
            ),
        )

    def test_architecture_testing_checkout_rejects_drift_before_test_dispatch(self) -> None:
        for commit, dirty in (
            ("f" * 40, False),
            (ARCHITECTURE_TESTING_COMMIT, True),
        ):
            with self.subTest(commit=commit, dirty=dirty):
                result, events = self._run_gate(
                    architecture_commit=commit,
                    architecture_dirty=dirty,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(
                    [event for event in events if event.startswith("phase:")],
                    [],
                )

    def test_clean_import_excludes_architecture_testing(self) -> None:
        gate = (PACKAGE_ROOT / "test.sh").read_text(encoding="utf-8")

        self.assertIn(
            'find_spec("control_plane_kit_architecture_testing") is None',
            gate,
        )

    def test_production_packages_exclude_architecture_testing(self) -> None:
        dependency = "control-plane-kit-architecture-testing"
        for package in ("control-plane-kit-core", "control-plane-kit-operations"):
            with self.subTest(package=package):
                metadata = (REPOSITORY_ROOT / package / "pyproject.toml").read_text(
                    encoding="utf-8"
                )
                self.assertNotIn(dependency, metadata)
                for source in (REPOSITORY_ROOT / package / "src").rglob("*.py"):
                    self.assertNotIn(
                        "control_plane_kit_architecture_testing",
                        source.read_text(encoding="utf-8"),
                    )

        core_gate = (
            REPOSITORY_ROOT / "control-plane-kit-core/test.sh"
        ).read_text(encoding="utf-8")
        self.assertNotIn("architecture-testing", core_gate)

    def test_postgres_fixture_uses_ephemeral_storage_and_volume_cleanup(self) -> None:
        gate = (PACKAGE_ROOT / "test.sh").read_text(encoding="utf-8")

        self.assertIn('docker rm -fv "$POSTGRES_CONTAINER"', gate)
        self.assertIn(
            "--tmpfs /var/lib/postgresql/data:rw,noexec,nosuid,size=2g",
            gate,
        )

    def test_postgres_fixture_has_bounded_wal_and_checkpoint_policy(self) -> None:
        result, events = self._run_gate()

        self.assertEqual(result.returncode, 0, result.stderr)
        launch = tuple(
            event.removeprefix("launch:")
            for event in events
            if event.startswith("launch:")
        )
        self.assertEqual(
            launch,
            (
                "run",
                "-d",
                "--name",
                "cpk-operations-test-postgres",
                "--network",
                "cpk-operations-test",
                "--tmpfs",
                "/var/lib/postgresql/data:rw,noexec,nosuid,size=2g",
                "-e",
                "POSTGRES_DB=cpk",
                "-e",
                "POSTGRES_USER=cpk",
                "-e",
                "POSTGRES_PASSWORD=cpk",
                "--health-cmd",
                "pg_isready -U cpk -d cpk",
                "--health-interval",
                "1s",
                "--health-timeout",
                "5s",
                "--health-retries",
                "30",
                "postgres:16-alpine",
                "postgres",
                "-c",
                "max_wal_size=512MB",
                "-c",
                "checkpoint_timeout=2min",
                "-c",
                "log_checkpoints=on",
            ),
        )

    def test_unittest_failure_emits_bounded_logs_before_exact_cleanup(self) -> None:
        result, events = self._run_gate(unittest_status=37)

        self.assertEqual(result.returncode, 37)
        self.assertIn("postgres-root-cause", result.stderr)
        self.assertEqual(
            [event for event in events if event.startswith("phase:")],
            ["phase:integrity", "phase:postgres", "phase:unittest"],
        )
        log_index = events.index(
            "logs:logs --timestamps --tail 400 cpk-operations-test-postgres"
        )
        final_container_cleanup = max(
            index
            for index, event in enumerate(events)
            if event == "rm:rm -fv cpk-operations-test-postgres"
        )
        final_network_cleanup = max(
            index
            for index, event in enumerate(events)
            if event == "network:network rm cpk-operations-test"
        )
        self.assertLess(events.index("phase:unittest"), log_index)
        self.assertLess(log_index, final_container_cleanup)
        self.assertLess(log_index, final_network_cleanup)

    def test_log_failure_preserves_unittest_status_and_cleanup(self) -> None:
        result, events = self._run_gate(unittest_status=37, log_status=19)

        self.assertEqual(result.returncode, 37)
        self.assertIn(
            "logs:logs --timestamps --tail 400 cpk-operations-test-postgres",
            events,
        )
        self.assertEqual(
            events.count("rm:rm -fv cpk-operations-test-postgres"),
            2,
        )
        self.assertEqual(
            events.count("network:network rm cpk-operations-test"),
            2,
        )

    def test_unhealthy_fixture_emits_bounded_logs_before_cleanup(self) -> None:
        result, events = self._run_gate(
            health_status="unhealthy",
            log_status=19,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("Postgres did not become healthy", result.stderr)
        expected_log = (
            "logs:logs --timestamps --tail 400 cpk-operations-test-postgres"
        )
        self.assertIn(expected_log, events)
        log_index = events.index(expected_log)
        final_container_cleanup = max(
            index
            for index, event in enumerate(events)
            if event == "rm:rm -fv cpk-operations-test-postgres"
        )
        final_network_cleanup = max(
            index
            for index, event in enumerate(events)
            if event == "network:network rm cpk-operations-test"
        )
        self.assertLess(log_index, final_container_cleanup)
        self.assertLess(log_index, final_network_cleanup)
        self.assertEqual(
            [event for event in events if event.startswith("phase:")],
            ["phase:integrity", "phase:postgres"],
        )

    def test_success_runs_all_phases_without_failure_diagnostics(self) -> None:
        result, events = self._run_gate()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("postgres-root-cause", result.stderr)
        self.assertFalse(any(event.startswith("logs:") for event in events))
        self.assertEqual(
            [event for event in events if event.startswith("phase:")],
            [
                "phase:integrity",
                "phase:postgres",
                "phase:unittest",
                "phase:compile",
                "phase:clean-import",
            ],
        )
        self.assertEqual(
            events.count("rm:rm -fv cpk-operations-test-postgres"),
            2,
        )
        self.assertEqual(
            events.count("network:network rm cpk-operations-test"),
            2,
        )
        self.assertEqual(
            events.count("network:network create cpk-operations-test"),
            1,
        )

    def _run_gate(
        self,
        *,
        unittest_status: int = 0,
        log_status: int = 0,
        health_status: str = "healthy",
        architecture_commit: str = ARCHITECTURE_TESTING_COMMIT,
        architecture_dirty: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            binary_directory = temporary / "bin"
            binary_directory.mkdir()
            events_path = temporary / "events"
            docker = binary_directory / "docker"
            docker.write_text(
                textwrap.dedent(
                    """\
                    #!/bin/sh
                    set -eu

                    command_name="$1"
                    case "$command_name" in
                      run)
                        phase=""
                        for argument in "$@"; do
                          case "$argument" in
                            */test-support/package_integrity.py)
                              phase="integrity"
                              ;;
                            postgres:16-alpine)
                              phase="postgres"
                              ;;
                            *"python -m unittest discover -s tests"*)
                              phase="unittest"
                              ;;
                            *"python -m compileall src tests"*)
                              phase="compile"
                              ;;
                            *"import control_plane_kit_operations"*)
                              phase="clean-import"
                              ;;
                          esac
                        done
                        if [ -z "$phase" ]; then
                          printf 'unexpected:%s\n' "$*" \
                            >>"$FAKE_DOCKER_EVENTS"
                          exit 99
                        fi
                        printf 'phase:%s\n' "$phase" \
                          >>"$FAKE_DOCKER_EVENTS"
                        printf 'run:%s:%s\n' "$phase" "$*" \
                          >>"$FAKE_DOCKER_EVENTS"
                        if [ "$phase" = "postgres" ]; then
                          for argument in "$@"; do
                            printf 'launch:%s\n' "$argument" \
                              >>"$FAKE_DOCKER_EVENTS"
                          done
                        fi
                        if [ "$phase" = "unittest" ]; then
                          exit "$FAKE_DOCKER_UNITTEST_STATUS"
                        fi
                        ;;
                      inspect)
                        printf 'inspect:%s\n' "$*" >>"$FAKE_DOCKER_EVENTS"
                        printf '%s\n' "$FAKE_DOCKER_HEALTH_STATUS"
                        ;;
                      logs)
                        printf 'logs:%s\n' "$*" >>"$FAKE_DOCKER_EVENTS"
                        printf 'postgres-root-cause\n' >&2
                        exit "$FAKE_DOCKER_LOG_STATUS"
                        ;;
                      rm)
                        printf 'rm:%s\n' "$*" >>"$FAKE_DOCKER_EVENTS"
                        ;;
                      network)
                        printf 'network:%s\n' "$*" >>"$FAKE_DOCKER_EVENTS"
                        ;;
                      *)
                        printf 'unexpected:%s\n' "$*" >>"$FAKE_DOCKER_EVENTS"
                        exit 99
                        ;;
                    esac
                    """
                ),
                encoding="utf-8",
            )
            docker.chmod(0o755)
            architecture_root = temporary / "architecture-testing"
            architecture_root.mkdir()
            git = binary_directory / "git"
            git.write_text(
                textwrap.dedent(
                    """\
                    #!/bin/sh
                    set -eu

                    if [ "$1" != "-C" ] || [ "$2" != "$FAKE_ARCHITECTURE_ROOT" ]; then
                      exit 98
                    fi
                    shift 2
                    case "$1" in
                      rev-parse)
                        printf '%s\n' "$FAKE_ARCHITECTURE_COMMIT"
                        ;;
                      status)
                        if [ "$FAKE_ARCHITECTURE_DIRTY" = "1" ]; then
                          printf ' M candidate\n'
                        fi
                        ;;
                      *)
                        exit 97
                        ;;
                    esac
                    """
                ),
                encoding="utf-8",
            )
            git.chmod(0o755)
            sleep = binary_directory / "sleep"
            sleep.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            sleep.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{binary_directory}:{environment['PATH']}",
                    "FAKE_DOCKER_EVENTS": str(events_path),
                    "FAKE_DOCKER_UNITTEST_STATUS": str(unittest_status),
                    "FAKE_DOCKER_LOG_STATUS": str(log_status),
                    "FAKE_DOCKER_HEALTH_STATUS": health_status,
                    "FAKE_ARCHITECTURE_ROOT": str(architecture_root),
                    "FAKE_ARCHITECTURE_COMMIT": architecture_commit,
                    "FAKE_ARCHITECTURE_DIRTY": "1" if architecture_dirty else "0",
                    "CPK_ARCHITECTURE_TESTING_ROOT": str(architecture_root),
                }
            )
            result = subprocess.run(
                ["sh", str(PACKAGE_ROOT / "test.sh")],
                cwd=PACKAGE_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            events = (
                events_path.read_text(encoding="utf-8").splitlines()
                if events_path.is_file()
                else []
            )
        return result, events


if __name__ == "__main__":
    unittest.main()
