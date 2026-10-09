"""C-L03/04/05/07 and C-N05/06: exact destructive intent remains non-executable."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
import json
import queue
import threading
import unittest
from unittest import mock

import psycopg
from psycopg.types.json import Jsonb

from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.operations import OperatorCommandKind
from control_plane_kit_operations.approvals import (
    ApprovalCommandService, ApprovalWorkflowError, ApprovalTargetNotFound, DecideApproval, RequestApproval,
)
from control_plane_kit_operations.postgres import SchemaInstallationError, install_schema
from control_plane_kit_operations.records import ApprovalDecisionKind, ApprovalRequestRecord, OperationActionRecord
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.configuration_cleanup_postgres_fixture import ConfigurationCleanupPostgresFixture, NOW
from tests.test_postgres_effect_attempt_start_concurrency import PostgresEffectAttemptStartConcurrencyTests


class ConfigurationCleanupApprovalTests(ConfigurationCleanupPostgresFixture, unittest.TestCase):
    _factory_with_pids = PostgresEffectAttemptStartConcurrencyTests._factory_with_pids
    _wait_until_blocked_by = PostgresEffectAttemptStartConcurrencyTests._wait_until_blocked_by

    def approvals(self, *, factory=None, clock=None):
        return ApprovalCommandService(factory or self.unit_of_work,
            clock=clock or self.sample_clock, id_factory=self.sample_id)

    def ask(self, plan, *, key="ask-cleanup"):
        return RequestApproval("session-config", plan.plan_id, "requester", (PolicyScope.PLAN_REQUEST,),
                               IdempotencyKey(key))

    def decide(self, request, *, key="decide-cleanup", actor="approver", scope=PolicyScope.PLAN_APPROVE_DESTRUCTIVE):
        return DecideApproval("session-config", request.request_id, actor, (scope,),
                              ApprovalDecisionKind.APPROVED, IdempotencyKey(key))

    def change_desired(self, stores):
        workspace = stores.workspaces.get("workspace-a")
        destination = "graph-current" if workspace.desired_graph_id == "graph-configured" else "graph-configured"
        return stores.workspaces.set_desired_graph("workspace-a", destination)

    def test_destructive_approval_binds_profile_digest_scope_and_distinct_principal(self):
        plan = self.publish().plan_record
        requested = self.approvals().execute(self.ask(plan))
        subject = requested.request.subject
        self.assertEqual(subject.descriptor(), {"kind": "activity-plan", "plan_id": plan.plan_id,
            "profile": "configuration-cleanup-approval.v1", "proposal_fingerprint":
            self.values.configuration_cleanup_proposal_fingerprint(plan.cleanup_proposal)})
        self.assertTrue(requested.request.destructive)
        self.assertIs(requested.request.required_scope, PolicyScope.PLAN_APPROVE_DESTRUCTIVE)
        try:
            self.connection.execute("UPDATE cpk_approval_requests SET destructive=false,required_scope=%s "
                "WHERE request_id=%s", (PolicyScope.PLAN_APPROVE.value, requested.request.request_id))
            corrupted = self.truth()
            self.sampled.clear()
            with self.assertRaises(ApprovalWorkflowError):
                self.approvals().execute(self.decide(requested.request, scope=PolicyScope.PLAN_APPROVE))
            with self.assertRaises(SchemaInstallationError):
                install_schema(self.connection)
            self.assertEqual(self.truth(), corrupted)
            self.assertEqual(self.sampled, [])
        finally:
            self.connection.execute("UPDATE cpk_approval_requests SET destructive=true,required_scope=%s "
                "WHERE request_id=%s", (PolicyScope.PLAN_APPROVE_DESTRUCTIVE.value, requested.request.request_id))
        before = self.truth()
        self.sampled.clear()
        for command in (self.decide(requested.request, scope=PolicyScope.PLAN_APPROVE),
                        self.decide(requested.request, actor="requester")):
            with self.subTest(command=command), self.assertRaises(ApprovalWorkflowError):
                self.approvals().execute(command)
            self.assertEqual(self.truth(), before)
        approved = self.approvals().execute(self.decide(requested.request))
        self.assertEqual(approved.request.subject, subject)
        self.assertIs(approved.decision.decision, ApprovalDecisionKind.APPROVED)
        self.assertEqual(self.member.protective_claims(), self.member.claims)

    def test_fresh_request_and_decision_revalidate_under_lifecycle_lock(self):
        plan = self.publish().plan_record
        first = self.approvals().execute(self.ask(plan))
        with self.unit_of_work() as uow:
            self.change_desired(uow.stores)
            uow.commit()
        self.sampled.clear()
        before = self.truth()
        for command in (self.ask(plan, key="fresh-stale-request"), self.decide(first.request)):
            with self.subTest(command=command), self.assertRaises(ApprovalWorkflowError):
                self.approvals().execute(command)
            self.assertEqual(self.truth(), before)
            self.assertEqual(self.sampled, [])
        replay = self.approvals().execute(self.ask(plan))
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.request, first.request)
        self.assertEqual(self.truth(), before)
        plan = self.publish(self.request(key="plan-unchanged-pins")).plan_record
        pending = self.approvals().execute(self.ask(plan, key="ask-unchanged-pins")).request
        pins = self.pins()
        retained = self.connection.execute("SELECT preimage FROM cpk_effect_attempt_outcomes WHERE run_id='run-config'").fetchone()[0]
        try:
            # Explicit retained-evidence corruption, with every graph pin held
            # fixed, proves BOTH fresh approval commands actually reread proof.
            self.connection.execute("UPDATE cpk_effect_attempt_outcomes SET preimage=%s WHERE run_id='run-config'",
                                    (b"corrupt-direct-outcome",))
            before = self.truth()
            self.sampled.clear()
            for command in (self.ask(plan, key="fresh-bad-outcome"),
                            self.decide(pending, key="decide-bad-outcome")):
                with self.subTest(command=command), self.assertRaises(ApprovalWorkflowError):
                    self.approvals().execute(command)
                self.assertEqual(self.pins(), pins)
                self.assertEqual(self.truth(), before)
                self.assertEqual(self.sampled, [])
        finally:
            self.connection.execute("UPDATE cpk_effect_attempt_outcomes SET preimage=%s WHERE run_id='run-config'", (retained,))

    def test_both_lock_winners_and_changed_locator_during_wait_leave_no_stale_authority(self):
        for command_kind in ("request", "decision"):
            for change in ("context", "record"):
                suffix = command_kind + "-" + change
                plan = self.publish(self.request(key="plan-" + suffix)).plan_record
                command = self.ask(plan, key="ask-" + suffix)
                if command_kind == "decision":
                    request = self.approvals().execute(command).request
                    command = self.decide(request, key="decide-" + suffix)
                pids = queue.Queue()
                self.sampled.clear()
                with ThreadPoolExecutor(max_workers=1) as pool:
                    with self.unit_of_work() as held:
                        held.stores.graphs.lock_receiver_lifecycle("workspace-a")
                        blocker = held.stores.connection.info.backend_pid
                        future = pool.submit(self.approvals(factory=self._factory_with_pids(pids)).execute, command)
                        self._wait_until_blocked_by(pids.get(timeout=5), blocker)
                        self.assertEqual(self.sampled, [])
                        # Locator reads must not take session/workspace row locks before L.
                        with psycopg.connect(self.database_url) as probe:
                            probe.execute("SELECT session_id FROM cpk_operation_sessions "
                                          "WHERE session_id='session-config' FOR UPDATE NOWAIT")
                            probe.execute("SELECT workspace_id FROM cpk_workspaces "
                                          "WHERE workspace_id='workspace-a' FOR UPDATE NOWAIT")
                        if change == "context":
                            self.change_desired(held.stores)
                        else:
                            # Explicit below-owner corruption while the nonauthorizing
                            # locator waits; publication must reload and refuse it.
                            held.stores.connection.execute("UPDATE cpk_activity_plans SET desired_graph_revision="
                                "desired_graph_revision+1 WHERE plan_id=%s", (plan.plan_id,))
                        held.commit()
                    with self.assertRaises(ApprovalWorkflowError):
                        future.result(timeout=10)
                if change == "record":
                    self.connection.execute("UPDATE cpk_activity_plans SET desired_graph_revision=%s WHERE plan_id=%s",
                                            (plan.desired_graph_revision, plan.plan_id))
                self.assertEqual(self.sampled, [])

        # Approval wins L: a concurrent legitimate desired edit must wait until
        # the approval receipt commits, then any fresh decision is stale.
        for command_kind in ("request", "decision"):
            plan = self.publish(self.request(key="plan-approval-wins-" + command_kind)).plan_record
            command = self.ask(plan, key="approval-wins-" + command_kind)
            if command_kind == "decision":
                request = self.approvals().execute(command).request
                command = self.decide(request, key="decision-wins")
            entered, release = threading.Event(), threading.Event()

            def paused_clock():
                entered.set()
                if not release.wait(15):
                    raise AssertionError("approval clock barrier was not released")
                return NOW

            pids = queue.Queue()
            with ThreadPoolExecutor(max_workers=2) as pool:
                approved = pool.submit(self.approvals(factory=self._factory_with_pids(pids), clock=paused_clock).execute,
                                       command)
                try:
                    self.assertTrue(entered.wait(10))
                    approval_pid = pids.get(timeout=5)

                    def edit():
                        with self._factory_with_pids(pids)() as uow:
                            self.change_desired(uow.stores)
                            uow.commit()

                    writer = pool.submit(edit)
                    self._wait_until_blocked_by(pids.get(timeout=5), approval_pid)
                finally:
                    release.set()
                receipt = approved.result(timeout=10)
                writer.result(timeout=10)
            self.assertFalse(receipt.replayed)
            if command_kind == "request":
                with self.assertRaises(ApprovalWorkflowError):
                    self.approvals().execute(self.decide(receipt.request, key="stale-after-approved"))
            else:
                self.assertIs(receipt.decision.decision, ApprovalDecisionKind.APPROVED)

    def test_approval_atomicity_replay_and_corrupt_stored_subject_never_downgrade(self):
        plan = self.publish().plan_record
        ask = self.ask(plan)
        for commit in (False, True):
            before = self.truth()
            with self.assertRaises(RuntimeError):
                self.approvals(factory=self.failure_factory(commit=commit)).execute(ask)
            self.assertEqual(self.truth(), before)
        request = self.approvals().execute(ask).request
        decide = self.decide(request)
        for commit in (False, True):
            before = self.truth()
            with self.assertRaises(RuntimeError):
                self.approvals(factory=self.failure_factory(commit=commit)).execute(decide)
            self.assertEqual(self.truth(), before)
        receipt = self.approvals().execute(decide)
        with self.unit_of_work() as uow:
            self.change_desired(uow.stores)
            uow.commit()
        before = self.truth()
        replay = self.approvals().execute(decide)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.decision, receipt.decision)
        self.assertEqual(self.truth(), before)
        self.sampled.clear()
        with self.assertRaises(ApprovalWorkflowError):
            self.approvals().execute(replace(decide, actor_scopes=()))
        self.assertEqual(self.truth(), before)
        self.assertEqual(self.sampled, [])
        legacy = ActivityPlanApprovalSubject(plan.plan_id)
        self.connection.execute("UPDATE cpk_approval_requests SET subject_payload=%s,review_digest=%s WHERE request_id=%s",
                                (Jsonb(legacy.descriptor()), legacy.review_digest, request.request_id))
        with self.assertRaises((ApprovalWorkflowError, ValueError)):
            self.approvals().execute(decide)

    def test_legacy_cleanup_history_decodes_and_replays_without_new_authority(self):
        plan = self.publish().plan_record
        legacy = replace(plan, plan_id="legacy-cleanup", derivation_profile=None, cleanup_proposal=None)
        historical_command = self.ask(legacy, key="historical-cleanup-request")
        # Literal historical receipt format, not a fresh approval or simulated
        # configuration provenance. Pin the accepted old intent-hash meaning.
        fingerprint = sha256(json.dumps(dict(command="request-approval", session_id=legacy.session_id,
            plan_id=legacy.plan_id, actor_id=historical_command.actor_id, comment=None),
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with self.unit_of_work() as uow:
            history = uow.stores.activity_history
            history.add_plan(legacy)
            subject = ActivityPlanApprovalSubject(legacy.plan_id)
            historical_request = history.add_approval_request(ApprovalRequestRecord(
                "legacy-cleanup-request", legacy.session_id, subject, historical_command.actor_id, NOW,
                PolicyScope.PLAN_APPROVE_DESTRUCTIVE, legacy.plan.activities[0].risk, True,
                idempotency_key=historical_command.idempotency_key.value, intent_fingerprint=fingerprint))
            historical_action = history.add_action(OperationActionRecord("legacy-cleanup-action", legacy.session_id,
                history.next_action_ordinal(legacy.session_id), OperatorCommandKind.REQUEST_APPROVAL,
                historical_command.actor_id, dict(request_id=historical_request.request_id, plan_id=legacy.plan_id,
                    required_scope=PolicyScope.PLAN_APPROVE_DESTRUCTIVE.value,
                    max_risk=legacy.plan.activities[0].risk.value, destructive=True), NOW,
                historical_command.idempotency_key.value, fingerprint))
            uow.commit()
        before = self.truth()
        install_schema(self.connection)
        with self.unit_of_work() as uow:
            recovered = uow.stores.activity_history.get_plan(legacy.plan_id)
            self.assertEqual(recovered, legacy)
            self.assertIsNone(recovered.cleanup_proposal)
        replay = self.approvals().execute(historical_command)
        self.assertTrue(replay.replayed)
        self.assertEqual((replay.request, replay.action), (historical_request, historical_action))
        with self.assertRaises(ApprovalWorkflowError):
            self.approvals().execute(self.ask(legacy, key="fresh-legacy"))
        self.assertEqual(self.truth(), before)

    def test_current_validator_accepts_cleanup_and_legacy_history_without_repair(self):
        plan = self.publish().plan_record
        request = self.approvals().execute(self.ask(plan)).request
        self.approvals().execute(self.decide(request))
        before = self.truth()
        install_schema(self.connection)
        self.assertEqual(self.truth(), before)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.activity_history.get_plan(plan.plan_id), plan)

    def test_current_validator_refuses_corrupt_cleanup_profile_digest_and_plan_relationship(self):
        plan = self.publish().plan_record
        request = self.approvals().execute(self.ask(plan)).request
        original = request.subject.descriptor()
        cases = ({**original, "profile": "unknown"}, {**original, "proposal_fingerprint": "f" * 64},
                 {"kind": "activity-plan", "plan_id": plan.plan_id})
        for payload in cases:
            try:
                self.connection.execute("UPDATE cpk_approval_requests SET subject_payload=%s WHERE request_id=%s",
                                        (Jsonb(payload), request.request_id))
                before = self.truth()
                with self.assertRaises(SchemaInstallationError):
                    install_schema(self.connection)
                self.assertEqual(self.truth(), before)
            finally:
                self.connection.execute("UPDATE cpk_approval_requests SET subject_payload=%s WHERE request_id=%s",
                                        (Jsonb(original), request.request_id))
        self.connection.execute("UPDATE cpk_activity_plans SET desired_graph_revision=desired_graph_revision+1 "
                                "WHERE plan_id=%s", (plan.plan_id,))
        before = self.truth()
        with self.assertRaises(SchemaInstallationError):
            install_schema(self.connection)
        self.assertEqual(self.truth(), before)


    def test_approval_preludes_replays_and_oversized_targets_are_bounded(self):
        from control_plane_kit_operations.postgres.configuration_evidence import _EvidenceRead
        from control_plane_kit_operations.configuration_preparation import ConfigurationEvidenceFootprint
        from tests.test_postgres_configuration_evidence import _ObservedConnection
        from control_plane_kit_operations.postgres import PostgresUnitOfWork
        plan = self.publish().plan_record
        ask = self.ask(plan)
        for command in (replace(ask, plan_id="absent-plan"),
                        DecideApproval("session-config", "absent-request", "approver",
                            (PolicyScope.PLAN_APPROVE_DESTRUCTIVE,), ApprovalDecisionKind.APPROVED,
                            IdempotencyKey("missing-request"))):
            with self.assertRaises(ApprovalTargetNotFound):
                self.approvals().execute(command)
        actual = _EvidenceRead.query
        injected = []
        last_ref = max(self.refs, key=lambda ref: ref.allocation_id)

        def near_capacity(reader, sql, params, **kwargs):
            rows = actual(reader, sql, params, **kwargs)
            # All allocations share the already cached original invocation;
            # exhaustion here leaves only the known command tail to account.
            if ("FROM cpk_configuration_claims WHERE workspace_id" in sql
                    and params == (last_ref.workspace_id, last_ref.allocation_id) and not injected):
                injected.append(True)
                reader.used = ConfigurationEvidenceFootprint(4095, 0, 0, 0)
            return rows

        before = self.truth()
        self.sampled.clear()
        with mock.patch.object(_EvidenceRead, "query", near_capacity):
            with self.assertRaises(ApprovalWorkflowError) as raised:
                self.approvals().execute(ask)
        self.assertIsNone(raised.exception.__context__)
        self.assertTrue(injected)
        self.assertEqual(self.sampled, [])
        self.assertEqual(self.truth(), before)
        requested = self.assert_accounted_command(lambda factory: self.approvals(factory=factory).execute(ask))
        decide = self.decide(requested.request)
        for command in (ask, decide, decide):
            self.assert_accounted_command(lambda factory: self.approvals(factory=factory).execute(command))
        original = self.connection.execute("SELECT payload FROM cpk_activity_plans WHERE plan_id=%s",
                                           (plan.plan_id,)).fetchone()[0]
        try:
            # Malformed new envelope with a contradictory legacy profile must
            # remain bounded before the whole JSONB payload is transported.
            malformed = original | {"derivation_profile": "structural-v1", "extra": "CANARY" * 400000}
            nested_scalar = dict(schema="control-plane-kit.operations.activity-plan-record",
                version=1, derivation_profile="structural-v1", plan=42)
            for payload in (malformed, 42, nested_scalar):
                self.connection.execute("UPDATE cpk_activity_plans SET payload=%s WHERE plan_id=%s",
                                        (Jsonb(payload), plan.plan_id))
                observed = dict(rows=0, bytes=0, largest_cell=0, statements=0)
                factory = lambda: PostgresUnitOfWork(lambda: _ObservedConnection(psycopg.connect(self.database_url), observed))
                before = self.truth()
                self.sampled.clear()
                for command in (replace(ask, idempotency_key=IdempotencyKey("oversized-fresh")), ask, decide):
                    with self.assertRaises(ApprovalWorkflowError) as raised:
                        self.approvals(factory=factory).execute(command)
                    self.assertIsNone(raised.exception.__context__)
                self.assertLess(observed["largest_cell"], 1024 * 1024)
                self.assertEqual(self.sampled, [])
                self.assertEqual(self.truth(), before)
        finally:
            self.connection.execute("UPDATE cpk_activity_plans SET payload=%s WHERE plan_id=%s", (Jsonb(original), plan.plan_id))


if __name__ == "__main__":
    unittest.main()
