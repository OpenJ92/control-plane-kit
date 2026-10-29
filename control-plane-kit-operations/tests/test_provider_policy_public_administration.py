"""#2062 public provider-policy administration composition laws."""
from dataclasses import dataclass, replace
import json
import unittest

import control_plane_kit_operations as operations
import control_plane_kit_operations.provider_policy_commands as command_owner_module
from control_plane_kit_core.identity import (
    AuthenticatedPrincipal,
    PrincipalIdentity,
    PrincipalKind,
)
from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.provider_policy import (
    ProviderPolicyCoordinate,
    ProviderPolicyCommandResult,
    ProviderPolicyErrorCode,
    ProviderPolicyFailure,
    ProviderPolicyGrantPage,
    ProviderPolicyGrantRead,
    ProviderPolicyGrantRecord,
    ProviderPolicyIntentMode,
    ProviderPolicyListRequest,
    ProviderPolicyOperation,
    ProviderPolicyOutcome,
    ProviderPolicyReadRequest,
)
from control_plane_kit_operations.workflows import IdempotencyKey


@dataclass(frozen=True)
class Request:
    surface: str
    route_id: str
    service_role: ControlPlaneServiceRole
    path_parameters: dict[str, str]
    payload: dict[str, object]
    principal: AuthenticatedPrincipal


class RecordingPort:
    def __init__(self, grant):
        self.grant_record = grant
        self.calls = []

    def read(self, request):
        self.calls.append(("read", request))
        return ProviderPolicyGrantRead(7, self.grant_record)

    def list(self, request):
        self.calls.append(("list", request))
        return ProviderPolicyGrantPage(7, (self.grant_record,), None)


class ResultPort:
    def __init__(self, read_result=None, list_result=None):
        self.read_result = read_result
        self.list_result = list_result

    def read(self, _request):
        return self.read_result

    def list(self, _request):
        return self.list_result


class RecordingFactory:
    def __init__(self, port):
        self.port = port
        self.bindings = []

    def __call__(self, binding):
        self.bindings.append(binding)
        return self.port


class RecordingService:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {"owner": "called"}

    def handle(self, request):
        self.calls.append(request)
        return self.result


class CommandOwner:
    def __init__(self):
        self.calls = []

    def grant(self, request):
        self.calls.append(("grant", request))
        raise operations.ProviderPolicyCommandUnavailable("not retained by fixture")

    revoke = grant
    reconcile = grant

    def read(self, context, command_id):
        self.calls.append(("read", context, command_id))
        raise operations.ProviderPolicyCommandNotFound("fixture command absent")


class RecordCommandOwner(CommandOwner):
    def __init__(self, record):
        super().__init__()
        self.record = record

    def read(self, context, command_id):
        self.calls.append(("read", context, command_id))
        return self.record


class CurrentOwner:
    def __init__(self, read_result, list_result):
        self.calls = []
        self.read_result = read_result
        self.list_result = list_result

    def read(self, context, request):
        self.calls.append(("read", context, request))
        return self.read_result

    def list(self, context, request):
        self.calls.append(("list", context, request))
        return self.list_result


class Resolver(operations.WorkspaceContextResolver):
    def __init__(self):
        self.calls = []

    def resolve(self, principal, workspace_id):
        self.calls.append((principal, workspace_id))
        raise AssertionError("provider administration must not resolve workspace context")


