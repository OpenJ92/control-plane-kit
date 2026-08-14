from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, dataclass, fields
import importlib
import inspect
import json
import os
from pathlib import Path
from types import UnionType
from typing import get_args
import unittest

from control_plane_kit_core.operations import (
    ActivityRunStatus,
    DeploymentProgramStage,
    EffectAttemptIdentity,
    ExecutionRequestStatus,
)
from control_plane_kit_core.planning import ActivityId
from control_plane_kit_operations.deployment_program import (
    DeploymentProgramReference,
    InvalidDeploymentProgramContract,
)
from control_plane_kit_operations.records import (
    ActivityPlanStatus,
    OperationSessionStatus,
)


PROJECTION_NAMES = (
    "DeploymentCompleted",
    "DeploymentSessionStopped",
    "DeploymentPlanStopped",
    "DeploymentNoChanges",
    "DeploymentReviewBlocked",
    "DeploymentApprovalRequestReady",
    "DeploymentApprovalRequired",
    "DeploymentApprovalRejected",
    "DeploymentReadinessRequired",
    "DeploymentAdmissionReady",
    "DeploymentClaimReady",
    "DeploymentExecutionStopped",
    "DeploymentExecutionReady",
    "DeploymentExecutionRunning",
    "DeploymentExecutionPaused",
    "DeploymentEffectInFlight",
    "DeploymentRecoveryRequired",
    "DeploymentCompensationInProgress",
    "DeploymentExecutionFailed",
    "DeploymentExecutionSettled",
    "DeploymentAdvancementReady",
)
EXPECTED_EXPORTS = ("DeploymentProgramProjection", *PROJECTION_NAMES)
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PACKAGE_ROOT / "src" / "control_plane_kit_operations"


@dataclass(frozen=True)
class _Case:
    name: str
    arguments: dict[str, object]
    stage: DeploymentProgramStage
    projection: str
    identity_fields: tuple[str, ...] = ()


def _contract():
    try:
        module = importlib.import_module(
            "control_plane_kit_operations.deployment_program_projections"
        )
    except ModuleNotFoundError as error:
        raise AssertionError("missing #1630 deployment-program projections") from error
    import control_plane_kit_operations as operations

    missing = tuple(name for name in EXPECTED_EXPORTS if not hasattr(operations, name))
    if missing:
        raise AssertionError(
            "missing #1630 deployment-program projection exports: "
            + ", ".join(missing)
        )
    return operations, module


def _reference() -> DeploymentProgramReference:
    return DeploymentProgramReference("workspace-a", "plan-a")


def _attempt(run_id: str = "run-a") -> EffectAttemptIdentity:
    return EffectAttemptIdentity(run_id, "activity-a", 1)


