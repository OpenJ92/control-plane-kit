"""#1846 narrow reuse of the accepted committed health first-start world."""
from datetime import datetime, timezone
import importlib
import importlib.util

from control_plane_kit_core.operations import EffectAttemptStatus
from control_plane_kit_core.operations.lifecycle import ActivityRunStatus, ExecutionRequestStatus
from control_plane_kit_operations.delegation_signing_keys import RegisteredDelegationSigningKeyStatus
from control_plane_kit_operations.records import ApprovalDecisionKind
from control_plane_kit_operations.secret_providers import secret_resolution_grant_for
from tests.postgres_health_effect_start_fixture import PostgresHealthEffectStartFixture

MODULE = "control_plane_kit_operations.health_signing_authority"
MISSING = "#1846 current health signing authority reload is missing"


def health_signing_api(test):
    test.assertIsNotNone(importlib.util.find_spec(MODULE), MISSING)
    return importlib.import_module(MODULE)


def timestamp(seconds, microseconds=0):
    value = datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=microseconds)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ" if microseconds else "%Y-%m-%dT%H:%M:%SZ")


class PostgresHealthSigningAuthorityFixture(PostgresHealthEffectStartFixture):
    def setUp(self):
        PostgresHealthEffectStartFixture.setUp(self)
        with self.observed_time("2030-01-01T00:00:00Z"):
            self.started, _ = self.execute_health()
        self.preparation = self.started.preparation
        self.current_time = self.preparation.transit_grant.not_before
        # All these assertions precede the missing-feature guard. Collection and
        # a registered-only or partial preparation cannot masquerade as target red.
        self.assertEqual(self.health_counts(), (1, 1, 2, 1))
        self.resolutions = {}
        with self.unit_of_work() as uow:
            request = uow.stores.execution.get_request("request-a")
            run = uow.stores.execution.get_run("run-a")
            self.assertIs(request.status, ExecutionRequestStatus.CLAIMED)
            self.assertEqual(request.claim.fence, self.start_value.fence)
            self.assertIs(run.status, ActivityRunStatus.RUNNING)
            self.assertEqual(uow.stores.execution.get_latest_run_for_request_for_update("request-a"), run)
            self.assertEqual(uow.stores.health_effect_preparations.get(self.preparation.identity), self.preparation)
            self.assertIs(self.started.start.attempt.state.status, EffectAttemptStatus.STARTED)
            self.assertEqual(uow.stores.activity_history.get_plan("plan-a").plan, self.health_plan)
            decision = uow.stores.activity_history.approval_decision_for_request(request.approval_request_id)
            self.assertEqual(decision.decision_id, request.approval_decision_id)
            self.assertIs(decision.decision, ApprovalDecisionKind.APPROVED)
            for family in ("transit", "workload"):
                key = self.keys[family]
                self.assertIs(key.status, RegisteredDelegationSigningKeyStatus.ACTIVE)
                self.assertEqual(uow.stores.delegation_signing_keys.require_unambiguous_active("workspace-a", key.purpose), key)
                use = uow.stores.secret_use_authorizations.get("workspace-a", getattr(self.preparation, family + "_authorization_id"))
                reference = uow.stores.secret_references.get_by_registration("workspace-a", use.reference_registration_id)
                provider = uow.stores.secret_providers.get_by_registration("workspace-a", use.provider_registration_id)
                self.assertEqual(reference, self.references[family])
                self.assertEqual(provider, self.provider)
                self.assertEqual(use.reference, key.private_key_reference)
                self.resolutions[family] = secret_resolution_grant_for(use, provider=provider)
        self.reload_api = health_signing_api(self)
        # Current-authority negatives must not pass merely because the real
        # clock is before this synthetic permission's not_before. Every default
        # reload inhabits its original interval; edge tests override this layer.
        self._default_reload_time = self.observed_time(timestamp(self.current_time))
        self.default_observations = self._default_reload_time.__enter__()
        self.addCleanup(self._default_reload_time.__exit__, None, None, None)

    def reload_command(self, **changes):
        first = self.start_health_command()
        values = dict(request_id=first.start.request_id, identity=first.start.transition.identity,
            context=first.context, authority=first.start.authority, fence=first.start.fence)
        return self.reload_api.ReloadHealthSigningAuthority(**(values | changes))

    def reload(self, command=None, *, unit_of_work=None):
        service = self.reload_api.HealthSigningAuthorityReloadService(unit_of_work or self.unit_of_work)
        return service.execute(self.reload_command() if command is None else command)

    def assert_history_unchanged(self, before):
        self.assertEqual(self.health_snapshot(), before)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.health_effect_preparations.get(self.preparation.identity), self.preparation)
