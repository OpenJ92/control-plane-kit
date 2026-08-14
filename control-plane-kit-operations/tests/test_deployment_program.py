from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
import importlib
from pathlib import Path
import unittest

from control_plane_kit_core.identity import (
    AuthenticatedPrincipal,
    PrincipalIdentity,
    PrincipalKind,
    TrustedCommandContext,
    WorkspaceGrant,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_operations.admission import ExternalReadinessAttestation
from control_plane_kit_operations.records import GraphProjectionLineage
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    InvalidOperationCommand,
)


EXPECTED_EXPORTS = (
    "DeploymentProgramCommand",
    "DeploymentProgramReference",
    "InvalidDeploymentProgramContract",
    "PrepareDeploymentProgram",
    "ProgressDeploymentProgram",
)
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PACKAGE_ROOT / "src" / "control_plane_kit_operations"


def _contract():
    try:
        module = importlib.import_module(
            "control_plane_kit_operations.deployment_program"
        )
    except ModuleNotFoundError as error:
        raise AssertionError("missing #1629 deployment-program command module") from error
    import control_plane_kit_operations as operations

    missing = tuple(name for name in EXPECTED_EXPORTS if not hasattr(operations, name))
    if missing:
        raise AssertionError(
            "missing #1629 deployment-program exports: " + ", ".join(missing)
        )
    return operations, module


def _context(workspace_id: str = "workspace-a") -> TrustedCommandContext:
    principal = AuthenticatedPrincipal(
        PrincipalIdentity("issuer.example", "operator-a", PrincipalKind.OPERATOR),
        (
            WorkspaceGrant(
                workspace_id,
                (PolicyScope.PLAN_REQUEST, PolicyScope.PLAN_EXECUTE),
            ),
        ),
    )
    return principal.command_context(workspace_id)


def _lineage(label: str) -> GraphProjectionLineage:
    return GraphProjectionLineage(f"graph-{label}", f"projection-{label}")


def _prepare(module, **changes):
    values = {
        "context": _context(),
        "desired": DeploymentGraph("desired-candidate"),
        "expected_current": _lineage("current"),
        "expected_desired": None,
        "expected_desired_graph_revision": 0,
        "title": "Deploy the desired topology",
        "idempotency_key": IdempotencyKey("program/prepare/a"),
        "approval_comment": None,
    }
    values.update(changes)
    return module.PrepareDeploymentProgram(**values)


def _progress(module, **changes):
    values = {
        "context": _context(),
        "reference": module.DeploymentProgramReference("workspace-a", "plan-a"),
        "readiness": (),
        "idempotency_key": IdempotencyKey("program/progress/a"),
    }
    values.update(changes)
    return module.ProgressDeploymentProgram(**values)


class _HostileText(str):
    def __len__(self):
        raise AssertionError("hostile text length was observed")


class _ReadinessSubclass(ExternalReadinessAttestation):
    pass


