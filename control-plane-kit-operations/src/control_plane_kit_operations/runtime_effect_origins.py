"""Immutable original authority selection; neither a permit nor an outcome."""

from dataclasses import dataclass
import hashlib
import re

import rfc8785

from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
from control_plane_kit_core.planning import (
    StartRuntime, ReconcileRuntime, RemoveRuntimeResource, StartNode, ReconcileNode,
    StopNode, RemoveNodeResource, NodeTarget, RuntimeTarget,
)
from control_plane_kit_core.runtime_authority import RuntimeAuthorityAccessDeliveryKind
from control_plane_kit_core.runtime_effects import RuntimeEffectKind, RuntimeProductMaterial
from control_plane_kit_core.runtime_effect_observation import RuntimeEffectIntent, runtime_effect_request_for_intent
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations._temporal import validate_canonical_utc_timestamp
from control_plane_kit_operations.effect_permissions import ExecutionCommandReference
from control_plane_kit_operations.records import OperationsRecordError
from control_plane_kit_operations.runtime_authorities import (
    RegisteredRuntimeAuthority, LocalDockerSocketAuthority, RemoteDockerTlsAuthority,
    runtime_authority_registration_id_for,
)
from control_plane_kit_operations.runtime_secret_uses import required_prepared_runtime_secret_uses


def original_runtime_observation_supported(intent, runtime=None):
    """Selected observer's closed request shapes, without authorizing an effect."""
    if (type(intent) is not RuntimeEffectIntent or intent.kind is not RuntimeEffectKind.REALIZE_ACTIVITY
            or intent.runtime_kind is not RuntimeKind.DOCKER or intent.authority_ref is None):
        return False
    operation = intent.operation
    if type(operation) in (StartRuntime, ReconcileRuntime, RemoveRuntimeResource):
        supported = type(operation.target) is RuntimeTarget and not intent.products
    elif type(operation) in (StartNode, ReconcileNode, StopNode, RemoveNodeResource):
        supported = (type(operation.target) is NodeTarget and len(intent.products) == 1
            and type(intent.products[0]) is RuntimeProductMaterial
            and intent.products[0].node_id == operation.target.node_id)
        if supported and type(operation) in (StartNode, ReconcileNode):
            contract = intent.products[0].product.runtime_contract
            supported = (not contract.configuration_artifacts and not contract.secret_deliveries
                and not contract.retained_data_mounts
                and all(delivery.delivery_kind is RuntimeAuthorityAccessDeliveryKind.LOCAL_DOCKER_SOCKET_MOUNT
                    for delivery in intent.authority_deliveries)
                and (type(operation) is not ReconcileNode or not contract.verification.checks))
    else:
        supported = False
    if runtime is not None and type(runtime.authority) is RemoteDockerTlsAuthority and intent.authority_deliveries:
        return False
    return bool(supported)


def unprepared_runtime_origin_supported(intent, runtime):
    if (not original_runtime_observation_supported(intent, runtime)
            or type(runtime) is not RegisteredRuntimeAuthority
            or type(runtime.authority) is not LocalDockerSocketAuthority
            or runtime.workspace_id != intent.source.workspace_id
            or runtime.authority_ref != intent.authority_ref or runtime.runtime_kind is not intent.runtime_kind):
        return False
    runtime.__post_init__()
    if runtime.registration_id != runtime_authority_registration_id_for(
            runtime.workspace_id, runtime.authority_ref, runtime.runtime_kind, runtime.authority):
        raise OperationsRecordError("original runtime selection is incongruent")
    request = runtime_effect_request_for_intent(intent, effect_id="original-selection", secret_resolution_grants=())
    return not required_prepared_runtime_secret_uses(request, runtime)


@dataclass(frozen=True, slots=True)
class RuntimeEffectOriginRecord:
    command: ExecutionCommandReference
    workspace_id: str
    identity: EffectAttemptIdentity
    original_event_id: str
    request_fingerprint: str
    runtime_registration_id: str
    recorded_at: str
    version: int = 1

    def __post_init__(self):
        if (type(self.command) is not ExecutionCommandReference or type(self.identity) is not EffectAttemptIdentity
                or type(self.version) is not int or self.version != 1
                or any(type(value) is not str or not 1 <= len(value) <= 512
                    or any(ord(char) < 32 for char in value)
                    for value in (self.workspace_id, self.original_event_id, self.runtime_registration_id))
                or type(self.request_fingerprint) is not str
                or re.fullmatch(r"[0-9a-f]{64}", self.request_fingerprint) is None):
            raise OperationsRecordError("original runtime selection is malformed")
        self.command.__post_init__()
        self.identity.__post_init__()
        validate_canonical_utc_timestamp(self.recorded_at)
        if self.command.run_id != self.identity.run_id.value:
            raise OperationsRecordError("original runtime command is incongruent")
        if len(rfc8785.dumps(self.descriptor())) > 8192:
            raise OperationsRecordError("original runtime descriptor is oversized")

    def descriptor(self):
        return dict(version=self.version, command=dict(run_id=self.command.run_id,
            idempotency_key=self.command.idempotency_key, intent_fingerprint=self.command.intent_fingerprint),
            workspace_id=self.workspace_id, identity=dict(run_id=self.identity.run_id.value,
                activity_id=self.identity.activity_id, attempt=self.identity.attempt),
            original_event_id=self.original_event_id, request_fingerprint=self.request_fingerprint,
            runtime_registration_id=self.runtime_registration_id, recorded_at=self.recorded_at)

    @property
    def activity_id(self):
        return self.identity.activity_id

    @property
    def attempt(self):
        return self.identity.attempt

    @classmethod
    def from_descriptor(cls, value):
        try:
            if type(value) is not dict:
                raise ValueError
            record = cls(command=ExecutionCommandReference(**value["command"]),
                identity=EffectAttemptIdentity(RunId(value["identity"]["run_id"]),
                    value["identity"]["activity_id"], value["identity"]["attempt"]),
                **{key: value[key] for key in ("version", "workspace_id", "original_event_id",
                    "request_fingerprint", "runtime_registration_id", "recorded_at")})
            if record.descriptor() != value:
                raise ValueError
            return record
        except (KeyError, ValueError, TypeError, AttributeError):
            raise OperationsRecordError("original runtime descriptor is malformed") from None


def runtime_effect_origin_fingerprint(record, intent, runtime):
    if type(record) is not RuntimeEffectOriginRecord or not unprepared_runtime_origin_supported(intent, runtime):
        raise OperationsRecordError("original runtime selection is unavailable")
    record.__post_init__()
    payload = record.descriptor()
    del payload["recorded_at"]
    payload.update(domain="cpk.runtime-effect-origin.v1", runtime_kind=intent.runtime_kind.value,
        authority_ref=intent.authority_ref.reference_id,
        runtime_id=(intent.products[0].runtime_id if intent.products else intent.operation.target.runtime_id))
    return hashlib.sha256(rfc8785.dumps(payload)).hexdigest()
