"""#1975 Gate 2 qualification proves owner decisions, never provider conformance."""
from dataclasses import fields, replace
import importlib
import unittest

from control_plane_kit_core.operations.execution import EffectResultKind
from control_plane_kit_core.planning import NodeTarget, StartNode, StopNode
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.products import ProductReference
from control_plane_kit_core.runtime_effect_observation import (
    runtime_effect_intent_for_request, runtime_effect_intent_fingerprint,
)
from control_plane_kit_core.runtime_effects import RuntimeEffectResult, RuntimeEffectFailure
from control_plane_kit_core.secret_installation import secret_material_selection_fingerprint
from control_plane_kit_core.secret_selections import SecretVersionSelection
from control_plane_kit_core.secrets import SecretProviderEndpointReference, SecretReference, SecretResolutionGrant
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.coordinator import RuntimeInterpreterDispatcher
from control_plane_kit_operations.runtime_effects import (
    runtime_effect_request_for_context, required_secret_uses_for_runtime_effect,
)
from tests.test_runtime_interpreter_dispatcher import (
    RecordingSecretUseAuthorizer, _registered_product, _registered_runtime_authority,
    context_for, projection_record_from_graph,
)


def registration(adapter, **changes):
    name = "control_plane_kit_operations.runtime_adapter_registration"
    try:
        module = importlib.import_module(name)
    except ModuleNotFoundError as error:
        if error.name != name:
            raise
        raise AssertionError("missing exact-version adapter registration") from error
    return module.RuntimeAdapterRegistration(adapter=adapter, **{
        "exact_version_execute": False, "exact_version_execute_with_authority": False,
        "exact_version_observe": False, **changes})


def selected_context(*, with_authority=False, operation=None, shared_probe=False):
    authority = _registered_runtime_authority()
    context = context_for(operation or StartNode(NodeTarget("api")),
        authority_ref=authority.authority_ref if with_authority else None,
        runtime_authorities=(authority,) if with_authority else (),
        worker_scopes=(PolicyScope.EXECUTION_OPERATE, PolicyScope.SECRET_PROVIDER_USE))
    product = _registered_product(secret_delivery=True)
    if shared_probe:
        from control_plane_kit_core.algebra import BlockSockets, ProviderSocket
        from control_plane_kit_core.products import ProductDescriptorCodec, ProviderRuntimePort
        from control_plane_kit_core.secrets import SecretReferenceEnvironmentDelivery, SecretUseIntent
        from control_plane_kit_core.types import Protocol
        from control_plane_kit_core.verification import PostgresPasswordAuthentication, PostgresQueryCheck, VerificationContract
        value = product.descriptor_document.product
        delivery = replace(value.runtime_contract.secret_deliveries[0], intent=SecretUseIntent.POSTGRES_PASSWORD)
        reference_only = SecretReferenceEnvironmentDelivery("OPAQUE_REFERENCE",
            SecretReference("secret://provider-a/opaque/reference"))
        contract = replace(value.runtime_contract,
            sockets=BlockSockets(providers=(ProviderSocket("db", Protocol.POSTGRES),)),
            provider_ports=(ProviderRuntimePort("db", 5432),), secret_deliveries=(delivery, reference_only),
            verification=VerificationContract((PostgresQueryCheck(check_id="db-ready", provider_socket="db",
                authentication=PostgresPasswordAuthentication(database="app", username="app",
                    password_reference=delivery.reference)),)))
        product = type(product).from_document(workspace_id=product.workspace_id,
            descriptor_document=ProductDescriptorCodec().encode_document(replace(value, runtime_contract=contract)),
            source=product.source, imported_by=product.imported_by, imported_at=product.imported_at)
    reference = ProductReference.from_document(product.descriptor_document)
    deliveries = product.descriptor_document.product.runtime_contract.secret_deliveries
    selection = SecretVersionSelection(deliveries[0].reference, deliveries[0].intent, "version-dispatch")
    graph = DEFAULT_GRAPH_CODEC.decode(context.desired_graph.graph_descriptor)
    graph = graph.update_node(replace(graph.node("api"), secret_deliveries=deliveries,
        sockets=product.descriptor_document.product.runtime_contract.sockets,
        block_spec=replace(graph.node("api").block_spec,
            verification=product.descriptor_document.product.runtime_contract.verification),
        secret_selections=(selection,), metadata={"product_identity": reference.identity.key,
            "product_descriptor_digest": reference.descriptor_sha256.value}))
    return replace(context, registered_products=(product,),
        desired_graph=projection_record_from_graph("graph-desired", graph, version=2),
        base_graph=projection_record_from_graph("graph-current", graph))