def _cases() -> tuple[_Case, ...]:
    return (
        _Case(
            "DeploymentCompleted",
            {"reference": _reference(), "event_id": "event-a"},
            DeploymentProgramStage.ADVANCE,
            "completed",
            ("event_id",),
        ),
        _Case(
            "DeploymentSessionStopped",
            {
                "reference": _reference(),
                "session_status": OperationSessionStatus.CLOSED,
            },
            DeploymentProgramStage.PLAN,
            "session-stopped",
        ),
        _Case(
            "DeploymentPlanStopped",
            {
                "reference": _reference(),
                "plan_status": ActivityPlanStatus.SUPERSEDED,
            },
            DeploymentProgramStage.PLAN,
            "plan-stopped",
        ),
        _Case(
            "DeploymentNoChanges",
            {"reference": _reference()},
            DeploymentProgramStage.PLAN,
            "no-changes",
        ),
        _Case(
            "DeploymentReviewBlocked",
            {"reference": _reference()},
            DeploymentProgramStage.PLAN,
            "review-blocked",
        ),
        _Case(
            "DeploymentApprovalRequestReady",
            {"reference": _reference()},
            DeploymentProgramStage.APPROVE,
            "approval-request-ready",
        ),
        _Case(
            "DeploymentApprovalRequired",
            {"reference": _reference(), "approval_request_id": "approval-a"},
            DeploymentProgramStage.APPROVE,
            "approval-required",
            ("approval_request_id",),
        ),
        _Case(
            "DeploymentApprovalRejected",
            {
                "reference": _reference(),
                "approval_request_id": "approval-a",
                "approval_decision_id": "decision-a",
            },
            DeploymentProgramStage.APPROVE,
            "approval-rejected",
            ("approval_request_id", "approval_decision_id"),
        ),
        _Case(
            "DeploymentReadinessRequired",
            {
                "reference": _reference(),
                "approval_request_id": "approval-a",
                "approval_decision_id": "decision-a",
                "activity_id": ActivityId("activity-a"),
            },
            DeploymentProgramStage.ADMIT,
            "readiness-required",
            ("approval_request_id", "approval_decision_id"),
        ),
        _Case(
            "DeploymentAdmissionReady",
            {
                "reference": _reference(),
                "approval_request_id": "approval-a",
                "approval_decision_id": "decision-a",
            },
            DeploymentProgramStage.ADMIT,
            "admission-ready",
            ("approval_request_id", "approval_decision_id"),
        ),
        _Case(
            "DeploymentClaimReady",
            {"reference": _reference(), "execution_request_id": "request-a"},
            DeploymentProgramStage.CLAIM,
            "claim-ready",
            ("execution_request_id",),
        ),
        _Case(
            "DeploymentExecutionStopped",
            {
                "reference": _reference(),
                "execution_request_id": "request-a",
                "execution_request_status": ExecutionRequestStatus.CANCELLED,
            },
            DeploymentProgramStage.CLAIM,
            "execution-stopped",
            ("execution_request_id",),
        ),
        _Case(
            "DeploymentExecutionReady",
            {"reference": _reference(), "run_id": "run-a"},
            DeploymentProgramStage.EXECUTE,
            "execution-ready",
            ("run_id",),
        ),
        _Case(
            "DeploymentExecutionRunning",
            {"reference": _reference(), "run_id": "run-a"},
            DeploymentProgramStage.EXECUTE,
            "execution-running",
            ("run_id",),
        ),
        _Case(
            "DeploymentExecutionPaused",
            {"reference": _reference(), "run_id": "run-a"},
            DeploymentProgramStage.EXECUTE,
            "execution-paused",
            ("run_id",),
        ),
        _Case(
            "DeploymentEffectInFlight",
            {
                "reference": _reference(),
                "run_id": "run-a",
                "run_status": ActivityRunStatus.RUNNING,
                "effect_attempt": _attempt(),
            },
            DeploymentProgramStage.EXECUTE,
            "effect-in-flight",
        ),
        _Case(
            "DeploymentRecoveryRequired",
            {
                "reference": _reference(),
                "run_id": "run-a",
                "run_status": ActivityRunStatus.RUNNING,
                "effect_attempt": _attempt(),
            },
            DeploymentProgramStage.EXECUTE,
            "recovery-required",
        ),
        _Case(
            "DeploymentCompensationInProgress",
            {"reference": _reference(), "run_id": "run-a"},
            DeploymentProgramStage.EXECUTE,
            "compensation-in-progress",
            ("run_id",),
        ),
        _Case(
            "DeploymentExecutionFailed",
            {
                "reference": _reference(),
                "run_id": "run-a",
                "run_status": ActivityRunStatus.FAILED,
            },
            DeploymentProgramStage.EXECUTE,
            "execution-failed",
            ("run_id",),
        ),
        _Case(
            "DeploymentExecutionSettled",
            {
                "reference": _reference(),
                "run_id": "run-a",
                "run_status": ActivityRunStatus.COMPENSATED,
            },
            DeploymentProgramStage.EXECUTE,
            "execution-settled",
            ("run_id",),
        ),
        _Case(
            "DeploymentAdvancementReady",
            {"reference": _reference(), "run_id": "run-a"},
            DeploymentProgramStage.ADVANCE,
            "advancement-ready",
            ("run_id",),
        ),
    )


def _descriptor(case: _Case) -> dict[str, object]:
    descriptor: dict[str, object] = {
        "projection": case.projection,
        "reference": _reference().descriptor(),
        "stage": case.stage.value,
    }
    for name, value in case.arguments.items():
        if name == "reference":
            continue
        if isinstance(value, ActivityId):
            descriptor[name] = value.value
        elif isinstance(value, EffectAttemptIdentity):
            descriptor[name] = value.descriptor()
        elif hasattr(value, "value"):
            descriptor[name] = value.value
        else:
            descriptor[name] = value
    return descriptor


class _HostileText(str):
    def __len__(self):
        raise AssertionError("hostile text length was observed")


class _ActivityIdSubclass(ActivityId):
    pass


class _EffectAttemptSubclass(EffectAttemptIdentity):
    pass


