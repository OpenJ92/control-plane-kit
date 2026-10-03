"""#1860: the authenticated Operations application must await execution."""

import unittest
from dataclasses import replace

from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.cpk_server import (
    CpkServerApplicationError, CpkServerExecutionService,
    CpkServerOperationsApplication, CpkServerUnsupportedService,
)
from tests.test_cpk_server_adapters import (
    DescriptorResult, RouteRequest, foreign_worker_principal, worker_principal,
)


class AwaitedExecutionRecorder:
    def __init__(self):
        self.commands = []

    async def execute_managed(self, command):
        self.commands.append(command)
        return DescriptorResult({"coordinator_status": "blocked"})

    def execute(self, command):
        raise AssertionError("managed request fell back to the synchronous entry")


class ManagedExecutionApplicationTests(unittest.IsolatedAsyncioTestCase):
    def application(self):
        recorder = AwaitedExecutionRecorder()
        services = {role: CpkServerUnsupportedService(role) for role in ControlPlaneServiceRole}
        services[ControlPlaneServiceRole.EXECUTION] = CpkServerExecutionService(recorder)
        application = CpkServerOperationsApplication(services)
        return application, recorder

    def request(self, surface="http"):
        path = {"workspace_id": "workspace-a", "run_id": "run-a"}
        payload = {"idempotency_key": "managed-a", "claim_generation": 7, "max_effects": 3,
            "actor_id": "forged-actor", "worker_id": "forged-worker", "scopes": ["forged:scope"]}
        if surface == "mcp":
            payload, path = {**path, **payload}, {}
        return RouteRequest(surface, "command.deployment.execute", ControlPlaneServiceRole.EXECUTION,
            path, payload, worker_principal(subject_id="authenticated-actor", scopes=tuple(PolicyScope)))

    async def invoke(self, application, request):
        handle = getattr(application, "handle_async", None)
        self.assertIsNotNone(handle, "authenticated application has no awaited execution entrance")
        return await handle(request)

    async def test_real_application_awaits_and_retains_genuine_context_separately(self):
        for surface in ("http", "mcp"):
            with self.subTest(surface=surface):
                application, recorder = self.application()
                request = self.request(surface)
                result = await self.invoke(application, request)
                self.assertEqual(result, {"coordinator_status": "blocked"})
                self.assertEqual(len(recorder.commands), 1)
                command = recorder.commands[0]
                self.assertEqual(command.context, request.principal.command_context("workspace-a"))
                self.assertEqual(command.execution.authority.worker_id, "authenticated-actor")
                self.assertEqual(command.execution.fence.worker_id, "authenticated-actor")
                self.assertEqual(command.execution.fence.generation, 7)
                self.assertEqual(command.execution.max_effects, 3)
                self.assertNotIn("forged:scope", tuple(scope.value for scope in command.context.granted_scopes))

    async def test_foreign_workspace_and_payload_scopes_never_reach_execution(self):
        for principal in (foreign_worker_principal(), worker_principal(scopes=(PolicyScope.NODE_CONTROL_READ,))):
            with self.subTest(principal=principal.identity.subject_id):
                application, recorder = self.application()
                with self.assertRaises(CpkServerApplicationError) as caught:
                    await self.invoke(application, replace(self.request(), principal=principal))
                self.assertEqual(caught.exception.status, 403)
                self.assertEqual(recorder.commands, [])
