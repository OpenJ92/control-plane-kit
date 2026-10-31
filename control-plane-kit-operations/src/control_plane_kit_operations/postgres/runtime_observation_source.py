"""One exact original source for fresh observation, without current-target fallback."""

from control_plane_kit_operations.execution_invocations import (
    ExecutionInvocationKind as Kind, ExecutionInvocationDisposition as Disposition,
    ExecutionInvocationConflict, ExecutionInvocationDenied, ExecutionInvocationPlanned, ExecutionInvocationControlError,
)
from control_plane_kit_core.runtime_effects import RuntimeEffectKind
from control_plane_kit_operations.prepared_runtime_invocations import prepared_runtime_invocation_profile
from control_plane_kit_operations.runtime_observation_invocations import runtime_observation_profile
from .execution import PostgresExecutionStore
from .runtime_authority_store import RuntimeAuthorityStore
from .runtime_effect_origin_store import RuntimeEffectOriginStore


def original_observation_source(connection, intent_record):
    try:
        if intent_record.intent.kind is not RuntimeEffectKind.REALIZE_ACTIVITY:
            raise ExecutionInvocationConflict()
        return _original_observation_source(connection, intent_record)
    except ExecutionInvocationControlError:
        raise
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ExecutionInvocationConflict() from None


def _original_observation_source(connection, intent_record):
    marker = PostgresExecutionStore(connection).original_final_invocation(intent_record.identity,
        original_event_id=intent_record.original_start_event.event_id,
        request_fingerprint=intent_record.request_fingerprint, kind=Kind.PREPARED_RUNTIME_DISPATCH)
    origin = RuntimeEffectOriginStore(connection).get(intent_record.identity)
    if (marker is None) == (origin is None):
        raise ExecutionInvocationConflict()
    original = marker if marker is not None else origin
    runtime = RuntimeAuthorityStore(connection).get_registration(intent_record.workspace_id,
        intent_record.intent.authority_ref, original.runtime_registration_id)
    if marker is not None:
        if marker.disposition is Disposition.PLANNED:
            raise ExecutionInvocationPlanned(marker.invocation_id)
        if marker.disposition is Disposition.DENIED:
            raise ExecutionInvocationDenied(marker.invocation_id)
        execution = prepared_runtime_invocation_profile(intent_record.intent,
            original_event_id=marker.original_event_id, runtime_authority=runtime)
        if marker.disposition is not Disposition.ADMITTED or execution.target_fingerprint != marker.target_fingerprint:
            raise ExecutionInvocationConflict()
    profile = runtime_observation_profile(original, intent_record.intent, runtime)
    return original, runtime, profile, profile.target_fingerprint
