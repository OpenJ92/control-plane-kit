from __future__ import annotations

import ast
from dataclasses import replace
import importlib
from pathlib import Path
from types import SimpleNamespace
import unittest

from control_plane_kit_core.identity import (
    AuthenticatedPrincipal,
    PrincipalIdentity,
    PrincipalKind,
    WorkspaceGrant,
)
from control_plane_kit_core.planning import compile_activity_plan, planning_scenarios
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.topology import DeploymentGraph, validate_graph
from control_plane_kit_operations.approvals import (
    ApprovalAuthorizationDenied,
    ApprovalStateConflict,
    RequestApproval,
)
from control_plane_kit_operations.deployment_program import (
    PrepareDeploymentProgram,
)
from control_plane_kit_operations.deployment_program_projections import (
    DeploymentApprovalRequired,
    DeploymentNoChanges,
    DeploymentReviewBlocked,
)
from control_plane_kit_operations.deployment_transitions import Deploy
from control_plane_kit_operations.planning import (
    ActivityPlanningGraphStateConflict,
    DesiredGraphCommandError,
    RequestActivityPlan,
    SetDesiredGraph,
)
from control_plane_kit_operations.records import GraphProjectionLineage
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    OperationIdempotencyConflict,
    StartOperationSession,
)


EXPECTED_INTENT_DIGEST = (
    "cfc940690ee179c918e384e9c933d69089b1c7c685a8fa499c46b47f0d63d54d"
)
EXPECTED_CHILD_KEYS = {
    "session": (
        "deployment-prepare.v1:session:"
        "39509cdcaca2f8bb491269fa81d10e968b0df605825dd4cf3529bee9c63a6024"
    ),
    "desired": (
        "deployment-prepare.v1:desired:"
        "3e13b389a61440a65bc1f4df528a821a0c0321de0c09b33bfcda2fd8efdcd3b4"
    ),
    "plan": (
        "deployment-prepare.v1:plan:"
        "7bbda244413d9aee7084b4bbae1a4c0c86e751a99b5900f4b717c39d9499e4b4"
    ),
    "approval": (
        "deployment-prepare.v1:approval:"
        "123199ba40a22600bb732be393bdc6dacf3321e294f489ace462f32248e95e5b"
    ),
}


class SentinelFailure(RuntimeError):
    pass


