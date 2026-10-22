"""Owning service laws: real UoW and controlled provider/authority ports."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
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
from control_plane_kit_core.secrets import SecretProviderId, SecretReference
from control_plane_kit_operations.delegation_key_generation_attempts import (
    GenerationAttemptAuthorizationDenied, GenerationAttemptConflict,
    GenerationAttemptError, GenerationAttemptState,
)
from control_plane_kit_operations.secret_providers import (
    RevokeSecretProviderCommand, SecretProviderRegistrationService,
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
