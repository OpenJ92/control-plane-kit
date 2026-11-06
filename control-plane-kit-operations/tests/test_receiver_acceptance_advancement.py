"""#1904 real advancement over assumed completion; no successor-health proof."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import unittest

import psycopg

from control_plane_kit_core.operations import ActivityEventKind, ActivityRunStatus
from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_operations.advancement import CurrentGraphAdvancementError, CurrentGraphAdvancementConflict
from control_plane_kit_operations.admission import ExecutionAdmissionConflict
from control_plane_kit_operations.deployment_transitions import InitialDeployment, NoOpDeployment, TeardownDeployment
from tests.receiver_canonical_acceptance_fixture import ReceiverCanonicalAcceptanceFixture


class ReceiverAcceptanceAdvancementTests(ReceiverCanonicalAcceptanceFixture, unittest.TestCase):
    def accept_receiver(self, suffix="first"):
        introduced = self.desired_receiver(suffix, graph=self.canonical_receiver_graph)
        claimed, transition, plan = self.retained_success(suffix)
        self.assertIsInstance(transition, InitialDeployment)
        self.assertTrue(plan.plan.activities)
        self.assertIsNone(self.receiver_origin().first_accepted_action_id)
        accepted = self.advance(claimed, suffix)
        origin = self.receiver_origin()
        self.assertEqual(origin.introducing_action_id, introduced.action.action_id)
        self.assertEqual(origin.first_accepted_action_id, accepted.action.action_id)
        self.assertEqual(origin.first_accepted_session_id, claimed.request.identity.session_id)
        self.assertIsNone(origin.retired_action_id)
        return claimed, accepted, origin

    def test_real_advancement_alone_creates_acceptance_from_assumed_completion(self):
        claimed, accepted, origin = self.accept_receiver()
        with self.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get("workspace-a")
            plan = uow.stores.activity_history.get_plan(claimed.request.identity.plan_id)
        self.assertEqual(workspace.current_graph_id, plan.desired_graph_id)
        self.assertEqual(workspace.current_realized_projection_id, plan.desired_realized_projection_id)
        self.assertEqual(self.advance(claimed, "first"), replace(accepted, replayed=True))
        self.assertEqual(self.receiver_origin(), origin)

    def test_desired_noop_b_and_c_refuse_execution_and_preserve_accepted_a(self):
        # Strengthened desired-only/refusal law. Actual accepted A -> B -> C
        # belongs to #1912; an empty plan is not executable work.
        _, _, original = self.accept_receiver()
        graph_ids = [original.introducing_graph_id]
        with self.unit_of_work() as uow:
            accepted = uow.stores.workspaces.get("workspace-a")
        for suffix in ("second", "third"):
            with self.subTest(suffix=suffix):
                desired = self.desired_receiver(suffix, graph=self.canonical_receiver_graph)
                graph_ids.append(desired.graph_version_id)
                transition, plan, approval = self.plan_and_approve(suffix)
                self.assertIsInstance(transition, NoOpDeployment)
                self.assertEqual(plan.plan.activities, ())
                before = self.acceptance_truth()
                with self.assertRaises(ExecutionAdmissionConflict):
                    self.admit_approved(suffix, plan, approval,
                        id_factory=lambda: self.fail("empty admission allocated an identity"))
                self.assertEqual(self.acceptance_truth(), before)
                with self.unit_of_work() as uow:
                    workspace = uow.stores.workspaces.get("workspace-a")
                self.assertEqual((workspace.current_graph_id, workspace.current_realized_projection_id),
                    (accepted.current_graph_id, accepted.current_realized_projection_id))
                self.assertEqual(workspace.desired_graph_id, desired.graph_version_id)
                self.assertEqual(workspace.desired_realized_projection_id, plan.desired_realized_projection_id)
                self.assertEqual(workspace.desired_graph_revision, plan.desired_graph_revision)
                self.assertEqual(self.receiver_origin(), original)
                self.assertIsNone(self.receiver_origin().retired_action_id)
        self.assertEqual(len(set(graph_ids)), 3)

    def test_desired_omission_does_not_retire_but_real_accepted_teardown_does(self):
        _, _, original = self.accept_receiver()
        self.desired_receiver("remove", graph=DeploymentGraph("removed"))
        self.assertEqual(self.receiver_origin(), original)
        claimed, transition, plan = self.retained_success("remove")
        self.assertIsInstance(transition, TeardownDeployment)
        self.assertTrue(plan.plan.activities)
        self.assertEqual(self.receiver_origin(), original)
        removed = self.advance(claimed, "remove")
        self.assertEqual(self.receiver_origin(), replace(original,
            retired_action_id=removed.action.action_id,
            retired_session_id=claimed.request.identity.session_id))

    def test_failed_or_incomplete_teardown_never_changes_current_or_retirement(self):
        _, _, original = self.accept_receiver()
        self.desired_receiver("remove", graph=DeploymentGraph("removed"))
        claimed, _, _ = self.retained_success("remove")
        row = self.connection.execute("SELECT status,settled_at FROM cpk_activity_runs WHERE run_id=%s",
            (claimed.run.run_id,)).fetchone()
        try:
            for status, settled in ((ActivityRunStatus.FAILED.value, None), (ActivityRunStatus.RUNNING.value, None)):
                with self.subTest(status=status):
                    self.connection.execute("UPDATE cpk_activity_runs SET status=%s,settled_at=%s WHERE run_id=%s",
                        (status, settled, claimed.run.run_id))
                    before = self.acceptance_truth()
                    with self.assertRaises(CurrentGraphAdvancementError):
                        self.advance(claimed, "refused-remove")
                    self.assertEqual(self.acceptance_truth(), before)
                    self.assertEqual(self.receiver_origin(), original)
        finally:
            self.connection.execute("UPDATE cpk_activity_runs SET status=%s,settled_at=%s WHERE run_id=%s",
                (*row, claimed.run.run_id))

    def test_missing_native_outcome_refuses_without_acceptance(self):
        self.desired_receiver("first", graph=self.canonical_receiver_graph)
        claimed, _, _ = self.retained_success("first")
        # An explicitly corrupted consumer input; no upstream execution claim.
        # Preserve the exact row for restoration even if the assertion fails.
        from psycopg.types.json import Jsonb
        row = self.connection.execute("SELECT to_jsonb(t) FROM cpk_effect_attempt_outcomes t "
            "WHERE run_id=%s ORDER BY activity_id LIMIT 1", (claimed.run.run_id,)).fetchone()[0]
        try:
            self.assertEqual(self.connection.execute("DELETE FROM cpk_effect_attempt_outcomes "
                "WHERE run_id=%s AND activity_id=%s AND attempt=%s",
                (row["run_id"], row["activity_id"], row["attempt"])).rowcount, 1)
            before = self.acceptance_truth()
            with self.assertRaises(CurrentGraphAdvancementError):
                self.advance(claimed, "missing-outcome")
            self.assertEqual(self.acceptance_truth(), before)
            self.assertIsNone(self.receiver_origin().first_accepted_action_id)
        finally:
            self.connection.execute("INSERT INTO cpk_effect_attempt_outcomes SELECT * FROM "
                "jsonb_populate_record(NULL::cpk_effect_attempt_outcomes,%s)", (Jsonb(row),))

    def test_late_action_and_deferred_commit_failure_roll_back_acceptance_and_retirement(self):
        self.desired_receiver("first", graph=self.canonical_receiver_graph)
        claimed, _, _ = self.retained_success("first")
        self.assert_late_and_deferred_rollback(claimed)
        self.advance(claimed, "first")
        original = self.receiver_origin()
        self.desired_receiver("remove", graph=DeploymentGraph("removed"))
        removal, _, _ = self.retained_success("remove")
        self.assert_late_and_deferred_rollback(removal)
        self.assertEqual(self.receiver_origin(), original)

    def assert_late_and_deferred_rollback(self, claimed):
        original = self.receiver_origin()
        self.connection.execute("CREATE FUNCTION c3_reject_acceptance() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'test acceptance refused' USING ERRCODE='23514'; END $$")
        try:
            for deferred in (False, True):
                with self.subTest(deferred=deferred):
                    if deferred:
                        query = "CREATE CONSTRAINT TRIGGER c3_reject_acceptance AFTER INSERT ON cpk_operation_actions " \
                            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                    else:
                        query = "CREATE TRIGGER c3_reject_acceptance BEFORE INSERT ON cpk_operation_actions FOR EACH ROW "
                    self.connection.execute(query + "WHEN (NEW.action_type='advance-current-graph') "
                        "EXECUTE FUNCTION c3_reject_acceptance()")
                    try:
                        before = self.acceptance_truth()
                        with self.assertRaises(psycopg.errors.CheckViolation):
                            self.advance(claimed, "late-" + str(deferred))
                        self.assertEqual(self.acceptance_truth(), before)
                        self.assertEqual(self.receiver_origin(), original)
                    finally:
                        self.connection.execute("DROP TRIGGER c3_reject_acceptance ON cpk_operation_actions")
        finally:
            self.connection.execute("DROP FUNCTION c3_reject_acceptance()")

    def test_competing_advancements_have_one_atomic_acceptance_winner(self):
        self.desired_receiver("first", graph=self.canonical_receiver_graph)
        claimed, _, _ = self.retained_success("first")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.advance, claimed, suffix) for suffix in ("winner-a", "winner-b")]
        accepted, refused = [], []
        for future in futures:
            try:
                accepted.append(future.result(timeout=1))
            except CurrentGraphAdvancementConflict as error:
                refused.append(error)
        self.assertEqual((len(accepted), len(refused)), (1, 1))
        self.assertEqual(self.receiver_origin().first_accepted_action_id, accepted[0].action.action_id)
        with self.unit_of_work() as uow:
            events = uow.stores.execution.events_for_run(claimed.run.run_id)
        self.assertEqual(tuple(event.event_id for event in events
            if event.kind is ActivityEventKind.CURRENT_GRAPH_ADVANCED), (accepted[0].event.event_id,))

    def test_witness_time_membership_drift_rolls_back_acceptance_and_retirement(self):
        self.desired_receiver("first", graph=self.canonical_receiver_graph)
        first, _, _ = self.retained_success("first")
        self.assert_witness_membership_rollback(first)
        self.advance(first, "first")
        self.desired_receiver("remove", graph=DeploymentGraph("removed"))
        removal, _, _ = self.retained_success("remove")
        self.assert_witness_membership_rollback(removal)
        self.advance(removal, "remove")
        self.assertIsNotNone(self.receiver_origin().retired_action_id)

    def assert_witness_membership_rollback(self, claimed):
        before = self.acceptance_truth()
        original = self.receiver_origin()
        self.connection.execute("CREATE FUNCTION c3_change_membership() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN DELETE FROM cpk_graph_receiver_bindings WHERE workspace_id=NEW.workspace_id "
            "AND receiver_id=NEW.receiver_id; RETURN NEW; END $$")
        self.connection.execute("CREATE TRIGGER c3_change_membership AFTER UPDATE ON cpk_graph_receiver_introductions "
            "FOR EACH ROW EXECUTE FUNCTION c3_change_membership()")
        try:
            with self.assertRaises(CurrentGraphAdvancementConflict):
                self.advance(claimed, "membership-drift")
            self.assertEqual(self.acceptance_truth(), before)
            self.assertEqual(self.receiver_origin(), original)
        finally:
            self.connection.execute("DROP TRIGGER c3_change_membership ON cpk_graph_receiver_introductions")
            self.connection.execute("DROP FUNCTION c3_change_membership()")

    def test_coherent_witness_time_intent_material_change_rolls_back(self):
        from psycopg import sql
        from psycopg.types.json import Jsonb
        from control_plane_kit_core.operations import EffectAttemptIdentity, EffectAttemptStatus, RunId
        from control_plane_kit_core.planning import StartRuntime
        from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
        from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
        from control_plane_kit_operations.effect_attempt_intent_evidence import EffectAttemptIntentRecord
        from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
        from control_plane_kit_operations.postgres import effect_attempt_store, effect_attempt_intent_store, effect_outcome_store
        from control_plane_kit_operations.receiver_execution_scopes import classify_receiver_scope_evidence
        from tests.receiver_recorded_completion_fixture import state_event

        self.desired_receiver("first", graph=self.canonical_receiver_graph)
        claimed, _, plan = self.retained_success("first")
        activity = next(item for item in plan.plan.activities if type(item.operation) is StartRuntime)
        identity = EffectAttemptIdentity(RunId(claimed.run.run_id), activity.activity_id.value, 1)
        with self.unit_of_work() as uow:
            retained = uow.stores.effect_attempt_intents.get(identity)
            attempt = uow.stores.effect_attempts.get(identity)
            outcome = uow.stores.effect_outcomes.get(identity, attempt.latest_transition_event.event_id)
        changed = replace(retained.intent, authority_ref=RuntimeAuthorityReference("different-authority"))
        fingerprint = runtime_effect_intent_fingerprint(changed)
        changed_outcome = replace(outcome.outcome, request_fingerprint=fingerprint)
        terminal = replace(attempt.state, request_fingerprint=fingerprint,
            outcome_fingerprint=changed_outcome.outcome_fingerprint)
        started = replace(terminal, status=EffectAttemptStatus.STARTED, outcome_fingerprint=None)
        start = state_event(started, attempt.original_start_event.event_id, attempt.original_start_event.ordinal,
            ActivityEventKind.STEP_STARTED, attempt.original_start_event.occurred_at)
        end = state_event(terminal, attempt.latest_transition_event.event_id, attempt.latest_transition_event.ordinal,
            ActivityEventKind.STEP_SUCCEEDED, attempt.latest_transition_event.occurred_at)
        changed_attempt = EffectAttemptRecord(terminal, start, end)
        changed_intent = EffectAttemptIntentRecord(identity, start, changed)
        changed_record = replace(outcome, outcome=changed_outcome, attempt=changed_attempt)
        _, preimage = effect_attempt_intent_store._require_record(changed_intent)
        predicate = sql.SQL(" WHERE run_id={} AND activity_id={} AND attempt={}").format(
            sql.Literal(identity.run_id.value), sql.Literal(identity.activity_id), sql.Literal(identity.attempt))
        statements = [sql.SQL("DELETE FROM {}{}").format(sql.Identifier(table), predicate) for table in
            ("cpk_effect_attempt_outcomes", "cpk_effect_attempts", "cpk_effect_attempt_intents")]
        for event in (start, end):
            statements.append(sql.SQL("UPDATE cpk_activity_events SET payload={} WHERE event_id={}").format(
                sql.Literal(Jsonb({"activity_id": event.activity_id, "evidence": event.evidence.descriptor(),
                    "failure": None, "recovery": None})), sql.Literal(event.event_id)))
        intent_values = (identity.run_id.value, identity.activity_id, identity.attempt, retained.workspace_id,
            retained.request_id, fingerprint, start.event_id, start.run_id, start.ordinal, preimage)
        for table, columns, values in (
                ("cpk_effect_attempt_intents", effect_attempt_intent_store._COLUMN_NAMES, intent_values),
                ("cpk_effect_attempts", effect_attempt_store._COLUMN_NAMES, effect_attempt_store._record_values(changed_attempt)),
                ("cpk_effect_attempt_outcomes", effect_outcome_store._COLUMN_NAMES,
                    effect_outcome_store._record_values(changed_record, retained.request_id))):
            statements.append(sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(sql.Identifier(table),
                sql.SQL(",").join(map(sql.Identifier, columns)), sql.SQL(",").join(map(sql.Literal, values))))
        # First prove this is coherent C1 input, not a broken fingerprint/FK.
        # This premise-only transaction is deliberately rolled back.
        with self.unit_of_work() as uow:
            for statement in statements:
                uow.stores.connection.execute(statement)
            guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            _, derived = uow.stores.execution._receiver_execution_material(claimed.request.identity, guard)
            evidence = uow.stores.execution.receiver_scope_evidence("workspace-a", derived.scopes, guard)
            self.assertEqual(evidence.state, "complete")
            self.assertEqual(classify_receiver_scope_evidence(evidence).disposition, "conflict")
            self.assertEqual(uow.stores.effect_attempt_intents.get(identity), changed_intent)
            self.assertEqual(uow.stores.effect_outcomes.get(identity, end.event_id), changed_record)
        before = self.acceptance_truth()
        body = "BEGIN " + "; ".join(statement.as_string(self.connection) for statement in statements) + "; RETURN NEW; END"
        self.connection.execute(sql.SQL("CREATE FUNCTION c3_change_material() RETURNS trigger LANGUAGE plpgsql AS {}").format(sql.Literal(body)))
        self.connection.execute("CREATE TRIGGER c3_change_material AFTER UPDATE ON cpk_graph_receiver_introductions "
            "FOR EACH ROW EXECUTE FUNCTION c3_change_material()")
        try:
            with self.assertRaises(CurrentGraphAdvancementConflict):
                self.advance(claimed, "material-drift")
            self.assertEqual(self.acceptance_truth(), before)
            self.assertIsNone(self.receiver_origin().first_accepted_action_id)
        finally:
            self.connection.execute("DROP TRIGGER c3_change_material ON cpk_graph_receiver_introductions")
            self.connection.execute("DROP FUNCTION c3_change_material()")
