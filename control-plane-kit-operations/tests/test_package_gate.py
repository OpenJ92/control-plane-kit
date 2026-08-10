import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class OperationsPackageGateTests(unittest.TestCase):
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
            [event for event in events if event.startswith("run:")],
            ["run:1", "run:2", "run:3"],
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
        self.assertLess(events.index("run:3"), log_index)
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

    def test_success_runs_all_phases_without_failure_diagnostics(self) -> None:
        result, events = self._run_gate()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("postgres-root-cause", result.stderr)
        self.assertFalse(any(event.startswith("logs:") for event in events))
        self.assertEqual(
            [event for event in events if event.startswith("run:")],
            ["run:1", "run:2", "run:3", "run:4", "run:5"],
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
    ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            binary_directory = temporary / "bin"
            binary_directory.mkdir()
            events_path = temporary / "events"
            state_path = temporary / "run-count"
            docker = binary_directory / "docker"
            docker.write_text(
                textwrap.dedent(
                    """\
                    #!/bin/sh
                    set -eu

                    command_name="$1"
                    case "$command_name" in
                      run)
                        run_count=0
                        if [ -f "$FAKE_DOCKER_STATE" ]; then
                          run_count="$(cat "$FAKE_DOCKER_STATE")"
                        fi
                        run_count=$((run_count + 1))
                        printf '%s\n' "$run_count" >"$FAKE_DOCKER_STATE"
                        printf 'run:%s\n' "$run_count" >>"$FAKE_DOCKER_EVENTS"
                        if [ "$run_count" -eq 2 ]; then
                          for argument in "$@"; do
                            printf 'launch:%s\n' "$argument" \
                              >>"$FAKE_DOCKER_EVENTS"
                          done
                        fi
                        if [ "$run_count" -eq 3 ]; then
                          exit "$FAKE_DOCKER_UNITTEST_STATUS"
                        fi
                        ;;
                      inspect)
                        printf 'inspect:%s\n' "$*" >>"$FAKE_DOCKER_EVENTS"
                        printf 'healthy\n'
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
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{binary_directory}:{environment['PATH']}",
                    "FAKE_DOCKER_EVENTS": str(events_path),
                    "FAKE_DOCKER_STATE": str(state_path),
                    "FAKE_DOCKER_UNITTEST_STATUS": str(unittest_status),
                    "FAKE_DOCKER_LOG_STATUS": str(log_status),
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
            events = events_path.read_text(encoding="utf-8").splitlines()
        return result, events


if __name__ == "__main__":
    unittest.main()
