"""Actor and exact predecessor are part of durable managed command identity."""

import unittest
from dataclasses import replace

from control_plane_kit_core.identity import (
    AuthenticatedPrincipal, PrincipalIdentity, PrincipalKind, WorkspaceGrant,
)
from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations import records


class ManagedExecutionReceiptIntentTests(unittest.TestCase):
    def context(self, *, actor="operator-a", issuer="authenticator-a", workspace="workspace-a",
            kind=PrincipalKind.OPERATOR,
            scopes=(PolicyScope.EXECUTION_OPERATE, PolicyScope.NODE_CONTROL_READ,
                PolicyScope.RUNTIME_AUTHORITY_USE)):
        return AuthenticatedPrincipal(PrincipalIdentity(issuer, actor, kind),
            (WorkspaceGrant(workspace, scopes),)).command_context(workspace)

    def intent(self, context=None, predecessor=None):
        value = getattr(records, "ManagedExecutionCommandIntent", None)
        self.assertIsNotNone(value, "managed receipt cannot bind actor and predecessor")
        return value.from_context(context or self.context(), predecessor=predecessor)

    def fingerprint(self, intent):
        return records.execution_command_intent_fingerprint(
            run_id="run-a", worker_id="worker-a", authority_scopes=(PolicyScope.EXECUTION_OPERATE,),
            claim_generation=7, max_effects=1, managed_intent=intent,
        )

    def test_actor_identity_workspace_and_granted_scopes_are_independent_of_worker(self):
        original = self.intent()
        changed = (
            self.intent(self.context(actor="operator-b")),
            self.intent(self.context(issuer="authenticator-b")),
            self.intent(self.context(workspace="workspace-b")),
            self.intent(self.context(scopes=(PolicyScope.EXECUTION_OPERATE,))),
            self.intent(self.context(kind=PrincipalKind.SERVICE)),
        )
        fingerprints = {self.fingerprint(original), *(self.fingerprint(value) for value in changed)}
        self.assertEqual(len(fingerprints), 6)
        self.assertEqual(original.actor, self.context().principal.identity)
        self.assertEqual(original.workspace_id, "workspace-a")
        self.assertEqual(original.actor_scopes, self.context().granted_scopes)
        reordered = self.intent(self.context(scopes=tuple(reversed(self.context().granted_scopes))))
        self.assertEqual(self.fingerprint(reordered), self.fingerprint(original))

    def test_reobserve_retains_exact_predecessor_and_deterministic_successor(self):
        predecessor = EffectAttemptIdentity(RunId("run-a"), "connection", 1)
        original = self.intent(predecessor=predecessor)
        self.assertEqual(original.predecessor, predecessor)
        self.assertEqual(original.successor, replace(predecessor, attempt=2))
        self.assertEqual(self.fingerprint(original), self.fingerprint(self.intent(predecessor=predecessor)))
        self.assertNotEqual(self.fingerprint(original), self.fingerprint(self.intent()))
        self.assertNotEqual(self.fingerprint(original), self.fingerprint(
            self.intent(predecessor=replace(predecessor, attempt=2))))
        self.assertNotEqual(self.fingerprint(original), self.fingerprint(
            self.intent(predecessor=replace(predecessor, activity_id="other-connection"))))

    def test_legacy_fingerprint_remains_distinct_and_reobserve_cannot_overflow(self):
        legacy = records.execution_command_intent_fingerprint(
            run_id="run-a", worker_id="worker-a", authority_scopes=(PolicyScope.EXECUTION_OPERATE,),
            claim_generation=7, max_effects=1,
        )
        # Frozen pre-change v1 canonical JSON: authority_scopes, claim_generation,
        # command="deployment.execute", domain=control-plane-kit.operations.execution-command.v1,
        # max_effects="1", run_id="run-a", worker_id="worker-a"; sorted compact keys.
        self.assertEqual(legacy, "b8c37da45b9dd0405226a2fe52ab75c19ec2bc1a4282852c98e36b6b1809700c")
        self.assertNotEqual(legacy, self.fingerprint(self.intent()))
        predecessor = EffectAttemptIdentity(RunId("run-a"), "connection", 2_147_483_647)
        with self.assertRaises(ValueError):
            self.intent(predecessor=predecessor)

    def test_command_run_cannot_borrow_another_runs_predecessor(self):
        foreign = EffectAttemptIdentity(RunId("foreign-run"), "connection", 1)
        with self.assertRaises(ValueError):
            self.fingerprint(self.intent(predecessor=foreign))
