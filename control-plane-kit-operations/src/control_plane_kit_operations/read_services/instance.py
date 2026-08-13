"""Read-only projections over durable operations truth."""

from __future__ import annotations

from datetime import datetime, timezone

from control_plane_kit_core.public_ingress import IngressAuthorityReference
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.secrets import SecretProviderId
from control_plane_kit_core.topology import (
    DEFAULT_GRAPH_CODEC,
    GraphDescriptorCodec,
)
from control_plane_kit_operations.read_pages import (
    ReadPage,
    ReadPageRequest,
)

from .authority_secrets import _AuthoritySecretReadProjection
from .gateway_security import _GatewaySecurityReadProjection
from .models import FocusedDetailReadModel
from .observations import (
    ObservationFreshnessPolicy,
    _ObservationReadProjection,
)
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
        self._observations = _ObservationReadProjection(
            self._workspace_graph.require_workspace,
            observed_state_store,
            clock=clock,
            freshness=observation_freshness,
        )
        self._authority_secrets = _AuthoritySecretReadProjection(
            self._workspace_graph.require_workspace,
            runtime_authority_store=runtime_authority_store,
            runtime_authority_delivery_store=runtime_authority_delivery_store,
            ingress_authority_store=ingress_authority_store,
            secret_provider_store=secret_provider_store,
            secret_reference_store=secret_reference_store,
        )
        self._gateway_security = _GatewaySecurityReadProjection(
            self._workspace_graph.require_workspace,
            gateway_probe_store=gateway_probe_store,
            delegation_signing_key_store=delegation_signing_key_store,
        )

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
        return self._observations.observed_state(request)

    def runtime_authorities(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        return self._authority_secrets.runtime_authorities(request)

    def runtime_authority_detail(
        self,
        workspace_id: str,
        authority_ref: RuntimeAuthorityReference,
    ) -> FocusedDetailReadModel:
        return self._authority_secrets.runtime_authority_detail(
            workspace_id,
            authority_ref,
        )

    def runtime_authority_deliveries(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        return self._authority_secrets.runtime_authority_deliveries(request)

    def runtime_authority_delivery_detail(
        self,
        workspace_id: str,
        authority_ref: RuntimeAuthorityReference,
    ) -> FocusedDetailReadModel:
        return self._authority_secrets.runtime_authority_delivery_detail(
            workspace_id,
            authority_ref,
        )

    def ingress_authorities(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        return self._authority_secrets.ingress_authorities(request)

    def ingress_authority_detail(
        self,
        workspace_id: str,
        authority_ref: IngressAuthorityReference,
    ) -> FocusedDetailReadModel:
        return self._authority_secrets.ingress_authority_detail(
            workspace_id,
            authority_ref,
        )

    def secret_providers(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        return self._authority_secrets.secret_providers(request)

    def secret_provider_detail(
        self,
        workspace_id: str,
        provider_id: SecretProviderId,
    ) -> FocusedDetailReadModel:
        return self._authority_secrets.secret_provider_detail(
            workspace_id,
            provider_id,
        )

    def secret_references(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        return self._authority_secrets.secret_references(request)

    def secret_reference_detail(
        self,
        workspace_id: str,
        registration_id: str,
    ) -> FocusedDetailReadModel:
        return self._authority_secrets.secret_reference_detail(
            workspace_id,
            registration_id,
        )

    def gateway_probe_timeline(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        return self._gateway_security.gateway_probe_timeline(request)

    def gateway_probe_detail(
        self,
        workspace_id: str,
        probe_id: str,
    ) -> FocusedDetailReadModel:
        return self._gateway_security.gateway_probe_detail(
            workspace_id,
            probe_id,
        )

    def delegation_signing_keys(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[dict[str, object]]:
        return self._gateway_security.delegation_signing_keys(request)

    def gateway_verifier_configuration(
        self,
        workspace_id: str,
        gateway_node_id: str,
    ) -> FocusedDetailReadModel:
        return self._gateway_security.gateway_verifier_configuration(
            workspace_id,
            gateway_node_id,
        )

    def control_surface(
        self,
        workspace_id: str,
        *,
        pointer: str = "current",
    ) -> ControlSurfaceReadModel:
        return self._workspace_graph.control_surface(workspace_id, pointer=pointer)
