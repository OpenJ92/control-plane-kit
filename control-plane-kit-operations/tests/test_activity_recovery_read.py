"""#2033 proposal laws against real retained owner truth; no provider fixture."""
from dataclasses import replace
from importlib import import_module
from importlib.util import find_spec
import json
import unittest
from unittest import mock

from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.postgres.configuration_preparation_store import ConfigurationPreparationStore
from tests.configuration_preparation_fixture import ConfigurationPreparationFixture
from tests.draft_catalogue_fixture import principal
from tests.receiver_authoring_context_fixture import ReceiverAuthoringContextFixture
from tests.test_postgres_configuration_evolved_replay import PostgresConfigurationEvolvedReplayTests


def recovery_api(test):
    name = "control_plane_kit_operations.read_services.activity_recovery"
    test.assertIsNotNone(find_spec(name), "#2033 missing bounded activity recovery inspection")
    return import_module(name)


class ActivityRecoveryQueryTests(unittest.TestCase):
    def query(self, api):
        return api.ActivityRecoveryQuery("workspace-a", EffectAttemptIdentity(RunId("run-a"), "start-api", 1),
            "a" * 64, "b" * 64)

    def test_query_is_strict_bounded_and_detached(self):
        api = recovery_api(self)
        query = self.query(api)
        descriptor = query.descriptor()
        self.assertEqual(api.ActivityRecoveryQuery.from_mapping(descriptor), query)
        descriptor["identity"]["attempt"] = 7
        self.assertEqual(query.identity.attempt, 1)
        for changes in ({"workspace_id": "private-marker\n"}, {"workspace_id": "x" * 2049},
                        {"expected_request_fingerprint": "A" * 64},
                        {"expected_outcome_fingerprint": "private-marker"}, {"extra": True}):
            with self.subTest(changes=tuple(changes)):
                with self.assertRaises(api.ActivityRecoveryError) as caught:
                    api.ActivityRecoveryQuery.from_mapping({**query.descriptor(), **changes})
                self.assertEqual(caught.exception.category, "malformed")
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
                self.assertNotIn("private-marker", str(caught.exception))

    def test_authority_precedes_factory_and_factory_errors_are_sanitized(self):
        api = recovery_api(self)
        factory = mock.Mock(side_effect=RuntimeError("private-marker SELECT token"))
        service = api.ActivityRecoveryReadService(factory)
        query = self.query(api)
        for context in (principal(scopes=()).command_context("workspace-a"),
                        principal("other").command_context("other")):
            with self.assertRaises(api.ActivityRecoveryError) as caught:
                service.read(query, context=context)
            self.assertEqual(caught.exception.category, "forbidden")
        factory.assert_not_called()
        with self.assertRaises(api.ActivityRecoveryError) as caught:
            service.read(query, context=principal(scopes=(PolicyScope.INSTANCE_WORKSPACE_READ,))
                         .command_context("workspace-a"))
        self.assertEqual(caught.exception.category, "unavailable")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn("private-marker", repr(caught.exception))


