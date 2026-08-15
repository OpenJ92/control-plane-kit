from __future__ import annotations

import dataclasses
import hashlib
import importlib
import json
import os
from pathlib import Path
import unittest

import control_plane_kit_operations as operations_root
from control_plane_kit_core.operations import RunId
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    FailureCategory,
    RecoveryDecisionKind,
    RecoveryScope,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionLeaseDuration
import control_plane_kit_operations.records as records_module
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    BoundedEvidence,
    FailureEvidence,
    OperationsRecordError,
)
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    InvalidOperationCommand,
)


RECOVERY_MODULE = "control_plane_kit_operations.execution_lease_recovery"


def _load_recovery_module(import_module=importlib.import_module):
    try:
        return import_module(RECOVERY_MODULE)
    except ModuleNotFoundError as error:
        if error.name != RECOVERY_MODULE:
            raise
        return None


recovery_module = _load_recovery_module()
AbandonExpiredExecutionClaim = getattr(
    recovery_module, "AbandonExpiredExecutionClaim", None
)
ExecutionLeaseRecoveryCommand = getattr(
    recovery_module, "ExecutionLeaseRecoveryCommand", None
)
RecoveryAuthority = getattr(recovery_module, "RecoveryAuthority", None)
RenewActiveExecutionClaim = getattr(
    recovery_module, "RenewActiveExecutionClaim", None
)
RenewExpiredExecutionClaim = getattr(
    recovery_module, "RenewExpiredExecutionClaim", None
)
TakeOverExpiredExecutionClaim = getattr(
    recovery_module, "TakeOverExpiredExecutionClaim", None
)
ExecutionLeaseRecoveryEvidence = getattr(
    records_module, "ExecutionLeaseRecoveryEvidence", None
)


