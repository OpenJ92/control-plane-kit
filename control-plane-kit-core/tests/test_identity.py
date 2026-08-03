from __future__ import annotations

import unittest

from control_plane_kit_core.identity import (
    AuthenticatedPrincipal,
    IdentityContractError,
    PrincipalIdentity,
    PrincipalKind,
    TrustedCommandContext,
    WorkspaceGrant,
)
from control_plane_kit_core.policies import PolicyScope


class AuthenticatedIdentityTests(unittest.TestCase):
    def test_principal_is_credential_free_and_has_bounded_descriptor(self) -> None:
        token = b"bearer-do-not-retain"
        principal = _principal()
        same_principal_from_another_credential = _principal()

        self.assertEqual(principal, same_principal_from_another_credential)
        self.assertEqual(hash(principal), hash(same_principal_from_another_credential))
        self.assertNotIn(token.decode(), repr(principal))
        self.assertNotIn("credential", repr(principal).lower())
        self.assertEqual(
            principal.descriptor(),
            {
                "identity": {
                    "issuer": "https://identity.openj92.dev",
                    "subject_id": "operator-jacob",
                    "kind": "operator",
                },
                "workspace_grants": (
                    {
                        "workspace_id": "workspace-a",
                        "scopes": (
                            "instance:workspace:read",
                            "plan:request",
                        ),
                    },
                ),
            },
        )

    def test_public_payload_cannot_construct_authenticated_authority(self) -> None:
        payload = {
            "actor_id": "forged-actor",
            "actor_scopes": [PolicyScope.PLAN_APPROVE.value],
            "workspace_id": "workspace-a",
        }

        with self.assertRaises(TypeError):
            AuthenticatedPrincipal(**payload)  # type: ignore[arg-type]

    def test_workspace_grants_reject_open_duplicate_and_malformed_scopes(self) -> None:
        with self.assertRaisesRegex(IdentityContractError, "PolicyScope"):
            WorkspaceGrant(
                "workspace-a",
                ("plan:request",),  # type: ignore[arg-type]
            )
        with self.assertRaisesRegex(IdentityContractError, "duplicates"):
            WorkspaceGrant(
                "workspace-a",
                (PolicyScope.PLAN_REQUEST, PolicyScope.PLAN_REQUEST),
            )
        with self.assertRaisesRegex(IdentityContractError, "tuple"):
            WorkspaceGrant(
                "workspace-a",
                [PolicyScope.PLAN_REQUEST],  # type: ignore[arg-type]
            )

    def test_principal_rejects_duplicate_workspace_grants(self) -> None:
        identity = PrincipalIdentity(
            "https://identity.openj92.dev",
            "operator-jacob",
            PrincipalKind.OPERATOR,
        )

        with self.assertRaisesRegex(IdentityContractError, "duplicate workspace"):
            AuthenticatedPrincipal(
                identity,
                (WorkspaceGrant("workspace-a"), WorkspaceGrant("workspace-a")),
            )

    def test_command_context_cannot_exceed_authenticated_workspace_grant(self) -> None:
        principal = _principal()
        context = principal.command_context("workspace-a")

        self.assertEqual(context.actor_id, "operator-jacob")
        self.assertEqual(
            context.granted_scopes,
            (PolicyScope.INSTANCE_WORKSPACE_READ, PolicyScope.PLAN_REQUEST),
        )
        self.assertEqual(
            context.descriptor(),
            {
                "principal": {
                    "issuer": "https://identity.openj92.dev",
                    "subject_id": "operator-jacob",
                    "kind": "operator",
                },
                "workspace_id": "workspace-a",
                "granted_scopes": (
                    "instance:workspace:read",
                    "plan:request",
                ),
            },
        )

        with self.assertRaisesRegex(IdentityContractError, "must match"):
            TrustedCommandContext(
                principal,
                "workspace-a",
                (PolicyScope.PLAN_APPROVE_DESTRUCTIVE,),
            )
        with self.assertRaisesRegex(IdentityContractError, "no grant"):
            principal.command_context("workspace-b")

    def test_worker_identity_is_not_operator_identity(self) -> None:
        operator = PrincipalIdentity(
            "https://identity.openj92.dev",
            "subject-a",
            PrincipalKind.OPERATOR,
        )
        worker = PrincipalIdentity(
            "https://identity.openj92.dev",
            "subject-a",
            PrincipalKind.WORKER,
        )

        self.assertNotEqual(operator, worker)


def _principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        PrincipalIdentity(
            issuer="https://identity.openj92.dev",
            subject_id="operator-jacob",
            kind=PrincipalKind.OPERATOR,
        ),
        (
            WorkspaceGrant(
                "workspace-a",
                (PolicyScope.PLAN_REQUEST, PolicyScope.INSTANCE_WORKSPACE_READ),
            ),
        ),
    )


if __name__ == "__main__":
    unittest.main()
