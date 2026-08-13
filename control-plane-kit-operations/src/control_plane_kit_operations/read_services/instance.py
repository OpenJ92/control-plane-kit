"""Read-only projections over durable operations truth."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.planning import (
    DEFAULT_ACTIVITY_PLAN_CODEC,
    ActivityImpact,
    ReviewChange,
    RiskLevel,
    plan_recovery_transition,
)
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.public_ingress import IngressAuthorityReference
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.secrets import SecretProviderId
from control_plane_kit_core.topology import (
    DEFAULT_GRAPH_CODEC,
    GraphDescriptorError,
    GraphDescriptorCodec,
    validate_graph,
)
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityRunRecord,
    ApprovalRequestRecord,
    BoundedEvidence,
    FailureEvidence,
    ObservationFreshness,
    ObservationRecord,
    ObservationStaleReason,
    OperationSessionRecord,
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
    ReadCollection,
    ReadPage,
    ReadPageError,
    ReadPageRequest,
)

from ._redaction import _redact_descriptor_value
from .errors import ReadModelError
from .models import FocusedDetailReadModel
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
        self._graph_topology_store = graph_topology_store
        self._workspace_graph = _WorkspaceGraphReadProjection(
            workspace_store,
            graph_topology_store,
            graph_codec=graph_codec,
        )
        self._activity_history_store = activity_history_store
        self._execution_store = execution_store
        self._observed_state_store = observed_state_store
        self._runtime_authority_store = runtime_authority_store
        self._runtime_authority_delivery_store = runtime_authority_delivery_store
        self._ingress_authority_store = ingress_authority_store
        self._secret_provider_store = secret_provider_store
        self._secret_reference_store = secret_reference_store
        self._gateway_probe_store = gateway_probe_store
        self._delegation_signing_key_store = delegation_signing_key_store
        self._graph_codec = graph_codec
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
        if request.collection is not ReadCollection.ACTIVITY_SESSIONS:
            raise ReadPageError("activity session request is incongruent")
        self._workspace(request.scope.workspace_id)
        return self._activity_history().session_page(request).map(
            _session_summary_descriptor
        )

    def open_sessions(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        if request.collection is not ReadCollection.OPEN_SESSIONS:
            raise ReadPageError("open session request is incongruent")
        self._workspace(request.scope.workspace_id)
        return self._activity_history().session_page(request).map(
            _session_summary_descriptor
        )

    def session_detail(
        self,
        workspace_id: str,
        session_id: str,
    ) -> FocusedDetailReadModel:
        self._workspace(workspace_id)
        store = self._activity_history()
        session = _session_in_workspace(store, workspace_id, session_id)
        return FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="session-detail",
            payload={
                "session": _session_summary_descriptor(session)
            },
        )

    def session_actions(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        if request.collection is not ReadCollection.SESSION_ACTIONS:
            raise ReadPageError("session action request is incongruent")
        self._workspace(request.scope.workspace_id)
        store = self._activity_history()
        _session_in_workspace(
            store,
            request.scope.workspace_id,
            request.scope.session_id,
        )
        return store.action_page(request).map(_action_descriptor)

    def run_events(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        if request.collection is not ReadCollection.RUN_EVENTS:
            raise ReadPageError("run event request is incongruent")
        workspace_id = request.scope.workspace_id
        self._workspace(workspace_id)
        store = self._execution()
        try:
            run = store.get_run(request.scope.run_id)
            execution_request = store.get_request(run.admission.request_id)
        except KeyError:
            raise ReadModelError("missing run in workspace") from None
        identity = getattr(execution_request, "identity", None)
        if getattr(identity, "workspace_id", None) != workspace_id:
            raise ReadModelError("missing run in workspace")
        return store.event_page(request).map(_event_descriptor)

    def session_plans(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        if request.collection is not ReadCollection.SESSION_PLANS:
            raise ReadPageError("session plan request is incongruent")
        self._workspace(request.scope.workspace_id)
        store = self._activity_history()
        _session_in_workspace(store, request.scope.workspace_id, request.scope.session_id)
        return store.plan_page(request).map(_plan_summary_descriptor)

    def session_approvals(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        if request.collection is not ReadCollection.SESSION_APPROVALS:
            raise ReadPageError("session approval request is incongruent")
        self._workspace(request.scope.workspace_id)
        store = self._activity_history()
        _session_in_workspace(store, request.scope.workspace_id, request.scope.session_id)
        return store.approval_page(request).map(
            lambda item: _approval_descriptor(item.request, item.decision)
        )

    def plan_detail(
        self,
        workspace_id: str,
        plan_id: str,
    ) -> FocusedDetailReadModel:
        self._workspace(workspace_id)
        store = self._activity_history()
        plan = _plan_in_workspace(store, workspace_id, plan_id)
        payload = _plan_summary_descriptor(plan)
        payload["risk_summary"] = _risk_summary(plan)
        payload["recovery"] = self._recovery_for_plan(workspace_id, plan)
        return FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="plan-detail",
            payload={"plan": payload},
        )

    def pending_approvals(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        if request.collection is not ReadCollection.PENDING_APPROVALS:
            raise ReadPageError("pending approval request is incongruent")
        self._workspace(request.scope.workspace_id)
        return self._activity_history().pending_approval_page(request).map(
            lambda item: _approval_descriptor(item.request, item.decision)
        )

    def plan_runs(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        if request.collection is not ReadCollection.PLAN_RUNS:
            raise ReadPageError("plan run request is incongruent")
        self._workspace(request.scope.workspace_id)
        _plan_in_workspace(
            self._activity_history(),
            request.scope.workspace_id,
            request.scope.plan_id,
        )
        return self._execution().run_page(request).map(_run_summary_descriptor)

    def approval_detail(
        self,
        workspace_id: str,
        approval_request_id: str,
    ) -> FocusedDetailReadModel:
        self._workspace(workspace_id)
        store = self._activity_history()
        approval = _approval_in_workspace(store, workspace_id, approval_request_id)
        decision = store.approval_decision_for_request(approval.request_id)
        detail: dict[str, object] = {
            "approval": _approval_descriptor(approval, decision)
        }
        if isinstance(approval.subject, ActivityPlanApprovalSubject):
            plan = _plan_in_workspace(store, workspace_id, approval.subject.plan_id)
            if plan.session_id != approval.session_id:
                raise ReadModelError(
                    f"approval {approval_request_id!r} references plan truth outside its session"
                )
            payload = _plan_summary_descriptor(plan)
            payload["risk_summary"] = _risk_summary(plan)
            payload["recovery"] = self._recovery_for_plan(workspace_id, plan)
            detail["plan"] = payload
        else:
            detail["rotation"] = approval.subject.descriptor()
        return FocusedDetailReadModel(
            workspace_id=workspace_id,
            kind="approval-detail",
            payload=detail,
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

    def _activity_history(self) -> ActivityHistoryStore:
        if self._activity_history_store is None:
            raise ReadModelError("activity history store is not configured")
        return self._activity_history_store

    def _execution(self) -> ExecutionStore:
        if self._execution_store is None:
            raise ReadModelError("execution store is not configured")
        return self._execution_store

    def _observed_state(self) -> ObservedStateStore:
        if self._observed_state_store is None:
            raise ReadModelError("observed state store is not configured")
        return self._observed_state_store

    def _recovery_for_plan(
        self,
        workspace_id: str,
        plan: ActivityPlanRecord,
    ) -> Mapping[str, object]:
        try:
            base = self._graph_topology_store.get(plan.base_graph_id)
            desired = self._graph_topology_store.get(plan.desired_graph_id)
        except KeyError as exc:
            raise ReadModelError(
                f"plan {plan.plan_id!r} references missing graph truth"
            ) from exc
        if base.workspace_id != workspace_id or desired.workspace_id != workspace_id:
            raise ReadModelError(
                f"plan {plan.plan_id!r} references graph truth outside workspace"
            )
        try:
            target = validate_graph(self._graph_codec.decode(base.graph_descriptor))
            current = validate_graph(self._graph_codec.decode(desired.graph_descriptor))
            candidate = plan_recovery_transition(current, target)
        except (GraphDescriptorError, ValueError, TypeError) as exc:
            raise ReadModelError(
                f"plan {plan.plan_id!r} has invalid recovery graph truth"
            ) from exc
        return candidate.descriptor()


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


def _session_summary_descriptor(session: OperationSessionRecord) -> dict[str, object]:
    return {
        "session_id": session.session_id,
        "workspace_id": session.workspace_id,
        "actor_id": session.actor_id,
        "title": session.title,
        "status": session.status.value,
        "created_at": session.created_at,
        "closed_at": session.closed_at,
        "metadata": _redact_descriptor_value("metadata", session.metadata),
    }


def _action_descriptor(action: object) -> dict[str, object]:
    return {
        "action_id": getattr(action, "action_id"),
        "session_id": getattr(action, "session_id"),
        "ordinal": getattr(action, "ordinal"),
        "action_type": getattr(action, "action_type").value,
        "actor_id": getattr(action, "actor_id"),
        "payload": _redact_descriptor_value("payload", getattr(action, "payload")),
        "created_at": getattr(action, "created_at"),
    }


def _approval_descriptor(
    approval: ApprovalRequestRecord,
    decision: object | None,
) -> dict[str, object]:
    descriptor = {
        "request_id": approval.request_id,
        "session_id": approval.session_id,
        "requested_by": approval.requested_by,
        "requested_at": approval.requested_at,
        "required_scope": approval.required_scope.value,
        "max_risk": approval.max_risk.value,
        "destructive": approval.destructive,
        "comment": approval.comment,
        "state": "pending" if decision is None else getattr(decision, "decision").value,
        "decision": None if decision is None else {
            "decision_id": getattr(decision, "decision_id"),
            "actor_id": getattr(decision, "actor_id"),
            "decision": getattr(decision, "decision").value,
            "scope": getattr(decision, "scope").value,
            "decided_at": getattr(decision, "decided_at"),
            "comment": getattr(decision, "comment"),
        },
    }
    if isinstance(approval.subject, ActivityPlanApprovalSubject):
        descriptor["plan_id"] = approval.plan_id
    else:
        descriptor["subject"] = approval.subject.descriptor()
        descriptor["review_digest"] = approval.subject.review_digest
    return descriptor


def _plan_summary_descriptor(plan: ActivityPlanRecord) -> dict[str, object]:
    return {
        "plan_id": plan.plan_id,
        "session_id": plan.session_id,
        "base_graph_id": plan.base_graph_id,
        "desired_graph_id": plan.desired_graph_id,
        "base_realized_projection_id": plan.base_realized_projection_id,
        "desired_realized_projection_id": plan.desired_realized_projection_id,
        "desired_graph_revision": plan.desired_graph_revision,
        "status": plan.status.value,
        "created_at": plan.created_at,
        "payload": DEFAULT_ACTIVITY_PLAN_CODEC.encode(plan.plan),
    }


def _run_summary_descriptor(run: ActivityRunRecord) -> dict[str, object]:
    return {
        "run_id": run.run_id,
        "plan_id": run.plan_id,
        "request_id": run.admission.request_id,
        "attempt": run.retry.attempt,
        "prior_run_id": run.retry.prior_run_id,
        "status": run.status.value,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "settled_at": run.settled_at,
        "metadata": _redact_descriptor_value("metadata", run.metadata.descriptor()),
    }


def _event_descriptor(event: ActivityEventRecord) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "run_id": event.run_id,
        "ordinal": event.ordinal,
        "event_type": event.kind.value,
        "occurred_at": event.occurred_at,
        "activity_id": event.activity_id,
        "payload": _redact_descriptor_value("payload", event.evidence.descriptor()),
        "failure": _failure_descriptor(event.failure),
    }


def _failure_descriptor(failure: FailureEvidence | None) -> dict[str, object] | None:
    if failure is None:
        return None
    return {
        "category": failure.category.value,
        "code": failure.code,
        "message": failure.message,
        "details": _redact_descriptor_value("details", failure.details.descriptor()),
    }


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


def _session_in_workspace(
    store: ActivityHistoryStore,
    workspace_id: str,
    session_id: str,
) -> OperationSessionRecord:
    try:
        session = store.get_session(session_id)
    except KeyError as exc:
        raise ReadModelError(
            f"missing session {session_id!r} in workspace {workspace_id!r}"
        ) from exc
    if session.workspace_id != workspace_id:
        raise ReadModelError(
            f"missing session {session_id!r} in workspace {workspace_id!r}"
        )
    return session


def _plan_in_workspace(
    store: ActivityHistoryStore,
    workspace_id: str,
    plan_id: str,
) -> ActivityPlanRecord:
    try:
        plan = store.get_plan(plan_id)
        session = store.get_session(plan.session_id)
    except KeyError as exc:
        raise ReadModelError(
            f"missing plan {plan_id!r} in workspace {workspace_id!r}"
        ) from exc
    if session.workspace_id != workspace_id:
        raise ReadModelError(
            f"missing plan {plan_id!r} in workspace {workspace_id!r}"
        )
    return plan


def _approval_in_workspace(
    store: ActivityHistoryStore,
    workspace_id: str,
    approval_request_id: str,
) -> ApprovalRequestRecord:
    try:
        approval = store.get_approval_request(approval_request_id)
        session = store.get_session(approval.session_id)
    except KeyError as exc:
        raise ReadModelError(
            f"missing approval {approval_request_id!r} in workspace {workspace_id!r}"
        ) from exc
    if session.workspace_id != workspace_id:
        raise ReadModelError(
            f"missing approval {approval_request_id!r} in workspace {workspace_id!r}"
        )
    return approval


def _risk_summary(plan: ActivityPlanRecord) -> dict[str, object]:
    counts = {risk.value: 0 for risk in RiskLevel}
    for activity in plan.plan.activities:
        counts[activity.risk.value] += 1
    max_risk = max(
        (activity.risk for activity in plan.plan.activities),
        key=_risk_rank,
        default=RiskLevel.INFORMATIONAL,
    )
    return {
        "max_risk": max_risk.value,
        "counts": counts,
        "destructive_count": sum(
            activity.impact is ActivityImpact.DESTRUCTIVE
            for activity in plan.plan.activities
        ),
        "review_blocker_count": sum(
            isinstance(activity.operation, ReviewChange)
            for activity in plan.plan.activities
        ),
        "ready_for_execution": plan.plan.ready_for_execution,
    }


def _risk_rank(risk: RiskLevel) -> int:
    return tuple(RiskLevel).index(risk)
