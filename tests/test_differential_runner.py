from __future__ import annotations

import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest

from extraction_parity.differential import compare_observations
from extraction_parity.runner import capture_command, evidence_record, main


SOURCE = "sha256:" + "a" * 64


def command(behavior: object, *, stdout: str = "") -> list[str]:
    code = (
        "import json,os;"
        f"open(os.environ['CPK_PARITY_BEHAVIOR_PATH'],'w').write(json.dumps({behavior!r}));"
        f"print({stdout!r},end='')"
    )
    return [sys.executable, "-c", code]


def capture(role: str, value: object, **options: object) -> dict[str, object]:
    return capture_command(
        command(value),
        role=role,
        identity=f"{role}-fixture",
        source_digest=SOURCE,
        timeout_seconds=float(options.get("timeout_seconds", 2)),
        output_limit=int(options.get("output_limit", 4096)),
        secrets=options.get("secrets"),
    )


class DifferentialRunnerTests(unittest.TestCase):
    def test_completed_command_produces_closed_observation(self) -> None:
        observation = capture("reference", {"response": "ok"})
        self.assertEqual(observation["process"], {"kind": "completed", "exit_code": 0})
        self.assertEqual(observation["behavior"], {"response": "ok"})
        self.assertEqual(observation["command"][0], sys.executable)

    def test_timeout_and_output_limit_are_infrastructure_outcomes(self) -> None:
        timeout = capture_command(
            [sys.executable, "-c", "import time; time.sleep(1)"],
            role="reference", identity="timeout", source_digest=SOURCE,
            timeout_seconds=0.05, output_limit=4096,
        )
        self.assertEqual(timeout["process"]["kind"], "timeout")

        overflow = capture_command(
            [sys.executable, "-c", "print('x' * 10000)"],
            role="reference", identity="overflow", source_digest=SOURCE,
            timeout_seconds=2, output_limit=128,
        )
        self.assertEqual(overflow["process"]["kind"], "output-limit")
        self.assertLessEqual(overflow["stdout"]["byte_length"], 128)

    def test_redaction_cannot_expand_output_past_the_declared_bound(self) -> None:
        secret = "abcdefgh"
        observation = capture_command(
            [sys.executable, "-c", "import os; print(os.environ['API_TOKEN'] * 20, end='')"],
            role="reference", identity="redaction-bound", source_digest=SOURCE,
            timeout_seconds=2, output_limit=64, secrets={"API_TOKEN": secret},
        )
        stdout = base64.b64decode(observation["stdout"]["content"])
        self.assertEqual(stdout, b"[REDACTED:output-limit]")
        self.assertLessEqual(observation["stdout"]["byte_length"], 64)

        tiny = capture_command(
            [sys.executable, "-c", "import os; print(os.environ['API_TOKEN'] * 20, end='')"],
            role="reference", identity="tiny-redaction-bound", source_digest=SOURCE,
            timeout_seconds=2, output_limit=8, secrets={"API_TOKEN": secret},
        )
        self.assertLessEqual(tiny["stdout"]["byte_length"], 8)
        self.assertEqual(base64.b64decode(tiny["stdout"]["content"]), b"")

    def test_missing_or_malformed_behavior_is_infrastructure_failure(self) -> None:
        missing = capture_command(
            [sys.executable, "-c", "pass"], role="reference", identity="missing",
            source_digest=SOURCE, timeout_seconds=2, output_limit=4096,
        )
        malformed = capture_command(
            [sys.executable, "-c", "import os; open(os.environ['CPK_PARITY_BEHAVIOR_PATH'],'w').write('{')"],
            role="reference", identity="malformed", source_digest=SOURCE,
            timeout_seconds=2, output_limit=4096,
        )
        self.assertEqual(missing["process"]["kind"], "infrastructure-failure")
        self.assertEqual(malformed["process"]["kind"], "infrastructure-failure")

    def test_declared_secret_is_redacted_before_persistence_and_forbidden_in_behavior(self) -> None:
        secret = "high-entropy-secret"
        code = (
            "import json,os;"
            "print(os.environ['API_TOKEN'],end='');"
            "open(os.environ['CPK_PARITY_BEHAVIOR_PATH'],'w').write(json.dumps({'response':'ok'}))"
        )
        observation = capture_command(
            [sys.executable, "-c", code], role="reference", identity="secret",
            source_digest=SOURCE, timeout_seconds=2, output_limit=4096,
            secrets={"API_TOKEN": secret},
        )
        durable = json.dumps(observation)
        self.assertNotIn(secret, durable)
        stdout = base64.b64decode(observation["stdout"]["content"])
        self.assertEqual(stdout, b"[REDACTED:API_TOKEN]")

        forbidden_code = (
            "import json,os;"
            "open(os.environ['CPK_PARITY_BEHAVIOR_PATH'],'w').write("
            "json.dumps({'token':os.environ['API_TOKEN']}))"
        )
        forbidden = capture_command(
            [sys.executable, "-c", forbidden_code],
            role="reference", identity="secret-behavior",
            source_digest=SOURCE, timeout_seconds=2, output_limit=4096,
            secrets={"API_TOKEN": secret},
        )
        self.assertEqual(forbidden["process"]["kind"], "infrastructure-failure")
        self.assertNotIn(secret, json.dumps(forbidden))

    def test_equivalent_result_emits_deterministic_passing_evidence(self) -> None:
        reference = capture("reference", {"response": "ok"})
        successor = capture("successor", {"response": "ok"})
        result = compare_observations(
            reference,
            successor,
            {"schema": "cpk.normalization-policy", "allowed": []},
        )
        first = evidence_record("proof-a", result)
        second = evidence_record("proof-a", result)
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "passing")
        self.assertRegex(first["digest"], r"^sha256:[0-9a-f]{64}$")

    def test_cli_captures_declared_artifacts_without_hiding_process_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "observation.json"
            artifact = root / "artifact.txt"
            code = (
                "import json,os,pathlib;"
                f"pathlib.Path({str(artifact)!r}).write_text('artifact-value', encoding='utf-8');"
                "open(os.environ['CPK_PARITY_BEHAVIOR_PATH'],'w').write("
                "json.dumps({'response':'ok'}))"
            )

            status = main([
                "capture",
                "--role", "reference",
                "--identity", "artifact-cli",
                "--source-digest", SOURCE,
                "--output", str(output),
                "--artifact", "artifact", "text/plain", str(artifact),
                "--",
                sys.executable, "-c", code,
            ])

            self.assertEqual(status, 0)
            observation = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(observation["implementation"]["identity"], "artifact-cli")
            self.assertEqual(observation["artifacts"][0]["name"], "artifact")
            self.assertEqual(base64.b64decode(observation["artifacts"][0]["payload"]["content"]), b"artifact-value")

    def test_semantic_drift_emits_failed_evidence(self) -> None:
        result = compare_observations(
            capture("reference", {"response": "blue"}),
            capture("successor", {"response": "green"}),
            {"schema": "cpk.normalization-policy", "allowed": []},
        )
        self.assertEqual(evidence_record("proof-drift", result)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
