"""Real-store setup and transaction observation for #1763 owner laws."""

from dataclasses import replace
from importlib import import_module
import threading
import uuid

import psycopg

from control_plane_kit_core.operations import operator_command_http_routes
from control_plane_kit_operations.planning import ActivityPlanningCommandService, RequestActivityPlan
from control_plane_kit_operations.postgres import PostgresUnitOfWork
from control_plane_kit_operations.workflows import IdempotencyKey
from draft_catalogue_fixture import DraftCatalogueFixture, NOW, principal


class _ObservedConnection:
    """Delegate every SQL operation; observe lock attempts without replacing stores."""
    def __init__(self, connection, statements, before_workspace=None, after_execute=None):
        self.connection = connection
        self.statements = statements
        self.before_workspace = before_workspace
        self.after_execute = after_execute

    def execute(self, query, parameters=None):
        statement = " ".join(str(query).split())
        self.statements.append(statement)
        if "FROM cpk_workspaces" in statement and "FOR UPDATE" in statement:
            if self.before_workspace is not None:
                self.before_workspace.set()
        cursor = self.connection.execute(query, parameters)
        if self.after_execute is not None:
            self.after_execute(statement)
        return cursor

    def __getattr__(self, name):
        return getattr(self.connection, name)


class DraftSelectionFixture(DraftCatalogueFixture):
    def setUp(self):
        super().setUp()
        routes = {route.route_id for route in operator_command_http_routes()}
        self.assertFalse({"command.desired-topology-draft.select",
                          "command.desired-topology-draft.delete"} - routes,
                         "missing public draft selection/tombstone routes")
        self.api = import_module("control_plane_kit_operations.desired_topology_drafts")
        for name in ("SelectDesiredTopologyDraft", "DeleteDesiredTopologyDraft"):
            self.assertTrue(callable(getattr(self.api, name, None)), f"missing command {name}")

    def workspace(self):
        with self.unit_of_work() as uow:
            return uow.stores.workspaces.get("workspace-a")

    def select_command(self, draft, *, key="select", workspace=None, **changes):
        current = workspace or self.workspace()
        command = self.api.SelectDesiredTopologyDraft(
            context=principal().command_context("workspace-a"),
            session_id=self.sessions["workspace-a"], draft_id=draft.draft_id,
            revision=draft.revision, expected_desired_graph_id=current.desired_graph_id,
            expected_desired_realized_projection_id=current.desired_realized_projection_id,
            expected_desired_graph_revision=current.desired_graph_revision,
            idempotency_key=IdempotencyKey(key),
        )
        return replace(command, **changes)

    def delete_command(self, draft, *, key="delete", **changes):
        command = self.api.DeleteDesiredTopologyDraft(
            context=principal().command_context("workspace-a"),
            session_id=self.sessions["workspace-a"], draft_id=draft.draft_id,
            expected_head_revision=draft.revision, idempotency_key=IdempotencyKey(key),
        )
        return replace(command, **changes)

    def truth(self):
        return {**self.catalogue_truth(), **self.runtime_truth()}

    def effects(self):
        return {name: rows for name, rows in self.runtime_truth().items()
                if name not in {"cpk_workspaces", "cpk_realized_graph_projections"}}

    def restore_legacy_desired(self):
        with self.unit_of_work() as uow:
            uow.stores.workspaces.get_for_update("workspace-a")
            uow.stores.workspaces.set_desired_graph("workspace-a", "workspace-a-current")
            uow.commit()

    def plan_command(self, *, workspace=None, session_id=None, key="plan"):
        current = workspace or self.workspace()
        return RequestActivityPlan(
            session_id=session_id or self.sessions["workspace-a"], workspace_id="workspace-a",
            actor_id="operator-a", expected_current_graph_id=current.current_graph_id,
            expected_current_realized_projection_id=current.current_realized_projection_id,
            expected_desired_graph_id=current.desired_graph_id,
            expected_desired_realized_projection_id=current.desired_realized_projection_id,
            expected_desired_graph_revision=current.desired_graph_revision,
            idempotency_key=IdempotencyKey(key),
        )

    def planner(self, *, clock=None, unit_of_work_factory=None):
        return ActivityPlanningCommandService(unit_of_work_factory or self.unit_of_work,
            clock=clock or (lambda: NOW), id_factory=lambda: uuid.uuid4().hex)

    def observed_uow(self, statements, before_workspace=None, after_execute=None):
        def connect():
            connection = psycopg.connect(self.database_url,
                options=f"-c search_path={self.schema} -c lock_timeout=5000 -c statement_timeout=10000")
            return _ObservedConnection(connection, statements, before_workspace, after_execute)
        return lambda: PostgresUnitOfWork(connect)

    def hold_clock(self):
        reached, release = threading.Event(), threading.Event()
        def clock():
            reached.set()
            if not release.wait(timeout=5):
                raise TimeoutError("owner clock release timed out")
            return NOW
        return clock, reached, release
