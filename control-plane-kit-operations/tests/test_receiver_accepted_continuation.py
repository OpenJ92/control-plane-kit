"""#1903 C2 provenance over explicitly recorded accepted-current history."""

from dataclasses import replace
import json
import unittest

from psycopg.types.json import Jsonb

from control_plane_kit_core.node_control import NodeHealthReadKind
from control_plane_kit_core.node_control_surface_reads import (
    WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationProfile,
)
from control_plane_kit_core.receiver_configuration import ReceiverNodeControlConfigurationCodec
from control_plane_kit_core.topology import DeploymentGraph

from control_plane_kit_operations.planning import DesiredGraphCommandError
from control_plane_kit_operations.desired_realized_projections import DesiredRealizedProjectionPublicationError
from tests.receiver_admission_fixture import ReceiverAdmissionFixture
from tests.receiver_recorded_acceptance_fixture import record_accepted_current
from tests.receiver_storage_fixture import RECEIVER


class ReceiverAcceptedContinuationTests(ReceiverAdmissionFixture, unittest.TestCase):
    def test_recorded_accepted_x_continues_while_new_y_gets_its_actual_origin(self):
        record_accepted_current(self)
        origin = self.introduction()
        graph = self.mixed_graph(self.receiver_graph(pretty=True)[0],
            self.receiver_graph(node_id="other", receiver="b" * 32)[0])
        command = self.desired_command(graph=graph, key="accepted-plus-new")
        result = self.desired_service().execute(command)
        self.assertEqual(self.introduction(), origin)
        new = self.introduction(receiver="b" * 32)
        self.assertEqual((new.introducing_graph_id, new.introducing_action_id),
                         (result.graph_version_id, result.action.action_id))
        self.assertIsNone(new.first_accepted_action_id)
        self.assertIsNone(new.retired_action_id)
        self.assertEqual({binding.receiver_id for binding in self.bindings_for_graph(result.graph_version_id)},
                         {RECEIVER, "b" * 32})

    def test_wrong_retained_acceptance_action_cannot_authorize_continuation(self):
        _, action, _ = record_accepted_current(self)
        changed = dict(action.payload) | {"to_realized_projection_digest": "0" * 64}
        self.connection.execute("UPDATE cpk_operation_actions SET payload=%s WHERE action_id=%s",
                                (Jsonb(changed), action.action_id))
        before = self.admission_truth()
        with self.assertRaises(DesiredGraphCommandError):
            self.desired_service().execute(self.desired_command(key="damaged-acceptance"))
        self.assertEqual(self.admission_truth(), before)

    def test_missing_current_binding_cannot_be_replaced_by_introduction_row_alone(self):
        original, _, _ = record_accepted_current(self)
        # A retained source violation is deliberately created only in this
        # transaction, so the deferred original-binding FK never commits it.
        before = self.admission_truth()
        with self.assertRaises(DesiredRealizedProjectionPublicationError):
            with self.unit_of_work() as uow:
                uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
                uow.stores.connection.execute("DELETE FROM cpk_graph_receiver_bindings WHERE graph_id=%s",
                                              (original.graph_version_id,))
                # Publication already supports caller UoW. Its source reads
                # must not accept the surviving introduction as membership.
                from control_plane_kit_operations.desired_realized_projections import (
                    prepare_desired_realized_projection_publication,
                    publish_desired_realized_projection_in_unit_of_work,
                )
                publication = self.publication(key="missing-current-binding")
                prepared = prepare_desired_realized_projection_publication(uow, "workspace-a",
                    publication.session_id, publication.idempotency_key.value)
                publish_desired_realized_projection_in_unit_of_work(uow, publication,
                    prepared=prepared, created_at="2026-09-06T18:02:00Z", action_id="missing-binding-action")
                uow.commit()
        self.assertEqual(self.admission_truth(), before)

    def test_malformed_acceptance_request_locator_refuses_without_database_type_error(self):
        _, action, _ = record_accepted_current(self)
        for locator in (42, True, [], "x" * 2049):
            with self.subTest(locator_type=type(locator).__name__):
                self.connection.execute("UPDATE cpk_operation_actions SET payload=%s WHERE action_id=%s",
                    (Jsonb(dict(action.payload) | {"execution_request_id": locator}), action.action_id))
                before = self.admission_truth()
                with self.assertRaises(DesiredGraphCommandError) as captured:
                    self.desired_service().execute(self.desired_command(key="malformed-receipt"))
                self.assertLess(len(str(captured.exception)), 256)
                self.assertEqual(self.admission_truth(), before)

    def test_relabelled_accepted_scope_and_moved_existing_identity_refuse(self):
        record_accepted_current(self)
        before = self.admission_truth()
        for graph in (self.receiver_graph(receiver="b" * 32)[0], self.receiver_graph(node_id="moved")[0]):
            with self.subTest(node=tuple(graph.nodes)), self.assertRaises(DesiredGraphCommandError):
                self.desired_service().execute(self.desired_command(graph=graph, key="illegal-relabel"))
            self.assertEqual(self.admission_truth(), before)

    def test_recorded_accepted_origin_survives_changed_public_key_and_declaration(self):
        record_accepted_current(self)
        origin = self.introduction()
        graph, artifact, _ = self.receiver_graph()
        node = graph.node("api")
        surface = replace(node.block_spec.control_surfaces[0],
            health_reads=(NodeHealthReadKind.LIVENESS, NodeHealthReadKind.READINESS))
        declaration = WorkloadNodeControlSurfaceDeclaration(surface, WorkloadNodeControlSurfaceDeclarationProfile.V2)
        document = json.loads(artifact.content)
        document["declaration"] = declaration.descriptor()
        for verifier in document["verifiers"]:
            verifier["public_keys"][0]["key_id"] = "replacement-public-key-id"
        codec = ReceiverNodeControlConfigurationCodec()
        updated_artifact = replace(artifact, content=codec.encode_bytes(codec.decode(document)).decode())
        graph = replace(graph, nodes={"api": replace(node,
            block_spec=replace(node.block_spec, control_surfaces=(surface,)),
            configuration_artifacts=(*node.configuration_artifacts[:-1], updated_artifact))})
        result = self.desired_service().execute(self.desired_command(graph=graph, key="changed-declaration"))
        self.assertEqual(self.introduction(), origin)
        binding, = self.bindings_for_graph(result.graph_version_id)
        self.assertEqual(binding.declaration_identity, declaration.identity().value)
        self.assertEqual(binding.selected_configuration_digest, updated_artifact.content_digest)

    def test_old_current_receiver_prevents_missing_expectations_even_when_desired_is_empty(self):
        record_accepted_current(self)
        self.desired_service().execute(self.desired_command(graph=DeploymentGraph("empty"), key="omit-desired"))
        command = replace(self.desired_command(graph=DeploymentGraph("still-empty"), key="old-current"),
                          receiver_lifecycle=None)
        before = self.admission_truth()
        with self.assertRaises(DesiredGraphCommandError):
            self.desired_service().execute(command)
        self.assertEqual(self.admission_truth(), before)

    def test_retired_retained_identity_never_enters_new_introduction_partition(self):
        _, accepted, _ = record_accepted_current(self)
        # Negative retained-state premise only: not a successful retirement
        # transition. C2 must refuse a retired identity even if copied into
        # otherwise selected material; C3 owns lawful retirement production.
        with self.unit_of_work() as uow:
            witness = replace(accepted, action_id="recorded-retirement-marker",
                ordinal=uow.stores.activity_history.next_action_ordinal(accepted.session_id))
            # Copy only this explicitly recorded negative history; the guarded
            # production writer must not publish a fabricated advancement.
            uow.stores.connection.execute(
                "INSERT INTO cpk_operation_actions (action_id,session_id,ordinal,action_type,actor_id,payload,"
                "created_at,idempotency_key,intent_fingerprint,advancement_workspace_id,advancement_request_id,"
                "advancement_plan_id,advancement_run_id,advancement_revision) "
                "SELECT %s,session_id,%s,action_type,actor_id,payload,created_at,idempotency_key,intent_fingerprint,"
                "advancement_workspace_id,advancement_request_id,advancement_plan_id,advancement_run_id,"
                "advancement_revision FROM cpk_operation_actions WHERE action_id=%s",
                (witness.action_id, witness.ordinal, accepted.action_id))
            uow.stores.connection.execute("UPDATE cpk_graph_receiver_introductions SET retired_action_id=%s, "
                "retired_session_id=%s WHERE workspace_id='workspace-a' AND receiver_id=%s",
                (witness.action_id, witness.session_id, RECEIVER))
            uow.commit()
        graph = self.mixed_graph(self.receiver_graph()[0],
            self.receiver_graph(node_id="other", receiver="b" * 32)[0])
        before = self.admission_truth()
        with self.assertRaises(DesiredGraphCommandError):
            self.desired_service().execute(self.desired_command(graph=graph, key="retired-plus-new"))
        self.assertEqual(self.admission_truth(), before)
        self.assertIsNone(self.introduction(receiver="b" * 32))

    def test_original_introducing_action_must_match_the_retained_graph(self):
        original, _, _ = record_accepted_current(self)
        changed = dict(original.action.payload) | {"desired_graph_id": "workspace-a-current"}
        self.connection.execute("UPDATE cpk_operation_actions SET payload=%s WHERE action_id=%s",
                                (Jsonb(changed), original.action.action_id))
        before = self.admission_truth()
        with self.assertRaises(DesiredGraphCommandError):
            self.desired_service().execute(self.desired_command(key="wrong-origin"))
        self.assertEqual(self.admission_truth(), before)
