"""#2053 shared application admission through actual permission/read owners."""
import asyncio
from dataclasses import dataclass, replace
import unittest

import control_plane_kit_operations as operations
from control_plane_kit_core.identity import AuthenticatedPrincipal, WorkspaceGrant
from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.workflows import IdempotencyKey
from tests import test_postgres_workspace_permissions as permission_fixtures


@dataclass(frozen=True)
class Request:
    surface: str
    route_id: str
    service_role: ControlPlaneServiceRole
    path_parameters: dict
    payload: dict
    principal: AuthenticatedPrincipal


class CountingResolver(operations.WorkspaceContextResolver):
    def __init__(self, factory, *, installation="installation-a"):
        super().__init__(factory, installation_id=installation)
        self.calls = []

    def resolve(self, principal, workspace_id):
        self.calls.append((principal, workspace_id))
        return super().resolve(principal, workspace_id)


class RetainingReadService(operations.CpkServerReadService):
    def __init__(self, factory):
        super().__init__(factory)
        self.requests = []

    def handle(self, request):
        self.requests.append(request)
        return super().handle(request)


class PostgresApplicationInitialContextTests(unittest.TestCase):
    # Reuse schema setup, not its TestCase inheritance or collected tests.
    setUp = permission_fixtures.PostgresWorkspacePermissionTests.setUp
    clock = staticmethod(permission_fixtures.PostgresWorkspacePermissionTests.clock)
    connect = permission_fixtures.PostgresWorkspacePermissionTests.connect
    unit_of_work = permission_fixtures.PostgresWorkspacePermissionTests.unit_of_work
    public = permission_fixtures.PostgresWorkspacePermissionTests.public
    policy = permission_fixtures.PostgresWorkspacePermissionTests.policy
    service = permission_fixtures.PostgresWorkspacePermissionTests.service
    initialize = permission_fixtures.PostgresWorkspacePermissionTests.initialize
    grant = permission_fixtures.PostgresWorkspacePermissionTests.grant

    def application(self, resolver=None, *, mode="dynamic"):
        application = operations.CpkServerOperationsApplication
        reads = RetainingReadService(self.unit_of_work)
        services = {role: operations.CpkServerUnsupportedService(role)
            for role in ControlPlaneServiceRole}
        services[ControlPlaneServiceRole.READS] = reads
        app = application(services, authority_mode=mode,
            workspace_context_resolver=resolver)
        return app, reads

    def make_request(self, surface="http", principal=None):
        target = {"workspace_id": "workspace-a"}
        return Request(surface, "read.workspace", ControlPlaneServiceRole.READS,
            target if surface == "http" else {},
            target if surface == "mcp" else {},
            principal or AuthenticatedPrincipal(self.member))

    def denied(self, call, status):
        with self.assertRaises(operations.CpkServerApplicationError) as caught:
            call()
        self.assertEqual(caught.exception.status, status)
        return caught.exception

    def test_same_verified_principal_observes_grant_then_revoke_on_each_new_request(self):
        service, context = self.initialize()
        resolver = CountingResolver(self.unit_of_work)
        app, reads = self.application(resolver)
        request = self.make_request()
        self.denied(lambda: app.handle(request), 403)
        self.assertEqual(reads.requests, [])
        self.grant(service, context)
        result = app.handle(request)
        self.assertEqual(result["workspace"]["workspace_id"], "workspace-a")
        service.revoke(operations.RevokeWorkspacePermissions(context=context,
            workspace_id="workspace-a", target=self.member, scopes=(self.read,),
            expected_revision=1, idempotency_key=IdempotencyKey("revoke-a")))
        self.denied(lambda: app.handle(request), 403)
        self.assertEqual(resolver.calls, [(request.principal, "workspace-a")] * 3)
        self.assertEqual(len(reads.requests), 1)

    def test_async_entry_prepares_once_and_external_reentry_resolves_again(self):
        service, context = self.initialize()
        self.grant(service, context)
        resolver = CountingResolver(self.unit_of_work)
        app, reads = self.application(resolver)
        request = self.make_request("mcp")
        result = asyncio.run(app.handle_async(request))
        self.assertEqual(result["workspace"]["workspace_id"], "workspace-a")
        self.assertEqual(len(resolver.calls), 1)
        retained = reads.requests[0]
        service.revoke(operations.RevokeWorkspacePermissions(context=context,
            workspace_id="workspace-a", target=self.member, scopes=(self.read,),
            expected_revision=1, idempotency_key=IdempotencyKey("revoke-a")))
        self.denied(lambda: asyncio.run(app.handle_async(retained)), 403)
        self.assertEqual(len(resolver.calls), 2)
        self.assertEqual(len(reads.requests), 1)

    def test_conflicting_target_and_wrong_role_refuse_before_resolution(self):
        self.initialize()
        resolver = CountingResolver(self.unit_of_work)
        app, reads = self.application(resolver)
        request = self.make_request()
        self.denied(lambda: app.handle(replace(request,
            payload={"workspace_id": "workspace-b"})), 400)
        self.denied(lambda: app.handle(replace(request,
            service_role=ControlPlaneServiceRole.AUTHORIZATION)), 404)
        self.denied(lambda: app.handle(replace(request,
            path_parameters={"workspace_id": "a" * 513})), 400)
        self.assertEqual(resolver.calls, [])
        self.assertEqual(reads.requests, [])

    def test_admission_snapshots_target_before_the_owner_read(self):
        service, context = self.initialize()
        self.grant(service, context)
        resolver = CountingResolver(self.unit_of_work)
        app, reads = self.application(resolver)
        original = self.make_request()
        class MutatingCallerReadService(operations.CpkServerReadService):
            def handle(inner_self, request):
                original.path_parameters["workspace_id"] = "workspace-b"
                return super().handle(request)
        services = dict(app.services)
        services[ControlPlaneServiceRole.READS] = MutatingCallerReadService(self.unit_of_work)
        app = replace(app, services=services)
        result = app.handle(original)
        self.assertEqual(result["workspace"]["workspace_id"], "workspace-a")
        self.assertEqual(resolver.calls, [(original.principal, "workspace-a")])

    def test_resolver_failure_is_bounded_and_cannot_fall_back_to_startup_grants(self):
        self.initialize()
        def unavailable():
            raise RuntimeError("private database endpoint must not escape")
        resolver = CountingResolver(unavailable)
        app, reads = self.application(resolver)
        verified = AuthenticatedPrincipal(self.member,
            (WorkspaceGrant("workspace-a", (self.read,)),))
        error = self.denied(lambda: app.handle(self.make_request(principal=verified)), 503)
        self.assertNotIn("private", repr(error.descriptor()))
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        self.assertEqual(reads.requests, [])

    def test_dynamic_legacy_creation_is_refused_before_ordinary_lookup(self):
        resolver = CountingResolver(self.unit_of_work)
        app, _ = self.application(resolver)
        request = replace(self.make_request(), route_id="command.workspace.create",
            service_role=ControlPlaneServiceRole.PLANNING)
        self.denied(lambda: app.handle(request), 403)
        self.assertEqual(resolver.calls, [])

    def test_static_compatibility_and_dynamic_configuration_are_explicit(self):
        app, _ = self.application(mode="static")
        verified = AuthenticatedPrincipal(self.member,
            (WorkspaceGrant("workspace-a", (self.read,)),))
        result = app.handle(self.make_request(principal=verified))
        self.assertEqual(result["workspace"]["workspace_id"], "workspace-a")
        for mode, resolver in (("dynamic", None),
                ("static", CountingResolver(self.unit_of_work)), ("unknown", None)):
            with self.subTest(mode=mode):
                with self.assertRaises(ValueError):
                    self.application(resolver, mode=mode)

    def test_confidential_provision_refuses_before_payload_access_on_both_entrances(self):
        service, context = self.initialize()
        resolver = CountingResolver(self.unit_of_work)
        app, _ = self.application(resolver)
        services = dict(app.services)
        services[ControlPlaneServiceRole.EXECUTION] = operations.CpkServerExecutionService(None)
        app = replace(app, services=services)
        accesses = []
        class PoisonRequest:
            route_id = "command.secret-material.provision"
            service_role = ControlPlaneServiceRole.EXECUTION
            surface = "mcp"
            path_parameters = {"workspace_id": "workspace-a"}
            principal = AuthenticatedPrincipal(self.member)
            @property
            def payload(inner_self):
                accesses.append("payload")
                raise AssertionError("confidential payload was accessed")
        request = PoisonRequest()
        for asynchronous in (False, True):
            call = (lambda: asyncio.run(app.handle_async(request))) if asynchronous else lambda: app.handle(request)
            with self.subTest(entrance="async" if asynchronous else "sync", condition="non-http"):
                self.denied(call, 400)
            request.surface = "http"
            with self.subTest(entrance="async" if asynchronous else "sync", condition="no-current-grant"):
                self.denied(call, 403)
            request.surface = "mcp"
        self.grant(service, context, scopes=(PolicyScope.SECRET_MATERIAL_PROVISION,
            PolicyScope.SECRET_PROVIDER_REGISTER))
        request.surface = "http"
        self.denied(lambda: app.handle(request), 501)
        self.denied(lambda: asyncio.run(app.handle_async(request)), 501)
        self.assertEqual(accesses, [])
