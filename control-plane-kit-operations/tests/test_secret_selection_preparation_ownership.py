"""Actual owner-issued preparation lifetime; no replacement token issuer."""

from copy import copy
from dataclasses import replace
import unittest

from control_plane_kit_core.secret_selections import SecretVersionSelection
from control_plane_kit_operations import graph_authoring
from draft_catalogue_fixture import NOW
from test_secret_selection_preparation import SecretSelectionPreparationFixture


class SecretSelectionPreparationOwnershipTests(SecretSelectionPreparationFixture, unittest.TestCase):
    def prepare_owner_fixture(self):
        self.prepare_fixture()
        for name in ("_prepare_secret_selections", "_secret_selection_unit_of_work"):
            self.assertTrue(callable(getattr(self.service, name, None)),
                f"missing actual authoring preparation boundary: {name}")

    def publish(self, uow, prepared, *, command=None, graph_id="witness-graph", guard=None):
        command = prepared.command if command is None else command
        guard = (uow.stores.graphs.lock_receiver_lifecycle(command.workspace_id)
            if guard is None else guard)
        return graph_authoring.set_desired_graph_in_unit_of_work(uow, command,
            lifecycle_guard=guard, graph_id=graph_id, created_at=NOW,
            _secret_preparation=prepared)

    def assert_no_insert(self, uow, graph_id="witness-graph"):
        with self.assertRaises(KeyError):
            uow.stores.graphs.get(graph_id)

    def test_same_live_owner_and_transaction_can_publish_prepared_version_once(self):
        self.prepare_owner_fixture()
        with self.service._prepare_secret_selections(self.command()) as prepared:
            with self.service._secret_selection_unit_of_work(prepared) as uow:
                result = self.publish(uow, prepared)
                uow.commit()
        self.assertEqual(self.result_choices(result)[0].version_id, "version-1")
        self.assertEqual(self.workspace().desired_graph_id, result.graph_version.graph_id)
        self.assertEqual(len(self.reader.calls), 1)

    def test_equal_copy_is_not_issued_and_refuses_before_catch_and_commit(self):
        self.prepare_owner_fixture()
        before = self.truth()
        with self.service._prepare_secret_selections(self.command()) as prepared:
            copied = copy(prepared)
            self.assertIsNot(copied, prepared)
            with self.service._secret_selection_unit_of_work(prepared) as uow:
                with self.assertRaises(graph_authoring.GraphAuthoringError):
                    self.publish(uow, copied)
                self.assert_no_insert(uow)
                uow.commit()
        self.assertEqual(self.truth(), before)

    def test_actor_workspace_or_material_substitution_refuses_before_catch_and_commit(self):
        self.prepare_owner_fixture()
        for change in ("actor", "workspace", "material"):
            with self.subTest(change=change):
                before = self.truth()
                with self.service._prepare_secret_selections(self.command()) as prepared:
                    command = prepared.command
                    if change == "actor":
                        command = replace(command, actor_id="operator-b")
                    elif change == "workspace":
                        foreign = self.workspace_for("workspace-b")
                        command = replace(command, workspace_id="workspace-b",
                            expected_desired_graph_id=foreign.desired_graph_id,
                            expected_desired_realized_projection_id=foreign.desired_realized_projection_id,
                            expected_desired_graph_revision=foreign.desired_graph_revision)
                    else:
                        node_id = next(iter(command.graph.nodes))
                        node = command.graph.nodes[node_id]
                        graph = command.graph.update_node(replace(node, secret_selections=(
                            SecretVersionSelection(self.reference, self.intent, "version-other"),)))
                        command = replace(command, graph=graph)
                    with self.service._secret_selection_unit_of_work(prepared) as uow:
                        with self.assertRaises(graph_authoring.GraphAuthoringError):
                            self.publish(uow, prepared, command=command)
                        self.assert_no_insert(uow)
                        uow.commit()
                self.assertEqual(self.truth(), before)

    def workspace_for(self, workspace_id):
        with self.unit_of_work() as uow:
            return uow.stores.workspaces.get(workspace_id)

    def test_other_service_cannot_bind_the_issuing_services_preparation(self):
        self.prepare_owner_fixture()
        other = graph_authoring.GraphAuthoringService(self.authoring_factory,
            graph_id_factory=lambda: "other-owner", clock=lambda: NOW,
            secret_metadata_reader=self.reader)
        before = self.truth()
        yielded = []
        with self.service._prepare_secret_selections(self.command()) as prepared:
            with self.assertRaises(graph_authoring.GraphAuthoringError):
                with other._secret_selection_unit_of_work(prepared) as uow:
                    yielded.append(uow)
                    self.publish(uow, prepared)
                    uow.commit()
        self.assertEqual(yielded, [], "a foreign issuance owner must refuse before yielding a UoW")
        self.assertEqual(self.truth(), before)

    def test_unbound_transaction_cannot_consume_an_issued_preparation(self):
        self.prepare_owner_fixture()
        before = self.truth()
        with self.service._prepare_secret_selections(self.command()) as prepared:
            with self.unit_of_work() as uow:
                with self.assertRaises(graph_authoring.GraphAuthoringError):
                    self.publish(uow, prepared)
                self.assert_no_insert(uow)
                uow.commit()
        self.assertEqual(self.truth(), before)

    def test_second_consumption_in_same_bound_transaction_adds_no_write(self):
        self.prepare_owner_fixture()
        statements = []
        self.service = graph_authoring.GraphAuthoringService(self.observed_uow(statements),
            graph_id_factory=lambda: "single-use-witness", clock=lambda: NOW,
            secret_metadata_reader=self.reader)
        with self.service._prepare_secret_selections(self.command()) as prepared:
            with self.service._secret_selection_unit_of_work(prepared) as uow:
                guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
                first = self.publish(uow, prepared, guard=guard)
                workspace = uow.stores.workspaces.get("workspace-a")
                statements.clear()
                with self.assertRaises(graph_authoring.GraphAuthoringError):
                    self.publish(uow, prepared, graph_id="second-consumption", guard=guard)
                self.assertEqual(statements, [],
                    "second consumption must refuse before a stale-CAS read could hide witness reuse")
                self.assert_no_insert(uow, "second-consumption")
                self.assertEqual(uow.stores.workspaces.get("workspace-a"), workspace)
                uow.commit()
        self.assertEqual(self.workspace().desired_graph_id, first.graph_version.graph_id)

    def test_normal_or_exceptional_scope_exit_expires_the_real_issued_object(self):
        self.prepare_owner_fixture()
        for exceptional in (False, True):
            with self.subTest(exceptional=exceptional):
                before = self.truth()
                prepared = None
                try:
                    with self.service._prepare_secret_selections(self.command()) as prepared:
                        if exceptional:
                            raise RuntimeError("owner caller cancelled")
                except RuntimeError as error:
                    self.assertEqual(str(error), "owner caller cancelled")
                self.assertIsNotNone(prepared)
                with self.assertRaises(graph_authoring.GraphAuthoringError):
                    with self.service._secret_selection_unit_of_work(prepared) as uow:
                        self.publish(uow, prepared)
                        uow.commit()
                self.assertEqual(self.truth(), before)

    def test_stale_store_refusal_consumes_witness_before_same_context_reuse(self):
        self.prepare_owner_fixture()
        statements = []
        self.service = graph_authoring.GraphAuthoringService(self.observed_uow(statements),
            graph_id_factory=lambda: "stale-witness", clock=lambda: NOW,
            secret_metadata_reader=self.reader)
        with self.service._prepare_secret_selections(self.command()) as prepared:
            with self.unit_of_work() as concurrent:
                workspace = concurrent.stores.workspaces.get_for_update("workspace-a")
                concurrent.stores.workspaces.set_desired_graph("workspace-a", workspace.desired_graph_id)
                concurrent.commit()
            before = self.truth()
            with self.service._secret_selection_unit_of_work(prepared) as uow:
                guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
                with self.assertRaises(graph_authoring.GraphAuthoringError):
                    self.publish(uow, prepared, guard=guard)
                self.assert_no_insert(uow)
                statements.clear()
                with self.assertRaises(graph_authoring.GraphAuthoringError):
                    self.publish(uow, prepared, graph_id="reused-stale", guard=guard)
                self.assertEqual(statements, [],
                    "consumed witness must refuse before sampling current database truth again")
                self.assert_no_insert(uow, "reused-stale")
                uow.commit()
        self.assertEqual(self.truth(), before)


if __name__ == "__main__":
    unittest.main()
