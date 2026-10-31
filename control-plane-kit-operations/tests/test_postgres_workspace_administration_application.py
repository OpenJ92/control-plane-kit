"""#2053 public administration projection through its actual durable owners."""
from dataclasses import replace
import json
import unittest
from uuid import uuid4

import control_plane_kit_operations as operations
from control_plane_kit_core.identity import AuthenticatedPrincipal, PrincipalIdentity, PrincipalKind, WorkspaceGrant
from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.policies import PolicyScope
from tests import test_postgres_managed_runtime_assignments as management_fixtures
from tests.test_postgres_application_initial_context import Request, CountingResolver


class PostgresWorkspaceAdministrationApplicationTests(unittest.TestCase):
    setUp = management_fixtures.PostgresManagedRuntimeAssignmentTests.setUp
    clock = staticmethod(management_fixtures.PostgresManagedRuntimeAssignmentTests.clock)
    connect = management_fixtures.PostgresManagedRuntimeAssignmentTests.connect
    unit_of_work = management_fixtures.PostgresManagedRuntimeAssignmentTests.unit_of_work
    public = management_fixtures.PostgresManagedRuntimeAssignmentTests.public
    register = management_fixtures.PostgresManagedRuntimeAssignmentTests.register
    capability_policy = management_fixtures.PostgresManagedRuntimeAssignmentTests.capability_policy

    def application(self, policy=None, *, permission_factory=None, workspace_factory=None):
        adapter_type = getattr(operations, "CpkServerWorkspaceAdministrationService", None)
        self.assertIsNotNone(adapter_type, "shared workspace administration service is unavailable")
        policy = policy or self.admin_policy
        permissions = operations.WorkspacePermissionCommandService(self.unit_of_work,
            administration_policy=policy, clock=self.clock, id_factory=lambda: uuid4().hex)
        adapter = adapter_type(
            administration_policy=policy,
            workspaces=operations.WorkspaceCommandService(workspace_factory or self.unit_of_work,
                administration_policy=policy, clock=self.clock, id_factory=lambda: uuid4().hex),
            permission_commands=permissions,
            permission_reads=operations.WorkspacePermissionReadService(permission_factory or self.unit_of_work,
                administration_policy=policy),
            management_commands=operations.ManagedRuntimeAssignmentCommandService(self.unit_of_work,
                administration_policy=policy, capability_policy=self.capability_policy(),
                clock=self.clock, id_factory=lambda: uuid4().hex),
            management_reads=operations.ManagedRuntimeAssignmentReadService(self.unit_of_work,
                administration_policy=policy),
        )
        # Only administration and real workspace reads are exercised here;
        # unrelated command dependencies remain unconfigured, not simulated.
        services = operations.cpk_server_services(unit_of_work_factory=self.unit_of_work,
            planning=None, approval=None, admission=None, lifecycle=None, execution=None,
            workspace_administration=adapter)
        resolver = CountingResolver(self.unit_of_work)
        app = operations.CpkServerOperationsApplication(services, authority_mode="dynamic",
            workspace_context_resolver=resolver)
        return app, resolver

    def make_request(self, route, payload=None, *, workspace="workspace-a", surface="http", principal=None):
        payload = dict(payload or {})
        if route == "command.workspace-administration.create":
            path = {}
            payload["workspace_id"] = workspace
        elif surface == "http":
            path = {"workspace_id": workspace}
        else:
            path = {}
            payload["workspace_id"] = workspace
        return Request(surface, route, ControlPlaneServiceRole.AUTHORIZATION, path, payload,
            principal or AuthenticatedPrincipal(self.admin))

    def permission(self, *, revision=0, key="grant-a", target=None):
        return dict(target=(target or self.member).descriptor(), scopes=[self.read.value],
            expected_revision=revision, idempotency_key=key)

    def management(self, *, revision=0, key="management-a"):
        return dict(runtime_id="docker", authority_ref=self.reference.reference_id,
            registration_id=self.authorities["workspace-a"].registration_id,
            capability_id="installer-local-docker", expected_revision=revision, idempotency_key=key)

    def denied(self, call, status):
        with self.assertRaises(operations.CpkServerApplicationError) as error:
            call()
        self.assertEqual(error.exception.status, status)
        return error.exception

    def test_admin_creation_needs_no_target_grant_and_returns_original_receipt_on_replay(self):
        app, resolver = self.application()
        request = self.make_request("command.workspace-administration.create",
            {"name": "Created", "idempotency_key": "create-new", "metadata": {"purpose": "fixture"}},
            workspace="created-without-member")
        result = app.handle(request)
        self.assertEqual(result["receipt"]["actor"], self.admin.descriptor())
        self.assertTrue(result["receipt"]["initialized"])
        replay = app.handle(request)
        self.assertEqual(replay, {"receipt": result["receipt"], "replayed": True})
        self.assertEqual(resolver.calls, [])

    def test_public_permission_changes_affect_next_ordinary_request_and_keep_historical_replay(self):
        app, _ = self.application()
        ordinary = Request("http", "read.workspace", ControlPlaneServiceRole.READS,
            {"workspace_id": "workspace-a"}, {}, AuthenticatedPrincipal(self.member))
        self.denied(lambda: app.handle(ordinary), 403)
        grant = self.make_request("command.workspace-permission.grant", self.permission())
        first = app.handle(grant)
        self.assertEqual(app.handle(ordinary)["workspace"]["workspace_id"], "workspace-a")
        app.handle(self.make_request("command.workspace-permission.revoke",
            self.permission(revision=1, key="revoke-a"), surface="mcp"))
        self.denied(lambda: app.handle(ordinary), 403)
        self.assertEqual(app.handle(grant), {"receipt": first["receipt"], "replayed": True})
        current = app.handle(self.make_request("read.workspace-permission", {"target": self.member.descriptor()}))
        self.assertEqual((current["revision"], current["scopes"]), (2, []))
        page = app.handle(self.make_request("read.workspace-permissions", surface="mcp"))
        self.assertEqual(page["items"], [current])

    def test_admin_identity_is_separate_from_broad_ordinary_grants_and_cannot_be_supplied_in_payload(self):
        app, resolver = self.application()
        ordinary = AuthenticatedPrincipal(self.member, (WorkspaceGrant("workspace-a", tuple(PolicyScope)),))
        self.denied(lambda: app.handle(self.make_request("command.workspace-permission.grant",
            self.permission(), principal=ordinary)), 403)
        self.denied(lambda: app.handle(self.make_request("command.workspace-permission.grant",
            {**self.permission(), "actor": self.admin.descriptor()})), 400)
        self.assertEqual(resolver.calls, [])

    def test_management_commands_reads_and_history_share_typed_conflict_and_not_found_results(self):
        app, resolver = self.application()
        target = {"runtime_id": "docker", "authority_ref": self.reference.reference_id}
        self.denied(lambda: app.handle(self.make_request("read.managed-runtime-assignment", target)), 404)
        first = app.handle(self.make_request("command.managed-runtime-access.grant", self.management()))
        current = app.handle(self.make_request("read.managed-runtime-assignment", target, surface="mcp"))
        self.assertEqual(current, first["receipt"]["resulting_assignment"])
        self.denied(lambda: app.handle(self.make_request("command.managed-runtime-access.grant",
            self.management(revision=1))), 409)
        app.handle(self.make_request("command.managed-runtime-access.revoke",
            {**target, "expected_revision": 1, "idempotency_key": "revoke-management"}))
        page = app.handle(self.make_request("read.managed-runtime-assignment-history", target))
        self.assertEqual(len(page["items"]), 2)
        assignments = app.handle(self.make_request("read.managed-runtime-assignments"))
        self.assertEqual(assignments["items"][0]["status"], "revoked")
        self.assertEqual(resolver.calls, [])

    def test_management_integrity_failure_is_unavailable_not_caller_conflict(self):
        app, _ = self.application()
        request = self.make_request("command.managed-runtime-access.grant", self.management())
        app.handle(request)
        self.connection.execute("UPDATE cpk_managed_runtime_assignment_receipts SET intent_fingerprint=%s",
            ("0" * 64,))
        self.denied(lambda: app.handle(request), 503)
        missing_dependency = self.management(key="missing-dependency")
        missing_dependency["registration_id"] = "not-registered"
        self.denied(lambda: app.handle(self.make_request("command.managed-runtime-access.grant", missing_dependency)), 503)

    def test_closed_inputs_and_scope_bound_cursors_are_rejected(self):
        reads = []
        creations = []
        def counted_reads():
            reads.append("read")
            return self.unit_of_work()
        def counted_creations():
            creations.append("create")
            return self.unit_of_work()
        app, _ = self.application(permission_factory=counted_reads, workspace_factory=counted_creations)
        for malformed in ({**self.permission(), "expected_revision": True},
                {**self.permission(), "idempotency_key": ""},
                {**self.permission(), "scopes": ["invented"]},
                {**self.permission(), "target": {**self.member.descriptor(), "extra": 1}}):
            with self.subTest(payload=malformed):
                self.denied(lambda: app.handle(self.make_request("command.workspace-permission.grant", malformed)), 400)
        self.denied(lambda: app.handle(self.make_request("read.workspace-permissions", {"limit": 11})), 400)
        cursor = operations.WorkspacePermissionListCursor("installation-a", "workspace-b", self.member)
        self.denied(lambda: app.handle(self.make_request("read.workspace-permissions", {"after": cursor.descriptor()})), 400)
        self.assertEqual(reads, [], "invalid page arguments must refuse before the read UoW")
        self.denied(lambda: app.handle(self.make_request("command.workspace-administration.create",
            {"name": "Invalid", "idempotency_key": "invalid-create", "metadata": {"large": "x" * 16384}})), 400)
        for workspace, name in (("bad\x01workspace", "Name"), ("new-workspace", "bad\x01name")):
            with self.subTest(workspace=workspace, name=name):
                self.denied(lambda: app.handle(self.make_request("command.workspace-administration.create",
                    {"name": name, "idempotency_key": "invalid-control"}, workspace=workspace)), 400)
        self.assertEqual(creations, [], "malformed public creation must refuse before the owner UoW")

    def test_owner_outage_is_fixed_redacted_and_unchained(self):
        def unavailable():
            raise RuntimeError("private endpoint and credentials")
        app, _ = self.application(permission_factory=unavailable)
        error = self.denied(lambda: app.handle(self.make_request("read.workspace-permissions")), 503)
        self.assertNotIn("private", repr(error.descriptor()))
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)

    def test_escaped_identity_history_is_complete_bounded_and_continues_with_owner_cursor(self):
        actor = PrincipalIdentity("a" + "\x01" * 511, "b" + '"' * 511, PrincipalKind.OPERATOR)
        target = PrincipalIdentity("c" + "\x01" * 511, "d" + "\x01" * 511, PrincipalKind.OPERATOR)
        workspace = "w" + "\\" * 511
        policy = operations.WorkspaceAdministrationPolicy("installation-a", (actor,), (actor, target))
        app, _ = self.application(policy)
        principal = AuthenticatedPrincipal(actor)
        app.handle(self.make_request("command.workspace-administration.create",
            {"name": "Escaped", "idempotency_key": "escaped-create"},
            workspace=workspace, principal=principal))
        for revision in range(11):
            app.handle(self.make_request("command.workspace-permission.grant",
                self.permission(revision=revision, key="k" * 198 + f"{revision:02d}", target=target),
                workspace=workspace, principal=principal))
        request = self.make_request("read.workspace-permission-history", {"target": target.descriptor()},
            workspace=workspace, principal=principal)
        first = app.handle(request)
        self.assertEqual(len(first["items"]), 10)
        self.assertEqual([item["revision"] for item in first["items"]], list(range(1, 11)))
        self.assertLessEqual(len(json.dumps(first, ensure_ascii=True).encode()), 1048576)
        for receipt in first["items"]:
            self.assertEqual((receipt["actor"], receipt["target"], receipt["workspace_id"]),
                (actor.descriptor(), target.descriptor(), workspace))
        following = app.handle(replace(request, payload={**request.payload, "after": first["next_cursor"]}))
        self.assertEqual([item["revision"] for item in following["items"]], [11])
        self.assertEqual([item["revision"] for item in first["items"] + following["items"]],
            list(range(1, 12)))
        self.assertIsNone(following["next_cursor"])
