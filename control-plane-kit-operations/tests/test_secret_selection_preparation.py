"""Gate 1 metadata and authoring laws against the real Operations stores."""

from contextlib import contextmanager
from dataclasses import fields, replace
import unittest
import uuid

from control_plane_kit_core.algebra import DeploymentTopology, DockerRuntime
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.products import (
    ProductDescriptorCodec, ProductIdentity, ProductInstanceConfiguration, ProductReference, instantiate_product,
)
from control_plane_kit_core.secret_selections import SecretVersionSelection
from control_plane_kit_core.secrets import SecretFileDelivery, SecretReference, SecretUseIntent
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, compile_topology
from control_plane_kit_operations import graph_authoring
from control_plane_kit_operations.products import InlineDescriptorSource, RegisteredProductStatus
from control_plane_kit_operations.planning import DesiredGraphCommandService, SetDesiredGraph
from control_plane_kit_operations.workflows import IdempotencyKey
from control_plane_kit_operations.secret_providers import (
    RegisteredSecretReferenceStatus, RevokeSecretReferenceCommand, SecretProviderRegistrationService,
)
from draft_catalogue_fixture import NOW
from draft_selection_fixture import DraftSelectionFixture
from secret_selection_authoring_fixture import admit_selection_reference


class _MetadataReader:
    """One injected observation port; no provider or Operations state machine."""

    def __init__(self, observe):
        self.observe = observe
        self.calls = []

    def read_version(self, request):
        self.calls.append(request)
        return self.observe(request)


