from __future__ import annotations

import unittest

from extraction_parity.differential import (
    DifferentialError,
    capture_payload,
    compare_observations,
    decode_observation,
    decode_policy,
    decode_result,
    normalize_value,
)


def observation(
    role: str,
    behavior: object,
    *,
    process_kind: str = "completed",
    exit_code: int | None = 0,
) -> dict[str, object]:
    return {
        "schema": "cpk.raw-observation",
        "implementation": {
            "role": role,
            "identity": f"{role}-implementation",
            "source_digest": "sha256:" + ("a" if role == "reference" else "b") * 64,
        },
        "command": ["python", "scenario.py"],
        "process": {"kind": process_kind, "exit_code": exit_code},
        "stdout": capture_payload(b"visible output\n"),
        "stderr": capture_payload(b""),
        "behavior": behavior,
        "artifacts": [],
    }


def policy(*allowed: str) -> dict[str, object]:
    return {"schema": "cpk.normalization-policy", "allowed": list(allowed)}


class DifferentialLanguageTests(unittest.TestCase):
    def test_observation_codec_is_closed_and_pins_source_identity(self) -> None:
        value = observation("reference", {"response": "ok"})
        self.assertEqual(decode_observation(value), value)
        with self.assertRaises(DifferentialError):
            decode_observation({**value, "extra": True})
        with self.assertRaises(DifferentialError):
            decode_observation({**value, "implementation": {**value["implementation"], "source_digest": "mutable"}})

    def test_incidental_values_are_typed_and_policy_is_closed(self) -> None:
        self.assertEqual(
            normalize_value(
                {"port": {"$incidental": {"kind": "allocated-port", "value": 49152}}},
                policy("allocated-port"),
            ),
            {"port": {"$normalized": "allocated-port"}},
        )
        for invalid in (
            {"$incidental": {"kind": "allocated-port", "value": "49152"}},
            {"$incidental": {"kind": "unknown", "value": "x"}},
            {"$incidental": {"kind": "timestamp", "value": "yesterday"}},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(DifferentialError):
                    normalize_value(invalid, policy("allocated-port", "timestamp"))
        with self.assertRaises(DifferentialError):
            decode_policy({"schema": "cpk.normalization-policy", "allowed": ["response-body"]})

    def test_normalization_is_idempotent(self) -> None:
        value = {"id": {"$incidental": {"kind": "generated-id", "value": "run-a"}}}
        normalized = normalize_value(value, policy("generated-id"))
        self.assertEqual(normalize_value(normalized, policy("generated-id")), normalized)

    def test_equivalent_incidental_values_compare_equal(self) -> None:
        reference = observation(
            "reference",
            {"response": "ok", "port": {"$incidental": {"kind": "allocated-port", "value": 41001}}},
        )
        successor = observation(
            "successor",
            {"response": "ok", "port": {"$incidental": {"kind": "allocated-port", "value": 52002}}},
        )
        result = compare_observations(reference, successor, policy("allocated-port"))
        self.assertEqual(result["status"], "equivalent")
        self.assertEqual(result["differences"], [])
        self.assertNotEqual(result["raw"]["reference"], result["raw"]["successor"])

    def test_untagged_semantic_drift_remains_visible(self) -> None:
        reference = observation("reference", {"response": {"status": 200, "body": "blue"}})
        successor = observation("successor", {"response": {"status": 200, "body": "green"}})
        result = compare_observations(
            reference,
            successor,
            policy("allocated-port", "generated-id", "timestamp", "container-name"),
        )
        self.assertEqual(result["status"], "drift")
        self.assertIn("behavior", result["differences"])

    def test_differential_result_rejects_tampered_digest_status_and_normalization(self) -> None:
        result = compare_observations(
            observation("reference", {"response": "ok"}),
            observation("successor", {"response": "ok"}),
            policy(),
        )
        self.assertEqual(decode_result(result), result)
        for invalid in (
            {**result, "reference_digest": "sha256:" + "0" * 64},
            {**result, "status": "drift"},
            {**result, "normalized": {"reference": "hidden", "successor": "hidden"}},
            {**result, "extra": True},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(DifferentialError):
                    decode_result(invalid)

    def test_process_failure_is_not_behavioral_drift(self) -> None:
        reference = observation("reference", {"response": "ok"})
        successor = observation("successor", None, process_kind="timeout", exit_code=None)
        result = compare_observations(reference, successor, policy())
        self.assertEqual(result["status"], "infrastructure-failure")
        self.assertIn("successor-process", result["differences"])

    def test_nonzero_completed_exit_is_comparable_and_output_is_audit_only(self) -> None:
        reference = observation("reference", {"error": "invalid"}, exit_code=2)
        successor = observation("successor", {"error": "invalid"}, exit_code=2)
        successor["stdout"] = capture_payload(b"different log formatting\n")
        result = compare_observations(reference, successor, policy())
        self.assertEqual(result["status"], "equivalent")
        self.assertNotEqual(result["raw"]["reference"]["stdout"], result["raw"]["successor"]["stdout"])


if __name__ == "__main__":
    unittest.main()
