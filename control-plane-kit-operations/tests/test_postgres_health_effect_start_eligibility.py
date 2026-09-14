"""#1852 new-law approval, pin, current reference and interval admission."""
from dataclasses import replace
import unittest
from unittest import mock

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.effect_attempt_start import EffectAttemptStartError
from control_plane_kit_operations.delegation_signing_keys import DelegationSigningKeyNotFound
from control_plane_kit_operations.health_effect_preparations import HealthEffectPreparationError
from control_plane_kit_operations.postgres.delegation_signing_key_store import DelegationSigningKeyStore
from control_plane_kit_operations.postgres.activity_history import PostgresActivityHistoryStore
from control_plane_kit_operations.secret_providers import SecretProviderRegistrationError
from tests.execution_lease_recovery_fixture import Sequence
from tests.postgres_health_effect_start_fixture import PostgresHealthEffectStartFixture


class PostgresHealthEffectStartEligibilityTests(PostgresHealthEffectStartFixture, unittest.TestCase):
    def test_actual_approval_decision_and_risk_must_match_the_admitted_plan(self):
        self.health_start_api()
        cases = (
            ("rejected", "UPDATE cpk_approval_decisions SET decision='rejected'", ()),
            ("scope", "UPDATE cpk_approval_decisions SET scope=%s", (PolicyScope.PLAN_EXECUTE.value,)),
            ("risk", "UPDATE cpk_approval_requests SET destructive=NOT destructive", ()),
            ("superseded", "UPDATE cpk_activity_plans SET status='superseded'", ()),
        )
        for label, statement, parameters in cases:
            with self.subTest(case=label):
                self.reset_health()
                if label == "scope":
                    with self.unit_of_work() as uow:
                        retained = uow.stores.activity_history.approval_decision_for_request("approval-request-a")
                    self.assertNotEqual(retained.scope.value, parameters[0])
                self.connection.execute(statement, parameters)
                before = self.health_snapshot()
                ids = Sequence("must-not-allocate")
                with self.assertRaises(EffectAttemptStartError):
                    self.execute_health(ids=ids)
                self.assertEqual(ids.calls, [])
                self.assertEqual(self.health_snapshot(), before)

    def test_foreign_decision_id_is_not_replaced_by_an_available_approval(self):
        self.health_start_api()
        with self.unit_of_work() as uow:
            decision = uow.stores.activity_history.approval_decision_for_request("approval-request-a")
        # The database's composite approval FK stays lawful. Exercise the
        # service's typed owner-read identity check, not a forbidden SQL update.
        foreign = replace(decision, decision_id="other-decision")
        before = self.health_snapshot()
        ids = Sequence("must-not-allocate")
        with mock.patch.object(PostgresActivityHistoryStore, "approval_decision_for_request", return_value=foreign) as read_decision:
            with self.assertRaises(EffectAttemptStartError):
                self.execute_health(ids=ids)
        read_decision.assert_called_once_with("approval-request-a")
        self.assertEqual(ids.calls, [])
        self.assertEqual(self.health_snapshot(), before)

    def test_both_explicit_projection_ids_and_selected_authored_side_are_required(self):
        self.health_start_api()
        for column in ("base_realized_projection_id", "desired_realized_projection_id"):
            for replacement in (None, "opposite"):
                with self.subTest(column=column, replacement=replacement):
                    self.reset_health()
                    value = replacement
                    if replacement == "opposite":
                        name = "health-desired" if column.startswith("base") else "health-base"
                        value = self.projections[name].projection_id
                    self.connection.execute("UPDATE cpk_activity_plans SET " + column + "=%s", (value,))
                    before = self.health_snapshot()
                    ids = Sequence("must-not-allocate")
                    with self.assertRaises((EffectAttemptStartError, HealthEffectPreparationError)):
                        self.execute_health(ids=ids)
                    self.assertEqual(ids.calls, [])
                    self.assertEqual(self.health_snapshot(), before)

    def test_changed_operation_pin_cannot_gain_authority_from_self_matching_intent(self):
        from control_plane_kit_core.planning import ActivityPlan, PlanGraphSide
        from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
        from control_plane_kit_operations.plan_derivation import PlanDerivationProfile, encode_stored_activity_plan
        from psycopg.types.json import Jsonb
        self.health_start_api()
        for changes in ({"graph_digest": "f" * 64}, {"relation_digest": "e" * 64}, {"graph_side": PlanGraphSide.BASE_GRAPH}):
            with self.subTest(changes=changes):
                self.reset_health()
                operation = replace(self.health_activity.operation, target=replace(self.health_activity.operation.target, **changes))
                plan = ActivityPlan(tuple(replace(item, operation=operation)
                    if item.activity_id == self.health_activity.activity_id else item for item in self.health_plan.activities))
                self.connection.execute("UPDATE cpk_activity_plans SET payload=%s", (Jsonb(encode_stored_activity_plan(plan,
                    profile=PlanDerivationProfile.MANAGEMENT_GRAPH_PAIR_V1)),))
                intent = replace(self.start_value.intent, operation=operation)
                start = replace(self.start_value, intent=intent, transition=replace(self.start_value.transition,
                    request_fingerprint=runtime_effect_intent_fingerprint(intent)))
                before = self.health_snapshot()
                with self.assertRaises((EffectAttemptStartError, HealthEffectPreparationError)):
                    self.execute_health(command=self.start_health_command(start=start))
                self.assertEqual(self.health_snapshot(), before)

    def test_each_current_key_must_match_its_exact_family_and_material_identity(self):
        self.health_start_api()
        original = DelegationSigningKeyStore.require_unambiguous_active
        for family in ("transit", "workload"):
            for mutation in ("purpose", "registration", "missing"):
                with self.subTest(family=family, mutation=mutation):
                    self.reset_health()
                    selected = self.keys[family]
                    def choose(store, workspace, purpose):
                        value = original(store, workspace, purpose)
                        if purpose is not selected.purpose:
                            return value
                        if mutation == "missing":
                            raise DelegationSigningKeyNotFound("active delegation signing key was not found")
                        if mutation == "purpose":
                            return replace(value, purpose=DelegationKeyPurpose.GATEWAY_PROBE)
                        return replace(value, registration_id="dkey_" + "f" * 64)
                    before = self.health_snapshot()
                    ids = Sequence("must-not-allocate")
                    with mock.patch.object(DelegationSigningKeyStore, "require_unambiguous_active", choose):
                        with self.assertRaises((EffectAttemptStartError, DelegationSigningKeyNotFound)):
                            self.execute_health(ids=ids)
                    self.assertEqual(ids.calls, [])
                    self.assertEqual(self.health_snapshot(), before)

    def test_each_reference_and_provider_must_still_admit_its_exact_intent(self):
        self.health_start_api()
        for family in ("transit", "workload"):
            with self.subTest(family=family):
                self.reset_health()
                other_intent = self.family_intents[1 if family == "transit" else 0]
                self.connection.execute("UPDATE cpk_secret_references SET allowed_intents=%s WHERE registration_id=%s",
                    ([other_intent.value], self.references[family].registration_id))
                before = self.health_snapshot()
                with self.assertRaises((EffectAttemptStartError, SecretProviderRegistrationError)):
                    self.execute_health()
                self.assertEqual(self.health_snapshot(), before)
        self.reset_health()
        self.connection.execute("UPDATE cpk_secret_providers SET status='revoked', revoked_by='operator-a', revoked_at='2030-01-01T00:00:00Z'")
        before = self.health_snapshot()
        with self.assertRaises((EffectAttemptStartError, SecretProviderRegistrationError)):
            self.execute_health()
        self.assertEqual(self.health_snapshot(), before)

    def test_interval_uses_integer_database_observation_and_never_extends_lease(self):
        self.health_start_api()
        epoch = 1_893_456_000  # 2030-01-01T00:00:00Z
        cases = (
            ("2030-01-01T00:00:00Z", "2030-01-01T00:10:00Z", (epoch, epoch + 300)),
            ("2030-01-01T00:00:00.5Z", "2030-01-01T00:10:00Z", (epoch + 1, epoch + 300)),
            ("2030-01-01T00:00:00.5Z", "2030-01-01T00:00:03.9Z", (epoch + 1, epoch + 3)),
            ("2030-01-01T00:00:00.5Z", "2030-01-01T00:00:01.9Z", None),
            ("2030-01-01T00:00:00Z", "2030-01-01T00:00:00Z", None),
        )
        for observed, expiry, expected in cases:
            with self.subTest(observed=observed, expiry=expiry):
                self.reset_health()
                ids = Sequence("health-original", "health-request", "health-transit-jti", "health-workload-jti")
                with self.observed_time(observed, expires_at=expiry) as observations:
                    before = self.health_snapshot()
                    if expected is None:
                        with self.assertRaises(EffectAttemptStartError):
                            self.execute_health(ids=ids)
                        self.assertEqual(ids.calls, [])
                        self.assertEqual(self.health_snapshot(), before)
                    else:
                        result, _ = self.execute_health(ids=ids)
                        for grant in (result.preparation.transit_grant, result.preparation.workload_grant):
                            self.assertEqual((grant.issued_at, grant.not_before, grant.expires_at),
                                (expected[0], expected[0], expected[1]))
                        self.assertEqual(result.start.attempt.original_start_event.occurred_at, observed)
                    self.assertEqual(len(observations), 1)

    def test_pair_distinctness_is_checked_even_with_coherent_individual_key_identities(self):
        from control_plane_kit_operations.delegation_signing_keys import delegation_signing_key_registration_id_for
        self.health_start_api()
        original = DelegationSigningKeyStore.require_unambiguous_active
        for shared_material in ("public-key", "private-reference"):
            with self.subTest(shared_material=shared_material):
                self.reset_health()
                transit, workload = self.keys["transit"], self.keys["workload"]
                public_key = (replace(workload.public_key, public_key_pem=transit.public_key.public_key_pem)
                    if shared_material == "public-key" else workload.public_key)
                private_reference = (transit.private_key_reference if shared_material == "private-reference"
                    else workload.private_key_reference)
                identity = delegation_signing_key_registration_id_for(workspace_id=workload.workspace_id,
                    purpose=workload.purpose, issuer=workload.issuer, public_key=public_key,
                    private_key_reference=private_reference)
                candidate = replace(workload, public_key=public_key, private_key_reference=private_reference,
                    registration_id=identity)
                def choose(store, workspace, purpose):
                    value = original(store, workspace, purpose)
                    return candidate if purpose is workload.purpose else value
                before = self.health_snapshot()
                ids = Sequence("must-not-allocate")
                with mock.patch.object(DelegationSigningKeyStore, "require_unambiguous_active", choose):
                    with self.assertRaises(EffectAttemptStartError):
                        self.execute_health(ids=ids)
                self.assertEqual(ids.calls, [])
                self.assertEqual(self.health_snapshot(), before)