class SecretSelectionPreparationFixture(DraftSelectionFixture):
    def require_preparation_api(self):
        for name in ("SecretVersionMetadataRequest", "SecretVersionMetadata"):
            self.assertTrue(callable(getattr(graph_authoring, name, None)),
                f"missing Operations metadata contract: {name}")
        command_fields = {item.name for item in fields(graph_authoring.SetDesiredGraphCommand)}
        self.assertIn("actor_scopes", command_fields, "metadata lookup requires scoped actor input")
        self.assertIn("explicit_secret_choices", command_fields,
            "authoring requires explicit existing-version intent")

    def prepare_fixture(self, *, repeated_delivery=False):
        self.require_preparation_api()
        self.reference = SecretReference("secret://workspace-secrets/workspace-a/token")
        self.intent = SecretUseIntent.APPLICATION_CONTROL_TOKEN
        deliveries = (SecretFileDelivery("/run/secrets/token", self.reference, self.intent),)
        if repeated_delivery:
            deliveries += (SecretFileDelivery("/run/secrets/second-token", self.reference, self.intent),)
        self.secret_product = replace(self.product,
            identity=ProductIdentity("catalogue", "prepared-secret-app", 1),
            runtime_contract=replace(self.product.runtime_contract, secret_deliveries=deliveries))
        document = ProductDescriptorCodec().encode_document(self.secret_product)
        with self.unit_of_work() as uow:
            uow.stores.registered_products.register(workspace_id="workspace-a",
                descriptor_document=document,
                source=InlineDescriptorSource(),
                imported_by="operator-a", imported_at=NOW)
            uow.commit()
        self.provider, self.registered_reference = admit_selection_reference(
            self.unit_of_work, self.reference, self.intent)
        self.open_units = 0
        self.reader = _MetadataReader(self.metadata)
        self.authoring_factory = self.observed_unit_of_work
        self.service = graph_authoring.GraphAuthoringService(self.authoring_factory,
            graph_id_factory=lambda: "prepared-" + uuid.uuid4().hex,
            clock=lambda: NOW, secret_metadata_reader=self.reader)

    @contextmanager
    def observed_unit_of_work(self):
        with self.unit_of_work() as uow:
            self.open_units += 1
            try:
                yield uow
            finally:
                self.open_units -= 1

    def metadata(self, request):
        self.assertEqual(self.open_units, 0, "provider metadata was read inside a transaction")
        self.assertIs(type(request), graph_authoring.SecretVersionMetadataRequest)
        self.assertEqual(request.workspace_id, "workspace-a")
        self.assertEqual(request.actor_subject, "operator-a")
        self.assertEqual(request.provider_registration_id, self.provider.registration_id)
        self.assertEqual(request.reference_registration_id, self.registered_reference.registration_id)
        self.assertEqual(request.endpoint_reference, self.provider.endpoint_reference)
        self.assertEqual(request.credential_reference, self.provider.credential_reference)
        self.assertEqual(request.selection.reference, self.reference)
        self.assertIs(request.selection.intent, self.intent)
        return graph_authoring.SecretVersionMetadata(
            correlation_id=request.correlation_id,
            provider_registration_id=request.provider_registration_id,
            reference_registration_id=request.reference_registration_id,
            selection=request.selection, available=True)

    def unselected_graph(self):
        block = instantiate_product(self.secret_product, "app",
            ProductInstanceConfiguration.from_contract(self.secret_product.runtime_contract))
        return compile_topology(DeploymentTopology("prepared", DockerRuntime(children=(block,))))

    def command(self, version="version-1", *, graph=None, actor_scopes=None):
        graph = self.unselected_graph() if graph is None else graph
        workspace = self.workspace()
        return graph_authoring.SetDesiredGraphCommand(
            workspace_id="workspace-a", actor_id="operator-a", graph=graph,
            expected_desired_graph_id=workspace.desired_graph_id,
            expected_desired_realized_projection_id=workspace.desired_realized_projection_id,
            expected_desired_graph_revision=workspace.desired_graph_revision,
            actor_scopes=(PolicyScope.SECRET_PROVIDER_USE,) if actor_scopes is None else actor_scopes,
            explicit_secret_choices={} if version is None else {
                next(iter(graph.nodes)): (SecretVersionSelection(self.reference, self.intent, version),)},
        )

    def result_choices(self, result):
        graph = DEFAULT_GRAPH_CODEC.decode(result.graph_version.graph_descriptor)
        return next(iter(graph.nodes.values())).secret_selections

    def revoke_authoring_admission(self):
        reference = ProductReference.from_document(ProductDescriptorCodec().encode_document(self.secret_product))
        with self.unit_of_work() as uow:
            revoked = uow.stores.registered_products.revoke("workspace-a", reference)
            self.assertIs(revoked.status, RegisteredProductStatus.REVOKED)
            uow.commit()
        revoked = SecretProviderRegistrationService(self.unit_of_work).revoke_reference(
            RevokeSecretReferenceCommand(workspace_id="workspace-a",
                registration_id=self.registered_reference.registration_id,
                revoked_by="operator-a", revoked_at=NOW,
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REVOKE,)))
        self.assertIs(revoked.status, RegisteredSecretReferenceStatus.REVOKED)


