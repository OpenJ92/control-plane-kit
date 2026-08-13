from __future__ import annotations

import ast
import inspect
from pathlib import Path
import unittest

from control_plane_kit_core.topology import GraphDescriptorError
from control_plane_kit_core.topology.graph import DeploymentGraph, RuntimeRecord
from control_plane_kit_core.types import RuntimeKind, WorkspaceLifecycle
from control_plane_kit_operations import (
    GraphVersionRecord,
    InstanceReadService,
    OperationSessionRecord,
    OperationSessionStatus,
    ReadModelError,
    WorkspaceRecord,
)


class _WorkspaceStore:
    def __init__(self, workspace: WorkspaceRecord) -> None:
        self.workspace = workspace
        self.calls: list[str] = []

    def get(self, workspace_id: str) -> WorkspaceRecord:
        self.calls.append(workspace_id)
        if workspace_id != self.workspace.workspace_id:
            raise KeyError(workspace_id)
        return self.workspace


class _GraphStore:
    def __init__(self, record: GraphVersionRecord | None) -> None:
        self.record = record

    def get(self, graph_id: str) -> GraphVersionRecord:
        if self.record is None or graph_id != self.record.graph_id:
            raise KeyError(graph_id)
        return self.record


class _ActivityStore:
    def __init__(self, session: OperationSessionRecord) -> None:
        self.session = session

    def get_session(self, session_id: str) -> OperationSessionRecord:
        if session_id != self.session.session_id:
            raise KeyError(session_id)
        return self.session


class _RejectedCodec:
    def __init__(self, rejected: str) -> None:
        self.rejected = rejected

    def decode(self, _descriptor):
        raise GraphDescriptorError(f"decoder rejected {self.rejected}")

    def encode(self, _graph):
        raise AssertionError("rejected graph must not be encoded")


class _InvalidGraphCodec:
    def __init__(self) -> None:
        self.graph = DeploymentGraph(
            "invalid-graph",
            runtimes={
                "runtime-a": RuntimeRecord(
                    "runtime-a",
                    RuntimeKind.DOCKER,
                    children=("missing-node",),
                )
            },
        )

    def decode(self, _descriptor):
        return self.graph

    def encode(self, _graph):
        return {}


def _workspace() -> WorkspaceRecord:
    return WorkspaceRecord(
        workspace_id="workspace-a",
        name="Workspace A",
        lifecycle=WorkspaceLifecycle.RUNNING,
        current_graph_id="graph-current",
        desired_graph_id="graph-current",
        current_realized_projection_id="projection-current",
        desired_realized_projection_id="projection-current",
    )


def _graph_record() -> GraphVersionRecord:
    return GraphVersionRecord(
        graph_id="graph-current",
        workspace_id="workspace-a",
        version=1,
        graph_descriptor={"name": "hostile-graph"},
        created_by="operator-a",
        created_at="2026-08-13T12:00:00Z",
    )


