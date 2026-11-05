"""#2041 current operator visibility, using the owning PostgreSQL services."""
from dataclasses import replace
import importlib.util
import os
import unittest
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from control_plane_kit_core.identity import AuthenticatedPrincipal, PrincipalIdentity, PrincipalKind
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.workspace_permissions import (
    GrantWorkspacePermissions, RevokeWorkspacePermissions, WorkspaceAdministrationPolicy,
    WorkspacePermissionCommandService,
)
from control_plane_kit_operations.workspaces import CreateWorkspace, WorkspaceCommandService
from control_plane_kit_operations.workflows import IdempotencyKey


MODULE = "control_plane_kit_operations.workspace_inventory"


class PostgresWorkspaceInventoryTests(unittest.TestCase):
    def setUp(self):
        self.database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not self.database_url:
            raise RuntimeError("Run the owning Operations Docker suite")
        self.schema = "workspace_inventory_" + uuid4().hex
        self.connection = psycopg.connect(self.database_url, autocommit=True)
        self.addCleanup(self.connection.close)
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.addCleanup(self.connection.execute, f'DROP SCHEMA "{self.schema}" CASCADE')
        self.connection.execute(f'SET search_path TO "{self.schema}"')
        install_schema(self.connection)
        self.admin = PrincipalIdentity("issuer", "admin", PrincipalKind.OPERATOR)
        self.member = PrincipalIdentity("issuer", "member", PrincipalKind.OPERATOR)
        self.other = replace(self.member, issuer="other-issuer")
        self.service_identity = replace(self.member, kind=PrincipalKind.SERVICE)
        self.worker = replace(self.member, kind=PrincipalKind.WORKER)
        self.read = PolicyScope.INSTANCE_WORKSPACE_READ
        self.workspaces = WorkspaceCommandService(self.unit_of_work, clock=self.clock, id_factory=lambda: uuid4().hex)
        for suffix, name in (("a", "Zulu"), ("b", "Hidden"), ("c", "Alpha"), ("d", "Other")):
            self.create("workspace-" + suffix, name)
        policy = WorkspaceAdministrationPolicy(installation_id="installation-a",
            administrators=(self.admin,), known_identities=(self.admin, self.member, self.other,
                self.service_identity, self.worker))
        self.context = policy.authorize(AuthenticatedPrincipal(self.admin))
        self.permissions = WorkspacePermissionCommandService(self.unit_of_work, administration_policy=policy,
            clock=self.clock, id_factory=lambda: uuid4().hex)
        self.permissions.initialize(self.context, idempotency_key=IdempotencyKey("initialize"))
        self.grant("workspace-a")
        self.grant("workspace-c")
        self.grant("workspace-b", scopes=(PolicyScope.PLAN_REQUEST,))
        self.grant("workspace-d", target=self.other)

    @staticmethod
    def clock():
        return "2026-11-05T20:00:00Z"

    def connect(self):
        return psycopg.connect(self.database_url, options=f"-c search_path={self.schema}")

    def unit_of_work(self):
        return PostgresUnitOfWork(self.connect)

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec(MODULE), "authorization-filtered workspace inventory is missing")
        return importlib.import_module(MODULE)

    def reader(self, *, installation="installation-a", factory=None):
        return self.api().WorkspaceInventoryReadService(factory or self.unit_of_work, installation_id=installation)

    def page(self, *, principal=None, **kwargs):
        return self.reader().read(AuthenticatedPrincipal(principal or self.member), **kwargs).descriptor()

    def create(self, workspace, name):
        self.workspaces.create(CreateWorkspace(workspace, name, self.admin.subject_id, IdempotencyKey("create-" + workspace)))

    def grant(self, workspace, *, target=None, scopes=None, revision=0):
        return self.permissions.grant(GrantWorkspacePermissions(context=self.context, workspace_id=workspace,
            target=target or self.member, scopes=(self.read,) if scopes is None else scopes,
            expected_revision=revision, idempotency_key=IdempotencyKey(uuid4().hex)))

    def revoke(self, workspace, *, revision=1):
        self.permissions.revoke(RevokeWorkspacePermissions(context=self.context, workspace_id=workspace,
            target=self.member, scopes=(self.read,), expected_revision=revision,
            idempotency_key=IdempotencyKey(uuid4().hex)))

    def error(self, call, status):
        with self.assertRaises(self.api().WorkspaceInventoryError) as caught:
            call()
        self.assertEqual(caught.exception.status, status)
        return str(caught.exception)

    @staticmethod
    def forbidden_lookup():
        raise AssertionError("inventory touched storage before eligibility/cursor validation")

    def test_filtered_page_and_lookahead_use_only_readable_rows(self):
        first = self.page(limit=1)
        self.assertEqual(first["items"], [{"workspace_id": "workspace-a", "name": "Zulu", "lifecycle": "created"}])
        self.assertEqual(set(first), {"items", "next_cursor"})
        self.assertIsInstance(first["next_cursor"], str)
        second = self.page(limit=1, after=first["next_cursor"])
        self.assertEqual(second["items"], [{"workspace_id": "workspace-c", "name": "Alpha", "lifecycle": "created"}])
        self.assertIsNone(second["next_cursor"])

    def test_operator_kind_is_required_before_cursor_or_store_access(self):
        for target in (self.worker, self.service_identity):
            self.grant("workspace-b", target=target)
            with self.subTest(kind=target.kind):
                self.error(lambda: self.reader(factory=self.forbidden_lookup).read(
                    AuthenticatedPrincipal(target), after="malformed"), 403)
        self.assertEqual([x["workspace_id"] for x in self.page()["items"]], ["workspace-a", "workspace-c"])

    def test_full_principal_scope_and_admin_without_grants(self):
        self.assertEqual([x["workspace_id"] for x in self.page(principal=self.other)["items"]], ["workspace-d"])
        self.assertEqual(self.page(principal=self.admin), {"items": [], "next_cursor": None})
        first = self.page(limit=1)
        self.error(lambda: self.reader(factory=self.forbidden_lookup).read(
            AuthenticatedPrincipal(self.other), after=first["next_cursor"]), 400)
        self.error(lambda: self.reader(installation="foreign", factory=self.forbidden_lookup).read(
            AuthenticatedPrincipal(self.member), after=first["next_cursor"]), 400)

    def test_missing_or_foreign_installation_is_unavailable_not_empty(self):
        self.error(lambda: self.reader(installation="foreign").read(AuthenticatedPrincipal(self.member)), 503)
        # Separate namespace proves absence without changing retained policy.
        schema = "inventory_uninitialized_" + uuid4().hex
        self.connection.execute(f'CREATE SCHEMA "{schema}"')
        self.addCleanup(self.connection.execute, f'DROP SCHEMA "{schema}" CASCADE')
        with psycopg.connect(self.database_url, autocommit=True) as connection:
            connection.execute(f'SET search_path TO "{schema}"')
            install_schema(connection)
        def empty_factory():
            return PostgresUnitOfWork(lambda: psycopg.connect(self.database_url, options=f"-c search_path={schema}"))
        self.error(lambda: self.reader(factory=empty_factory).read(AuthenticatedPrincipal(self.member)), 503)

    def test_current_grants_and_first_page_refresh_have_live_keyset_semantics(self):
        first = self.page(limit=1)
        self.revoke("workspace-c")
        self.assertEqual(self.page(limit=1, after=first["next_cursor"]), {"items": [], "next_cursor": None})
        self.grant("workspace-b", revision=1)
        self.create("workspace-0", "Earlier")
        self.grant("workspace-0")
        self.assertEqual([x["workspace_id"] for x in self.page(after=first["next_cursor"])["items"]], ["workspace-b"])
        self.assertEqual([x["workspace_id"] for x in self.page()["items"]], ["workspace-0", "workspace-a", "workspace-b"])

    def test_hidden_missing_and_revoked_anchors_are_indistinguishable(self):
        api = self.api()
        revoked = self.page(limit=1)["next_cursor"]
        self.revoke("workspace-a")
        hidden = api.WorkspaceInventoryCursor("installation-a", self.member, "workspace-b").encode()
        missing = api.WorkspaceInventoryCursor("installation-a", self.member, "never-existed").encode()
        messages = [self.error(lambda token=token: self.reader().read(AuthenticatedPrincipal(self.member), after=token), 409)
                    for token in (revoked, hidden, missing)]
        self.assertEqual(len(set(messages)), 1)
        self.assertNotIn("workspace", messages[0].replace("workspace inventory", ""))
        self.assertNotIn("never-existed", messages[0])

    def test_hidden_changes_do_not_change_items_or_cursor(self):
        before = self.page(limit=1)
        self.create("workspace-bb", "Foreign hidden row")
        self.grant("workspace-bb", target=self.other)
        self.connection.execute("UPDATE cpk_workspaces SET name='Different hidden name' WHERE workspace_id='workspace-b'")
        self.assertEqual(self.page(limit=1), before)
        self.permissions.revoke(RevokeWorkspacePermissions(context=self.context, workspace_id="workspace-bb",
            target=self.other, scopes=(self.read,), expected_revision=1, idempotency_key=IdempotencyKey(uuid4().hex)))
        self.assertEqual(self.page(limit=1), before)

    def test_display_name_is_bounded_without_changing_workspace_identity(self):
        self.api()
        self.connection.execute("UPDATE cpk_workspaces SET name=%s WHERE workspace_id='workspace-a'", ("x" * 1024,))
        item = self.page(limit=1)["items"][0]
        self.assertEqual(item["workspace_id"], "workspace-a")
        self.assertEqual(item["name"], "x" * 511 + "…")
        self.assertEqual(self.connection.execute("SELECT length(name) FROM cpk_workspaces WHERE workspace_id='workspace-a'").fetchone()[0], 1024)

    def test_typed_validation_includes_candidates_lookahead_and_anchor(self):
        self.api()
        def corrupt(workspace, scopes):
            self.connection.execute("UPDATE cpk_workspace_permissions SET scopes=%s WHERE workspace_id=%s AND subject_id='member' AND kind='operator'",
                (Jsonb(scopes), workspace))
        corrupt("workspace-a", [self.read.value, "unknown-scope"])
        self.error(lambda: self.page(limit=1), 503)
        corrupt("workspace-a", [self.read.value])
        corrupt("workspace-c", [self.read.value, self.read.value])
        self.error(lambda: self.page(limit=1), 503)
        corrupt("workspace-c", [self.read.value])
        cursor = self.page(limit=1)["next_cursor"]
        corrupt("workspace-a", [self.read.value, "unknown-scope"])
        self.error(lambda: self.page(limit=1, after=cursor), 503)

    def test_arguments_and_unavailability_are_bounded(self):
        for limit in (0, 101, True, "1"):
            with self.subTest(limit=limit):
                self.error(lambda: self.reader(factory=self.forbidden_lookup).read(AuthenticatedPrincipal(self.member), limit=limit), 400)
        self.error(lambda: self.reader(factory=self.forbidden_lookup).read(AuthenticatedPrincipal(self.member), after="not-a-cursor"), 400)
        def unavailable():
            raise RuntimeError("private-store-detail")
        message = self.error(lambda: self.reader(factory=unavailable).read(AuthenticatedPrincipal(self.member)), 503)
        self.assertNotIn("private-store-detail", message)
