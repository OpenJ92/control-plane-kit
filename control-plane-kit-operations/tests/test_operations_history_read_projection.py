from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
import unittest

import control_plane_kit_operations.read_services as read_services
from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.planning import ActivityPlan, RiskLevel
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.topology import (
    DEFAULT_GRAPH_CODEC,
    GraphDescriptorError,
)
from control_plane_kit_core.topology.graph import DeploymentGraph, RuntimeRecord
from control_plane_kit_core.types import RuntimeKind, WorkspaceLifecycle
from control_plane_kit_operations import InstanceReadService, ReadModelError
from control_plane_kit_operations.records import (
    ActivityPlanRecord,
    ActivityPlanStatus,
    ApprovalRequestRecord,
    GraphVersionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
    WorkspaceRecord,
)

from test_read_services_package import _local_module_imports


_FACADE_METHODS = {
    "activity_sessions",
    "open_sessions",
    "session_detail",
    "session_actions",
    "run_events",
    "session_plans",
    "session_approvals",
    "plan_detail",
    "pending_approvals",
    "plan_runs",
    "approval_detail",
}

_MOVED_HELPERS = {
    "_activity_history",
    "_execution",
    "_recovery_for_plan",
    "_session_summary_descriptor",
    "_action_descriptor",
    "_approval_descriptor",
    "_plan_summary_descriptor",
    "_run_summary_descriptor",
    "_event_descriptor",
    "_failure_descriptor",
    "_session_in_workspace",
    "_plan_in_workspace",
    "_approval_in_workspace",
    "_risk_summary",
    "_risk_rank",
}


class _WorkspaceStore:
    def get(self, workspace_id: str) -> WorkspaceRecord:
        if workspace_id != "workspace-a":
            raise KeyError(workspace_id)
        return WorkspaceRecord(
            workspace_id="workspace-a",
            name="Workspace A",
            lifecycle=WorkspaceLifecycle.RUNNING,
        )


class _GraphStore:
    def __init__(
        self,
        records: dict[str, GraphVersionRecord],
        *,
        failure: BaseException | None = None,
    ) -> None:
        self.records = records
        self.failure = failure

    def get(self, graph_id: str) -> GraphVersionRecord:
        if self.failure is not None:
            raise self.failure
        try:
            return self.records[graph_id]
        except KeyError:
            raise KeyError(f"missing hostile graph {graph_id}") from None


class _ActivityStore:
    def __init__(self) -> None:
        self.session = OperationSessionRecord(
            session_id="session-a",
            workspace_id="workspace-a",
            actor_id="operator-a",
            title="Deploy",
            status=OperationSessionStatus.OPEN,
            created_at="2026-08-13T12:00:00Z",
        )
        self.plan = ActivityPlanRecord(
            plan_id="plan-hostile",
            session_id="session-a",
            base_graph_id="graph-hostile-base",
            desired_graph_id="graph-hostile-desired",
            status=ActivityPlanStatus.PLANNED,
            created_at="2026-08-13T12:01:00Z",
            plan=ActivityPlan(()),
        )
        self.approval = ApprovalRequestRecord(
            request_id="approval-hostile",
            session_id="session-a",
            subject=ActivityPlanApprovalSubject(self.plan.plan_id),
            requested_by="operator-a",
            requested_at="2026-08-13T12:02:00Z",
            required_scope=PolicyScope.PLAN_APPROVE,
            max_risk=RiskLevel.INFORMATIONAL,
            destructive=False,
        )

    def get_session(self, session_id: str) -> OperationSessionRecord:
        if session_id != self.session.session_id:
            raise KeyError(session_id)
        return self.session

    def get_plan(self, plan_id: str) -> ActivityPlanRecord:
        if plan_id != self.plan.plan_id:
            raise KeyError(plan_id)
        return self.plan

    def get_approval_request(self, request_id: str) -> ApprovalRequestRecord:
        if request_id != self.approval.request_id:
            raise KeyError(request_id)
        return self.approval

    def approval_decision_for_request(self, _request_id: str):
        return None


class _RejectedCodec:
    def __init__(self, candidate: str) -> None:
        self.candidate = candidate

    def decode(self, _descriptor):
        raise GraphDescriptorError(f"rejected {self.candidate}")

    def encode(self, _graph):
        raise AssertionError("rejected graph must not be encoded")


class _ValidationFailureGraph:
    def __init__(self, candidate: str) -> None:
        self.candidate = candidate

    @property
    def runtimes(self):
        raise TypeError(f"invalid graph during validation {self.candidate}")


