from __future__ import annotations

import dataclasses
import inspect
import unittest

import control_plane_kit_operations as operations_root
import control_plane_kit_operations.lifecycle as lifecycle
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.postgres import current_schema_contract
from control_plane_kit_operations.postgres.execution import PostgresExecutionStore
from control_plane_kit_operations.records import (
    ClaimIdentity,
    OperationsRecordError,
)
from control_plane_kit_operations.workflows import InvalidOperationCommand


class ExecutionLeaseLanguageTargetTests(unittest.TestCase):
    def duration_type(self):
        duration_type = getattr(lifecycle, "ExecutionLeaseDuration", None)
        self.assertIsNotNone(
            duration_type,
            "ExecutionLeaseDuration is missing from the lifecycle language",
        )
        return duration_type

    def test_duration_is_one_bounded_public_value(self) -> None:
        duration_type = self.duration_type()

        self.assertIs(
            getattr(operations_root, "ExecutionLeaseDuration", None),
            duration_type,
        )
        self.assertEqual(duration_type(1).seconds, 1)
        self.assertEqual(duration_type(3600).seconds, 3600)
        for candidate in (True, 0, -1, 3601, 1.5, "60"):
            with self.subTest(candidate=candidate):
                with self.assertRaises(InvalidOperationCommand):
                    duration_type(candidate)

    def test_claim_identity_has_one_bounded_generation(self) -> None:
        self.assertEqual(
            tuple(field.name for field in dataclasses.fields(ClaimIdentity)),
            ("worker_id", "generation", "claimed_at", "lease_expires_at"),
        )
        self.assertEqual(
            ClaimIdentity("worker-a", 1, "claimed", "expires").generation,
            1,
        )
        self.assertEqual(
            ClaimIdentity(
                "worker-a", 2**63 - 1, "claimed", "expires"
            ).generation,
            2**63 - 1,
        )
        for candidate in (True, 0, -1, 2**63):
            with self.subTest(candidate=candidate):
                with self.assertRaises(OperationsRecordError):
                    ClaimIdentity("worker-a", candidate, "claimed", "expires")

    def test_claim_command_uses_duration_and_not_absolute_expiry(self) -> None:
        duration_type = self.duration_type()
        field_names = tuple(
            field.name for field in dataclasses.fields(lifecycle.ClaimAndOpenActivityRun)
        )

        self.assertEqual(
            field_names,
            ("request_id", "authority", "lease_duration", "idempotency_key"),
        )
        command = lifecycle.ClaimAndOpenActivityRun(
            request_id="request-a",
            authority=lifecycle.ExecutionWorkerAuthority(
                "worker-a",
                (PolicyScope.EXECUTION_OPERATE,),
            ),
            lease_duration=duration_type(600),
            idempotency_key=operations_root.IdempotencyKey("claim-a"),
        )
        self.assertEqual(command.descriptor()["lease_duration_seconds"], 600)
        self.assertNotIn("lease_expires_at", command.descriptor())

    def test_store_claim_interface_accepts_duration_only(self) -> None:
        parameters = tuple(
            inspect.signature(PostgresExecutionStore.claim_request).parameters
        )

        self.assertEqual(
            parameters,
            ("self", "request_id", "worker_id", "lease_duration_seconds"),
        )
        self.assertTrue(
            hasattr(PostgresExecutionStore, "observe_request_lease_for_update")
        )

    def test_direct_schema_contract_has_exact_bigint_generation(self) -> None:
        contract = current_schema_contract.CURRENT_POSTGRES_SCHEMA_CONTRACT
        columns = {
            (column.relation, column.name): column for column in contract.columns
        }
        generation = columns.get(("cpk_execution_requests", "claim_generation"))

        self.assertIsNotNone(generation)
        self.assertEqual(generation.formatted_type, "bigint")
        self.assertTrue(
            any(
                constraint.relation == "cpk_execution_requests"
                and constraint.local_columns == ("claim_generation",)
                and "9223372036854775807" in (constraint.check_expression or "")
                for constraint in contract.constraints
            )
        )


if __name__ == "__main__":
    unittest.main()
