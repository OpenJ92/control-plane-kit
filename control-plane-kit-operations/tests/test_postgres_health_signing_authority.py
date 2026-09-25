"""#1846 current authority laws over a committed historical preparation."""
from dataclasses import replace
import unittest
from unittest import mock

from control_plane_kit_core.secrets import SecretUseIntent
from control_plane_kit_operations.delegation_signing_keys import (
    DelegationSigningKeyNotFound, delegation_signing_key_registration_id_for,
)
from control_plane_kit_operations.node_control_signing_authority import _NodeControlSigningAuthorityStoreError
from control_plane_kit_operations.postgres import PostgresExecutionStore
from control_plane_kit_operations.postgres.activity_history import PostgresActivityHistoryStore
from control_plane_kit_operations.postgres.delegation_signing_key_store import DelegationSigningKeyStore
from control_plane_kit_operations.postgres.health_effect_preparation_store import HealthEffectPreparationStore
from control_plane_kit_operations.secret_providers import secret_use_correlation_for
from tests.health_effect_preparation_fixture import forged_copy, pair_for_request
from tests.health_effect_start_fixture import trusted_health_context
from tests.health_signing_authority_fixture import PostgresHealthSigningAuthorityFixture, timestamp
from tests.postgres_effect_attempt_store_fixture import PostgresEffectAttemptStoreFixture
from tests.test_node_control_signing_authority import PUBLIC_KEY_C