class ExecutionLeaseRecoveryLanguageTests(unittest.TestCase):
    maxDiff = None

    def assert_safe_error(self, error: BaseException, *canaries: str) -> None:
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = f"{error!s} {error!r}"
        self.assertLessEqual(len(rendered), 512)
        for canary in canaries:
            if canary:
                self.assertNotIn(canary, rendered)

    def require_language(self) -> None:
        required = {
            "RecoveryAuthority": RecoveryAuthority,
            "RenewActiveExecutionClaim": RenewActiveExecutionClaim,
            "RenewExpiredExecutionClaim": RenewExpiredExecutionClaim,
            "TakeOverExpiredExecutionClaim": TakeOverExpiredExecutionClaim,
            "AbandonExpiredExecutionClaim": AbandonExpiredExecutionClaim,
            "ExecutionLeaseRecoveryCommand": ExecutionLeaseRecoveryCommand,
            "ExecutionLeaseRecoveryEvidence": ExecutionLeaseRecoveryEvidence,
        }
        self.assertEqual(
            [name for name, value in required.items() if value is None],
            [],
            "execution-lease recovery public language is missing",
        )

    def authority(
        self,
        *,
        reference: str = "authority-reference-canary",
        scopes: tuple[RecoveryScope, ...] = (RecoveryScope.RENEW_CLAIM,),
    ):
        self.require_language()
        return RecoveryAuthority("operator-a", reference, scopes)

    def command(self, decision: RecoveryDecisionKind, **changes):
        self.require_language()
        common = {
            "request_id": "request-a",
            "retained_run_id": RunId("run-a"),
            "expected_fence": ExecutionLeaseFence("worker-a", 7),
            "authority": self.authority(),
            "idempotency_key": IdempotencyKey("recover-a"),
        }
        common.update(changes)
        if decision is RecoveryDecisionKind.RENEW_ACTIVE_CLAIM:
            duration = common.pop("lease_duration", ExecutionLeaseDuration(600))
            return RenewActiveExecutionClaim(
                lease_duration=duration, **common
            )
        if decision is RecoveryDecisionKind.RENEW_EXPIRED_CLAIM:
            duration = common.pop("lease_duration", ExecutionLeaseDuration(600))
            return RenewExpiredExecutionClaim(
                lease_duration=duration, **common
            )
        if decision is RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM:
            duration = common.pop("lease_duration", ExecutionLeaseDuration(600))
            next_worker_id = common.pop("next_worker_id", "worker-b")
            return TakeOverExpiredExecutionClaim(
                next_worker_id=next_worker_id,
                lease_duration=duration,
                **common,
            )
        return AbandonExpiredExecutionClaim(**common)

    def evidence(self, decision: RecoveryDecisionKind):
        self.require_language()
        prior = ExecutionLeaseFence("worker-a", 7)
        if decision in (
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
        ):
            replacement = ExecutionLeaseFence("worker-a", 8)
        elif decision is RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM:
            replacement = ExecutionLeaseFence("worker-b", 8)
        else:
            replacement = None
        return ExecutionLeaseRecoveryEvidence(
            decision, RunId("run-a"), prior, replacement
        )

    def test_public_language_is_frozen_nominal_and_root_identical(self) -> None:
        self.require_language()
        public_values = (
            RecoveryAuthority,
            RenewActiveExecutionClaim,
            RenewExpiredExecutionClaim,
            TakeOverExpiredExecutionClaim,
            AbandonExpiredExecutionClaim,
            ExecutionLeaseRecoveryEvidence,
        )
        for value in public_values:
            with self.subTest(value=value.__name__):
                self.assertIs(getattr(operations_root, value.__name__, None), value)

        self.assertEqual(
            ExecutionLeaseRecoveryCommand,
            RenewActiveExecutionClaim
            | RenewExpiredExecutionClaim
            | TakeOverExpiredExecutionClaim
            | AbandonExpiredExecutionClaim,
        )
        for value in public_values:
            self.assertTrue(dataclasses.is_dataclass(value))
            self.assertEqual(value.__dataclass_params__.frozen, True)
        self.assertEqual(
            tuple(field.name for field in dataclasses.fields(RecoveryAuthority)),
            ("actor_id", "authority_reference", "scopes"),
        )
        self.assertEqual(
            tuple(
                field.name
                for field in dataclasses.fields(ExecutionLeaseRecoveryEvidence)
            ),
            (
                "decision_kind",
                "retained_run_id",
                "prior_fence",
                "replacement_fence",
            ),
        )

    def test_missing_module_guard_never_masks_nested_import_failures(self) -> None:
        nested = ModuleNotFoundError("nested dependency missing")
        nested.name = "nested_dependency"

        def missing_nested(_name):
            raise nested

        with self.assertRaises(ModuleNotFoundError) as captured:
            _load_recovery_module(missing_nested)
        self.assertIs(captured.exception, nested)

        def partial_import(_name):
            raise ImportError("partial module export failure")

        with self.assertRaises(ImportError):
            _load_recovery_module(partial_import)

    def test_authority_is_bounded_canonical_and_secret_adjacent_reference_is_hidden(
        self,
    ) -> None:
        self.require_language()
        authority = RecoveryAuthority(
            "a" * 512,
            "reference-canary",
            (
                RecoveryScope.TAKE_OVER_CLAIM,
                RecoveryScope.RENEW_CLAIM,
                RecoveryScope.TAKE_OVER_CLAIM,
            ),
        )
        self.assertEqual(
            authority.scopes,
            (RecoveryScope.RENEW_CLAIM, RecoveryScope.TAKE_OVER_CLAIM),
        )
        self.assertNotIn("reference-canary", repr(authority))
        self.assertFalse(
            next(
                field
                for field in dataclasses.fields(RecoveryAuthority)
                if field.name == "authority_reference"
            ).repr
        )

        for field_name in ("actor_id", "authority_reference"):
            for candidate in ("", "bad\nvalue", "x" * 513, 1, None):
                values = {
                    "actor_id": "operator-a",
                    "authority_reference": "reference-a",
                    "scopes": (RecoveryScope.RENEW_CLAIM,),
                }
                values[field_name] = candidate
                with self.subTest(field=field_name, candidate=type(candidate)):
                    with self.assertRaises(InvalidOperationCommand) as captured:
                        RecoveryAuthority(**values)
                    self.assert_safe_error(captured.exception, str(candidate))

        for scopes in (
            ("recovery:renew-claim",),
            (RecoveryScope.RENEW_CLAIM, "scope-canary"),
            [RecoveryScope.RENEW_CLAIM],
        ):
            with self.subTest(scopes=scopes):
                with self.assertRaises(InvalidOperationCommand) as captured:
                    RecoveryAuthority("operator-a", "reference-a", scopes)
                self.assert_safe_error(captured.exception, "scope-canary")

    def test_four_commands_have_exact_fields_descriptors_and_closed_variants(self) -> None:
        expected = {
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM: (
                RenewActiveExecutionClaim,
                (
                    "request_id",
                    "retained_run_id",
                    "expected_fence",
                    "authority",
                    "lease_duration",
                    "idempotency_key",
                ),
                "renew-active-claim",
            ),
            RecoveryDecisionKind.RENEW_EXPIRED_CLAIM: (
                RenewExpiredExecutionClaim,
                (
                    "request_id",
                    "retained_run_id",
                    "expected_fence",
                    "authority",
                    "lease_duration",
                    "idempotency_key",
                ),
                "renew-expired-claim",
            ),
            RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM: (
                TakeOverExpiredExecutionClaim,
                (
                    "request_id",
                    "retained_run_id",
                    "expected_fence",
                    "authority",
                    "next_worker_id",
                    "lease_duration",
                    "idempotency_key",
                ),
                "take-over-expired-claim",
            ),
            RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM: (
                AbandonExpiredExecutionClaim,
                (
                    "request_id",
                    "retained_run_id",
                    "expected_fence",
                    "authority",
                    "idempotency_key",
                ),
                "abandon-expired-claim",
            ),
        }
        for decision, (command_type, fields, command_name) in expected.items():
            with self.subTest(decision=decision):
                command = self.command(decision)
                self.assertIs(type(command), command_type)
                self.assertEqual(
                    tuple(field.name for field in dataclasses.fields(command)), fields
                )
                descriptor = command.descriptor()
                expected_keys = {
                    "command",
                    "request_id",
                    "retained_run_id",
                    "expected_fence",
                    "actor_id",
                    "idempotency_key",
                }
                if decision is not RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM:
                    expected_keys.add("lease_duration_seconds")
                if decision is RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM:
                    expected_keys.add("next_worker_id")
                self.assertEqual(set(descriptor), expected_keys)
                self.assertEqual(descriptor["command"], command_name)
                self.assertNotIn("authority_reference", repr(descriptor))
                self.assertNotIn("scopes", descriptor)

    def test_command_boundaries_require_exact_nominal_values(self) -> None:
        self.require_language()

        class FenceSubclass(ExecutionLeaseFence):
            pass

        class RunIdSubclass(RunId):
            pass

        class DurationSubclass(ExecutionLeaseDuration):
            pass

        class IdempotencySubclass(IdempotencyKey):
            pass

        invalid = (
            {"request_id": ""},
            {"request_id": "bad\nrequest"},
            {"request_id": "x" * 513},
            {"retained_run_id": "run-a"},
            {"retained_run_id": RunIdSubclass("run-a")},
            {"expected_fence": ("worker-a", 7)},
            {"expected_fence": FenceSubclass("worker-a", 7)},
            {"authority": object()},
            {"idempotency_key": "recover-a"},
            {"idempotency_key": IdempotencySubclass("recover-a")},
            {"lease_duration": 600},
            {"lease_duration": DurationSubclass(600)},
        )
        for changes in invalid:
            with self.subTest(changes=tuple(changes)):
                with self.assertRaises(InvalidOperationCommand) as captured:
                    self.command(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM, **changes)
                self.assert_safe_error(captured.exception)

        for next_worker_id in ("worker-a", "", "bad\nworker", "x" * 513, 1):
            with self.subTest(next_worker_id=type(next_worker_id)):
                with self.assertRaises(InvalidOperationCommand) as captured:
                    self.command(
                        RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM,
                        next_worker_id=next_worker_id,
                    )
                self.assert_safe_error(captured.exception, str(next_worker_id))

    def test_fingerprint_is_exact_canonical_intent_and_excludes_retry_authority(self) -> None:
        common = {
            "actor_id": "operator-a",
            "authority_reference": "authority-reference-canary",
            "expected_fence": {"generation": 7, "worker_id": "worker-a"},
            "request_id": "request-a",
            "retained_run_id": "run-a",
        }
        documents = {
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM: {
                **common,
                "command": "renew-active-claim",
                "lease_duration_seconds": 600,
            },
            RecoveryDecisionKind.RENEW_EXPIRED_CLAIM: {
                **common,
                "command": "renew-expired-claim",
                "lease_duration_seconds": 600,
            },
            RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM: {
                **common,
                "command": "take-over-expired-claim",
                "lease_duration_seconds": 600,
                "next_worker_id": "worker-b",
            },
            RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM: {
                **common,
                "command": "abandon-expired-claim",
            },
        }
        for decision, document in documents.items():
            with self.subTest(decision=decision):
                expected = hashlib.sha256(
                    json.dumps(
                        document, sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest()
                self.assertEqual(self.command(decision).intent_fingerprint(), expected)
                self.assertRegex(expected, r"^[0-9a-f]{64}$")

        command = self.command(RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM)
        expected = command.intent_fingerprint()

        changed_reference = dataclasses.replace(
            command, authority=self.authority(reference="different-reference")
        )
        changed_scopes = dataclasses.replace(
            command,
            authority=self.authority(
                scopes=(RecoveryScope.TAKE_OVER_CLAIM, RecoveryScope.ACCEPT_LOSS)
            ),
        )
        changed_key = dataclasses.replace(command, idempotency_key=IdempotencyKey("b"))
        self.assertNotEqual(changed_reference.intent_fingerprint(), expected)
        self.assertEqual(changed_scopes.intent_fingerprint(), expected)
        self.assertEqual(changed_key.intent_fingerprint(), expected)

    def test_evidence_is_an_exact_closed_sum_with_bounded_descriptor(self) -> None:
        self.require_language()
        expected = {
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM: ("worker-a", 8),
            RecoveryDecisionKind.RENEW_EXPIRED_CLAIM: ("worker-a", 8),
            RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM: ("worker-b", 8),
            RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM: None,
        }
        for decision, replacement in expected.items():
            with self.subTest(decision=decision):
                evidence = self.evidence(decision)
                descriptor = evidence.descriptor()
                self.assertEqual(
                    set(descriptor),
                    {
                        "decision",
                        "retained_run_id",
                        "prior_fence",
                        "replacement_fence",
                    },
                )
                self.assertEqual(descriptor["decision"], decision.value)
                self.assertEqual(descriptor["retained_run_id"], "run-a")
                self.assertEqual(
                    descriptor["replacement_fence"],
                    None
                    if replacement is None
                    else {"worker_id": replacement[0], "generation": replacement[1]},
                )

        invalid = (
            (
                RecoveryDecisionKind.CONFIRM_EFFECT_SUCCEEDED,
                ExecutionLeaseFence("worker-a", 8),
            ),
            (
                RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                ExecutionLeaseFence("worker-b", 8),
            ),
            (
                RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                ExecutionLeaseFence("worker-a", 9),
            ),
            (
                RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM,
                ExecutionLeaseFence("worker-a", 8),
            ),
            (
                RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM,
                ExecutionLeaseFence("worker-a", 8),
            ),
        )
        for decision, replacement in invalid:
            with self.subTest(decision=decision, replacement=replacement):
                with self.assertRaises(OperationsRecordError) as captured:
                    ExecutionLeaseRecoveryEvidence(
                        decision,
                        RunId("run-a"),
                        ExecutionLeaseFence("worker-a", 7),
                        replacement,
                    )
                self.assert_safe_error(captured.exception)

        nominal_invalid = (
            ("renew-active-claim", RunId("run-a"), ExecutionLeaseFence("worker-a", 7), ExecutionLeaseFence("worker-a", 8)),
            (RecoveryDecisionKind.RENEW_ACTIVE_CLAIM, "run-a", ExecutionLeaseFence("worker-a", 7), ExecutionLeaseFence("worker-a", 8)),
            (RecoveryDecisionKind.RENEW_ACTIVE_CLAIM, RunId("run-a"), ("worker-a", 7), ExecutionLeaseFence("worker-a", 8)),
            (RecoveryDecisionKind.RENEW_ACTIVE_CLAIM, RunId("run-a"), ExecutionLeaseFence("worker-a", 7), ("worker-a", 8)),
        )
        for values in nominal_invalid:
            with self.subTest(values=tuple(type(value).__name__ for value in values)):
                with self.assertRaises(OperationsRecordError):
                    ExecutionLeaseRecoveryEvidence(*values)

        class RunIdSubclass(RunId):
            pass

        class FenceSubclass(ExecutionLeaseFence):
            pass

        nominal_subclasses = (
            (
                RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                RunIdSubclass("run-a"),
                ExecutionLeaseFence("worker-a", 7),
                ExecutionLeaseFence("worker-a", 8),
            ),
            (
                RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                RunId("run-a"),
                FenceSubclass("worker-a", 7),
                ExecutionLeaseFence("worker-a", 8),
            ),
            (
                RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                RunId("run-a"),
                ExecutionLeaseFence("worker-a", 7),
                FenceSubclass("worker-a", 8),
            ),
        )
        for values in nominal_subclasses:
            with self.subTest(
                values=tuple(type(value).__name__ for value in values)
            ):
                with self.assertRaises(OperationsRecordError):
                    ExecutionLeaseRecoveryEvidence(*values)

        maximum = 2**63 - 1
        accepted_maximum = ExecutionLeaseRecoveryEvidence(
            RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            RunId("run-a"),
            ExecutionLeaseFence("worker-a", maximum - 1),
            ExecutionLeaseFence("worker-a", maximum),
        )
        self.assertEqual(accepted_maximum.replacement_fence.generation, maximum)
        with self.assertRaises(OperationsRecordError):
            ExecutionLeaseRecoveryEvidence(
                RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                RunId("run-a"),
                ExecutionLeaseFence("worker-a", maximum),
                ExecutionLeaseFence("worker-a", maximum),
            )

    def test_recovery_event_is_intrinsic_exclusive_and_backward_compatible(self) -> None:
        evidence = self.evidence(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM)
        event = ActivityEventRecord(
            "event-a",
            "run-a",
            1,
            ActivityEventKind.RECOVERY_DECISION_RECORDED,
            "observed",
            recovery=evidence,
        )
        self.assertIs(event.recovery, evidence)
        self.assertEqual(event.evidence, BoundedEvidence())
        self.assertIsNone(event.failure)

        with self.assertRaises(OperationsRecordError):
            ActivityEventRecord(
                "event-a",
                "run-a",
                1,
                ActivityEventKind.RECOVERY_DECISION_RECORDED,
                "observed",
            )
        with self.assertRaises(OperationsRecordError):
            ActivityEventRecord(
                "event-a",
                "run-other",
                1,
                ActivityEventKind.RECOVERY_DECISION_RECORDED,
                "observed",
                recovery=evidence,
            )
        with self.assertRaises(OperationsRecordError):
            ActivityEventRecord(
                "event-a",
                "run-a",
                1,
                ActivityEventKind.RECOVERY_DECISION_RECORDED,
                "observed",
                failure=FailureEvidence(
                    FailureCategory.TERMINAL,
                    "failure-code",
                    "bounded failure",
                ),
                recovery=evidence,
            )
        with self.assertRaises(OperationsRecordError):
            ActivityEventRecord(
                "event-a",
                "run-a",
                1,
                ActivityEventKind.RECOVERY_DECISION_RECORDED,
                "observed",
                evidence=BoundedEvidence.from_mapping({"duplicate": True}),
                recovery=evidence,
            )
        with self.assertRaises(OperationsRecordError):
            ActivityEventRecord(
                "event-a",
                "run-a",
                1,
                ActivityEventKind.RUN_OPENED,
                "observed",
                recovery=evidence,
            )

        ordinary = ActivityEventRecord(
            "event-b", "run-a", 2, ActivityEventKind.RUN_OPENED, "observed"
        )
        self.assertIsNone(ordinary.recovery)


class ExecutionLeaseRecoveryOwnershipTests(unittest.TestCase):
    def test_new_module_has_one_exhaustive_operations_inventory_row(self) -> None:
        inventory_path = Path(
            os.environ.get(
                "CPK_PACKAGE_MODULE_INVENTORY",
                Path(__file__).parents[2]
                / "docs"
                / "architecture"
                / "package-module-inventory.json",
            )
        )
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        rows = [
            row
            for row in inventory["modules"]
            if row["module"]
            == "control_plane_kit_operations.execution_lease_recovery"
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["owner"], "operation")
        self.assertEqual(
            rows[0]["destination"],
            "control_plane_kit_operations.execution_lease_recovery",
        )
        self.assertEqual(rows[0]["optional_external_dependencies"], [])
        self.assertIn(
            "tests/test_execution_lease_recovery_contract.py",
            rows[0]["protecting_tests"],
        )
        self.assertIn(
            "tests/test_execution_lease_recovery_result.py",
            rows[0]["protecting_tests"],
        )


if __name__ == "__main__":
    unittest.main()
