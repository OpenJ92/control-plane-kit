"""#1852 approved first-start atomicity and retained-only replay laws."""
from dataclasses import replace
import unittest
from unittest import mock

from control_plane_kit_operations.effect_attempt_start import EffectAttemptStartDenied, EffectAttemptStartError, ExistingAttempt, NewlyStarted
from control_plane_kit_operations.health_effect_preparations import HealthEffectPreparationError, health_effect_attempt_wire_id
from control_plane_kit_operations.postgres import PostgresExecutionStore
from control_plane_kit_operations.postgres.effect_attempt_store import EffectAttemptStore
from control_plane_kit_operations.postgres.effect_attempt_intent_store import EffectAttemptIntentStore
from control_plane_kit_operations.postgres.health_effect_preparation_store import HealthEffectPreparationStore
from control_plane_kit_operations.postgres.secret_provider_store import SecretUseAuthorizationStore
from control_plane_kit_operations.secret_providers import authorized_secret_use_for
from tests.execution_lease_recovery_fixture import Sequence
from tests.health_effect_start_fixture import trusted_health_context
from tests.postgres_effect_attempt_store_fixture import PostgresEffectAttemptStoreFixture
from tests.postgres_health_effect_start_fixture import PostgresHealthEffectStartFixture


class PostgresHealthEffectStartFirstReplayTests(PostgresHealthEffectStartFixture, unittest.TestCase):
    def test_ungated_fixture_retains_real_approved_ready_plan_without_partial_evidence(self):
        from control_plane_kit_operations.delegation_signing_keys import RegisteredDelegationSigningKeyStatus
        from control_plane_kit_core.planning import derive_schedule, project_activity_journal
        from control_plane_kit_core.policies import ApprovalPolicy
        from control_plane_kit_operations.activity_journal import activity_journal_events
        with self.unit_of_work() as uow:
            plan = uow.stores.activity_history.get_plan("plan-a")
            request = uow.stores.execution.get_request("request-a")
            approval = uow.stores.activity_history.get_approval_request(request.approval_request_id)
            decision = uow.stores.activity_history.approval_decision_for_request(approval.request_id)
            journal = project_activity_journal(plan.plan,
                activity_journal_events(uow.stores.execution.events_for_run("run-a")))
            self.assertIn(self.health_activity.activity_id,
                tuple(item.activity_id for item in derive_schedule(plan.plan, journal.state).ready))
            requirement = ApprovalPolicy().requirement_for(plan.plan)
            self.assertEqual((approval.required_scope, approval.max_risk, approval.destructive),
                (requirement.required_scope, requirement.max_risk, requirement.destructive))
            self.assertEqual(decision.decision_id, request.approval_decision_id)
            self.assertEqual(self.start_value.intent.operation, self.health_activity.operation)
            for key in self.keys.values():
                self.assertIs(key.status, RegisteredDelegationSigningKeyStatus.ACTIVE)
                self.assertEqual(uow.stores.delegation_signing_keys.require_unambiguous_active(
                    "workspace-a", key.purpose), key)
        self.assertEqual(self.health_counts(), (0, 0, 0, 0))
        self.assertNotEqual(self.keys["transit"].private_key_reference, self.keys["workload"].private_key_reference)

    def test_first_start_commits_exact_pair_actor_pins_and_original_history(self):
        with self.observed_time("2030-01-01T00:00:00Z") as observations:
            result, ids = self.execute_health()
        preparation = result.preparation
        self.assertIs(type(result.start), NewlyStarted)
        self.assertEqual(ids.calls, ["health-original", "health-request", "health-transit-jti", "health-workload-jti"])
        self.assertEqual(len(observations), 1)
        self.assertEqual(self.health_counts(), (1, 1, 2, 1))
        event = result.start.attempt.original_start_event
        self.assertEqual((event.event_id, event.ordinal, event.occurred_at),
            ("health-original", self.original_event_ordinal, "2030-01-01T00:00:00Z"))
        self.assertEqual(preparation.request.target.graph_revision.value, "health-desired")
        self.assertEqual(preparation.base_realized_projection_id, self.projections["health-base"].projection_id)
        self.assertEqual(preparation.desired_realized_projection_id, self.projections["health-desired"].projection_id)
        self.assertEqual(preparation.request.runtime_id.value, self.health_activity.operation.target.runtime_id)
        self.assertEqual(preparation.transit_grant.attempt_id, health_effect_attempt_wire_id(self.start_value.transition.identity))
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.effect_attempts.get(preparation.identity), result.start.attempt)
            intent = uow.stores.effect_attempt_intents.get(preparation.identity)
            self.assertEqual(intent.intent, self.start_value.intent)
            self.assertEqual(intent.original_start_event, event)
            self.assertEqual(uow.stores.health_effect_preparations.get(preparation.identity), preparation)
            for family in ("transit", "workload"):
                use = uow.stores.secret_use_authorizations.get("workspace-a", getattr(preparation, family + "_authorization_id"))
                expected = authorized_secret_use_for(self.expected_use_command(family, requested_at=event.occurred_at),
                    reference=self.references[family], provider=self.provider)
                self.assertEqual(use, expected)
                self.assertEqual(use.actor_subject, "health-operator")
                self.assertNotIn(use.actor_subject, ("operator-a", "manager-a", "worker-a"))
                self.assertEqual(getattr(preparation, family + "_key_registration_id"), self.keys[family].registration_id)

    def test_event_intent_attempt_two_uses_preparation_then_single_commit_request(self):
        from contextlib import ExitStack
        calls = []
        boundaries = ((PostgresExecutionStore, "add_event", "event"),
            (EffectAttemptIntentStore, "insert", "intent"), (EffectAttemptStore, "insert_absent", "attempt"),
            (SecretUseAuthorizationStore, "add", "use"), (HealthEffectPreparationStore, "insert_absent", "preparation"))
        def unit_of_work():
            uow = self.unit_of_work()
            commit = uow.commit
            def request_commit():
                calls.append("commit-request")
                return commit()
            uow.commit = request_commit
            return uow
        with ExitStack() as stack:
            for owner, method, label in boundaries:
                original = getattr(owner, method)
                def write(store, value, original=original, label=label):
                    calls.append(label if label != "use" else value.intent.value)
                    return original(store, value)
                stack.enter_context(mock.patch.object(owner, method, write))
            result, _ = self.execute_health(unit_of_work=unit_of_work)
        self.assertIs(type(result.start), NewlyStarted)
        self.assertEqual(calls, ["event", "intent", "attempt", *[intent.value for intent in self.family_intents], "preparation", "commit-request"])

    def test_generic_fresh_health_cannot_bypass_required_preparation(self):
        before = self.health_snapshot()
        service, ids = self.health_service()
        with self.forbid_fresh_health():
            with self.assertRaises(EffectAttemptStartDenied):
                service.execute(self.start_value)
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.health_snapshot(), before)

    def test_expired_replay_keeps_original_preparation_without_current_key_authority(self):
        result, _ = self.execute_health()
        self.expire_claim()
        # Retained keys remain readable, but neither family is current authority.
        self.connection.execute("UPDATE cpk_delegation_signing_keys SET status='revoked', revoked_by='operator-a', revoked_at='2030-01-01T00:10:00Z'")
        before = self.health_snapshot()
        ids = Sequence("must-not-allocate")
        with self.forbid_fresh_health():
            replay, _ = self.execute_health(ids=ids)
            generic, _ids = self.health_service(ids=ids)
            generic_replay = generic.execute(self.start_value)
        self.assertIs(type(replay.start), ExistingAttempt)
        self.assertEqual(replay.preparation, result.preparation)
        self.assertEqual(replay.start.attempt, result.start.attempt)
        self.assertEqual(generic_replay, ExistingAttempt(result.start.attempt))
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.health_snapshot(), before)

    def test_evolved_attempt_replay_retains_exact_original_event_and_preparation(self):
        result, _ = self.execute_health()
        evolved = PostgresEffectAttemptStoreFixture.transition(self, result.start.attempt, "succeeded",
            event_id="health-succeeded", ordinal=self.original_event_ordinal + 1)
        with self.unit_of_work() as uow:
            uow.stores.execution.add_event(evolved.latest_transition_event)
            self.assertEqual(uow.stores.effect_attempts.compare_and_set(result.start.attempt, evolved), evolved)
            uow.commit()
        before = self.health_snapshot()
        with self.forbid_fresh_health():
            replay, ids = self.execute_health(ids=Sequence("never"))
        self.assertEqual(replay.start, ExistingAttempt(evolved))
        self.assertEqual(replay.preparation, result.preparation)
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.health_snapshot(), before)

    def test_other_actor_cannot_relabel_dedicated_retained_preparation(self):
        self.execute_health()
        before = self.health_snapshot()
        with self.forbid_fresh_health():
            with self.assertRaises(EffectAttemptStartError):
                self.execute_health(command=self.start_health_command(context=trusted_health_context(actor="other-actor")))
        self.assertEqual(self.health_snapshot(), before)

    def test_missing_or_corrupt_required_preparation_refuses_both_replay_entrances(self):
        for condition in ("missing", "corrupt"):
            with self.subTest(condition=condition):
                self.reset_health()
                self.execute_health()
                if condition == "missing":
                    self.connection.execute("DELETE FROM cpk_health_effect_preparations")
                else:
                    self.connection.execute("UPDATE cpk_health_effect_preparations SET preimage=%s", (b"{}",))
                before = self.health_snapshot()
                service, ids = self.health_service()
                command = self.start_health_command()
                with self.forbid_fresh_health():
                    for execute in (lambda: service.execute(self.start_value), lambda: service.execute_health(command)):
                        with self.assertRaises((EffectAttemptStartError, HealthEffectPreparationError, KeyError)):
                            execute()
                self.assertEqual(ids.calls, [])
                self.assertEqual(self.health_snapshot(), before)

    def test_preexisting_either_family_correlation_is_not_adopted_even_at_equal_time(self):
        for family in ("transit", "workload"):
            for retained_time in ("2030-01-01T00:00:00Z", "2029-12-31T23:59:59Z"):
                with self.subTest(family=family, retained_time=retained_time):
                    self.reset_health()
                    retained = self.seed_existing_use(family, requested_at=retained_time)
                    with self.observed_time("2030-01-01T00:00:00Z"):
                        before = self.health_snapshot()
                        with mock.patch.object(SecretUseAuthorizationStore, "add",
                            side_effect=AssertionError("either-family conflict must precede both authorizations")):
                            with self.assertRaises(EffectAttemptStartError):
                                self.execute_health()
                    self.assertEqual(self.health_snapshot(), before)
                    with self.unit_of_work() as uow:
                        self.assertEqual(uow.stores.secret_use_authorizations.get("workspace-a", retained.authorization_id), retained)
                    self.assertEqual(self.health_counts(), (0, 0, 1, 0))

    def test_equal_content_keeps_the_approved_base_side_authored_revision(self):
        from control_plane_kit_core.planning import PlanGraphSide
        self.health_start_api()
        # This explicitly approved typed plan selects BASE. Equal content may
        # not silently turn that approved side into DESIRED or current lineage.
        self.reset_health(side=PlanGraphSide.BASE_GRAPH)
        self.assertEqual(self.projections["health-base"].graph_descriptor,
            self.projections["health-desired"].graph_descriptor)
        result, _ = self.execute_health()
        self.assertEqual(result.preparation.request.target.graph_revision.value, "health-base")
        self.assertNotEqual(result.preparation.base_realized_projection_id,
            result.preparation.desired_realized_projection_id)
