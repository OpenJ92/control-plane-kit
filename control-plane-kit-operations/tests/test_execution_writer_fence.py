from __future__ import annotations

import dataclasses
import ast
import inspect
import textwrap
import unittest

from control_plane_kit_core.operations.lifecycle import FailureCategory
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.coordinator import (
    ActivityRealizationContext,
    ExecuteActivityRun,
    ExecutionCoordinator,
)
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
import control_plane_kit_operations.lifecycle as lifecycle_module
from control_plane_kit_operations.lifecycle import (
    CancelActivityRun,
    CompleteActivityRun,
    ExecutionWorkerAuthority,
    FailActivityRun,
    PauseActivityRun,
    ResumeActivityRun,
    StartActivityRun,
)
from control_plane_kit_operations.records import BoundedEvidence, FailureEvidence
from control_plane_kit_operations.runtime_effects import RuntimeEffectRequest
from control_plane_kit_operations.workflows import IdempotencyKey, InvalidOperationCommand


class ExecutionWriterFenceLanguageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.authority = ExecutionWorkerAuthority(
            "worker-a",
            (PolicyScope.EXECUTION_OPERATE,),
        )
        self.fence = ExecutionLeaseFence("worker-a", 7)
        self.key = IdempotencyKey("writer-fence")

    def test_all_post_claim_commands_have_one_common_fence_position(self) -> None:
        expected_prefix = ("run_id", "authority", "fence", "idempotency_key")

        for command_type in (
            StartActivityRun,
            PauseActivityRun,
            ResumeActivityRun,
            CompleteActivityRun,
            FailActivityRun,
            CancelActivityRun,
        ):
            with self.subTest(command=command_type.__name__):
                fields = tuple(field.name for field in dataclasses.fields(command_type))
                self.assertEqual(fields[:4], expected_prefix)

    def test_lifecycle_commands_require_exact_worker_fence_congruence(self) -> None:
        common = {
            "run_id": "run-a",
            "authority": self.authority,
            "fence": self.fence,
            "idempotency_key": self.key,
        }
        commands = (
            StartActivityRun(**common),
            PauseActivityRun(**common, evidence=BoundedEvidence()),
            ResumeActivityRun(**common),
            CompleteActivityRun(**common, evidence=BoundedEvidence()),
            FailActivityRun(
                **common,
                failure=FailureEvidence(
                    FailureCategory.TERMINAL,
                    "failed",
                    "failed safely",
                ),
            ),
            CancelActivityRun(**common, evidence=BoundedEvidence()),
        )
        self.assertTrue(all(command.fence is self.fence for command in commands))

        foreign = ExecutionLeaseFence("different-worker-canary", 7)
        factories = (
            lambda: StartActivityRun("run-a", self.authority, foreign, self.key),
            lambda: PauseActivityRun("run-a", self.authority, foreign, self.key),
            lambda: ResumeActivityRun("run-a", self.authority, foreign, self.key),
            lambda: CompleteActivityRun("run-a", self.authority, foreign, self.key),
            lambda: FailActivityRun(
                "run-a",
                self.authority,
                foreign,
                self.key,
                FailureEvidence(
                    FailureCategory.TERMINAL,
                    "failed",
                    "failed safely",
                ),
            ),
            lambda: CancelActivityRun("run-a", self.authority, foreign, self.key),
        )
        for factory in factories:
            with self.subTest(factory=factory.__code__.co_firstlineno):
                with self.assertRaises(InvalidOperationCommand) as captured:
                    factory()
                self.assertEqual(
                    str(captured.exception),
                    "authority and fence must agree",
                )
                self.assertIsNone(captured.exception.__cause__)
                self.assertIsNone(captured.exception.__context__)
                self.assertNotIn(
                    "different-worker-canary",
                    repr(captured.exception),
                )

    def test_post_claim_intent_fingerprint_binds_generation(self) -> None:
        first = StartActivityRun(
            "run-a",
            self.authority,
            ExecutionLeaseFence("worker-a", 1),
            self.key,
        )
        second = StartActivityRun(
            "run-a",
            self.authority,
            ExecutionLeaseFence("worker-a", 2),
            self.key,
        )

        self.assertNotEqual(
            lifecycle_module._fingerprint(first),
            lifecycle_module._fingerprint(second),
        )

    def test_coordinator_command_and_realization_context_carry_the_fence(self) -> None:
        command_fields = tuple(
            field.name for field in dataclasses.fields(ExecuteActivityRun)
        )
        context_fields = tuple(
            field.name for field in dataclasses.fields(ActivityRealizationContext)
        )

        self.assertEqual(
            command_fields,
            ("run_id", "authority", "fence", "idempotency_key", "max_effects"),
        )
        self.assertIn("fence", context_fields)
        command = ExecuteActivityRun(
            "run-a",
            self.authority,
            self.fence,
            self.key,
        )
        self.assertIs(command.fence, self.fence)

        with self.assertRaises(InvalidOperationCommand) as captured:
            ExecuteActivityRun(
                "run-a",
                self.authority,
                ExecutionLeaseFence("different-worker-canary", 7),
                self.key,
            )
        self.assertEqual(str(captured.exception), "authority and fence must agree")
        self.assertNotIn("different-worker-canary", repr(captured.exception))

    def test_provider_runtime_request_language_has_no_execution_fence(self) -> None:
        fields = tuple(field.name for field in dataclasses.fields(RuntimeEffectRequest))

        self.assertNotIn("fence", fields)
        self.assertNotIn("claim_generation", fields)
        self.assertNotIn("worker_authority", fields)

    def test_all_coordinator_writer_phases_use_the_shared_locked_boundary(self) -> None:
        for method_name in ("_load_context", "_record_step_event", "_record_outcome"):
            method = getattr(ExecutionCoordinator, method_name)
            tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
            called_names = tuple(
                node.func.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            )
            with self.subTest(method=method_name):
                self.assertEqual(called_names.count("_locked_request_and_run"), 1)


if __name__ == "__main__":
    unittest.main()
