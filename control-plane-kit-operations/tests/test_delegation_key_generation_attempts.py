from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import importlib
import importlib.util
import threading
import unittest
import uuid

import psycopg

from control_plane_kit_core.identity import PrincipalIdentity, PrincipalKind
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.secrets import SecretReference
from control_plane_kit_operations.delegation_key_generation import (
    AdmitGeneratedDelegationSigningKey,
)
from control_plane_kit_operations.delegation_signing_keys import DelegationSigningKeyConflict
from control_plane_kit_operations.postgres import install_schema
from control_plane_kit_operations.postgres.schema import SchemaInstallationError
import test_delegation_key_generation as generation_fixture


NOW = "2026-10-22T08:30:00Z"
LATER = "2026-10-22T08:31:00Z"
SCOPES = (PolicyScope.DELEGATION_KEY_GENERATE, PolicyScope.DELEGATION_KEY_REGISTER)


class DelegationKeyGenerationAttemptTests(unittest.TestCase):
    def setUp(self):
        module = "control_plane_kit_operations.delegation_key_generation_attempts"
        self.assertIsNotNone(
            importlib.util.find_spec(module),
            "missing durable generation-attempt owner",
        )
        self.api = importlib.import_module(module)
        self.fixture = generation_fixture.DelegationKeyGenerationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.actor = PrincipalIdentity("operator-issuer", "operator-a", PrincipalKind.OPERATOR)
        self.grant = self.fixture.service().prepare(self.fixture.generate_command())
        self.intent = self.api.GenerationAttemptIntent("request-a", self.actor, self.grant)

    def reserve(self, intent=None):
        with self.fixture.unit_of_work() as uow:
            result = uow.stores.delegation_key_generation_attempts.reserve(
                intent or self.intent, created_at=NOW
            )
            uow.commit()
        return result

    def read(self):
        with self.fixture.unit_of_work() as uow:
            return uow.stores.delegation_key_generation_attempts.get("workspace-a", "request-a")

    def claim(self, *, reconcile=False, fence=0):
        with self.fixture.unit_of_work() as uow:
            store = uow.stores.delegation_key_generation_attempts
            method = store.claim_reconciliation if reconcile else store.claim_initial
            result = method("workspace-a", "request-a", expected_fence=fence, claimed_at=LATER)
            uow.commit()
        return result

    def receipt(self, claim, evidence=None):
        with self.fixture.unit_of_work() as uow:
            result = uow.stores.delegation_key_generation_attempts.record_receipt(
                claim, evidence or self.fixture.evidence(), observed_at=LATER
            )
            uow.commit()
        return result

    def admit(self, claim, *, actor=None, scopes=SCOPES, factory=None):
        return self.api.GenerationAttemptAdmissionService(
            factory or self.fixture.unit_of_work
        ).admit(
            claim, actor=actor or self.actor, actor_scopes=scopes, admitted_at=LATER
        )

    def test_reserve_restart_and_duplicate_preserve_original_intent(self):
        first = self.reserve()
        later_intent = replace(self.intent, grant=replace(self.grant, requested_at=LATER))
        self.assertEqual(self.reserve(later_intent), first)
        self.assertEqual(self.read(), first)
        self.assertEqual(first.state, self.api.GenerationAttemptState.INTENDED)
        self.assertEqual(first.fence, 0)
        self.assertIsNone(first.receipt)

    def test_full_identity_and_changed_semantics_conflict(self):
        first = self.reserve()
        changed = (
            replace(self.intent, actor=replace(self.actor, issuer="foreign-issuer")),
            replace(self.intent, actor=replace(self.actor, kind=PrincipalKind.SERVICE)),
            replace(self.intent, grant=replace(self.grant, issuer="other-signing-issuer")),
            replace(self.intent, grant=replace(self.grant, custody_grant=replace(
                self.grant.custody_grant, correlation_id="another-correlation"
            ))),
        )
        for intent in changed:
            with self.subTest(intent=intent):
                with self.assertRaises(self.api.GenerationAttemptConflict):
                    self.reserve(intent)
        self.assertEqual(self.read(), first)

    def test_actor_issuer_bound_uses_exact_utf8_bytes_without_normalization(self):
        actor = replace(self.actor, issuer="é" * 256)
        intent = replace(self.intent, actor=actor)
        self.assertEqual(self.reserve(intent).intent.actor, actor)
        self.assertEqual(self.read().intent.actor, actor)
        with self.assertRaises(self.api.GenerationAttemptError):
            replace(self.intent, actor=replace(actor, issuer=actor.issuer + "x"))
        with self.assertRaises(self.api.GenerationAttemptConflict):
            self.reserve(replace(self.intent, actor=replace(actor, issuer="e\u0301" * 170)))
        with self.assertRaises(self.api.GenerationAttemptError):
            replace(self.intent, actor=replace(self.actor, subject_id="different-operator"))

    def test_one_initial_and_one_reconciliation_claim_per_observed_fence(self):
        self.reserve()
        for reconcile, fence in ((False, 0), (True, 1)):
            barrier = threading.Barrier(2)

            def claim():
                barrier.wait(timeout=5)
                return self.claim(reconcile=reconcile, fence=fence)

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(claim) for _ in range(2)]
                results = [future.result(timeout=15) for future in futures]
            self.assertEqual(sum(result is not None for result in results), 1)
            self.assertEqual(self.read().fence, fence + 1)
        self.assertIsNone(self.claim(fence=2))

    def test_restart_never_reopens_dispatch_and_stale_completion_refuses(self):
        self.reserve()
        old = self.claim()
        with self.fixture.unit_of_work() as uow:
            uow.stores.delegation_key_generation_attempts.record_uncertain(old, observed_at=LATER)
            uow.commit()
        self.assertEqual(self.read().state, self.api.GenerationAttemptState.UNCERTAIN)
        self.assertIsNone(self.claim(fence=old.fence))
        current = self.claim(reconcile=True, fence=old.fence)
        for outcome in ("record_uncertain", "record_definite_failure", "record_refusal"):
            with self.subTest(outcome=outcome):
                with self.assertRaises(self.api.GenerationAttemptConflict):
                    with self.fixture.unit_of_work() as uow:
                        getattr(uow.stores.delegation_key_generation_attempts, outcome)(
                            old, observed_at=LATER
                        )
                        uow.commit()
        with self.assertRaises(self.api.GenerationAttemptConflict):
            self.receipt(old)
        self.receipt(current)
        with self.assertRaises(self.api.GenerationAttemptConflict):
            self.admit(old)
        self.assertEqual(self.read().state, self.api.GenerationAttemptState.RECEIPT_RECORDED)

    def test_receipt_is_monotonic_and_reconciliation_is_admission_only(self):
        self.reserve()
        initial = self.claim()
        retained = self.receipt(initial)
        self.assertEqual(self.receipt(initial), retained)
        self.assertEqual(self.receipt(initial, replace(retained.receipt, replayed=True)), retained)
        claim = self.claim(reconcile=True, fence=initial.fence)
        self.assertEqual(claim.kind, self.api.GenerationClaimKind.RECONCILE_ADMISSION)
        self.assertEqual(self.read().receipt, retained.receipt)
        for outcome in ("record_uncertain", "record_definite_failure", "record_refusal"):
            with self.subTest(outcome=outcome):
                with self.assertRaises(self.api.GenerationAttemptConflict):
                    with self.fixture.unit_of_work() as uow:
                        getattr(uow.stores.delegation_key_generation_attempts, outcome)(
                            claim, observed_at=LATER
                        )
                        uow.commit()
        with self.assertRaises(self.api.GenerationAttemptConflict):
            self.receipt(claim, replace(retained.receipt, version_id="different-version"))
        self.assertEqual(self.read().receipt, retained.receipt)

    def test_receipt_requires_exact_public_evidence(self):
        self.reserve()
        claim = self.claim()
        for evidence in (
            replace(self.fixture.evidence(), issuer="foreign-issuer"),
            replace(self.fixture.evidence(), workspace_id="foreign-workspace"),
            replace(self.fixture.evidence(), correlation_id="foreign-correlation"),
            {"private_key": "private-value-must-not-leak"},
        ):
            with self.subTest(evidence_type=type(evidence).__name__):
                with self.assertRaises(self.api.GenerationAttemptError) as raised:
                    self.receipt(claim, evidence)
                self.assertNotIn("private-value-must-not-leak", str(raised.exception))
        self.assertIsNone(self.read().receipt)

    def test_admission_commits_reference_key_and_attempt_without_activation(self):
        self.reserve()
        claim = self.claim()
        receipt = self.receipt(claim)
        admitted = self.admit(claim)
        self.assertEqual(admitted, self.read())
        self.assertEqual(admitted.state, self.api.GenerationAttemptState.ADMITTED)
        self.assertEqual(admitted.receipt, receipt.receipt)
        with self.fixture.unit_of_work() as uow:
            reference = uow.stores.secret_references.get_active("workspace-a", self.grant.reference)
            key = uow.stores.delegation_signing_keys.get(
                "workspace-a", self.grant.purpose, self.grant.issuer, receipt.receipt.public_key.key_id
            )
        self.assertEqual(admitted.reference_registration_id, reference.registration_id)
        self.assertEqual(admitted.key_registration_id, key.registration_id)
        self.assertIsNone(key.activated_at)
        self.assertIsNone(self.claim(reconcile=True, fence=claim.fence))
        with self.assertRaises(self.api.GenerationAttemptConflict):
            self.receipt(claim, replace(receipt.receipt, version_id="replacement"))
        self.assertEqual(self.read(), admitted)

    def test_admission_requires_full_original_actor_and_both_current_scopes(self):
        self.reserve()
        claim = self.claim()
        self.receipt(claim)
        for actor in (replace(self.actor, issuer="other"), replace(self.actor, kind=PrincipalKind.SERVICE)):
            with self.subTest(actor=actor):
                with self.assertRaises(self.api.GenerationAttemptAuthorizationDenied):
                    self.admit(claim, actor=actor)
        for scopes in ((PolicyScope.DELEGATION_KEY_GENERATE,), (PolicyScope.DELEGATION_KEY_REGISTER,)):
            with self.subTest(scopes=scopes):
                with self.assertRaises(self.api.GenerationAttemptAuthorizationDenied):
                    self.admit(claim, scopes=scopes)
        self.assertEqual(self.read().state, self.api.GenerationAttemptState.RECEIPT_RECORDED)

    def test_second_write_conflict_retains_receipt_without_partial_admission(self):
        self.fixture.service().admit_generated(AdmitGeneratedDelegationSigningKey(
            self.grant, self.fixture.evidence(), "operator-a", NOW, (PolicyScope.DELEGATION_KEY_REGISTER,)
        ))
        reference = SecretReference("secret://workspace-secrets/keys/new-destination")
        grant = self.fixture.service().prepare(replace(
            self.fixture.generate_command(), reference=reference, correlation_id="new-correlation"
        ))
        self.reserve(replace(self.intent, grant=grant))
        claim = self.claim()
        evidence = replace(
            self.fixture.evidence(), reference=reference, correlation_id=grant.correlation_id,
            public_key=replace(self.fixture.evidence().public_key, public_key_pem=generation_fixture.PUBLIC_KEY_B),
        )
        receipt = self.receipt(claim, evidence)
        with self.assertRaises(DelegationSigningKeyConflict):
            self.admit(claim)
        self.assertEqual(self.read(), receipt)
        with self.fixture.unit_of_work() as uow:
            refs = uow.stores.secret_references.list_active("workspace-a")
        self.assertEqual(tuple(value.reference for value in refs), (self.grant.reference,))

    def test_late_outer_failure_rolls_back_all_admission_but_retains_receipt(self):
        self.reserve()
        claim = self.claim()
        retained = self.receipt(claim)

        @contextmanager
        def fail_after_commit_request():
            with self.fixture.unit_of_work() as uow:
                yield uow
                raise RuntimeError("late owner failure")

        with self.assertRaisesRegex(RuntimeError, "^late owner failure$"):
            self.admit(claim, factory=fail_after_commit_request)
        self.assertEqual(self.read(), retained)
        with self.fixture.unit_of_work() as uow:
            self.assertEqual(uow.stores.secret_references.list_active("workspace-a"), ())
            self.assertEqual(uow.stores.delegation_signing_keys.list_workspace("workspace-a"), ())

    def test_nonempty_pre_attempt_schema_refuses_without_repair_or_row_loss(self):
        schema = "generation_attempt_" + uuid.uuid4().hex
        with psycopg.connect(self.fixture.database_url, autocommit=True) as connection:
            connection.execute(f'CREATE SCHEMA "{schema}"')
            try:
                connection.execute(f'SET search_path TO "{schema}"')
                install_schema(connection)
                connection.execute("INSERT INTO cpk_workspaces (workspace_id, name, lifecycle) VALUES ('retained', 'Retained', 'created')")
                connection.execute("DROP TABLE cpk_delegation_key_generation_attempts")
                with self.assertRaisesRegex(SchemaInstallationError, "^operations schema reset is required$"):
                    install_schema(connection)
                self.assertEqual(connection.execute("SELECT workspace_id FROM cpk_workspaces").fetchall(), [("retained",)])
                self.assertEqual(connection.execute("SELECT to_regclass('cpk_delegation_key_generation_attempts')").fetchone(), (None,))
            finally:
                connection.execute("SET search_path TO public")
                connection.execute(f'DROP SCHEMA "{schema}" CASCADE')


if __name__ == "__main__":
    unittest.main()
