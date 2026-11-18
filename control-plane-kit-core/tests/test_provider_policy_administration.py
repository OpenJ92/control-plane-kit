from dataclasses import replace
import unittest

from control_plane_kit_core.provider_policy import (
    ProviderPolicyCommandResult,
    ProviderPolicyContractError,
    ProviderPolicyCoordinate,
    ProviderPolicyErrorCode,
    ProviderPolicyFailure,
    ProviderPolicyGrantCommand,
    ProviderPolicyIntentMode,
    ProviderPolicyListRequest,
    ProviderPolicyOperation,
    ProviderPolicyOutcome,
    ProviderPolicyReadRequest,
    ProviderPolicyRevokeCommand,
    provider_policy_command_fingerprint,
)


class ProviderPolicyAdministrationContractTests(unittest.TestCase):
    def coordinate(self) -> ProviderPolicyCoordinate:
        return ProviderPolicyCoordinate(
            subject="workload-a",
            workspace_id="workspace-a",
            action="secret.metadata",
            intent=None,
        )

    def test_operations_and_intent_filters_are_closed(self) -> None:
        self.assertEqual(
            tuple(value.value for value in ProviderPolicyOperation),
            ("grant", "revoke", "read", "list", "command-result"),
        )
        self.assertEqual(
            tuple(value.value for value in ProviderPolicyIntentMode),
            ("any", "none", "exact"),
        )
        with self.assertRaises(ValueError):
            ProviderPolicyOperation("forward")

    def test_mutation_commands_are_exact_bounded_and_transport_free(self) -> None:
        grant = ProviderPolicyGrantCommand(
            logical_provider_id="provider-a",
            provider_id="Secrets Provider East",
            coordinate=self.coordinate(),
            expected_revision=3,
            caller_idempotency_key="grant-workload-a",
            provider_correlation="cpk.install-a.command-a",
        )
        revoke = ProviderPolicyRevokeCommand(
            logical_provider_id="provider-a",
            provider_id="Secrets Provider East",
            coordinate=self.coordinate(),
            expected_revision=4,
            caller_idempotency_key="revoke-workload-a",
            provider_correlation="cpk.install-a.command-b",
            grant_id="grant-a",
        )

        self.assertEqual(grant.operation, ProviderPolicyOperation.GRANT)
        self.assertEqual(revoke.operation, ProviderPolicyOperation.REVOKE)
        self.assertNotEqual(
            provider_policy_command_fingerprint(grant),
            provider_policy_command_fingerprint(revoke),
        )
        descriptor = grant.descriptor()
        self.assertEqual(descriptor["intent"], None)
        for forbidden in ("endpoint", "url", "credential", "bearer", "actor", "method"):
            self.assertNotIn(forbidden, descriptor)

        with self.assertRaises(ProviderPolicyContractError):
            replace(grant, expected_revision=1 << 63)
        with self.assertRaises(ProviderPolicyContractError):
            replace(revoke, grant_id="")

    def test_reads_have_exact_filters_and_bounded_pagination(self) -> None:
        read = ProviderPolicyReadRequest("provider-a", "grant-a")
        listing = ProviderPolicyListRequest(
            logical_provider_id="provider-a",
            subject="workload-a",
            workspace_id="workspace-a",
            action="secret.metadata",
            intent_mode=ProviderPolicyIntentMode.NONE,
            limit=100,
            after_grant_id="grant-a",
        )

        self.assertEqual(read.operation, ProviderPolicyOperation.READ)
        self.assertEqual(listing.operation, ProviderPolicyOperation.LIST)
        with self.assertRaises(ProviderPolicyContractError):
            replace(listing, limit=101)
        with self.assertRaises(ProviderPolicyContractError):
            replace(listing, intent_mode=ProviderPolicyIntentMode.EXACT, intent=None)
        with self.assertRaises(ProviderPolicyContractError):
            replace(listing, intent_mode=ProviderPolicyIntentMode.NONE, intent="postgres.password")

    def test_canonical_results_match_only_the_immutable_command(self) -> None:
        grant = ProviderPolicyGrantCommand(
            logical_provider_id="provider-a",
            provider_id="Secrets Provider East",
            coordinate=self.coordinate(),
            expected_revision=3,
            caller_idempotency_key="grant-workload-a",
            provider_correlation="cpk.install-a.command-a",
        )
        result = ProviderPolicyCommandResult(
            provider_id="Secrets Provider East",
            correlation_id=grant.provider_correlation,
            operation=ProviderPolicyOperation.GRANT,
            coordinate=grant.coordinate,
            expected_revision=3,
            outcome=ProviderPolicyOutcome.GRANTED,
            code="provider-policy-granted",
            grant_id="provider-issued-grant-a",
            resulting_revision=4,
            provider_actor="policy-admin",
            occurred_at="2026-10-28T12:00:00Z",
        )

        self.assertTrue(result.matches(grant))
        self.assertFalse(replace(result, correlation_id="other-command").matches(grant))
        self.assertFalse(replace(result, provider_id="Secrets Provider West").matches(grant))
        self.assertFalse(replace(result, coordinate=replace(grant.coordinate, subject="other")).matches(grant))

        revoke = ProviderPolicyRevokeCommand(
            logical_provider_id="provider-a",
            provider_id="Secrets Provider East",
            coordinate=self.coordinate(),
            expected_revision=4,
            caller_idempotency_key="revoke-workload-a",
            provider_correlation="cpk.install-a.command-b",
            grant_id="provider-issued-grant-a",
        )
        revoked = replace(
            result,
            correlation_id=revoke.provider_correlation,
            operation=ProviderPolicyOperation.REVOKE,
            expected_revision=4,
            outcome=ProviderPolicyOutcome.REVOKED,
            code="provider-policy-revoked",
            resulting_revision=5,
        )
        self.assertTrue(revoked.matches(revoke))
        self.assertFalse(replace(revoked, grant_id="different-grant").matches(revoke))

    def test_canonical_results_reject_contradictory_evidence(self) -> None:
        result = ProviderPolicyCommandResult(
            provider_id="Secrets Provider East",
            correlation_id="cpk.install-a.command-a",
            operation=ProviderPolicyOperation.GRANT,
            coordinate=self.coordinate(),
            expected_revision=3,
            outcome=ProviderPolicyOutcome.GRANTED,
            code="provider-policy-granted",
            grant_id="provider-issued-grant-a",
            resulting_revision=4,
            provider_actor="policy-admin",
            occurred_at="2026-10-28T12:00:00Z",
        )

        for change in (
            {"operation": "grant"},
            {"outcome": ProviderPolicyOutcome.REVOKED},
            {"code": "provider-policy-revoked"},
            {"resulting_revision": 0},
        ):
            with self.subTest(change=change):
                with self.assertRaises(ProviderPolicyContractError):
                    replace(result, **change)

    def test_failures_are_bounded_and_distinguish_ambiguous_effects(self) -> None:
        denied = ProviderPolicyFailure(
            ProviderPolicyErrorCode.DENIED,
            "provider policy administration denied",
        )
        uncertain = ProviderPolicyFailure(
            ProviderPolicyErrorCode.TRANSPORT,
            "provider response unavailable",
            effect_may_have_occurred=True,
        )

        self.assertFalse(denied.effect_may_have_occurred)
        self.assertTrue(uncertain.effect_may_have_occurred)
        self.assertNotIn("credential", denied.descriptor())
        malformed_after_dispatch = ProviderPolicyFailure(
            ProviderPolicyErrorCode.MALFORMED_RESULT,
            "provider result was invalid",
            effect_may_have_occurred=True,
        )
        self.assertTrue(malformed_after_dispatch.effect_may_have_occurred)
        with self.assertRaises(ProviderPolicyContractError):
            ProviderPolicyFailure(
                ProviderPolicyErrorCode.DENIED,
                "denied",
                effect_may_have_occurred=True,
            )

    def test_conflict_results_have_only_the_accepted_provider_shapes(self) -> None:
        common = dict(
            provider_id="Secrets Provider East",
            correlation_id="cpk.install-a.command-a",
            coordinate=self.coordinate(),
            expected_revision=3,
            provider_actor="policy-admin",
            occurred_at="2026-10-28T12:00:00Z",
        )
        revision_conflict = ProviderPolicyCommandResult(
            **common,
            operation=ProviderPolicyOperation.GRANT,
            outcome=ProviderPolicyOutcome.CONFLICT,
            code="policy-revision-conflict",
            grant_id=None,
            resulting_revision=5,
        )
        active = ProviderPolicyCommandResult(
            **common,
            operation=ProviderPolicyOperation.GRANT,
            outcome=ProviderPolicyOutcome.CONFLICT,
            code="provider-grant-active",
            grant_id="existing-grant",
            resulting_revision=3,
        )
        revoke_conflict = ProviderPolicyCommandResult(
            **common,
            operation=ProviderPolicyOperation.REVOKE,
            outcome=ProviderPolicyOutcome.CONFLICT,
            code="provider-grant-revoke-conflict",
            grant_id="requested-grant",
            resulting_revision=3,
        )
        self.assertEqual(
            (revision_conflict.code, active.code, revoke_conflict.code),
            (
                "policy-revision-conflict",
                "provider-grant-active",
                "provider-grant-revoke-conflict",
            ),
        )

        valid_grant = ProviderPolicyCommandResult(
            **common,
            operation=ProviderPolicyOperation.GRANT,
            outcome=ProviderPolicyOutcome.GRANTED,
            code="provider-policy-granted",
            grant_id="new-grant",
            resulting_revision=4,
        )
        for change in (
            {"outcome": ProviderPolicyOutcome.CONFLICT},
            {"outcome": ProviderPolicyOutcome.CONFLICT, "code": "unknown-conflict"},
            {
                "outcome": ProviderPolicyOutcome.CONFLICT,
                "code": "provider-grant-revoke-conflict",
            },
        ):
            with self.subTest(change=change):
                with self.assertRaises(ProviderPolicyContractError):
                    replace(valid_grant, **change)


if __name__ == "__main__":
    unittest.main()
