"""Bounded version evidence syntax; only interpreters establish recipient truth."""
from dataclasses import dataclass
import json
import re

import rfc8785

from control_plane_kit_core.configuration_installation import (
    ConfigurationInstallationDisposition, ConfigurationInstallationReceipt,
    ConfigurationInstallationReceiptCodec, _configuration_receipt_for_correlation,
)
from control_plane_kit_core.configuration_instances import ConfigurationInstanceSelection
from control_plane_kit_core.configuration_invocation import ConfigurationInvocationCorrelation
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectIntentSource, RuntimeEffectObservationEvidence, RuntimeEffectObservationResult,
    RuntimeEffectObservedSucceeded, _exact_text,
    runtime_effect_intent_for_request, runtime_effect_intent_fingerprint, runtime_effect_result_fingerprint,
    runtime_effect_observation_fingerprint,
)
from control_plane_kit_core.runtime_effects import (
    RuntimeEffectRequest, RuntimeEffectContractError, RuntimeEffectKind, EffectResultKind,
)
from control_plane_kit_core.planning import NodeTarget, StartNode, ReconcileNode
from control_plane_kit_core.secret_selections import SecretVersionSelection, normalize_secret_selections
from control_plane_kit_core.secrets import (
    SecretDelivery, SecretEnvironmentDelivery, SecretReferenceEnvironmentDelivery, SecretFileDelivery,
    secret_delivery_from_descriptor,
)
from control_plane_kit_core.secret_version_grants import (
    secret_material_commitment, _secret_material_components_commitment,
)

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
class SecretInstallationCorrelation:
    """Supplied original context; neither origin evidence nor execution authority."""

    request_fingerprint: str
    effect_id: str
    kind: RuntimeEffectKind
    source: RuntimeEffectIntentSource
    operation: StartNode | ReconcileNode
    runtime_id: str
    node_id: str
    secret_deliveries: tuple[SecretDelivery, ...]
    secret_selections: tuple[SecretVersionSelection, ...]
    configuration_instances: ConfigurationInstanceSelection | None = None

    def __post_init__(self):
        valid = False
        try:
            _require(type(self.request_fingerprint) is str and _DIGEST.fullmatch(self.request_fingerprint) is not None)
            for value in (self.effect_id, self.runtime_id, self.node_id):
                _exact_text(value, "secret installation identity")
                _require(bool(value.strip()))
            _require(type(self.kind) is RuntimeEffectKind and self.kind in (
                RuntimeEffectKind.REALIZE_ACTIVITY, RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1))
            _require(type(self.source) is RuntimeEffectIntentSource)
            RuntimeEffectIntentSource.__post_init__(self.source)
            type(self.source.run_id).__post_init__(self.source.run_id)
            _require(type(self.operation) in (StartNode, ReconcileNode)
                and type(self.operation.target) is NodeTarget
                and type(self.operation.target.node_id) is str
                and self.operation.target.node_id == self.node_id)
            _require(type(self.secret_deliveries) is tuple)
            for delivery in self.secret_deliveries:
                _require(type(delivery) in (SecretEnvironmentDelivery, SecretReferenceEnvironmentDelivery, SecretFileDelivery))
                # Decode every closed variant, including nonresolving references,
                # without repairing nested values on the supplied correlation.
                _require(secret_delivery_from_descriptor(delivery.descriptor()) == delivery)
            _require(type(self.secret_selections) is tuple and all(
                type(value) is SecretVersionSelection for value in self.secret_selections))
            _require(normalize_secret_selections(self.secret_deliveries, self.secret_selections) == self.secret_selections)
            if self.kind is RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1:
                _require(type(self.configuration_instances) is ConfigurationInstanceSelection)
                selection = ConfigurationInstanceSelection(self.configuration_instances.instances)
                _require(selection == self.configuration_instances)
                context = _configuration_context(self, selection)
                _require(all(ref.runtime_id == self.runtime_id and ref.node_id == self.node_id
                    and ref.workspace_id == self.source.workspace_id for ref in context.selection.instances))
            else:
                _require(self.configuration_instances is None)
            valid = True
        except (ValueError, TypeError, AttributeError, KeyError, IndexError, RecursionError):
            valid = False
        # Do not retain a decoder exception containing candidate material.
        _require(valid)


def _configuration_context(context, selection=None):
    # The existing selection codec revalidates by normalizing its input. Give
    # it a detached selection so receipt reads never mutate retained context.
    if selection is None:
        selection = ConfigurationInstanceSelection(context.configuration_instances.instances)
    return ConfigurationInvocationCorrelation(context.request_fingerprint, context.effect_id,
        context.kind, context.source, context.operation, selection)


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


def _request_correlation(request):
    """Admit the whole real request, preserving the non-node intent boundary."""
    _require(type(request) is RuntimeEffectRequest)
    RuntimeEffectRequest.__post_init__(request)
    intent = runtime_effect_intent_for_request(request)
    fingerprint = runtime_effect_intent_fingerprint(intent)
    if type(request.operation) not in (StartNode, ReconcileNode):
        return None, fingerprint
    material = request.products[0]
    return SecretInstallationCorrelation(fingerprint, request.effect_id, request.kind, intent.source,
        request.operation, material.runtime_id, material.node_id,
        material.product.runtime_contract.secret_deliveries, material.secret_selections,
        request.configuration_instances), fingerprint