class ActivityRecoveryReadTests(ConfigurationPreparationFixture, unittest.TestCase):
    fold_direct_outcome = PostgresConfigurationEvolvedReplayTests.fold_direct_outcome
    measured_factory = ReceiverAuthoringContextFixture.measured_factory

    def assert_measured_snapshot(self, observed):
        self.assertEqual(len(observed), 1)
        observer = observed[0]
        self.assertEqual(observer.observation_failures, [])
        self.assertTrue(observer.modes)
        self.assertEqual(set(observer.modes), {("repeatable read", "on")})
        self.assertTrue(observer.connection.closed)
        self.assertEqual(observer.closes, 1)
        self.assertLessEqual(observer.data_reads, 1024)
        self.assertLessEqual(observer.rows, 4096)
        self.assertLessEqual(observer.value_bytes, 8_388_608)
        self.assertLessEqual(observer.largest_cell, 1_048_576)
        statements = " ".join(observer.statements)
        for forbidden in ("pg_advisory", "for update", "for share", "insert into", "update cpk_",
                          "delete from", "cpk_delegation_signing_keys"):
            self.assertNotIn(forbidden, statements)

    def seed_uncertain(self):
        self.api = recovery_api(self)
        started = self.start_service("recovery-original-start").execute(self.configuration_command())
        self.original, _ = self.fold_direct_outcome(started, status="uncertain")
        self.query = self.api.ActivityRecoveryQuery("workspace-a", self.original.state.identity,
            self.original.state.request_fingerprint, self.original.state.outcome_fingerprint)

    def read(self, query=None, factory=None):
        return self.api.ActivityRecoveryReadService(factory or self.unit_of_work).read(
            query or self.query, context=principal(scopes=(PolicyScope.INSTANCE_WORKSPACE_READ,))
            .command_context("workspace-a"))

    def test_reconstructed_reader_reports_original_uncertainty_without_effect_or_write(self):
        self.seed_uncertain()
        before = self.complete_start_snapshot(), self.protection_rows()
        factory, measured = self.measured_factory()
        with mock.patch.object(ConfigurationPreparationStore, "_prepare", side_effect=AssertionError("prepared")), \
                mock.patch.object(ConfigurationPreparationStore, "_proposal", side_effect=AssertionError("proposed")), \
                mock.patch.object(PostgresUnitOfWork, "commit", side_effect=AssertionError("committed")):
            result = self.read(factory=factory)
            reconstructed = self.read()
        data = result.descriptor()
        self.assertEqual(data["status"], "blocked")
        self.assertEqual(data["historical_outcome"], "uncertain")
        self.assertEqual(data["provider_observation"], "not-collected")
        self.assertEqual(data["identity"], self.query.identity.descriptor())
        self.assertEqual(data["evidence"]["request_fingerprint"], self.query.expected_request_fingerprint)
        self.assertEqual(data["evidence"]["outcome_fingerprint"], self.query.expected_outcome_fingerprint)
        self.assertEqual(data["evidence"]["outcome_recorded_at"], self.original.latest_transition_event.occurred_at)
        self.assertEqual(data["evidence"]["configuration_protection"], "validated")
        self.assertGreater(data["evidence"]["configuration_count"], 0)
        self.assertEqual(data["source"]["request_id"], "request-a")
        self.assertEqual(data["source"]["plan_id"], "plan-a")
        self.assertEqual(data["evidence"], reconstructed.descriptor()["evidence"])
        for blocker in ("provider-observation-not-collected", "predecessor-quiescence-not-established",
                        "continuation-authority-not-established"):
            self.assertIn(blocker, data["blockers"])
        self.assert_measured_snapshot(measured)
        self.assertEqual((self.complete_start_snapshot(), self.protection_rows()), before)
        self.assertLessEqual(len(json.dumps(data).encode()), 16384)
        self.assertNotIn("configuration.fixture-outcome", repr(result))
        self.assertNotIn("Recorded bounded test outcome", json.dumps(data))

    def test_descriptor_is_detached_from_immutable_proposal_and_fingerprint(self):
        self.seed_uncertain()
        result = self.read()
        fingerprint = result.fingerprint
        original = result.descriptor()
        exposed = result.descriptor()
        exposed["identity"]["attempt"] = 20
        exposed["blockers"].clear()
        exposed["evidence"]["outcome_fingerprint"] = "c" * 64
        self.assertEqual(result.descriptor(), original)
        self.assertEqual(result.fingerprint, fingerprint)

    def test_expected_evidence_mismatch_and_nonuncertain_attempt_are_refused(self):
        self.seed_uncertain()
        before = self.complete_start_snapshot(), self.protection_rows()
        for key in ("expected_request_fingerprint", "expected_outcome_fingerprint"):
            with self.assertRaises(self.api.ActivityRecoveryError) as caught:
                self.read(replace(self.query, **{key: "f" * 64}))
            self.assertEqual(caught.exception.category, "stale-evidence")
        self.assertEqual((self.complete_start_snapshot(), self.protection_rows()), before)
        self.reset_start_truth()
        started = self.start_service("known-failed-start").execute(self.configuration_command())
        failed, _ = self.fold_direct_outcome(started, status="failed")
        query = self.api.ActivityRecoveryQuery("workspace-a", failed.state.identity,
            failed.state.request_fingerprint, failed.state.outcome_fingerprint)
        with self.assertRaises(self.api.ActivityRecoveryError) as caught:
            self.read(query)
        self.assertEqual(caught.exception.category, "stale-evidence")

    def test_expired_claim_is_snapshot_fact_not_retry_or_quiescence_permission(self):
        self.seed_uncertain()
        self.expire_claim()
        data = self.read().descriptor()
        self.assertTrue(data["current"]["claim"]["expired"])
        self.assertIn("claim-unavailable-or-expired", data["blockers"])
        self.assertIn("predecessor-quiescence-not-established", data["blockers"])
        self.assertEqual(data["status"], "blocked")
        self.assertNotEqual(data["current"]["snapshot_at"], data["evidence"]["outcome_recorded_at"])

    def test_missing_protection_refuses_without_repair_or_remint(self):
        self.seed_uncertain()
        identity = self.query.identity
        self.connection.execute("DELETE FROM cpk_configuration_claims WHERE run_id=%s AND activity_id=%s AND attempt=%s",
            (identity.run_id.value, identity.activity_id, identity.attempt))
        before = self.complete_start_snapshot(), self.protection_rows()
        with self.assertRaises(self.api.ActivityRecoveryError) as caught:
            self.read()
        self.assertEqual(caught.exception.category, "unavailable")
        self.assertEqual((self.complete_start_snapshot(), self.protection_rows()), before)
