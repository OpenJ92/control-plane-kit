"""Real existing values for the #1852 public entrance; no effects or test runner."""
from dataclasses import replace
import importlib
import importlib.util

from control_plane_kit_core.identity import (
    AuthenticatedPrincipal, PrincipalIdentity, PrincipalKind, WorkspaceGrant,
)
from control_plane_kit_core.operations import EffectAttemptIdentity, EffectAttemptTransition, EffectAttemptTransitionKind, RunId
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
from control_plane_kit_operations.effect_attempt_start import StartEffectAttempt
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from tests.effect_attempt_intent_fixture import EffectAttemptIntentFixture
from tests.health_effect_preparation_fixture import HealthEffectPreparationFixture

MODULE = "control_plane_kit_operations.health_effect_attempt_start"
MISSING = "#1852 atomic health first-start entrance is missing"
HEALTH_SCOPES = (
    PolicyScope.NODE_CONTROL_READ, PolicyScope.NODE_CONTROL_EXECUTE,
    PolicyScope.DELEGATION_KEY_USE, PolicyScope.SECRET_PROVIDER_USE,
)


def trusted_health_context(*, actor="health-operator", workspace="workspace-a", scopes=HEALTH_SCOPES):
    principal = AuthenticatedPrincipal(
        PrincipalIdentity("test-authenticator", actor, PrincipalKind.OPERATOR),
        (WorkspaceGrant(workspace, scopes),),
    )
    return principal.command_context(workspace)


class HealthEffectStartValues(HealthEffectPreparationFixture):
    def health_start_api(self):
        self.assertIsNotNone(importlib.util.find_spec(MODULE), MISSING)
        return importlib.import_module(MODULE)

    def health_start_value(self, *, activity=None):
        if activity is None:
            _, activity, _, _, _ = self.health_context()
        intent = EffectAttemptIntentFixture.intent(self, activity_id=activity.activity_id.value,
            products=(), process_delivery=False)
        intent = replace(intent, operation=activity.operation, source=replace(intent.source,
            base_graph_id="health-base", desired_graph_id="health-desired"))
        identity = EffectAttemptIdentity(RunId("run-a"), activity.activity_id.value, 1)
        transition = EffectAttemptTransition(EffectAttemptTransitionKind.STARTED, identity,
            runtime_effect_intent_fingerprint(intent))
        return StartEffectAttempt("request-a", transition, intent,
            ExecutionWorkerAuthority("worker-a", (PolicyScope.EXECUTION_OPERATE,)),
            ExecutionLeaseFence("worker-a", 7))

    def health_command(self, *, start=None, context=None):
        start = self.health_start_value() if start is None else start
        context = trusted_health_context() if context is None else context
        return self.health_start_api().StartHealthEffectAttempt(start, context)
