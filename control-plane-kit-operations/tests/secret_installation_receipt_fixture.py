"""Synthetic installation assertions for recorded test premises, not provider proof."""
from dataclasses import replace
from control_plane_kit_core.planning import StartNode
from control_plane_kit_core.runtime_effect_observation import (
    runtime_effect_intent_fingerprint, runtime_effect_intent_for_request,
    runtime_effect_request_for_intent, RuntimeEffectObservationEvidence,
)
from control_plane_kit_core.runtime_effects import RuntimeEffectKind, RuntimeEffectResult
from control_plane_kit_core.secret_installation import (
    SecretInstallationDisposition, SecretInstallationReceipt, SecretResolutionReceipt,
    secret_material_selection_fingerprint,
    secret_installation_receipt_for_result, secret_installation_receipt_for_observation,
)


def installation_evidence(request):
    # This fixture makes only ordinary start premises, never managed replacement
    # or configured-installation assertions.
    assert type(request.operation) is StartNode
    assert request.kind is RuntimeEffectKind.REALIZE_ACTIVITY
    fingerprint = runtime_effect_intent_fingerprint(runtime_effect_intent_for_request(request))
    selection = secret_material_selection_fingerprint(request)
    resolution = SecretResolutionReceipt(fingerprint, selection)
    installation = SecretInstallationReceipt(fingerprint, selection,
        SecretInstallationDisposition.CREATED, None, "a" * 64)
    return {
        "secret_resolution_receipt": resolution.descriptor(),
        "secret_installation_receipt": installation.descriptor(),
    }


def selected_start_success(intent, value):
    """Enrich an explicitly selected start success, preserving its actual variant."""
    request = runtime_effect_request_for_intent(intent, effect_id=value.effect_id)
    if type(value) is RuntimeEffectResult:
        result = replace(value, evidence={**value.evidence, **installation_evidence(request)})
        assert secret_installation_receipt_for_result(request, result) is not None
    else:
        result = replace(value, evidence=RuntimeEffectObservationEvidence({
            **value.evidence.descriptor(), **installation_evidence(request)}))
        assert secret_installation_receipt_for_observation(request, result) is not None
    return result
