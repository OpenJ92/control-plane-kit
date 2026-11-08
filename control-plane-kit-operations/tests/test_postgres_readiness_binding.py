"""Original record provenance only; no authority, private resolution or dispatch."""
from dataclasses import replace
import unittest

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime, BlockSockets, ProviderSocket
from control_plane_kit_core.configuration_instances import ConfigurationInstanceRef, ConfigurationInstanceSelection
from control_plane_kit_core.configuration_installation import ConfigurationInstallationDisposition, ConfigurationInstallationReceipt
from control_plane_kit_core.capabilities import CapabilityName
from control_plane_kit_core.configuration_invocation import configuration_invocation_selection_fingerprint
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding
from control_plane_kit_core.operations import ActivityEventKind, RunId
from control_plane_kit_core.planning import ActivityId, ActivityPlan, ActivityDependency, NodeTarget, PlannedActivity, StartNode, WaitForHealthy
from control_plane_kit_core.postgres_readiness import PostgresReadinessObserver
from control_plane_kit_core.products import ProductDescriptorCodec, ProviderRuntimePort, ProductInstanceConfiguration, instantiate_product
from control_plane_kit_core.receiver_configuration import ReceiverNodeControlConfigurationCodec
from control_plane_kit_core.node_control_surface_reads import WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationProfile
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_for_request, runtime_effect_intent_fingerprint
from control_plane_kit_core.runtime_effects import RuntimeEffectKind, RuntimeEffectResult, RuntimeEffectRequest, RuntimeEffectSource, RuntimeProductMaterial
from control_plane_kit_core.secret_installation import SecretInstallationReceipt, SecretResolutionReceipt, secret_material_selection_fingerprint
from control_plane_kit_core.secret_selections import SecretVersionSelection
from control_plane_kit_core.secrets import SecretFileDelivery, SecretFilePathBinding, SecretUseIntent, SecretReference
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph, compile_topology
from control_plane_kit_core.types import Protocol
from control_plane_kit_core.verification import PostgresQueryCheck, PostgresPasswordAuthentication, VerificationContract
from control_plane_kit_core.wrapper_configuration import WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT
from control_plane_kit_operations.effect_attempt_intent_evidence import EffectAttemptIntentRecord
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.effect_outcome_evidence import ExecutionEffectOutcome
from control_plane_kit_operations.postgres_readiness import bind_original_postgres_readiness, PostgresReadinessProvenanceUnavailable
from tests.effect_attempt_record_fixture import EffectAttemptRecordFixture
from tests.health_receiver_trust_fixture import artifact
from tests.runtime_management_fixtures import bootstrap_management_graph
from tests.test_runtime_interpreter_dispatcher import context_for, projection_record_from_graph


