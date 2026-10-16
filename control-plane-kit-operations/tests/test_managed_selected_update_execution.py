"""#1991 public selected-consumer preparation over real Operations history.

Uses the existing recording effect ports and execution loop, not live providers.
Every receiver origin is introduced by the actual public draft command.
"""
from dataclasses import replace
import json

from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.planning import ManagementBootstrapStage, ObserveManagementBootstrap, ReconcileNode, StartNode
from control_plane_kit_core.products import ProductDescriptorCodec
from control_plane_kit_core.secret_selections import SecretVersionSelection
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_operations.cpk_server import CpkServerApplicationError
from control_plane_kit_operations.graph_authoring import SecretVersionMetadata
from tests import test_managed_application_chain as chain
from tests.secret_selection_authoring_fixture import admit_selection_reference
from tests.test_managed_configuration_update_admission import APPLICATION
from tests.test_managed_configuration_update_execution import ManagedConfigurationUpdateExecutionTests
from tests.test_managed_selected_update_admission import DELIVERY


class ManagedSelectedUpdateExecutionTests(chain.ManagedApplicationFixture):
    application_artifacts = (APPLICATION,)
    application_deliveries = (DELIVERY,)
    complete_initial = chain.ManagedUpdateExecutionTests.complete_initial
    prepare_content = ManagedConfigurationUpdateExecutionTests.prepare_content
    execute_content = ManagedConfigurationUpdateExecutionTests.execute_content
    test_missing_replacement_receipt_preserves_uncertainty_and_original_retry = (
        ManagedConfigurationUpdateExecutionTests.test_missing_replacement_receipt_preserves_uncertainty_and_original_retry)

    def setUp(self):
        super().setUp()
        self.metadata_reader = self
        self.metadata_calls = []
        self.family = "version"

    def authored_graph(self):
        graph, documents, registry = super().authored_graph()
        for name, node in tuple(graph.nodes.items()):
            graph = graph.update_node(replace(node, metadata={**node.metadata,
                "oci_image": documents[name].product.image.execution_reference}))
        graph = DEFAULT_GRAPH_CODEC.decode(DEFAULT_GRAPH_CODEC.encode(graph))
        # Choose the existing ordering fixture with the eventual admitted input.
        # Return it unselected; only the public metadata-backed command selects it.
        selected = graph.update_node(replace(graph.node("api"), secret_selections=(
            SecretVersionSelection(DELIVERY.reference, DELIVERY.intent, "version-1"),)))
        ordered = self.native_before_path_graph(selected)
        graph = replace(graph, public_ingresses=ordered.public_ingresses)
        return graph, documents, registry

    def read_version(self, request):
        self.assertEqual(self.tracker.active, 0)
        self.assertEqual(request.provider_registration_id, self.consumer_provider.registration_id)
        self.assertEqual(request.reference_registration_id, self.consumer_reference.registration_id)
        self.assertEqual(request.selection.reference, DELIVERY.reference)
        self.assertEqual(request.selection.intent, DELIVERY.intent)
        self.metadata_calls.append(request)
        return SecretVersionMetadata(correlation_id=request.correlation_id,
            provider_registration_id=request.provider_registration_id,
            reference_registration_id=request.reference_registration_id,
            selection=request.selection, available=True)

    async def context(self, **values):
        return await self.invoke("read.receiver-authoring-context", ControlPlaneServiceRole.READS,
            path={"workspace_id": "workspace-a"}, payload=values)

    async def draft_command(self, verb, **values):
        context = await self.context()
        return await self.invoke("command.desired-topology-draft." + verb,
            ControlPlaneServiceRole.PLANNING, path={"workspace_id": "workspace-a"}, payload={
                "session_id": self.consumer_session,
                "receiver_lifecycle": context["expectation"], **values})

    async def select_draft(self, revision, key):
        pins = (await self.context())["expectation"]
        return await self.draft_command("select", draft_id=revision["draft_id"],
            revision=revision["revision"], expected_desired_graph_id=pins["desired_graph_id"],
            expected_desired_realized_projection_id=pins["desired_realized_projection_id"],
            expected_desired_graph_revision=pins["desired_graph_revision"], idempotency_key=key)

    async def select_version(self, graph, version, key):
        pins = (await self.context())["expectation"]
        selection = SecretVersionSelection(DELIVERY.reference, DELIVERY.intent, version)
        result = await self.invoke("command.desired-graph.set", ControlPlaneServiceRole.PLANNING,
            path={"workspace_id": "workspace-a"}, payload={
                "session_id": self.consumer_session, "graph": DEFAULT_GRAPH_CODEC.encode(graph),
                "expected_desired_graph_id": pins["desired_graph_id"],
                "expected_desired_realized_projection_id": pins["desired_realized_projection_id"],
                "expected_desired_graph_revision": pins["desired_graph_revision"],
                "receiver_lifecycle": pins, "idempotency_key": key,
                "explicit_secret_choices": {"api": [selection.descriptor()]}})
        # Author from declared input and the admitted explicit choice, never a redacted read.
        selected = graph.update_node(replace(graph.node("api"), secret_selections=(selection,)))
        with self.unit_of_work() as uow:
            stored = uow.stores.graphs.get(result["desired_graph_id"])
        self.assertEqual(DEFAULT_GRAPH_CODEC.decode(stored.graph_descriptor).node("api").secret_selections,
            selected.node("api").secret_selections)
        return selected

    async def author_initial_desired(self):
        self.consumer_provider, self.consumer_reference = admit_selection_reference(
            self.unit_of_work, DELIVERY.reference, DELIVERY.intent)
        session = await self.invoke("command.operation-session.start", ControlPlaneServiceRole.LIFECYCLE,
            path={"workspace_id": "workspace-a"}, payload={
                "title": "Initial selected consumer", "idempotency_key": "consumer-session"})
        self.consumer_session = session["session_id"]
        original_context = await self.context()
        first = await self.draft_command("create", graph=DEFAULT_GRAPH_CODEC.encode(self.graph),
            title="Consumer from outset", idempotency_key="consumer-draft")
        with self.unit_of_work() as uow:
            original_revision = uow.stores.graphs.get(first["graph_id"])
        await self.select_draft(first, "select-consumer-unselected")
        first_context = await self.context(pending_draft={"draft_id": first["draft_id"], "expected_head_revision": 1})
        self.graph = await self.select_version(self.graph, "version-1", "initial-consumer-version")
        second = await self.draft_command("revise", graph=DEFAULT_GRAPH_CODEC.encode(self.graph),
            draft_id=first["draft_id"], expected_head_revision=1, idempotency_key="selected-consumer-revision")
        self.assertEqual(second["revision"], 2)
        await self.select_draft(second, "select-consumer-selected")
        context = await self.context(pending_draft={"draft_id": first["draft_id"], "expected_head_revision": 2})
        self.assertEqual(context["current"], original_context["current"])
        self.assertEqual(context["pending_draft"]["graph_id"], second["graph_id"])
        for original, pending in zip(first_context["pending_draft"]["receivers"],
                context["pending_draft"]["receivers"], strict=True):
            self.assertEqual(original["origin"], pending["origin"])
            self.assertEqual(original["configuration_artifact"], pending["configuration_artifact"])
            self.assertEqual(pending["origin"]["introducing_graph_id"], first["graph_id"])
            self.assertEqual(pending["origin"]["introducing_draft_id"], first["draft_id"])
            self.assertEqual(pending["binding"]["graph_id"], second["graph_id"])
            self.assertEqual(pending["binding"]["realized_projection_id"], context["pending_draft"]["realized_projection_id"])
            self.assertEqual(pending["lifecycle"], "pending")
        with self.unit_of_work() as uow:
            first_record = uow.stores.graphs.get(first["graph_id"])
            second_record = uow.stores.graphs.get(second["graph_id"])
        self.assertEqual(first_record, original_revision)
        self.assertEqual(DEFAULT_GRAPH_CODEC.decode(second_record.graph_descriptor).node("api").secret_selections,
            self.graph.node("api").secret_selections)
        self.assertIsNone(DEFAULT_GRAPH_CODEC.decode(first_record.graph_descriptor).node("api").secret_selections[0].descriptor()["version_id"])
        before = self.durable_snapshot(), self.effects()
        with self.assertRaises(CpkServerApplicationError):
            await self.context(pending_draft={"draft_id": first["draft_id"], "expected_head_revision": 1})
        with self.assertRaises(CpkServerApplicationError):
            await self.context(expected=first_context["expectation"])
        self.assertEqual((self.durable_snapshot(), self.effects()), before)
        self.assertEqual(self.runtime.calls, [])
        self.assertEqual(self.effects(), ((), (), (), (), ()))
        pins = context["expectation"]
        self.workspace = {**self.workspace, "desired_graph_revision": pins["desired_graph_revision"]}
        self.initial_selected_draft = second
        self.initial_receiver_context = context
        return {"authored_graph_id": pins["desired_graph_id"],
            "realized_projection_id": pins["desired_realized_projection_id"]}

    def check_initial_prepared(self):
        with self.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get("workspace-a")
            prepared = uow.stores.graphs.get(self.plan_descriptor["desired_graph_id"])
            bindings = uow.stores.graphs.receiver_bindings("workspace-a", workspace.desired_graph_id,
                workspace.desired_realized_projection_id)
            introductions = tuple(uow.stores.graphs.receiver_introduction("workspace-a", value.receiver_id)
                for value in bindings)
        self.assertEqual(workspace.desired_graph_id, prepared.graph_id)
        self.assertNotEqual(prepared.graph_id, self.initial_selected_draft["graph_id"])
        self.assertEqual(DEFAULT_GRAPH_CODEC.decode(prepared.graph_descriptor).node("api"), self.graph.node("api"))
        self.assertEqual(workspace.current_graph_id, self.workspace["current_graph_id"])
        expected = {value["binding"]["receiver_id"]: value
            for value in self.initial_receiver_context["pending_draft"]["receivers"]}
        self.assertEqual(len(bindings), len(expected))
        self.assertEqual({value.receiver_id for value in bindings}, set(expected))
        origins = {value.receiver_id: value for value in introductions}
        for binding in bindings:
            original = expected[binding.receiver_id]
            for name, value in original["binding"].items():
                if name not in ("graph_id", "realized_projection_id"):
                    self.assertEqual(getattr(binding, name), value)
            for name, value in original["origin"].items():
                self.assertEqual(getattr(origins[binding.receiver_id], name), value)
            self.assertEqual((binding.graph_id, binding.realized_projection_id),
                (workspace.desired_graph_id, workspace.desired_realized_projection_id))
        with self.unit_of_work() as uow:
            actual = uow.stores.activity_history.get_plan(self.plan_id).plan
        stages = {value.operation.stage: value for value in actual.activities
            if type(value.operation) is ObserveManagementBootstrap}
        self.assertLess(actual.activities.index(stages[ManagementBootstrapStage.CONNECTOR_CONNECTED]),
            actual.activities.index(stages[ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH]),
            "selected fixture must retain native-before-PATH ordering without changing production dependencies")
        self.assertEqual(self.runtime.calls, [])
        self.assertEqual(self.effects(), ((), (), (), (), ()))

    async def desired_update(self):
        if self.family == "version":
            return await self.select_version(self.graph, "version-2", "replacement-consumer-version")
        if self.family == "content":
            return await ManagedConfigurationUpdateExecutionTests.desired_update(self)
        original = self.documents["api"].product
        successor = replace(original, identity=replace(original.identity, contract_revision=2),
            image=replace(original.image, digest="sha256:" + "b" * 64))
        document = ProductDescriptorCodec().encode_document(successor)
        await self.invoke("command.product.import", ControlPlaneServiceRole.PLANNING,
            path={"workspace_id": "workspace-a"}, payload={"descriptor_document": json.loads(document.content),
                "imported_at": chain.now(), "idempotency_key": "consumer-image"})
        return self.graph.update_node(replace(self.graph.node("api"), metadata={
            "product_identity": successor.identity.key, "product_descriptor_digest": document.content_digest,
            "oci_image": successor.image.execution_reference}))

    async def test_public_version_selection_prepares_executes_and_replays_exact_update(self):
        await self.prepare_content()
        result = await self.execute_content()
        self.assertEqual((result["coordinator_status"], result["run_status"]), ("completed", "succeeded"))
        update, = self.runtime.calls[len(self.origin_calls):]
        self.assertIs(type(update.operation), ReconcileNode)
        material, = update.products
        self.assertEqual(material.secret_selections[0].version_id, "version-2")
        self.assertEqual([request.selection.version_id for request in self.metadata_calls], ["version-1", "version-2"])
        self.assertEqual(self.history(), self.origin_history)
        original, = (value for value in self.origin_calls if type(value.operation) is StartNode
            and value.operation.target.node_id == "api")
        self.assertEqual(material.product.image, original.products[0].product.image)
        self.assertEqual(material.product.runtime_contract.configuration_artifacts,
            original.products[0].product.runtime_contract.configuration_artifacts)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.workspaces.get("workspace-a").current_graph_id,
                self.before_update.current_graph_id)
        plan = self.update_plan
        advanced = await self.invoke("command.graph.advance-current", ControlPlaneServiceRole.LIFECYCLE,
            path={"workspace_id": "workspace-a", "run_id": self.update_run}, principal=self.worker,
            payload={"plan_id": self.record.plan_id,
                "expected_current_graph_id": self.before_update.current_graph_id,
                "expected_current_realized_projection_id": self.before_update.current_realized_projection_id,
                "desired_graph_id": plan["desired_graph_id"],
                "desired_realized_projection_id": plan["desired_realized_projection_id"],
                "expected_desired_graph_revision": plan["desired_graph_revision"],
                "claim_generation": self.update_generation, "idempotency_key": "advance-selected-version"})
        self.assertEqual(advanced["to_graph_id"], plan["desired_graph_id"])
        with self.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get("workspace-a")
            accepted = uow.stores.graphs.get(workspace.current_graph_id)
        self.assertEqual(workspace.current_graph_id, plan["desired_graph_id"])
        self.assertEqual(DEFAULT_GRAPH_CODEC.decode(accepted.graph_descriptor).node("api").secret_selections,
            material.secret_selections)

    async def test_existing_content_carries_exact_selected_version(self):
        self.family = "content"
        await self.prepare_content()
        result = await self.execute_content()
        self.assertEqual(result["coordinator_status"], "completed")
        update, = self.runtime.calls[len(self.origin_calls):]
        self.assertEqual(update.products[0].secret_selections[0].version_id, "version-1")
        application, = (value for value in update.products[0].product.runtime_contract.configuration_artifacts
            if value.artifact_id == "application")
        self.assertEqual(application.content, '{"mode":"green"}')
        self.assertNotEqual(application.content_digest, APPLICATION.content_digest)
        self.assertEqual(len(self.metadata_calls), 1)

    async def test_immutable_image_carries_exact_selected_version(self):
        self.family = "image"
        await self.prepare_content()
        result = await self.execute_content()
        self.assertEqual(result["coordinator_status"], "completed")
        update, = self.runtime.calls[len(self.origin_calls):]
        self.assertEqual(update.products[0].secret_selections[0].version_id, "version-1")
        self.assertEqual(update.products[0].product.image.digest, "sha256:" + "b" * 64)
        self.assertEqual(update.products[0].product.runtime_contract.configuration_artifacts,
            self.documents["api"].product.runtime_contract.configuration_artifacts)
        self.assertEqual(len(self.metadata_calls), 1)
