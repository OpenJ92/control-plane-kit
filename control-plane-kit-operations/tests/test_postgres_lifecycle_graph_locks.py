"""#1896 strengthened graph/reference locking and prepared-entry laws."""

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import unittest
import uuid

from control_plane_kit_operations import desired_realized_projections as publication
from control_plane_kit_operations.graph_authoring import (
    GraphAuthoringError, GraphAuthoringService, SetDesiredGraphCommand,
    set_desired_graph_in_unit_of_work,
)
from control_plane_kit_operations.planning import DesiredGraphCommandService, SetDesiredGraph
from control_plane_kit_operations.postgres.unit_of_work import UnitOfWorkStateError
from control_plane_kit_operations.workflows import IdempotencyKey
from tests.draft_catalogue_fixture import NOW
from tests.saved_preparation_fixture import SavedPreparationFixture
from tests.lifecycle_lock_fixture import (
    LifecycleLockFixture, LIFECYCLE_LOCK, SESSION_LOCK, WORKSPACE_LOCK,
)


class PostgresLifecycleGraphLockTests(
    LifecycleLockFixture, SavedPreparationFixture, unittest.TestCase,
):
    def graph_command(self):
        workspace = self.workspace()
        return SetDesiredGraphCommand(
            "workspace-a", "operator-a", self.graph(), workspace.desired_graph_id,
            workspace.desired_realized_projection_id, workspace.desired_graph_revision,
        )

    def publication_command(self):
        workspace = self.workspace()
        with self.unit_of_work() as uow:
            projection = uow.stores.realized_graphs.get(workspace.desired_realized_projection_id)
        return publication.PublishDesiredRealizedProjection(
            self.sessions["workspace-a"], "workspace-a", "operator-a",
            workspace.desired_graph_id, workspace.desired_realized_projection_id,
            workspace.desired_graph_revision, projection, "operation-a", 1,
            IdempotencyKey("publish-a"),
        )

    def publisher(self, uow):
        return publication.DesiredRealizedProjectionCommandService(
            uow, clock=lambda: NOW, action_id_factory=lambda: uuid.uuid4().hex,
        )

    def guard(self, uow, workspace="workspace-a"):
        acquire = getattr(uow.stores.graphs, "lock_receiver_lifecycle", None)
        self.assertTrue(callable(acquire), "#1896 graph-owned lifecycle guard is missing")
        return acquire(workspace)

    def test_fresh_graph_reference_entrants_take_exact_guard_before_session_and_workspace(self):
        for kind in ("create", "revise", "select", "delete", "plan", "saved",
                     "standalone", "desired-command", "publication"):
            with self.subTest(kind=kind):
                draft = self.create(key="fixture-" + kind)
                if kind in {"create", "revise", "select", "delete"}:
                    command = {
                        "create": lambda: self.create_command(key="fresh-create"),
                        "revise": lambda: self.revise_command(draft),
                        "select": lambda: self.select_command(draft),
                        "delete": lambda: self.delete_command(draft),
                    }[kind]()
                    execute = lambda uow: self.catalogue(unit_of_work_factory=uow).execute(command)
                elif kind == "plan":
                    command = self.plan_command()
                    execute = lambda uow: self.planner(unit_of_work_factory=uow).execute(command)
                elif kind == "saved":
                    self.catalogue().execute(self.select_command(draft, key="select-saved"))
                    command = self.prepare_command(draft)
                    execute = lambda uow: self.program(uow=uow).prepare(command)
                elif kind == "standalone":
                    command = self.graph_command()
                    execute = lambda uow: GraphAuthoringService(
                        uow, clock=lambda: NOW, graph_id_factory=lambda: uuid.uuid4().hex,
                    ).set_desired_graph(command)
                elif kind == "desired-command":
                    value = self.graph_command()
                    command = SetDesiredGraph(
                        self.sessions["workspace-a"], value.workspace_id, value.actor_id,
                        value.graph, value.expected_desired_graph_id, IdempotencyKey("desired-a"),
                        value.expected_desired_realized_projection_id, value.expected_desired_graph_revision,
                    )
                    execute = lambda uow: DesiredGraphCommandService(
                        uow, clock=lambda: NOW, id_factory=lambda: uuid.uuid4().hex,
                    ).execute(command)
                else:
                    command = self.publication_command()
                    execute = lambda uow: self.publisher(uow).execute(command)
                before = self.all_truth()
                with self.blocked_command(
                    LIFECYCLE_LOCK, ("receiver-lifecycle:workspace-a",), execute,
                ) as future:
                    self.assert_row_lockable(SESSION_LOCK, (self.sessions["workspace-a"],))
                    self.assert_row_lockable(WORKSPACE_LOCK, ("workspace-a",))
                    self.assert_advisory_available("receiver-lifecycle:workspace-b", available=True)
                    self.assertEqual(self.all_truth(), before)
                self.assertIsNotNone(future.result(timeout=1))

    def test_guard_is_owned_by_exact_workspace_and_transaction(self):
        command = self.graph_command()
        before = self.all_truth()
        statements = []
        with self.observed_uow(statements)() as outer:
            guard = self.guard(outer)
            self.guard(outer)  # Exact-key transaction reentry is legal.
            self.assert_advisory_available("receiver-lifecycle:workspace-a", available=False)
            self.assert_advisory_available("receiver-lifecycle:workspace-b", available=True)
            with self.observed_uow(statements)() as other:
                with self.assertRaises(GraphAuthoringError):
                    set_desired_graph_in_unit_of_work(
                        other, command, lifecycle_guard=guard, graph_id="foreign-uow", created_at=NOW,
                    )
            with self.assertRaises(GraphAuthoringError):
                set_desired_graph_in_unit_of_work(
                    outer, replace(command, workspace_id="workspace-b"),
                    lifecycle_guard=guard, graph_id="foreign-workspace", created_at=NOW,
                )
        with self.assertRaises(UnitOfWorkStateError):
            set_desired_graph_in_unit_of_work(
                outer, command, lifecycle_guard=guard, graph_id="stale-uow", created_at=NOW,
            )
        self.assert_advisory_available("receiver-lifecycle:workspace-a", available=True)
        self.assertEqual(self.all_truth(), before)
        self.assertFalse(any(sql.startswith(("INSERT", "UPDATE", "DELETE")) for sql in statements))

    def test_desired_graph_helper_requires_guard_and_rolls_back_with_its_caller(self):
        command, before = self.graph_command(), self.all_truth()
        with self.unit_of_work() as uow:
            with self.assertRaises(TypeError):
                set_desired_graph_in_unit_of_work(uow, command, graph_id="unprepared", created_at=NOW)
        with self.unit_of_work() as uow:
            result = set_desired_graph_in_unit_of_work(
                uow, command, lifecycle_guard=self.guard(uow), graph_id="prepared", created_at=NOW,
            )
            self.assertEqual(result.workspace.desired_graph_id, "prepared")
            # No commit request: graph/projection/pointer must all roll back.
        self.assertEqual(self.all_truth(), before)

    def prepare(self, uow, command):
        # The explicit missing-contract assertion keeps target red behavioral,
        # rather than letting a changed signature break test collection.
        self.assertTrue(callable(getattr(uow.stores.graphs, "lock_receiver_lifecycle", None)),
                        "#1896 graph-owned lifecycle guard is missing")
        return publication.prepare_desired_realized_projection_publication(
            uow, command.workspace_id, command.session_id, command.idempotency_key.value,
        )

    def publish_prepared(self, uow, command, prepared):
        return publication.publish_desired_realized_projection_in_unit_of_work(
            uow, command, prepared=prepared, created_at=NOW, action_id="prepared-action",
        )

    def test_publication_preparation_rejects_wrong_owner_workspace_session_key_and_stale_context(self):
        command, before = self.publication_command(), self.all_truth()
        other_session = self.start_session("workspace-a")
        before = self.all_truth()
        statements = []
        with self.observed_uow(statements)() as outer:
            prepared = self.prepare(outer, command)
            with self.observed_uow(statements)() as other:
                with self.assertRaises(publication.DesiredRealizedProjectionPublicationError):
                    self.publish_prepared(other, command, prepared)
            with self.unit_of_work() as other:
                foreign_projection = other.stores.realized_graphs.identity_for_authored(
                    "workspace-b", "workspace-b-current")
            foreign = replace(command, workspace_id="workspace-b",
                expected_authored_graph_id="workspace-b-current", projection=foreign_projection)
            for changed in (foreign, replace(command, session_id=other_session),
                            replace(command, idempotency_key=IdempotencyKey("different-key"))):
                with self.subTest(changed=changed.idempotency_key.value):
                    with self.assertRaises(publication.DesiredRealizedProjectionPublicationError):
                        self.publish_prepared(outer, changed, prepared)
        with self.assertRaises(UnitOfWorkStateError):
            self.publish_prepared(outer, command, prepared)
        with self.observed_uow(statements)() as other:
            with self.assertRaises(publication.DesiredRealizedProjectionPublicationError):
                self.publish_prepared(other, command, prepared)
        self.assertEqual(self.all_truth(), before)
        self.assertFalse(any(sql.startswith(("INSERT", "UPDATE", "DELETE")) for sql in statements))

    def test_completed_draft_and_publication_replay_bypass_fresh_lifecycle_guard(self):
        commands = (self.create_command(key="replay-create"), self.publication_command())
        for command in commands:
            service = (self.publisher(self.unit_of_work)
                if isinstance(command, publication.PublishDesiredRealizedProjection) else self.catalogue())
            accepted = service.execute(command)
            before = self.all_truth()
            blocker = self.lock_connection()
            blocker.execute(LIFECYCLE_LOCK, ("receiver-lifecycle:workspace-a",))
            with ThreadPoolExecutor(max_workers=1) as pool:
                try:
                    replay = pool.submit(service.execute, command).result(timeout=3)
                finally:
                    blocker.rollback()
                    blocker.close()
            if isinstance(command, publication.PublishDesiredRealizedProjection):
                self.assertTrue(replay.replayed)
                self.assertEqual(replay.action, accepted.action)
            else:
                self.assertEqual(replay, accepted)
            self.assertEqual(self.all_truth(), before)

    def test_publication_requires_preparation_and_keeps_caller_rollback_atomic(self):
        command, before = self.publication_command(), self.all_truth()
        with self.unit_of_work() as uow:
            with self.assertRaises(TypeError):
                publication.publish_desired_realized_projection_in_unit_of_work(
                    uow, command, created_at=NOW, action_id="unprepared")
        with self.unit_of_work() as uow:
            result = self.publish_prepared(uow, command, self.prepare(uow, command))
            self.assertEqual(result.desired_graph_revision, command.expected_desired_graph_revision + 1)
        self.assertEqual(self.all_truth(), before)
