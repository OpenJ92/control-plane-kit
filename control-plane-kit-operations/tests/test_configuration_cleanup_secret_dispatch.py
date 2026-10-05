"""Dispatcher contract only; typed TLS references cause no network or secret read."""
import unittest
from dataclasses import replace

from control_plane_kit_core.operations import RunId
from control_plane_kit_core.planning import CleanupConfigurationInstances, ActivityPlan, RiskLevel, ActivityImpact
from control_plane_kit_core.configuration_instances import ConfigurationCleanupReason, ConfigurationCleanupStatus
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.runtime_effects import (
    RuntimeEffectKind, RuntimeEffectRequest, RuntimeEffectSource, configuration_cleanup_outcomes,
)
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.coordinator import RuntimeInterpreterDispatcher
from control_plane_kit_operations.runtime_authorities import RegisteredRuntimeAuthority, RemoteDockerTlsAuthority
from tests.configuration_instance_fixture import configuration_ref
from tests.test_runtime_interpreter_dispatcher import (
    context_for, AuthorityAwareRecordingInterpreter, RecordingSecretUseAuthorizer,
)


class ConfigurationCleanupSecretDispatchTests(unittest.TestCase):
    def test_remote_cleanup_secret_denial_is_known_not_attempted_without_interpreter_call(self):
        reference = RuntimeAuthorityReference("remote-cleanup")
        authority = RegisteredRuntimeAuthority.from_authority(workspace_id="workspace-a",
            authority_ref=reference, runtime_kind=RuntimeKind.DOCKER,
            authority=RemoteDockerTlsAuthority("tcp://cleanup.invalid:2376",
                SecretReference("secret://test/ca"), SecretReference("secret://test/cert"),
                SecretReference("secret://test/key")), admitted_by="operator-a",
            admitted_at="2026-10-03T12:00:00Z")
        operation = CleanupConfigurationInstances((configuration_ref(),))
        context = context_for(operation, authority_ref=reference, runtime_authorities=(authority,))
        activity = replace(context.activity, risk=RiskLevel.CRITICAL, impact=ActivityImpact.DESTRUCTIVE)
        context = replace(context, activity=activity, plan_record=replace(context.plan_record,
            plan=ActivityPlan((activity,))))
        request = RuntimeEffectRequest("event-intent", RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1,
            RuntimeKind.DOCKER, RuntimeEffectSource("workspace-a", "request-a", RunId("run-a"),
                "plan-a", "graph-current", "graph-desired", "event-intent"),
            context.activity.activity_id, operation, authority_ref=reference)
        interpreter = AuthorityAwareRecordingInterpreter("docker")
        authorizer = RecordingSecretUseAuthorizer(denied=True)
        result = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: interpreter},
            secret_use_authorizer=authorizer).execute_runtime(context, request)
        self.assertEqual(interpreter.requests, [])
        self.assertEqual(interpreter.authorities, [])
        self.assertEqual(len(authorizer.commands), 1)
        self.assertEqual(tuple((row.ref, row.status, row.reason)
            for row in configuration_cleanup_outcomes(request, result).outcomes),
            ((configuration_ref(), ConfigurationCleanupStatus.UNKNOWN, ConfigurationCleanupReason.NOT_ATTEMPTED),))