class _ValidationFailureCodec:
    def __init__(self, candidate: str) -> None:
        self.graph = _ValidationFailureGraph(candidate)

    def decode(self, _descriptor):
        return self.graph

    def encode(self, _graph):
        raise AssertionError("invalid graph must not be encoded")


class _InvalidGraphCodec:
    def __init__(self) -> None:
        self.graph = DeploymentGraph(
            "transition-hostile",
            runtimes={
                "runtime-a": RuntimeRecord(
                    "runtime-a",
                    RuntimeKind.DOCKER,
                    children=("missing-hostile-node",),
                )
            },
        )

    def decode(self, _descriptor):
        return self.graph

    def encode(self, _graph):
        return {}


class _DriverFailure(RuntimeError):
    pass


def _graph_record(graph_id: str, *, workspace_id: str = "workspace-a") -> GraphVersionRecord:
    return GraphVersionRecord.from_graph(
        graph_id=graph_id,
        workspace_id=workspace_id,
        version=1,
        graph=DeploymentGraph(graph_id),
        created_by="operator-a",
        created_at="2026-08-13T12:00:00Z",
    )


class OperationsHistoryReadProjectionStructureTests(unittest.TestCase):
    def _trees(self) -> tuple[ast.Module, ast.Module]:
        paths = tuple(getattr(read_services, "__path__", ()))
        self.assertEqual(len(paths), 1)
        package_path = Path(paths[0])
        owner_path = package_path / "operations_history.py"
        self.assertTrue(owner_path.is_file(), "operations-history owner is absent")
        instance_path = package_path / "instance.py"
        return (
            ast.parse(owner_path.read_text(encoding="utf-8")),
            ast.parse(instance_path.read_text(encoding="utf-8")),
        )

    @staticmethod
    def _definitions(tree: ast.Module) -> tuple[set[str], set[str]]:
        functions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        projection = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.ClassDef)
                and node.name == "_OperationsHistoryReadProjection"
            ),
            None,
        )
        methods = set()
        if projection is not None:
            methods = {
                node.name
                for node in projection.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
        return functions, methods

    def test_owner_has_exact_method_and_helper_family(self) -> None:
        owner, instance = self._trees()
        owner_functions, owner_methods = self._definitions(owner)
        owner_definitions = {
            node.name
            for node in ast.walk(owner)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        instance_definitions = {
            node.name
            for node in ast.walk(instance)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        class_helpers = {"_activity_history", "_execution", "_recovery_for_plan"}
        self.assertEqual(
            owner_methods,
            {"__init__"} | _FACADE_METHODS | class_helpers,
        )
        self.assertEqual(owner_functions, _MOVED_HELPERS - class_helpers)
        self.assertEqual(
            owner_definitions,
            {"__init__"} | _FACADE_METHODS | _MOVED_HELPERS,
        )
        self.assertTrue(_MOVED_HELPERS.isdisjoint(instance_definitions))
        self.assertNotIn("_mapping", instance_definitions)
        self.assertNotIn("_mapping", owner_definitions)

    def test_facade_is_exact_one_step_delegate(self) -> None:
        _owner, instance = self._trees()
        service = next(
            node
            for node in instance.body
            if isinstance(node, ast.ClassDef) and node.name == "InstanceReadService"
        )
        methods = {
            node.name: node
            for node in service.body
            if isinstance(node, ast.FunctionDef)
        }
        for name in _FACADE_METHODS:
            with self.subTest(name=name):
                method = methods[name]
                self.assertEqual(len(method.body), 1)
                returned = method.body[0]
                self.assertIsInstance(returned, ast.Return)
                self.assertIsInstance(returned.value, ast.Call)
                function = returned.value.func
                self.assertIsInstance(function, ast.Attribute)
                self.assertEqual(function.attr, name)
                self.assertIsInstance(function.value, ast.Attribute)
                self.assertEqual(function.value.attr, "_operations_history")

    def test_facade_composes_without_retaining_moved_dependencies(self) -> None:
        owner, instance = self._trees()
        service = next(
            node
            for node in instance.body
            if isinstance(node, ast.ClassDef) and node.name == "InstanceReadService"
        )
        initializer = next(
            node
            for node in service.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        assigned: set[str] = set()
        for node in ast.walk(initializer):
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = (node.target,)
            else:
                continue
            assigned.update(
                target.attr
                for target in targets
                if isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            )
        self.assertIn("_operations_history", assigned)
        self.assertTrue(
            {
                "_activity_history_store",
                "_execution_store",
                "_graph_topology_store",
                "_graph_codec",
            }.isdisjoint(assigned)
        )

        self.assertEqual(
            _local_module_imports(owner, {"instance", "workspace_graph"}),
            set(),
        )

    def test_forbidden_owner_edges_cover_every_supported_import_form(self) -> None:
        sources = (
            "from .workspace_graph import WorkspaceSummary",
            "from . import workspace_graph",
            "from control_plane_kit_operations.read_services.workspace_graph "
            "import WorkspaceSummary",
            "from control_plane_kit_operations.read_services import workspace_graph",
            "import control_plane_kit_operations.read_services.workspace_graph",
        )
        for source in sources:
            with self.subTest(source=source):
                self.assertEqual(
                    _local_module_imports(ast.parse(source), {"workspace_graph"}),
                    {"workspace_graph"},
                )


class OperationsHistoryRecoveryFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.activity = _ActivityStore()
        self.base = _graph_record("graph-hostile-base")
        self.desired = _graph_record("graph-hostile-desired")

    def service(
        self,
        records: dict[str, GraphVersionRecord],
        *,
        codec=DEFAULT_GRAPH_CODEC,
        failure: BaseException | None = None,
    ) -> InstanceReadService:
        return InstanceReadService(
            workspace_store=_WorkspaceStore(),
            graph_topology_store=_GraphStore(records, failure=failure),
            activity_history_store=self.activity,
            graph_codec=codec,
        )

    def _assert_matrix(self, invoke) -> None:
        cases = (
            (
                "missing-base",
                "plan recovery graph truth is unavailable",
                {self.desired.graph_id: self.desired},
                DEFAULT_GRAPH_CODEC,
                "graph-hostile-base",
            ),
            (
                "missing-desired",
                "plan recovery graph truth is unavailable",
                {self.base.graph_id: self.base},
                DEFAULT_GRAPH_CODEC,
                "graph-hostile-desired",
            ),
            (
                "foreign-workspace",
                "plan recovery graph truth is unavailable",
                {
                    self.base.graph_id: replace(self.base, workspace_id="workspace-other"),
                    self.desired.graph_id: self.desired,
                },
                DEFAULT_GRAPH_CODEC,
                "workspace-other",
            ),
            (
                "decode",
                "plan recovery graph truth is invalid",
                {self.base.graph_id: self.base, self.desired.graph_id: self.desired},
                _RejectedCodec("decoder-hostile-candidate"),
                "decoder-hostile-candidate",
            ),
            (
                "validation",
                "plan recovery graph truth is invalid",
                {self.base.graph_id: self.base, self.desired.graph_id: self.desired},
                _ValidationFailureCodec("validation-hostile-candidate"),
                "validation-hostile-candidate",
            ),
            (
                "transition",
                "plan recovery graph truth is invalid",
                {self.base.graph_id: self.base, self.desired.graph_id: self.desired},
                _InvalidGraphCodec(),
                "missing-hostile-node",
            ),
        )
        for name, expected, records, codec, forbidden in cases:
            with self.subTest(name=name):
                try:
                    invoke(self.service(records, codec=codec))
                except BaseException as error:
                    caught = error
                else:
                    self.fail("recovery truth failure was not raised")
                self.assertIs(type(caught), ReadModelError)
                self.assertEqual(str(caught), expected)
                self.assertLessEqual(len(repr(caught)), 96)
                for candidate in (
                    forbidden,
                    "plan-hostile",
                    "graph-hostile-base",
                    "graph-hostile-desired",
                ):
                    self.assertNotIn(candidate, str(caught))
                    self.assertNotIn(candidate, repr(caught))
                self.assertIsNone(caught.__cause__)
                self.assertIsNone(caught.__context__)

    def test_plan_detail_recovery_failures_are_categorical(self) -> None:
        self._assert_matrix(
            lambda service: service.plan_detail("workspace-a", "plan-hostile")
        )

    def test_approval_detail_recovery_failures_are_categorical(self) -> None:
        self._assert_matrix(
            lambda service: service.approval_detail(
                "workspace-a", "approval-hostile"
            )
        )

    def test_unexpected_graph_store_failure_remains_raw(self) -> None:
        failure = _DriverFailure("driver operational failure")
        service = self.service({}, failure=failure)
        with self.assertRaises(_DriverFailure) as caught:
            service.plan_detail("workspace-a", "plan-hostile")
        self.assertIs(caught.exception, failure)


if __name__ == "__main__":
    unittest.main()