class WorkspaceGraphReadProjectionTests(unittest.TestCase):
    def service(
        self,
        *,
        graph_record: GraphVersionRecord | None = None,
        graph_codec=None,
        activity_history_store=None,
    ) -> tuple[InstanceReadService, _WorkspaceStore]:
        workspace_store = _WorkspaceStore(_workspace())
        service = InstanceReadService(
            workspace_store=workspace_store,
            graph_topology_store=_GraphStore(graph_record),
            graph_codec=graph_codec or _RejectedCodec("default-candidate"),
            activity_history_store=activity_history_store,
        )
        return service, workspace_store

    def test_public_facade_signatures_and_delegation_are_exact(self) -> None:
        expected = {
            "workspace": ("self", "workspace_id"),
            "current_graph": ("self", "workspace_id"),
            "desired_graph": ("self", "workspace_id"),
            "operator_graph": ("self", "workspace_id", "pointer"),
            "control_surface": ("self", "workspace_id", "pointer"),
        }
        for name, parameters in expected.items():
            with self.subTest(name=name):
                signature = inspect.signature(getattr(InstanceReadService, name))
                self.assertEqual(tuple(signature.parameters), parameters)
                if "pointer" in parameters:
                    pointer = signature.parameters["pointer"]
                    self.assertEqual(pointer.kind, inspect.Parameter.KEYWORD_ONLY)
                    self.assertEqual(pointer.default, "current")

        module_path = Path(inspect.getsourcefile(InstanceReadService) or "")
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        service_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "InstanceReadService"
        )
        methods = {
            node.name: node
            for node in service_class.body
            if isinstance(node, ast.FunctionDef)
        }
        for name in expected:
            with self.subTest(delegation=name):
                method = methods[name]
                self.assertEqual(len(method.body), 1)
                returned = method.body[0]
                self.assertIsInstance(returned, ast.Return)
                self.assertIsInstance(returned.value, ast.Call)
                function = returned.value.func
                self.assertIsInstance(function, ast.Attribute)
                self.assertEqual(function.attr, name)
                self.assertIsInstance(function.value, ast.Attribute)
                self.assertEqual(function.value.attr, "_workspace_graph")

        private = methods["_workspace"]
        self.assertEqual(len(private.body), 1)
        returned = private.body[0]
        self.assertIsInstance(returned, ast.Return)
        self.assertIsInstance(returned.value, ast.Call)
        self.assertEqual(returned.value.func.attr, "require_workspace")
        self.assertEqual(returned.value.func.value.attr, "_workspace_graph")

    def test_non_graph_facade_uses_the_shared_workspace_lookup(self) -> None:
        session = OperationSessionRecord(
            session_id="session-a",
            workspace_id="workspace-a",
            actor_id="operator-a",
            title="Session A",
            status=OperationSessionStatus.OPEN,
            created_at="2026-08-13T12:00:00Z",
        )
        service, workspace_store = self.service(
            activity_history_store=_ActivityStore(session)
        )
        detail = service.session_detail("workspace-a", "session-a")
        self.assertEqual(detail.kind, "session-detail")
        self.assertEqual(workspace_store.calls, ["workspace-a"])

    def test_pointer_and_graph_failures_are_categorical_and_candidate_free(self) -> None:
        cases = (
            (
                "unknown-pointer-operator",
                "unknown graph pointer",
                "hostile-pointer-candidate",
                lambda service: service.operator_graph(
                    "workspace-a", pointer="hostile-pointer-candidate"
                ),
                None,
                None,
            ),
            (
                "unknown-pointer-surface",
                "unknown graph pointer",
                "hostile-pointer-candidate",
                lambda service: service.control_surface(
                    "workspace-a", pointer="hostile-pointer-candidate"
                ),
                None,
                None,
            ),
            (
                "missing-operator",
                "missing graph truth",
                "graph-current",
                lambda service: service.operator_graph("workspace-a"),
                None,
                None,
            ),
            (
                "missing-surface",
                "missing graph truth",
                "graph-current",
                lambda service: service.control_surface("workspace-a"),
                None,
                None,
            ),
            (
                "decode-operator",
                "invalid stored graph descriptor",
                "hostile-decoder-candidate",
                lambda service: service.operator_graph("workspace-a"),
                _graph_record(),
                _RejectedCodec("hostile-decoder-candidate"),
            ),
            (
                "decode-surface",
                "invalid stored graph descriptor",
                "hostile-decoder-candidate",
                lambda service: service.control_surface("workspace-a"),
                _graph_record(),
                _RejectedCodec("hostile-decoder-candidate"),
            ),
            (
                "validation-operator",
                "invalid stored graph descriptor",
                "missing-node",
                lambda service: service.operator_graph("workspace-a"),
                _graph_record(),
                _InvalidGraphCodec(),
            ),
            (
                "validation-surface",
                "invalid stored graph descriptor",
                "missing-node",
                lambda service: service.control_surface("workspace-a"),
                _graph_record(),
                _InvalidGraphCodec(),
            ),
        )
        for name, message, forbidden, invoke, record, codec in cases:
            with self.subTest(name=name):
                service, _ = self.service(graph_record=record, graph_codec=codec)
                try:
                    invoke(service)
                except BaseException as error:
                    caught = error
                else:
                    self.fail("categorical graph failure was not raised")
                self.assertIs(type(caught), ReadModelError)
                self.assertEqual(str(caught), message)
                self.assertNotIn(forbidden, str(caught))
                self.assertNotIn(forbidden, repr(caught))
                self.assertLessEqual(len(repr(caught)), 96)
                self.assertIsNone(caught.__cause__)
                self.assertIsNone(caught.__context__)


if __name__ == "__main__":
    unittest.main()