class ProviderPolicyPublicAdministrationTests(unittest.TestCase):
    def setUp(self):
        self.identity = PrincipalIdentity("issuer-a", "operator-a", PrincipalKind.OPERATOR)
        self.principal = AuthenticatedPrincipal(self.identity)
        self.binding = operations.ProviderPolicyBinding(
            "binding-a", "secrets-a", "provider-private-a"
        )
        self.policy = operations.ProviderPolicyAdministrationPolicy(
            "installation-a",
            (operations.ProviderPolicyAdmission(self.identity, self.binding),),
        )
        self.context = self.policy.authorize(self.principal, "secrets-a")
        self.coordinate = ProviderPolicyCoordinate(
            "subject-a", "workspace-a", "read", "deploy"
        )
        self.grant = ProviderPolicyGrantRecord(
            "grant-a",
            "provider-private-a",
            self.coordinate,
            "direct",
            "provider-actor-private",
            "correlation-private",
            "2026-10-29T00:00:00Z",
        )
        self.current_read_type = getattr(
            operations, "ProviderPolicyCurrentReadService", None
        )
        self.adapter_type = getattr(
            operations, "CpkServerProviderPolicyAdministrationService", None
        )
        self.dispatcher_type = getattr(
            operations, "CpkServerAuthorizationService", None
        )

    def require_types(self):
        self.assertIsNotNone(self.current_read_type, "current provider-policy read owner is missing")
        self.assertIsNotNone(self.adapter_type, "provider-policy public adapter is missing")
        self.assertIsNotNone(self.dispatcher_type, "closed authorization dispatcher is missing")

    def current_service(self, factory):
        self.require_types()
        return self.current_read_type(
            administration_policy=self.policy, port_factory=factory
        )

    def adapter(self, *, command_owner=None, current_owner=None, policy=None):
        self.require_types()
        return self.adapter_type(
            administration_policy=policy or self.policy,
            commands=command_owner or CommandOwner(),
            current_reads=current_owner
            or CurrentOwner(
                ProviderPolicyGrantRead(7, self.grant),
                ProviderPolicyGrantPage(7, (self.grant,), "grant-next"),
            ),
        )

    def request(self, route, *, surface="http", path=None, payload=None, principal=None):
        default_path = {"logical_provider_id": "secrets-a"}
        if route in {
            "read.provider-policy-grant",
            "command.provider-policy.revoke",
        }:
            default_path["grant_id"] = "grant-a"
        if route in {
            "read.provider-policy-command",
            "command.provider-policy.reconcile",
        }:
            default_path["command_id"] = "a" * 64
        return Request(
            surface,
            route,
            ControlPlaneServiceRole.AUTHORIZATION,
            default_path if path is None else path,
            {} if payload is None else payload,
            principal or self.principal,
        )

    def denied(self, call, status):
        with self.assertRaises(operations.CpkServerApplicationError) as error:
            call()
        self.assertEqual(error.exception.status, status)
        return error.exception

    def test_current_read_owner_rechecks_policy_and_preserves_typed_requests(self):
        port = RecordingPort(self.grant)
        factory = RecordingFactory(port)
        service = self.current_service(factory)
        read_request = ProviderPolicyReadRequest("secrets-a", "grant-a")
        list_request = ProviderPolicyListRequest(
            "secrets-a",
            subject="subject-a",
            workspace_id="workspace-a",
            action="read",
            intent_mode=ProviderPolicyIntentMode.EXACT,
            intent="deploy",
            limit=17,
            after_grant_id="grant-0",
        )
        self.assertEqual(service.read(self.context, read_request), ProviderPolicyGrantRead(7, self.grant))
        self.assertEqual(service.list(self.context, list_request), ProviderPolicyGrantPage(7, (self.grant,), None))
        self.assertEqual(factory.bindings, [self.binding, self.binding])
        self.assertEqual(port.calls, [("read", read_request), ("list", list_request)])

    def test_denial_or_logical_provider_mismatch_precedes_port_acquisition(self):
        factory = RecordingFactory(RecordingPort(self.grant))
        service = self.current_service(factory)
        wrong_context = operations.ProviderPolicyInvocationContext(
            "other-installation", self.identity, self.binding
        )
        for context, request in (
            (wrong_context, ProviderPolicyReadRequest("secrets-a", "grant-a")),
            (self.context, ProviderPolicyReadRequest("other-provider", "grant-a")),
        ):
            with self.subTest(context=context, request=request):
                with self.assertRaises(operations.ProviderPolicyAdministrationDenied):
                    service.read(context, request)
        self.assertEqual(factory.bindings, [])

    def test_port_factory_failure_is_bounded_without_raw_exception_text(self):
        def unavailable(_binding):
            raise RuntimeError("credential-value-must-not-escape")

        service = self.current_service(unavailable)
        with self.assertRaises(ProviderPolicyFailure) as error:
            service.read(self.context, ProviderPolicyReadRequest("secrets-a", "grant-a"))
        self.assertIs(error.exception.code, ProviderPolicyErrorCode.UNAVAILABLE)
        self.assertNotIn("credential-value", str(error.exception))

    def test_closed_dispatcher_never_falls_back_between_route_families(self):
        self.require_types()
        workspace = RecordingService({"family": "workspace"})
        provider = RecordingService({"family": "provider"})
        dispatcher = self.dispatcher_type(
            workspace_administration=workspace,
            provider_policy_administration=provider,
        )
        provider_request = self.request("read.provider-policy-grant")
        self.assertEqual(dispatcher.handle(provider_request), {"family": "provider"})
        self.assertEqual(workspace.calls, [])
        workspace_request = Request(
            "http",
            "read.workspace-permissions",
            ControlPlaneServiceRole.AUTHORIZATION,
            {"workspace_id": "workspace-a"},
            {},
            self.principal,
        )
        self.assertEqual(dispatcher.handle(workspace_request), {"family": "workspace"})
        self.assertEqual(len(provider.calls), 1)
        unknown = self.request("read.provider-policy-unknown")
        self.denied(lambda: dispatcher.handle(unknown), 404)
        wrong_role = Request(
            "http",
            provider_request.route_id,
            ControlPlaneServiceRole.READS,
            provider_request.path_parameters,
            {},
            self.principal,
        )
        self.denied(lambda: dispatcher.handle(wrong_role), 404)
        provider_absent = self.dispatcher_type(workspace_administration=workspace)
        self.denied(lambda: provider_absent.handle(provider_request), 404)
        self.assertEqual(len(workspace.calls), 1)

    def test_adapter_authorizes_selector_before_decode_and_projects_current_truth(self):
        current = CurrentOwner(
            ProviderPolicyGrantRead(7, self.grant),
            ProviderPolicyGrantPage(7, (self.grant,), "grant-next"),
        )
        adapter = self.adapter(current_owner=current)
        denied_identity = AuthenticatedPrincipal(
            PrincipalIdentity("issuer-a", "other", PrincipalKind.OPERATOR)
        )
        self.denied(
            lambda: adapter.handle(
                self.request(
                    "read.provider-policy-grants",
                    payload={"endpoint": "forbidden"},
                    principal=denied_identity,
                )
            ),
            403,
        )
        self.assertEqual(current.calls, [])
        self.denied(
            lambda: adapter.handle(
                self.request(
                    "read.provider-policy-grants", payload={"endpoint": "forbidden"}
                )
            ),
            400,
        )
        self.assertEqual(current.calls, [])
        result = adapter.handle(
            self.request(
                "read.provider-policy-grant",
                surface="mcp",
                path={},
                payload={
                    "logical_provider_id": "secrets-a",
                    "grant_id": "grant-a",
                },
            )
        )
        self.assertEqual(
            result,
            {
                "revision": 7,
                "grant": {
                    "grant_id": "grant-a",
                    "coordinate": self.coordinate.descriptor(),
                    "origin": "direct",
                    "granted_at": "2026-10-29T00:00:00Z",
                    "revoked_at": None,
                },
            },
        )
        self.assertNotIn("provider-private-a", repr(result))
        self.assertNotIn("provider-actor-private", repr(result))
        self.assertNotIn("correlation-private", repr(result))

    def test_full_principal_identity_denial_precedes_owner_calls(self):
        current = CurrentOwner(
            ProviderPolicyGrantRead(7, self.grant),
            ProviderPolicyGrantPage(7, (self.grant,), "grant-next"),
        )
        adapter = self.adapter(current_owner=current)
        denied = (
            PrincipalIdentity("issuer-a", "other", PrincipalKind.OPERATOR),
            PrincipalIdentity("other-issuer", "operator-a", PrincipalKind.OPERATOR),
            PrincipalIdentity("issuer-a", "operator-a", PrincipalKind.SERVICE),
        )
        for identity in denied:
            with self.subTest(identity=identity):
                self.denied(
                    lambda: adapter.handle(
                        self.request(
                            "read.provider-policy-grant",
                            principal=AuthenticatedPrincipal(identity),
                        )
                    ),
                    403,
                )
        self.assertEqual(current.calls, [])

    def test_command_routes_delegate_typed_owner_requests_without_recreating_history(self):
        command_owner = CommandOwner()
        adapter = self.adapter(command_owner=command_owner)
        grant_payload = {
            "coordinate": self.coordinate.descriptor(),
            "expected_revision": 4,
            "idempotency_key": "grant-key",
        }
        self.denied(
            lambda: adapter.handle(
                self.request("command.provider-policy.grant", payload=grant_payload)
            ),
            503,
        )
        operation, grant = command_owner.calls.pop(0)
        self.assertEqual(operation, "grant")
        self.assertIs(type(grant), operations.GrantProviderPolicy)
        self.assertEqual(
            (
                grant.context,
                grant.coordinate,
                grant.expected_revision,
                grant.idempotency_key.value,
            ),
            (self.context, self.coordinate, 4, "grant-key"),
        )
        self.denied(
            lambda: adapter.handle(
                self.request(
                    "command.provider-policy.reconcile",
                    surface="mcp",
                    path={},
                    payload={
                        "logical_provider_id": "secrets-a",
                        "command_id": "a" * 64,
                    },
                )
            ),
            503,
        )
        _, reconcile = command_owner.calls.pop(0)
        self.assertIs(type(reconcile), operations.ReconcileProviderPolicy)
        self.assertEqual(
            (reconcile.context, reconcile.command_id),
            (self.context, "a" * 64),
        )
        self.denied(
            lambda: adapter.handle(self.request("read.provider-policy-command")),
            404,
        )
        self.assertEqual(
            command_owner.calls,
            [("read", self.context, "a" * 64)],
        )

    def test_http_and_mcp_use_same_list_decoder_and_owner(self):
        current = CurrentOwner(
            ProviderPolicyGrantRead(7, self.grant),
            ProviderPolicyGrantPage(7, (self.grant,), "grant-next"),
        )
        adapter = self.adapter(current_owner=current)
        payload = {
            "subject": "subject-a",
            "workspace_id": "workspace-a",
            "action": "read",
            "intent_mode": "exact",
            "intent": "deploy",
            "limit": 17,
            "after_grant_id": "grant-0",
        }
        http = adapter.handle(self.request("read.provider-policy-grants", payload=payload))
        mcp = adapter.handle(
            self.request(
                "read.provider-policy-grants",
                surface="mcp",
                path={},
                payload={"logical_provider_id": "secrets-a", **payload},
            )
        )
        self.assertEqual(http, mcp)
        requests = [call[2] for call in current.calls]
        self.assertEqual(requests, [requests[0], requests[0]])
        self.assertEqual(
            (
                requests[0].intent_mode,
                requests[0].limit,
                requests[0].after_grant_id,
            ),
            (ProviderPolicyIntentMode.EXACT, 17, "grant-0"),
        )

    def test_real_mcp_envelopes_normalize_identifiers_before_shared_decode(self):
        current = CurrentOwner(
            ProviderPolicyGrantRead(7, self.grant),
            ProviderPolicyGrantPage(7, (self.grant,), "grant-a"),
        )
        adapter = self.adapter(current_owner=current)
        http = adapter.handle(self.request("read.provider-policy-grant"))
        mcp = adapter.handle(
            self.request(
                "read.provider-policy-grant",
                surface="mcp",
                path={},
                payload={
                    "logical_provider_id": "secrets-a",
                    "grant_id": "grant-a",
                },
            )
        )
        self.assertEqual(mcp, http)
        self.assertEqual(current.calls[0][2], current.calls[1][2])

        command_owner = CommandOwner()
        command_adapter = self.adapter(command_owner=command_owner)
        payload = {
            "logical_provider_id": "secrets-a",
            "coordinate": self.coordinate.descriptor(),
            "expected_revision": 4,
            "idempotency_key": "grant-key",
        }
        self.denied(
            lambda: command_adapter.handle(
                self.request(
                    "command.provider-policy.grant",
                    surface="mcp",
                    path={},
                    payload=payload,
                )
            ),
            503,
        )
        operation, prepared = command_owner.calls[0]
        self.assertEqual(operation, "grant")
        self.assertEqual(prepared.context, self.context)

    def test_malformed_route_identifiers_are_400_before_owner_or_port(self):
        current = CurrentOwner(
            ProviderPolicyGrantRead(7, self.grant),
            ProviderPolicyGrantPage(7, (self.grant,), "grant-a"),
        )
        command_owner = CommandOwner()
        adapter = self.adapter(command_owner=command_owner, current_owner=current)
        self.denied(
            lambda: adapter.handle(
                self.request(
                    "read.provider-policy-grant",
                    path={
                        "logical_provider_id": "x" * 129,
                        "grant_id": "grant-a",
                    },
                )
            ),
            400,
        )
        self.denied(
            lambda: adapter.handle(
                self.request(
                    "read.provider-policy-command",
                    path={
                        "logical_provider_id": "secrets-a",
                        "command_id": "not-a-digest",
                    },
                )
            ),
            400,
        )
        self.denied(
            lambda: adapter.handle(
                self.request(
                    "command.provider-policy.revoke",
                    path={
                        "logical_provider_id": "secrets-a",
                        "grant_id": "bad/id",
                    },
                    payload={
                        "coordinate": self.coordinate.descriptor(),
                        "expected_revision": 4,
                        "idempotency_key": "revoke-key",
                    },
                )
            ),
            400,
        )
        self.assertEqual(command_owner.calls, [])
        self.assertEqual(current.calls, [])

    def test_current_reads_reject_typed_results_not_bound_to_request(self):
        wrong_provider = replace(self.grant, provider_id="provider-private-b")
        wrong_grant = replace(self.grant, grant_id="grant-b")
        for result in (
            ProviderPolicyGrantRead(7, wrong_provider),
            ProviderPolicyGrantRead(7, wrong_grant),
        ):
            with self.subTest(result=result):
                service = self.current_service(
                    RecordingFactory(ResultPort(read_result=result))
                )
                with self.assertRaises(ProviderPolicyFailure) as error:
                    service.read(
                        self.context,
                        ProviderPolicyReadRequest("secrets-a", "grant-a"),
                    )
                self.assertIs(
                    error.exception.code, ProviderPolicyErrorCode.MALFORMED_RESULT
                )

        filtered = replace(
            self.grant,
            grant_id="grant-b",
            coordinate=replace(self.coordinate, subject="other-subject"),
        )
        pages = (
            ProviderPolicyGrantPage(7, (self.grant, filtered), "grant-b"),
            ProviderPolicyGrantPage(7, (filtered,), "grant-b"),
            ProviderPolicyGrantPage(7, (wrong_provider,), "grant-a"),
            ProviderPolicyGrantPage(7, (self.grant,), "not-the-last-grant"),
        )
        request = ProviderPolicyListRequest(
            "secrets-a", subject="subject-a", limit=1
        )
        for page in pages:
            with self.subTest(page=page):
                service = self.current_service(
                    RecordingFactory(ResultPort(list_result=page))
                )
                with self.assertRaises(ProviderPolicyFailure) as error:
                    service.list(self.context, request)
                self.assertIs(
                    error.exception.code, ProviderPolicyErrorCode.MALFORMED_RESULT
                )

        revoked = replace(
            self.grant,
            grant_id="grant-c",
            revoked_by="provider-actor-private",
            revoke_correlation_id="revoke-correlation-private",
            revoked_at="2026-10-29T00:01:00Z",
        )
        cursor_cases = (
            (
                ProviderPolicyListRequest(
                    "secrets-a", limit=2, after_grant_id="grant-b"
                ),
                ProviderPolicyGrantPage(7, (self.grant,), None),
            ),
            (
                ProviderPolicyListRequest(
                    "secrets-a", limit=2, after_grant_id="grant-0"
                ),
                ProviderPolicyGrantPage(7, (filtered, self.grant), "grant-a"),
            ),
            (
                ProviderPolicyListRequest(
                    "secrets-a", limit=2, after_grant_id="grant-0"
                ),
                ProviderPolicyGrantPage(7, (revoked,), None),
            ),
            (
                ProviderPolicyListRequest(
                    "secrets-a", limit=2, after_grant_id="grant-0"
                ),
                ProviderPolicyGrantPage(7, (self.grant,), "grant-a"),
            ),
        )
        for cursor_request, page in cursor_cases:
            with self.subTest(cursor_request=cursor_request, page=page):
                service = self.current_service(
                    RecordingFactory(ResultPort(list_result=page))
                )
                with self.assertRaises(ProviderPolicyFailure) as error:
                    service.list(self.context, cursor_request)
                self.assertIs(
                    error.exception.code, ProviderPolicyErrorCode.MALFORMED_RESULT
                )

    def test_command_projection_is_bounded_and_truthful_when_evidence_is_omitted(self):
        long_value = '\\\"' * 256
        coordinate = ProviderPolicyCoordinate(
            long_value, long_value, long_value, long_value
        )
        request = operations.GrantProviderPolicy(
            context=self.context,
            coordinate=coordinate,
            expected_revision=4,
            idempotency_key=IdempotencyKey("large-evidence"),
        )
        record = command_owner_module._record_for(
            request, ProviderPolicyOperation.GRANT, "2026-10-29T00:00:00Z"
        )
        observations = []
        for index in range(100):
            result = ProviderPolicyCommandResult(
                provider_id=record.command.provider_id,
                correlation_id=record.command.provider_correlation,
                operation=ProviderPolicyOperation.GRANT,
                coordinate=coordinate,
                expected_revision=4,
                outcome=ProviderPolicyOutcome.GRANTED,
                code="provider-policy-granted",
                grant_id=f"grant-{index}",
                resulting_revision=5,
                provider_actor="private-provider-actor",
                occurred_at="2026-10-29T00:00:00Z",
            )
            observations.append(
                command_owner_module._result_observation(
                    record,
                    result,
                    operations.ProviderPolicyObservationSource.COMMAND_RESULT_READ,
                    "2026-10-29T00:00:00Z",
                )
            )
        record = replace(
            record,
            dispatch_claimed_at="2026-10-29T00:00:00Z",
            observations=tuple(observations),
            observation_count=len(observations),
            evidence_integrity_conflict=True,
            has_matching_result=True,
        )
        adapter = self.adapter(command_owner=RecordCommandOwner(record))
        projected = adapter.handle(self.request("read.provider-policy-command"))
        encoded = json.dumps(
            projected, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("ascii")
        self.assertLessEqual(len(encoded), 65_536)
        self.assertLessEqual(len(encoded), 60 * 1024)
        mcp_envelope = json.dumps(
            {"jsonrpc": "2.0", "id": "provider-policy-command", "result": projected},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        self.assertLessEqual(len(mcp_envelope), 65_536)
        self.assertEqual(projected["command_id"], record.command_id)
        self.assertEqual(projected["state"], record.state.value)
        self.assertEqual(projected["observation_count"], 100)
        self.assertTrue(projected["evidence_integrity_conflict"])
        self.assertGreater(projected["observations_omitted"], 0)
        self.assertEqual(
            len(projected["observations"]) + projected["observations_omitted"],
            projected["observation_count"],
        )
        self.assertNotIn("private-provider-actor", encoded.decode("ascii"))

    def test_dynamic_application_never_invokes_workspace_resolver_for_provider_routes(self):
        adapter = self.adapter()
        services = operations.cpk_server_services(
            unit_of_work_factory=lambda: None,
            planning=None,
            approval=None,
            admission=None,
            lifecycle=None,
            execution=None,
            provider_policy_administration=adapter,
        )
        resolver = Resolver()
        app = operations.CpkServerOperationsApplication(
            services,
            authority_mode="dynamic",
            workspace_context_resolver=resolver,
        )
        result = app.handle(self.request("read.provider-policy-grant"))
        self.assertEqual(result["grant"]["grant_id"], "grant-a")
        self.assertEqual(resolver.calls, [])


if __name__ == "__main__":
    unittest.main()
