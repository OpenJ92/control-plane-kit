from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
import inspect
from typing import get_type_hints
import unittest

import control_plane_kit_operations as operations_root
import control_plane_kit_operations.lifecycle as lifecycle
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.postgres import current_schema_contract
from control_plane_kit_operations.postgres.execution import PostgresExecutionStore
from control_plane_kit_operations.gateway_key_rotation_overlap_program import (
    PrepareGatewayKeyRotationOverlap,
)
from control_plane_kit_operations.gateway_key_rotation_retirement_program import (
    PrepareGatewayKeyRotationRetirement,
)
from control_plane_kit_operations.records import (
    ClaimIdentity,
    OperationsRecordError,
)
from control_plane_kit_operations.workflows import InvalidOperationCommand


class ExecutionLeaseLanguageTargetTests(unittest.TestCase):
    def assert_safe_error(self, error: BaseException, *canaries: str) -> None:
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = f"{error!s} {error!r}"
        self.assertLessEqual(len(rendered), 512)
        for canary in canaries:
            self.assertNotIn(canary, rendered)

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
        for candidate in (True, 0, -1, 3601, 1.5, "secret-duration-canary"):
            with self.subTest(candidate=candidate):
                with self.assertRaises(InvalidOperationCommand) as captured:
                    duration_type(candidate)
                self.assert_safe_error(captured.exception, str(candidate))

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
        for candidate in (True, 0, -1, 2**63, "secret-generation-canary"):
            with self.subTest(candidate=candidate):
                with self.assertRaises(OperationsRecordError) as captured:
                    ClaimIdentity("worker-a", candidate, "claimed", "expires")
                self.assert_safe_error(captured.exception, str(candidate))

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

    def test_gateway_rotation_claim_callers_use_the_same_duration_value(self) -> None:
        duration_type = self.duration_type()
        for command_type in (
            PrepareGatewayKeyRotationOverlap,
            PrepareGatewayKeyRotationRetirement,
        ):
            with self.subTest(command=command_type.__name__):
                fields = {
                    field.name: field.type for field in dataclasses.fields(command_type)
                }
                self.assertNotIn("lease_expires_at", fields)
                self.assertIn("lease_duration", fields)
                self.assertIs(
                    get_type_hints(command_type)["lease_duration"],
                    duration_type,
                )

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

    def test_locked_observation_sql_locks_before_one_inclusive_clock_sample(
        self,
    ) -> None:
        self.assertTrue(
            hasattr(PostgresExecutionStore, "observe_request_lease_for_update"),
            "locked lease observation primitive is missing",
        )
        connection = _LeaseObservationConnection()
        store = PostgresExecutionStore(connection)

        observation = store.observe_request_lease_for_update("request-a")

        self.assertTrue(observation.expired)
        normalized = tuple(" ".join(sql.split()).lower() for sql in connection.sql)
        complete_sql = " ".join(normalized)
        self.assertIn("for update", complete_sql)
        self.assertEqual(complete_sql.count("clock_timestamp()"), 1)
        self.assertIn("lease_expires_at <=", complete_sql)

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
                and ">= 1" in (constraint.check_expression or "")
                and "9223372036854775807" in (constraint.check_expression or "")
                for constraint in contract.constraints
            )
        )
        claim_shape = next(
            constraint
            for constraint in contract.constraints
            if constraint.relation == "cpk_execution_requests"
            and constraint.name == "cpk_execution_requests_claim_check"
        )
        self.assertEqual(
            claim_shape.local_columns,
            (
                "status",
                "claim_worker_id",
                "claim_generation",
                "claimed_at",
                "lease_expires_at",
            ),
        )
        for name in (
            "claim_worker_id",
            "claim_generation",
            "claimed_at",
            "lease_expires_at",
        ):
            self.assertIn(f"({name} IS NOT NULL)", claim_shape.check_expression)
            self.assertIn(f"({name} IS NULL)", claim_shape.check_expression)

    def test_lifecycle_result_has_no_duplicate_generation_field(self) -> None:
        self.assertEqual(
            tuple(
                field.name for field in dataclasses.fields(lifecycle.RunLifecycleResult)
            ),
            ("request", "run", "event", "action", "replayed"),
        )


class _Row:
    def __init__(self, value):
        self._value = value

    def fetchone(self):
        return self._value


class _LeaseObservationConnection:
    def __init__(self) -> None:
        self.sql: list[str] = []
        self.observed_at = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)

    def execute(self, sql, parameters=()):
        del parameters
        self.sql.append(sql)
        request_row = (
            "request-a",
            "workspace-a",
            "session-a",
            "plan-a",
            "claimed",
            "operator-a",
            self.observed_at,
            "approval-request-a",
            "approval-decision-a",
            "execute-a",
            "fingerprint-a",
            "worker-a",
            1,
            self.observed_at,
            self.observed_at,
        )
        if "FOR UPDATE" in sql and "clock_timestamp()" in sql:
            return _Row((*request_row, self.observed_at, True))
        if "FOR UPDATE" in sql:
            return _Row(request_row)
        if "clock_timestamp()" in sql:
            return _Row((self.observed_at, True))
        raise AssertionError("unexpected lease observation SQL")


if __name__ == "__main__":
    unittest.main()
