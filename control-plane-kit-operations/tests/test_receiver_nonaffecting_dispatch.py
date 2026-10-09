"""Real nonreceiver ingress owner in receiver-bearing pinned graph material."""

from dataclasses import replace
import unittest

from control_plane_kit_core.operations import ActivityEventKind
from control_plane_kit_core.planning import RemovePublicIngress
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import SecretProviderEndpointReference, SecretReference, SecretUseIntent
from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_operations.coordinator import ActivityExecutionDispatcher
from control_plane_kit_operations.ingress_authorities import IngressAuthorityProviderKind, OwnedIngressResourceStatus
from control_plane_kit_operations.ingress_realization import IngressRealizationAdapter
from control_plane_kit_operations.secret_providers import (
    RegisteredSecretProvider, RegisteredSecretReference, SecretProviderKind, SecretUseAuthorizationService,
)
from tests.postgres_effect_attempt_coordinator_fixture import RecordingRuntimeAdapter
from tests.receiver_canonical_acceptance_fixture import ReceiverCanonicalAcceptanceFixture
from tests.receiver_recorded_completion_fixture import retain_completion_inputs
from tests.receiver_fresh_execution_fixture import load_execution_context
from tests.test_ingress_realization import RecordingIngressInterpreter, TrackingUnitOfWorkFactory


class ReceiverNonaffectingDispatchTests(ReceiverCanonicalAcceptanceFixture, unittest.TestCase):
    def test_real_ingress_teardown_dispatch_keeps_its_authority_in_receiver_graph(self):
        self.desired_receiver("first", graph=self.canonical_receiver_graph)
        original, _, _ = self.retained_success("first")
        self.advance(original, "first")
        accepted = self.receiver_origin()
        self.desired_receiver("remove", graph=DeploymentGraph("removed"))
        _, plan, _ = self.plan_and_admit("remove")
        claimed = self.ready_run("remove")
        command = self.execution_command(claimed, "ingress")
        command = replace(command, authority=replace(command.authority, scopes=tuple(PolicyScope)))
        context = load_execution_context(
            self.coordinator(self.unit_of_work, RecordingRuntimeAdapter(), "ingress"), command)
        index = next(index for index, activity in enumerate(plan.plan.activities)
            if type(activity.operation) is RemovePublicIngress)
        activity = plan.plan.activities[index]
        retain_completion_inputs(self, context, activities=plan.plan.activities[:index])
        with self.unit_of_work() as uow:
            authority = uow.stores.ingress_authorities.require_active_for_hostname("workspace-a",
                self.canonical_receiver_graph.public_ingresses[0].authority_ref,
                self.canonical_receiver_graph.public_ingresses[0].hostname)
            # Synthetic reference registrations authorize only the test provider
            # stub; no secret value is read and no external provider is called.
            uow.stores.secret_providers.register(RegisteredSecretProvider(
                registration_id="sprov-c3-api", workspace_id="workspace-a",
                provider_id=authority.authority.api_token_ref.provider_id,
                provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS, display_name="Fixture API custody",
                endpoint_reference=SecretProviderEndpointReference("c3-api"),
                credential_reference=SecretReference("secret://bootstrap/c3-api"),
                allowed_reference_prefixes=(authority.authority.api_token_ref,),
                allowed_intents=(SecretUseIntent.CLOUDFLARE_API_TOKEN,), admitted_by="operator-a", admitted_at=self.now()))
            uow.stores.secret_references.register(RegisteredSecretReference(
                registration_id="sref-c3-api", workspace_id="workspace-a", reference=authority.authority.api_token_ref,
                provider_registration_id="sprov-c3-api", allowed_intents=(SecretUseIntent.CLOUDFLARE_API_TOKEN,),
                admitted_by="operator-a", admitted_at=self.now()))
            uow.commit()
        tracker = TrackingUnitOfWorkFactory(self.database_url)
        interpreter = RecordingIngressInterpreter(tracker)
        runtime = RecordingRuntimeAdapter()
        ingress = IngressRealizationAdapter(tracker,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter}, clock=self.now,
            secret_use_authorizer=SecretUseAuthorizationService(tracker))
        self.coordinator(tracker, ActivityExecutionDispatcher(runtime, ingress), "ingress").execute(command)
        self.assertEqual(interpreter.teardown_active_counts, [0])
        self.assertEqual(len(interpreter.teardown_resources), 1)
        self.assertTrue(interpreter.teardown_grants[0].permits(
            authority.authority.api_token_ref, SecretUseIntent.CLOUDFLARE_API_TOKEN))
        self.assertEqual(runtime.runtime_calls, [])
        self.assertEqual(runtime.legacy_calls, [])
        self.assertEqual(self.receiver_origin(), accepted)
        with self.unit_of_work() as uow:
            original = interpreter.teardown_resources[0]
            matching = tuple(resource for resource in uow.stores.ingress_resources.list_cloudflare("workspace-a")
                if (resource.workspace_id, resource.runtime_id, resource.ingress_id, resource.epoch)
                == (original.workspace_id, original.runtime_id, original.ingress_id, original.epoch))
            self.assertEqual(len(matching), 1)
            removed, = matching
            self.assertIs(removed.status, OwnedIngressResourceStatus.REMOVED)
            self.assertEqual(removed.removed_by_run_id, claimed.run.run_id)
            events = uow.stores.execution.events_for_run(claimed.run.run_id)
        self.assertEqual(tuple(event.kind for event in events if event.activity_id == activity.activity_id.value),
            (ActivityEventKind.STEP_STARTED, ActivityEventKind.STEP_SUCCEEDED))
