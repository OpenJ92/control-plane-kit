"""Bounded version evidence syntax; only interpreters establish recipient truth."""
from dataclasses import dataclass
import json
import re

import rfc8785

from control_plane_kit_core.configuration_installation import (
    ConfigurationInstallationDisposition, ConfigurationInstallationReceipt,
    ConfigurationInstallationReceiptCodec, _configuration_receipt_for_correlation,
)
from control_plane_kit_core.configuration_invocation import configuration_invocation_correlation_for_request
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectObservationEvidence, RuntimeEffectObservationResult, RuntimeEffectObservedSucceeded,
    runtime_effect_intent_for_request, runtime_effect_intent_fingerprint, runtime_effect_result_fingerprint,
    runtime_effect_observation_fingerprint,
)
from control_plane_kit_core.runtime_effects import (
    RuntimeEffectRequest, RuntimeEffectContractError, RuntimeEffectKind, EffectResultKind,
)
from control_plane_kit_core.planning import StartNode, ReconcileNode
from control_plane_kit_core.secret_version_grants import secret_material_commitment

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_RESOLUTION = "secret_resolution_receipt"
_INSTALLATION = "secret_installation_receipt"
_CONFIGURATION = "configuration_installation_receipt"
_RESOLUTION_PROFILE = "secret-resolution-receipt.v1"
_INSTALLATION_PROFILE = "secret-installation-receipt.v1"
_MAX_BYTES = 1024
SecretInstallationDisposition = ConfigurationInstallationDisposition


def _require(condition):
    if not condition:
        raise RuntimeEffectContractError("secret installation evidence is malformed or incomplete")


@dataclass(frozen=True, slots=True, repr=False)
class SecretResolutionReceipt:
    request_fingerprint: str
    selection_fingerprint: str

    def __post_init__(self):
        for value in (self.request_fingerprint, self.selection_fingerprint):
            _require(type(value) is str and _DIGEST.fullmatch(value) is not None)

    def descriptor(self):
        SecretResolutionReceipt.__post_init__(self)
        return dict(profile=_RESOLUTION_PROFILE, request_fingerprint=self.request_fingerprint,
                    selection_fingerprint=self.selection_fingerprint)


@dataclass(frozen=True, slots=True, repr=False)
class SecretInstallationReceipt(ConfigurationInstallationReceipt):
    """Same incarnation laws, a distinct assertion of private secret recipient checks."""

    def descriptor(self):
        return {**ConfigurationInstallationReceipt.descriptor(self), "profile": _INSTALLATION_PROFILE}


class _ReceiptCodec:
    value_type = SecretResolutionReceipt
    profile = _RESOLUTION_PROFILE
    fields = {"profile", "request_fingerprint", "selection_fingerprint"}

    def encode(self, value):
        _require(type(value) is self.value_type)
        return value.descriptor()

    def decode(self, document):
        value = None
        try:
            _require(type(document) is dict and set(document) == self.fields)
            _require(type(document["profile"]) is str and document["profile"] == self.profile)
            arguments = {key: val for key, val in document.items() if key != "profile"}
            if self.value_type is SecretInstallationReceipt:
                _require(type(arguments["disposition"]) is str)
                arguments["disposition"] = SecretInstallationDisposition(arguments["disposition"])
            value = self.value_type(**arguments)
        except (ValueError, TypeError, AttributeError, RecursionError):
            value = None
        _require(value is not None)
        return value

    def encode_canonical_bytes(self, value):
        document = rfc8785.dumps(self.encode(value))
        _require(len(document) <= _MAX_BYTES)
        return document

    def decode_canonical_bytes(self, document):
        value = None
        try:
            _require(type(document) is bytes and 1 <= len(document) <= _MAX_BYTES)
            value = self.decode(json.loads(document.decode("utf-8")))
            _require(self.encode_canonical_bytes(value) == document)
        except (ValueError, TypeError, RecursionError):
            value = None
        _require(value is not None)
        return value


class SecretResolutionReceiptCodec(_ReceiptCodec):
    """Exact canonical resolution profile, without recipient claims."""


class SecretInstallationReceiptCodec(_ReceiptCodec):
    value_type = SecretInstallationReceipt
    profile = _INSTALLATION_PROFILE
    fields = _ReceiptCodec.fields | {"disposition", "prior_realization_fingerprint", "current_realization_fingerprint"}


