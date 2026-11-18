"""#2037 durable permission behavior through Operations' public owner services."""

from dataclasses import replace
import os
import unittest
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

import control_plane_kit_operations as operations
from control_plane_kit_core.identity import (
    AuthenticatedPrincipal, IdentityContractError, PrincipalIdentity, PrincipalKind,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.topology import DeploymentGraph
from control_plane_kit_operations.records import GraphVersionRecord
from control_plane_kit_operations.postgres import PostgresUnitOfWork, SchemaInstallationError, install_schema
from control_plane_kit_operations.workflows import IdempotencyKey
from control_plane_kit_operations.workspaces import CreateWorkspace, WorkspaceCommandService
from tests.lifecycle_lock_fixture import LifecycleLockFixture


class PostgresWorkspacePermissionTests(LifecycleLockFixture, unittest.TestCase):
    def setUp(self):
        self.database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not self.database_url:
            raise RuntimeError("Run ./control-plane-kit-operations/test.sh for Docker PostgreSQL")
        self.schema = "workspace_permissions_" + uuid4().hex
        self.connection = psycopg.connect(self.database_url, autocommit=True)
        self.addCleanup(self.connection.close)
        self.connection.execute(f'CREATE SCHEMA "{self.schema}"')
        self.addCleanup(self.connection.execute, f'DROP SCHEMA "{self.schema}" CASCADE')
        self.connection.execute(f'SET search_path TO "{self.schema}"')
        install_schema(self.connection)
        self.admin = PrincipalIdentity("issuer-a", "admin", PrincipalKind.OPERATOR)
        self.member = PrincipalIdentity("issuer-a", "member", PrincipalKind.OPERATOR)
        self.read = PolicyScope.INSTANCE_WORKSPACE_READ
        self.request = PolicyScope.PLAN_REQUEST
        self.legacy_workspaces = WorkspaceCommandService(
            self.unit_of_work, clock=self.clock, id_factory=lambda: uuid4().hex,
        )
        # Valid existing owner behavior must succeed before the new boundary is tested.
        for workspace in ("workspace-a", "workspace-b"):
            self.legacy_workspaces.create(CreateWorkspace(
                workspace, workspace, self.admin.subject_id, IdempotencyKey("create-" + workspace),
            ))

    @staticmethod
    def clock():
        return "2026-10-25T20:00:00Z"

    def connect(self):
        return psycopg.connect(self.database_url, options=f"-c search_path={self.schema}")

    def unit_of_work(self):
        return PostgresUnitOfWork(self.connect)

    def public(self, name):
        value = getattr(operations, name, None)
        self.assertIsNotNone(value, "durable workspace permission capability is unavailable: " + name)
        return value

    def policy(self, known=None):
        return self.public("WorkspaceAdministrationPolicy")(
            installation_id="installation-a", administrators=(self.admin,),
            known_identities=known if known is not None else (self.admin, self.member),
        )

    def service(self, policy=None, unit_of_work=None):
        return self.public("WorkspacePermissionCommandService")(
            unit_of_work or self.unit_of_work, administration_policy=policy or self.policy(),
            clock=self.clock, id_factory=lambda: uuid4().hex,
        )

    def initialize(self):
        policy = self.policy()
        context = policy.authorize(AuthenticatedPrincipal(self.admin))
        service = self.service(policy)
        service.initialize(context, idempotency_key=IdempotencyKey("initialize-installation"))
        return service, context

    def grant(self, service, context, *, key="grant-a", scopes=None, revision=0):
        command = self.public("GrantWorkspacePermissions")(
            context=context, workspace_id="workspace-a", target=self.member,
            scopes=scopes if scopes is not None else (self.read,),
            expected_revision=revision, idempotency_key=IdempotencyKey(key),
        )
        return command, service.grant(command)

    def resolver(self):
        return self.public("WorkspaceContextResolver")(
            self.unit_of_work, installation_id="installation-a",
        )

    def test_fresh_request_observes_grant_and_revoke_without_new_credentials(self):
        service, context = self.initialize()
        verified = AuthenticatedPrincipal(self.member)
        resolver = self.resolver()
        with self.assertRaises(IdentityContractError):
            resolver.resolve(verified, "workspace-a")
        self.grant(service, context)
        self.assertEqual(resolver.resolve(verified, "workspace-a").granted_scopes, (self.read,))
        service.revoke(self.public("RevokeWorkspacePermissions")(
            context=context, workspace_id="workspace-a", target=self.member,
            scopes=(self.read,), expected_revision=1, idempotency_key=IdempotencyKey("revoke-a"),
        ))
        with self.assertRaises(IdentityContractError):
            resolver.resolve(verified, "workspace-a")

    def test_grant_is_exact_by_workspace_full_identity_and_installation(self):
        service, context = self.initialize()
        self.grant(service, context)
        for identity, workspace in (
            (self.member, "workspace-b"),
            (replace(self.member, issuer="issuer-b"), "workspace-a"),
            (replace(self.member, kind=PrincipalKind.WORKER), "workspace-a"),
        ):
            with self.subTest(identity=identity, workspace=workspace):
                with self.assertRaises(IdentityContractError):
                    self.resolver().resolve(AuthenticatedPrincipal(identity), workspace)
        foreign = self.public("WorkspaceContextResolver")(
            self.unit_of_work, installation_id="installation-b",
        )
        with self.assertRaises(IdentityContractError):
            foreign.resolve(AuthenticatedPrincipal(self.member), "workspace-a")

    def test_new_grant_cannot_expand_an_original_request_ceiling(self):
        service, context = self.initialize()
        self.grant(service, context)
        original = self.resolver().resolve(AuthenticatedPrincipal(self.member), "workspace-a")
        self.grant(service, context, key="grant-request", scopes=(self.request,), revision=1)
        current = self.resolver().resolve(AuthenticatedPrincipal(self.member), "workspace-a")
        self.assertEqual(set(current.granted_scopes), {self.read, self.request})
        authorizer = self.public("DurablePrincipalAuthorizer")(
            self.unit_of_work, installation_id="installation-a",
        )
        self.assertEqual(authorizer.authorize(original.principal, "workspace-a").granted_scopes,
                         (self.read,))

    def test_old_grant_replay_cannot_resurrect_revoked_policy(self):
        service, context = self.initialize()
        command, granted = self.grant(service, context)
        service.revoke(self.public("RevokeWorkspacePermissions")(
            context=context, workspace_id="workspace-a", target=self.member,
            scopes=(self.read,), expected_revision=1, idempotency_key=IdempotencyKey("revoke-a"),
        ))
        replay = self.service().grant(command)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.receipt, granted.receipt)
        with self.assertRaises(IdentityContractError):
            self.resolver().resolve(AuthenticatedPrincipal(self.member), "workspace-a")

    def test_late_authorizer_narrows_original_scopes_after_each_revocation(self):
        service, context = self.initialize()
        self.grant(service, context, scopes=(self.read, self.request))
        original = self.resolver().resolve(AuthenticatedPrincipal(self.member), "workspace-a")
        authorizer = self.public("DurablePrincipalAuthorizer")(
            self.unit_of_work, installation_id="installation-a",
        )
        service.revoke(self.public("RevokeWorkspacePermissions")(
            context=context, workspace_id="workspace-a", target=self.member,
            scopes=(self.request,), expected_revision=1, idempotency_key=IdempotencyKey("revoke-request"),
        ))
        narrowed = authorizer.authorize(original.principal, "workspace-a")
        self.assertEqual(narrowed.granted_scopes, (self.read,))
        service.revoke(self.public("RevokeWorkspacePermissions")(
            context=context, workspace_id="workspace-a", target=self.member,
            scopes=(self.read,), expected_revision=2, idempotency_key=IdempotencyKey("revoke-read"),
        ))
        with self.assertRaises(IdentityContractError):
            authorizer.authorize(original.principal, "workspace-a")
        self.assertEqual(set(original.granted_scopes), {self.read, self.request})

    def test_removed_identity_can_be_revoked_and_reintroduction_does_not_restore_access(self):
        service, context = self.initialize()
        self.grant(service, context)
        removed_policy = self.policy(known=(self.admin,))
        removed_service = self.service(removed_policy)
        removed_context = removed_policy.authorize(AuthenticatedPrincipal(self.admin))
        removed_service.revoke(self.public("RevokeWorkspacePermissions")(
            context=removed_context, workspace_id="workspace-a", target=self.member,
            scopes=(self.read,), expected_revision=1, idempotency_key=IdempotencyKey("revoke-removed"),
        ))
        # Reconstruct with the original known-identity configuration, keeping durable truth.
        restored = self.service(self.policy())
        with self.assertRaises(IdentityContractError):
            self.resolver().resolve(AuthenticatedPrincipal(self.member), "workspace-a")
        self.grant(restored, context, key="explicit-regrant", revision=2)
        self.assertEqual(self.resolver().resolve(AuthenticatedPrincipal(self.member), "workspace-a")
                         .granted_scopes, (self.read,))

    def test_workspace_membership_does_not_supply_administrative_authority(self):
        service, context = self.initialize()
        self.grant(service, context, scopes=tuple(PolicyScope))
        principal = self.resolver().resolve(AuthenticatedPrincipal(self.member), "workspace-a").principal
        with self.assertRaises(self.public("WorkspacePermissionDenied")):
            self.policy().authorize(principal)

    def test_conflicting_replay_and_stale_revision_leave_original_policy(self):
        service, context = self.initialize()
        command, _ = self.grant(service, context)
        with self.assertRaises(self.public("WorkspacePermissionConflict")):
            service.grant(replace(command, scopes=(self.request,)))
        with self.assertRaises(self.public("WorkspacePermissionConflict")):
            service.grant(replace(command, idempotency_key=IdempotencyKey("stale-grant")))
        self.assertEqual(self.resolver().resolve(AuthenticatedPrincipal(self.member), "workspace-a")
                         .granted_scopes, (self.read,))

    def test_admin_creation_records_full_identity_and_truthful_existing_workspace_reuse(self):
        self.initialize()
        second = replace(self.admin, issuer="issuer-b")
        policy = self.public("WorkspaceAdministrationPolicy")(
            installation_id="installation-a", administrators=(self.admin, second),
            known_identities=(self.admin, second, self.member),
        )
        create = getattr(self.legacy_workspaces, "create_as_administrator", None)
        self.assertIsNotNone(create, "full-identity administrative workspace creation is unavailable")
        service = WorkspaceCommandService(self.unit_of_work, clock=self.clock,
            id_factory=lambda: uuid4().hex, administration_policy=policy)
        original = service.create_as_administrator(
            policy.authorize(AuthenticatedPrincipal(self.admin)), workspace_id="workspace-new",
            name="New", idempotency_key=IdempotencyKey("admin-create"),
        )
        reused = service.create_as_administrator(
            policy.authorize(AuthenticatedPrincipal(second)), workspace_id="workspace-new",
            name="New", idempotency_key=IdempotencyKey("admin-create"),
        )
        self.assertEqual(original.receipt.actor, self.admin)
        self.assertEqual(reused.receipt.actor, second)
        self.assertTrue(original.receipt.initialized)
        self.assertFalse(reused.receipt.initialized)
        self.assertEqual(original.receipt.observed_current_graph_id, reused.receipt.observed_current_graph_id)
        later_graph = GraphVersionRecord.from_graph(graph_id="later-graph", workspace_id="workspace-new",
            version=2, graph=DeploymentGraph("later-empty"), created_by=self.admin.subject_id,
            created_at=self.clock())
        with self.unit_of_work() as uow:
            uow.stores.graphs.save(later_graph)
            uow.stores.workspaces.set_current_graph("workspace-new", later_graph.graph_id)
            uow.commit()
        replay = service.create_as_administrator(
            policy.authorize(AuthenticatedPrincipal(self.admin)), workspace_id="workspace-new",
            name="New", idempotency_key=IdempotencyKey("admin-create"),
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.receipt, original.receipt)
        with self.unit_of_work() as uow:
            self.assertEqual(uow.stores.workspaces.get("workspace-new").current_graph_id, later_graph.graph_id)
        self.assertNotEqual(replay.receipt.observed_current_graph_id, later_graph.graph_id)
        with self.assertRaises(self.public("WorkspacePermissionConflict")):
            service.create_as_administrator(policy.authorize(AuthenticatedPrincipal(self.admin)),
                workspace_id="workspace-conflict", name="Conflict",
                idempotency_key=IdempotencyKey("admin-create"))
        self.assertIsNone(self.connection.execute(
            "SELECT workspace_id FROM cpk_workspaces WHERE workspace_id='workspace-conflict'"
        ).fetchone())

    def test_admin_receipt_failure_rolls_back_workspace_initialization(self):
        _, context = self.initialize()
        self.assertIsNotNone(getattr(self.legacy_workspaces, "create_as_administrator", None),
                             "atomic administrative workspace creation is unavailable")
        # Real PostgreSQL failure at the receipt write, not a replacement store.
        self.connection.execute("ALTER TABLE cpk_workspace_administration_receipts "
                                "ADD CONSTRAINT test_refuse_admin_receipt CHECK (false)")
        service = WorkspaceCommandService(self.unit_of_work, clock=self.clock,
            id_factory=lambda: uuid4().hex, administration_policy=self.policy())
        with self.assertRaises(psycopg.errors.CheckViolation):
            service.create_as_administrator(context, workspace_id="workspace-rollback",
                name="Rollback", idempotency_key=IdempotencyKey("admin-rollback"))
        self.assertIsNone(self.connection.execute(
            "SELECT workspace_id FROM cpk_workspaces WHERE workspace_id='workspace-rollback'"
        ).fetchone())
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM cpk_workspace_initializations WHERE workspace_id='workspace-rollback'"
        ).fetchone()[0], 0)

    def test_malformed_installation_receipt_is_rejected_by_replay_and_startup(self):
        service, context = self.initialize()
        original = self.connection.execute(
            "SELECT receipt FROM cpk_workspace_permission_installation WHERE singleton"
        ).fetchone()[0]
        for invalid in (
            dict(original, unexpected="must-not-return"),
            dict(original, installation_id="different-installation"),
            dict(original, occurred_at="not-a-time"),
        ):
            with self.subTest(invalid=tuple(invalid)):
                self.connection.execute("UPDATE cpk_workspace_permission_installation SET receipt=%s",
                                        (Jsonb(invalid),))
                with self.assertRaises(self.public("WorkspacePermissionError")) as caught:
                    service.initialize(context, idempotency_key=IdempotencyKey("initialize-installation"))
                self.assertNotIn("must-not-return", str(caught.exception))
                with self.assertRaises(SchemaInstallationError):
                    install_schema(self.connection)
        self.connection.execute("UPDATE cpk_workspace_permission_installation SET receipt=%s",
                                (Jsonb(original),))
        self.assertTrue(service.initialize(context, idempotency_key=IdempotencyKey("initialize-installation"))
                        .replayed)

    def test_concurrent_admin_key_conflict_cannot_leave_a_second_workspace(self):
        _, context = self.initialize()

        def create(factory, workspace):
            service = WorkspaceCommandService(factory, clock=self.clock,
                id_factory=lambda: uuid4().hex, administration_policy=self.policy())
            return service.create_as_administrator(context, workspace_id=workspace,
                name=workspace, idempotency_key=IdempotencyKey("concurrent-admin-key"))

        first, second = self.opposing_commands(
            lambda factory: create(factory, "workspace-leader"),
            lambda factory: create(factory, "workspace-follower"),
            lambda query, parameters: "pg_advisory_xact_lock" in query and parameters
                and str(parameters[0]).startswith("workspace-administration:"),
        )
        self.assertTrue(first.result().receipt.initialized)
        with self.assertRaises(self.public("WorkspacePermissionConflict")):
            second.result()
        self.assertEqual(self.connection.execute(
            "SELECT workspace_id FROM cpk_workspaces WHERE workspace_id IN (%s,%s) ORDER BY workspace_id",
            ("workspace-leader", "workspace-follower"),
        ).fetchall(), [("workspace-leader",)])
        self.assertEqual(self.connection.execute(
            "SELECT count(*) FROM cpk_workspace_administration_receipts WHERE idempotency_key=%s",
            ("concurrent-admin-key",),
        ).fetchone()[0], 1)

    def test_accepted_noop_deltas_advance_revision_without_changing_scopes(self):
        service, context = self.initialize()
        self.grant(service, context)
        _, repeated = self.grant(service, context, key="same-scope-new-command", revision=1)
        self.assertEqual((repeated.receipt.prior_revision, repeated.receipt.revision), (1, 2))
        unchanged = service.revoke(self.public("RevokeWorkspacePermissions")(
            context=context, workspace_id="workspace-a", target=self.member, scopes=(self.request,),
            expected_revision=2, idempotency_key=IdempotencyKey("remove-absent-scope"),
        ))
        self.assertEqual(unchanged.receipt.revision, 3)
        self.assertEqual(unchanged.receipt.resulting_scopes, (self.read,))
        self.assertEqual(self.resolver().resolve(AuthenticatedPrincipal(self.member), "workspace-a")
                         .granted_scopes, (self.read,))

    def test_receipt_failure_rolls_back_initial_permission_row(self):
        service, context = self.initialize()
        self.connection.execute("ALTER TABLE cpk_workspace_permission_receipts "
                                "ADD CONSTRAINT test_refuse_permission_receipt CHECK (false)")
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.grant(service, context)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_workspace_permissions")
                         .fetchone()[0], 0)
        with self.assertRaises(IdentityContractError):
            self.resolver().resolve(AuthenticatedPrincipal(self.member), "workspace-a")

    def test_concurrent_expected_revision_has_one_winner(self):
        service, context = self.initialize()
        self.grant(service, context)

        def change(factory, key):
            return self.grant(self.service(unit_of_work=factory), context, key=key,
                              scopes=(self.request,), revision=1)[1]

        first, second = self.opposing_commands(
            lambda factory: change(factory, "leader-revision"),
            lambda factory: change(factory, "follower-revision"),
            lambda query, parameters: "FROM cpk_workspace_permissions" in query
                and query.endswith("FOR UPDATE"),
        )
        self.assertEqual(first.result().receipt.revision, 2)
        with self.assertRaises(self.public("WorkspacePermissionConflict")):
            second.result()
        self.assertEqual(self.connection.execute("SELECT count(*) FROM cpk_workspace_permission_receipts")
                         .fetchone()[0], 2)

    def test_admin_reads_are_bounded_scoped_and_keep_history_distinct_from_current_policy(self):
        service, context = self.initialize()
        self.grant(service, context)
        service.revoke(self.public("RevokeWorkspacePermissions")(
            context=context, workspace_id="workspace-a", target=self.member, scopes=(self.read,),
            expected_revision=1, idempotency_key=IdempotencyKey("revoke-for-history"),
        ))
        reads = self.public("WorkspacePermissionReadService")(
            self.unit_of_work, administration_policy=self.policy(known=(self.admin,)),
        )
        current = reads.inspect(context, workspace_id="workspace-a", target=self.member)
        self.assertEqual((current.scopes, current.revision), ((), 2))
        first = reads.history(context, workspace_id="workspace-a", target=self.member, limit=1)
        self.assertEqual(tuple(item.operation for item in first.items), ("grant",))
        self.assertIsNotNone(first.next_cursor)
        second = reads.history(context, workspace_id="workspace-a", target=self.member,
                               limit=1, cursor=first.next_cursor)
        self.assertEqual(tuple(item.operation for item in second.items), ("revoke",))
        self.assertIsNone(second.next_cursor)
        with self.assertRaises(self.public("WorkspacePermissionError")):
            reads.history(context, workspace_id="workspace-b", target=self.member,
                          limit=1, cursor=first.next_cursor)
        with self.assertRaises(self.public("WorkspacePermissionError")):
            reads.list(context, workspace_id="workspace-a", limit=101)
        page = reads.list(context, workspace_id="workspace-a", limit=1)
        self.assertEqual(page.items, (current,))

    def test_read_authority_and_full_identity_cursor_boundaries(self):
        service, context = self.initialize()
        self.grant(service, context)
        other_issuer = replace(self.member, issuer="issuer-b")
        other_kind = replace(self.member, kind=PrincipalKind.WORKER)
        policy = self.policy(known=(self.admin, self.member, other_issuer, other_kind))
        service = self.service(policy)
        for target in (other_issuer, other_kind):
            service.grant(self.public("GrantWorkspacePermissions")(
                context=context, workspace_id="workspace-a", target=target, scopes=(self.read,),
                expected_revision=0, idempotency_key=IdempotencyKey("grant-other"),
            ))
        reads = self.public("WorkspacePermissionReadService")(self.unit_of_work, administration_policy=policy)
        first = reads.list(context, workspace_id="workspace-a", limit=1)
        second = reads.list(context, workspace_id="workspace-a", limit=1, cursor=first.next_cursor)
        third = reads.list(context, workspace_id="workspace-a", limit=1, cursor=second.next_cursor)
        self.assertEqual(tuple(page.items[0].target for page in (first, second, third)),
                         (self.member, other_kind, other_issuer))
        self.assertIsNone(third.next_cursor)
        for cursor in (first.next_cursor.descriptor(),
                       replace(first.next_cursor, installation_id="installation-b")):
            with self.assertRaises(self.public("WorkspacePermissionError")):
                reads.list(context, workspace_id="workspace-a", limit=1, cursor=cursor)
        with self.assertRaises(self.public("WorkspacePermissionError")):
            reads.list(context, workspace_id="workspace-a", limit=True)
        self.grant(service, context, key="second-history-entry", revision=1)
        history = reads.history(context, workspace_id="workspace-a", target=self.member, limit=1)
        with self.assertRaises(self.public("WorkspacePermissionError")):
            reads.history(context, workspace_id="workspace-a", target=other_kind,
                          limit=1, cursor=history.next_cursor)
        ordinary = self.public("WorkspaceAdministrationContext")(self.member, "installation-a")
        for read in (
            lambda: reads.inspect(ordinary, workspace_id="workspace-a", target=self.member),
            lambda: reads.list(ordinary, workspace_id="workspace-a"),
            lambda: reads.history(ordinary, workspace_id="workspace-a", target=self.member),
        ):
            with self.assertRaises(self.public("WorkspacePermissionDenied")):
                read()


if __name__ == "__main__":
    unittest.main()
