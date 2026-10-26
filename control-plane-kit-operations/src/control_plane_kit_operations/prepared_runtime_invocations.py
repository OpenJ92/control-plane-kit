"""Pure, closed original-target profile for prepared runtime invocation history."""

from dataclasses import dataclass
import hashlib
import re
import rfc8785

from control_plane_kit_core.planning import (
    NodeTarget, ReconcileRuntime, RemoveNodeResource, RemoveRuntimeResource,
    RuntimeTarget, StartRuntime, StopNode, StopRuntime,
)
from control_plane_kit_core.runtime_effects import RuntimeEffectKind, RuntimeProductMaterial
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectIntent, runtime_effect_intent_fingerprint, runtime_effect_request_for_intent,
)
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.runtime_authorities import (
    RegisteredRuntimeAuthority, RemoteDockerTlsAuthority, runtime_authority_registration_id_for,
)
from control_plane_kit_operations.runtime_secret_uses import required_secret_uses_for_runtime_effect, _secret_use_key


@dataclass(frozen=True, slots=True)
class PreparedRuntimeInvocationProfile:
    runtime_registration_id: str
    required_uses: tuple
    target_fingerprint: str


def runtime_preparation_candidate(intent):
    return (type(intent) is RuntimeEffectIntent and intent.kind is RuntimeEffectKind.REALIZE_ACTIVITY
        and type(intent.operation) in (StartRuntime, ReconcileRuntime, StopRuntime,
            RemoveRuntimeResource, StopNode, RemoveNodeResource) and intent.runtime_kind is RuntimeKind.DOCKER
        and intent.authority_ref is not None)


def prepared_runtime_observation_supported(intent):
    """Closed selected-observer support; this grants no observation authority."""
    return runtime_preparation_candidate(intent) and type(intent.operation) is not StopRuntime


def prepared_runtime_invocation_profile(intent, *, original_event_id, runtime_authority):
    if (not runtime_preparation_candidate(intent)
            or type(runtime_authority) is not RegisteredRuntimeAuthority
            or type(runtime_authority.authority) is not RemoteDockerTlsAuthority
            or runtime_authority.workspace_id != intent.source.workspace_id
            or runtime_authority.authority_ref != intent.authority_ref
            or runtime_authority.runtime_kind is not intent.runtime_kind):
        raise ValueError("prepared runtime target is invalid")
    runtime_authority.__post_init__()
    if runtime_authority.registration_id != runtime_authority_registration_id_for(
            runtime_authority.workspace_id, runtime_authority.authority_ref,
            runtime_authority.runtime_kind, runtime_authority.authority):
        raise ValueError("prepared runtime registration is incongruent")
    if type(intent.operation) in (StopNode, RemoveNodeResource):
        if (type(intent.operation.target) is not NodeTarget or len(intent.products) != 1
                or type(intent.products[0]) is not RuntimeProductMaterial):
            raise ValueError("prepared runtime node material is invalid")
        material = intent.products[0]
        material.__post_init__()
        if material.node_id != intent.operation.target.node_id:
            raise ValueError("prepared runtime node material is incongruent")
        runtime_id = material.runtime_id
    else:
        # Preserve historical StartRuntime acceptance and its exact target recipe.
        if type(intent.operation) is not StartRuntime and (
                type(intent.operation.target) is not RuntimeTarget or intent.products):
            raise ValueError("prepared runtime material is invalid")
        runtime_id = intent.operation.target.runtime_id
    request = runtime_effect_request_for_intent(intent,
        effect_id=original_event_id, secret_resolution_grants=())
    uses = required_secret_uses_for_runtime_effect(request, runtime_authority)
    if not uses:
        raise ValueError("prepared runtime target has no preparation")
    target = dict(domain="cpk.prepared-runtime-final-target.v1", kind="prepared-runtime-dispatch",
        request_fingerprint=runtime_effect_intent_fingerprint(intent), workspace_id=intent.source.workspace_id,
        runtime_id=runtime_id, runtime_kind=intent.runtime_kind.value,
        authority_ref=intent.authority_ref.reference_id, runtime_registration_id=runtime_authority.registration_id,
        required_uses=[list(_secret_use_key(use)) for use in uses])
    return PreparedRuntimeInvocationProfile(runtime_authority.registration_id, uses,
        hashlib.sha256(rfc8785.dumps(target)).hexdigest())


def prepared_runtime_reconciliation_target_fingerprint(*, original_invocation_id,
        original_target_fingerprint, original_request_fingerprint, runtime_registration_id):
    if (any(type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in (original_invocation_id, original_target_fingerprint, original_request_fingerprint))
            or type(runtime_registration_id) is not str or not 1 <= len(runtime_registration_id) <= 512
            or any(ord(character) < 32 for character in runtime_registration_id)):
        raise ValueError("prepared reconciliation target is invalid")
    target = dict(domain="cpk.prepared-runtime-reconciliation-target.v1", kind="reconciliation-observation",
        original_invocation_id=original_invocation_id, original_target_fingerprint=original_target_fingerprint,
        original_request_fingerprint=original_request_fingerprint, runtime_registration_id=runtime_registration_id)
    return hashlib.sha256(rfc8785.dumps(target)).hexdigest()