def secret_installation_correlation_for_request(request):
    """Project a validated real node request without inventing missing material."""
    context = None
    try:
        context, _ = _request_correlation(request)
    except (ValueError, TypeError, AttributeError, KeyError, IndexError, RecursionError):
        context = None
    _require(context is not None)
    return context


def _receipt_from_evidence(context, evidence, *, succeeded):
    """Validate closed claims after the caller admits its actual envelope."""
    required = context is not None and bool(context.secret_selections)
    present = _RESOLUTION in evidence or _INSTALLATION in evidence
    if not succeeded:
        _require(not present and not (required and _CONFIGURATION in evidence))
        return None
    if not required and not present:
        return None
    _require(required and _RESOLUTION in evidence and _INSTALLATION in evidence)
    resolution = SecretResolutionReceiptCodec().decode(evidence[_RESOLUTION])
    receipt = SecretInstallationReceiptCodec().decode(evidence[_INSTALLATION])
    commitment = _secret_material_components_commitment(context.source.workspace_id,
        context.runtime_id, context.node_id, context.secret_deliveries, context.secret_selections)
    for value in (resolution, receipt):
        _require(value.request_fingerprint == context.request_fingerprint and value.selection_fingerprint == commitment)
    if context.kind is RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1:
        _require(_CONFIGURATION in evidence)
    if _CONFIGURATION in evidence:
        _require(context.kind is RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1)
        configuration = _configuration_receipt_for_correlation(
            _configuration_context(context),
            ConfigurationInstallationReceiptCodec().decode(evidence[_CONFIGURATION]))
        _require((configuration.disposition, configuration.prior_realization_fingerprint,
            configuration.current_realization_fingerprint) == (
            receipt.disposition, receipt.prior_realization_fingerprint, receipt.current_realization_fingerprint))
    return receipt


def _result_evidence(effect_id, result):
    runtime_effect_result_fingerprint(result)
    _require(result.effect_id == effect_id)
    return result.evidence


def _observation_evidence(effect_id, fingerprint, observation):
    # The fingerprint owner first rejects non-exact observation variants.
    runtime_effect_observation_fingerprint(observation)
    type(observation).__post_init__(observation)
    if observation.failure is not None:
        type(observation.failure).__post_init__(observation.failure)
    _require(observation.effect_id == effect_id and observation.request_fingerprint == fingerprint)
    return RuntimeEffectObservationEvidence(observation.evidence.descriptor()).descriptor()


def secret_installation_receipt_for_correlated_result(context, result):
    """Interpret an actual result; the caller establishes original provenance."""
    receipt = None
    valid = False
    try:
        _require(type(context) is SecretInstallationCorrelation)
        SecretInstallationCorrelation.__post_init__(context)
        evidence = _result_evidence(context.effect_id, result)
        receipt = _receipt_from_evidence(context, evidence, succeeded=result.kind is EffectResultKind.SUCCEEDED)
        valid = True
    except (ValueError, TypeError, AttributeError, KeyError, IndexError, RecursionError):
        valid = False
    _require(valid)
    return receipt


def secret_installation_receipt_for_correlated_observation(context, observation):
    """Interpret the actual observed variant, never a synthetic direct result."""
    receipt = None
    valid = False
    try:
        _require(type(context) is SecretInstallationCorrelation)
        SecretInstallationCorrelation.__post_init__(context)
        evidence = _observation_evidence(context.effect_id, context.request_fingerprint, observation)
        receipt = _receipt_from_evidence(context, evidence, succeeded=type(observation) is RuntimeEffectObservedSucceeded)
        valid = True
    except (ValueError, TypeError, AttributeError, KeyError, IndexError, RecursionError):
        valid = False
    _require(valid)
    return receipt


def secret_installation_receipt_for_result(request, result):
    """Read complete current evidence; failure/uncertainty remains without proof."""
    receipt = None
    valid = False
    try:
        context, _ = _request_correlation(request)
        evidence = _result_evidence(request.effect_id, result)
        receipt = _receipt_from_evidence(context, evidence,
            succeeded=result.kind is EffectResultKind.SUCCEEDED)
        valid = True
    except (ValueError, TypeError, AttributeError, KeyError, IndexError, RecursionError):
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
        context, fingerprint = _request_correlation(request)
        evidence = _observation_evidence(request.effect_id, fingerprint, observation)
        receipt = _receipt_from_evidence(context, evidence,
            succeeded=type(observation) is RuntimeEffectObservedSucceeded)
        valid = True
    except (ValueError, TypeError, AttributeError, KeyError, IndexError, RecursionError):
        valid = False
    _require(valid)
    return receipt


__all__ = ["SecretResolutionReceipt", "SecretResolutionReceiptCodec", "SecretInstallationReceipt",
           "SecretInstallationReceiptCodec", "SecretInstallationDisposition",
           "secret_material_selection_fingerprint", "secret_installation_receipt_for_result",
           "secret_installation_receipt_for_observation", "SecretInstallationCorrelation",
           "secret_installation_correlation_for_request", "secret_installation_receipt_for_correlated_result",
           "secret_installation_receipt_for_correlated_observation"]