class RecordingService:
    def __init__(self, name: str, trace: list[tuple[str, object]], result) -> None:
        self.name = name
        self.trace = trace
        self.result = result

    def execute(self, command):
        self.trace.append((self.name, command))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class DeploymentProgramInterpreterTests(unittest.TestCase):
    def module(self):
        try:
            return importlib.import_module(
                "control_plane_kit_operations.deployment_program_interpreter"
            )
        except ModuleNotFoundError as error:
            self.fail(f"deployment program interpreter is missing: {error}")

    def context(
        self,
        *scopes: PolicyScope,
        workspace_id: str = "workspace-a",
        actor_id: str = "operator-a",
    ):
        principal = AuthenticatedPrincipal(
            PrincipalIdentity("issuer-a", actor_id, PrincipalKind.OPERATOR),
            (WorkspaceGrant(workspace_id, tuple(scopes)),),
        )
        return principal.command_context(workspace_id)

    def command(
        self,
        *,
        desired: DeploymentGraph | None = None,
        scopes: tuple[PolicyScope, ...] = (
            PolicyScope.INSTANCE_WORKSPACE_EDIT,
            PolicyScope.PLAN_REQUEST,
        ),
        workspace_id: str = "workspace-a",
        actor_id: str = "operator-a",
        parent_key: str = "parent-key",
    ) -> PrepareDeploymentProgram:
        return PrepareDeploymentProgram(
            context=self.context(
                *scopes,
                workspace_id=workspace_id,
                actor_id=actor_id,
            ),
            desired=desired or DeploymentGraph("desired"),
            expected_current=GraphProjectionLineage(
                "graph-current",
                "projection-current",
            ),
            expected_desired=None,
            expected_desired_graph_revision=0,
            title="Deploy desired",
            idempotency_key=IdempotencyKey(parent_key),
            approval_comment="review me",
        )

    def scenario(self, scenario_id: str):
        return next(
            value
            for value in planning_scenarios()
            if value.scenario_id == scenario_id
        )

    def plan_result(self, scenario_id: str):
        scenario = self.scenario(scenario_id)
        current = validate_graph(scenario.current_graph)
        desired = validate_graph(scenario.desired_graph)
        current.require_valid()
        desired.require_valid()
        transition = Deploy(current, desired)
        plan = compile_activity_plan(transition.diff)
        return SimpleNamespace(
            plan_record=SimpleNamespace(plan_id="plan-a", plan=plan),
            transition=transition,
        )

    def services(self, planning_result=None, *, failure=None):
        trace: list[tuple[str, object]] = []
        results = {
            "operations": SimpleNamespace(
                session=SimpleNamespace(session_id="session-a")
            ),
            "desired": SimpleNamespace(
                graph_version_id="graph-desired",
                desired_realized_projection_id="projection-desired",
                desired_graph_revision=1,
            ),
            "planning": planning_result or self.plan_result("fresh-deployment"),
            "approval": SimpleNamespace(
                request=SimpleNamespace(request_id="approval-a")
            ),
        }
        if failure is not None:
            stage, error = failure
            results[stage] = error
        values = tuple(
            RecordingService(name, trace, results[name])
            for name in ("operations", "desired", "planning", "approval")
        )
        return values, trace

    def program(self, planning_result=None, *, failure=None):
        services, trace = self.services(planning_result, failure=failure)
        return self.module().DeploymentProgram(*services), trace

    def test_preflight_requires_both_scopes_before_validation_or_services(self) -> None:
        invalid = self.scenario("insert-rate-limiter").desired_graph
        invalid = replace(invalid, edges={})
        cases = (
            (PolicyScope.INSTANCE_WORKSPACE_EDIT,),
            (PolicyScope.PLAN_REQUEST,),
            (),
        )
        for scopes in cases:
            with self.subTest(scopes=scopes):
                program, trace = self.program()
                module = self.module()
                with self.assertRaises(
                    module.DeploymentProgramAuthorizationDenied
                ) as captured:
                    program.prepare(self.command(desired=invalid, scopes=scopes))
                self.assertEqual(
                    str(captured.exception),
                    "deployment preparation is not authorized",
                )
                self.assertEqual(trace, [])
                self._assert_clean_error(captured.exception, invalid.name)

    def test_intrinsic_invalid_graph_stops_before_any_service(self) -> None:
        invalid = self.scenario("insert-rate-limiter").desired_graph
        invalid = replace(invalid, edges={})
        self.assertFalse(validate_graph(invalid).valid)
        program, trace = self.program()
        module = self.module()

        with self.assertRaises(module.DeploymentProgramStateConflict) as captured:
            program.prepare(self.command(desired=invalid))

        self.assertEqual(
            str(captured.exception),
            "deployment preparation state is unavailable",
        )
        self.assertEqual(trace, [])
        self._assert_clean_error(captured.exception, invalid.name)

    def test_exact_child_commands_keys_digest_and_object_identity(self) -> None:
        command = self.command()
        program, trace = self.program(self.plan_result("fresh-deployment"))

        result = program.prepare(command)

        self.assertIsInstance(result, DeploymentApprovalRequired)
        self.assertEqual(result.reference.workspace_id, "workspace-a")
        self.assertEqual(result.reference.plan_id, "plan-a")
        self.assertEqual(result.approval_request_id, "approval-a")
        self.assertEqual([name for name, _ in trace], [
            "operations",
            "desired",
            "planning",
            "approval",
        ])
        session, desired, planning, approval = (value for _, value in trace)
        self.assertIsInstance(session, StartOperationSession)
        self.assertEqual(session.workspace_id, command.context.workspace_id)
        self.assertEqual(session.actor_id, command.context.actor_id)
        self.assertEqual(session.title, command.title)
        self.assertEqual(session.idempotency_key.value, EXPECTED_CHILD_KEYS["session"])
        self.assertEqual(
            session.metadata,
            {"deployment_prepare_intent_sha256": EXPECTED_INTENT_DIGEST},
        )

        self.assertIsInstance(desired, SetDesiredGraph)
        self.assertEqual(desired.session_id, "session-a")
        self.assertIs(desired.graph, command.desired)
        self.assertIsNone(desired.expected_desired_graph_id)
        self.assertIsNone(desired.expected_desired_realized_projection_id)
        self.assertEqual(desired.expected_desired_graph_revision, 0)
        self.assertEqual(desired.idempotency_key.value, EXPECTED_CHILD_KEYS["desired"])

        self.assertIsInstance(planning, RequestActivityPlan)
        self.assertEqual(planning.session_id, "session-a")
        self.assertEqual(planning.expected_current_graph_id, "graph-current")
        self.assertEqual(
            planning.expected_current_realized_projection_id,
            "projection-current",
        )
        self.assertEqual(planning.expected_desired_graph_id, "graph-desired")
        self.assertEqual(
            planning.expected_desired_realized_projection_id,
            "projection-desired",
        )
        self.assertEqual(planning.expected_desired_graph_revision, 1)
        self.assertEqual(planning.idempotency_key.value, EXPECTED_CHILD_KEYS["plan"])

        self.assertIsInstance(approval, RequestApproval)
        self.assertEqual(approval.session_id, "session-a")
        self.assertEqual(approval.plan_id, "plan-a")
        self.assertEqual(approval.actor_scopes, command.context.granted_scopes)
        self.assertEqual(approval.comment, command.approval_comment)
        self.assertEqual(
            approval.idempotency_key.value,
            EXPECTED_CHILD_KEYS["approval"],
        )
        rendered = f"{result!r} {result.descriptor()!r}"
        for forbidden in (
            command.title,
            command.approval_comment,
            command.desired.name,
            EXPECTED_INTENT_DIGEST,
            command.idempotency_key.value,
        ):
            self.assertNotIn(forbidden, rendered)

    def test_exact_terminal_projection_and_call_trace_matrix(self) -> None:
        cases = (
            ("no-change", DeploymentNoChanges, 3),
            ("unsupported-implementation-transition", DeploymentReviewBlocked, 3),
            ("fresh-deployment", DeploymentApprovalRequired, 4),
        )
        for scenario_id, projection_type, call_count in cases:
            with self.subTest(scenario_id=scenario_id):
                program, trace = self.program(self.plan_result(scenario_id))
                result = program.prepare(self.command())
                self.assertIsInstance(result, projection_type)
                self.assertEqual(len(trace), call_count)
                self.assertEqual(
                    [name for name, _ in trace[:3]],
                    ["operations", "desired", "planning"],
                )
                self.assertEqual(
                    [name for name, _ in trace[3:]],
                    ["approval"] if call_count == 4 else [],
                )

    def test_zero_activity_update_is_not_misclassified_as_no_change(self) -> None:
        current = validate_graph(DeploymentGraph("before"))
        desired = validate_graph(DeploymentGraph("after"))
        current.require_valid()
        desired.require_valid()
        transition = Deploy(current, desired)
        plan = compile_activity_plan(transition.diff)
        self.assertEqual(plan.activities, ())
        planning_result = SimpleNamespace(
            plan_record=SimpleNamespace(plan_id="plan-a", plan=plan),
            transition=transition,
        )
        program, trace = self.program(planning_result)

        result = program.prepare(self.command())

        self.assertIsInstance(result, DeploymentApprovalRequired)
        self.assertEqual([name for name, _ in trace][-1], "approval")

    def test_expected_child_failures_are_bounded_and_preserve_authorization(self) -> None:
        module = self.module()
        cases = (
            (
                "operations",
                OperationIdempotencyConflict("SESSION-CANARY"),
                module.DeploymentProgramStateConflict,
                "deployment preparation state is unavailable",
            ),
            (
                "desired",
                DesiredGraphCommandError("DESIRED-CANARY"),
                module.DeploymentProgramStateConflict,
                "deployment preparation state is unavailable",
            ),
            (
                "planning",
                ActivityPlanningGraphStateConflict("PLAN-CANARY"),
                module.DeploymentProgramStateConflict,
                "deployment preparation state is unavailable",
            ),
            (
                "approval",
                ApprovalStateConflict("APPROVAL-STATE-CANARY"),
                module.DeploymentProgramStateConflict,
                "deployment preparation state is unavailable",
            ),
            (
                "approval",
                ApprovalAuthorizationDenied("APPROVAL-AUTH-CANARY"),
                module.DeploymentProgramAuthorizationDenied,
                "deployment preparation is not authorized",
            ),
        )
        for stage, failure, expected_type, expected_message in cases:
            with self.subTest(stage=stage, failure=type(failure).__name__):
                program, trace = self.program(failure=(stage, failure))
                with self.assertRaises(expected_type) as captured:
                    program.prepare(self.command())
                self.assertEqual(str(captured.exception), expected_message)
                self.assertEqual([name for name, _ in trace][-1], stage)
                self._assert_clean_error(captured.exception, str(failure))

    def test_unexpected_child_failure_escapes_by_identity(self) -> None:
        sentinel = SentinelFailure("unexpected-programming-failure")
        program, _ = self.program(failure=("desired", sentinel))

        with self.assertRaises(SentinelFailure) as captured:
            program.prepare(self.command())

        self.assertIs(captured.exception, sentinel)

    def test_child_keys_are_bounded_and_workspace_namespaced(self) -> None:
        first, first_trace = self.program()
        second, second_trace = self.program()
        first.prepare(self.command(parent_key="x" * 200))
        second.prepare(
            self.command(
                workspace_id="workspace-b",
                parent_key="x" * 200,
            )
        )
        first_keys = tuple(
            command.idempotency_key.value for _, command in first_trace
        )
        second_keys = tuple(
            command.idempotency_key.value for _, command in second_trace
        )
        self.assertTrue(all(len(value) <= 200 for value in first_keys + second_keys))
        self.assertEqual(len(set(first_keys)), 4)
        self.assertEqual(len(set(second_keys)), 4)
        self.assertTrue(set(first_keys).isdisjoint(second_keys))
        self.assertTrue(all("x" * 16 not in value for value in first_keys))

    def test_root_exports_and_source_boundary_are_exact(self) -> None:
        import control_plane_kit_operations

        expected = {
            "DeploymentProgram",
            "DeploymentProgramAuthorizationDenied",
            "DeploymentProgramError",
            "DeploymentProgramStateConflict",
        }
        self.assertTrue(expected.issubset(control_plane_kit_operations.__all__))
        for name in expected:
            self.assertIs(
                getattr(control_plane_kit_operations, name),
                getattr(self.module(), name),
            )

        source_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "control_plane_kit_operations"
            / "deployment_program_interpreter.py"
        )
        self.assertTrue(source_path.is_file())
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        forbidden_roots = {
            "control_plane_kit_interpreters",
            "control_plane_kit_servers",
            "control_plane_kit_secrets",
            "psycopg",
            "docker",
            "fastapi",
            "mcp",
        }
        self.assertFalse(
            {
                module_name.split(".", 1)[0]
                for module_name in imports
            }
            & forbidden_roots
        )
        source_names = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        }
        self.assertTrue(
            {"OperationCommandService", "DesiredGraphCommandService",
             "ActivityPlanningCommandService", "ApprovalCommandService"}
            .issubset(source_names)
        )
        self.assertFalse(
            source_names
            & {"PostgresUnitOfWork", "UnitOfWork", "stores", "connection", "cursor"}
        )

    def _assert_clean_error(self, error: BaseException, *canaries: str) -> None:
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        for canary in canaries:
            self.assertNotIn(canary, str(error))


if __name__ == "__main__":
    unittest.main()