class VersionAuthorizer(RecordingSecretUseAuthorizer):
    def __init__(self, substitute=None):
        super().__init__()
        self.version_commands = []
        self.substitute = substitute

    def authorize_version_resolution(self, command):
        from control_plane_kit_core.secret_version_grants import SecretVersionResolutionGrant
        self.version_commands.append(command)
        grant = SecretVersionResolutionGrant(
            authorization_id="suse_" + "e" * 64, workspace_id=command.workspace_id,
            reference_registration_id="sref_" + "b" * 64,
            provider_registration_id="sprov_" + "c" * 64,
            endpoint_reference=SecretProviderEndpointReference("provider-a"),
            credential_reference=SecretReference("secret://bootstrap/provider-a-token"),
            selection=command.selection, actor_subject=command.actor_subject,
            correlation_id=command.correlation_id, intent_fingerprint="d" * 64,
            operation_id=command.operation_id, session_id=command.session_id,
            run_id=command.run_id, activity_id=command.activity_id, effect_id=command.effect_id,
            request_fingerprint=command.request_fingerprint, selection_fingerprint=command.selection_fingerprint)
        return self.substitute(grant) if self.substitute else grant


class RecordingVersionAdapter:
    def __init__(self):
        self.calls = []

    def execute(self, request):
        self.calls.append(("execute", request, None))
        return RuntimeEffectResult.failed(request.effect_id,
            RuntimeEffectFailure("fixture.no-installation", "No installation was performed."))

    def execute_with_authority(self, request, authority):
        self.calls.append(("execute_with_authority", request, authority))
        return RuntimeEffectResult.failed(request.effect_id,
            RuntimeEffectFailure("fixture.no-installation", "No installation was performed."))


def operational_substitution(grant):
    values = {item.name: getattr(grant, item.name) for item in fields(SecretResolutionGrant)
        if item.name not in ("reference", "intent", "probe_id")}
    return SecretResolutionGrant(**values, reference=grant.selection.reference,
        intent=grant.selection.intent, probe_id=None)