def secret_material_selection_fingerprint(request):
    """Commit public effective slots and versions, never a digest of secret bytes."""
    _require(type(request) is RuntimeEffectRequest)
    RuntimeEffectRequest.__post_init__(request)
    _require(type(request.operation) in (StartNode, ReconcileNode))
    return secret_material_commitment(request.source.workspace_id, request.products[0])


def _receipt_from_evidence(request, evidence, *, succeeded):
    """Validate closed claims after the caller admits its actual envelope."""
    request_fingerprint = runtime_effect_intent_fingerprint(runtime_effect_intent_for_request(request))
    required = (type(request.operation) in (StartNode, ReconcileNode)
                and bool(request.products[0].secret_selections))
    present = _RESOLUTION in evidence or _INSTALLATION in evidence
    if not succeeded:
        _require(not present and not (required and _CONFIGURATION in evidence))
        return None
    if not required and not present:
        return None
    _require(required and _RESOLUTION in evidence and _INSTALLATION in evidence)
    resolution = SecretResolutionReceiptCodec().decode(evidence[_RESOLUTION])
    receipt = SecretInstallationReceiptCodec().decode(evidence[_INSTALLATION])
    commitment = secret_material_selection_fingerprint(request)
    for value in (resolution, receipt):
        _require(value.request_fingerprint == request_fingerprint and value.selection_fingerprint == commitment)
    if request.kind is RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1:
        _require(_CONFIGURATION in evidence)
    if _CONFIGURATION in evidence:
        _require(request.kind is RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1)
        configuration = _configuration_receipt_for_correlation(
            configuration_invocation_correlation_for_request(request),
            ConfigurationInstallationReceiptCodec().decode(evidence[_CONFIGURATION]))
        _require((configuration.disposition, configuration.prior_realization_fingerprint,
            configuration.current_realization_fingerprint) == (
            receipt.disposition, receipt.prior_realization_fingerprint, receipt.current_realization_fingerprint))
    return receipt


def secret_installation_receipt_for_result(request, result):
    """Read complete current evidence; failure/uncertainty remains without proof."""
    receipt = None
    valid = False
    try:
        _require(type(request) is RuntimeEffectRequest)
        RuntimeEffectRequest.__post_init__(request)
        runtime_effect_result_fingerprint(result)
        _require(result.effect_id == request.effect_id)
        receipt = _receipt_from_evidence(request, result.evidence,
            succeeded=result.kind is EffectResultKind.SUCCEEDED)
        valid = True
    except (ValueError, TypeError, AttributeError, IndexError, RecursionError):
        valid = False
    _require(valid)
    return receipt


def secret_installation_receipt_for_observation(
    request: RuntimeEffectRequest,
    observation: RuntimeEffectObservationResult,
) -> SecretInstallationReceipt | None:
    """Read the actual bounded observation against the original request."""
    receipt = None
    valid = False
    try:
        _require(type(request) is RuntimeEffectRequest)
        RuntimeEffectRequest.__post_init__(request)
        # The fingerprint owner first rejects non-exact observation variants.
        runtime_effect_observation_fingerprint(observation)
        type(observation).__post_init__(observation)
        if observation.failure is not None:
            type(observation.failure).__post_init__(observation.failure)
        _require(observation.effect_id == request.effect_id)
        _require(observation.request_fingerprint
            == runtime_effect_intent_fingerprint(runtime_effect_intent_for_request(request)))
        # Revalidate a detached evidence value; never mutate retained evidence.
        evidence = RuntimeEffectObservationEvidence(observation.evidence.descriptor()).descriptor()
        receipt = _receipt_from_evidence(request, evidence,
            succeeded=type(observation) is RuntimeEffectObservedSucceeded)
        valid = True
    except (ValueError, TypeError, AttributeError, IndexError, RecursionError):
        valid = False
    _require(valid)
    return receipt


__all__ = ["SecretResolutionReceipt", "SecretResolutionReceiptCodec", "SecretInstallationReceipt",
           "SecretInstallationReceiptCodec", "SecretInstallationDisposition",
           "secret_material_selection_fingerprint", "secret_installation_receipt_for_result",
           "secret_installation_receipt_for_observation"]