class SecretSelectionPreparationTests(SecretSelectionPreparationFixture, unittest.TestCase):
    def test_explicit_existing_version_is_read_outside_transaction_and_committed_exactly(self):
        self.prepare_fixture(repeated_delivery=True)
        command = self.command()
        original = command.graph.descriptor()
        result = self.service.set_desired_graph(command)
        self.assertEqual(len(self.reader.calls), 1)
        self.assertEqual(self.result_choices(result),
            (SecretVersionSelection(self.reference, self.intent, "version-1"),))
        self.assertEqual(command.graph.descriptor(), original)
        self.assertEqual(self.workspace().desired_graph_id, result.graph_version.graph_id)
        self.assertEqual(self.open_units, 0)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.graphs.get(result.graph_version.graph_id), result.graph_version)

    def test_compatible_new_revision_carries_exact_choice_without_metadata_call(self):
        self.prepare_fixture()
        first = self.service.set_desired_graph(self.command())
        self.reader.calls.clear()
        second = self.service.set_desired_graph(self.command(version=None))
        self.assertEqual(self.reader.calls, [])
        self.assertEqual(self.result_choices(second), self.result_choices(first))
        self.assertNotEqual(second.graph_version.graph_id, first.graph_version.graph_id)

    def test_explicit_version_change_reads_new_version_without_rewriting_prior_revision(self):
        self.prepare_fixture()
        first = self.service.set_desired_graph(self.command())
        second = self.service.set_desired_graph(self.command(version="version-2"))
        self.assertEqual([item.selection.version_id for item in self.reader.calls],
            ["version-1", "version-2"])
        self.assertEqual(self.result_choices(second)[0].version_id, "version-2")
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.graphs.get(first.graph_version.graph_id), first.graph_version)

    def test_saved_selection_replay_preserves_original_version_after_desired_change(self):
        self.prepare_fixture()
        first = self.service.set_desired_graph(self.command())
        graph = DEFAULT_GRAPH_CODEC.decode(first.graph_version.graph_descriptor)
        draft = self.catalogue().execute(self.create_command(graph=graph))
        command = self.select_command(draft)
        selected = self.catalogue().execute(command)
        changed = self.service.set_desired_graph(self.command(version="version-2"))
        self.assertEqual(self.result_choices(changed)[0].version_id, "version-2")
        self.assertEqual(self.workspace().desired_graph_id, changed.graph_version.graph_id)
        self.revoke_authoring_admission()
        self.reader.calls.clear()
        before = self.truth()
        self.assertEqual(self.catalogue().execute(command), selected)
        self.assertEqual(self.truth(), before)
        self.assertEqual(self.reader.calls, [])
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.graphs.get(draft.graph_id).graph_descriptor,
                first.graph_version.graph_descriptor)

    def test_planning_carry_forward_replay_uses_original_revision_after_desired_change(self):
        self.prepare_fixture()
        self.service.set_desired_graph(self.command())
        workspace = self.workspace()
        command = SetDesiredGraph(session_id=self.sessions["workspace-a"],
            workspace_id="workspace-a", actor_id="operator-a", graph=self.unselected_graph(),
            expected_desired_graph_id=workspace.desired_graph_id,
            expected_desired_realized_projection_id=workspace.desired_realized_projection_id,
            expected_desired_graph_revision=workspace.desired_graph_revision,
            idempotency_key=IdempotencyKey("carried-version"))
        service = DesiredGraphCommandService(self.unit_of_work,
            clock=lambda: NOW, id_factory=lambda: uuid.uuid4().hex)
        self.reader.calls.clear()
        first = service.execute(command)
        self.assertEqual(self.reader.calls, [])
        with self.unit_of_work() as uow:
            original = uow.stores.graphs.get(first.graph_version_id)
            graph = DEFAULT_GRAPH_CODEC.decode(original.graph_descriptor)
            self.assertEqual(next(iter(graph.nodes.values())).secret_selections[0].version_id,
                "version-1")
        changed = self.service.set_desired_graph(self.command(version="version-2"))
        self.assertEqual(self.result_choices(changed)[0].version_id, "version-2")
        self.assertEqual(self.workspace().desired_graph_id, changed.graph_version.graph_id)
        self.revoke_authoring_admission()
        self.reader.calls.clear()
        before = self.truth()
        replay = service.execute(command)
        self.assertEqual(replay, replace(first, replayed=True))
        self.assertEqual(self.reader.calls, [])
        self.assertEqual(self.truth(), before)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.graphs.get(first.graph_version_id), original)

    def test_missing_metadata_scope_refuses_before_reader_and_without_writes(self):
        self.prepare_fixture()
        command = self.command(actor_scopes=())
        before = self.truth()
        with self.assertRaises(graph_authoring.GraphAuthoringError):
            self.service.set_desired_graph(command)
        self.assertEqual(self.reader.calls, [])
        self.assertEqual(self.truth(), before)

    def test_absent_metadata_reader_refuses_explicit_choice_without_writes(self):
        self.prepare_fixture()
        service = graph_authoring.GraphAuthoringService(self.observed_unit_of_work,
            graph_id_factory=lambda: "no-reader", clock=lambda: NOW)
        before = self.truth()
        with self.assertRaises(graph_authoring.GraphAuthoringError):
            service.set_desired_graph(self.command())
        self.assertEqual(self.reader.calls, [])
        self.assertEqual(self.truth(), before)

    def test_invalid_choice_target_or_use_refuses_before_metadata_io(self):
        self.prepare_fixture()
        graph = self.unselected_graph()
        node_id = next(iter(graph.nodes))
        for choices in (
            {"foreign-node": (SecretVersionSelection(self.reference, self.intent, "version-1"),)},
            {node_id: (SecretVersionSelection(self.reference, SecretUseIntent.POSTGRES_PASSWORD, "version-1"),)},
            {node_id: (SecretVersionSelection(SecretReference("secret://foreign/token"), self.intent, "version-1"),)},
        ):
            with self.subTest(choices=choices):
                before = self.truth()
                with self.assertRaises(graph_authoring.GraphAuthoringError):
                    command = replace(self.command(graph=graph), explicit_secret_choices=choices)
                    self.service.set_desired_graph(command)
                self.assertEqual(self.reader.calls, [])
                self.assertEqual(self.truth(), before)

    def test_caller_choice_mapping_is_snapshotted_before_metadata_read(self):
        self.prepare_fixture()
        command = self.command()
        choices = dict(command.explicit_secret_choices)
        command = replace(command, explicit_secret_choices=choices)
        node_id = next(iter(choices))
        original = choices[node_id]
        def observed(request):
            response = self.metadata(request)
            choices[node_id] = (SecretVersionSelection(self.reference, self.intent, "version-other"),)
            return response
        self.reader.observe = observed
        result = self.service.set_desired_graph(command)
        self.assertEqual(self.result_choices(result), original)
        self.assertEqual(command.explicit_secret_choices[node_id], original)
        self.assertEqual(self.reader.calls[0].selection.version_id, "version-1")

    def test_revoked_reference_blocks_carry_forward_without_metadata_io(self):
        self.prepare_fixture()
        self.service.set_desired_graph(self.command())
        self.reader.calls.clear()
        SecretProviderRegistrationService(self.unit_of_work).revoke_reference(
            RevokeSecretReferenceCommand(workspace_id="workspace-a",
                registration_id=self.registered_reference.registration_id,
                revoked_by="operator-a", revoked_at=NOW,
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REVOKE,)))
        before = self.truth()
        with self.assertRaises(graph_authoring.GraphAuthoringError):
            self.service.set_desired_graph(self.command(version=None))
        self.assertEqual(self.reader.calls, [])
        self.assertEqual(self.truth(), before)

    def test_unavailable_or_mismatched_metadata_cannot_publish_a_selection(self):
        self.prepare_fixture()
        for changes in ({"available": False}, {"correlation_id": "foreign-correlation"},
                {"provider_registration_id": "foreign-provider"},
                {"reference_registration_id": "foreign-reference"},
                {"selection": SecretVersionSelection(self.reference, self.intent, "version-other")}):
            with self.subTest(changes=changes):
                self.reader.observe = lambda request: replace(self.metadata(request), **changes)
                before = self.truth()
                calls_before = len(self.reader.calls)
                with self.assertRaises(graph_authoring.GraphAuthoringError):
                    self.service.set_desired_graph(self.command())
                self.assertEqual(len(self.reader.calls), calls_before + 1)
                self.assertEqual(self.truth(), before)
                self.assertEqual(self.open_units, 0)

    def test_metadata_failure_then_retry_performs_a_new_read(self):
        self.prepare_fixture()
        command = self.command()
        before = self.truth()
        def unavailable(request):
            self.metadata(request)
            raise RuntimeError("provider observation unavailable")
        self.reader.observe = unavailable
        with self.assertRaises(graph_authoring.GraphAuthoringError):
            self.service.set_desired_graph(command)
        self.assertEqual(self.truth(), before)
        self.reader.observe = self.metadata
        result = self.service.set_desired_graph(command)
        self.assertEqual(len(self.reader.calls), 2)
        self.assertEqual(self.result_choices(result)[0].version_id, "version-1")

    def test_desired_revision_race_after_metadata_refuses_without_partial_publication(self):
        self.prepare_fixture()
        command = self.command()
        after_race = []
        def observed(request):
            response = self.metadata(request)
            with self.unit_of_work() as uow:
                workspace = uow.stores.workspaces.get_for_update("workspace-a")
                uow.stores.workspaces.set_desired_graph("workspace-a", workspace.desired_graph_id)
                uow.commit()
            after_race.append(self.truth())
            return response
        self.reader.observe = observed
        with self.assertRaises(graph_authoring.GraphAuthoringError):
            self.service.set_desired_graph(command)
        self.assertEqual(len(after_race), 1)
        self.assertEqual(self.workspace().desired_graph_revision,
            command.expected_desired_graph_revision + 1)
        self.assertEqual(self.truth(), after_race[0])

    def test_reference_revocation_after_metadata_refuses_without_graph_write(self):
        self.prepare_fixture()
        before = self.truth()
        def observed(request):
            response = self.metadata(request)
            SecretProviderRegistrationService(self.unit_of_work).revoke_reference(
                RevokeSecretReferenceCommand(workspace_id="workspace-a",
                    registration_id=self.registered_reference.registration_id,
                    revoked_by="operator-a", revoked_at=NOW,
                    actor_scopes=(PolicyScope.SECRET_PROVIDER_REVOKE,)))
            return response
        self.reader.observe = observed
        with self.assertRaises(graph_authoring.GraphAuthoringError):
            self.service.set_desired_graph(self.command())
        self.assertEqual(len(self.reader.calls), 1)
        with self.unit_of_work() as uow:
            reference = uow.stores.secret_references.get_by_registration(
                "workspace-a", self.registered_reference.registration_id)
            self.assertIs(reference.status, RegisteredSecretReferenceStatus.REVOKED)
        self.assertEqual(self.truth(), before)

    def test_product_revocation_after_metadata_refuses_without_graph_write(self):
        self.prepare_fixture()
        before = self.truth()
        revoked = []
        reference = ProductReference.from_document(ProductDescriptorCodec().encode_document(self.secret_product))
        def observed(request):
            response = self.metadata(request)
            with self.unit_of_work() as uow:
                revoked.append(uow.stores.registered_products.revoke("workspace-a", reference))
                uow.commit()
            return response
        self.reader.observe = observed
        with self.assertRaises(graph_authoring.GraphAuthoringError):
            self.service.set_desired_graph(self.command())
        self.assertEqual(len(self.reader.calls), 1)
        self.assertEqual(len(revoked), 1)
        self.assertIs(revoked[0].status, RegisteredProductStatus.REVOKED)
        self.assertEqual(self.truth(), before)

    def test_mutated_effective_delivery_during_metadata_read_invalidates_preparation(self):
        self.prepare_fixture()
        command = self.command()
        node_id = next(iter(command.graph.nodes))
        before = self.truth()
        def observed(request):
            response = self.metadata(request)
            node = command.graph.nodes[node_id]
            command.graph.nodes[node_id] = replace(node, secret_deliveries=(
                replace(node.secret_deliveries[0], target_path="/run/secrets/changed"),))
            return response
        self.reader.observe = observed
        with self.assertRaises(graph_authoring.GraphAuthoringError):
            self.service.set_desired_graph(command)
        self.assertEqual(len(self.reader.calls), 1)
        self.assertEqual(command.graph.nodes[node_id].secret_deliveries[0].target_path,
            "/run/secrets/changed")
        self.assertEqual(self.truth(), before)


if __name__ == "__main__":
    unittest.main()
