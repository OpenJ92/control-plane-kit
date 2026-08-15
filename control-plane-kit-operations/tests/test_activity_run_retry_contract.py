from __future__ import annotations

import ast
import dataclasses
import importlib
import json
import os
from pathlib import Path
import unittest

import control_plane_kit_operations as operations_root
from control_plane_kit_core.operations import RunId
from control_plane_kit_core.operations.lifecycle import (
    RecoveryDecisionKind,
    RecoveryScope,
)
from control_plane_kit_operations.execution_lease_recovery import (
    RecoveryAuthority,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.records import (
    ExecutionLeaseRecoveryEvidence,
    OperationsRecordError,
    RetryIdentity,
)
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    InvalidOperationCommand,
)


RETRY_MODULE = "control_plane_kit_operations.activity_run_retry"
MAX_ATTEMPT = 2_147_483_647


def _load_retry_module(import_module=importlib.import_module):
    try:
        return import_module(RETRY_MODULE)
    except ModuleNotFoundError as error:
        if error.name != RETRY_MODULE:
            raise
        return None


retry_module = _load_retry_module()
RetryFailedActivityRun = getattr(
    retry_module, "RetryFailedActivityRun", None
)


class ActivityRunRetryLanguageTests(unittest.TestCase):
    maxDiff = None

    def require_language(self) -> None:
        self.assertIsNotNone(
            RetryFailedActivityRun,
            "activity-run retry public language is missing",
        )

    def assert_safe_error(self, error: BaseException, *canaries: str) -> None:
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = f"{error!s} {error!r}"
        self.assertLessEqual(len(rendered), 512)
        for canary in canaries:
            if canary:
                self.assertNotIn(canary, rendered)

    def authority(
        self,
        *,
        actor_id: str = "operator-a",
        reference: str = "authority-a",
        scopes: tuple[RecoveryScope, ...] = (RecoveryScope.OPERATE,),
    ) -> RecoveryAuthority:
        return RecoveryAuthority(actor_id, reference, scopes)

    def command(self, **changes):
        self.require_language()
        values = {
            "request_id": "request-a",
            "prior_run_id": RunId("run-a"),
            "expected_fence": ExecutionLeaseFence("worker-a", 7),
            "authority": self.authority(),
            "idempotency_key": IdempotencyKey("retry-a"),
        }
        values.update(changes)
        return RetryFailedActivityRun(**values)

    def test_public_command_is_exact_frozen_nominal_and_root_identical(self) -> None:
        self.require_language()
        self.assertIs(
            getattr(operations_root, "RetryFailedActivityRun", None),
            RetryFailedActivityRun,
        )
        self.assertTrue(dataclasses.is_dataclass(RetryFailedActivityRun))
        self.assertTrue(RetryFailedActivityRun.__dataclass_params__.frozen)
        self.assertEqual(
            tuple(
                field.name
                for field in dataclasses.fields(RetryFailedActivityRun)
            ),
            (
                "request_id",
                "prior_run_id",
                "expected_fence",
                "authority",
                "idempotency_key",
            ),
        )

        class HostileRetry(RetryFailedActivityRun):
            pass

        valid = self.command()
        with self.assertRaises(InvalidOperationCommand) as captured:
            HostileRetry(
                valid.request_id,
                valid.prior_run_id,
                valid.expected_fence,
                valid.authority,
                valid.idempotency_key,
            )
        self.assert_safe_error(captured.exception)

    def test_missing_module_guard_never_masks_nested_import_failures(self) -> None:
        nested = ModuleNotFoundError("nested dependency missing")
        nested.name = "nested_dependency"

        def missing_nested(_name):
            raise nested

        with self.assertRaises(ModuleNotFoundError) as captured:
            _load_retry_module(missing_nested)
        self.assertIs(captured.exception, nested)

        def partial_import(_name):
            raise ImportError("partial module export failure")

        with self.assertRaises(ImportError):
            _load_retry_module(partial_import)

    def test_command_descriptor_is_exact_and_redacted(self) -> None:
        command = self.command()
        self.assertEqual(
            command.descriptor(),
            {
                "command": "retry-as-new-run",
                "request_id": "request-a",
                "prior_run_id": "run-a",
                "expected_fence": {
                    "worker_id": "worker-a",
                    "generation": 7,
                },
                "actor_id": "operator-a",
                "idempotency_key": "retry-a",
            },
        )
        descriptor = repr(command.descriptor())
        rendered = f"{command!r} {descriptor}"
        for forbidden in (
            "authority-a",
            "claimed_at",
            "lease_expires_at",
            "secret",
            "endpoint",
        ):
            self.assertNotIn(forbidden, rendered)
        self.assertNotIn("scopes", descriptor)
        self.assertIn("scopes", repr(command))

    def test_command_fingerprint_has_four_exact_golden_vectors(self) -> None:
        vectors = (
            (
                self.command(),
                "1ed9a460092e273312d49a6b72dfe299aec069324709d4f780319a2b8612b4e7",
            ),
            (
                self.command(request_id="request-b"),
                "117f758be1189e402b77236f7af7e8ffa185c904a59e9e9ceda626325b8a4bb7",
            ),
            (
                self.command(
                    expected_fence=ExecutionLeaseFence("worker-a", 8)
                ),
                "6b5e5ee296e679c092bc917446207dd5231168428765b6796306b740790144ac",
            ),
            (
                self.command(
                    prior_run_id=RunId("run-b"),
                    authority=self.authority(
                        actor_id="operator-b",
                        reference="authority-b",
                    ),
                ),
                "e1ecd3b403c6d6beb33f6f154049513d54632e59b4be5614a189327a654ae6ca",
            ),
        )
        for command, expected in vectors:
            with self.subTest(command=command.descriptor()):
                self.assertEqual(command.intent_fingerprint(), expected)

    def test_fingerprint_includes_exact_intent_and_excludes_replay_coordinates(
        self,
    ) -> None:
        command = self.command()
        changed = (
            self.command(request_id="request-b"),
            self.command(prior_run_id=RunId("run-b")),
            self.command(expected_fence=ExecutionLeaseFence("worker-b", 7)),
            self.command(expected_fence=ExecutionLeaseFence("worker-a", 8)),
            self.command(authority=self.authority(actor_id="operator-b")),
            self.command(authority=self.authority(reference="authority-b")),
        )
        for candidate in changed:
            with self.subTest(candidate=candidate.descriptor()):
                self.assertNotEqual(
                    candidate.intent_fingerprint(),
                    command.intent_fingerprint(),
                )

        self.assertEqual(
            self.command(
                authority=self.authority(
                    scopes=(
                        RecoveryScope.OPERATE,
                        RecoveryScope.RENEW_CLAIM,
                    )
                )
            ).intent_fingerprint(),
            command.intent_fingerprint(),
        )
        self.assertEqual(
            self.command(
                idempotency_key=IdempotencyKey("retry-other")
            ).intent_fingerprint(),
            command.intent_fingerprint(),
        )

    def test_command_rejects_every_non_nominal_or_unbounded_coordinate(self) -> None:
        self.require_language()

        class HostileRunId(RunId):
            pass

        class HostileFence(ExecutionLeaseFence):
            pass

        class HostileAuthority(RecoveryAuthority):
            pass

        class HostileIdempotency(IdempotencyKey):
            pass

        class HostileText(str):
            pass

        cases = (
            ({"request_id": ""}, ""),
            ({"request_id": None}, ""),
            ({"request_id": True}, ""),
            ({"request_id": 7}, ""),
            ({"request_id": HostileText("hostile-request")}, "hostile-request"),
            ({"request_id": "x" * 513}, "x" * 513),
            ({"request_id": "request\ncanary"}, "request\ncanary"),
            ({"prior_run_id": "run-a"}, "run-a"),
            ({"prior_run_id": HostileRunId("run-a")}, "run-a"),
            ({"expected_fence": {"worker_id": "worker-a"}}, "worker-a"),
            ({"expected_fence": HostileFence("worker-a", 7)}, "worker-a"),
            ({"authority": "authority-canary"}, "authority-canary"),
            (
                {"authority": HostileAuthority(
                    "operator-a",
                    "hostile-reference",
                    (RecoveryScope.OPERATE,),
                )},
                "hostile-reference",
            ),
            ({"idempotency_key": "retry-a"}, "retry-a"),
            (
                {"idempotency_key": HostileIdempotency("hostile-key")},
                "hostile-key",
            ),
        )
        for changes, canary in cases:
            with self.subTest(changes=tuple(changes)):
                with self.assertRaises(InvalidOperationCommand) as captured:
                    self.command(**changes)
                self.assert_safe_error(captured.exception, canary)


