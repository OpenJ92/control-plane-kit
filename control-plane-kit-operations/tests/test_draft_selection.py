"""#1763 selection/tombstone laws at the owning transaction boundary."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading
import unittest

import psycopg
from psycopg.types.json import Jsonb

from control_plane_kit_operations.workflows import CloseOperationSession, IdempotencyKey, OperationCommandService
from draft_catalogue_fixture import NOW, principal
from draft_selection_fixture import DraftSelectionFixture


class DraftSelectionTests(DraftSelectionFixture, unittest.TestCase):
    def test_exact_old_revision_identity_is_selected_without_graph_copy_and_stays_selected_after_revise(self):
        first = self.create()
        second = self.catalogue().execute(self.revise_command(first))
        graphs = self.rows("cpk_graph_versions")
        effects = self.effects()
        current = self.workspace()
        result = self.catalogue().execute(self.select_command(first))
        self.assertEqual(result.descriptor(), {
            "workspace_id": "workspace-a", "draft_id": first.draft_id,
            "revision": 1, "graph_id": first.graph_id,
            "desired_realized_projection_id": result.desired_realized_projection_id,
            "desired_graph_revision": current.desired_graph_revision + 1,
        })
        with self.unit_of_work() as uow:
            projection = uow.stores.realized_graphs.identity_for_authored("workspace-a", first.graph_id)
            self.assertEqual(result.desired_realized_projection_id, projection.projection_id)
            self.assertEqual(projection.graph_descriptor, uow.stores.graphs.get(first.graph_id).graph_descriptor)
        self.assertEqual(self.rows("cpk_graph_versions"), graphs)
        self.assertEqual(self.workspace().desired_graph_id, first.graph_id)
        self.assertEqual(self.workspace().current_lineage, current.current_lineage)
        self.catalogue().execute(self.revise_command(second, expected=2, key="third"))
        self.assertEqual(self.workspace().desired_graph_id, first.graph_id)
        self.assertEqual(self.workspace().desired_graph_revision, result.desired_graph_revision)
        self.assertEqual(self.effects(), effects)

    def test_each_expected_coordinate_is_fenced_and_same_graph_selection_increments_generation(self):
        first = self.create()
        command = self.select_command(first)
        for changes in ({"expected_desired_graph_id": first.graph_id},
                        {"expected_desired_realized_projection_id": "foreign-projection"},
                        {"expected_desired_graph_revision": 0}):
            before = self.truth()
            with self.subTest(changes=changes), self.assertRaises(self.api.DesiredTopologyDraftConflict):
                self.catalogue().execute(replace(command, **changes))
            self.assertEqual(self.truth(), before)
        selected = self.catalogue().execute(command)
        projections = self.rows("cpk_realized_graph_projections")
        again = self.catalogue().execute(self.select_command(first, key="same-graph"))
        self.assertEqual(again.graph_id, selected.graph_id)
        self.assertEqual(again.desired_realized_projection_id, selected.desired_realized_projection_id)
        self.assertEqual(again.desired_graph_revision, selected.desired_graph_revision + 1)
        self.assertEqual(self.rows("cpk_realized_graph_projections"), projections)
        self.restore_legacy_desired()
        before = self.truth()
        with self.assertRaises(self.api.DesiredTopologyDraftConflict):
            self.catalogue().execute(replace(command, idempotency_key=IdempotencyKey("aba")))
        self.assertEqual(self.truth(), before)

    def test_overflow_and_malformed_generation_fail_before_any_write(self):
        first = self.create()
        self.connection.execute("UPDATE cpk_workspaces SET desired_graph_revision=%s WHERE workspace_id='workspace-a'",
                                (9_223_372_036_854_775_807,))
        for generation in (9_223_372_036_854_775_807, -1, True, "1"):
            statements = []
            before = self.truth()
            with self.subTest(generation=generation), self.assertRaises(self.api.DesiredTopologyDraftError):
                self.catalogue(unit_of_work_factory=self.observed_uow(statements)).execute(
                    self.select_command(first, expected_desired_graph_revision=generation))
            self.assertEqual(self.truth(), before)
            self.assertFalse(any(query.startswith(("INSERT", "UPDATE", "DELETE")) for query in statements))

    def test_selection_revalidates_workspace_active_products_without_writes(self):
        first = self.create()
        with self.unit_of_work() as uow:
            registration = self.register_product(uow, "workspace-b")
            uow.stores.registered_products.revoke("workspace-a", registration.reference)
            uow.commit()
        before = self.truth()
        with self.assertRaises(self.api.DesiredTopologyDraftError):
            self.catalogue().execute(self.select_command(first))
        self.assertEqual(self.truth(), before)
        foreign = self.select_command(first, context=principal("workspace-b").command_context("workspace-b"),
                                      session_id=self.sessions["workspace-b"])
        with self.assertRaises(self.api.DesiredTopologyDraftError):
            self.catalogue().execute(foreign)
        self.assertEqual(self.truth(), before)

    def test_saved_graph_semantics_and_foreign_workspace_product_are_rechecked_on_selection(self):
        from control_plane_kit_core.algebra import RequirementSocket
        from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, validate_graph
        from control_plane_kit_core.types import Protocol
        from control_plane_kit_operations.records import GraphVersionRecord

        first = self.create()
        graph = self.graph()
        node = graph.nodes["app"]
        invalid = replace(graph, nodes={"app": replace(node, sockets=replace(node.sockets,
            requirements=(RequirementSocket("missing", Protocol.HTTP, ("MISSING_URL",), True),)))})
        descriptor = DEFAULT_GRAPH_CODEC.encode(invalid)
        self.assertFalse(validate_graph(invalid).valid)
        self.connection.execute("UPDATE cpk_graph_versions SET graph_descriptor=%s WHERE graph_id=%s",
                                (Jsonb(descriptor), first.graph_id))
        before = self.truth()
        with self.assertRaises(self.api.DesiredTopologyDraftError):
            self.catalogue().execute(self.select_command(first))
        self.assertEqual(self.truth(), before)
        with self.unit_of_work() as uow:
            workspace = uow.stores.workspaces.get_for_update("workspace-b")
            saved = GraphVersionRecord.from_graph(graph_id="workspace-b-saved", workspace_id="workspace-b",
                version=2, graph=graph, created_by="operator-a", created_at=NOW)
            uow.stores.graphs.save(saved)
            uow.stores.desired_topology_drafts.create(self.api.DesiredTopologyDraftRecord(
                "workspace-b", "foreign-products", "Foreign products", 1, "operator-a", NOW))
            revision = self.api.DesiredTopologyDraftRevisionRecord(
                "workspace-b", "foreign-products", 1, saved.graph_id, "operator-a", NOW)
            uow.stores.desired_topology_drafts.append(revision, expected_head_revision=None)
            uow.commit()
        command = self.select_command(revision, workspace=workspace,
            context=principal("workspace-b").command_context("workspace-b"),
            session_id=self.sessions["workspace-b"])
        before = self.truth()
        with self.assertRaises(self.api.DesiredTopologyDraftError):
            self.catalogue().execute(command)
        self.assertEqual(self.truth(), before)

    def test_plan_reference_lookup_has_separate_leading_indexes(self):
        rows = self.connection.execute("""
            SELECT indexdef FROM pg_indexes
            WHERE schemaname=current_schema() AND tablename='cpk_activity_plans'
        """).fetchall()
        definitions = [row[0] for row in rows]
        for column in ("base_graph_id", "desired_graph_id"):
            self.assertTrue(any(f"USING btree ({column})" in value for value in definitions),
                            f"missing indexed plan reference lookup for {column}")

    def test_unused_tombstone_preserves_all_history_and_remains_readable(self):
        first = self.create()
        second = self.catalogue().execute(self.revise_command(first))
        before = self.truth()
        result = self.catalogue().execute(self.delete_command(second))
        self.assertEqual(result.descriptor(), {"workspace_id": "workspace-a", "draft_id": first.draft_id,
            "head_revision": 2, "deleted_by": "operator-a", "deleted_at": NOW})
        after = self.truth()
        for table in before.keys() - {"cpk_desired_topology_drafts", "cpk_operation_actions"}:
            self.assertEqual(after[table], before[table])
        summary = self.read()["items"][0]
        self.assertEqual((summary["head_revision"], summary["deleted_by"], summary["deleted_at"]),
                         (2, "operator-a", NOW))
        self.assertEqual(len(self.read("read.desired-topology-draft-revisions", draft_id=first.draft_id)["items"]), 2)
        self.assertEqual(self.read("read.desired-topology-draft-revision", draft_id=first.draft_id,
                                  revision=1)["graph_id"], first.graph_id)
        for command in (self.select_command(first), self.revise_command(second, expected=2),
                        self.delete_command(second, key="delete-again")):
            with self.assertRaises(self.api.DesiredTopologyDraftError):
                self.catalogue().execute(command)
        self.assertEqual(self.truth(), after)

    def test_stale_delete_and_every_revision_reference_category_block_tombstone(self):
        # Each fixture draft gets a retained reference through one public owner;
        # older revisions and every plan status remain retention obligations.
        for category in ("current", "desired", "plan-base", "plan-desired"):
            for status in (("planned", "cancelled", "superseded") if category.startswith("plan") else (None,)):
                first = self.create(key=f"create-{category}-{status}")
                second = self.catalogue().execute(self.revise_command(first, key=f"revise-{category}-{status}"))
                before = self.truth()
                with self.assertRaises(self.api.DesiredTopologyDraftConflict):
                    self.catalogue().execute(self.delete_command(first, key=f"stale-{category}-{status}"))
                self.assertEqual(self.truth(), before)
                with self.unit_of_work() as uow:
                    uow.stores.workspaces.get_for_update("workspace-a")
                    if category in {"current", "plan-base"}:
                        uow.stores.workspaces.set_current_graph("workspace-a", first.graph_id)
                    else:
                        uow.stores.workspaces.set_desired_graph("workspace-a", first.graph_id)
                    uow.commit()
                if category.startswith("plan"):
                    plan = self.planner().execute(self.plan_command(key=f"plan-{category}-{status}"))
                    self.connection.execute("UPDATE cpk_activity_plans SET status=%s WHERE plan_id=%s",
                                            (status, plan.plan_record.plan_id))
                    with self.unit_of_work() as uow:
                        uow.stores.workspaces.get_for_update("workspace-a")
                        uow.stores.workspaces.set_current_graph("workspace-a", "workspace-a-current")
                        uow.stores.workspaces.set_desired_graph("workspace-a", "workspace-a-current")
                        uow.commit()
                before = self.truth()
                with self.subTest(category=category, status=status), self.assertRaises(self.api.DesiredTopologyDraftConflict):
                    self.catalogue().execute(self.delete_command(second, key=f"blocked-{category}-{status}"))
                self.assertEqual(self.truth(), before)

    def test_select_and_delete_replay_survive_later_head_pointer_product_and_session_changes(self):
        first = self.create()
        selection = self.select_command(first)
        selected = self.catalogue().execute(selection)
        second = self.catalogue().execute(self.revise_command(first))
        self.restore_legacy_desired()
        deletion = self.delete_command(second)
        deleted = self.catalogue().execute(deletion)
        with self.unit_of_work() as uow:
            registration = self.register_product(uow, "workspace-b")
            uow.stores.registered_products.revoke("workspace-a", registration.reference)
            uow.commit()
        OperationCommandService(self.unit_of_work, clock=lambda: NOW, id_factory=lambda: "close-action").execute(
            CloseOperationSession(self.sessions["workspace-a"], "operator-a", IdempotencyKey("close")))
        def forbidden_id():
            self.fail("historical replay allocated an identity")
        statements = []
        service = self.catalogue(id_factory=forbidden_id, unit_of_work_factory=self.observed_uow(statements))
        before = self.truth()
        self.assertEqual(service.execute(selection), selected)
        self.assertEqual(service.execute(deletion), deleted)
        for changed in (replace(selection, revision=2), replace(deletion, expected_head_revision=1)):
            with self.assertRaises(self.api.DesiredTopologyDraftConflict):
                service.execute(changed)
        self.assertEqual(self.truth(), before)
        self.assertFalse(any(query.startswith(("INSERT", "UPDATE", "DELETE")) for query in statements))

    def test_replay_rejects_false_and_malformed_selection_or_tombstone_evidence(self):
        first = self.create()
        selection = self.select_command(first)
        selected = self.catalogue().execute(selection)
        self.restore_legacy_desired()
        deletion = self.delete_command(first)
        deleted = self.catalogue().execute(deletion)
        for command, result, corruptions in (
            (selection, selected, ({"revision": True}, {"graph_id": "workspace-a-current"},
                {"desired_realized_projection_id": self.workspace().current_realized_projection_id},
                {"desired_graph_revision": selected.desired_graph_revision + 1}, {"draft_id": "missing"})),
            (deletion, deleted, ({"head_revision": 2}, {"deleted_by": "foreign-actor"},
                {"deleted_at": "2026-09-06T18:00:01Z"}, {"draft_id": "missing"})),
        ):
            original = result.descriptor()
            invalid = [{**original, **change} for change in corruptions]
            invalid += [{**original, "private-marker": "do-not-disclose"},
                        {key: value for key, value in original.items() if key != "workspace_id"}]
            for payload in invalid:
                self.connection.execute("UPDATE cpk_operation_actions SET payload=%s WHERE session_id=%s AND idempotency_key=%s",
                    (Jsonb(payload), command.session_id, command.idempotency_key.value))
                before = self.truth()
                def forbidden_id():
                    self.fail("malformed replay allocated an identity")
                with self.assertRaises(self.api.DesiredTopologyDraftError) as error:
                    self.catalogue(id_factory=forbidden_id).execute(command)
                self.assertNotIn("private-marker", str(error.exception))
                self.assertLessEqual(len(str(error.exception)), 512)
                self.assertEqual(self.truth(), before)
            self.connection.execute("UPDATE cpk_operation_actions SET payload=%s WHERE session_id=%s AND idempotency_key=%s",
                (Jsonb(original), command.session_id, command.idempotency_key.value))
            self.assertEqual(self.catalogue().execute(command), result)

    def test_late_action_collision_rolls_back_selection_projection_or_tombstone(self):
        first = self.create()
        collision = self.rows("cpk_operation_actions")[0][0]["action_id"]
        for command in (self.select_command(first), self.delete_command(first)):
            before = self.truth()
            with self.assertRaises(psycopg.errors.UniqueViolation):
                self.catalogue(id_factory=lambda: collision).execute(command)
            self.assertEqual(self.truth(), before)

    def test_selection_lock_order_and_concurrent_distinct_cas_have_one_winner(self):
        first = self.create()
        commands = [self.select_command(first, key=f"race-{index}",
                    session_id=self.start_session("workspace-a")) for index in range(2)]
        barrier = threading.Barrier(2)
        traces = [[], []]
        def submit(index):
            barrier.wait(timeout=5)
            try:
                return self.catalogue(unit_of_work_factory=self.observed_uow(traces[index])).execute(commands[index])
            except self.api.DesiredTopologyDraftConflict as error:
                return error
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, range(2)))
        self.assertEqual(sum(isinstance(result, self.api.DesiredTopologyDraftConflict) for result in results), 1)
        successful = traces[next(index for index, result in enumerate(results)
                                 if not isinstance(result, self.api.DesiredTopologyDraftConflict))]
        locks = [query for query in successful if "pg_advisory_xact_lock" in query or "FOR UPDATE" in query]
        self.assertIn("pg_advisory_xact_lock", locks[0])
        self.assertIn("cpk_operation_sessions", locks[1])
        self.assertIn("cpk_workspaces", locks[2])
        self.assertIn("cpk_desired_topology_drafts", locks[3])
        self.assertEqual(self.workspace().desired_graph_revision, commands[0].expected_desired_graph_revision + 1)

    def test_select_and_delete_each_lock_owner_excludes_the_other(self):
        for first_kind in ("select", "delete"):
            draft = self.create(key=f"create-{first_kind}")
            commands = {"select": self.select_command(draft, key=f"select-{first_kind}",
                        session_id=self.start_session("workspace-a")),
                        "delete": self.delete_command(draft, key=f"delete-{first_kind}",
                        session_id=self.start_session("workspace-a"))}
            clock, reached, release = self.hold_clock()
            attempted = threading.Event()
            owner = self.catalogue(clock=clock)
            other = self.catalogue(unit_of_work_factory=self.observed_uow([], attempted))
            with ThreadPoolExecutor(max_workers=2) as pool:
                leader = pool.submit(owner.execute, commands[first_kind])
                try:
                    self.assertTrue(reached.wait(timeout=5))
                    follower = pool.submit(other.execute, commands["delete" if first_kind == "select" else "select"])
                    self.assertTrue(attempted.wait(timeout=5))
                    self.assertFalse(follower.done())
                finally:
                    release.set()
                leader.result(timeout=10)
                with self.assertRaises(self.api.DesiredTopologyDraftError):
                    follower.result(timeout=10)
            self.restore_legacy_desired()

    def test_planning_and_delete_share_workspace_serialization_in_both_orders(self):
        first = self.create()
        self.catalogue().execute(self.select_command(first))
        plan_command = self.plan_command(session_id=self.start_session("workspace-a"))
        delete = self.delete_command(first, session_id=self.start_session("workspace-a"))
        clock, reached, release = self.hold_clock()
        attempted = threading.Event()
        with ThreadPoolExecutor(max_workers=2) as pool:
            leader = pool.submit(self.planner(clock=clock).execute, plan_command)
            try:
                self.assertTrue(reached.wait(timeout=5))
                follower = pool.submit(self.catalogue(unit_of_work_factory=self.observed_uow([], attempted)).execute, delete)
                self.assertTrue(attempted.wait(timeout=5))
                self.assertFalse(follower.done())
            finally:
                release.set()
            leader.result(timeout=10)
            with self.assertRaises(self.api.DesiredTopologyDraftConflict):
                follower.result(timeout=10)
        self.restore_legacy_desired()
        with self.assertRaises(self.api.DesiredTopologyDraftConflict):
            self.catalogue().execute(delete)  # Retained plan still owns its reference.
        unused = self.create(key="unused")
        stale_plan = replace(self.plan_command(session_id=self.start_session("workspace-a"), key="stale-plan"),
                             expected_desired_graph_id=unused.graph_id)
        clock, reached, release = self.hold_clock()
        attempted = threading.Event()
        before = self.rows("cpk_activity_plans")
        with ThreadPoolExecutor(max_workers=2) as pool:
            leader = pool.submit(self.catalogue(clock=clock).execute, self.delete_command(unused, key="unused-delete"))
            try:
                self.assertTrue(reached.wait(timeout=5))
                follower = pool.submit(self.planner(unit_of_work_factory=self.observed_uow([], attempted)).execute, stale_plan)
                self.assertTrue(attempted.wait(timeout=5))
                self.assertFalse(follower.done())
            finally:
                release.set()
            leader.result(timeout=10)
            from control_plane_kit_operations.planning import ActivityPlanningGraphStateConflict
            with self.assertRaises(ActivityPlanningGraphStateConflict):
                follower.result(timeout=10)
        self.assertEqual(self.rows("cpk_activity_plans"), before)
