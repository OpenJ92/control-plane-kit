import unittest

from control_plane_kit.core.topology.graph import DeploymentGraph
from control_plane_kit.stores import (
    GraphVersionRecord,
    OperationActionKind,
    OperationActionRecord,
)
from control_plane_kit.workflows import (
    DesiredGraphEditResult,
    IdempotencyKey,
    InvalidOperationCommand,
    SetDesiredGraph,
)


class DesiredGraphCommandTests(unittest.TestCase):
    def test_set_desired_graph_is_typed_and_operator_descriptor_is_bounded(self):
        graph = DeploymentGraph(name="desired")
        command = SetDesiredGraph(
            session_id="session-a",
            workspace_id="workspace-a",
            actor_id="jacob",
            graph=graph,
            expected_desired_graph_id="graph-a",
            idempotency_key=IdempotencyKey("request-a"),
        )

        self.assertIs(command.graph, graph)
        self.assertEqual(
            command.descriptor(),
            {
                "command": "set_desired_graph",
                "session_id": "session-a",
                "workspace_id": "workspace-a",
                "actor_id": "jacob",
                "expected_desired_graph_id": "graph-a",
                "idempotency_key": "request-a",
                "graph": {
                    "name": "desired",
                    "runtime_ids": [],
                    "node_ids": [],
                    "edge_ids": [],
                },
            },
        )

    def test_invalid_or_untyped_edit_data_fails_closed(self):
        invalid = (
            lambda: SetDesiredGraph(
                "", "workspace-a", "jacob", DeploymentGraph("desired"), None, IdempotencyKey("a")
            ),
            lambda: SetDesiredGraph(
                "session-a", "workspace-a", "jacob", object(), None, IdempotencyKey("a")  # type: ignore[arg-type]
            ),
            lambda: SetDesiredGraph(
                "session-a", "workspace-a", "jacob", DeploymentGraph("desired"), " ", IdempotencyKey("a")
            ),
            lambda: SetDesiredGraph(
                "session-a", "workspace-a", "jacob", DeploymentGraph(" "), None, IdempotencyKey("a")
            ),
        )

        for construct in invalid:
            with self.subTest(construct=construct), self.assertRaises(InvalidOperationCommand):
                construct()

    def test_result_ties_graph_and_action_evidence_to_one_workspace(self):
        graph_record = GraphVersionRecord(
            graph_id="graph-b",
            workspace_id="workspace-a",
            version=2,
            graph_descriptor=DeploymentGraph("desired").descriptor(),
            created_by="jacob",
            created_at="2026-07-15T00:00:00Z",
        )
        action = OperationActionRecord(
            action_id="action-a",
            session_id="session-a",
            ordinal=2,
            action_type=OperationActionKind.SET_DESIRED_GRAPH,
            actor_id="jacob",
            payload={
                "workspace_id": "workspace-a",
                "previous_desired_graph_id": "graph-a",
                "desired_graph_id": "graph-b",
            },
        )
        result = DesiredGraphEditResult(
            workspace_id="workspace-a",
            previous_desired_graph_id="graph-a",
            graph_version=graph_record,
            action=action,
        )

        self.assertEqual(result.descriptor()["desired_graph_id"], "graph-b")
        with self.assertRaisesRegex(InvalidOperationCommand, "workspace"):
            DesiredGraphEditResult(
                workspace_id="workspace-b",
                previous_desired_graph_id=None,
                graph_version=graph_record,
                action=action,
            )
        with self.assertRaisesRegex(InvalidOperationCommand, "SET_DESIRED_GRAPH"):
            DesiredGraphEditResult(
                workspace_id="workspace-a",
                previous_desired_graph_id=None,
                graph_version=graph_record,
                action=OperationActionRecord(
                    action_id="action-b",
                    session_id="session-a",
                    ordinal=2,
                    action_type=OperationActionKind.ADD_BLOCK,
                    actor_id="jacob",
                    payload={
                        "workspace_id": "workspace-a",
                        "previous_desired_graph_id": None,
                        "desired_graph_id": "graph-b",
                    },
                ),
            )


if __name__ == "__main__":
    unittest.main()
