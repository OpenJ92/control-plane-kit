from __future__ import annotations

import dataclasses
import importlib
import importlib.util
import unittest

import control_plane_kit_operations as operations_root
from control_plane_kit_core import EffectAttemptFence
from control_plane_kit_operations.records import ClaimIdentity


class ExecutionLeaseAuthorityTargetTests(unittest.TestCase):
    def fence_type(self):
        value = getattr(operations_root, "ExecutionLeaseFence", None)
        self.assertIsNotNone(value, "ExecutionLeaseFence is missing from the package root")
        return value

    def error_type(self):
        value = getattr(operations_root, "InvalidExecutionLeaseFence", None)
        self.assertIsNotNone(
            value,
            "InvalidExecutionLeaseFence is missing from the package root",
        )
        return value

    def assert_safe_error(self, error: BaseException, *canaries: str) -> None:
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = f"{error!s} {error!r}"
        self.assertLessEqual(len(rendered), 512)
        for canary in canaries:
            self.assertNotIn(canary, rendered)

    def test_defining_module_and_root_export_have_exact_object_identity(self) -> None:
        module_name = "control_plane_kit_operations.execution_leases"
        self.assertIsNotNone(
            importlib.util.find_spec(module_name),
            "execution_leases module is missing",
        )
        module = importlib.import_module(module_name)

        self.assertIs(operations_root.ExecutionLeaseFence, module.ExecutionLeaseFence)
        self.assertIs(
            operations_root.InvalidExecutionLeaseFence,
            module.InvalidExecutionLeaseFence,
        )
        self.assertTrue(issubclass(module.InvalidExecutionLeaseFence, ValueError))

    def test_fence_is_one_frozen_nominal_pair_with_stable_descriptor(self) -> None:
        fence_type = self.fence_type()
        fence = fence_type("worker-a", 7)

        self.assertTrue(dataclasses.is_dataclass(fence_type))
        self.assertEqual(
            tuple(field.name for field in dataclasses.fields(fence_type)),
            ("worker_id", "generation"),
        )
        self.assertEqual(
            fence.descriptor(),
            {"worker_id": "worker-a", "generation": 7},
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            fence.generation = 8

        rendered = repr(fence)
        self.assertIn("worker-a", rendered)
        self.assertIn("generation=7", rendered)
        for forbidden in (
            "claimed_at",
            "lease_expires_at",
            "authority_reference",
            "secret",
            "SELECT",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_worker_identity_has_exact_total_claim_domain(self) -> None:
        fence_type = self.fence_type()
        error_type = self.error_type()

        self.assertEqual(fence_type("w", 1).worker_id, "w")
        self.assertEqual(fence_type("w" * 512, 1).worker_id, "w" * 512)
        for candidate in (
            "",
            "w" * 513,
            "control-canary\n",
            1,
        ):
            with self.subTest(candidate_type=type(candidate).__name__):
                with self.assertRaises(error_type) as captured:
                    fence_type(candidate, 1)
                canaries = () if candidate == "" else (str(candidate),)
                self.assert_safe_error(captured.exception, *canaries)

    def test_generation_is_exact_positive_postgres_bigint(self) -> None:
        fence_type = self.fence_type()
        error_type = self.error_type()

        self.assertEqual(fence_type("worker-a", 1).generation, 1)
        self.assertEqual(
            fence_type("worker-a", 2**63 - 1).generation,
            2**63 - 1,
        )
        for candidate in (True, 0, -1, 2**63, "generation-canary"):
            with self.subTest(candidate=candidate):
                with self.assertRaises(error_type) as captured:
                    fence_type("worker-a", candidate)
                self.assert_safe_error(captured.exception, str(candidate))

    def test_claim_identity_projects_fence_without_duplicate_truth(self) -> None:
        fence_type = self.fence_type()
        claim = ClaimIdentity(
            worker_id="worker-a",
            generation=11,
            claimed_at="2026-08-14T12:00:00Z",
            lease_expires_at="2026-08-14T12:10:00Z",
        )

        self.assertEqual(
            tuple(field.name for field in dataclasses.fields(ClaimIdentity)),
            ("worker_id", "generation", "claimed_at", "lease_expires_at"),
        )
        self.assertIs(type(claim.fence), fence_type)
        self.assertEqual(claim.fence, fence_type("worker-a", 11))
        self.assertNotIn("claimed_at", claim.fence.descriptor())
        self.assertNotIn("lease_expires_at", claim.fence.descriptor())

    def test_claim_projection_is_total_at_both_durable_maxima(self) -> None:
        fence_type = self.fence_type()
        claim = ClaimIdentity(
            worker_id="w" * 512,
            generation=2**63 - 1,
            claimed_at="claimed",
            lease_expires_at="expires",
        )

        self.assertEqual(claim.fence, fence_type("w" * 512, 2**63 - 1))

    def test_execution_fence_is_not_the_core_effect_attempt_fence(self) -> None:
        fence_type = self.fence_type()

        self.assertIsNot(fence_type, EffectAttemptFence)
        self.assertNotIsInstance(fence_type("worker-a", 1), EffectAttemptFence)


if __name__ == "__main__":
    unittest.main()
