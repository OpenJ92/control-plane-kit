"""Pure completion syntax and correlation for one configuration invocation.

These values grant no authority and prove neither provider completion nor
non-use. Readers must supply correlation from verified original source; a real
interpreter may attest completion only after all its invoked mutations finish.
"""

from dataclasses import dataclass
from hashlib import sha256
import re

from control_plane_kit_core.configuration_instances import (
    ConfigurationInstanceSelection, ConfigurationInstanceSelectionCodec,
)
from control_plane_kit_core.planning import NodeTarget, ReconcileNode, StartNode
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectIntentSource, _exact_text,
    runtime_effect_intent_fingerprint, runtime_effect_intent_for_request,
    runtime_effect_result_fingerprint,
)
from control_plane_kit_core.runtime_effects import (
    EffectResultKind, RuntimeEffectContractError, RuntimeEffectKind,
    RuntimeEffectRequest, RuntimeEffectResult,
)


_PROFILE = "configuration-invocation-completion.v1"
_KEY = "configuration_invocation_completion"
_SELECTION_DOMAIN = b"control-plane-kit.configuration-invocation-selection.v1\x00"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_FIELDS = {"profile", "request_fingerprint", "selection_fingerprint"}


def _require(condition: bool) -> None:
    if not condition:
        raise RuntimeEffectContractError("configuration invocation is malformed")


def _digest(value: object) -> None:
    _require(type(value) is str and _DIGEST.fullmatch(value) is not None)


@dataclass(frozen=True)
class ConfigurationInvocationCompletion:
    """Closed terminal-invocation assertion; not attachment or deletion proof."""

    request_fingerprint: str
    selection_fingerprint: str

    def __post_init__(self) -> None:
        _digest(self.request_fingerprint)
        _digest(self.selection_fingerprint)

    def descriptor(self) -> dict[str, str]:
        self.__post_init__()
        return {
            "profile": _PROFILE,
            "request_fingerprint": self.request_fingerprint,
            "selection_fingerprint": self.selection_fingerprint,
        }


def decode_configuration_invocation_completion(payload: object) -> ConfigurationInvocationCompletion:
    """Decode only the reserved profile's exact v1 mapping."""
    _require(type(payload) is dict and set(payload) == _FIELDS)
    _require(type(payload["profile"]) is str and payload["profile"] == _PROFILE)
    return ConfigurationInvocationCompletion(
        payload["request_fingerprint"], payload["selection_fingerprint"])


def configuration_invocation_selection_fingerprint(selection: ConfigurationInstanceSelection) -> str:
    """Commit the entire existing canonical selection with a distinct domain."""
    canonical = None
    try:
        canonical = ConfigurationInstanceSelectionCodec().encode_canonical_bytes(selection)
    except (TypeError, ValueError):
        pass
    # Raise outside the parser exception context; never retain candidate data.
    _require(canonical is not None)
    return sha256(_SELECTION_DOMAIN + canonical).hexdigest()


@dataclass(frozen=True)
class ConfigurationInvocationCorrelation:
    """Same-original context supplied by an owner, not an origin certificate."""

    request_fingerprint: str
    effect_id: str
    kind: RuntimeEffectKind
    source: RuntimeEffectIntentSource
    operation: StartNode | ReconcileNode
    selection: ConfigurationInstanceSelection

    def __post_init__(self) -> None:
        _digest(self.request_fingerprint)
        _exact_text(self.effect_id, "configuration invocation identity")
        _require(self.kind is RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1)
        _require(type(self.source) is RuntimeEffectIntentSource)
        RuntimeEffectIntentSource.__post_init__(self.source)
        _require(type(self.operation) in (StartNode, ReconcileNode))
        _require(type(self.operation.target) is NodeTarget)
        configuration_invocation_selection_fingerprint(self.selection)
        # The selection codec already proves one workspace/runtime/node scope.
        ref = self.selection.instances[0]
        _require(ref.workspace_id == self.source.workspace_id
            and ref.node_id == self.operation.target.node_id)


def configuration_invocation_correlation_for_request(
    request: RuntimeEffectRequest,
) -> ConfigurationInvocationCorrelation:
    """Derive context from a full real request without inventing product data."""
    _require(type(request) is RuntimeEffectRequest)
    RuntimeEffectRequest.__post_init__(request)
    intent = runtime_effect_intent_for_request(request)
    return ConfigurationInvocationCorrelation(
        request_fingerprint=runtime_effect_intent_fingerprint(intent),
        effect_id=request.effect_id, kind=request.kind, source=intent.source,
        operation=request.operation, selection=request.configuration_instances,
    )


def configuration_invocation_completion_for_result(
    context: ConfigurationInvocationCorrelation,
    result: RuntimeEffectResult,
) -> ConfigurationInvocationCompletion | None:
    """Read optional completion only after validating the entire original result.

    An absent profile is ordinary unprofiled history. A present profile must
    describe a succeeded/failed result for this exact effect and full selection.
    Even a valid profile does not establish that resources are detached.
    """
    _require(type(context) is ConfigurationInvocationCorrelation)
    ConfigurationInvocationCorrelation.__post_init__(context)
    runtime_effect_result_fingerprint(result)
    if _KEY not in result.evidence:
        return None
    completion = decode_configuration_invocation_completion(result.evidence[_KEY])
    _require(result.kind in (EffectResultKind.SUCCEEDED, EffectResultKind.FAILED))
    _require(result.effect_id == context.effect_id)
    _require(completion.request_fingerprint == context.request_fingerprint)
    _require(completion.selection_fingerprint
        == configuration_invocation_selection_fingerprint(context.selection))
    return completion


__all__ = [
    "ConfigurationInvocationCompletion",
    "ConfigurationInvocationCorrelation",
    "configuration_invocation_selection_fingerprint",
    "decode_configuration_invocation_completion",
    "configuration_invocation_correlation_for_request",
    "configuration_invocation_completion_for_result",
]
