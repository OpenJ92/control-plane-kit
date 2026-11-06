"""Observation targets preserve original identities and consume only connection roles."""

from dataclasses import dataclass
import hashlib

import rfc8785

from control_plane_kit_core.secrets import SecretUseIntent
from control_plane_kit_operations.execution_invocations import ExecutionInvocationRecord
from control_plane_kit_operations.prepared_runtime_invocations import (
    prepared_runtime_observation_supported, prepared_runtime_reconciliation_target_fingerprint,
)
from control_plane_kit_operations.runtime_authorities import RemoteDockerTlsAuthority
from control_plane_kit_operations.runtime_effect_origins import (
    RuntimeEffectOriginRecord, runtime_effect_origin_fingerprint, original_runtime_observation_supported,
)
from control_plane_kit_operations.runtime_secret_uses import OperationalSecretUse, _secret_use_key


@dataclass(frozen=True, slots=True)
class RuntimeObservationInvocationProfile:
    runtime_registration_id: str
    required_uses: tuple
    target_fingerprint: str
    requires_original_qualification: bool


def observation_secret_uses(runtime):
    if type(runtime.authority) is not RemoteDockerTlsAuthority:
        return ()
    authority = runtime.authority
    return tuple(sorted((OperationalSecretUse(authority.ca_certificate, SecretUseIntent.DOCKER_REMOTE_TLS_CA_CERTIFICATE),
        OperationalSecretUse(authority.client_certificate, SecretUseIntent.DOCKER_REMOTE_TLS_CLIENT_CERTIFICATE),
        OperationalSecretUse(authority.client_key, SecretUseIntent.DOCKER_REMOTE_TLS_CLIENT_KEY)), key=_secret_use_key))


def runtime_observation_profile(original, intent, runtime):
    if not original_runtime_observation_supported(intent, runtime):
        raise ValueError("original runtime observation is unsupported")
    old = type(original) is ExecutionInvocationRecord and prepared_runtime_observation_supported(intent)
    if old:
        target = prepared_runtime_reconciliation_target_fingerprint(original_invocation_id=original.invocation_id,
            original_target_fingerprint=original.target_fingerprint,
            original_request_fingerprint=original.request_fingerprint,
            runtime_registration_id=original.runtime_registration_id)
    else:
        source = dict(original_request_fingerprint=original.request_fingerprint,
            runtime_registration_id=original.runtime_registration_id)
        if type(original) is ExecutionInvocationRecord:
            source.update(source_kind="prepared-runtime-dispatch", original_invocation_id=original.invocation_id,
                original_target_fingerprint=original.target_fingerprint)
        elif type(original) is RuntimeEffectOriginRecord:
            source.update(source_kind="runtime-effect-origin",
                origin_fingerprint=runtime_effect_origin_fingerprint(original, intent, runtime))
        else:
            raise ValueError("original runtime observation source is invalid")
        target = hashlib.sha256(rfc8785.dumps(dict(domain="cpk.original-runtime-observation-target.v1",
            source=source))).hexdigest()
    return RuntimeObservationInvocationProfile(runtime.registration_id, observation_secret_uses(runtime), target, not old)
