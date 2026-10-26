"""Pure Operations runtime secret-use roles and authorization projection."""

from dataclasses import dataclass
from control_plane_kit_core.planning import (StartNode, ReconcileNode, StopNode, RemoveNodeResource,
    StopRuntime, RemoveRuntimeResource, RemoveSocketConnection, CleanupConfigurationInstances)
from control_plane_kit_core.runtime_effects import RuntimeEffectRequest
from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_for_request, runtime_effect_intent_fingerprint
from control_plane_kit_core.secret_installation import secret_material_selection_fingerprint
from control_plane_kit_core.secret_selections import SecretVersionSelection
from control_plane_kit_core.secrets import SecretReference, SecretUseIntent
from control_plane_kit_core.verification import PostgresQueryCheck
from control_plane_kit_operations.runtime_authorities import RegisteredRuntimeAuthority, RemoteDockerTlsAuthority
from control_plane_kit_operations.secret_providers import (AuthorizeSecretUse, AuthorizeSecretVersionUse,
    secret_use_correlation_for, secret_version_use_correlation_for)
from control_plane_kit_operations.workflows import InvalidOperationCommand


@dataclass(frozen=True, slots=True)
class OperationalSecretUse:
    reference: SecretReference
    intent: SecretUseIntent

    def __post_init__(self):
        if type(self.reference) is not SecretReference or type(self.intent) is not SecretUseIntent:
            raise InvalidOperationCommand("operational secret use is malformed")
        SecretReference(self.reference.reference_id)


def _secret_use_key(use):
    if type(use) is SecretVersionSelection:
        return ("consumer-version", use.reference.reference_id, use.intent.value, use.version_id)
    if type(use) is OperationalSecretUse:
        return ("operational", use.reference.reference_id, use.intent.value)
    raise InvalidOperationCommand("runtime secret use role is malformed")


def secret_authorization_command(request, use, *, actor_subject, requested_at, actor_scopes, session_id=None):
    """Pure projection of the original request and an explicit resolution role."""
    _secret_use_key(use)
    values = dict(workspace_id=request.source.workspace_id, actor_subject=actor_subject,
        operation_id=request.source.request_id, run_id=request.source.run_id.value,
        activity_id=request.activity_id.value, effect_id=request.effect_id, session_id=session_id)
    if type(use) is SecretVersionSelection:
        values.update(selection=use,
            request_fingerprint=runtime_effect_intent_fingerprint(runtime_effect_intent_for_request(request)),
            selection_fingerprint=secret_material_selection_fingerprint(request))
        return AuthorizeSecretVersionUse(**values, correlation_id=secret_version_use_correlation_for(**values),
            requested_at=requested_at, actor_scopes=actor_scopes)
    values.update(reference=use.reference, intent=use.intent)
    return AuthorizeSecretUse(**values, correlation_id=secret_use_correlation_for(**values),
        requested_at=requested_at, actor_scopes=actor_scopes)


def required_secret_uses_for_runtime_effect(
    request: RuntimeEffectRequest,
    authority: RegisteredRuntimeAuthority | None,
) -> tuple[OperationalSecretUse | SecretVersionSelection, ...]:
    """Keep selected workload versions distinct from probe/pull/connection authority."""

    if not isinstance(request, RuntimeEffectRequest):
        raise InvalidOperationCommand(
            "secret-use enumeration requires RuntimeEffectRequest"
        )
    cleanup = type(request.operation) in (StopNode, RemoveNodeResource, StopRuntime,
        RemoveRuntimeResource, RemoveSocketConnection, CleanupConfigurationInstances)
    uses: set[OperationalSecretUse | SecretVersionSelection] = set()
    for material in request.products:
        contract = material.product.runtime_contract
        if type(request.operation) in (StartNode, ReconcileNode):
            for selection in material.secret_selections:
                if type(selection) is not SecretVersionSelection:
                    raise InvalidOperationCommand("runtime consumer version is not selected")
                uses.add(selection)
        if not cleanup and material.pull_authority is not None:
            uses.add(
                OperationalSecretUse(
                    material.pull_authority.credential_reference,
                    SecretUseIntent.OCI_PULL_CREDENTIAL,
                )
            )
        for check in (() if cleanup else contract.verification.checks):
            if (
                isinstance(check, PostgresQueryCheck)
                and check.authentication is not None
            ):
                uses.add(
                    OperationalSecretUse(
                        check.authentication.password_reference,
                        SecretUseIntent.POSTGRES_PASSWORD,
                    )
                )
    if authority is not None and isinstance(
        authority.authority,
        RemoteDockerTlsAuthority,
    ):
        uses.update(
            (
                OperationalSecretUse(
                    authority.authority.ca_certificate,
                    SecretUseIntent.DOCKER_REMOTE_TLS_CA_CERTIFICATE,
                ),
                OperationalSecretUse(
                    authority.authority.client_certificate,
                    SecretUseIntent.DOCKER_REMOTE_TLS_CLIENT_CERTIFICATE,
                ),
                OperationalSecretUse(
                    authority.authority.client_key,
                    SecretUseIntent.DOCKER_REMOTE_TLS_CLIENT_KEY,
                ),
            )
        )
    return tuple(
        sorted(
            uses,
            key=_secret_use_key,
        )
    )