class RetryIdentityAndEvidenceTests(unittest.TestCase):
    def assert_safe_record_error(
        self, error: BaseException, *canaries: str
    ) -> None:
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = f"{error!s} {error!r}"
        self.assertLessEqual(len(rendered), 512)
        for canary in canaries:
            self.assertNotIn(canary, rendered)

    def test_retry_identity_matches_exact_postgres_integer_domain(self) -> None:
        self.assertEqual(RetryIdentity(1), RetryIdentity(1, None))
        self.assertEqual(
            RetryIdentity(MAX_ATTEMPT, "run-prior"),
            RetryIdentity(MAX_ATTEMPT, "run-prior"),
        )
        rejected = (
            (True, None),
            (0, None),
            (-1, None),
            (1, "run-prior"),
            (2, None),
            (MAX_ATTEMPT + 1, "run-prior"),
        )
        for attempt, prior in rejected:
            with self.subTest(attempt=attempt, prior=prior):
                with self.assertRaises(OperationsRecordError) as captured:
                    RetryIdentity(attempt, prior)
                self.assert_safe_record_error(captured.exception)

    def retry_evidence(
        self,
        *,
        prior: ExecutionLeaseFence | None = None,
        replacement: ExecutionLeaseFence | None | object = ...,
        run_id: RunId | object = RunId("run-a"),
    ) -> ExecutionLeaseRecoveryEvidence:
        prior_fence = prior or ExecutionLeaseFence("worker-a", 7)
        replacement_fence = (
            prior_fence if replacement is ... else replacement
        )
        return ExecutionLeaseRecoveryEvidence(
            RecoveryDecisionKind.RETRY_AS_NEW_RUN,
            run_id,
            prior_fence,
            replacement_fence,
        )

    def test_retry_evidence_consumes_exact_fence_without_rotation(self) -> None:
        evidence = self.retry_evidence()
        self.assertEqual(
            evidence.descriptor(),
            {
                "decision": "retry-as-new-run",
                "retained_run_id": "run-a",
                "prior_fence": {
                    "worker_id": "worker-a",
                    "generation": 7,
                },
                "replacement_fence": {
                    "worker_id": "worker-a",
                    "generation": 7,
                },
            },
        )
        maximum = ExecutionLeaseFence("worker-a", 2**63 - 1)
        self.assertEqual(
            self.retry_evidence(prior=maximum).replacement_fence,
            maximum,
        )

    def test_retry_evidence_rejects_every_nonidentical_fence_shape(self) -> None:
        self.retry_evidence()

        class HostileFence(ExecutionLeaseFence):
            pass

        class HostileRunId(RunId):
            pass

        cases = (
            ({"replacement": None}, ()),
            (
                {"replacement": ExecutionLeaseFence("worker-b", 7)},
                ("worker-b",),
            ),
            (
                {"replacement": ExecutionLeaseFence("worker-a", 8)},
                (),
            ),
            (
                {"replacement": HostileFence("worker-a", 7)},
                ("worker-a",),
            ),
            (
                {"prior": HostileFence("worker-a", 7)},
                ("worker-a",),
            ),
            ({"run_id": HostileRunId("run-hostile")}, ("run-hostile",)),
        )
        for changes, canaries in cases:
            with self.subTest(changes=tuple(changes)):
                with self.assertRaises(OperationsRecordError) as captured:
                    self.retry_evidence(**changes)
                self.assert_safe_record_error(captured.exception, *canaries)

    def test_existing_claim_recovery_fence_sum_remains_exact(self) -> None:
        prior = ExecutionLeaseFence("worker-a", 7)
        accepted = (
            (
                RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
                ExecutionLeaseFence("worker-a", 8),
            ),
            (
                RecoveryDecisionKind.RENEW_EXPIRED_CLAIM,
                ExecutionLeaseFence("worker-a", 8),
            ),
            (
                RecoveryDecisionKind.TAKE_OVER_EXPIRED_CLAIM,
                ExecutionLeaseFence("worker-b", 8),
            ),
            (RecoveryDecisionKind.ABANDON_EXPIRED_CLAIM, None),
        )
        for decision, replacement in accepted:
            with self.subTest(decision=decision):
                evidence = ExecutionLeaseRecoveryEvidence(
                    decision,
                    RunId("run-a"),
                    prior,
                    replacement,
                )
                self.assertIs(evidence.decision_kind, decision)


class ActivityRunRetryOwnershipTests(unittest.TestCase):
    def test_module_has_one_exhaustive_operations_inventory_row(self) -> None:
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
            if row["module"] == RETRY_MODULE
        ]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["owner"], "operation")
        self.assertEqual(row["destination"], RETRY_MODULE)
        self.assertEqual(row["optional_external_dependencies"], [])
        self.assertEqual(
            row["canonical_public_exports"],
            ["ActivityRunRetryResult", "RetryFailedActivityRun"],
        )
        self.assertEqual(
            row["protecting_tests"],
            [
                "tests/test_activity_run_retry_contract.py",
                "tests/test_activity_run_retry_result.py",
            ],
        )

    def test_pure_module_has_no_store_interpreter_or_postgres_import(self) -> None:
        self.assertIsNotNone(retry_module, "activity-run retry module is missing")
        source_path = (
            Path(__file__).parents[1]
            / "src"
            / "control_plane_kit_operations"
            / "activity_run_retry.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)
        forbidden = (
            "postgres",
            "stores",
            "unit_of_work",
            "interpreter",
        )
        self.assertEqual(
            sorted(
                name
                for name in imported
                if any(part in name for part in forbidden)
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
