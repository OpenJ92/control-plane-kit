"""Closed operational/consumer authority values; no provider or persistence IO."""
from dataclasses import dataclass, fields
from hashlib import sha256
import json
import re

import rfc8785

from control_plane_kit_core.secret_selections import SecretVersionSelection, secret_selection_from_descriptor
from control_plane_kit_core.secrets import (
    SecretEnvironmentDelivery, SecretFileDelivery, SecretProviderEndpointReference,
    SecretReference, SecretResolutionGrant, SecretUseIntent, secret_delivery_sort_key,
    _validate_grant_identifier, _validate_run_identifier, _validate_activity_identifier,
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_OPERATIONAL = "secret-operational-resolution-grant.v1"
_CONSUMER = "secret-version-resolution-grant.v1"
_MAX_BYTES = 32_768


def _require(valid):
    if not valid:
        raise ValueError("secret resolution grant is malformed or incomplete")


@dataclass(frozen=True, slots=True, repr=False)
class SecretVersionResolutionGrant:
    """Committed authority for one selected version in one exact consuming effect."""

    authorization_id: str
    workspace_id: str
    reference_registration_id: str
    provider_registration_id: str
    endpoint_reference: SecretProviderEndpointReference
    credential_reference: SecretReference
    selection: SecretVersionSelection
    actor_subject: str
    correlation_id: str
    intent_fingerprint: str
    operation_id: str
    run_id: str
    activity_id: str
    effect_id: str
    request_fingerprint: str
    selection_fingerprint: str
    session_id: str | None = None

    def __post_init__(self):
        for name in ("authorization_id", "workspace_id", "reference_registration_id",
                     "provider_registration_id", "actor_subject", "correlation_id",
                     "operation_id", "effect_id"):
            value = getattr(self, name)
            _require(type(value) is str)
            _validate_grant_identifier(value, name)
        _require(type(self.run_id) is str and type(self.activity_id) is str)
        _validate_run_identifier(self.run_id)
        _validate_activity_identifier(self.activity_id)
        if self.session_id is not None:
            _require(type(self.session_id) is str)
            _validate_grant_identifier(self.session_id, "session_id")
        _require(type(self.endpoint_reference) is SecretProviderEndpointReference)
        _require(type(self.credential_reference) is SecretReference)
        SecretProviderEndpointReference(self.endpoint_reference.reference_id)
        SecretReference(self.credential_reference.reference_id)
        _require(type(self.selection) is SecretVersionSelection)
        SecretVersionSelection.__post_init__(self.selection)
        for value in (self.intent_fingerprint, self.request_fingerprint, self.selection_fingerprint):
            _require(type(value) is str and _SHA256.fullmatch(value) is not None)

    def descriptor(self):
        return SecretResolutionGrantCodec().encode(self)


ResolutionGrant = SecretResolutionGrant | SecretVersionResolutionGrant


def validate_resolution_grant(grant):
    _require(type(grant) in (SecretResolutionGrant, SecretVersionResolutionGrant))
    type(grant).__post_init__(grant)
    _require(type(grant.endpoint_reference) is SecretProviderEndpointReference)
    _require(type(grant.credential_reference) is SecretReference)
    if type(grant) is SecretResolutionGrant:
        _require(type(grant.reference) is SecretReference and type(grant.intent) is SecretUseIntent)


def resolution_grant_key(grant):
    validate_resolution_grant(grant)
    if type(grant) is SecretResolutionGrant:
        return ("operational", grant.reference.reference_id, grant.intent.value)
    return ("consumer-version", grant.selection.reference.reference_id,
        grant.selection.intent.value, grant.selection.version_id)


class SecretResolutionGrantCodec:
    """One bounded canonical codec for the two exact, non-interchangeable roles."""

    def encode(self, grant):
        validate_resolution_grant(grant)
        operational = type(grant) is SecretResolutionGrant
        document = {item.name: getattr(grant, item.name) for item in fields(grant)}
        document["endpoint_reference"] = grant.endpoint_reference.reference_id
        document["credential_reference"] = grant.credential_reference.reference_id
        if operational:
            document["reference_id"] = document.pop("reference").reference_id
            document["intent"] = grant.intent.value
        else:
            document["selection"] = grant.selection.descriptor()
        return {"profile": _OPERATIONAL if operational else _CONSUMER, **document}

    def decode(self, document):
        result = None
        try:
            _require(type(document) is dict)
            profile = document.get("profile")
            _require(type(profile) is str and profile in (_OPERATIONAL, _CONSUMER))
            value_type = SecretResolutionGrant if profile == _OPERATIONAL else SecretVersionResolutionGrant
            expected = {item.name for item in fields(value_type)} | {"profile"}
            if value_type is SecretResolutionGrant:
                expected = (expected - {"reference"}) | {"reference_id"}
            _require(set(document) == expected)
            values = {key: value for key, value in document.items() if key != "profile"}
            for name, value in values.items():
                if name != "selection":
                    _require(value is None or type(value) is str)
            values["endpoint_reference"] = SecretProviderEndpointReference(values["endpoint_reference"])
            values["credential_reference"] = SecretReference(values["credential_reference"])
            if value_type is SecretResolutionGrant:
                values["reference"] = SecretReference(values.pop("reference_id"))
                values["intent"] = SecretUseIntent(values["intent"])
            else:
                values["selection"] = secret_selection_from_descriptor(values["selection"])
            result = value_type(**values)
            validate_resolution_grant(result)
            _require(len(rfc8785.dumps(self.encode(result))) <= _MAX_BYTES)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            result = None
        _require(result is not None)
        return result

    def encode_canonical_bytes(self, grant):
        encoded = rfc8785.dumps(self.encode(grant))
        _require(len(encoded) <= _MAX_BYTES)
        return encoded

    def decode_canonical_bytes(self, encoded):
        result = None
        try:
            _require(type(encoded) is bytes and 0 < len(encoded) <= _MAX_BYTES)
            result = self.decode(json.loads(encoded.decode("utf-8")))
            _require(self.encode_canonical_bytes(result) == encoded)
        except (ValueError, TypeError, RecursionError):
            result = None
        _require(result is not None)
        return result


def secret_material_commitment(workspace_id, material):
    """Commit already-validated public material without re-entering request validation."""
    return _secret_material_components_commitment(workspace_id, material.runtime_id, material.node_id,
        material.product.runtime_contract.secret_deliveries, material.secret_selections)


def _secret_material_components_commitment(workspace_id, runtime_id, node_id, deliveries, selections):
    """One unchanged commitment for admitted full material or original correlation."""
    descriptor = dict(workspace_id=workspace_id, runtime_id=runtime_id, node_id=node_id,
        deliveries=[value.descriptor() for value in sorted(deliveries,
            key=secret_delivery_sort_key) if type(value) in (SecretEnvironmentDelivery, SecretFileDelivery)],
        selections=[value.descriptor() for value in selections])
    return _commitment(b"control-plane-kit.secret-material-selection.v1\0", descriptor)


def request_intent_commitment(descriptor):
    """Project validated request descriptor bytes to the unchanged intent domain."""
    descriptor = dict(descriptor)
    descriptor.pop("effect_id")
    descriptor["source"] = {key: value for key, value in descriptor["source"].items()
        if key != "intent_event_id"}
    return _commitment(b"control-plane-kit.runtime-effect-intent.v1\0", descriptor)


def _commitment(domain, descriptor):
    canonical = rfc8785.dumps(descriptor)
    _require(len(canonical) <= 1_048_576)
    return sha256(domain + canonical).hexdigest()
