"""Closed original targets for permission-controlled ordinary verification."""

from dataclasses import dataclass
import hashlib

import rfc8785

from control_plane_kit_core.planning import NodeTarget, WaitForHealthy
from control_plane_kit_core.runtime_effects import RuntimeEffectKind, RuntimeProductMaterial
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectIntent, runtime_effect_intent_fingerprint, runtime_effect_request_for_intent,
)
from control_plane_kit_core.secrets import SecretUseIntent
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_core.verification import PostgresQueryCheck
from control_plane_kit_operations.runtime_authorities import (
    RegisteredRuntimeAuthority, LocalDockerSocketAuthority, RemoteDockerTlsAuthority,
    runtime_authority_registration_id_for,
)
from control_plane_kit_operations.runtime_secret_uses import OperationalSecretUse, _secret_use_key, secret_authorization_command
from control_plane_kit_operations.secret_providers import AuthorizedSecretUse, validate_authorized_secret_use


@dataclass(frozen=True, slots=True)
class OrdinaryVerificationCheck:
    check_id: str
    target_fingerprint: str
    credential_use: OperationalSecretUse | None

    @property
    def probe_id(self):
        return self.target_fingerprint


@dataclass(frozen=True, slots=True)
class OrdinaryVerificationInvocationProfile:
    runtime_registration_id: str
    target_fingerprint: str
    connection_uses: tuple
    checks: tuple[OrdinaryVerificationCheck, ...]

    @property
    def family_bound(self):
        return 1 + len(self.connection_uses) + len(self.checks) + sum(
            check.credential_use is not None for check in self.checks)

    def check(self, check_id):
        if type(check_id) is str:
            for check in self.checks:
                if check.check_id == check_id:
                    return check
        raise ValueError("ordinary verification check is not declared")

    @property
    def secret_roles(self):
        return tuple((None, use) for use in self.connection_uses) + tuple(
            (check, check.credential_use) for check in self.checks if check.credential_use is not None)


def ordinary_verification_authorization_command(request, role, *, actor_subject, session_id, requested_at, actor_scopes):
    check, use = role
    return secret_authorization_command(request, use, actor_subject=actor_subject,
        requested_at=requested_at, actor_scopes=actor_scopes, session_id=session_id,
        probe_id=None if check is None else check.probe_id)


def require_ordinary_verification_authorization(authorized, command):
    validate_authorized_secret_use(authorized)
    if type(authorized) is not AuthorizedSecretUse or any(
            getattr(authorized, name) != getattr(command, name) for name in (
                "workspace_id", "actor_subject", "operation_id", "session_id", "run_id",
                "activity_id", "effect_id", "correlation_id", "reference", "intent", "probe_id")):
        raise ValueError("ordinary verification authorization is incongruent")


def ordinary_verification_candidate(intent):
    return (type(intent) is RuntimeEffectIntent and intent.runtime_kind is RuntimeKind.DOCKER
        and intent.kind is RuntimeEffectKind.REALIZE_ACTIVITY
        and type(intent.operation) is WaitForHealthy)


def ordinary_verification_invocation_profile(intent, *, original_event_id, runtime_authority):
    if (not ordinary_verification_candidate(intent)
            or type(intent.operation.target) is not NodeTarget or len(intent.products) != 1
            or type(intent.products[0]) is not RuntimeProductMaterial
            or intent.products[0].node_id != intent.operation.target.node_id
            or type(runtime_authority) is not RegisteredRuntimeAuthority
            or type(runtime_authority.authority) not in (LocalDockerSocketAuthority, RemoteDockerTlsAuthority)
            or runtime_authority.workspace_id != intent.source.workspace_id
            or runtime_authority.authority_ref != intent.authority_ref
            or runtime_authority.runtime_kind is not intent.runtime_kind):
        raise ValueError("ordinary verification target is invalid")
    runtime_authority.__post_init__()
    intent.products[0].__post_init__()
    if runtime_authority.registration_id != runtime_authority_registration_id_for(
            runtime_authority.workspace_id, runtime_authority.authority_ref,
            runtime_authority.runtime_kind, runtime_authority.authority):
        raise ValueError("ordinary verification registration is incongruent")
    request = runtime_effect_request_for_intent(intent,
        effect_id=original_event_id, secret_resolution_grants=())
    target = hashlib.sha256(rfc8785.dumps(dict(
        domain="cpk.ordinary-verification-acquisition-target.v1",
        request_fingerprint=runtime_effect_intent_fingerprint(intent),
        workspace_id=intent.source.workspace_id, runtime_kind=intent.runtime_kind.value,
        runtime_id=intent.products[0].runtime_id, authority_ref=intent.authority_ref.reference_id,
        runtime_registration_id=runtime_authority.registration_id))).hexdigest()
    connection_uses = ()
    authority = runtime_authority.authority
    if type(authority) is RemoteDockerTlsAuthority:
        connection_uses = tuple(sorted((
            OperationalSecretUse(authority.ca_certificate, SecretUseIntent.DOCKER_REMOTE_TLS_CA_CERTIFICATE),
            OperationalSecretUse(authority.client_certificate, SecretUseIntent.DOCKER_REMOTE_TLS_CLIENT_CERTIFICATE),
            OperationalSecretUse(authority.client_key, SecretUseIntent.DOCKER_REMOTE_TLS_CLIENT_KEY),
        ), key=_secret_use_key))
    checks = tuple(OrdinaryVerificationCheck(check.check_id,
        hashlib.sha256(rfc8785.dumps(dict(domain="cpk.ordinary-verification-check-target.v1",
            acquisition_target=target, check_id=check.check_id))).hexdigest(),
        OperationalSecretUse(check.authentication.password_reference, SecretUseIntent.POSTGRES_PASSWORD)
            if isinstance(check, PostgresQueryCheck) and check.authentication is not None else None)
        for check in request.products[0].product.runtime_contract.verification.checks)
    return OrdinaryVerificationInvocationProfile(runtime_authority.registration_id, target, connection_uses, checks)
