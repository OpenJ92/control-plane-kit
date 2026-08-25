from __future__ import annotations

import importlib
import unittest

from control_plane_kit_core.operations import (
    ActivityEventKind,
    ActivityRunStatus,
    RecoveryDecisionKind,
    RecoveryScope,
    RunId,
)
from control_plane_kit_operations.execution_lease_recovery import RecoveryAuthority
from control_plane_kit_operations.records import (
    BoundedEvidence,
    FailureEvidence,
)
from control_plane_kit_core.operations import FailureCategory
from control_plane_kit_operations.workflows import IdempotencyKey, InvalidOperationCommand


TARGET_MODULE = "control_plane_kit_operations.failed_run_compensation"


def target_module():
    try:
        return importlib.import_module(TARGET_MODULE)
    except ModuleNotFoundError as error:
        if error.name != TARGET_MODULE:
            raise
        return None


class FailedRunCompensationCommandContractTests(unittest.TestCase):
    def require_contract(self):
        module = target_module()
        self.assertIsNotNone(
            module,
            "failed-run compensation Operations contract is missing",
        )
        return module

    def command(self, module, **changes):
        values = {
            "workspace_id": "workspace-a",
            "request_id": "request-a",
            "run_id": RunId("run-a"),
            "plan_id": "plan-a",
            "expected_current_graph_id": "graph-current",
            "desired_graph_id": "graph-desired",
            "expected_desired_graph_revision": 7,
            "execution_intent_fingerprint": "a" * 64,
            "authority": RecoveryAuthority(
                "operator-a",
                "authority-reference-canary",
                (RecoveryScope.COMPENSATE,),
            ),
            "reason": self.require_contract().FailedRunCompensationReason.POST_EFFECT_FAILURE,
            "source_failure": FailureEvidence(
                FailureCategory.TERMINAL,
                "runtime.effect-failed",
                "runtime effect reported failure",
                BoundedEvidence.from_mapping({"phase": "start"}),
            ),
            "idempotency_key": IdempotencyKey("compensate-a"),
        }
        values.update(changes)
        return module.BeginFailedRunCompensation(**values)

    def test_command_is_closed_authorized_and_fingerprinted(self) -> None:
        module = self.require_contract()
        command = self.command(module)

        self.assertEqual(
            command.descriptor(),
            {
                "command": "begin-compensation",
                "workspace_id": "workspace-a",
                "request_id": "request-a",
                "run_id": "run-a",
                "plan_id": "plan-a",
                "expected_current_graph_id": "graph-current",
                "desired_graph_id": "graph-desired",
                "expected_desired_graph_revision": 7,
                "execution_intent_fingerprint": "a" * 64,
                "actor_id": "operator-a",
                "reason": "post-effect-failure",
                "source_failure": {
                    "category": "terminal",
                    "code": "runtime.effect-failed",
                    "message": "runtime effect reported failure",
                    "details": {"phase": "start"},
                },
                "idempotency_key": "compensate-a",
            },
        )
        self.assertRegex(command.intent_fingerprint(), r"^[0-9a-f]{64}$")
        self.assertNotIn("authority-reference-canary", str(command))
        self.assertNotIn("authority-reference-canary", repr(command))
        self.assertNotIn(
            "authority-reference-canary",
            str(command.descriptor()),
        )

        with self.assertRaises(InvalidOperationCommand):
            self.command(
                module,
                authority=RecoveryAuthority(
                    "operator-a",
                    "authority-reference-canary",
                    (RecoveryScope.OPERATE,),
                ),
            )
        with self.assertRaises(InvalidOperationCommand):
            self.command(module, execution_intent_fingerprint="not-a-sha")
        with self.assertRaises(InvalidOperationCommand):
            self.command(module, expected_desired_graph_revision=-1)

    def test_result_binds_exact_program_action_event_and_compensating_fold(self) -> None:
        module = self.require_contract()
        names = (
            "BeginFailedRunCompensation",
            "FailedRunCompensationCommandService",
            "FailedRunCompensationConflict",
            "FailedRunCompensationDenied",
            "FailedRunCompensationIdempotencyConflict",
            "FailedRunCompensationNotFound",
            "FailedRunCompensationRecord",
            "FailedRunCompensationResult",
        )
        for name in names:
            with self.subTest(name=name):
                self.assertTrue(hasattr(module, name))

        source = importlib.import_module(TARGET_MODULE)
        self.assertIn("ActivityRunStatus.COMPENSATING", source.__loader__.get_source(TARGET_MODULE))
        self.assertIn("ActivityEventKind.RUN_COMPENSATION_STARTED", source.__loader__.get_source(TARGET_MODULE))
        self.assertIn("RecoveryDecisionKind.BEGIN_COMPENSATION", source.__loader__.get_source(TARGET_MODULE))

    def test_command_rejects_subclasses_and_open_failure_material(self) -> None:
        module = self.require_contract()

        class HostileRunId(RunId):
            pass

        invalid = (
            lambda: self.command(module, run_id=HostileRunId("run-a")),
            lambda: self.command(
                module,
                source_failure=FailureEvidence(
                    FailureCategory.TERMINAL,
                    "runtime.effect-failed",
                    "runtime effect reported failure",
                    BoundedEvidence.from_mapping(
                        {"provider_message": "credential-canary"}
                    ),
                ),
            ),
        )
        for factory in invalid:
            with self.subTest(factory=factory):
                with self.assertRaises((InvalidOperationCommand, ValueError)):
                    factory()


if __name__ == "__main__":
    unittest.main()
