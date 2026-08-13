"""Internal store capabilities required by the read service."""

from __future__ import annotations

from typing import Protocol

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.public_ingress import IngressAuthorityReference
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.secrets import SecretProviderId
from control_plane_kit_operations.delegation_signing_keys import (
    RegisteredDelegationSigningKey,
)
from control_plane_kit_operations.read_pages import ReadPage, ReadPageRequest
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityRunRecord,
    ApprovalRequestRecord,
    GraphVersionRecord,
    ObservationRecord,
    OperationSessionRecord,
    WorkspaceRecord,
)
from control_plane_kit_operations.secret_providers import (
    RegisteredSecretProvider,
    RegisteredSecretReference,
)


class WorkspaceStore(Protocol):
    def get(self, workspace_id: str) -> WorkspaceRecord: ...


class GraphTopologyStore(Protocol):
    def get(self, graph_id: str) -> GraphVersionRecord: ...


class ActivityHistoryStore(Protocol):
    def get_session(self, session_id: str) -> OperationSessionRecord: ...
    def sessions_for_workspace(
        self, workspace_id: str
    ) -> tuple[OperationSessionRecord, ...]: ...
    def session_page(
        self, request: ReadPageRequest
    ) -> ReadPage[OperationSessionRecord]: ...
    def actions_for_session(self, session_id: str) -> tuple[object, ...]: ...
    def action_page(self, request: ReadPageRequest) -> ReadPage[object]: ...
    def get_plan(self, plan_id: str) -> ActivityPlanRecord: ...
    def plans_for_session(self, session_id: str) -> tuple[ActivityPlanRecord, ...]: ...
    def plan_page(self, request: ReadPageRequest) -> ReadPage[ActivityPlanRecord]: ...
    def get_approval_request(self, request_id: str) -> ApprovalRequestRecord: ...
    def approval_requests_for_session(
        self, session_id: str
    ) -> tuple[ApprovalRequestRecord, ...]: ...
    def approval_page(self, request: ReadPageRequest) -> ReadPage[object]: ...
    def pending_approval_page(self, request: ReadPageRequest) -> ReadPage[object]: ...
    def approval_decision_for_request(self, request_id: str) -> object | None: ...


class ExecutionStore(Protocol):
    def get_request(self, request_id: str) -> object: ...
    def get_run(self, run_id: str) -> ActivityRunRecord: ...
    def runs_for_plan(self, plan_id: str) -> tuple[ActivityRunRecord, ...]: ...
    def run_page(self, request: ReadPageRequest) -> ReadPage[ActivityRunRecord]: ...
    def events_for_run(self, run_id: str) -> tuple[ActivityEventRecord, ...]: ...
    def event_page(self, request: ReadPageRequest) -> ReadPage[ActivityEventRecord]: ...


class ObservedStateStore(Protocol):
    def latest_for_workspace(self, workspace_id: str) -> tuple[ObservationRecord, ...]: ...
    def latest_page(self, request: ReadPageRequest) -> ReadPage[ObservationRecord]: ...


class RuntimeAuthorityStore(Protocol):
    def get(self, workspace_id: str, authority_ref: RuntimeAuthorityReference) -> object: ...
    def list_active(self, workspace_id: str) -> tuple[object, ...]: ...
    def active_page(self, request: ReadPageRequest) -> ReadPage[object]: ...


class RuntimeAuthorityDeliveryStore(Protocol):
    def get(self, workspace_id: str, authority_ref: RuntimeAuthorityReference) -> object: ...
    def list_active(self, workspace_id: str) -> tuple[object, ...]: ...
    def active_page(self, request: ReadPageRequest) -> ReadPage[object]: ...


class IngressAuthorityStore(Protocol):
    def get(self, workspace_id: str, authority_ref: IngressAuthorityReference) -> object: ...
    def list_active(self, workspace_id: str) -> tuple[object, ...]: ...
    def active_page(self, request: ReadPageRequest) -> ReadPage[object]: ...


class SecretProviderStore(Protocol):
    def get_active(
        self,
        workspace_id: str,
        provider_id: SecretProviderId,
    ) -> RegisteredSecretProvider: ...
    def list_active(
        self,
        workspace_id: str,
    ) -> tuple[RegisteredSecretProvider, ...]: ...
    def active_page(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[RegisteredSecretProvider]: ...


class SecretReferenceStore(Protocol):
    def get_by_registration(
        self,
        workspace_id: str,
        registration_id: str,
    ) -> RegisteredSecretReference: ...
    def list_active(
        self,
        workspace_id: str,
    ) -> tuple[RegisteredSecretReference, ...]: ...
    def active_page(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[RegisteredSecretReference]: ...


class GatewayProbeStore(Protocol):
    def get(self, probe_id: str) -> object: ...
    def page(self, request: ReadPageRequest) -> ReadPage[object]: ...


class DelegationSigningKeyStore(Protocol):
    def list_workspace(
        self,
        workspace_id: str,
    ) -> tuple[RegisteredDelegationSigningKey, ...]: ...
    def workspace_page(
        self,
        request: ReadPageRequest,
    ) -> ReadPage[RegisteredDelegationSigningKey]: ...
    def require_unambiguous_active(
        self,
        workspace_id: str,
        purpose: DelegationKeyPurpose,
    ) -> RegisteredDelegationSigningKey: ...
    def list_for_verification(
        self,
        workspace_id: str,
        purpose: DelegationKeyPurpose,
        issuer: str,
    ) -> tuple[RegisteredDelegationSigningKey, ...]: ...
