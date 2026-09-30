"""Original gateway admission, recovery ownership and bounded receipt reads."""
from contextlib import contextmanager
from dataclasses import replace
import unittest

from psycopg.types.json import Jsonb

from control_plane_kit_core.operations.commands import OperatorCommandKind
from control_plane_kit_core.operations.lifecycle import RecoveryDecisionKind
from control_plane_kit_operations.lifecycle import RunLifecycleError
from control_plane_kit_operations.postgres import GatewayKeyRotationStore, PostgresActivityHistoryStore
from tests.activity_run_retry_interpreter_fixture import PostgresActivityRunRetryFixture


class GatewayChildRecoveryPermissionTests(PostgresActivityRunRetryFixture, unittest.TestCase):
    @contextmanager
    def forbid_rotation_reads(self):
        originals = {name: getattr(GatewayKeyRotationStore, name) for name in ("get", "get_for_update")}
        def forbidden(*args, **kwargs):
            self.fail("retained child permission read mutable rotation state")
        try:
            for name in originals:
                setattr(GatewayKeyRotationStore, name, forbidden)
            yield
        finally:
            for name, method in originals.items():
                setattr(GatewayKeyRotationStore, name, method)

    def active_child(self):
        self.reset_truth(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM,
            approval_subject="gateway-key-rotation")
        with self.unit_of_work() as uow:
            request = uow.stores.execution.get_request("request-a")
            approval = uow.stores.activity_history.get_approval_request(request.approval_request_id)
            plan = uow.stores.activity_history.get_plan(request.identity.plan_id)
        self.assertNotEqual(approval.session_id, request.identity.session_id)
        return request, approval, plan

    def test_real_parent_child_fresh_and_replay_keep_original_approval_after_parent_close(self):
        for retry in (False, True):
            with self.subTest(retry=retry):
                if retry:
                    self.reset_retry_truth(approval_subject="gateway-key-rotation")
                    service = self.retry_service("run-b", "retry-decision", "retry-opened", "retry-action")
                    command = self.retry_command()
                else:
                    self.active_child()
                    service = self.service("renew-decision", "renew-consequence", "renew-action")
                    command = self.command(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM)
                self.connection.execute("UPDATE cpk_operation_sessions SET status='closed', closed_at=clock_timestamp() "
                    "WHERE session_id='rotation-session'")
                with self.forbid_rotation_reads():
                    result = service.execute(command)
                    self.assertFalse(result.replayed)
                    self.connection.execute("UPDATE cpk_operation_sessions SET status='closed', closed_at=clock_timestamp() "
                        "WHERE session_id='session-a'")
                    self.connection.execute("UPDATE cpk_execution_requests SET claimed_at='1999-01-01', "
                        "lease_expires_at='2000-01-01' WHERE request_id='request-a'")
                    before = self.snapshot()
                    replay = service.execute(command)
                    self.assertEqual(replay, replace(result, request=replay.request, replayed=True))
                    self.assertEqual(self.snapshot(), before)

    def test_original_association_corruption_refuses_without_ids_or_writes(self):
        mutations = {
            "admission": ("UPDATE cpk_operation_actions SET payload=payload || %s WHERE action_id='action-admit-fixture-request-a'",
                Jsonb({"approval_request_id": "foreign"})),
            "approval-action": ("UPDATE cpk_operation_actions SET payload=payload || %s WHERE action_id='approval-request-a-action'",
                Jsonb({"review_digest": "f" * 64})),
            "publication-rotation": ("UPDATE cpk_operation_actions SET payload=payload || %s WHERE action_id='child-publication'",
                Jsonb({"source_operation_id": "foreign"})),
            "publication-version": ("UPDATE cpk_operation_actions SET payload=payload || %s WHERE action_id='child-publication'",
                Jsonb({"source_operation_version": 0})),
            "publication-lineage": ("UPDATE cpk_operation_actions SET payload=payload || %s WHERE action_id='child-publication'",
                Jsonb({"previous_realized_projection_id": "foreign"})),
        }
        for label, (sql, value) in mutations.items():
            with self.subTest(label=label):
                self.active_child()
                self.connection.execute(sql, (value,))
                before = self.snapshot()
                service, ids = self.service_with_sequence("unused-decision", "unused-consequence", "unused-action")
                with self.forbid_rotation_reads(), self.assertRaises(RunLifecycleError):
                    service.execute(self.command(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM))
                self.assertEqual(ids.calls, [])
                self.assertEqual(self.snapshot(), before)

    def test_publication_lookup_keeps_unique_candidate_amid_long_unrelated_history(self):
        _, _, plan = self.active_child()
        # Unrelated history is not an arbitrary denial budget. It supplies no
        # authority; only the one original matching publication may qualify.
        self.connection.execute("INSERT INTO cpk_operation_actions "
            "(action_id,session_id,ordinal,action_type,actor_id,payload,created_at) "
            "SELECT 'unrelated-' || n, session_id, 100+n, action_type, actor_id, "
            "jsonb_build_object('desired_realized_projection_id','unrelated-' || n), created_at "
            "FROM cpk_operation_actions CROSS JOIN generate_series(1,250) n WHERE action_id='child-publication'")
        with self.unit_of_work() as uow:
            history = uow.stores.activity_history
            candidates = history._projection_publication_actions("session-a", plan.desired_realized_projection_id)
            self.assertEqual(tuple(action.action_id for action in candidates), ("child-publication",))
            self.assertEqual(history._projection_publication_actions("rotation-session", plan.desired_realized_projection_id), ())
            self.assertEqual(history._projection_publication_actions("session-a", "other-projection"), ())
        with self.forbid_rotation_reads():
            self.assertFalse(self.service("renew-decision", "renew-consequence", "renew-action").execute(
                self.command(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM)).replayed)

    def test_missing_malformed_or_ambiguous_publication_never_elects_a_good_receipt(self):
        for kind in ("missing", "wrong-kind", "wrong-phase", "duplicate", "malformed-duplicate"):
            with self.subTest(kind=kind):
                _, _, plan = self.active_child()
                if kind == "missing":
                    self.connection.execute("DELETE FROM cpk_operation_actions WHERE action_id='child-publication'")
                elif kind == "wrong-kind":
                    self.connection.execute("UPDATE cpk_operation_actions SET action_type=%s WHERE action_id='child-publication'",
                        (OperatorCommandKind.START_OPERATION_SESSION.value,))
                elif kind == "wrong-phase":
                    self.connection.execute("UPDATE cpk_realized_graph_projections SET projection_key='foreign-phase' WHERE projection_id=%s",
                        (plan.desired_realized_projection_id,))
                else:
                    with self.unit_of_work() as uow:
                        history = uow.stores.activity_history
                        original, = history._projection_publication_actions("session-a", plan.desired_realized_projection_id)
                        payload = dict(original.payload)
                        if kind == "malformed-duplicate":
                            payload["source_operation_id"] = "foreign"
                        for ordinal in (100, 101, 102):
                            history.add_action(replace(original, action_id=f"duplicate-{ordinal}", ordinal=ordinal,
                                idempotency_key=None, intent_fingerprint=None, payload=payload))
                        self.assertEqual(len(history._projection_publication_actions(
                            "session-a", plan.desired_realized_projection_id)), 2)
                        uow.commit()
                before = self.snapshot()
                service, ids = self.service_with_sequence("unused-a", "unused-b", "unused-c")
                with self.forbid_rotation_reads(), self.assertRaises(RunLifecycleError):
                    service.execute(self.command(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM))
                self.assertEqual(ids.calls, [])
                self.assertEqual(self.snapshot(), before)

    def test_final_same_owner_approval_reread_rolls_back_recovery_and_retry(self):
        for retry in (False, True):
            with self.subTest(retry=retry):
                if retry:
                    self.reset_retry_truth(approval_subject="gateway-key-rotation")
                    service = self.retry_service("late-run", "late-decision", "late-opened", "late-action")
                    command = self.retry_command()
                else:
                    self.active_child()
                    service = self.service("late-decision", "late-consequence", "late-action")
                    command = self.command(RecoveryDecisionKind.RENEW_ACTIVE_CLAIM)
                before = self.snapshot()
                digest = self.connection.execute("SELECT review_digest FROM cpk_approval_requests "
                    "WHERE request_id='approval-request-a'").fetchone()
                original = PostgresActivityHistoryStore.add_action
                def corrupt_after_write(store, action):
                    result = original(store, action)
                    if action.action_id == "late-action":
                        store._connection.execute("UPDATE cpk_approval_requests SET review_digest=%s "
                            "WHERE request_id='approval-request-a'", ("f" * 64,))
                    return result
                PostgresActivityHistoryStore.add_action = corrupt_after_write
                try:
                    with self.forbid_rotation_reads(), self.assertRaises(RunLifecycleError):
                        service.execute(command)
                finally:
                    PostgresActivityHistoryStore.add_action = original
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.connection.execute("SELECT review_digest FROM cpk_approval_requests "
                    "WHERE request_id='approval-request-a'").fetchone(), digest)
