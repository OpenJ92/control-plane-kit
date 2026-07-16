import unittest

from control_plane_kit.stores import (
    OperationActionKind,
    OperationActionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
)
from control_plane_kit.workflows import (
    CancelOperationSession,
    CloseOperationSession,
    IdempotencyKey,
    InvalidOperationCommand,
    OperationCommandResult,
    RecordOperationAction,
    StartOperationSession,
)


class OperationCommandVocabularyTests(unittest.TestCase):
    def test_start_command_is_a_validated_deterministic_product(self):
        command = StartOperationSession(
            workspace_id="workspace-a",
            actor_id="jacob",
            title="Swap API",
            idempotency_key=IdempotencyKey("request-a"),
            metadata={"z": "last", "a": "first"},
        )

        self.assertEqual(
            command.descriptor(),
            {
                "command": "start_operation_session",
                "workspace_id": "workspace-a",
                "actor_id": "jacob",
                "title": "Swap API",
                "idempotency_key": "request-a",
                "metadata": {"a": "<redacted>", "z": "<redacted>"},
            },
        )

    def test_transition_commands_have_distinct_tags(self):
        key = IdempotencyKey("request-a")

        close = CloseOperationSession("session-a", "jacob", key).descriptor()
        cancel = CancelOperationSession("session-a", "jacob", key).descriptor()

        self.assertEqual(close["command"], "close_operation_session")
        self.assertEqual(cancel["command"], "cancel_operation_session")

    def test_record_action_requires_closed_action_kind(self):
        command = RecordOperationAction(
            session_id="session-a",
            actor_id="jacob",
            action_type=OperationActionKind.ADD_BLOCK,
            idempotency_key=IdempotencyKey("request-a"),
            payload={"node_id": "api-v2"},
        )

        self.assertEqual(command.descriptor()["action_type"], "add_block")
        self.assertEqual(command.descriptor()["payload"], {"node_id": "<redacted>"})
        with self.assertRaisesRegex(InvalidOperationCommand, "OperationActionKind"):
            RecordOperationAction(
                session_id="session-a",
                actor_id="jacob",
                action_type="invented_action",  # type: ignore[arg-type]
                idempotency_key=IdempotencyKey("request-b"),
            )

    def test_required_identifiers_and_idempotency_keys_fail_closed(self):
        invalid = (
            lambda: IdempotencyKey(" "),
            lambda: IdempotencyKey("x" * 201),
            lambda: IdempotencyKey(1),  # type: ignore[arg-type]
            lambda: StartOperationSession("", "jacob", "Swap API", IdempotencyKey("a")),
            lambda: StartOperationSession(
                "workspace-a",
                "jacob",
                "Swap API",
                IdempotencyKey("a"),
                metadata={"token": 1},  # type: ignore[dict-item]
            ),
            lambda: CloseOperationSession("session-a", "", IdempotencyKey("a")),
            lambda: CancelOperationSession("", "jacob", IdempotencyKey("a")),
        )

        for construct in invalid:
            with self.subTest(construct=construct), self.assertRaises(InvalidOperationCommand):
                construct()

    def test_command_descriptors_do_not_publish_operator_values(self):
        start = StartOperationSession(
            workspace_id="workspace-a",
            actor_id="jacob",
            title="Rotate credentials",
            idempotency_key=IdempotencyKey("request-a"),
            metadata={"deployment_label": "do-not-publish"},
        )
        action = RecordOperationAction(
            session_id="session-a",
            actor_id="jacob",
            action_type=OperationActionKind.PATCH_VARIABLE,
            idempotency_key=IdempotencyKey("request-b"),
            payload={"database_address": "postgresql://internal"},
        )

        self.assertEqual(
            start.descriptor()["metadata"],
            {"deployment_label": "<redacted>"},
        )
        self.assertEqual(
            action.descriptor()["payload"],
            {"database_address": "<redacted>"},
        )

    def test_command_evidence_accepts_secret_references_but_rejects_secret_values(self):
        accepted = RecordOperationAction(
            session_id="session-a",
            actor_id="jacob",
            action_type=OperationActionKind.PATCH_VARIABLE,
            idempotency_key=IdempotencyKey("request-a"),
            payload={"database_secret_ref": "vault://database/a"},
        )

        self.assertEqual(accepted.payload["database_secret_ref"], "vault://database/a")
        with self.assertRaisesRegex(InvalidOperationCommand, "secret reference"):
            RecordOperationAction(
                session_id="session-a",
                actor_id="jacob",
                action_type=OperationActionKind.PATCH_VARIABLE,
                idempotency_key=IdempotencyKey("request-b"),
                payload={"nested": {"api_token": "do-not-store"}},
            )

    def test_durable_records_reject_untyped_lifecycle_and_action_values(self):
        with self.assertRaisesRegex(TypeError, "OperationSessionStatus"):
            OperationSessionRecord(
                session_id="session-a",
                workspace_id="workspace-a",
                actor_id="jacob",
                title="Swap API",
                status="invented",  # type: ignore[arg-type]
                created_at="2026-07-15T00:00:00Z",
            )
        with self.assertRaisesRegex(TypeError, "OperationActionKind"):
            OperationActionRecord(
                action_id="action-a",
                session_id="session-a",
                ordinal=1,
                action_type="invented",  # type: ignore[arg-type]
                actor_id="jacob",
            )

    def test_result_descriptor_preserves_typed_evidence(self):
        result = OperationCommandResult(
            session=OperationSessionRecord(
                session_id="session-a",
                workspace_id="workspace-a",
                actor_id="jacob",
                title="Swap API",
                status=OperationSessionStatus.OPEN,
                created_at="2026-07-15T00:00:00Z",
            ),
            action=OperationActionRecord(
                action_id="action-a",
                session_id="session-a",
                ordinal=1,
                action_type=OperationActionKind.SESSION_STARTED,
                actor_id="jacob",
            ),
            replayed=True,
        )

        self.assertEqual(
            result.descriptor(),
            {
                "session_id": "session-a",
                "status": "open",
                "action_id": "action-a",
                "action_type": "session_started",
                "ordinal": 1,
                "replayed": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