class DeploymentProgramProjectionTests(unittest.TestCase):
    def assert_contract_error(self, callback, *canaries: str) -> None:
        with self.assertRaises(InvalidDeploymentProgramContract) as captured:
            callback()
        error = captured.exception
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = f"{error!s} {error!r}"
        self.assertLessEqual(len(rendered), 512)
        for canary in canaries:
            if canary:
                self.assertNotIn(canary, rendered)

    def test_existing_nominal_vocabulary_and_inventory_owner_are_current(self) -> None:
        self.assertEqual(
            tuple(DeploymentProgramStage),
            (
                DeploymentProgramStage.PLAN,
                DeploymentProgramStage.APPROVE,
                DeploymentProgramStage.ADMIT,
                DeploymentProgramStage.CLAIM,
                DeploymentProgramStage.EXECUTE,
                DeploymentProgramStage.ADVANCE,
            ),
        )
        self.assertEqual(
            tuple(ExecutionRequestStatus),
            (
                ExecutionRequestStatus.QUEUED,
                ExecutionRequestStatus.CLAIMED,
                ExecutionRequestStatus.CANCELLED,
                ExecutionRequestStatus.ABANDONED,
            ),
        )
        self.assertEqual(len(tuple(ActivityRunStatus)), 10)
        self.assertEqual(ActivityId("activity-a").value, "activity-a")
        self.assertEqual(_attempt().descriptor()["run_id"], "run-a")
        self.assertEqual(_reference().descriptor()["plan_id"], "plan-a")

        inventory_path = os.environ.get("CPK_PACKAGE_MODULE_INVENTORY")
        if inventory_path is None:
            inventory_path = str(
                PACKAGE_ROOT.parent
                / "docs"
                / "architecture"
                / "package-module-inventory.json"
            )
        inventory = json.loads(Path(inventory_path).read_text(encoding="utf-8"))
        rows = tuple(
            row
            for row in inventory["modules"]
            if row.get("destination")
            == "control_plane_kit.operations.application.deploy.values"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["owner"], "operation")

    def test_module_root_union_and_frozen_slotted_values_are_exact(self) -> None:
        operations, module = _contract()

        self.assertEqual(tuple(module.__all__), EXPECTED_EXPORTS)
        for name in EXPECTED_EXPORTS:
            with self.subTest(name=name):
                self.assertIs(getattr(operations, name), getattr(module, name))
                self.assertIn(name, operations.__all__)
        union = module.DeploymentProgramProjection
        self.assertIsInstance(union, UnionType)
        self.assertEqual(
            tuple(item.__name__ for item in get_args(union)),
            PROJECTION_NAMES,
        )

        for case in _cases():
            value = getattr(module, case.name)(**case.arguments)
            with self.subTest(case=case.name):
                self.assertFalse(hasattr(value, "__dict__"))
                with self.assertRaises(FrozenInstanceError):
                    value.extra = "forbidden"

    def test_all_variants_have_exact_fields_stages_and_descriptors(self) -> None:
        _, module = _contract()

        for case in _cases():
            variant = getattr(module, case.name)
            value = variant(**case.arguments)
            with self.subTest(case=case.name):
                self.assertEqual(
                    tuple(item.name for item in fields(value)),
                    tuple(case.arguments),
                )
                self.assertIs(value.stage, case.stage)
                self.assertNotIn("stage", inspect.signature(variant).parameters)
                self.assertEqual(value.descriptor(), _descriptor(case))

    def test_every_variant_requires_exact_reference_and_required_arguments(self) -> None:
        _, module = _contract()

        class _ReferenceSubclass(DeploymentProgramReference):
            pass

        for case in _cases():
            variant = getattr(module, case.name)
            for candidate in (object(), _ReferenceSubclass("workspace-a", "plan-a")):
                arguments = dict(case.arguments)
                arguments["reference"] = candidate
                with self.subTest(case=case.name, candidate=type(candidate).__name__):
                    self.assert_contract_error(lambda: variant(**arguments))
            for field_name in case.arguments:
                arguments = dict(case.arguments)
                del arguments[field_name]
                with self.subTest(case=case.name, missing=field_name):
                    with self.assertRaises(TypeError):
                        variant(**arguments)

    def test_every_scalar_identity_path_is_exact_bounded_text(self) -> None:
        _, module = _contract()
        invalid = (
            (object(), ""),
            (True, "True"),
            ("", ""),
            ("x" * 513, "x" * 513),
            ("identity\nsecret-canary", "secret-canary"),
            (_HostileText("hostile-identity-canary"), "hostile-identity-canary"),
        )

        for case in _cases():
            variant = getattr(module, case.name)
            for field_name in case.identity_fields:
                valid = dict(case.arguments)
                valid[field_name] = "v" * 512
                with self.subTest(case=case.name, field=field_name, boundary="max"):
                    self.assertEqual(getattr(variant(**valid), field_name), "v" * 512)
                for candidate, canary in invalid:
                    arguments = dict(case.arguments)
                    arguments[field_name] = candidate
                    with self.subTest(
                        case=case.name,
                        field=field_name,
                        candidate=type(candidate).__name__,
                    ):
                        self.assert_contract_error(
                            lambda arguments=arguments: variant(**arguments),
                            canary,
                        )

    def test_status_subsets_are_exhaustive_and_non_substitutable(self) -> None:
        _, module = _contract()
        matrices = (
            (
                "DeploymentSessionStopped",
                "session_status",
                tuple(OperationSessionStatus),
                (OperationSessionStatus.CLOSED, OperationSessionStatus.CANCELLED),
            ),
            (
                "DeploymentPlanStopped",
                "plan_status",
                tuple(ActivityPlanStatus),
                (ActivityPlanStatus.SUPERSEDED, ActivityPlanStatus.CANCELLED),
            ),
            (
                "DeploymentExecutionStopped",
                "execution_request_status",
                tuple(ExecutionRequestStatus),
                (ExecutionRequestStatus.CANCELLED, ExecutionRequestStatus.ABANDONED),
            ),
            (
                "DeploymentEffectInFlight",
                "run_status",
                tuple(ActivityRunStatus),
                (
                    ActivityRunStatus.RUNNING,
                    ActivityRunStatus.PAUSED,
                    ActivityRunStatus.COMPENSATING,
                ),
            ),
            (
                "DeploymentRecoveryRequired",
                "run_status",
                tuple(ActivityRunStatus),
                (
                    ActivityRunStatus.RUNNING,
                    ActivityRunStatus.PAUSED,
                    ActivityRunStatus.COMPENSATING,
                ),
            ),
            (
                "DeploymentExecutionFailed",
                "run_status",
                tuple(ActivityRunStatus),
                (ActivityRunStatus.FAILED,),
            ),
            (
                "DeploymentExecutionSettled",
                "run_status",
                tuple(ActivityRunStatus),
                (
                    ActivityRunStatus.COMPENSATED,
                    ActivityRunStatus.PARTIALLY_FAILED,
                    ActivityRunStatus.UNCOMPENSATED_FAILURE,
                    ActivityRunStatus.CANCELLED,
                ),
            ),
        )
        cases = {case.name: case for case in _cases()}
        for name, field_name, universe, accepted in matrices:
            variant = getattr(module, name)
            for status in universe:
                arguments = dict(cases[name].arguments)
                arguments[field_name] = status
                with self.subTest(case=name, status=status):
                    if status in accepted:
                        self.assertIs(getattr(variant(**arguments), field_name), status)
                    else:
                        self.assert_contract_error(lambda: variant(**arguments))
            wrong_family = (
                ActivityPlanStatus.CANCELLED
                if field_name != "plan_status"
                else ExecutionRequestStatus.CANCELLED
            )
            for candidate in (wrong_family, accepted[0].value, object()):
                arguments = dict(cases[name].arguments)
                arguments[field_name] = candidate
                with self.subTest(
                    case=name,
                    wrong_candidate=type(candidate).__name__,
                ):
                    self.assert_contract_error(lambda: variant(**arguments))

    def test_effect_attempt_is_exact_and_run_congruent_for_both_variants(self) -> None:
        _, module = _contract()
        for name in ("DeploymentEffectInFlight", "DeploymentRecoveryRequired"):
            variant = getattr(module, name)
            for status in (
                ActivityRunStatus.RUNNING,
                ActivityRunStatus.PAUSED,
                ActivityRunStatus.COMPENSATING,
            ):
                value = variant(_reference(), "r" * 256, status, _attempt("r" * 256))
                with self.subTest(case=name, status=status):
                    self.assertEqual(value.effect_attempt, _attempt("r" * 256))
            invalid = (
                object(),
                _EffectAttemptSubclass("run-a", "activity-a", 1),
                EffectAttemptIdentity("other-run", "activity-a", 1),
            )
            for attempt in invalid:
                with self.subTest(case=name, attempt=type(attempt).__name__):
                    self.assert_contract_error(
                        lambda attempt=attempt: variant(
                            _reference(),
                            "run-a",
                            ActivityRunStatus.RUNNING,
                            attempt,
                        )
                    )

    def test_readiness_requires_and_preserves_exact_activity_id(self) -> None:
        _, module = _contract()
        variant = module.DeploymentReadinessRequired
        boundary = ActivityId("a" * 200)
        value = variant(
            _reference(),
            "approval-a",
            "decision-a",
            boundary,
        )
        self.assertIs(value.activity_id, boundary)
        self.assertEqual(value.descriptor()["activity_id"], "a" * 200)

        invalid = (
            object(),
            "activity-a",
            _ActivityIdSubclass("activity-a"),
        )
        for candidate in invalid:
            with self.subTest(candidate=type(candidate).__name__):
                self.assert_contract_error(
                    lambda candidate=candidate: variant(
                        _reference(),
                        "approval-a",
                        "decision-a",
                        candidate,
                    )
                )

    def test_repr_descriptors_and_errors_are_bounded_and_redacted(self) -> None:
        _, module = _contract()
        forbidden = (
            "secret://",
            "credential",
            "private_key",
            "provider_endpoint",
            "failure_body",
            "graph_descriptor",
            "readiness_evidence",
        )
        for case in _cases():
            value = getattr(module, case.name)(**case.arguments)
            rendered = f"{value!r} {value.descriptor()!r}"
            with self.subTest(case=case.name):
                self.assertLessEqual(len(rendered), 4096)
                for canary in forbidden:
                    self.assertNotIn(canary, rendered)

        self.assertIs(module.InvalidDeploymentProgramContract, InvalidDeploymentProgramContract)
        self.assertFalse(hasattr(module, "DeploymentProgramStateConflict"))
        self.assert_contract_error(
            lambda: module.DeploymentCompleted(
                _reference(),
                _HostileText("secret://candidate/error-canary"),
            ),
            "secret://candidate/error-canary",
        )

    def test_source_package_dag_inventory_and_effect_free_boundary_are_exact(self) -> None:
        _, module = _contract()
        source_path = SOURCE_ROOT / "deployment_program_projections.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))

        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        allowed = {
            "__future__",
            "dataclasses",
            "typing",
            "control_plane_kit_core.operations",
            "control_plane_kit_core.operations.lifecycle",
            "control_plane_kit_core.operations.recovery",
            "control_plane_kit_core.operations.services",
            "control_plane_kit_core.planning",
            "control_plane_kit_operations.deployment_program",
            "control_plane_kit_operations.records",
        }
        self.assertLessEqual(imports, allowed)
        for forbidden in (
            "postgres",
            "store",
            "schema",
            "adapter",
            "cpk_server",
            "socket",
            "subprocess",
        ):
            self.assertFalse(any(forbidden in imported for imported in imports))

        forbidden_calls = {
            "open",
            "connect",
            "commit",
            "rollback",
            "execute",
            "transaction",
            "callback",
        }
        observed_calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        } | {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertFalse(observed_calls & forbidden_calls)

        owner_tree = ast.parse(
            (SOURCE_ROOT / "deployment_program.py").read_text(encoding="utf-8")
        )
        owner_imports = {
            node.module
            for node in ast.walk(owner_tree)
            if isinstance(node, ast.ImportFrom) and node.module
        } | {
            alias.name
            for node in ast.walk(owner_tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertFalse(any("deployment_program_projections" in item for item in owner_imports))

        inventory_path = os.environ.get("CPK_PACKAGE_MODULE_INVENTORY")
        if inventory_path is None:
            inventory_path = str(
                PACKAGE_ROOT.parent
                / "docs"
                / "architecture"
                / "package-module-inventory.json"
            )
        inventory = json.loads(Path(inventory_path).read_text(encoding="utf-8"))
        rows = tuple(
            row
            for row in inventory["modules"]
            if row.get("destination")
            == "control_plane_kit.operations.application.deploy.values"
        )
        self.assertEqual(len(rows), 1)
        self.assertLessEqual(set(EXPECTED_EXPORTS), set(rows[0]["canonical_public_exports"]))
        operations = importlib.import_module("control_plane_kit_operations")
        for name in EXPECTED_EXPORTS:
            self.assertIs(getattr(module, name), getattr(operations, name))


if __name__ == "__main__":
    unittest.main()
