"""Confidential command laws over real stores and controlled external ports."""

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, replace
import importlib
import importlib.util
import json
import threading
from types import SimpleNamespace
import unittest

from control_plane_kit_core.identity import AuthenticatedPrincipal, PrincipalIdentity, PrincipalKind, WorkspaceGrant
from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import SecretCustodyReceipt, SecretReference, SecretUseIntent, SecretValue
from control_plane_kit_operations.cpk_server import CpkServerApplicationError, CpkServerExecutionService, CpkServerReadService
from control_plane_kit_operations.secret_material_provision_attempts import (
    ProvisionAttemptAuthorizationDenied, ProvisionAttemptConflict, ProvisionAttemptError,
    ProvisionAttemptNotFound, ProvisionAttemptState,
)
from control_plane_kit_operations.secret_providers import RevokeSecretProviderCommand
import test_secret_material_provision_attempts as attempt_fixture


NOW = attempt_fixture.NOW
SCOPES = (PolicyScope.SECRET_MATERIAL_PROVISION, PolicyScope.SECRET_PROVIDER_REGISTER)
VALUE = "opaque-input-sentinel-2030"


class _ProcessLoss(BaseException):
    pass


class SecretMaterialProvisionCommandTests(unittest.TestCase):
    def setUp(self):
        module = "control_plane_kit_operations.secret_material_provision_commands"
        self.assertIsNotNone(importlib.util.find_spec(module), "missing confidential provision command owner")
        self.api = importlib.import_module(module)
        self.fixture = attempt_fixture.SecretMaterialProvisionAttemptTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.context = AuthenticatedPrincipal(self.fixture.actor, (WorkspaceGrant("workspace-a", SCOPES),)).command_context("workspace-a")
        self.calls = []
        self.local = threading.local()
        self.authority_calls = 0
        self.current_scopes = SCOPES
        self.authority_behavior = None
        self.behavior = lambda grant, value: SecretCustodyReceipt(
            grant.custody_id, grant.provider_registration_id, grant.reference, "version-a", 1)

    @contextmanager
    def unit_of_work(self):
        self.local.active = getattr(self.local, "active", 0) + 1
        try:
            with self.fixture.fixture.unit_of_work() as uow:
                yield uow
        finally:
            self.local.active -= 1

    def authorize(self, principal, workspace_id):
        self.assertEqual(getattr(self.local, "active", 0), 0, "authority port called inside UoW")
        self.authority_calls += 1
        if self.authority_behavior is not None:
            return self.authority_behavior(principal, workspace_id)
        return replace(principal, workspace_grants=(WorkspaceGrant(workspace_id, self.current_scopes),)).command_context(workspace_id)

    def store(self, grant, value):
        self.assertEqual(getattr(self.local, "active", 0), 0, "custody port called inside UoW")
        self.assertEqual(self.read(grant.correlation_id).state, ProvisionAttemptState.DISPATCHING)
        self.calls.append(grant)
        return self.behavior(grant, value)

    def service(self, *, factory=None):
        return self.api.SecretMaterialProvisionCommandService(factory or self.unit_of_work,
            SimpleNamespace(store=self.store), authorizer=SimpleNamespace(authorize=self.authorize), clock=lambda: NOW)

    def command(self, request_id="request-a"):
        return self.api.ProvisionSecretMaterialCommand(self.context, request_id,
            self.fixture.provider.registration_id, self.fixture.grant.reference,
            SecretUseIntent.CLOUDFLARE_API_TOKEN)

    def payload(self, value=VALUE, request_id="request-a"):
        return {"request_id": request_id, "provider_registration_id": self.fixture.provider.registration_id,
                "secret_reference": self.fixture.grant.reference.reference_id,
                "intent": "cloudflare.api-token", "value_base64": base64.b64encode(value.encode("utf-8")).decode("ascii")}

    def read(self, correlation_id=None, request_id="request-a"):
        # Correlation identity is not a second lookup API: the tests use exact request identity.
        with self.fixture.fixture.unit_of_work() as uow:
            if correlation_id is not None:
                row = self.fixture.fixture.connection.execute(
                    "SELECT request_id FROM cpk_secret_material_provision_attempts "
                    "WHERE intent_descriptor->'custody_grant'->>'correlation_id'=%s", (correlation_id,)).fetchone()
                self.assertIsNotNone(row)
                request_id = row[0]
            return uow.stores.secret_material_provision_attempts.get("workspace-a", request_id)

    def request(self, *, payload=None, surface="http", route="command.secret-material.provision", principal=None):
        return SimpleNamespace(route_id=route, service_role=ControlPlaneServiceRole.EXECUTION,
            surface=surface, path_parameters={"workspace_id": "workspace-a"},
            payload=self.payload() if payload is None else payload,
            principal=self.context.principal if principal is None else principal)

    def test_strict_decoder_preserves_exact_text_and_separates_safe_command(self):
        for value in (VALUE, " \n\x00", "é" * 32768, "x" * 65536):
            command, material = self.api.decode_provision_request(self.context, self.payload(value))
            self.assertEqual(material.reveal(), value)
            self.assertEqual(command, self.command())
            self.assertNotIn("value_base64", asdict(command))
            self.assertNotIn(VALUE, repr(command))

    def test_decoder_rejects_malformed_oversized_and_noncanonical_input_without_causes(self):
        invalid = ("", "A" * 87385, "!!!!", "YQ", "YQ===", "YR==", "YQ==\n", "/w==", "é===",
                   base64.b64encode(b"x" * 65537).decode("ascii"), 123, None)
        for value in invalid:
            with self.subTest(case=type(value).__name__):
                with self.assertRaises(ProvisionAttemptError) as error:
                    self.api.decode_provision_request(self.context, {**self.payload(), "value_base64": value})
                self.assertIsNone(error.exception.__context__)
                self.assertIsNone(error.exception.__cause__)
                self.assertNotIn(VALUE, str(error.exception))
        for field in ("actor", "scopes", "endpoint", "token", "extra"):
            with self.assertRaises(ProvisionAttemptError):
                self.api.decode_provision_request(self.context, {**self.payload(), field: VALUE})

    def test_typed_value_cannot_bypass_utf8_and_byte_bounds(self):
        for material in (SecretValue("x" * 65537), SecretValue("\ud800"), VALUE):
            with self.assertRaises(ProvisionAttemptError) as error:
                self.service().provision(self.command(), material)
            self.assertIsNone(error.exception.__context__)
        self.assertEqual(self.calls, [])
        with self.assertRaises(ProvisionAttemptNotFound):
            self.read()

    def test_current_denial_precedes_decoder_and_durable_work(self):
        self.current_scopes = ()
        with self.assertRaises(ProvisionAttemptAuthorizationDenied):
            self.service().provision_encoded(self.context, object())
        with self.assertRaises(ProvisionAttemptNotFound):
            self.read()
        self.assertEqual(self.calls, [])

    def test_current_identity_and_original_grant_ceiling_are_preserved(self):
        for scopes in ((SCOPES[0],), (SCOPES[1],), (*SCOPES, PolicyScope.SECRET_PROVIDER_READ)):
            self.current_scopes = scopes
            with self.assertRaises(ProvisionAttemptAuthorizationDenied):
                self.service().provision(self.command(), SecretValue(VALUE))
        self.authority_behavior = lambda principal, workspace: replace(principal,
            identity=replace(principal.identity, issuer="other")).command_context(workspace)
        with self.assertRaises(ProvisionAttemptAuthorizationDenied):
            self.service().provision_encoded(self.context, self.payload())
        self.assertEqual(self.calls, [])

    def test_unavailable_current_authority_has_no_sensitive_exception_chain(self):
        def unavailable(principal, workspace):
            raise RuntimeError(VALUE)
        self.authority_behavior = unavailable
        with self.assertRaises(ProvisionAttemptAuthorizationDenied) as error:
            self.service().provision_encoded(self.context, self.payload())
        self.assertIsNone(error.exception.__context__)
        self.assertIsNone(error.exception.__cause__)
        self.assertNotIn(VALUE, str(vars(error.exception)))
        self.assertEqual(self.calls, [])

    def test_provider_binding_failures_precede_reservation_and_io(self):
        for command in (replace(self.command(), provider_registration_id="absent"),
                        replace(self.command(), secret_reference=SecretReference("secret://other/outside"))):
            with self.assertRaises(ProvisionAttemptConflict):
                self.service().provision(command, SecretValue(VALUE))
        self.fixture.fixture.service().revoke_provider(RevokeSecretProviderCommand(
            workspace_id="workspace-a", provider_id=self.fixture.provider.provider_id,
            revoked_by="operator-a", revoked_at=NOW, actor_scopes=(PolicyScope.SECRET_PROVIDER_REVOKE,)))
        with self.assertRaises(ProvisionAttemptConflict):
            self.service().provision(self.command(), SecretValue(VALUE))
        with self.assertRaises(ProvisionAttemptNotFound):
            self.read()
        self.assertEqual(self.calls, [])

    def test_preclaim_current_denial_preserves_intended_without_dispatch(self):
        def authority(principal, workspace):
            try:
                self.read()
                scopes = ()
            except ProvisionAttemptNotFound:
                scopes = SCOPES
            return replace(principal, workspace_grants=(WorkspaceGrant(workspace, scopes),)).command_context(workspace)
        self.authority_behavior = authority
        with self.assertRaises(ProvisionAttemptAuthorizationDenied):
            self.service().provision(self.command(), SecretValue(VALUE))
        self.assertEqual(self.read().state, ProvisionAttemptState.INTENDED)
        self.assertEqual(self.calls, [])

    def test_committed_dispatch_exact_material_and_safe_admitted_status(self):
        def custody(grant, value):
            self.assertEqual(value.reveal(), VALUE)
            return SecretCustodyReceipt(grant.custody_id, grant.provider_registration_id, grant.reference, "version-a", 1)
        self.behavior = custody
        result = self.service().provision(self.command(), SecretValue(VALUE))
        self.assertEqual(result.state, ProvisionAttemptState.ADMITTED)
        self.assertEqual(len(self.calls), 1)
        self.assertGreaterEqual(self.authority_calls, 4)
        attempt = self.read()
        for rendered in (repr(result), json.dumps(asdict(result)), json.dumps(result.descriptor()),
                         json.dumps(attempt.intent.descriptor())):
            for forbidden in (VALUE, self.payload()["value_base64"], "value_digest"):
                self.assertNotIn(forbidden, rendered)
        self.assertEqual(result.descriptor()["admission"]["reference_id"], self.command().secret_reference.reference_id)

    def test_every_duplicate_conflicts_and_restart_never_redispatches(self):
        self.service().provision(self.command(), SecretValue(VALUE))
        for material in (VALUE, "changed-opaque-input"):
            with self.assertRaises(ProvisionAttemptConflict):
                self.service().provision(self.command(), SecretValue(material))
        self.assertEqual(len(self.calls), 1)

    def test_concurrent_duplicate_conflicts_during_original_dispatch(self):
        entered, release = threading.Event(), threading.Event()
        original = self.behavior
        def custody(grant, value):
            entered.set()
            if not release.wait(15):
                raise AssertionError("test did not release custodian")
            return original(grant, value)
        self.behavior = custody
        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(self.service().provision, self.command(), SecretValue(VALUE))
            try:
                self.assertTrue(entered.wait(10))
                with self.assertRaises(ProvisionAttemptConflict):
                    self.service().provision(self.command(), SecretValue(VALUE))
            finally:
                release.set()
            self.assertEqual(future.result(15).state, ProvisionAttemptState.ADMITTED)
        self.assertEqual(len(self.calls), 1)

    def test_independent_ambiguous_provider_outcomes_never_adopt_or_retry(self):
        stored = []
        def audit_failure(grant, value):
            stored.append(grant.reference)
            raise RuntimeError(VALUE + " audit failed after create")
        def transport_failure(grant, value):
            raise ConnectionError(VALUE)
        def conflict(grant, value):
            raise RuntimeError("409 " + VALUE)
        cases = (audit_failure, transport_failure, conflict, lambda grant, value: object())
        for index, behavior in enumerate(cases):
            request_id = f"request-{index}"
            self.behavior = behavior
            result = self.service().provision(self.command(request_id), SecretValue(VALUE))
            self.assertEqual(result.state, ProvisionAttemptState.UNCERTAIN)
            self.assertIsNone(result.descriptor()["admission"])
            with self.assertRaises(ProvisionAttemptConflict):
                self.service().provision(self.command(request_id), SecretValue(VALUE))
            self.assertNotIn(VALUE, repr(result))
        self.assertEqual(len(self.calls), len(cases))
        self.assertEqual(len(stored), 1)

    def test_process_loss_leaves_dispatching_without_recovery(self):
        def crash(grant, value):
            raise _ProcessLoss()
        self.behavior = crash
        with self.assertRaises(_ProcessLoss):
            self.service().provision(self.command(), SecretValue(VALUE))
        self.assertEqual(self.read().state, ProvisionAttemptState.DISPATCHING)
        with self.assertRaises(ProvisionAttemptConflict):
            self.service().provision(self.command(), SecretValue(VALUE))
        self.assertEqual(len(self.calls), 1)

    def test_postclaim_denial_records_only_proven_preinvocation_refusal(self):
        def authority(principal, workspace):
            try:
                state = self.read().state
            except ProvisionAttemptNotFound:
                state = None
            scopes = () if state is ProvisionAttemptState.DISPATCHING else SCOPES
            return replace(principal, workspace_grants=(WorkspaceGrant(workspace, scopes),)).command_context(workspace)
        self.authority_behavior = authority
        with self.assertRaises(ProvisionAttemptAuthorizationDenied):
            self.service().provision(self.command(), SecretValue(VALUE))
        self.assertEqual(self.read().state, ProvisionAttemptState.REFUSED)
        self.assertEqual(self.calls, [])

    def test_authority_loss_after_receipt_retains_pending_custody(self):
        original = self.behavior
        def custody(grant, value):
            self.current_scopes = ()
            return original(grant, value)
        self.behavior = custody
        with self.assertRaises(ProvisionAttemptAuthorizationDenied):
            self.service().provision(self.command(), SecretValue(VALUE))
        self.assertEqual(self.read().state, ProvisionAttemptState.RECEIPT_RECORDED)
        self.assertEqual(self.read().receipt.version_id, "version-a")
        self.assertEqual(len(self.calls), 1)

    def test_provider_revocation_after_custody_leaves_pending_receipt(self):
        original = self.behavior
        def custody(grant, value):
            self.fixture.fixture.service().revoke_provider(RevokeSecretProviderCommand(
                workspace_id="workspace-a", provider_id=self.fixture.provider.provider_id,
                revoked_by="operator-a", revoked_at=NOW, actor_scopes=(PolicyScope.SECRET_PROVIDER_REVOKE,)))
            return original(grant, value)
        self.behavior = custody
        result = self.service().provision(self.command(), SecretValue(VALUE))
        self.assertEqual(result.state, ProvisionAttemptState.RECEIPT_RECORDED)
        self.assertIsNone(result.descriptor()["admission"])
        self.assertEqual(len(self.calls), 1)

    def test_failed_outcome_or_admission_commit_never_claims_persistence_or_retries(self):
        for index, (failing_state, retained_state) in enumerate((
            ("receipt-recorded", ProvisionAttemptState.DISPATCHING),
            ("admitted", ProvisionAttemptState.RECEIPT_RECORDED),
        )):
            request_id = f"request-{index}"
            @contextmanager
            def failing_commit():
                with self.unit_of_work() as uow:
                    yield uow
                    connection = uow.stores.connection
                    row = connection.execute("SELECT state FROM cpk_secret_material_provision_attempts "
                        "WHERE workspace_id='workspace-a' AND request_id=%s", (request_id,)).fetchone()
                    if row == (failing_state,):
                        connection.execute("CREATE TEMP TABLE provision_command_guard "
                            "(value integer UNIQUE DEFERRABLE INITIALLY DEFERRED)")
                        connection.execute("INSERT INTO provision_command_guard VALUES (1),(1)")
            with self.assertRaises(self.api.ProvisionCommandUnavailable) as error:
                self.service(factory=failing_commit).provision(self.command(request_id), SecretValue(VALUE))
            self.assertIsNone(error.exception.__context__)
            self.assertIsNone(error.exception.__cause__)
            self.assertEqual(self.read(request_id=request_id).state, retained_state)
            with self.assertRaises(ProvisionAttemptConflict):
                self.service().provision(self.command(request_id), SecretValue(VALUE))
        self.assertEqual(len(self.calls), 2)

    def test_non_http_and_missing_service_refuse_before_payload_access(self):
        class PoisonRequest:
            route_id = "command.secret-material.provision"
            surface = "mcp"
            path_parameters = {"workspace_id": "workspace-a"}
            principal = self.context.principal
            @property
            def payload(self):
                raise AssertionError("confidential payload was accessed")
        request = PoisonRequest()
        for adapter in (CpkServerExecutionService(None, secret_material_provision=self.service()),
                        CpkServerExecutionService(None)):
            with self.assertRaises(CpkServerApplicationError):
                adapter.handle(request)
        request.surface = "http"
        with self.assertRaises(CpkServerApplicationError) as error:
            CpkServerExecutionService(None).handle(request)
        self.assertEqual(error.exception.status, 501)
        self.assertEqual(self.calls, [])

    def test_http_sync_async_conflict_navigation_and_independent_status_authority(self):
        adapter = CpkServerExecutionService(None, secret_material_provision=self.service())
        result = asyncio.run(adapter.handle_async(self.request()))
        self.assertEqual(result["state"], "admitted")
        with self.assertRaises(CpkServerApplicationError) as error:
            adapter.handle(self.request())
        self.assertEqual(error.exception.status, 409)
        self.assertIsNone(error.exception.__context__)
        self.assertEqual(error.exception.descriptor()["error"]["status_reference"], {
            "route_id": "read.secret-material-provision", "workspace_id": "workspace-a", "request_id": "request-a"})
        read = self.request(route="read.secret-material-provision", payload={})
        read.path_parameters["request_id"] = "request-a"
        read.service_role = ControlPlaneServiceRole.READS
        reader = CpkServerReadService(self.fixture.fixture.unit_of_work)
        with self.assertRaises(CpkServerApplicationError) as denied:
            reader.handle(read)
        self.assertEqual(denied.exception.status, 403)
        read.principal = replace(self.context.principal, workspace_grants=(WorkspaceGrant("workspace-a",
            (PolicyScope.INSTANCE_WORKSPACE_READ, PolicyScope.SECRET_PROVIDER_READ)),))
        self.assertEqual(reader.handle(read), result)
        read.surface, read.path_parameters, read.payload = "mcp", {}, {"workspace_id": "workspace-a", "request_id": "request-a"}
        self.assertEqual(reader.handle(read), result)
        self.assertEqual(len(self.calls), 1)