class SecretVersionDispatchTests(unittest.TestCase):
    def test_enumeration_preserves_consumer_probe_roles_and_excludes_reference_only_delivery(self):
        from control_plane_kit_operations import runtime_effects
        operational_type = getattr(runtime_effects, "OperationalSecretUse", None)
        self.assertIsNotNone(operational_type, "missing explicit operational secret use role")
        request = runtime_effect_request_for_context(selected_context(shared_probe=True))
        selection, = request.products[0].secret_selections
        probe, = request.products[0].product.runtime_contract.verification.checks
        self.assertEqual(probe.authentication.password_reference, selection.reference)
        uses = required_secret_uses_for_runtime_effect(request, None)
        self.assertEqual(set(uses), {selection, operational_type(selection.reference, selection.intent)})
        self.assertEqual(len(uses), 2)

    def test_qualified_shared_pair_reaches_each_arm_as_two_distinct_grant_roles(self):
        for remote in (False, True):
            with self.subTest(with_authority=remote):
                context = selected_context(with_authority=remote, shared_probe=True)
                request = runtime_effect_request_for_context(context)
                selection, = request.products[0].secret_selections
                probe, = request.products[0].product.runtime_contract.verification.checks
                self.assertEqual(probe.authentication.password_reference, selection.reference)
                adapter, authorizer = RecordingVersionAdapter(), VersionAuthorizer()
                registered = registration(adapter, exact_version_execute=not remote,
                    exact_version_execute_with_authority=remote)
                from control_plane_kit_core.secret_version_grants import SecretVersionResolutionGrant
                result = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: registered},
                    secret_use_authorizer=authorizer).execute_runtime(context, request)
                self.assertEqual(result.kind, EffectResultKind.FAILED)
                self.assertEqual(len(authorizer.commands), 1)
                self.assertEqual(len(authorizer.version_commands), 1)
                operational_command, = authorizer.commands
                consumer_command, = authorizer.version_commands
                self.assertEqual((operational_command.reference, operational_command.intent),
                    (consumer_command.selection.reference, consumer_command.selection.intent))
                self.assertNotEqual(operational_command.correlation_id, consumer_command.correlation_id)
                self.assertEqual(len(adapter.calls), 1)
                arm, received, authority = adapter.calls[0]
                self.assertEqual(arm, "execute_with_authority" if remote else "execute")
                self.assertEqual(authority, context.runtime_authorities[0] if remote else None)
                self.assertEqual(len(received.secret_resolution_grants), 2)
                operational, = (grant for grant in received.secret_resolution_grants if type(grant) is SecretResolutionGrant)
                consumer, = (grant for grant in received.secret_resolution_grants if type(grant) is SecretVersionResolutionGrant)
                self.assertEqual((operational.reference, operational.intent),
                    (consumer.selection.reference, consumer.selection.intent))
                self.assertEqual(consumer.selection, request.products[0].secret_selections[0])
                self.assertEqual(operational.correlation_id, operational_command.correlation_id)
                self.assertEqual(consumer.correlation_id, consumer_command.correlation_id)

    def test_unqualified_legacy_adapter_refuses_each_execution_arm_before_authorization(self):
        for remote in (False, True):
            with self.subTest(with_authority=remote):
                context = selected_context(with_authority=remote)
                request = runtime_effect_request_for_context(context)
                adapter, authorizer = RecordingVersionAdapter(), VersionAuthorizer()
                result = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: adapter},
                    secret_use_authorizer=authorizer).execute_runtime(context, request)
                self.assertEqual(result.kind, EffectResultKind.UNSUPPORTED)
                self.assertEqual(authorizer.commands, [])
                self.assertEqual(authorizer.version_commands, [])
                self.assertEqual(adapter.calls, [])

    def test_capability_for_the_other_execution_arm_is_not_sufficient(self):
        for remote in (False, True):
            with self.subTest(with_authority=remote):
                context = selected_context(with_authority=remote)
                adapter, authorizer = RecordingVersionAdapter(), VersionAuthorizer()
                registered = registration(adapter, exact_version_execute=remote,
                    exact_version_execute_with_authority=not remote, exact_version_observe=True)
                result = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: registered},
                    secret_use_authorizer=authorizer).execute_runtime(context,
                        runtime_effect_request_for_context(context))
                self.assertEqual(result.kind, EffectResultKind.UNSUPPORTED)
                self.assertEqual(authorizer.commands + authorizer.version_commands, [])
                self.assertEqual(adapter.calls, [])

    def test_qualified_each_arm_receives_exact_consumer_grant_and_original_commitments(self):
        for remote in (False, True):
            with self.subTest(with_authority=remote):
                context = selected_context(with_authority=remote)
                request = runtime_effect_request_for_context(context)
                adapter, authorizer = RecordingVersionAdapter(), VersionAuthorizer()
                registered = registration(adapter, exact_version_execute=not remote,
                    exact_version_execute_with_authority=remote)
                result = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: registered},
                    secret_use_authorizer=authorizer).execute_runtime(context, request)
                self.assertEqual(result.kind, EffectResultKind.FAILED)
                self.assertEqual(authorizer.commands, [])
                self.assertEqual(len(authorizer.version_commands), 1)
                self.assertEqual(len(adapter.calls), 1)
                arm, received, authority = adapter.calls[0]
                self.assertEqual(arm, "execute_with_authority" if remote else "execute")
                self.assertEqual(authority, context.runtime_authorities[0] if remote else None)
                grant, = received.secret_resolution_grants
                self.assertEqual(grant.selection, request.products[0].secret_selections[0])
                self.assertEqual(grant.request_fingerprint,
                    runtime_effect_intent_fingerprint(runtime_effect_intent_for_request(request)))
                self.assertEqual(grant.selection_fingerprint, secret_material_selection_fingerprint(request))
                self.assertEqual(runtime_effect_intent_for_request(received), runtime_effect_intent_for_request(request))
                self.assertIsNone(authorizer.version_commands[0].session_id)

    def test_substituted_consumer_grant_never_reaches_either_execution_arm(self):
        changes = ({"actor_subject": "other-worker"}, {"correlation_id": "other-correlation"},
            {"effect_id": "other-effect"}, {"request_fingerprint": "f" * 64},
            {"selection_fingerprint": "e" * 64})
        substitutions = tuple((next(iter(change)), lambda grant, change=change: replace(grant, **change))
            for change in changes) + (
                ("version", lambda grant: replace(grant, selection=replace(grant.selection, version_id="version-other"))),
                ("operational-role", operational_substitution),
            )
        for remote in (False, True):
            for label, substitute in substitutions:
                with self.subTest(with_authority=remote, change=label):
                    context = selected_context(with_authority=remote)
                    adapter = RecordingVersionAdapter()
                    authorizer = VersionAuthorizer(substitute)
                    result = RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: registration(adapter,
                        exact_version_execute=True, exact_version_execute_with_authority=True)},
                        secret_use_authorizer=authorizer).execute_runtime(context,
                            runtime_effect_request_for_context(context))
                    self.assertEqual(result.kind, EffectResultKind.UNSUPPORTED)
                    self.assertEqual(len(authorizer.version_commands), 1)
                    self.assertEqual(adapter.calls, [])

    def test_cleanup_never_requests_consumer_resolution(self):
        context = selected_context(operation=StopNode(NodeTarget("api")))
        request = runtime_effect_request_for_context(context)
        self.assertEqual(required_secret_uses_for_runtime_effect(request, None), ())
        adapter, authorizer = RecordingVersionAdapter(), VersionAuthorizer()
        RuntimeInterpreterDispatcher({RuntimeKind.DOCKER: adapter},
            secret_use_authorizer=authorizer).execute_runtime(context, request)
        self.assertEqual(authorizer.commands + authorizer.version_commands, [])
        self.assertEqual(len(adapter.calls), 1)

    def test_registration_flags_require_exact_booleans(self):
        for name in ("exact_version_execute", "exact_version_execute_with_authority", "exact_version_observe"):
            for value in (None, 0, 1, "yes"):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    registration(RecordingVersionAdapter(), **{name: value})