class DeploymentProgramCommandTests(unittest.TestCase):
    def assert_contract_error(self, callback, *canaries: str) -> None:
        _, module = _contract()
        with self.assertRaises(module.InvalidDeploymentProgramContract) as captured:
            callback()
        error = captured.exception
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        rendered = f"{error!s} {error!r}"
        self.assertLessEqual(len(rendered), 512)
        for canary in canaries:
            if canary:
                self.assertNotIn(canary, rendered)

    def test_module_root_union_and_frozen_slotted_values_are_exact(self) -> None:
        operations, module = _contract()

        self.assertEqual(tuple(module.__all__), EXPECTED_EXPORTS)
        for name in EXPECTED_EXPORTS:
            with self.subTest(name=name):
                self.assertIs(getattr(operations, name), getattr(module, name))
                self.assertIn(name, operations.__all__)
        self.assertEqual(
            module.DeploymentProgramCommand,
            module.PrepareDeploymentProgram | module.ProgressDeploymentProgram,
        )
        self.assertFalse(hasattr(module, "DeploymentProgramStateConflict"))
        self.assertFalse(hasattr(operations, "DeploymentProgramStateConflict"))

        values = (
            module.DeploymentProgramReference("workspace-a", "plan-a"),
            _prepare(module),
            _progress(module),
        )
        for value in values:
            with self.subTest(value=type(value).__name__):
                self.assertFalse(hasattr(value, "__dict__"))
                with self.assertRaises(FrozenInstanceError):
                    value.extra = "forbidden"

    def test_reference_enforces_exact_bounded_candidate_free_identity(self) -> None:
        _, module = _contract()
        reference = module.DeploymentProgramReference("w" * 512, "p" * 512)

        self.assertEqual(reference.workspace_id, "w" * 512)
        self.assertEqual(reference.plan_id, "p" * 512)
        self.assertEqual(
            reference.descriptor(),
            {"workspace_id": "w" * 512, "plan_id": "p" * 512},
        )
        candidates = (
            ({"workspace_id": "", "plan_id": "plan-a"}, ""),
            ({"workspace_id": "w" * 513, "plan_id": "plan-a"}, "w" * 513),
            ({"workspace_id": "workspace-a", "plan_id": "p" * 513}, "p" * 513),
            ({"workspace_id": "workspace\nsecret", "plan_id": "plan-a"}, "secret"),
            ({"workspace_id": True, "plan_id": "plan-a"}, "True"),
            (
                {"workspace_id": _HostileText("hostile-workspace"), "plan_id": "plan-a"},
                "hostile-workspace",
            ),
        )
        for arguments, canary in candidates:
            with self.subTest(arguments=arguments):
                self.assert_contract_error(
                    lambda arguments=arguments: module.DeploymentProgramReference(
                        **arguments
                    ),
                    canary,
                )

    def test_prepare_requires_exact_types_and_coupled_desired_lineage(self) -> None:
        _, module = _contract()
        absent = _prepare(module)
        present = _prepare(
            module,
            expected_desired=_lineage("desired"),
            expected_desired_graph_revision=1,
        )

        self.assertIsNone(absent.expected_desired)
        self.assertEqual(absent.expected_desired_graph_revision, 0)
        self.assertEqual(present.expected_desired, _lineage("desired"))
        self.assertEqual(present.expected_desired_graph_revision, 1)

        invalid = (
            {"context": object()},
            {"desired": object()},
            {"expected_current": object()},
            {"expected_desired": object(), "expected_desired_graph_revision": 1},
            {"expected_desired": None, "expected_desired_graph_revision": 1},
            {
                "expected_desired": _lineage("desired"),
                "expected_desired_graph_revision": 0,
            },
            {
                "expected_desired": _lineage("desired"),
                "expected_desired_graph_revision": -1,
            },
            {
                "expected_desired": _lineage("desired"),
                "expected_desired_graph_revision": True,
            },
            {"idempotency_key": object()},
            {"context": _context("w" * 513)},
        )
        for changes in invalid:
            with self.subTest(changes=changes):
                self.assert_contract_error(
                    lambda changes=changes: _prepare(module, **changes),
                    "w" * 513,
                )

    def test_prepare_bounds_optional_comment_and_redacts_repr_descriptor(self) -> None:
        _, module = _contract()
        command = _prepare(
            module,
            title="title-canary",
            approval_comment="comment-canary",
            desired=DeploymentGraph("graph-canary"),
        )

        self.assertEqual(
            command.descriptor(),
            {
                "command": "prepare-deployment-program",
                "workspace_id": "workspace-a",
                "expected_current": {
                    "authored_graph_id": "graph-current",
                    "realized_projection_id": "projection-current",
                },
                "expected_desired": None,
                "expected_desired_graph_revision": 0,
                "idempotency_key": "program/prepare/a",
                "approval_comment_present": True,
            },
        )
        rendered = f"{command!r} {command.descriptor()!r}"
        for canary in (
            "title-canary",
            "comment-canary",
            "graph-canary",
            "operator-a",
            "issuer.example",
            PolicyScope.PLAN_EXECUTE.value,
        ):
            self.assertNotIn(canary, rendered)

        for field_name in ("context", "desired", "title", "approval_comment"):
            self.assertFalse(
                next(item for item in fields(command) if item.name == field_name).repr
            )

        valid = (
            {"title": "t" * 512},
            {"approval_comment": None},
            {"approval_comment": "c" * 512},
        )
        for changes in valid:
            with self.subTest(changes=changes):
                _prepare(module, **changes)

        invalid = (
            ({"title": ""}, ""),
            ({"title": "t" * 513}, "t" * 513),
            ({"title": "title\nsecret"}, "secret"),
            ({"approval_comment": ""}, ""),
            ({"approval_comment": "c" * 513}, "c" * 513),
            ({"approval_comment": "comment\nsecret"}, "secret"),
            ({"approval_comment": True}, "True"),
        )
        for changes, canary in invalid:
            with self.subTest(changes=changes):
                self.assert_contract_error(
                    lambda changes=changes: _prepare(module, **changes),
                    canary,
                )

    def test_progress_enforces_workspace_tuple_nominality_order_and_uniqueness(self) -> None:
        _, module = _contract()
        activity_a = ExternalReadinessAttestation(
            "activity-a", "readiness/check-a"
        )
        activity_b = ExternalReadinessAttestation(
            "activity-b", "readiness/check-b"
        )
        command = _progress(module, readiness=(activity_b, activity_a))

        self.assertEqual(command.readiness, (activity_a, activity_b))
        canonical_boundary = ExternalReadinessAttestation(
            "a" * 200,
            "readiness/boundary-id",
        )
        self.assertEqual(
            _progress(module, readiness=(canonical_boundary,)).readiness,
            (canonical_boundary,),
        )
        with self.assertRaises(InvalidOperationCommand):
            ExternalReadinessAttestation("a" * 513, "readiness/large-id")

        invalid = (
            {"context": object()},
            {"reference": object()},
            {"readiness": [activity_a]},
            {"readiness": (object(),)},
            {
                "readiness": (
                    _ReadinessSubclass("activity-a", "readiness/subclass"),
                )
            },
            {"readiness": (activity_a, activity_a)},
            {"idempotency_key": object()},
            {
                "context": _context("workspace-b"),
                "reference": module.DeploymentProgramReference(
                    "workspace-a", "plan-a"
                ),
            },
        )
        for changes in invalid:
            with self.subTest(changes=changes):
                self.assert_contract_error(
                    lambda changes=changes: _progress(module, **changes),
                    "activity-a",
                    "readiness/subclass",
                    "workspace-b",
                )

    def test_progress_repr_descriptor_hide_authority_and_readiness_content(self) -> None:
        _, module = _contract()
        readiness = ExternalReadinessAttestation(
            "activity-canary", "readiness/evidence-canary"
        )
        command = _progress(module, readiness=(readiness,))

        self.assertEqual(
            command.descriptor(),
            {
                "command": "progress-deployment-program",
                "reference": {"workspace_id": "workspace-a", "plan_id": "plan-a"},
                "readiness_count": 1,
                "idempotency_key": "program/progress/a",
            },
        )
        rendered = f"{command!r} {command.descriptor()!r}"
        for canary in (
            "activity-canary",
            "evidence-canary",
            "operator-a",
            "issuer.example",
            PolicyScope.PLAN_EXECUTE.value,
        ):
            self.assertNotIn(canary, rendered)
        self.assertFalse(
            next(item for item in fields(command) if item.name == "context").repr
        )
        self.assertFalse(
            next(item for item in fields(command) if item.name == "readiness").repr
        )

    def test_source_has_exact_fields_reused_values_and_effect_free_import_dag(self) -> None:
        _, module = _contract()
        source_path = SOURCE_ROOT / "deployment_program.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))

        self.assertEqual(
            tuple(item.name for item in fields(module.DeploymentProgramReference)),
            ("workspace_id", "plan_id"),
        )
        self.assertEqual(
            tuple(item.name for item in fields(module.PrepareDeploymentProgram)),
            (
                "context",
                "desired",
                "expected_current",
                "expected_desired",
                "expected_desired_graph_revision",
                "title",
                "idempotency_key",
                "approval_comment",
            ),
        )
        self.assertEqual(
            tuple(item.name for item in fields(module.ProgressDeploymentProgram)),
            ("context", "reference", "readiness", "idempotency_key"),
        )
        self.assertIs(
            module.ExternalReadinessAttestation,
            ExternalReadinessAttestation,
        )
        self.assertIs(module.IdempotencyKey, IdempotencyKey)

        imports: dict[str, set[str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.setdefault(node.module, set()).update(
                    alias.name for alias in node.names
                )
        self.assertEqual(
            imports,
            {
                "__future__": {"annotations"},
                "dataclasses": {"dataclass", "field"},
                "typing": {"TypeAlias"},
                "control_plane_kit_core.identity": {"TrustedCommandContext"},
                "control_plane_kit_core.topology": {"DeploymentGraph"},
                "control_plane_kit_operations.admission": {
                    "ExternalReadinessAttestation"
                },
                "control_plane_kit_operations.records": {"GraphProjectionLineage"},
                "control_plane_kit_operations.workflows": {"IdempotencyKey"},
            },
        )
        forbidden_calls = {
            "open",
            "exec",
            "eval",
            "compile",
            "connect",
            "commit",
            "rollback",
            "execute",
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

        for owner_name in ("admission.py", "workflows.py"):
            owner_tree = ast.parse(
                (SOURCE_ROOT / owner_name).read_text(encoding="utf-8")
            )
            imported_modules = {
                node.module
                for node in ast.walk(owner_tree)
                if isinstance(node, ast.ImportFrom) and node.module
            } | {
                alias.name
                for node in ast.walk(owner_tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            self.assertFalse(
                any("deployment_program" in value for value in imported_modules)
            )


if __name__ == "__main__":
    unittest.main()
