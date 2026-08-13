"""Read-only projections over durable operations truth."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.public_ingress import IngressAuthorityReference
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.secrets import SecretProviderId
from control_plane_kit_core.topology import (
    DEFAULT_GRAPH_CODEC,
    GraphDescriptorCodec,
)
from control_plane_kit_operations.records import (
    BoundedEvidence,
    ObservationFreshness,
    ObservationRecord,
    ObservationStaleReason,
    WorkspaceRecord,
)
from control_plane_kit_operations.runtime_authorities import RuntimeAuthorityNotFound
from control_plane_kit_operations.ingress_authorities import IngressAuthorityNotFound
from control_plane_kit_operations.secret_providers import (
    RegisteredSecretProvider,
    RegisteredSecretReference,
    SecretProviderNotFound,
)
from control_plane_kit_operations.delegation_signing_keys import (
    DelegationSigningKeyNotFound,
    RegisteredDelegationSigningKey,
    RegisteredDelegationSigningKeyStatus,
)
from control_plane_kit_operations.gateway_probes import (
    GatewayProbeError,
    GatewayProbeVerifierConfiguration,
)
from control_plane_kit_operations.read_pages import (
    ReadPage,
    ReadPageRequest,
)

from ._redaction import _redact_descriptor_value
from .errors import ReadModelError
from .models import FocusedDetailReadModel
from .operations_history import _OperationsHistoryReadProjection
from .protocols import (
    ActivityHistoryStore,
    DelegationSigningKeyStore,
    ExecutionStore,
    GatewayProbeStore,
    GraphTopologyStore,
    IngressAuthorityStore,
    ObservedStateStore,
    RuntimeAuthorityDeliveryStore,
    RuntimeAuthorityStore,
    SecretProviderStore,
    SecretReferenceStore,
    WorkspaceStore,
)
from .workspace_graph import (
    ControlSurfaceReadModel,
    GraphPointerReadModel,
    WorkspaceReadModel,
    WorkspaceSummary,
    _WorkspaceGraphReadProjection,
)


@dataclass(frozen=True)
class ObservationFreshnessPolicy:
    """Maximum age for evidence to describe the current graph."""

    maximum_age: timedelta = timedelta(minutes=5)

    def __post_init__(self) -> None:
        if self.maximum_age <= timedelta(0):
            raise ValueError("observation maximum age must be positive")


@dataclass(frozen=True)
class ProjectedObservation:
    """Observation interpreted at one explicit read instant."""

    record: ObservationRecord
    freshness: ObservationFreshness
    stale_reason: ObservationStaleReason | None


class InstanceReadService:
    """Compose canonical operations stores into read-only instance views."""

    def __init__(
        self,
        *,
        workspace_store: WorkspaceStore,
        graph_topology_store: GraphTopologyStore,
        activity_history_store: ActivityHistoryStore | None = None,
        execution_store: ExecutionStore | None = None,
        observed_state_store: ObservedStateStore | None = None,
        runtime_authority_store: RuntimeAuthorityStore | None = None,
        runtime_authority_delivery_store: RuntimeAuthorityDeliveryStore | None = None,
        ingress_authority_store: IngressAuthorityStore | None = None,
        secret_provider_store: SecretProviderStore | None = None,
        secret_reference_store: SecretReferenceStore | None = None,
        gateway_probe_store: GatewayProbeStore | None = None,
        delegation_signing_key_store: DelegationSigningKeyStore | None = None,
        graph_codec: GraphDescriptorCodec = DEFAULT_GRAPH_CODEC,
        clock=lambda: datetime.now(timezone.utc),
        observation_freshness: ObservationFreshnessPolicy = ObservationFreshnessPolicy(),
    ) -> None:
        self._workspace_graph = _WorkspaceGraphReadProjection(
            workspace_store,
            graph_topology_store,
            graph_codec=graph_codec,
        )
        self._operations_history = _OperationsHistoryReadProjection(
            self._workspace_graph.require_workspace,
            graph_topology_store,
            activity_history_store,
            execution_store,
            graph_codec=graph_codec,
        )
        self._observed_state_store = observed_state_store
        self._runtime_authority_store = runtime_authority_store
        self._runtime_authority_delivery_store = runtime_authority_delivery_store
        self._ingress_authority_store = ingress_authority_store
        self._secret_provider_store = secret_provider_store
        self._secret_reference_store = secret_reference_store
        self._gateway_probe_store = gateway_probe_store
        self._delegation_signing_key_store = delegation_signing_key_store
        self._clock = clock
        self._observation_freshness = observation_freshness

    def workspace(self, workspace_id: str) -> WorkspaceReadModel:
        return self._workspace_graph.workspace(workspace_id)

    def current_graph(self, workspace_id: str) -> GraphPointerReadModel:
        return self._workspace_graph.current_graph(workspace_id)

    def desired_graph(self, workspace_id: str) -> GraphPointerReadModel:
        return self._workspace_graph.desired_graph(workspace_id)

    def operator_graph(
        self,
        workspace_id: str,
        *,
        pointer: str = "current",
    ) -> GraphPointerReadModel:
        return self._workspace_graph.operator_graph(workspace_id, pointer=pointer)

    def activity_sessions(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        return self._operations_history.activity_sessions(request)

    def open_sessions(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        return self._operations_history.open_sessions(request)

    def session_detail(
        self,
        workspace_id: str,
        session_id: str,
    ) -> FocusedDetailReadModel:
        return self._operations_history.session_detail(workspace_id, session_id)

    def session_actions(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        return self._operations_history.session_actions(request)

    def run_events(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        return self._operations_history.run_events(request)

    def session_plans(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        return self._operations_history.session_plans(request)

    def session_approvals(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        return self._operations_history.session_approvals(request)

    def plan_detail(
        self,
        workspace_id: str,
        plan_id: str,
    ) -> FocusedDetailReadModel:
        return self._operations_history.plan_detail(workspace_id, plan_id)

    def pending_approvals(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        return self._operations_history.pending_approvals(request)

    def plan_runs(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        return self._operations_history.plan_runs(request)

    def approval_detail(
        self,
        workspace_id: str,
        approval_request_id: str,
    ) -> FocusedDetailReadModel:
        return self._operations_history.approval_detail(
            workspace_id,
            approval_request_id,
        )

    def observed_state(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        workspace_id = request.scope.workspace_id
        workspace = self._workspace(workspace_id)
        as_of = self._clock()
        if not isinstance(as_of, datetime) or as_of.tzinfo is None:
            raise ReadModelError("read-service clock must return a timezone-aware datetime")
        return self._observed_state().latest_page(request).map(
            lambda record: _observation_descriptor(
                project_observation(
                    record,
                    current_graph_id=workspace.current_graph_id,
                    as_of=as_of,
                    policy=self._observation_freshness,
                )
            )
        )

    def runtime_authorities(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        workspace_id = request.scope.workspace_id
        self._workspace(workspace_id)
        if self._runtime_authority_store is None:
            raise ReadModelError("runtime authority store is not configured")
        return self._runtime_authority_store.active_page(request).map(
            lambda value: dict(_redacted_runtime_authority(value))
        )

    def runtime_authority_detail(
        self,
        workspace_id: str,
        authority_ref: RuntimeAuthorityReference,
    ) -> FocusedDetailReadModel:
        self._workspace(workspace_id)
        if self._runtime_authority_store is None:
            raise ReadModelError("runtime authority store is not configured")
        try:
            authority = self._runtime_authority_store.get(workspace_id, authority_ref)
        except (KeyError, RuntimeAuthorityNotFound) as exc:
            raise ReadModelError(
                f"missing runtime authority {authority_ref.reference_id!r}"
            ) from exc
        return FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="runtime-authority-detail",
            payload={"runtime_authority": _redacted_runtime_authority(authority)},
        )

    def runtime_authority_deliveries(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        workspace_id = request.scope.workspace_id
        self._workspace(workspace_id)
        if self._runtime_authority_delivery_store is None:
            raise ReadModelError("runtime authority delivery store is not configured")
        return self._runtime_authority_delivery_store.active_page(request).map(
            lambda value: dict(_redacted_runtime_authority_delivery(value))
        )

    def runtime_authority_delivery_detail(
        self,
        workspace_id: str,
        authority_ref: RuntimeAuthorityReference,
    ) -> FocusedDetailReadModel:
        self._workspace(workspace_id)
        if self._runtime_authority_delivery_store is None:
            raise ReadModelError("runtime authority delivery store is not configured")
        try:
            delivery = self._runtime_authority_delivery_store.get(
                workspace_id,
                authority_ref,
            )
        except (KeyError, RuntimeAuthorityNotFound) as exc:
            raise ReadModelError(
                f"missing runtime authority delivery {authority_ref.reference_id!r}"
            ) from exc
        return FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="runtime-authority-delivery-detail",
            payload={
                "runtime_authority_delivery": (
                    _redacted_runtime_authority_delivery(delivery)
                )
            },
        )

    def ingress_authorities(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        workspace_id = request.scope.workspace_id
        self._workspace(workspace_id)
        if self._ingress_authority_store is None:
            raise ReadModelError("ingress authority store is not configured")
        return self._ingress_authority_store.active_page(request).map(
            lambda value: dict(_redacted_ingress_authority(value))
        )

    def ingress_authority_detail(
        self,
        workspace_id: str,
        authority_ref: IngressAuthorityReference,
    ) -> FocusedDetailReadModel:
        self._workspace(workspace_id)
        if self._ingress_authority_store is None:
            raise ReadModelError("ingress authority store is not configured")
        try:
            authority = self._ingress_authority_store.get(workspace_id, authority_ref)
        except (KeyError, IngressAuthorityNotFound) as exc:
            raise ReadModelError(
                f"missing ingress authority {authority_ref.reference_id!r}"
            ) from exc
        return FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="ingress-authority-detail",
            payload={"ingress_authority": _redacted_ingress_authority(authority)},
        )

    def secret_providers(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        workspace_id = request.scope.workspace_id
        self._workspace(workspace_id)
        if self._secret_provider_store is None:
            raise ReadModelError("secret provider store is not configured")
        return self._secret_provider_store.active_page(request).map(
            lambda value: dict(_public_secret_provider(value))
        )

    def secret_provider_detail(
        self,
        workspace_id: str,
        provider_id: SecretProviderId,
    ) -> FocusedDetailReadModel:
        self._workspace(workspace_id)
        if self._secret_provider_store is None:
            raise ReadModelError("secret provider store is not configured")
        try:
            provider = self._secret_provider_store.get_active(
                workspace_id,
                provider_id,
            )
        except (KeyError, SecretProviderNotFound) as error:
            raise ReadModelError("missing secret provider") from error
        return FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="secret-provider-detail",
            payload={"secret_provider": _public_secret_provider(provider)},
        )

    def secret_references(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        workspace_id = request.scope.workspace_id
        self._workspace(workspace_id)
        if self._secret_reference_store is None:
            raise ReadModelError("secret reference store is not configured")
        return self._secret_reference_store.active_page(request).map(
            lambda value: dict(_public_secret_reference(value))
        )

    def secret_reference_detail(
        self,
        workspace_id: str,
        registration_id: str,
    ) -> FocusedDetailReadModel:
        self._workspace(workspace_id)
        if self._secret_reference_store is None:
            raise ReadModelError("secret reference store is not configured")
        try:
            reference = self._secret_reference_store.get_by_registration(
                workspace_id,
                registration_id,
            )
        except (KeyError, SecretProviderNotFound) as error:
            raise ReadModelError("missing secret reference") from error
        return FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="secret-reference-detail",
            payload={"secret_reference": _public_secret_reference(reference)},
        )

    def gateway_probe_timeline(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        workspace_id = request.scope.workspace_id
        self._workspace(workspace_id)
        if self._gateway_probe_store is None:
            raise ReadModelError("gateway probe store is not configured")
        return self._gateway_probe_store.page(request).map(
            lambda value: dict(value.descriptor())
        )

    def gateway_probe_detail(
        self,
        workspace_id: str,
        probe_id: str,
    ) -> FocusedDetailReadModel:
        self._workspace(workspace_id)
        if self._gateway_probe_store is None:
            raise ReadModelError("gateway probe store is not configured")
        try:
            attempt = self._gateway_probe_store.get(probe_id)
        except KeyError as error:
            raise ReadModelError(f"missing gateway probe {probe_id!r}") from error
        if attempt.workspace_id != workspace_id:
            raise ReadModelError(f"missing gateway probe {probe_id!r}")
        return FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="gateway-probe-detail",
            payload={"gateway_probe": attempt.descriptor()},
        )

    def delegation_signing_keys(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        workspace_id = request.scope.workspace_id
        self._workspace(workspace_id)
        if self._delegation_signing_key_store is None:
            raise ReadModelError("delegation signing key store is not configured")
        return self._delegation_signing_key_store.workspace_page(request).map(
            _public_delegation_signing_key
        )

    def gateway_verifier_configuration(
        self,
        workspace_id: str,
        gateway_node_id: str,
    ) -> FocusedDetailReadModel:
        self._workspace(workspace_id)
        if self._delegation_signing_key_store is None:
            raise ReadModelError("delegation signing key store is not configured")
        try:
            active = self._delegation_signing_key_store.require_unambiguous_active(
                workspace_id,
                DelegationKeyPurpose.GATEWAY_PROBE,
            )
            verification_keys = (
                self._delegation_signing_key_store.list_for_verification(
                    workspace_id,
                    DelegationKeyPurpose.GATEWAY_PROBE,
                    active.issuer,
                )
            )
            if not any(
                value.status is RegisteredDelegationSigningKeyStatus.ACTIVE
                for value in verification_keys
            ):
                raise GatewayProbeError("gateway verifier set has no active key")
            configuration = GatewayProbeVerifierConfiguration(
                issuer=active.issuer,
                audience=f"gateway:{workspace_id}:{gateway_node_id}",
                gateway_node_id=gateway_node_id,
                public_keys=tuple(value.public_key for value in verification_keys),
            )
        except (DelegationSigningKeyNotFound, GatewayProbeError) as error:
            raise ReadModelError(
                "gateway verifier configuration is unavailable"
            ) from error
        return FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="gateway-verifier-configuration",
            payload={
                "gateway_verifier_configuration": {
                    "issuer": configuration.issuer,
                    "audience": configuration.audience,
                    "gateway_node_id": configuration.gateway_node_id,
                    "public_keys": [
                        {
                            **key.descriptor(),
                            "public_key_pem": key.public_key_pem,
                        }
                        for key in configuration.public_keys
                    ],
                    "public_environment": [
                        binding.descriptor()
                        for binding in configuration.public_environment()
                    ],
                }
            },
        )

    def control_surface(
        self,
        workspace_id: str,
        *,
        pointer: str = "current",
    ) -> ControlSurfaceReadModel:
        return self._workspace_graph.control_surface(workspace_id, pointer=pointer)

    def _workspace(self, workspace_id: str) -> WorkspaceRecord:
        return self._workspace_graph.require_workspace(workspace_id)

    def _observed_state(self) -> ObservedStateStore:
        if self._observed_state_store is None:
            raise ReadModelError("observed state store is not configured")
        return self._observed_state_store


def project_observation(
    record: ObservationRecord,
    *,
    current_graph_id: str | None,
    as_of: datetime,
    policy: ObservationFreshnessPolicy,
) -> ProjectedObservation:
    """Derive usability without rewriting durable observation evidence."""

    if as_of.tzinfo is None:
        raise ValueError("observation projection clock must be timezone-aware")
    if record.freshness is ObservationFreshness.STALE:
        return _stale(record, ObservationStaleReason.RECORDED_STALE)
    if record.graph_id is None:
        return _stale(record, ObservationStaleReason.UNCORRELATED)
    if current_graph_id != record.graph_id:
        return _stale(record, ObservationStaleReason.GRAPH_CHANGED)
    try:
        observed_at = datetime.fromisoformat(record.observed_at.replace("Z", "+00:00"))
    except ValueError:
        return _stale(record, ObservationStaleReason.MALFORMED_TIMESTAMP)
    if observed_at.tzinfo is None:
        return _stale(record, ObservationStaleReason.MALFORMED_TIMESTAMP)
    normalized_as_of = as_of.astimezone(timezone.utc)
    normalized_observed_at = observed_at.astimezone(timezone.utc)
    if normalized_observed_at > normalized_as_of:
        return _stale(record, ObservationStaleReason.FUTURE_TIMESTAMP)
    if normalized_as_of - normalized_observed_at > policy.maximum_age:
        return _stale(record, ObservationStaleReason.EXPIRED)
    return ProjectedObservation(record, ObservationFreshness.FRESH, None)


def _stale(
    record: ObservationRecord,
    reason: ObservationStaleReason,
) -> ProjectedObservation:
    return ProjectedObservation(record, ObservationFreshness.STALE, reason)


def _redacted_runtime_authority(value: object) -> Mapping[str, object]:
    descriptor_method = getattr(value, "descriptor", None)
    if not callable(descriptor_method):
        raise ReadModelError("runtime authority record cannot be projected")
    descriptor = _mapping(descriptor_method())
    return _redact_descriptor_value("runtime_authority", descriptor)


def _redacted_ingress_authority(value: object) -> Mapping[str, object]:
    descriptor_method = getattr(value, "descriptor", None)
    if not callable(descriptor_method):
        raise ReadModelError("ingress authority record cannot be projected")
    descriptor = _mapping(descriptor_method())
    return _redact_descriptor_value("ingress_authority", descriptor)


def _redacted_runtime_authority_delivery(value: object) -> Mapping[str, object]:
    descriptor_method = getattr(value, "descriptor", None)
    if not callable(descriptor_method):
        raise ReadModelError("runtime authority delivery record cannot be projected")
    descriptor = _mapping(descriptor_method())
    return _redact_descriptor_value("runtime_authority_delivery", descriptor)


def _public_secret_provider(
    value: RegisteredSecretProvider,
) -> Mapping[str, object]:
    if not isinstance(value, RegisteredSecretProvider):
        raise ReadModelError("secret provider record cannot be projected")
    return value.descriptor()


def _public_secret_reference(
    value: RegisteredSecretReference,
) -> Mapping[str, object]:
    if not isinstance(value, RegisteredSecretReference):
        raise ReadModelError("secret reference record cannot be projected")
    return value.descriptor()


def _public_delegation_signing_key(
    value: RegisteredDelegationSigningKey,
) -> dict[str, object]:
    if not isinstance(value, RegisteredDelegationSigningKey):
        raise ReadModelError("delegation signing key record cannot be projected")
    return {
        "registration_id": value.registration_id,
        "workspace_id": value.workspace_id,
        "purpose": value.purpose.value,
        "issuer": value.issuer,
        "key_id": value.public_key.key_id,
        "algorithm": value.public_key.algorithm.value,
        "fingerprint_sha256": value.public_key.fingerprint_sha256,
        "admitted_by": value.admitted_by,
        "admitted_at": value.admitted_at,
        "status": value.status.value,
        "activated_by": value.activated_by,
        "activated_at": value.activated_at,
        "retired_by": value.retired_by,
        "retired_at": value.retired_at,
        "revoked_by": value.revoked_by,
        "revoked_at": value.revoked_at,
    }


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ReadModelError("expected mapping in graph descriptor")
    return value


def _observation_descriptor(projected: ProjectedObservation) -> dict[str, object]:
    record = projected.record
    return {
        "observation_id": record.observation_id,
        "workspace_id": record.workspace_id,
        "subject_id": record.subject_id,
        "status": record.status.value,
        "observed_at": record.observed_at,
        "graph_id": record.graph_id,
        "probe_kind": None if record.probe_kind is None else record.probe_kind.value,
        "probe_outcome": (
            None if record.probe_outcome is None else record.probe_outcome.value
        ),
        "endpoint_context": (
            None if record.endpoint_context is None else record.endpoint_context.value
        ),
        "freshness": projected.freshness.value,
        "stale": projected.freshness is ObservationFreshness.STALE,
        "stale_reason": (
            None if projected.stale_reason is None else projected.stale_reason.value
        ),
        "payload": _redact_descriptor_value("payload", record.evidence.descriptor()),
    }
