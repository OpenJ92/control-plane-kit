"""Immutable pre-start runtime-effect intent evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
import json

import rfc8785

from control_plane_kit_core.operations import (
    ActivityEventKind,
    EffectAttemptIdentity,
    RunId,
)
from control_plane_kit_core.planning import (
    ActivityId,
    activity_operation_from_descriptor,
)
from control_plane_kit_core.runtime_authority import (
    RuntimeAuthorityAccessDeliveryCodec,
    RuntimeAuthorityReferenceCodec,
)
from control_plane_kit_core.runtime_effect_observation import (
    RuntimeEffectIntent,
    RuntimeEffectIntentSource,
    runtime_effect_intent_fingerprint,
    runtime_effect_intent_for_request,
    runtime_effect_request_for_intent,
)
from control_plane_kit_core.runtime_effects import (
    RuntimeEffectContractError,
    RuntimeEffectKind,
    RuntimeProductMaterial,
)
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    OperationsRecordError,
)


_INTENT_ERROR = "effect attempt intent evidence is invalid"
_INTENT_MAX_BYTES = 1_048_576
_VALIDATION_EVENT_ID = "effect-attempt-intent-validation"


def _object_without_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value


def _reject_nonfinite(_value: str) -> object:
    raise ValueError


def _canonical_runtime_effect_intent(
    intent: RuntimeEffectIntent,
    event_kind: ActivityEventKind | None = None,
) -> bytes:
    descriptor = RuntimeEffectIntent.descriptor(intent)
    if event_kind is ActivityEventKind.STEP_STARTED:
        expected_operation_kind = "start-node"
    elif event_kind is ActivityEventKind.STEP_COMPENSATION_STARTED:
        expected_operation_kind = "stop-node"
    elif event_kind is None:
        expected_operation_kind = None
    else:
        return b""
    if (
        expected_operation_kind is not None
        and descriptor["operation"]["kind"] != expected_operation_kind
    ):
        return b""
    return rfc8785.dumps(descriptor)


def _encode_runtime_effect_intent(
    intent: RuntimeEffectIntent,
    event_kind: ActivityEventKind | None = None,
) -> bytes:
    invalid = (
        type(intent) is not RuntimeEffectIntent
        or type(intent.source) is not RuntimeEffectIntentSource
        or type(intent.source.run_id) is not RunId
        or type(intent.source.run_id.value) is not str
        or type(intent.activity_id) is not ActivityId
        or type(intent.activity_id.value) is not str
    )
    document = b""
    reconstructed = None
    if not invalid:
        try:
            request = runtime_effect_request_for_intent(
                intent,
                effect_id=_VALIDATION_EVENT_ID,
                secret_resolution_grants=(),
            )
            reconstructed = runtime_effect_intent_for_request(request)
            document = _canonical_runtime_effect_intent(intent, event_kind)
        except (RuntimeEffectContractError, ValueError):
            invalid = True
    if (
        invalid
        or reconstructed != intent
        or not 1 <= len(document) <= _INTENT_MAX_BYTES
    ):
        _raise_intent_error()
    return document


def _decode_runtime_effect_intent(document: bytes) -> RuntimeEffectIntent:
    invalid = (
        type(document) is not bytes
        or not 1 <= len(document) <= _INTENT_MAX_BYTES
    )
    intent = None
    canonical = b""
    if not invalid:
        try:
            value = json.loads(
                document,
                object_pairs_hook=_object_without_duplicate_keys,
                parse_constant=_reject_nonfinite,
            )
            match value:
                case {
                "kind": kind,
                "runtime_kind": runtime_kind,
                "authority_ref": authority_ref_descriptor,
                "authority_deliveries": [*authority_delivery_descriptors],
                "source": {
                    "workspace_id": workspace_id,
                    "request_id": request_id,
                    "run_id": run_id,
                    "plan_id": plan_id,
                    "base_graph_id": base_graph_id,
                    "desired_graph_id": desired_graph_id,
                    **source_extra,
                },
                "activity_id": activity_id,
                "operation": {**operation_descriptor},
                "products": [*product_descriptors],
                **extra,
                } if not source_extra and not extra:
                    intent = RuntimeEffectIntent(
                    kind=RuntimeEffectKind(kind),
                    runtime_kind=RuntimeKind(runtime_kind),
                    source=RuntimeEffectIntentSource(
                        workspace_id=workspace_id,
                        request_id=request_id,
                        run_id=RunId(run_id),
                        plan_id=plan_id,
                        base_graph_id=base_graph_id,
                        desired_graph_id=desired_graph_id,
                    ),
                    activity_id=ActivityId(activity_id),
                    operation=activity_operation_from_descriptor(
                        operation_descriptor
                    ),
                    authority_ref=(
                        None
                        if authority_ref_descriptor is None
                        else RuntimeAuthorityReferenceCodec.decode(
                            RuntimeAuthorityReferenceCodec(),
                            authority_ref_descriptor,
                        )
                    ),
                    authority_deliveries=(
                        *[
                            RuntimeAuthorityAccessDeliveryCodec.decode(
                                RuntimeAuthorityAccessDeliveryCodec(),
                                item,
                            )
                            for item in authority_delivery_descriptors
                        ],
                    ),
                    products=(
                        *[
                            RuntimeProductMaterial.from_descriptor(item)
                            for item in product_descriptors
                        ],
                    ),
                )
                    canonical = _canonical_runtime_effect_intent(intent)
                case _:
                    invalid = True
        except (KeyError, RuntimeEffectContractError, ValueError, RecursionError):
            invalid = True
    if invalid or canonical != document:
        _raise_intent_error()
    return intent


def _raise_intent_error() -> None:
    raise OperationsRecordError(_INTENT_ERROR) from None


@dataclass(frozen=True)
class EffectAttemptIntentRecord:
    """Exact protected intent evidence bound to one original start event."""

    identity: EffectAttemptIdentity
    original_start_event: ActivityEventRecord = field(repr=False)
    intent: RuntimeEffectIntent = field(repr=False)

    def __post_init__(self) -> None:
        invalid = (
            type(self) is not EffectAttemptIntentRecord
            or type(self.identity) is not EffectAttemptIdentity
            or type(self.original_start_event) is not ActivityEventRecord
            or type(self.intent) is not RuntimeEffectIntent
            or type(self.identity.run_id) is not RunId
            or type(self.identity.run_id.value) is not str
        )
        identity = None
        event = None
        intent = None
        if not invalid:
            try:
                identity = EffectAttemptIdentity(
                    self.identity.run_id,
                    self.identity.activity_id,
                    self.identity.attempt,
                )
                event = ActivityEventRecord(
                    self.original_start_event.event_id,
                    self.original_start_event.run_id,
                    self.original_start_event.ordinal,
                    self.original_start_event.kind,
                    self.original_start_event.occurred_at,
                    activity_id=self.original_start_event.activity_id,
                    evidence=self.original_start_event.evidence,
                    failure=self.original_start_event.failure,
                    recovery=self.original_start_event.recovery,
                )
            except ValueError:
                invalid = True
        if not invalid:
            document = _encode_runtime_effect_intent(self.intent, event.kind)
            intent = _decode_runtime_effect_intent(document)
            invalid = (
                identity != self.identity
                or event != self.original_start_event
                or intent != self.intent
                or identity.run_id != intent.source.run_id
                or identity.activity_id != intent.activity_id.value
                or event.run_id != identity.run_id.value
                or event.activity_id != identity.activity_id
            )
        if invalid:
            _raise_intent_error()

    @property
    def workspace_id(self) -> str:
        return self.intent.source.workspace_id

    @property
    def request_id(self) -> str:
        return self.intent.source.request_id

    @property
    def request_fingerprint(self) -> str:
        return runtime_effect_intent_fingerprint(self.intent)


__all__ = ["EffectAttemptIntentRecord"]