class PostgresHealthSigningAuthorityTests(PostgresHealthSigningAuthorityFixture, unittest.TestCase):
    def test_complete_exact_pair_repeated_reload_preserves_original_history(self):
        before = self.health_snapshot()
        with self.observed_time(timestamp(self.current_time)) as observations:
            first = self.reload()
            second = self.reload()
        self.assertEqual(len(observations), 2)
        self.assertEqual(first, second)
        self.assertEqual(first.preparation, self.preparation)
        for family in ("transit", "workload"):
            value = getattr(first, family)
            self.assertEqual(value.public_key, self.keys[family].public_key)
            self.assertEqual(value.resolution_grant, self.resolutions[family])
        for canary in ("BEGIN PUBLIC KEY", "secret://", "health-provider", "health-operator", "health-secrets"):
            self.assertNotIn(canary, repr(first) + repr(first.transit) + repr(first.workload))
        self.assertFalse(hasattr(first, "descriptor"))
        self.assert_history_unchanged(before)

    def test_current_actor_workspace_and_fence_cannot_borrow_saved_permission(self):
        command = self.reload_command()
        candidates = (
            replace(command, context=trusted_health_context(actor="other-operator")),
            replace(command, context=trusted_health_context(workspace="foreign-workspace")),
            replace(command, fence=replace(command.fence, generation=8)),
        )
        before = self.health_snapshot()
        for candidate in candidates:
            with self.subTest(candidate=repr(candidate)), self.assertRaises(self.reload_api.HealthSigningAuthorityUnavailable):
                self.reload(candidate)
            self.assert_history_unchanged(before)

    def test_progressed_attempt_refuses_while_historical_preparation_remains_readable(self):
        evolved = PostgresEffectAttemptStoreFixture.transition(self, self.started.start.attempt, "succeeded",
            event_id="health-succeeded", ordinal=self.original_event_ordinal + 1)
        with self.unit_of_work() as uow:
            uow.stores.execution.add_event(evolved.latest_transition_event)
            self.assertEqual(uow.stores.effect_attempts.compare_and_set(self.started.start.attempt, evolved), evolved)
            uow.commit()
        before = self.health_snapshot()
        with self.assertRaises(self.reload_api.HealthSigningAuthorityUnavailable):
            self.reload()
        self.assert_history_unchanged(before)

    def test_latest_run_and_running_status_are_independently_required(self):
        with self.unit_of_work() as uow:
            run = uow.stores.execution.get_run("run-a")
        # A valid typed different run isolates the latest-run owner boundary;
        # this is not a database insertion or proof of a new retry mechanism.
        other = replace(run, run_id="run-new")
        before = self.health_snapshot()
        with mock.patch.object(PostgresExecutionStore, "get_latest_run_for_request_for_update", return_value=other) as read:
            with self.assertRaises(self.reload_api.HealthSigningAuthorityUnavailable):
                self.reload()
        read.assert_called_once_with("request-a")
        self.assert_history_unchanged(before)
        self.connection.execute("UPDATE cpk_activity_runs SET status='compensating' WHERE run_id='run-a'")
        before = self.health_snapshot()
        with self.assertRaises(self.reload_api.HealthSigningAuthorityUnavailable):
            self.reload()
        self.assert_history_unchanged(before)

    def test_plan_status_approval_and_both_exact_projection_pins_are_current_checks(self):
        with self.unit_of_work() as uow:
            plan = uow.stores.activity_history.get_plan("plan-a")
        for field, value in (("base_realized_projection_id", plan.desired_realized_projection_id),
                ("desired_realized_projection_id", plan.base_realized_projection_id)):
            self.assertNotEqual(getattr(plan, field), value)
            # Composite FKs forbid equivalent SQL tampering; substitute only the
            # new current plan read, leaving historical reconstruction untouched.
            with mock.patch.object(PostgresActivityHistoryStore, "get_plan_for_share", return_value=replace(plan, **{field: value})) as read:
                with self.assertRaises(self.reload_api.HealthSigningAuthorityUnavailable):
                    self.reload()
            read.assert_called_once_with("plan-a")
        self.connection.execute("UPDATE cpk_activity_plans SET status='superseded' WHERE plan_id='plan-a'")
        before = self.health_snapshot()
        with self.assertRaises(self.reload_api.HealthSigningAuthorityUnavailable):
            self.reload()
        self.assert_history_unchanged(before)
        self.connection.execute("UPDATE cpk_activity_plans SET status='planned' WHERE plan_id='plan-a'")
        self.connection.execute("UPDATE cpk_approval_decisions SET decision='rejected' WHERE decision_id='approval-decision-a'")
        before = self.health_snapshot()
        with self.assertRaises(self.reload_api.HealthSigningAuthorityUnavailable):
            self.reload()
        self.assert_history_unchanged(before)

    def test_each_exact_key_and_reference_chain_can_independently_deny(self):
        for family in ("transit", "workload"):
            key = self.keys[family]
            original = DelegationSigningKeyStore.require_unambiguous_active
            other = self.keys["workload" if family == "transit" else "transit"]
            public = replace(key.public_key, key_id=key.key_id + "-replacement", public_key_pem=PUBLIC_KEY_C)
            replacement = replace(key, public_key=public, registration_id=delegation_signing_key_registration_id_for(
                workspace_id=key.workspace_id, purpose=key.purpose, issuer=key.issuer,
                public_key=public, private_key_reference=key.private_key_reference))
            self.assertIs(replacement.purpose, key.purpose)
            self.assertIs(replacement.status, key.status)
            self.assertNotEqual(replacement.registration_id, key.registration_id)
            for candidate in (other, replacement):
                reads = []
                def substituted(store, workspace, purpose):
                    actual = original(store, workspace, purpose)
                    reads.append(purpose)
                    return candidate if purpose is key.purpose else actual
                with self.observed_time(timestamp(self.current_time)):
                    before = self.health_snapshot()
                    with mock.patch.object(DelegationSigningKeyStore, "require_unambiguous_active", substituted):
                        with self.assertRaises(self.reload_api.HealthSigningAuthorityUnavailable):
                            self.reload()
                    self.assertIn(key.purpose, reads)
                    self.assert_history_unchanged(before)
            reference = self.references[family]
            self.connection.execute("UPDATE cpk_secret_references SET status='revoked', revoked_by='operator-a', revoked_at='2030-01-01T00:00:00Z' WHERE registration_id=%s", (reference.registration_id,))
            before = self.health_snapshot()
            with self.assertRaises(_NodeControlSigningAuthorityStoreError):
                self.reload()
            self.assert_history_unchanged(before)
            self.connection.execute("UPDATE cpk_secret_references SET status='active', revoked_by=NULL, revoked_at=NULL WHERE registration_id=%s", (reference.registration_id,))
        self.connection.execute("UPDATE cpk_secret_providers SET status='revoked', revoked_by='operator-a', revoked_at='2030-01-01T00:00:00Z' WHERE registration_id=%s", (self.provider.registration_id,))
        before = self.health_snapshot()
        with self.assertRaises(_NodeControlSigningAuthorityStoreError):
            self.reload()
        self.assert_history_unchanged(before)
        self.connection.execute("UPDATE cpk_secret_providers SET status='active', revoked_by=NULL, revoked_at=NULL WHERE registration_id=%s", (self.provider.registration_id,))
        self.connection.execute("UPDATE cpk_delegation_signing_keys SET status='revoked', revoked_by='operator-a', revoked_at='2030-01-01T00:00:00Z'")
        before = self.health_snapshot()
        with self.assertRaises(DelegationSigningKeyNotFound):
            self.reload()
        self.assert_history_unchanged(before)

    def test_foreign_approval_id_and_self_consistent_saved_target_cannot_authorize_themselves(self):
        with self.unit_of_work() as uow:
            decision = uow.stores.activity_history.approval_decision_for_request("approval-request-a")
        before = self.health_snapshot()
        with mock.patch.object(PostgresActivityHistoryStore, "approval_decision_for_request",
                return_value=replace(decision, decision_id="foreign-decision")) as read:
            with self.assertRaises(self.reload_api.HealthSigningAuthorityUnavailable):
                self.reload()
        read.assert_called_once_with("approval-request-a")
        self.assert_history_unchanged(before)
        target = self.preparation.request.target
        foreign_request = replace(self.preparation.request,
            target=replace(target, node_id=replace(target.node_id, value="foreign-node")))
        candidates = (
            pair_for_request(self.preparation, foreign_request),
            replace(self.preparation, transit_grant=replace(self.preparation.transit_grant,
                gateway_node_id=replace(self.preparation.transit_grant.gateway_node_id, value="foreign-gateway"))),
        )
        for candidate in candidates:
            # Typed internally valid retained value, substituted after the store
            # boundary to isolate independent current target reconstruction.
            with mock.patch.object(HealthEffectPreparationStore, "get", return_value=candidate) as read:
                with self.assertRaises(self.reload_api.HealthSigningAuthorityUnavailable):
                    self.reload()
            read.assert_called_once_with(self.preparation.identity)
            self.assert_history_unchanged(before)

    def test_saved_shared_interval_has_exact_edges_and_renewed_lease_does_not_extend_it(self):
        transit, workload = self.preparation.transit_grant, self.preparation.workload_grant
        self.assertEqual((transit.issued_at, transit.not_before, transit.expires_at),
            (workload.issued_at, workload.not_before, workload.expires_at))
        edges = ((transit.not_before - 1, 999999, False), (transit.not_before, 0, True),
            (transit.expires_at - 1, 999999, True), (transit.expires_at, 0, False))
        for seconds, microseconds, accepted in edges:
            with self.subTest(seconds=seconds, microseconds=microseconds):
                with self.observed_time(timestamp(seconds, microseconds), expires_at="2030-01-01T01:00:00Z") as observations:
                    before = self.health_snapshot()
                    if accepted:
                        self.assertEqual(self.reload().preparation, self.preparation)
                    else:
                        with self.assertRaises(self.reload_api.HealthSigningAuthorityUnavailable):
                            self.reload()
                self.assertEqual(len(observations), 1)
                self.assert_history_unchanged(before)
        with self.observed_time(timestamp(self.current_time), expires_at=timestamp(self.current_time)) as observations:
            with self.assertRaises(self.reload_api.HealthSigningAuthorityUnavailable):
                self.reload()
        self.assertEqual(len(observations), 1)

    def test_both_core_verifiers_receive_independent_context_and_can_refuse(self):
        names = ("verify_gateway_node_health_read_transit_grant", "verify_workload_node_health_read_grant")
        # Instrument comparison boundaries on valid equal-interval retained data.
        # Force refusal by changing only the independently supplied verifier time,
        # not by constructing an impossible differently timed saved pair.
        for name in names:
            original = getattr(self.reload_api, name)
            calls = []
            def verify(grant, request, **context):
                calls.append(context)
                self.assertEqual(context["now"], self.current_time)
                self.assertEqual(context["expected_target"], self.preparation.request.target)
                self.assertEqual(context["expected_runtime_id"], self.preparation.request.runtime_id)
                self.assertEqual(context["expected_declaration"].identity(), self.preparation.request.declaration_identity)
                return original(grant, request, **(context | {"now": grant.expires_at}))
            with self.observed_time(timestamp(self.current_time)):
                with mock.patch.object(self.reload_api, name, verify):
                    with self.assertRaises(self.reload_api.HealthSigningAuthorityUnavailable):
                        self.reload()
            self.assertEqual(len(calls), 1)

    def test_pair_rejects_family_substitution_and_forged_context_material(self):
        with self.observed_time(timestamp(self.current_time)):
            pair = self.reload()
        for family, other in ((pair.transit, pair.workload), (pair.workload, pair.transit)):
            constructor = type(family)
            self.assertEqual(constructor(family.public_key, family.resolution_grant), family)
            for key, grant in (
                    (forged_copy(family.public_key, subclass=True), family.resolution_grant),
                    (family.public_key, forged_copy(family.resolution_grant, subclass=True)),
                    (family.public_key, replace(family.resolution_grant, intent=other.resolution_grant.intent))):
                with self.assertRaises(self.reload_api.HealthSigningAuthorityError) as caught:
                    constructor(key, grant)
                self.assert_safe(caught.exception, "secret://", "BEGIN PUBLIC KEY")
        for field, value in (("actor_subject", "coherent-foreign-actor"), ("session_id", "coherent-foreign-session")):
            changed = {}
            for name in ("transit", "workload"):
                family = getattr(pair, name)
                resolution = replace(family.resolution_grant, **{field: value})
                semantics = {key: getattr(resolution, key) for key in (
                    "workspace_id", "reference", "intent", "actor_subject", "operation_id",
                    "session_id", "run_id", "activity_id", "effect_id", "probe_id")}
                resolution = replace(resolution, correlation_id=secret_use_correlation_for(**semantics))
                self.assertNotEqual(resolution.correlation_id, family.resolution_grant.correlation_id)
                self.assertEqual(resolution.authorization_id, family.resolution_grant.authorization_id)
                self.assertEqual(resolution.intent_fingerprint, family.resolution_grant.intent_fingerprint)
                changed[name] = type(family)(family.public_key, resolution)
            # Shared context and freshly coherent correlations cannot relabel
            # the semantics identified by both retained authorization digests.
            with self.assertRaises(self.reload_api.HealthSigningAuthorityError) as caught:
                replace(pair, **changed)
            self.assert_safe(caught.exception, value, "secret://")
        for field, candidate in (("transit", pair.workload), ("workload", pair.transit),
                ("transit", forged_copy(pair.transit, subclass=True)),
                ("workload", forged_copy(pair.workload, resolution_grant=replace(pair.workload.resolution_grant, actor_subject="foreign-actor"))),
                ("transit", forged_copy(pair.transit, resolution_grant=replace(pair.transit.resolution_grant, session_id="foreign-session"))),
                ("workload", forged_copy(pair.workload, resolution_grant=replace(pair.workload.resolution_grant,
                    intent=SecretUseIntent.WORKLOAD_NODE_CONTROL_SIGNING_KEY))),
                ("workload", forged_copy(pair.workload, public_key=pair.transit.public_key))):
            with self.subTest(field=field), self.assertRaises(self.reload_api.HealthSigningAuthorityError) as caught:
                replace(pair, **{field: candidate})
            self.assert_safe(caught.exception, "foreign-actor", "foreign-session", "secret://")
