"""#1897 B1–B5/G2/U1/L1: staged storage integrity, not C authorization."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
import threading
import unittest

import psycopg

from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_operations.postgres import install_schema
from control_plane_kit_operations.records import GraphVersionRecord, RealizedGraphProjectionRecord
from tests.draft_catalogue_fixture import NOW
from tests.receiver_storage_fixture import ReceiverStorageFixture, RECEIVER


class PostgresReceiverStorageTests(ReceiverStorageFixture, unittest.TestCase):
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
                    with self.assertRaises(api.ReceiverLifecycleStorageConflict) as caught:
                        self.reserve(uow, candidate, realized, witness, guard,
                                     draft_id=None if change == "draft" else draft_id)
                self.assert_bounded(caught.exception, "receiver identity is unavailable")
                self.assertEqual(self.truth(), before)
                self.assertEqual(self.introduction(), original)

    def test_stored_material_is_authoritative_over_self_consistent_caller_records(self):
        record, projection, action, _ = self.seed()
        before = self.truth()
        changed = GraphVersionRecord.from_graph(
            graph_id=record.graph_id, workspace_id=record.workspace_id, version=record.version,
            graph=self.receiver_graph(receiver="b" * 32)[0], created_by="operator-a", created_at=NOW,
        )
        forged_projection = RealizedGraphProjectionRecord.identity_for_authored(authored_record=changed)
        forged_projection = replace(forged_projection, projection_id=projection.projection_id)
        for candidate, realized in ((changed, projection), (changed, forged_projection),
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
        before = self.truth()
        with self.unit_of_work() as prior:
            stale = prior.stores.graphs.lock_receiver_lifecycle("workspace-a")
        with self.unit_of_work() as outer:
            foreign = outer.stores.graphs.lock_receiver_lifecycle("workspace-a")
            with self.unit_of_work() as uow:
                api = self.require_storage(uow.stores.graphs)
                wrong_workspace = uow.stores.graphs.lock_receiver_lifecycle("workspace-b")
                for guard in (stale, foreign, wrong_workspace, object()):
                    with self.subTest(guard=type(guard).__name__):
                        with self.assertRaises(api.ReceiverLifecycleStorageError):
                            self.reserve(uow, record, projection, action, guard)
                        with self.assertRaises(api.ReceiverLifecycleStorageError):
                            uow.stores.graphs._persist_receiver_bindings(record, projection, lifecycle_guard=guard)
        self.assertEqual(self.truth(), before)

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
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            with self.unit_of_work() as uow:
                guard = uow.stores.graphs.lock_receiver_lifecycle("workspace-a")
                graph, projection = self.material(uow)
                draft_id = self.draft(uow, graph)
                action = self.action(uow)
                self.reserve(uow, graph, projection, action, guard, draft_id=draft_id)
                self.assertIsNotNone(uow.stores.graphs.receiver_introduction("workspace-a", RECEIVER))
                uow.commit()
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
