"""Pure runtime-effect intent and provider-observation language."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
from typing import TypeAlias

import rfc8785

from control_plane_kit_core.runtime_effects import (
    ActivityId,
    EffectResultKind,
    RunId,
    RuntimeAuthorityAccessDelivery,
    RuntimeAuthorityAccessDeliveryKind,
    RuntimeAuthorityReference,
    RuntimeEffectContractError,
    RuntimeEffectFailure,
    RuntimeEffectKind,
    RuntimeEffectRequest,
    RuntimeEffectResult,
    RuntimeEffectSource,
    RuntimeEndpointObservation,
    RuntimeKind,
    RuntimeProductMaterial,
    activity_operation_descriptor,
)
from control_plane_kit_core.secrets import SecretResolutionGrant, SecretUseIntent


_INTENT_DOMAIN = b"control-plane-kit.runtime-effect-intent.v1\x00"
_RESULT_DOMAIN = b"control-plane-kit.runtime-effect-result.v1\x00"
_OBSERVATION_DOMAIN = b"control-plane-kit.runtime-effect-observation.v1\x00"
_INTENT_MAX_BYTES = 1_048_576
_OUTCOME_MAX_BYTES = 8_192
_EVIDENCE_MAX_BYTES = 4_096
_MAX_TEXT = 512
_MAX_FIELDS = 32
_MAX_ITEMS = 32
_MAX_JSON_INTEGER = 9_007_199_254_740_991
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class RuntimeEffectIntentSource:
    """Pre-start durable coordinates that exclude the generated start event."""

    workspace_id: str
    request_id: str
    run_id: RunId
    plan_id: str
    base_graph_id: str
    desired_graph_id: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.workspace_id, "workspace identity"),
            (self.request_id, "request identity"),
            (self.plan_id, "plan identity"),
            (self.base_graph_id, "base graph identity"),
            (self.desired_graph_id, "desired graph identity"),
        ):
            _exact_text(value, label)
        if type(self.run_id) is not RunId:
            raise RuntimeEffectContractError("run identity must be RunId")

    def descriptor(self) -> dict[str, str]:
        return {
            "workspace_id": self.workspace_id,
            "request_id": self.request_id,
            "run_id": self.run_id.value,
            "plan_id": self.plan_id,
            "base_graph_id": self.base_graph_id,
            "desired_graph_id": self.desired_graph_id,
        }


@dataclass(frozen=True)
class RuntimeEffectIntent:
    """Canonical runtime intent available before a start event is allocated."""

    kind: RuntimeEffectKind
    runtime_kind: RuntimeKind
    source: RuntimeEffectIntentSource
    activity_id: ActivityId
    operation: object
    authority_ref: RuntimeAuthorityReference | None
    authority_deliveries: tuple[RuntimeAuthorityAccessDelivery, ...]
    products: tuple[RuntimeProductMaterial, ...]

    def __post_init__(self) -> None:
        if type(self.kind) is not RuntimeEffectKind:
            raise RuntimeEffectContractError("runtime effect intent kind is malformed")
        if type(self.runtime_kind) is not RuntimeKind:
            raise RuntimeEffectContractError("runtime effect intent runtime kind is malformed")
        if type(self.source) is not RuntimeEffectIntentSource:
            raise RuntimeEffectContractError("runtime effect intent source is malformed")
        if type(self.activity_id) is not ActivityId:
            raise RuntimeEffectContractError("runtime effect intent activity is malformed")
        malformed_operation = False
        try:
            activity_operation_descriptor(self.operation)
        except Exception:
            malformed_operation = True
        if malformed_operation:
            raise RuntimeEffectContractError(
                "runtime effect intent operation is malformed"
            )
        if self.authority_ref is not None and type(self.authority_ref) is not RuntimeAuthorityReference:
            raise RuntimeEffectContractError("runtime effect intent authority is malformed")
        if type(self.authority_deliveries) is not tuple or not all(
            type(value) is RuntimeAuthorityAccessDelivery
            for value in self.authority_deliveries
        ):
            raise RuntimeEffectContractError(
                "runtime effect intent authority deliveries are malformed"
            )
        if type(self.products) is not tuple or not all(
            type(value) is RuntimeProductMaterial for value in self.products
        ):
            raise RuntimeEffectContractError("runtime effect intent products are malformed")

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "runtime_kind": self.runtime_kind.value,
            "authority_ref": (
                None if self.authority_ref is None else self.authority_ref.descriptor()
            ),
            "authority_deliveries": [
                value.descriptor() for value in self.authority_deliveries
            ],
            "source": self.source.descriptor(),
            "activity_id": self.activity_id.value,
            "operation": activity_operation_descriptor(self.operation),
            "products": [value.descriptor() for value in self.products],
        }


def runtime_effect_intent_for_request(
    request: RuntimeEffectRequest,
) -> RuntimeEffectIntent:
    """Project one exact post-start request into its pre-start intent."""

    if type(request) is not RuntimeEffectRequest or type(request.source) is not RuntimeEffectSource:
        raise RuntimeEffectContractError("runtime effect intent requires exact request")
    if (
        type(request.effect_id) is not str
        or type(request.source.intent_event_id) is not str
        or request.effect_id != request.source.intent_event_id
    ):
        raise RuntimeEffectContractError(
            "runtime effect identity must match intent event identity"
        )
    source = request.source
    return RuntimeEffectIntent(
        kind=request.kind,
        runtime_kind=request.runtime_kind,
        source=RuntimeEffectIntentSource(
            workspace_id=source.workspace_id,
            request_id=source.request_id,
            run_id=source.run_id,
            plan_id=source.plan_id,
            base_graph_id=source.base_graph_id,
            desired_graph_id=source.desired_graph_id,
        ),
        activity_id=request.activity_id,
        operation=request.operation,
        authority_ref=request.authority_ref,
        authority_deliveries=request.authority_deliveries,
        products=request.products,
    )


def runtime_effect_request_for_intent(
    intent: RuntimeEffectIntent,
    *,
    effect_id: str,
    secret_resolution_grants: tuple[SecretResolutionGrant, ...] = (),
) -> RuntimeEffectRequest:
    """Bind a generated start-event identity and transient grants to an intent."""

    if type(intent) is not RuntimeEffectIntent:
        raise RuntimeEffectContractError("runtime effect request requires exact intent")
    _exact_text(effect_id, "runtime effect event identity")
    if type(secret_resolution_grants) is not tuple or not all(
        type(value) is SecretResolutionGrant for value in secret_resolution_grants
    ):
        raise RuntimeEffectContractError(
            "runtime effect request grants are malformed"
        )
    return RuntimeEffectRequest(
        effect_id=effect_id,
        kind=intent.kind,
        runtime_kind=intent.runtime_kind,
        source=RuntimeEffectSource(
            workspace_id=intent.source.workspace_id,
            request_id=intent.source.request_id,
            run_id=intent.source.run_id,
            plan_id=intent.source.plan_id,
            base_graph_id=intent.source.base_graph_id,
            desired_graph_id=intent.source.desired_graph_id,
            intent_event_id=effect_id,
        ),
        activity_id=intent.activity_id,
        operation=intent.operation,
        authority_ref=intent.authority_ref,
        authority_deliveries=intent.authority_deliveries,
        secret_resolution_grants=secret_resolution_grants,
        products=intent.products,
    )


def runtime_effect_intent_fingerprint(intent: RuntimeEffectIntent) -> str:
    if type(intent) is not RuntimeEffectIntent or type(intent.source) is not RuntimeEffectIntentSource:
        raise RuntimeEffectContractError("runtime effect intent is malformed")
    return _fingerprint(
        _INTENT_DOMAIN,
        intent.descriptor(),
        _INTENT_MAX_BYTES,
        "runtime effect intent",
    )


@dataclass(frozen=True)
class RuntimeEffectObservationRequest:
    """Exact post-start request delivered to a read-only runtime observer."""

    runtime_request: RuntimeEffectRequest = field(repr=False)
    intent: RuntimeEffectIntent = field(init=False)
    request_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.runtime_request) is not RuntimeEffectRequest:
            raise RuntimeEffectContractError("runtime observation requires exact request")
        intent = runtime_effect_intent_for_request(self.runtime_request)
        _validate_grants(self.runtime_request)
        object.__setattr__(self, "intent", intent)
        object.__setattr__(
            self,
            "request_fingerprint",
            runtime_effect_intent_fingerprint(intent),
        )

    @property
    def effect_id(self) -> str:
        return self.runtime_request.effect_id

    def descriptor(self) -> dict[str, object]:
        return {
            "effect_id": self.effect_id,
            "request_fingerprint": self.request_fingerprint,
            "intent": self.intent.descriptor(),
        }


@dataclass(frozen=True)
class RuntimeEffectObservationEvidence:
    values: dict[str, object]

    def __post_init__(self) -> None:
        values = _evidence(self.values)
        if not values:
            raise RuntimeEffectContractError("runtime observation evidence is empty")
        object.__setattr__(self, "values", values)

    def descriptor(self) -> dict[str, object]:
        return _copy_json(self.values)


@dataclass(frozen=True)
class RuntimeEffectObservationFailure:
    code: str
    message: str
    details: RuntimeEffectObservationEvidence | None = None

    def __post_init__(self) -> None:
        _exact_text(self.code, "runtime observation failure code")
        _exact_text(self.message, "runtime observation failure message")
        if self.details is not None and type(self.details) is not RuntimeEffectObservationEvidence:
            raise RuntimeEffectContractError(
                "runtime observation failure details are malformed"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "details": {} if self.details is None else self.details.descriptor(),
        }


def _observation_descriptor(value: object, kind: str) -> dict[str, object]:
    return {
        "kind": kind,
        "effect_id": value.effect_id,
        "request_fingerprint": value.request_fingerprint,
        "evidence": value.evidence.descriptor(),
        "failure": None if value.failure is None else value.failure.descriptor(),
        "observations": [item.descriptor() for item in value.observations],
    }


def _validate_observation_result(
    value: object,
    *,
    requires_failure: bool,
    forbids_observations: bool = False,
) -> None:
    _exact_text(value.effect_id, "runtime observation effect identity")
    if type(value.request_fingerprint) is not str or not _SHA256.fullmatch(
        value.request_fingerprint
    ):
        raise RuntimeEffectContractError(
            "runtime observation request fingerprint is malformed"
        )
    if type(value.evidence) is not RuntimeEffectObservationEvidence:
        raise RuntimeEffectContractError("runtime observation evidence is malformed")
    if requires_failure:
        if type(value.failure) is not RuntimeEffectObservationFailure:
            raise RuntimeEffectContractError("runtime observation failure is required")
    elif value.failure is not None:
        raise RuntimeEffectContractError("runtime observation failure is not allowed")
    if type(value.observations) is not tuple or not all(
        type(item) is RuntimeEndpointObservation for item in value.observations
    ):
        raise RuntimeEffectContractError("runtime endpoint observations are malformed")
    if forbids_observations and value.observations:
        raise RuntimeEffectContractError(
            "runtime observation variant does not admit endpoint observations"
        )


@dataclass(frozen=True)
class RuntimeEffectObservedSucceeded:
    effect_id: str
    request_fingerprint: str
    evidence: RuntimeEffectObservationEvidence = field(repr=False)
    failure: RuntimeEffectObservationFailure | None = field(default=None, repr=False)
    observations: tuple[object, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        _validate_observation_result(self, requires_failure=False)

    def descriptor(self) -> dict[str, object]:
        return _observation_descriptor(self, "succeeded")


@dataclass(frozen=True)
class RuntimeEffectObservedFailed:
    effect_id: str
    request_fingerprint: str
    evidence: RuntimeEffectObservationEvidence = field(repr=False)
    failure: RuntimeEffectObservationFailure | None = field(default=None, repr=False)
    observations: tuple[object, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        _validate_observation_result(self, requires_failure=True)

    def descriptor(self) -> dict[str, object]:
        return _observation_descriptor(self, "failed")


@dataclass(frozen=True)
class RuntimeEffectObservedAbsent:
    effect_id: str
    request_fingerprint: str
    evidence: RuntimeEffectObservationEvidence = field(repr=False)
    failure: RuntimeEffectObservationFailure | None = field(default=None, repr=False)
    observations: tuple[object, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        _validate_observation_result(
            self, requires_failure=False, forbids_observations=True
        )

    def descriptor(self) -> dict[str, object]:
        return _observation_descriptor(self, "absent")


@dataclass(frozen=True)
class RuntimeEffectObservedConflict:
    effect_id: str
    request_fingerprint: str
    evidence: RuntimeEffectObservationEvidence = field(repr=False)
    failure: RuntimeEffectObservationFailure | None = field(default=None, repr=False)
    observations: tuple[object, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        _validate_observation_result(self, requires_failure=True)

    def descriptor(self) -> dict[str, object]:
        return _observation_descriptor(self, "conflict")


@dataclass(frozen=True)
class RuntimeEffectObservedIndeterminate:
    effect_id: str
    request_fingerprint: str
    evidence: RuntimeEffectObservationEvidence = field(repr=False)
    failure: RuntimeEffectObservationFailure | None = field(default=None, repr=False)
    observations: tuple[object, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        _validate_observation_result(self, requires_failure=True)

    def descriptor(self) -> dict[str, object]:
        return _observation_descriptor(self, "indeterminate")


@dataclass(frozen=True)
class RuntimeEffectObserverUnsupported:
    effect_id: str
    request_fingerprint: str
    evidence: RuntimeEffectObservationEvidence = field(repr=False)
    failure: RuntimeEffectObservationFailure | None = field(default=None, repr=False)
    observations: tuple[object, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        _validate_observation_result(
            self, requires_failure=True, forbids_observations=True
        )

    def descriptor(self) -> dict[str, object]:
        return _observation_descriptor(self, "observer-unsupported")


RuntimeEffectObservationResult: TypeAlias = (
    RuntimeEffectObservedSucceeded
    | RuntimeEffectObservedFailed
    | RuntimeEffectObservedAbsent
    | RuntimeEffectObservedConflict
    | RuntimeEffectObservedIndeterminate
    | RuntimeEffectObserverUnsupported
)


_OBSERVATION_TYPES = (
    RuntimeEffectObservedSucceeded,
    RuntimeEffectObservedFailed,
    RuntimeEffectObservedAbsent,
    RuntimeEffectObservedConflict,
    RuntimeEffectObservedIndeterminate,
    RuntimeEffectObserverUnsupported,
)


def runtime_effect_observation_fingerprint(
    observation: RuntimeEffectObservationResult,
) -> str:
    if type(observation) not in _OBSERVATION_TYPES:
        raise RuntimeEffectContractError("runtime effect observation is malformed")
    return _fingerprint(
        _OBSERVATION_DOMAIN,
        observation.descriptor(),
        _OUTCOME_MAX_BYTES,
        "runtime effect observation",
    )


def runtime_effect_result_fingerprint(result: RuntimeEffectResult) -> str:
    if type(result) is not RuntimeEffectResult:
        raise RuntimeEffectContractError("runtime effect result is malformed")
    _validate_live_result(result)
    return _fingerprint(
        _RESULT_DOMAIN,
        result.descriptor(),
        _OUTCOME_MAX_BYTES,
        "runtime effect result",
    )


def _validate_grants(request: RuntimeEffectRequest) -> None:
    grants = request.secret_resolution_grants
    if type(grants) is not tuple or not all(type(value) is SecretResolutionGrant for value in grants):
        raise RuntimeEffectContractError("runtime observation grants are malformed")
    uses = tuple((value.reference, value.intent) for value in grants)
    if len(set(uses)) != len(uses):
        raise RuntimeEffectContractError("runtime observation grants are duplicated")
    allowed = _allowed_grant_uses(request)
    if any(
        use not in allowed
        or grant.workspace_id != request.source.workspace_id
        or grant.effect_id != request.effect_id
        or grant.run_id != request.source.run_id.value
        or grant.activity_id != request.activity_id.value
        for grant, use in zip(grants, uses, strict=True)
    ):
        raise RuntimeEffectContractError("runtime observation grant is not admitted")


def _allowed_grant_uses(
    request: RuntimeEffectRequest,
) -> set[tuple[object, SecretUseIntent]]:
    allowed: set[tuple[object, SecretUseIntent]] = set()
    delivery_intents = {
        "ca-cert": SecretUseIntent.DOCKER_REMOTE_TLS_CA_CERTIFICATE,
        "client-cert": SecretUseIntent.DOCKER_REMOTE_TLS_CLIENT_CERTIFICATE,
        "client-key": SecretUseIntent.DOCKER_REMOTE_TLS_CLIENT_KEY,
    }
    for delivery in request.authority_deliveries:
        if (
            delivery.delivery_kind
            is RuntimeAuthorityAccessDeliveryKind.REMOTE_DOCKER_TLS_SECRET_FILES
        ):
            for reference in delivery.secret_references:
                intent = delivery_intents.get(reference.label)
                if intent is not None:
                    allowed.add((reference.reference, intent))
    for material in request.products:
        contract = material.product.runtime_contract
        for delivery in contract.secret_deliveries:
            reference = getattr(delivery, "reference", None)
            intent = getattr(delivery, "intent", None)
            if reference is not None and type(intent) is SecretUseIntent:
                allowed.add((reference, intent))
        if material.pull_authority is not None:
            allowed.add(
                (
                    material.pull_authority.credential_reference,
                    SecretUseIntent.OCI_PULL_CREDENTIAL,
                )
            )
        for check in contract.verification.checks:
            authentication = getattr(check, "authentication", None)
            reference = getattr(authentication, "password_reference", None)
            if reference is not None:
                allowed.add((reference, SecretUseIntent.POSTGRES_PASSWORD))
    return allowed


def _fingerprint(
    domain: bytes,
    descriptor: dict[str, object],
    maximum_bytes: int,
    label: str,
) -> str:
    malformed = False
    try:
        canonical = rfc8785.dumps(descriptor)
    except (TypeError, ValueError):
        malformed = True
        canonical = b""
    if malformed:
        raise RuntimeEffectContractError(f"{label} is malformed")
    if len(canonical) > maximum_bytes:
        raise RuntimeEffectContractError(f"{label} is too large")
    return hashlib.sha256(domain + canonical).hexdigest()


def _exact_text(value: object, label: str) -> None:
    if type(value) is not str or not value or len(value) > _MAX_TEXT or "\x00" in value:
        raise RuntimeEffectContractError(f"{label} must be bounded text")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise RuntimeEffectContractError(f"{label} must be bounded text")
    lowered = value.lower()
    if any(marker in lowered for marker in ("password=", "token=", "secret=")):
        raise RuntimeEffectContractError(f"{label} contains secret-shaped text")


def _evidence(values: object) -> dict[str, object]:
    if type(values) is not dict or len(values) > _MAX_FIELDS:
        raise RuntimeEffectContractError("runtime observation evidence is malformed")
    normalized = {
        _evidence_key(key): _evidence_value(value, depth=0)
        for key, value in values.items()
    }
    canonical = rfc8785.dumps(normalized)
    if len(canonical) > _EVIDENCE_MAX_BYTES:
        raise RuntimeEffectContractError("runtime observation evidence is too large")
    return dict(sorted(normalized.items()))


def _evidence_key(value: object) -> str:
    _exact_text(value, "runtime observation evidence key")
    if value.lower() in {"address", "endpoint"}:
        raise RuntimeEffectContractError("runtime observation evidence contains raw endpoint material")
    return value


def _evidence_value(value: object, *, depth: int) -> object:
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if not -_MAX_JSON_INTEGER <= value <= _MAX_JSON_INTEGER:
            raise RuntimeEffectContractError("runtime observation evidence integer is invalid")
        return value
    if type(value) is str:
        _exact_text(value, "runtime observation evidence text")
        lowered = value.lower()
        if (
            "://" in lowered
            or "private key" in lowered
            or re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", value)
        ):
            raise RuntimeEffectContractError("runtime observation evidence contains raw material")
        return value
    if type(value) is list:
        if depth >= 4 or len(value) > _MAX_ITEMS:
            raise RuntimeEffectContractError("runtime observation evidence is too deeply nested")
        return [_evidence_value(item, depth=depth + 1) for item in value]
    if type(value) is dict:
        if depth >= 4 or len(value) > _MAX_FIELDS:
            raise RuntimeEffectContractError("runtime observation evidence is too deeply nested")
        normalized = {
            _evidence_key(key): _evidence_value(item, depth=depth + 1)
            for key, item in value.items()
        }
        return dict(sorted(normalized.items()))
    raise RuntimeEffectContractError("runtime observation evidence contains unsupported value")


def _copy_json(value: dict[str, object]) -> dict[str, object]:
    return {key: _copy_json_value(item) for key, item in value.items()}


def _copy_json_value(value: object) -> object:
    if type(value) is dict:
        return _copy_json(value)
    if type(value) is list:
        return [_copy_json_value(item) for item in value]
    return value


def _validate_live_result(result: RuntimeEffectResult) -> None:
    _exact_text(result.effect_id, "runtime effect result identity")
    if type(result.kind) is not EffectResultKind:
        raise RuntimeEffectContractError("runtime effect result kind is malformed")
    if type(result.evidence) is not dict:
        raise RuntimeEffectContractError("runtime effect result evidence is malformed")
    _validate_live_json(result.evidence)
    if result.failure is not None:
        if type(result.failure) is not RuntimeEffectFailure:
            raise RuntimeEffectContractError("runtime effect result failure is malformed")
        _exact_text(result.failure.code, "runtime effect result failure code")
        _exact_text(result.failure.message, "runtime effect result failure message")
        if type(result.failure.details) is not dict:
            raise RuntimeEffectContractError(
                "runtime effect result failure details are malformed"
            )
        _validate_live_json(result.failure.details)
    if type(result.observations) is not tuple or not all(
        type(item) is RuntimeEndpointObservation for item in result.observations
    ):
        raise RuntimeEffectContractError("runtime effect result observations are malformed")


def _validate_live_json(value: object) -> None:
    if value is None or type(value) in {bool, int, float, str}:
        if type(value) is str and any(
            0xD800 <= ord(character) <= 0xDFFF for character in value
        ):
            raise RuntimeEffectContractError("runtime effect result JSON is malformed")
        return
    if type(value) is list:
        for item in value:
            _validate_live_json(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise RuntimeEffectContractError("runtime effect result JSON is malformed")
            _validate_live_json(item)
        return
    raise RuntimeEffectContractError("runtime effect result JSON is malformed")


__all__ = [
    "RuntimeEffectIntent",
    "RuntimeEffectIntentSource",
    "RuntimeEffectObservationEvidence",
    "RuntimeEffectObservationFailure",
    "RuntimeEffectObservationRequest",
    "RuntimeEffectObservedSucceeded",
    "RuntimeEffectObservedFailed",
    "RuntimeEffectObservedAbsent",
    "RuntimeEffectObservedConflict",
    "RuntimeEffectObservedIndeterminate",
    "RuntimeEffectObserverUnsupported",
    "RuntimeEffectObservationResult",
    "runtime_effect_intent_for_request",
    "runtime_effect_request_for_intent",
    "runtime_effect_intent_fingerprint",
    "runtime_effect_result_fingerprint",
    "runtime_effect_observation_fingerprint",
]
