from __future__ import annotations

import ast
import json
from pathlib import Path
import unittest


CONTRACT_PATH = Path(__file__).resolve().parents[1] / "session-command-serialization.json"
SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "control_plane_kit_operations"
EXPECTED_ROLES = (
    "command-identity",
    "session-lifecycle",
    "workspace-graph",
    "dependent-truth",
    "action-ordinal",
)
EXPECTED_COMMANDS = {
    "start-operation-session",
    "close-operation-session",
    "cancel-operation-session",
    "record-operation-action",
    "set-desired-graph",
    "publish-desired-realized-projection",
    "request-activity-plan",
    "request-approval",
    "request-gateway-key-rotation-approval",
    "decide-approval",
    "admit-execution",
    "claim-run",
    "transition-run",
    "record-recovery-decision",
    "retry-failed-activity-run",
    "advance-current-graph",
}


class SessionCommandSerializationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    def test_global_lock_roles_are_closed_unique_and_ordered(self) -> None:
        roles = self.contract["global_lock_order"]

        self.assertEqual(tuple(item["role"] for item in roles), EXPECTED_ROLES)
        self.assertEqual(
            tuple(item["ordinal"] for item in roles),
            tuple(range(1, len(EXPECTED_ROLES) + 1)),
        )
        self.assertEqual(len({item["role"] for item in roles}), len(roles))
        for item in roles:
            self.assertTrue(item["mechanism"])
            self.assertTrue(item["law"])

    def test_every_session_action_writer_has_one_role_map(self) -> None:
        commands = self.contract["command_roles"]
        identities = [item["command"] for item in commands]

        self.assertEqual(set(identities), EXPECTED_COMMANDS)
        self.assertEqual(len(identities), len(set(identities)))
        for item in commands:
            self.assertTrue(item["service"])
            self.assertTrue(item["writer"])
            self.assertTrue(item["source"])
            self.assertTrue(item["session_rule"])

    def test_role_map_covers_every_current_operation_action_writer(self) -> None:
        expected = {item["writer"] for item in self.contract["command_roles"]}

        self.assertEqual(_operation_action_writers(), expected)

    def test_every_existing_session_writer_acquires_locks_in_contract_order(self) -> None:
        writers = _writer_call_sequences()

        for item in self.contract["command_roles"]:
            if item["command"] == "start-operation-session":
                continue
            self.assertIn(
                item["writer"],
                writers,
                f"missing declared session writer {item['writer']}",
            )
            calls = writers[item["writer"]]
            with self.subTest(command=item["command"]):
                identity = calls.index("lock_action_idempotency")
                session = calls.index("get_session_for_update")
                if item["command"] in {
                    "record-recovery-decision",
                    "retry-failed-activity-run",
                }:
                    observation = calls.index(
                        "observe_request_lease_for_update"
                    )
                    event_ordinal = calls.index("next_event_ordinal")
                    self.assertLess(session, observation)
                    self.assertLess(observation, event_ordinal)
                ordinal = calls.index("next_action_ordinal")
                self.assertLess(identity, session)
                self.assertLess(session, ordinal)
                if item["command"] in {
                    "record-recovery-decision",
                    "retry-failed-activity-run",
                }:
                    self.assertLess(event_ordinal, ordinal)

    def test_rotation_projection_locks_workspace_before_rotation_truth(self) -> None:
        source = (
            SOURCE_ROOT / "gateway_key_rotation_projection.py"
        ).read_text(encoding="utf-8")

        locator = source.index("gateway_key_rotations.get(command.rotation_id)")
        workspace = source.index("workspaces.get_for_update")
        rotation = source.index("gateway_key_rotations.get_for_update")
        self.assertLess(locator, workspace)
        self.assertLess(workspace, rotation)

    def test_each_command_uses_a_strict_global_order_subsequence(self) -> None:
        order = {
            role: index
            for index, role in enumerate(EXPECTED_ROLES)
        }

        for item in self.contract["command_roles"]:
            roles = item["roles"]
            with self.subTest(command=item["command"]):
                self.assertEqual(len(roles), len(set(roles)))
                self.assertTrue(set(roles).issubset(order))
                self.assertEqual(
                    [order[role] for role in roles],
                    sorted(order[role] for role in roles),
                )
                self.assertEqual(roles[0], "command-identity")
                self.assertEqual(roles[-1], "action-ordinal")
                if item["command"] != "start-operation-session":
                    self.assertIn("session-lifecycle", roles)

    def test_replay_terminal_and_transaction_outcomes_are_explicit(self) -> None:
        laws = self.contract["outcome_laws"]

        self.assertEqual(
            set(laws),
            {
                "identical_replay",
                "changed_intent",
                "competing_commands",
                "terminal_fence",
                "terminal_replay",
                "transaction",
            },
        )
        for law in laws.values():
            self.assertTrue(law)

def _operation_action_writers() -> set[str]:
    return set(_writer_call_sequences())


def _writer_call_sequences() -> dict[str, tuple[str, ...]]:
    writers: dict[str, tuple[str, ...]] = {}
    for source_path in SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        relative = source_path.relative_to(SOURCE_ROOT).as_posix()
        class_names: list[str] = []

        class WriterVisitor(ast.NodeVisitor):
            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                class_names.append(node.name)
                self.generic_visit(node)
                class_names.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                calls = tuple(
                    _call_name(candidate)
                    for candidate in sorted(
                        (item for item in ast.walk(node) if isinstance(item, ast.Call)),
                        key=lambda item: (item.lineno, item.col_offset),
                    )
                )
                if "add_action" in calls:
                    owner = ".".join((*class_names, node.name))
                    writers[f"{relative}:{owner}"] = calls

        WriterVisitor().visit(tree)
    return writers


def _call_name(node: ast.AST) -> str:
    if not isinstance(node, ast.Call):
        return ""
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        if node.func.id in {
            "_desired_session",
            "_get_open_session_for_update",
            "_get_session_for_update",
            "_open_session",
            "_session_for_update",
        }:
            return "get_session_for_update"
        return node.func.id
    return ""


if __name__ == "__main__":
    unittest.main()
