"""#1897 B1–B5/G2/U1/L1: staged storage integrity, not C authorization."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
import json
import threading
import unittest

import psycopg
from psycopg.types.json import Jsonb

from control_plane_kit_core.topology import DeploymentGraph, validate_graph
from control_plane_kit_core.configuration import ConfigurationMediaType
from control_plane_kit_core.node_control_surface_reads import (
    WorkloadNodeControlSurfaceDeclaration, WorkloadNodeControlSurfaceDeclarationProfile,
)
from control_plane_kit_core.wrapper_configuration import WorkloadNodeControlConfigurationCodec
from control_plane_kit_operations.postgres import install_schema
from control_plane_kit_operations.postgres.schema import SchemaInstallationError
from control_plane_kit_operations.records import GraphVersionRecord, RealizedGraphProjectionRecord
from tests.draft_catalogue_fixture import NOW
from tests.receiver_storage_fixture import ReceiverStorageFixture, RECEIVER
from tests.health_receiver_trust_fixture import artifact as legacy_artifact


class PostgresReceiverStorageTests(ReceiverStorageFixture, unittest.TestCase):
    def test_distinct_legacy_and_receiver_nodes_preserve_legacy_material(self):
        first = self.receiver_graph()[0]
        other = self.receiver_graph(node_id="legacy", receiver="b" * 32)[0].node("legacy")
        declaration = WorkloadNodeControlSurfaceDeclaration(
            other.block_spec.control_surfaces[0], WorkloadNodeControlSurfaceDeclarationProfile.V2)
        artifact = legacy_artifact("workload", declaration, node="legacy")
        WorkloadNodeControlConfigurationCodec().decode_bytes(artifact.content.encode())
        other = replace(other, configuration_artifacts=(artifact,), public_environment=(
            replace(other.public_environment[0], value=artifact.target_path),))
        graph = replace(first, nodes={**first.nodes, "legacy": other}, runtimes={
            "docker": replace(first.runtimes["docker"], children=("api", "legacy")),})
        record, projection, _, _ = self.seed(graph=graph)
        before = self.truth()
        install_schema(self.connection)
        with self.unit_of_work() as uow:
            members = uow.stores.graphs.receiver_bindings("workspace-a", record.graph_id, projection.projection_id)
            self.assertEqual(tuple(item.node_id for item in members), ("api",))
            self.assertEqual(uow.stores.realized_graphs.get(projection.projection_id), projection)
        self.assertEqual(self.truth(), before)

    def test_unindexed_generic_history_does_not_gain_wrapper_requirements(self):
        original, artifact, _ = self.receiver_graph()
        node = original.node("api")
        graphs = (
            replace(original, nodes={"api": replace(node, configuration_artifacts=())}),
            replace(original, nodes={"api": replace(node, configuration_artifacts=(
                replace(artifact, media_type=ConfigurationMediaType.TEXT,
                        content="opaque historical application content"),))}),
        )
        records = []
        with self.unit_of_work() as uow:
            for graph in graphs:
                record, projection = self.material(uow, graph=graph)
                # Existing lineage validation still applies; this is not merely
                # an unreachable retained graph.
                self.draft(uow, record)
                records.append((record, projection))
            uow.commit()
        before = self.truth()
        install_schema(self.connection)
        with self.unit_of_work() as uow:
            for record, projection in records:
                self.assertEqual(uow.stores.graphs.get(record.graph_id), record)
                self.assertEqual(uow.stores.realized_graphs.get(projection.projection_id), projection)
        self.assertEqual(self.truth(), before)

    def test_indexed_selected_artifact_corruption_and_partial_membership_refuse_reentry(self):
        first = self.receiver_graph()[0]
        second = self.receiver_graph(node_id="Other-node", receiver="b" * 32)[0]
        graph = replace(first, nodes={**first.nodes, **second.nodes}, runtimes={
            "docker": replace(first.runtimes["docker"], children=("api", "Other-node")),})
        original, _, _, _ = self.seed(graph=graph)
        with self.unit_of_work() as uow:
            guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            later, projection = self.material(uow, graph=graph)
            uow.stores.graphs._persist_receiver_bindings(later, projection, lifecycle_guard=guard)
            uow.commit()
        node = graph.node("api")
        selected = node.configuration_artifacts[-1]
        document = json.loads(selected.content)
        del document["target"]
        invalid_artifacts = (
            replace(selected, target_path="/not-selected.json"),
            replace(selected, content=json.dumps(document)),
        )
        for artifact in invalid_artifacts:
            changed = replace(graph, nodes={**graph.nodes, "api": replace(node,
                configuration_artifacts=(*node.configuration_artifacts[:-1], artifact))})
            corrupted = RealizedGraphProjectionRecord.from_graph(
                projection_id=projection.projection_id, workspace_id=projection.workspace_id,
                source_authored_graph_id=later.graph_id, projection_kind=projection.projection_kind,
                projection_key=projection.projection_key, graph=changed,
                created_by=projection.created_by, created_at=projection.created_at)
            with self.subTest(artifact=artifact.target_path), self.connection.transaction():
                self.connection.execute(
                    "UPDATE cpk_realized_graph_projections SET graph_descriptor=%s,projection_digest=%s "
                    "WHERE projection_id=%s",
                    (Jsonb(corrupted.graph_descriptor), corrupted.projection_digest, projection.projection_id))
                with self.assertRaises(SchemaInstallationError):
                    install_schema(self.connection)
                self.assertEqual(self.connection.execute(
                    "SELECT graph_descriptor FROM cpk_realized_graph_projections WHERE projection_id=%s",
                    (projection.projection_id,)).fetchone()[0], corrupted.graph_descriptor)
                raise psycopg.Rollback()
        before = self.truth()
        self.connection.execute("DELETE FROM cpk_graph_receiver_bindings WHERE graph_id=%s AND node_id=%s",
                                (later.graph_id, "Other-node"))
        with self.assertRaises(SchemaInstallationError):
            install_schema(self.connection)
        self.assertEqual(self.truth()[-1], before[-1] - 1)
        self.assertEqual(self.introduction().introducing_graph_id, original.graph_id)

    def test_member_order_is_independent_of_database_text_collation(self):
        graphs = [self.receiver_graph(node_id=name, receiver=receiver * 32)[0]
                  for name, receiver in (("a-node", "a"), ("A_node", "b"), ("z.node", "c"))]
        graph = replace(graphs[0], nodes={name: node for item in graphs for name, node in item.nodes.items()},
                        runtimes={"docker": replace(graphs[0].runtimes["docker"],
                                  children=("a-node", "A_node", "z.node"))})
        record, projection, _, _ = self.seed(graph=graph)
        with self.unit_of_work() as uow:
            bindings = uow.stores.graphs.receiver_bindings("workspace-a", record.graph_id, projection.projection_id)
            self.assertEqual(tuple(item.node_id for item in bindings), ("A_node", "a-node", "z.node"))

    def test_exact_selected_bytes_scope_and_origin_survive_reload_and_replay(self):
        graph, artifact, declaration = self.receiver_graph(pretty=True)
        record, projection, action, draft_id = self.seed(graph=graph, with_draft=True)
        original = self.introduction()
        self.assertEqual((original.workspace_id, original.receiver_id, original.runtime_id,
                          original.node_id, original.provider_socket_name),
                         ("workspace-a", RECEIVER, "docker", "api", "http"))
        self.assertEqual((original.introducing_graph_id, original.introducing_realized_projection_id,
                          original.introducing_action_id, original.introducing_session_id,
                          original.introducing_draft_id),
                         (record.graph_id, projection.projection_id, action.action_id,
                          action.session_id, draft_id))
        self.assertIsNone(original.first_accepted_action_id)
        self.assertIsNone(original.retired_action_id)
        with self.assertRaises(FrozenInstanceError):
            original.receiver_id = "b" * 32
        before = self.truth()
        with self.unit_of_work() as uow:
            guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            self.reserve(uow, record, projection, action, guard, draft_id=draft_id)
            uow.stores.graphs._persist_receiver_bindings(record, projection, lifecycle_guard=guard)
            bindings = uow.stores.graphs.receiver_bindings("workspace-a", record.graph_id, projection.projection_id)
            self.assertEqual(len(bindings), 1)
            binding = bindings[0]
            with self.assertRaises(FrozenInstanceError):
                binding.receiver_id = "b" * 32
            self.assertEqual((binding.receiver_id, binding.runtime_id, binding.node_id,
                              binding.provider_socket_name), (RECEIVER, "docker", "api", "http"))
            self.assertEqual(binding.selected_configuration_digest, artifact.content_digest)
            self.assertEqual(binding.declaration_identity, declaration.identity().value)
            self.assertEqual(uow.stores.graphs.get(record.graph_id), record)
            self.assertEqual(uow.stores.realized_graphs.get(projection.projection_id), projection)
            uow.commit()
        self.assertEqual(self.truth(), before)
        self.assertEqual(self.introduction(), original)
        self.assertIsNone(self.introduction("workspace-b"))
        self.assertIsNone(self.introduction(receiver="f" * 32))

    def test_same_receiver_can_bind_later_material_without_rewriting_original(self):
        self.seed()
        original = self.introduction()
        with self.unit_of_work() as uow:
            guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            record, projection = self.material(uow, graph=self.receiver_graph(pretty=True)[0])
            uow.stores.graphs._persist_receiver_bindings(record, projection, lifecycle_guard=guard)
            self.assertEqual(len(uow.stores.graphs.receiver_bindings(
                "workspace-a", record.graph_id, projection.projection_id)), 1)
            uow.commit()
        self.assertEqual(self.introduction(), original)

    def test_global_claim_race_has_one_owner_and_tenant_blind_loser(self):
        with self.unit_of_work() as uow:
            api = self.require_storage(uow.stores.graphs)
        before = self.truth()
        barrier = threading.Barrier(2)

        def claim(workspace):
            try:
                with self.unit_of_work() as uow:
                    guard = uow.stores.graphs.lock_receiver_lifecycle(workspace)
                    graph, projection = self.material(uow, workspace=workspace)
                    action = self.action(uow, workspace)
                    barrier.wait(timeout=5)
                    self.reserve(uow, graph, projection, action, guard)
                    uow.stores.graphs._persist_receiver_bindings(graph, projection, lifecycle_guard=guard)
                    uow.commit()
                return workspace, None
            except api.ReceiverLifecycleStorageConflict as error:
                return workspace, error

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(claim, workspace) for workspace in ("workspace-a", "workspace-b")]
            results = [future.result(timeout=15) for future in futures]
        winners = [workspace for workspace, error in results if error is None]
        losers = [(workspace, error) for workspace, error in results if error is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(len(losers), 1)
        self.assert_bounded(losers[0][1], "receiver identity is unavailable")
        self.assertIsNotNone(self.introduction(winners[0]))
        self.assertIsNone(self.introduction(losers[0][0]))
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM cpk_graph_receiver_introductions").fetchone()[0], 1)
        self.assertEqual(self.truth(), tuple(value + delta for value, delta in zip(
            before, (1, 1, 1, 0, 0, 1, 1), strict=True)))

    def test_original_provenance_cannot_be_replaced_by_new_graph_or_action(self):
        record, projection, action, draft_id = self.seed(with_draft=True)
        original, before = self.introduction(), self.truth()
        for change in ("graph", "action", "draft"):
            with self.subTest(change=change):
                with self.unit_of_work() as uow:
                    api = self.require_storage(uow.stores.graphs)
                    guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
                    candidate, realized = self.material(uow) if change == "graph" else (record, projection)
                    witness = self.action(uow) if change == "action" else action
                    candidate_draft = self.draft(uow, candidate) if change == "graph" else draft_id
                    with self.assertRaises(api.ReceiverLifecycleStorageConflict) as caught:
                        self.reserve(uow, candidate, realized, witness, guard,
                                     draft_id=None if change == "draft" else candidate_draft)
                self.assert_bounded(caught.exception, "receiver identity is unavailable")
                self.assertEqual(self.truth(), before)
                self.assertEqual(self.introduction(), original)

    def test_stored_material_is_authoritative_over_self_consistent_caller_records(self):
        record, projection, action, _ = self.seed()
        with self.unit_of_work() as uow:
            uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            _, other_projection = self.material(uow)
            uow.commit()
        before = self.truth()
        changed = GraphVersionRecord.from_graph(
            graph_id=record.graph_id, workspace_id=record.workspace_id, version=record.version,
            graph=self.receiver_graph(receiver="b" * 32)[0], created_by="operator-a", created_at=NOW,
        )
        forged_projection = RealizedGraphProjectionRecord.identity_for_authored(authored_record=changed)
        forged_projection = replace(forged_projection, projection_id=projection.projection_id)
        for candidate, realized in ((changed, projection), (changed, forged_projection),
                                    (record, other_projection),
                                    (record, replace(projection, projection_id="missing-projection"))):
            for writer in ("reserve", "bindings"):
                with self.subTest(writer=writer, candidate=candidate.graph_id, projection=realized.projection_id):
                    with self.unit_of_work() as uow:
                        api = self.require_storage(uow.stores.graphs)
                        guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
                        with self.assertRaises(api.ReceiverLifecycleStorageError):
                            if writer == "reserve":
                                self.reserve(uow, candidate, realized, action, guard)
                            else:
                                uow.stores.graphs._persist_receiver_bindings(candidate, realized, lifecycle_guard=guard)
                    self.assertEqual(self.truth(), before)

    def test_scope_substitution_and_unselected_configuration_refuse_without_rows(self):
        self.seed()
        before = self.truth()
        cases = [dict(target_changes={key: value}) for key, value in (
            ("workspace_id", "workspace-b"), ("runtime_id", "other-runtime"),
            ("node_id", "other-node"),
        )]
        cases.append(dict(artifact_changes={"target_path": "/etc/test/not-selected.json"}))
        for options in cases:
            with self.subTest(options=options), self.unit_of_work() as uow:
                api = self.require_storage(uow.stores.graphs)
                guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
                record, projection = self.material(uow, graph=self.receiver_graph(**options)[0])
                with self.assertRaises(api.ReceiverLifecycleStorageError):
                    uow.stores.graphs._persist_receiver_bindings(record, projection, lifecycle_guard=guard)
            self.assertEqual(self.truth(), before)

    def test_member_reads_refuse_missing_extra_or_crossed_material_instead_of_partial_sets(self):
        original, projection, _, _ = self.seed()
        with self.unit_of_work() as uow:
            api = self.require_storage(uow.stores.graphs)
            guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            later, realized = self.material(uow)
            uow.stores.graphs._persist_receiver_bindings(later, realized, lifecycle_guard=guard)
            empty, empty_projection = self.material(uow, graph=DeploymentGraph("empty"))
            self.assertEqual(uow.stores.graphs.receiver_bindings(
                "workspace-a", empty.graph_id, empty_projection.projection_id), ())
            uow.commit()
        self.connection.execute("DELETE FROM cpk_graph_receiver_bindings WHERE graph_id=%s", (later.graph_id,))
        self.connection.execute(
            "INSERT INTO cpk_graph_receiver_bindings "
            "SELECT workspace_id,%s,%s,runtime_id,node_id,provider_socket_name,receiver_id,"
            "selected_configuration_digest,declaration_identity FROM cpk_graph_receiver_bindings WHERE graph_id=%s",
            (empty.graph_id, empty_projection.projection_id, original.graph_id),
        )
        for workspace, graph_id, projection_id in (
            ("workspace-a", later.graph_id, realized.projection_id),
            ("workspace-a", empty.graph_id, empty_projection.projection_id),
            ("workspace-a", original.graph_id, realized.projection_id),
            ("workspace-b", original.graph_id, projection.projection_id),
        ):
            with self.subTest(workspace=workspace, graph_id=graph_id), self.unit_of_work() as uow:
                with self.assertRaises(api.ReceiverLifecycleStorageError):
                    uow.stores.graphs.receiver_bindings(workspace, graph_id, projection_id)

    def test_guard_from_other_store_transaction_or_workspace_has_no_write_authority(self):
        record, projection, action, _ = self.seed()
        with self.unit_of_work() as uow:
            accept, retire = self.action(uow), self.action(uow)
            uow.commit()

        def refuse_all(store, guard):
            api = self.require_storage(store)
            operations = (
                lambda: store._reserve_receiver_introductions(record, projection,
                    action_id=action.action_id, session_id=action.session_id, lifecycle_guard=guard),
                lambda: store._persist_receiver_bindings(record, projection, lifecycle_guard=guard),
                lambda: store._record_receiver_first_acceptance("workspace-a", RECEIVER,
                    action_id=accept.action_id, session_id=accept.session_id, lifecycle_guard=guard),
                lambda: store._record_receiver_retirement("workspace-a", RECEIVER,
                    action_id=retire.action_id, session_id=retire.session_id, lifecycle_guard=guard),
            )
            for index, operation in enumerate(operations):
                with self.subTest(writer=index), self.assertRaises(api.ReceiverLifecycleStorageError):
                    operation()

        with self.unit_of_work() as prior:
            retained = prior.stores.graphs
            stale = retained.lock_receiver_lifecycle("workspace-a")
        for phase in ("pending", "accepted"):
            if phase == "accepted":
                with self.unit_of_work() as uow:
                    guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
                    uow.stores.graphs._record_receiver_first_acceptance("workspace-a", RECEIVER,
                        action_id=accept.action_id, session_id=accept.session_id, lifecycle_guard=guard)
                    uow.commit()
            before, original = self.truth(), self.introduction()
            with self.subTest(phase=phase, retained_store=True):
                refuse_all(retained, stale)
            with self.unit_of_work() as outer:
                foreign = outer.stores.graphs.lock_receiver_lifecycle("workspace-a")
                with self.unit_of_work() as uow:
                    wrong_workspace = uow.stores.graphs.lock_receiver_lifecycle("workspace-b")
                    for guard in (stale, foreign, wrong_workspace, object()):
                        with self.subTest(phase=phase, guard=type(guard).__name__):
                            refuse_all(uow.stores.graphs, guard)
            self.assertEqual(self.truth(), before)
            self.assertEqual(self.introduction(), original)

    def test_acceptance_and_retirement_are_paired_write_once_and_replay_exactly(self):
        self.seed()
        with self.unit_of_work() as uow:
            api = self.require_storage(uow.stores.graphs)
            guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            accept, retire, other = (self.action(uow) for _ in range(3))
            retire_writer = uow.stores.graphs._record_receiver_retirement
            accept_writer = uow.stores.graphs._record_receiver_first_acceptance
            kwargs = dict(lifecycle_guard=guard)
            with self.assertRaises(api.ReceiverLifecycleStorageConflict):
                retire_writer("workspace-a", RECEIVER, action_id=retire.action_id,
                              session_id=retire.session_id, **kwargs)
            for _ in range(2):
                accept_writer("workspace-a", RECEIVER, action_id=accept.action_id,
                              session_id=accept.session_id, **kwargs)
            with self.assertRaises(api.ReceiverLifecycleStorageConflict):
                retire_writer("workspace-a", RECEIVER, action_id=accept.action_id,
                              session_id=accept.session_id, **kwargs)
            for _ in range(2):
                retire_writer("workspace-a", RECEIVER, action_id=retire.action_id,
                              session_id=retire.session_id, **kwargs)
            for writer in (accept_writer, retire_writer):
                with self.assertRaises(api.ReceiverLifecycleStorageConflict):
                    writer("workspace-a", RECEIVER, action_id=other.action_id,
                           session_id=other.session_id, **kwargs)
                for action_id, session_id in ((None, None), (accept.action_id, None), (None, accept.session_id)):
                    with self.assertRaises(api.ReceiverLifecycleStorageError):
                        writer("workspace-a", RECEIVER, action_id=action_id, session_id=session_id, **kwargs)
            uow.commit()
        result = self.introduction()
        self.assertEqual((result.first_accepted_action_id, result.first_accepted_session_id,
                          result.retired_action_id, result.retired_session_id),
                         (accept.action_id, accept.session_id, retire.action_id, retire.session_id))

    def test_missing_original_binding_fails_at_commit_and_rolls_back_every_prior_write(self):
        with self.unit_of_work() as uow:
            self.require_storage(uow.stores.graphs)
        before = self.truth()
        reached_commit_request = False
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            with self.unit_of_work() as uow:
                guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
                graph, projection = self.material(uow)
                draft_id = self.draft(uow, graph)
                action = self.action(uow)
                self.reserve(uow, graph, projection, action, guard, draft_id=draft_id)
                self.assertIsNotNone(uow.stores.graphs.receiver_introduction("workspace-a", RECEIVER))
                uow.commit()
                reached_commit_request = True
        self.assertTrue(reached_commit_request, "failure must occur at deferred physical commit, not reservation")
        self.assertEqual(self.truth(), before)

    def test_multiple_members_are_complete_and_duplicate_receiver_scope_is_refused(self):
        first = self.receiver_graph()[0]
        second = self.receiver_graph(node_id="other", receiver="b" * 32)[0]
        graph = replace(first, nodes={**first.nodes, **second.nodes}, runtimes={
            "docker": replace(first.runtimes["docker"], children=("api", "other")),
        })
        validate_graph(graph).require_valid()
        record, projection, _, _ = self.seed(graph=graph)
        with self.unit_of_work() as uow:
            api = self.require_storage(uow.stores.graphs)
            members = uow.stores.graphs.receiver_bindings("workspace-a", record.graph_id, projection.projection_id)
            self.assertEqual({(member.node_id, member.receiver_id) for member in members},
                             {("api", RECEIVER), ("other", "b" * 32)})
            self.assertEqual(len(members), 2)
        before = self.truth()
        fresh_first = self.receiver_graph(receiver="c" * 32)[0]
        duplicate = self.receiver_graph(node_id="other", receiver="c" * 32)[0]
        duplicate_graph = replace(graph, nodes={**fresh_first.nodes, **duplicate.nodes})
        validate_graph(duplicate_graph).require_valid()
        with self.unit_of_work() as uow:
            guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            candidate, realized = self.material(uow, graph=duplicate_graph)
            action = self.action(uow)
            with self.assertRaises(api.ReceiverLifecycleStorageError):
                self.reserve(uow, candidate, realized, action, guard)
        self.assertEqual(self.truth(), before)

    def test_exception_after_commit_request_rolls_back_graph_action_and_both_indices(self):
        with self.unit_of_work() as uow:
            self.require_storage(uow.stores.graphs)
        before = self.truth()
        with self.assertRaisesRegex(RuntimeError, "injected late command failure"):
            with self.unit_of_work() as uow:
                guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
                graph, projection = self.material(uow)
                action = self.action(uow)
                self.reserve(uow, graph, projection, action, guard)
                uow.stores.graphs._persist_receiver_bindings(graph, projection, lifecycle_guard=guard)
                uow.commit()
                raise RuntimeError("injected late command failure")
        self.assertEqual(self.truth(), before)

    def test_draft_tombstone_and_current_reverification_retain_original_history(self):
        graph, projection, action, draft_id = self.seed(with_draft=True)
        original = self.introduction()
        with self.unit_of_work() as uow:
            uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
            uow.stores.desired_topology_drafts.tombstone(
                "workspace-a", draft_id, expected_head_revision=1,
                deleted_by="operator-a", deleted_at=NOW,
            )
            uow.commit()
        before = self.truth()
        install_schema(self.connection)
        self.assertEqual(self.truth(), before)
        self.assertEqual(self.introduction(), original)
        for table, column, value in (
            ("cpk_graph_versions", "graph_id", graph.graph_id),
            ("cpk_realized_graph_projections", "projection_id", projection.projection_id),
            ("cpk_operation_actions", "action_id", action.action_id),
        ):
            with self.subTest(table=table), self.assertRaises(psycopg.errors.ForeignKeyViolation):
                self.connection.execute("DELETE FROM " + table + " WHERE " + column + "=%s", (value,))
        self.assertEqual(self.introduction(), original)
