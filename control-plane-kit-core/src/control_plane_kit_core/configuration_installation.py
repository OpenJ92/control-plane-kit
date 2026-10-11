"""Pure installation receipt syntax; provider truth and durable acceptance are external.

Realization commitments identify provider-scoped immutable resource incarnations.
Only the trusted interpreter can attest installation and prior removal; these
values neither derive provider identities nor grant execution or cleanup power.
"""
from dataclasses import dataclass
from enum import Enum
import json
import re

import rfc8785

from control_plane_kit_core.configuration_invocation import (
    ConfigurationInvocationCorrelation, configuration_invocation_selection_fingerprint,
)
from control_plane_kit_core.runtime_effect_observation import runtime_effect_result_fingerprint
from control_plane_kit_core.runtime_effects import (
    EffectResultKind, RuntimeEffectContractError, RuntimeEffectResult,
)

_PROFILE = "configuration-installation-receipt.v1"
_KEY = "configuration_installation_receipt"
_FIELDS = frozenset({"profile", "request_fingerprint", "selection_fingerprint",
    "disposition", "prior_realization_fingerprint", "current_realization_fingerprint"})
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_MAX_BYTES = 1_024
_ERROR = "configuration installation receipt is malformed"


def _require(condition: bool) -> None:
    if not condition:
        raise RuntimeEffectContractError(_ERROR)


def _digest(value: object) -> None:
    _require(type(value) is str and _DIGEST.fullmatch(value) is not None)


class ConfigurationInstallationDisposition(str, Enum):
    CREATED = "created"
    REUSED = "reused"
    REPLACED = "replaced"


@dataclass(frozen=True, slots=True, repr=False)
class ConfigurationInstallationReceipt:
    """Closed trusted assertion, not a signature, health or allocation cleanup proof."""

    request_fingerprint: str
    selection_fingerprint: str
    disposition: ConfigurationInstallationDisposition
    prior_realization_fingerprint: str | None
    current_realization_fingerprint: str

    def __post_init__(self) -> None:
        _digest(self.request_fingerprint)
        _digest(self.selection_fingerprint)
        _digest(self.current_realization_fingerprint)
        _require(type(self.disposition) is ConfigurationInstallationDisposition)
        prior = self.prior_realization_fingerprint
        if prior is not None:
            _digest(prior)
        if self.disposition is ConfigurationInstallationDisposition.CREATED:
            _require(prior is None)
        elif self.disposition is ConfigurationInstallationDisposition.REUSED:
            _require(prior == self.current_realization_fingerprint)
        else:
            _require(prior is not None and prior != self.current_realization_fingerprint)

    def descriptor(self) -> dict[str, object]:
        ConfigurationInstallationReceipt.__post_init__(self)
        return dict(profile=_PROFILE, request_fingerprint=self.request_fingerprint,
            selection_fingerprint=self.selection_fingerprint, disposition=self.disposition.value,
            prior_realization_fingerprint=self.prior_realization_fingerprint,
            current_realization_fingerprint=self.current_realization_fingerprint)


class ConfigurationInstallationReceiptCodec:
    """Exact mapping and canonical UTF-8 representation of the six-field profile."""

    def encode(self, receipt: ConfigurationInstallationReceipt) -> dict[str, object]:
        _require(type(receipt) is ConfigurationInstallationReceipt)
        return receipt.descriptor()

    def decode(self, document: object) -> ConfigurationInstallationReceipt:
        _require(type(document) is dict and document.keys() == _FIELDS)
        _require(type(document["profile"]) is str and document["profile"] == _PROFILE)
        _require(type(document["disposition"]) is str)
        try:
            return ConfigurationInstallationReceipt(
                request_fingerprint=document["request_fingerprint"],
                selection_fingerprint=document["selection_fingerprint"],
                disposition=ConfigurationInstallationDisposition(document["disposition"]),
                prior_realization_fingerprint=document["prior_realization_fingerprint"],
                current_realization_fingerprint=document["current_realization_fingerprint"])
        except (TypeError, ValueError):
            failure = RuntimeEffectContractError(_ERROR)
        raise failure

    def encode_canonical_bytes(self, receipt: ConfigurationInstallationReceipt) -> bytes:
        document = rfc8785.dumps(self.encode(receipt))
        _require(len(document) <= _MAX_BYTES)
        return document

    def decode_canonical_bytes(self, document: bytes) -> ConfigurationInstallationReceipt:
        _require(type(document) is bytes and 1 <= len(document) <= _MAX_BYTES)
        try:
            receipt = self.decode(json.loads(document.decode("utf-8")))
            # Exact equality also rejects duplicate keys and alternate JSON spellings.
            _require(self.encode_canonical_bytes(receipt) == document)
            return receipt
        except (TypeError, ValueError, RecursionError):
            failure = RuntimeEffectContractError(_ERROR)
        raise failure


def configuration_installation_receipt_for_result(
    context: ConfigurationInvocationCorrelation,
    result: RuntimeEffectResult,
) -> ConfigurationInstallationReceipt | None:
    """Read optional installation evidence against verified original-source context.

    Validate the entire context/result before absence. A present receipt requires
    success and exact effect/request/full-selection correlation. The caller owns
    source provenance; this pure reader neither verifies a provider nor rewrites
    historical unprofiled results or another evidence profile's meaning.
    """
    try:
        _require(type(context) is ConfigurationInvocationCorrelation)
        ConfigurationInvocationCorrelation.__post_init__(context)
        runtime_effect_result_fingerprint(result)
        if _KEY not in result.evidence:
            return None
        receipt = ConfigurationInstallationReceiptCodec().decode(result.evidence[_KEY])
        _require(result.kind is EffectResultKind.SUCCEEDED)
        _require(result.effect_id == context.effect_id)
        _require(receipt.request_fingerprint == context.request_fingerprint)
        _require(receipt.selection_fingerprint
            == configuration_invocation_selection_fingerprint(context.selection))
        return receipt
    except (TypeError, ValueError, AttributeError, RecursionError):
        failure = RuntimeEffectContractError(_ERROR)
    raise failure


__all__ = [
    "ConfigurationInstallationDisposition", "ConfigurationInstallationReceipt",
    "ConfigurationInstallationReceiptCodec", "configuration_installation_receipt_for_result",
]
