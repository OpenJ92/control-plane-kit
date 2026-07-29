"""Credential extraction and verifier composition for cpk-server."""

from __future__ import annotations

from dataclasses import dataclass, field
import hmac
from typing import Mapping

from control_plane_kit_core.identity import (
    AuthenticatedPrincipal,
    CredentialVerifier,
    PrincipalIdentity,
    PrincipalKind,
    WorkspaceGrant,
)


MAXIMUM_BEARER_CREDENTIAL_BYTES = 4096


class CredentialAuthenticationError(ValueError):
    """Bounded authentication failure that never retains credential material."""

    def __init__(self) -> None:
        super().__init__("invalid credential")


def authenticate_bearer_credential(
    headers: Mapping[str, str],
    verifier: CredentialVerifier,
) -> AuthenticatedPrincipal:
    """Extract one bearer credential and exchange it for a trusted principal."""

    credential = _extract_bearer_credential(headers)
    try:
        principal = verifier.authenticate(credential)
    except Exception:
        principal = None
    finally:
        credential = b""
    if not isinstance(principal, AuthenticatedPrincipal):
        raise CredentialAuthenticationError()
    return principal


def _extract_bearer_credential(headers: Mapping[str, str]) -> bytes:
    values = tuple(
        value for name, value in headers.items() if name.lower() == "authorization"
    )
    if len(values) != 1:
        raise CredentialAuthenticationError()
    value = values[0]
    if not isinstance(value, str):
        raise CredentialAuthenticationError()
    parts = value.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise CredentialAuthenticationError()
    token = parts[1]
    if not token or any(character.isspace() for character in token):
        raise CredentialAuthenticationError()
    try:
        credential = token.encode("ascii")
    except UnicodeEncodeError as error:
        raise CredentialAuthenticationError() from error
    if len(credential) > MAXIMUM_BEARER_CREDENTIAL_BYTES:
        raise CredentialAuthenticationError()
    return credential


@dataclass(frozen=True, slots=True, eq=False)
class StaticDevelopmentPrincipalCredential:
    """One explicit local-development credential and its trusted principal."""

    credential: bytes = field(repr=False, compare=False, hash=False)
    principal: AuthenticatedPrincipal

    def __post_init__(self) -> None:
        credential_text = None
        if isinstance(self.credential, bytes):
            try:
                credential_text = self.credential.decode("ascii")
            except UnicodeDecodeError:
                credential_text = None
        if (
            not isinstance(self.credential, bytes)
            or not self.credential
            or len(self.credential) > MAXIMUM_BEARER_CREDENTIAL_BYTES
            or credential_text is None
            or any(character.isspace() for character in credential_text)
        ):
            raise CredentialAuthenticationError()
        if not isinstance(self.principal, AuthenticatedPrincipal):
            raise CredentialAuthenticationError()


@dataclass(frozen=True, slots=True, eq=False)
class StaticDevelopmentMultiCredentialVerifier:
    """Explicit local-development verifier for several known principals."""

    credentials: tuple[StaticDevelopmentPrincipalCredential, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.credentials, tuple)
            or not self.credentials
            or len(self.credentials) > 16
            or not all(
                isinstance(credential, StaticDevelopmentPrincipalCredential)
                for credential in self.credentials
            )
        ):
            raise CredentialAuthenticationError()
        raw_credentials = tuple(credential.credential for credential in self.credentials)
        if len(set(raw_credentials)) != len(raw_credentials):
            raise CredentialAuthenticationError()

    def authenticate(self, credential: bytes) -> AuthenticatedPrincipal:
        for candidate in self.credentials:
            if hmac.compare_digest(credential, candidate.credential):
                return candidate.principal
        raise CredentialAuthenticationError()


@dataclass(frozen=True, slots=True, eq=False)
class StaticDevelopmentCredentialVerifier:
    """Explicit local-development verifier; never an accept-all default."""

    expected_credential: bytes = field(repr=False, compare=False, hash=False)
    workspace_grants: tuple[WorkspaceGrant, ...] = ()

    def __post_init__(self) -> None:
        self._multi()

    def authenticate(self, credential: bytes) -> AuthenticatedPrincipal:
        return self._multi().authenticate(credential)

    def _multi(self) -> StaticDevelopmentMultiCredentialVerifier:
        return StaticDevelopmentMultiCredentialVerifier(
            (
                StaticDevelopmentPrincipalCredential(
                    self.expected_credential,
                    AuthenticatedPrincipal(
                        _static_operator_identity(),
                        self.workspace_grants,
                    ),
                ),
            )
        )


def _static_operator_identity() -> PrincipalIdentity:
    return PrincipalIdentity(
        issuer="urn:control-plane-kit:static-development",
        subject_id="local-development-operator",
        kind=PrincipalKind.OPERATOR,
    )


def static_development_principal(
    *,
    subject_id: str,
    kind: PrincipalKind,
    workspace_grants: tuple[WorkspaceGrant, ...],
) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        PrincipalIdentity(
            issuer="urn:control-plane-kit:static-development",
            subject_id=subject_id,
            kind=kind,
        ),
        workspace_grants,
    )
