"""Pure authenticated identity and trusted command-context contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from control_plane_kit_core.policies import PolicyScope


class IdentityContractError(ValueError):
    """Raised when authenticated identity material is malformed."""


class PrincipalKind(StrEnum):
    """Closed kinds of authenticated subjects at the public process boundary."""

    OPERATOR = "operator"
    SERVICE = "service"
    WORKER = "worker"


@dataclass(frozen=True, order=True)
class PrincipalIdentity:
    """Provider-neutral issuer, subject, and kind for one authenticated actor."""

    issuer: str
    subject_id: str
    kind: PrincipalKind

    def __post_init__(self) -> None:
        _required_text(self.issuer, "principal issuer")
        _required_text(self.subject_id, "principal subject_id")
        if not isinstance(self.kind, PrincipalKind):
            raise IdentityContractError("principal kind must be PrincipalKind")

    def descriptor(self) -> dict[str, str]:
        return {
            "issuer": self.issuer,
            "subject_id": self.subject_id,
            "kind": self.kind.value,
        }


@dataclass(frozen=True, order=True)
class WorkspaceGrant:
    """Closed scopes granted to one principal in one workspace."""

    workspace_id: str
    scopes: tuple[PolicyScope, ...] = ()

    def __post_init__(self) -> None:
        _required_text(self.workspace_id, "workspace grant workspace_id")
        scopes = _policy_scopes(self.scopes, "workspace grant scopes")
        object.__setattr__(self, "scopes", scopes)

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "scopes": tuple(scope.value for scope in self.scopes),
        }


@dataclass(frozen=True, order=True)
class AuthenticatedPrincipal:
    """Credential-free identity and workspace grants produced by authentication."""

    identity: PrincipalIdentity
    workspace_grants: tuple[WorkspaceGrant, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.identity, PrincipalIdentity):
            raise IdentityContractError(
                "authenticated principal identity must be PrincipalIdentity"
            )
        if not isinstance(self.workspace_grants, tuple):
            raise IdentityContractError(
                "authenticated principal workspace_grants must be a tuple"
            )
        if not all(
            isinstance(grant, WorkspaceGrant) for grant in self.workspace_grants
        ):
            raise IdentityContractError(
                "authenticated principal grants must be WorkspaceGrant values"
            )
        workspace_ids = tuple(grant.workspace_id for grant in self.workspace_grants)
        if len(set(workspace_ids)) != len(workspace_ids):
            raise IdentityContractError(
                "authenticated principal has duplicate workspace grants"
            )
        object.__setattr__(
            self,
            "workspace_grants",
            tuple(sorted(self.workspace_grants, key=lambda grant: grant.workspace_id)),
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "identity": self.identity.descriptor(),
            "workspace_grants": tuple(
                grant.descriptor() for grant in self.workspace_grants
            ),
        }

    def command_context(self, workspace_id: str) -> "TrustedCommandContext":
        _required_text(workspace_id, "command workspace_id")
        for grant in self.workspace_grants:
            if grant.workspace_id == workspace_id:
                return TrustedCommandContext(
                    principal=self,
                    workspace_id=workspace_id,
                    granted_scopes=grant.scopes,
                )
        raise IdentityContractError(
            "authenticated principal has no grant for requested workspace"
        )


@dataclass(frozen=True, order=True)
class TrustedCommandContext:
    """Workspace authority derived from an authenticated principal, not payload."""

    principal: AuthenticatedPrincipal
    workspace_id: str
    granted_scopes: tuple[PolicyScope, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.principal, AuthenticatedPrincipal):
            raise IdentityContractError(
                "trusted command context principal must be AuthenticatedPrincipal"
            )
        _required_text(self.workspace_id, "trusted command context workspace_id")
        scopes = _policy_scopes(
            self.granted_scopes,
            "trusted command context granted_scopes",
        )
        expected = next(
            (
                grant.scopes
                for grant in self.principal.workspace_grants
                if grant.workspace_id == self.workspace_id
            ),
            None,
        )
        if expected is None:
            raise IdentityContractError(
                "trusted command context workspace is not granted to principal"
            )
        if scopes != expected:
            raise IdentityContractError(
                "trusted command context scopes must match principal workspace grant"
            )
        object.__setattr__(self, "granted_scopes", scopes)

    @property
    def actor_id(self) -> str:
        """Stable durable actor identity for operations history."""

        return self.principal.identity.subject_id

    def descriptor(self) -> dict[str, object]:
        return {
            "principal": self.principal.identity.descriptor(),
            "workspace_id": self.workspace_id,
            "granted_scopes": tuple(scope.value for scope in self.granted_scopes),
        }


class CredentialVerifier(Protocol):
    """Process-boundary protocol that exchanges credentials for a principal."""

    def authenticate(self, credential: bytes) -> AuthenticatedPrincipal:
        """Validate opaque credential bytes without retaining or returning them."""


class PrincipalAuthorizer(Protocol):
    """Operations-boundary protocol deriving one trusted workspace context."""

    def authorize(
        self,
        principal: AuthenticatedPrincipal,
        workspace_id: str,
    ) -> TrustedCommandContext:
        """Return only authority already present in the principal's grant."""


def _required_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise IdentityContractError(f"{field_name} must be nonempty text")


def _policy_scopes(
    value: object,
    field_name: str,
) -> tuple[PolicyScope, ...]:
    if not isinstance(value, tuple):
        raise IdentityContractError(f"{field_name} must be a tuple")
    if not all(isinstance(scope, PolicyScope) for scope in value):
        raise IdentityContractError(
            f"{field_name} must contain only PolicyScope values"
        )
    if len(set(value)) != len(value):
        raise IdentityContractError(f"{field_name} must not contain duplicates")
    return tuple(sorted(value, key=lambda scope: scope.value))
