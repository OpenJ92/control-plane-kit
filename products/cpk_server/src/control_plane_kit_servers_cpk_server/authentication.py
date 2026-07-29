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
class StaticDevelopmentCredentialVerifier:
    """Explicit local-development verifier; never an accept-all default."""

    expected_credential: bytes = field(repr=False, compare=False, hash=False)

    def __post_init__(self) -> None:
        credential_text = None
        if isinstance(self.expected_credential, bytes):
            try:
                credential_text = self.expected_credential.decode("ascii")
            except UnicodeDecodeError:
                credential_text = None
        if (
            not isinstance(self.expected_credential, bytes)
            or not self.expected_credential
            or len(self.expected_credential) > MAXIMUM_BEARER_CREDENTIAL_BYTES
            or credential_text is None
            or any(character.isspace() for character in credential_text)
        ):
            raise CredentialAuthenticationError()

    def authenticate(self, credential: bytes) -> AuthenticatedPrincipal:
        if not hmac.compare_digest(credential, self.expected_credential):
            raise CredentialAuthenticationError()
        return AuthenticatedPrincipal(
            PrincipalIdentity(
                issuer="urn:control-plane-kit:static-development",
                subject_id="local-development-operator",
                kind=PrincipalKind.OPERATOR,
            )
        )
