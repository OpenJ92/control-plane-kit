"""Owning service laws: real UoW and controlled provider/authority ports."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import asyncio
import importlib
import importlib.util
import json
import threading
from types import SimpleNamespace
import unittest

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.identity import (
    AuthenticatedPrincipal, IdentityContractError, PrincipalIdentity,
    PrincipalKind, WorkspaceGrant,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.secrets import SecretProviderId, SecretReference, SecretUseIntent
from control_plane_kit_operations.delegation_key_generation_attempts import (
    GenerationAttemptAuthorizationDenied, GenerationAttemptConflict,
    GenerationAttemptError, GenerationAttemptState,
)
from control_plane_kit_operations.secret_providers import (
    RegisterSecretReferenceCommand, RevokeSecretReferenceCommand,
    RevokeSecretProviderCommand, SecretProviderRegistrationService,
)
from control_plane_kit_operations.cpk_server import (
    CpkServerApplicationError, CpkServerExecutionService, CpkServerReadService,
)
import test_delegation_key_generation as generation_fixture


NOW = "2026-10-22T08:30:00Z"
SCOPES = (PolicyScope.DELEGATION_KEY_GENERATE, PolicyScope.DELEGATION_KEY_REGISTER)


class _CurrentAuthority:
    """Controlled current policy evidence; not a production permission store."""

    def __init__(self):
        self.scopes = SCOPES
        self.available = True

    def authorize(self, principal, workspace_id):
        if not self.available:
            raise IdentityContractError("sensitive-authorizer-diagnostic")
        return replace(principal, workspace_grants=(
            WorkspaceGrant(workspace_id, self.scopes),
        )).command_context(workspace_id)


class _ProcessLoss(BaseException):
    pass


class DelegationKeyGenerationCommandTests(unittest.TestCase):
    def setUp(self):
        module = "control_plane_kit_operations.delegation_key_generation_commands"
        self.assertIsNotNone(importlib.util.find_spec(module),
                             "missing generation command orchestration owner")
        self.api = importlib.import_module(module)
        self.fixture = generation_fixture.WorkloadHealthDelegationKeyGenerationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.context = AuthenticatedPrincipal(
            PrincipalIdentity("operator-issuer", "operator-a", PrincipalKind.OPERATOR),
            (WorkspaceGrant("workspace-a", SCOPES),),
        ).command_context("workspace-a")
        self.authorizer = _CurrentAuthority()
        self.calls = []
        self.local = threading.local()
        self.behavior = self.provider_result

    @contextmanager
    def unit_of_work(self):
        self.local.active = getattr(self.local, "active", 0) + 1
        try:
            with self.fixture.unit_of_work() as uow:
                yield uow
        finally:
            self.local.active -= 1

    def provider_result(self, grant):
        return SimpleNamespace(
            reference=grant.reference, purpose=grant.purpose, issuer=grant.issuer,
            correlation_id=grant.correlation_id,
            metadata=SimpleNamespace(version_id="version-a", version_number=1),
            public_key=self.fixture.evidence().public_key, replayed=len(self.calls) > 1,
        )

    def generate_provider(self, grant):
        self.assertEqual(getattr(self.local, "active", 0), 0,
                         "provider invoked within an open owner UoW")
        # A different connection must already observe committed dispatch.
        self.assertEqual(self.read().state, GenerationAttemptState.DISPATCHING)
        self.calls.append(grant)
        return self.behavior(grant)

    def service(self, *, factory=None):
        return self.api.DelegationKeyGenerationCommandService(
            factory or self.unit_of_work,
            SimpleNamespace(generate=self.generate_provider),
            authorizer=self.authorizer, clock=lambda: NOW,
        )

    def command(self):
        return self.api.GenerateDelegationKeyCommand(
            context=self.context, request_id="request-a",
            provider_registration_id=self.fixture.provider.registration_id,
            secret_reference=SecretReference("secret://workspace-secrets/keys/new-key"),
            purpose=self.fixture.purpose, issuer="cpk-server",
        )

    def reconcile(self):
        return self.service().reconcile(self.api.ReconcileDelegationKeyGenerationCommand(
            context=self.context, request_id="request-a",
        ))

    def read(self):
        with self.fixture.unit_of_work() as uow:
            return uow.stores.delegation_key_generation_attempts.get("workspace-a", "request-a")

    def revoke_provider(self):
        SecretProviderRegistrationService(self.fixture.unit_of_work).revoke_provider(
            RevokeSecretProviderCommand(
                workspace_id="workspace-a", provider_id=SecretProviderId("workspace-secrets"),
                revoked_by="operator-a", revoked_at=NOW,
                actor_scopes=(PolicyScope.SECRET_PROVIDER_REVOKE,),
            )
        )

    def test_committed_dispatch_and_admission_without_activation(self):
        result = self.service().generate(self.command()).descriptor()
        attempt = self.read()
        self.assertEqual(result["state"], "admitted")
        self.assertEqual(result["receipt"]["reference"], self.command().secret_reference.reference_id)
        self.assertEqual(result["receipt"]["version_id"], "version-a")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0].correlation_id, attempt.intent.attempt_id)
        with self.fixture.unit_of_work() as uow:
            keys = uow.stores.delegation_signing_keys.list_workspace("workspace-a")
        self.assertEqual(len(keys), 1)
        self.assertIsNone(keys[0].activated_at)
        serialized = json.dumps(result)
        for forbidden in ("provider-token", "secrets-endpoint", "credential_reference", "claim_kind"):
            self.assertNotIn(forbidden, serialized)

    def test_duplicate_reads_original_outcome_after_provider_revocation(self):
        original = self.service().generate(self.command()).descriptor()
        self.revoke_provider()
        self.assertEqual(self.service().generate(self.command()).descriptor(), original)
        self.assertEqual(len(self.calls), 1)
        changed_actor = replace(self.context.principal,
                                identity=replace(self.context.principal.identity, issuer="other"))
        for command in (
            replace(self.command(), issuer="different"),
            replace(self.command(), context=changed_actor.command_context("workspace-a")),
        ):
            with self.assertRaises(GenerationAttemptConflict):
                self.service().generate(command)
        self.assertEqual(len(self.calls), 1)

    def test_current_denial_and_unavailable_authority_precede_provider(self):
        for scopes in ((SCOPES[0],), (SCOPES[1],), ()):
            self.authorizer.scopes = scopes
            with self.assertRaises(GenerationAttemptAuthorizationDenied):
                self.service().generate(self.command())
        self.authorizer.available = False
        with self.assertRaises(GenerationAttemptAuthorizationDenied) as raised:
            self.service().generate(self.command())
        self.assertNotIn("sensitive-authorizer-diagnostic", str(raised.exception))
        self.assertIsNone(raised.exception.__context__)
        self.assertEqual(self.calls, [])

    def test_authorizer_cannot_amplify_original_grant_or_substitute_identity(self):
        self.authorizer.scopes = (*SCOPES, PolicyScope.DELEGATION_KEY_ACTIVATE)
        with self.assertRaises(GenerationAttemptAuthorizationDenied):
            self.service().generate(self.command())
        self.authorizer = SimpleNamespace(authorize=lambda principal, workspace: replace(
            principal, identity=replace(principal.identity, issuer="other")
        ).command_context(workspace))
        with self.assertRaises(GenerationAttemptAuthorizationDenied):
            self.service().generate(self.command())
        self.assertEqual(self.calls, [])

    def test_concurrent_duplicate_never_dispatches_twice(self):
        entered, release = threading.Event(), threading.Event()

        def paused(grant):
            entered.set()
            if not release.wait(timeout=15):
                raise AssertionError("test did not release provider")
            return self.provider_result(grant)

        self.behavior = paused
        with ThreadPoolExecutor(max_workers=2) as pool:
            initial = pool.submit(self.service().generate, self.command())
            try:
                self.assertTrue(entered.wait(timeout=10))
                duplicate = pool.submit(self.service().generate, self.command()).result(timeout=10)
                self.assertEqual(duplicate.descriptor()["state"], "dispatching")
                self.assertEqual(len(self.calls), 1)
            finally:
                release.set()
            self.assertEqual(initial.result(timeout=15).descriptor()["state"], "admitted")

    def test_current_denial_after_claim_commit_prevents_provider_invocation(self):
        original = self.authorizer

        def authorize(principal, workspace):
            rows = self.fixture.connection.execute(
                "SELECT state FROM cpk_delegation_key_generation_attempts "
                "WHERE workspace_id='workspace-a' AND request_id='request-a'"
            ).fetchall()
            if rows == [("dispatching",)]:
                original.available = False
            return original.authorize(principal, workspace)

        self.authorizer = SimpleNamespace(authorize=authorize)
        with self.assertRaises(GenerationAttemptAuthorizationDenied):
            self.service().generate(self.command())
        self.assertEqual(self.read().state, GenerationAttemptState.DISPATCHING)
        self.assertEqual(self.calls, [])

    def test_process_loss_requires_explicit_original_correlation_reconciliation(self):
        def interrupted(grant):
            raise _ProcessLoss()

        self.behavior = interrupted
        with self.assertRaises(_ProcessLoss):
            self.service().generate(self.command())
        self.assertEqual(self.service().generate(self.command()).descriptor()["state"], "dispatching")
        self.assertEqual(len(self.calls), 1)
        self.behavior = self.provider_result
        self.assertEqual(self.reconcile().descriptor()["state"], "admitted")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0], self.calls[1])

    def test_unknown_provider_exception_stays_uncertain_without_leaking(self):
        def failed(grant):
            raise RuntimeError("private-key-and-provider-credential")

        self.behavior = failed
        result = self.service().generate(self.command()).descriptor()
        self.assertEqual(result["state"], "uncertain")
        self.assertIsNone(result["receipt"])
        self.assertNotIn("private-key-and-provider-credential", json.dumps(result))
        self.assertEqual(self.read().state, GenerationAttemptState.UNCERTAIN)
        self.assertEqual(self.service().generate(self.command()).descriptor(), result)
        self.assertEqual(len(self.calls), 1)

    def test_mismatched_provider_evidence_is_uncertain_not_admitted(self):
        def mismatched(grant):
            result = self.provider_result(grant)
            result.correlation_id = "foreign-correlation"
            return result

        self.behavior = mismatched
        self.assertEqual(self.service().generate(self.command()).descriptor()["state"], "uncertain")
        self.assertIsNone(self.read().receipt)

    def test_lost_current_authority_retains_receipt_and_reconciles_admission_only(self):
        def revoked(grant):
            self.authorizer.scopes = (PolicyScope.DELEGATION_KEY_GENERATE,)
            return self.provider_result(grant)

        self.behavior = revoked
        with self.assertRaises(GenerationAttemptAuthorizationDenied):
            self.service().generate(self.command())
        retained = self.read()
        self.assertEqual(retained.state, GenerationAttemptState.RECEIPT_RECORDED)
        self.assertIsNotNone(retained.receipt)
        self.authorizer.scopes = SCOPES
        self.assertEqual(self.reconcile().descriptor()["state"], "admitted")
        self.assertEqual(len(self.calls), 1)

    def test_provider_revoked_during_effect_retains_pending_receipt(self):
        def revoked(grant):
            self.revoke_provider()
            return self.provider_result(grant)

        self.behavior = revoked
        result = self.service().generate(self.command()).descriptor()
        self.assertEqual(result["state"], "receipt-recorded")
        self.assertIsNone(self.read().reference_registration_id)
        with self.assertRaises(GenerationAttemptConflict):
            self.reconcile()
        self.assertEqual(len(self.calls), 1)

    def test_receipt_transaction_failure_leaves_unresolved_without_retry(self):
        @contextmanager
        def fail_receipt_transaction():
            with self.unit_of_work() as uow:
                yield uow
                # Fail only the actual receipt transaction, not a counted call.
                rows = uow._active_connection().execute(
                    "SELECT state FROM cpk_delegation_key_generation_attempts "
                    "WHERE workspace_id='workspace-a' AND request_id='request-a'"
                ).fetchall()
                if rows == [("receipt-recorded",)]:
                    raise RuntimeError("sensitive-storage-diagnostic")

        with self.assertRaises(GenerationAttemptError) as raised:
            self.service(factory=fail_receipt_transaction).generate(self.command())
        self.assertNotIn("sensitive-storage-diagnostic", str(raised.exception))
        self.assertIsNone(raised.exception.__context__)
        self.assertEqual(self.read().state, GenerationAttemptState.DISPATCHING)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.reconcile().descriptor()["state"], "admitted")
        self.assertEqual(self.calls[0], self.calls[1])

    def test_legacy_internal_purpose_is_not_public_generation_authority(self):
        with self.assertRaises(GenerationAttemptError):
            self.service().generate(replace(self.command(), purpose=DelegationKeyPurpose.GATEWAY_PROBE))
        self.assertEqual(self.calls, [])

    def test_previously_revoked_destination_refuses_before_reserve_or_effect(self):
        owner = SecretProviderRegistrationService(self.fixture.unit_of_work)
        reference = owner.register_reference(RegisterSecretReferenceCommand(
            workspace_id="workspace-a", reference=self.command().secret_reference,
            provider_registration_id=self.fixture.provider.registration_id,
            allowed_intents=(self.fixture.intent,), admitted_by="operator-a",
            admitted_at=NOW, actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,),
        ))
        owner.revoke_reference(RevokeSecretReferenceCommand(
            workspace_id="workspace-a", registration_id=reference.registration_id,
            revoked_by="operator-a", revoked_at=NOW,
            actor_scopes=(PolicyScope.SECRET_PROVIDER_REVOKE,),
        ))
        with self.assertRaises(GenerationAttemptConflict):
            self.service().generate(self.command())
        self.assertEqual(self.calls, [])
        self.assertEqual(self.fixture.connection.execute(
            "SELECT count(*) FROM cpk_delegation_key_generation_attempts"
        ).fetchone(), (0,))

    def test_all_public_purposes_require_their_exact_provider_intent(self):
        families = (
            (DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT,
             SecretUseIntent.GATEWAY_NODE_HEALTH_READ_TRANSIT_SIGNING_KEY),
            (DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ,
             SecretUseIntent.WORKLOAD_NODE_HEALTH_READ_SIGNING_KEY),
            (DelegationKeyPurpose.WORKLOAD_NODE_CONTROL_SURFACE_READ,
             SecretUseIntent.GATEWAY_PROBE_SIGNING_KEY),
        )
        for index, (purpose, intent) in enumerate(families):
            with self.subTest(purpose=purpose):
                fixture = generation_fixture.DelegationKeyGenerationTests()
                fixture.purpose, fixture.intent = purpose, intent
                fixture.setUp()
                self.addCleanup(fixture.tearDown)
                self.fixture, self.calls = fixture, []
                with self.assertRaises(GenerationAttemptConflict):
                    self.service().generate(replace(self.command(), purpose=families[(index + 1) % 3][0]))
                self.assertEqual(self.calls, [])
                result = self.service().generate(self.command()).descriptor()
                self.assertEqual(result["state"], "admitted")
                self.assertEqual(result["purpose"], purpose.value)
                self.assertEqual(self.calls[0].custody_grant.intent, intent)

    def test_reconciliation_requires_full_original_actor(self):
        self.behavior = lambda grant: (_ for _ in ()).throw(_ProcessLoss())
        with self.assertRaises(_ProcessLoss):
            self.service().generate(self.command())
        for identity in (
            replace(self.context.principal.identity, issuer="other-issuer"),
            replace(self.context.principal.identity, kind=PrincipalKind.SERVICE),
        ):
            context = replace(self.context.principal, identity=identity).command_context("workspace-a")
            with self.assertRaises(GenerationAttemptAuthorizationDenied):
                self.service().reconcile(self.api.ReconcileDelegationKeyGenerationCommand(context, "request-a"))
        self.assertEqual(len(self.calls), 1)

    def test_post_effect_reference_conflict_retains_receipt_and_does_not_reinvoke(self):
        def competing_admission(grant):
            SecretProviderRegistrationService(self.fixture.unit_of_work).register_reference(
                RegisterSecretReferenceCommand(
                    workspace_id="workspace-a", reference=grant.reference,
                    provider_registration_id=self.fixture.provider.registration_id,
                    allowed_intents=(self.fixture.intent,), admitted_by="operator-a",
                    admitted_at=NOW, actor_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,),
                ))
            return self.provider_result(grant)

        self.behavior = competing_admission
        result = self.service().generate(self.command()).descriptor()
        self.assertEqual(result["state"], "receipt-recorded")
        self.assertIsNone(result["admission"])
        self.assertEqual(self.reconcile().descriptor()["state"], "receipt-recorded")
        self.assertEqual(len(self.calls), 1)
        with self.fixture.unit_of_work() as uow:
            self.assertEqual(uow.stores.delegation_signing_keys.list_workspace("workspace-a"), ())

    def test_competing_explicit_reconciliation_cannot_overwrite_newer_completion(self):
        self.behavior = lambda grant: (_ for _ in ()).throw(_ProcessLoss())
        with self.assertRaises(_ProcessLoss):
            self.service().generate(self.command())
        entered, release = threading.Event(), threading.Event()

        def competing(grant):
            result = self.provider_result(grant)
            if len(self.calls) == 2:
                entered.set()
                if not release.wait(timeout=15):
                    raise AssertionError("test did not release old reconciliation")
                result.metadata.version_id = "stale-version"
            return result

        self.behavior = competing
        with ThreadPoolExecutor(max_workers=2) as pool:
            old = pool.submit(self.reconcile)
            try:
                self.assertTrue(entered.wait(timeout=10))
                current = pool.submit(self.reconcile).result(timeout=10).descriptor()
                self.assertEqual(current["state"], "admitted")
                self.assertEqual(current["receipt"]["version_id"], "version-a")
            finally:
                release.set()
            self.assertEqual(old.result(timeout=10).descriptor(), current)
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(all(grant == self.calls[0] for grant in self.calls))

    def route_request(self, route, *, surface="http", payload=None, principal=None):
        path = {"workspace_id": "workspace-a"}
        if route != "command.delegation-key.generate":
            path["request_id"] = "request-a"
        payload = dict(payload or {})
        if surface == "mcp":
            payload = {**path, **payload}
            path = {}
        return SimpleNamespace(
            surface=surface, route_id=route,
            service_role=ControlPlaneServiceRole.READS if route.startswith("read.") else ControlPlaneServiceRole.EXECUTION,
            path_parameters=path, payload=payload,
            principal=principal or self.context.principal,
        )

    def public_payload(self):
        command = self.command()
        return {"request_id": command.request_id,
                "provider_registration_id": command.provider_registration_id,
                "secret_reference": command.secret_reference.reference_id,
                "purpose": command.purpose.value, "issuer": command.issuer}

    def test_http_mcp_sync_async_and_exact_status_share_durable_owner(self):
        adapter = CpkServerExecutionService(None, delegation_key_generation=self.service())
        first = adapter.handle(self.route_request("command.delegation-key.generate", payload=self.public_payload()))
        duplicate = asyncio.run(adapter.handle_async(self.route_request(
            "command.delegation-key.generate", surface="mcp", payload=self.public_payload())))
        self.assertEqual(first, duplicate)
        self.assertEqual(len(self.calls), 1)
        read_principal = replace(self.context.principal, workspace_grants=(WorkspaceGrant(
            "workspace-a", (PolicyScope.INSTANCE_WORKSPACE_READ, PolicyScope.DELEGATION_KEY_READ)),))
        reads = CpkServerReadService(self.fixture.unit_of_work)
        for surface in ("http", "mcp"):
            request = self.route_request("read.delegation-key-generation", surface=surface, principal=read_principal)
            self.assertEqual(reads.handle(request), first)
        self.assertEqual(adapter.handle(self.route_request("command.delegation-key-generation.reconcile")), first)
        self.assertEqual(len(self.calls), 1)

    def test_public_extra_fields_and_missing_service_fail_before_provider(self):
        adapter = CpkServerExecutionService(None, delegation_key_generation=self.service())
        for surface in ("http", "mcp"):
            for name in ("actor_id", "actor_scopes", "requested_at", "provider_url", "credentials", "labels"):
                with self.subTest(surface=surface, field=name):
                    request = self.route_request("command.delegation-key.generate", surface=surface,
                        payload={**self.public_payload(), name: "untrusted"})
                    with self.assertRaises(CpkServerApplicationError) as raised:
                        adapter.handle(request)
                    self.assertEqual(raised.exception.status, 400)
            request = self.route_request("command.delegation-key.generate", surface=surface,
                                         payload=self.public_payload())
            with self.assertRaises(CpkServerApplicationError) as raised:
                CpkServerExecutionService(None).handle(request)
            self.assertEqual(raised.exception.status, 501)
        self.assertEqual(self.calls, [])

    def test_exact_status_requires_both_read_scopes_and_closed_request(self):
        reads = CpkServerReadService(self.fixture.unit_of_work)
        for scopes in ((PolicyScope.DELEGATION_KEY_READ,), (PolicyScope.INSTANCE_WORKSPACE_READ,)):
            principal = replace(self.context.principal, workspace_grants=(WorkspaceGrant("workspace-a", scopes),))
            with self.assertRaises(CpkServerApplicationError) as raised:
                reads.handle(self.route_request("read.delegation-key-generation", principal=principal))
            self.assertEqual(raised.exception.status, 403)
        principal = replace(self.context.principal, workspace_grants=(WorkspaceGrant(
            "workspace-a", (PolicyScope.INSTANCE_WORKSPACE_READ, PolicyScope.DELEGATION_KEY_READ)),))
        with self.assertRaises(CpkServerApplicationError) as raised:
            reads.handle(self.route_request("read.delegation-key-generation", principal=principal,
                                            payload={"limit": 10}))
        self.assertEqual(raised.exception.status, 400)