class OriginalPostgresReadinessRecordTests(EffectAttemptRecordFixture, unittest.TestCase):
    def intent_for_attempt(self, **_):
        return runtime_effect_intent_for_request(self.fixture_request)

    def recorded(self, request, story, prefix, ordinal):
        self.fixture_request = request
        record = self.record(story, activity_id=request.activity_id.value,
            event_prefix=prefix, original_ordinal=ordinal, latest_ordinal=ordinal + 1,
            original_time="2026-11-08T12:00:00Z", latest_time="2026-11-08T12:00:01Z")
        original = EffectAttemptIntentRecord(record.state.identity, record.original_start_event,
            runtime_effect_intent_for_request(request))
        return record, original

    def supplied_records(self):
        context = context_for(StartNode(NodeTarget("api")))
        base_product = context.registered_products[0]
        surface = bootstrap_management_graph(self).node("api").block_spec.control_surfaces[0]
        declaration = WorkloadNodeControlSurfaceDeclaration(surface, WorkloadNodeControlSurfaceDeclarationProfile.V2)
        control = artifact("workload", declaration, receiver=True, runtime="runtime-a")
        reference = SecretReference("secret://local/workspace-a/postgres/password")
        delivery = SecretFileDelivery("/run/secrets/postgres/password", reference, SecretUseIntent.POSTGRES_PASSWORD,
            path_binding=SecretFilePathBinding("POSTGRES_PASSWORD_FILE"))
        check = PostgresQueryCheck(check_id="original-sql", provider_socket="sql",
            authentication=PostgresPasswordAuthentication("cpk", "cpk", reference))
        source_product = base_product.descriptor_document.product
        contract = replace(source_product.runtime_contract,
            sockets=BlockSockets(providers=(ProviderSocket("sql", Protocol.POSTGRES), ProviderSocket("http", Protocol.HTTP))),
            provider_ports=(ProviderRuntimePort("sql", 5432), ProviderRuntimePort("http", 8000)),
            configuration_artifacts=(control,), control_surfaces=(surface,),
            capabilities=(CapabilityName.NODE_CONTROLLABLE, CapabilityName.HEALTH_CHECKABLE),
            public_environment=(PublicStaticEnvironmentBinding(WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT, control.target_path),),
            secret_deliveries=(delivery,), verification=VerificationContract((check,)))
        document = ProductDescriptorCodec().encode_document(replace(source_product, runtime_contract=contract))
        product = type(base_product).from_document(workspace_id="workspace-a", descriptor_document=document,
            source=base_product.source, imported_by=base_product.imported_by, imported_at=base_product.imported_at)
        block = instantiate_product(document.product, "api", ProductInstanceConfiguration.from_contract(contract))
        graph = compile_topology(DeploymentTopology("original-sql", DockerRuntime(runtime_id="runtime-a", children=(block,))))
        selection = SecretVersionSelection(reference, SecretUseIntent.POSTGRES_PASSWORD, "selected-version-1")
        graph = graph.update_node(replace(graph.node("api"), secret_selections=(selection,)))
        current = projection_record_from_graph("graph-current", DeploymentGraph("empty"))
        desired = projection_record_from_graph("graph-desired", graph, version=2)
        start_id, wait_id = ActivityId("start-a"), ActivityId("wait-a")
        start = PlannedActivity(start_id, StartNode(NodeTarget("api")))
        wait = PlannedActivity(wait_id, WaitForHealthy(NodeTarget("api")), (ActivityDependency(start_id),))
        plan = replace(context.plan_record, plan=ActivityPlan((start, wait)),
            base_realized_projection_id=current.projection_id, desired_realized_projection_id=desired.projection_id)
        context = replace(context, activity=start, plan_record=plan, base_graph=current, desired_graph=desired,
            registered_products=(product,), intent_event=replace(context.intent_event, activity_id=start_id.value))
        # Supplied original records do not claim that today's unsupported SQL
        # plan was admitted by the normal runtime translator or dispatched.
        install = RuntimeEffectRequest(effect_id="install-start", kind=RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1,
            runtime_kind=graph.runtimes["runtime-a"].kind,
            source=RuntimeEffectSource("workspace-a", context.request.identity.request_id, RunId(context.run.run_id),
                plan.plan_id, plan.base_graph_id, plan.desired_graph_id, "install-start"),
            activity_id=start_id, operation=start.operation,
            products=(RuntimeProductMaterial("api", "runtime-a", product.reference, document.product,
                public_environment=contract.public_environment, secret_selections=(selection,)),),
            configuration_instances=ConfigurationInstanceSelection((ConfigurationInstanceRef(
                "config-allocation", "workspace-a", "runtime-a", "api", control.artifact_id, control.target_path,
                control.media_type, control.file_mode, control.content_digest),)))
        install_attempt, install_original = self.recorded(install, "succeeded", "install", 1)
        install = replace(install, effect_id=install_original.original_start_event.event_id,
            source=replace(install.source, intent_event_id=install_original.original_start_event.event_id))
        fp = runtime_effect_intent_fingerprint(install_original.intent)
        secret_fp = secret_material_selection_fingerprint(install)
        common = dict(disposition=ConfigurationInstallationDisposition.CREATED, prior_realization_fingerprint=None,
            current_realization_fingerprint="a" * 64)
        result = RuntimeEffectResult.succeeded(install.effect_id, evidence={
            "secret_resolution_receipt": SecretResolutionReceipt(fp, secret_fp).descriptor(),
            "secret_installation_receipt": SecretInstallationReceipt(fp, secret_fp, **common).descriptor(),
            "configuration_installation_receipt": ConfigurationInstallationReceipt(fp,
                configuration_invocation_selection_fingerprint(install.configuration_instances), **common).descriptor()})
        outcome = ExecutionEffectOutcome(install_original.identity, fp, result)
        state = replace(install_attempt.state, outcome_fingerprint=outcome.outcome_fingerprint)
        latest = self.event(state, ActivityEventKind.STEP_SUCCEEDED, event_id="install-latest", ordinal=2,
            occurred_at="2026-11-08T12:00:01Z")
        install_attempt = EffectAttemptRecord(state, install_attempt.original_start_event, latest)
        original_wait = replace(install, kind=RuntimeEffectKind.REALIZE_ACTIVITY, configuration_instances=None,
            activity_id=wait_id, operation=wait.operation)
        wait_attempt, wait_original = self.recorded(original_wait, "started", "wait", 3)
        observer = PostgresReadinessObserver(product.reference, document.product.image,
            "sql", "cpk", "cpk", delivery.target_path, "POSTGRES_PASSWORD_FILE")
        return dict(plan=plan, request=context.request, current=current, desired=desired, product=product,
            wait_original=wait_original, wait_attempt=wait_attempt, install_original=install_original,
            install_attempt=install_attempt, install_outcome=outcome, observer=observer, check_id=check.check_id)

    def test_original_record_join_reconstructs_one_request_and_original_window(self):
        values = self.supplied_records()
        binding = bind_original_postgres_readiness(**values)
        self.assertEqual(binding.request(), bind_original_postgres_readiness(**values).request())
        self.assertEqual(binding.deadline_epoch_ms - binding.started_at_epoch_ms, 5000)
        self.assertEqual(binding.descriptor()["credential_selection"]["version_id"], "selected-version-1")
        self.assertEqual(binding.wait.effect_id, values["wait_original"].original_start_event.event_id)

    def test_wrong_predecessor_projection_or_uncommitted_outcome_never_binds(self):
        values = self.supplied_records()
        waiter = values["plan"].plan.activities[1]
        changes = (
            dict(plan=replace(values["plan"], plan=ActivityPlan((values["plan"].plan.activities[0], replace(waiter, dependencies=()))))),
            dict(current=values["desired"]),
            dict(install_outcome=replace(values["install_outcome"], result=replace(values["install_outcome"].result,
                evidence={**values["install_outcome"].result.evidence, "different-outcome": True}))),
        )
        for change in changes:
            with self.subTest(fields=tuple(change)), self.assertRaises(PostgresReadinessProvenanceUnavailable) as caught:
                bind_original_postgres_readiness(**(values | change))
            self.assertEqual(str(caught.exception), "original PostgreSQL readiness evidence is unavailable")
            self.assertIsNone(caught.exception.__context__)

    def test_pinned_graph_product_identity_cannot_be_substituted_by_matching_contract(self):
        values = self.supplied_records()
        graph = DEFAULT_GRAPH_CODEC.decode(values["desired"].graph_descriptor)
        node = graph.node("api")
        changed = graph.update_node(replace(node, metadata={**node.metadata, "product_descriptor_digest": "f" * 64}))
        desired = projection_record_from_graph("graph-desired", changed, version=2)
        plan = replace(values["plan"], desired_realized_projection_id=desired.projection_id)
        with self.assertRaises(PostgresReadinessProvenanceUnavailable):
            bind_original_postgres_readiness(**(values | dict(desired=desired, plan=plan)))


if __name__ == "__main__":
    unittest.main()
