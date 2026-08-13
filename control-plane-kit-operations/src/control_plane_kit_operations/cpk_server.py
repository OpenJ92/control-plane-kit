"""Operations-backed application services for the cpk-server wrapper."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from control_plane_kit_core.identity import (
    AuthenticatedPrincipal,
    IdentityContractError,
    PrincipalKind,
    TrustedCommandContext,
)
from control_plane_kit_core.gateway_delegation import (
    GatewayProbeAccessPath,
    GatewayProbeCommandKind,
    GatewayProbeRequest,
)
from control_plane_kit_core.delegation_keys import (
    DelegationKeyAlgorithm,
    DelegationKeyPurpose,
    DelegationPublicKey,
)
from control_plane_kit_core.runtime_effects import GatewayTargetId
from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.operations.commands import OperatorCommandKind
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.public_ingress import IngressAuthorityReference
from control_plane_kit_core.runtime_authority import (
    RuntimeAuthorityAccessDeliveryCodec,
    RuntimeAuthorityReference,
)
from control_plane_kit_core.runtime_effects import ImagePullAuthority
from control_plane_kit_core.products import ProductDescriptorCodec, ProductDescriptorError
from control_plane_kit_core.secrets import (
    SecretProviderEndpointReference,
    SecretProviderId,
    SecretReference,
    SecretUseIntent,
)
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, GraphDescriptorError
from control_plane_kit_core.types import RuntimeKind

from control_plane_kit_operations.admission import (
    ExecutionAdmissionCommandService,
    ExternalReadinessAttestation,
    RequestPlanExecution,
)
from control_plane_kit_operations.advancement import (
    AdvanceCurrentGraph,
    CurrentGraphAdvancementCommandService,
)
from control_plane_kit_operations.approvals import (
    ApprovalCommandService,
    DecideApproval,
    RequestApproval,
)
from control_plane_kit_operations.coordinator import ExecuteActivityRun, ExecutionCoordinator
from control_plane_kit_operations.ingress_authorities import (
    CloudflareZoneIngressAuthority,
    IngressAuthorityAuthorizationDenied,
    IngressAuthorityRegistrationError,
    IngressAuthorityRegistrationService,
    RegisterIngressAuthorityCommand,
    RegisteredIngressAuthority,
    RevokeIngressAuthorityCommand,
)
from control_plane_kit_operations.gateway_probes import (
    GatewayProbeAuthorizationDenied,
    GatewayProbeCommandService,
    GatewayProbeConflict,
    GatewayProbeError,
    GatewayProbeNotFound,
    RequestGatewayProbe,
)
from control_plane_kit_operations.delegation_signing_keys import (
    ActivateDelegationSigningKeyCommand,
    DelegationSigningKeyAuthorizationDenied,
    DelegationSigningKeyConflict,
    DelegationSigningKeyError,
    DelegationSigningKeyNotFound,
    DelegationSigningKeyRegistrationService,
    RegisterDelegationSigningKeyCommand,
    RetireDelegationSigningKeyCommand,
    RevokeDelegationSigningKeyCommand,
)
from control_plane_kit_operations.lifecycle import (
    ClaimAndOpenActivityRun,
    ExecutionWorkerAuthority,
    RunLifecycleCommandService,
    StartActivityRun,
)
from control_plane_kit_operations.planning import (
    ActivityPlanningCommandService,
    RequestActivityPlan,
    DesiredGraphCommandService,
    SetDesiredGraph,
)
from control_plane_kit_operations.products import (
    DescriptorSourceCodec,
    ImagePullAuthorityRegistrationService,
    ImportProductDescriptorCommand,
    InlineDescriptorSource,
    ProductRegistrationService,
    RegisterImagePullAuthorityCommand,
)
from control_plane_kit_operations.read_services import InstanceReadService, ReadModelError
from control_plane_kit_operations.read_pages import (
    PlanReadScope,
    ReadCollection,
    ReadPageError,
    ReadPageRequest,
    ReadScope,
    RunReadScope,
    SessionReadScope,
    WorkspaceReadScope,
    read_cursor_from_mapping,
)
from control_plane_kit_operations.records import ApprovalDecisionKind
from control_plane_kit_operations.runtime_authorities import (
    LocalDockerSocketAuthority,
    RegisterRuntimeAuthorityCommand,
    RegisterRuntimeAuthorityDeliveryCommand,
    RegisteredRuntimeAuthority,
    RegisteredRuntimeAuthorityDelivery,
    RemoteDockerTlsAuthority,
    RevokeRuntimeAuthorityCommand,
    RevokeRuntimeAuthorityDeliveryCommand,
    RuntimeAuthorityAuthorizationDenied,
    RuntimeAuthorityRegistrationError,
    RuntimeAuthorityRegistrationService,
)
from control_plane_kit_operations.secret_providers import (
    RegisterSecretProviderCommand,
    RegisterSecretReferenceCommand,
    RevokeSecretProviderCommand,
    RevokeSecretReferenceCommand,
    SecretProviderAuthorizationDenied,
    SecretProviderKind,
    SecretProviderNotFound,
    SecretProviderRegistrationConflict,
    SecretProviderRegistrationError,
    SecretProviderRegistrationService,
)
from control_plane_kit_operations.workflows import (
    CancelOperationSession,
    CloseOperationSession,
    IdempotencyKey,
    OperationCommandService,
    RecordOperationAction,
    StartOperationSession,
)
from control_plane_kit_operations.workspaces import CreateWorkspace, WorkspaceCommandService


class CpkServerRouteRequest(Protocol):
    """Route-shaped request supplied by the cpk-server HTTP/MCP wrapper."""

    surface: str
    route_id: str
    service_role: ControlPlaneServiceRole
    path_parameters: Mapping[str, str]
    payload: Mapping[str, object]
    principal: AuthenticatedPrincipal


class CpkServerApplicationService(Protocol):
    """One operation service exposed to a process adapter."""

    def handle(self, request: CpkServerRouteRequest) -> Mapping[str, object]:
        ...


class CpkServerApplicationError(RuntimeError):
    """Bounded process-adapter error raised by operations services."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        if type(status) is not int or not 400 <= status <= 599:
            raise ValueError("application error status must be a 4xx or 5xx integer")
        if not isinstance(message, str) or not message:
            raise ValueError("application error message must be non-empty text")
        self.status = status
        self.message = message

    def descriptor(self) -> dict[str, object]:
        return {
            "error": {
                "status": self.status,
                "message": self.message,
            }
        }


@dataclass(frozen=True)
class RouteAuthorizationPolicy:
    """Closed public-route permission and principal-kind requirements."""

    required_scopes: tuple[PolicyScope, ...] = ()
    any_scopes: tuple[PolicyScope, ...] = ()
    principal_kinds: tuple[PrincipalKind, ...] = (PrincipalKind.OPERATOR,)

    def authorize(self, context: TrustedCommandContext) -> None:
        if context.principal.identity.kind not in self.principal_kinds:
            raise CpkServerApplicationError(403, "principal kind is not authorized")
        missing = tuple(
            scope for scope in self.required_scopes if scope not in context.granted_scopes
        )
        if missing:
            raise CpkServerApplicationError(
                403,
                f"missing required scope {missing[0].value!r}",
            )
        if self.any_scopes and not any(
            scope in context.granted_scopes for scope in self.any_scopes
        ):
            names = ", ".join(scope.value for scope in self.any_scopes)
            raise CpkServerApplicationError(
                403,
                f"missing one required scope from {names}",
            )


_WORKSPACE_READ = RouteAuthorizationPolicy(
    required_scopes=(PolicyScope.INSTANCE_WORKSPACE_READ,)
)
_WORKSPACE_EDIT = RouteAuthorizationPolicy(
    required_scopes=(PolicyScope.INSTANCE_WORKSPACE_EDIT,)
)
_WORKER_OPERATION = RouteAuthorizationPolicy(
    required_scopes=(PolicyScope.EXECUTION_OPERATE,),
    principal_kinds=(PrincipalKind.WORKER, PrincipalKind.SERVICE),
)

_ROUTE_AUTHORIZATION_POLICIES: dict[str, RouteAuthorizationPolicy] = {
    "read.workspace": _WORKSPACE_READ,
    "read.current-graph": _WORKSPACE_READ,
    "read.desired-graph": _WORKSPACE_READ,
    "read.operator-graph": _WORKSPACE_READ,
    "read.activity": _WORKSPACE_READ,
    "read.sessions": _WORKSPACE_READ,
    "read.session-detail": _WORKSPACE_READ,
    "read.session-actions": _WORKSPACE_READ,
    "read.session-plans": _WORKSPACE_READ,
    "read.session-approvals": _WORKSPACE_READ,
    "read.run-events": _WORKSPACE_READ,
    "read.plan-runs": _WORKSPACE_READ,
    "read.plan-detail": _WORKSPACE_READ,
    "read.approval-detail": _WORKSPACE_READ,
    "read.pending-approvals": _WORKSPACE_READ,
    "read.observed-state": _WORKSPACE_READ,
    "read.control-surface": _WORKSPACE_READ,
    "read.runtime-authorities": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.RUNTIME_AUTHORITY_READ,)
    ),
    "read.runtime-authority-detail": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.RUNTIME_AUTHORITY_READ,)
    ),
    "read.runtime-authority-deliveries": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.RUNTIME_AUTHORITY_DELIVERY_READ,)
    ),
    "read.runtime-authority-delivery-detail": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.RUNTIME_AUTHORITY_DELIVERY_READ,)
    ),
    "read.ingress-authorities": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.INGRESS_AUTHORITY_READ,)
    ),
    "read.ingress-authority-detail": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.INGRESS_AUTHORITY_READ,)
    ),
    "read.secret-providers": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.SECRET_PROVIDER_READ,)
    ),
    "read.secret-provider-detail": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.SECRET_PROVIDER_READ,)
    ),
    "read.secret-references": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.SECRET_PROVIDER_READ,)
    ),
    "read.secret-reference-detail": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.SECRET_PROVIDER_READ,)
    ),
    "read.gateway-probe-timeline": _WORKSPACE_READ,
    "read.gateway-probe-detail": _WORKSPACE_READ,
    "read.delegation-keys": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.DELEGATION_KEY_READ,)
    ),
    "read.gateway-verifier-configuration": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.DELEGATION_KEY_READ,)
    ),
    "command.workspace.create": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.HUB_INSTANCE_CREATE,)
    ),
    "command.product.import": _WORKSPACE_EDIT,
    "command.image-pull-authority.register": _WORKSPACE_EDIT,
    "command.runtime-authority.register": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.RUNTIME_AUTHORITY_REGISTER,)
    ),
    "command.runtime-authority.revoke": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.RUNTIME_AUTHORITY_REVOKE,)
    ),
    "command.runtime-authority-delivery.register": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.RUNTIME_AUTHORITY_DELIVERY_REGISTER,)
    ),
    "command.runtime-authority-delivery.revoke": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.RUNTIME_AUTHORITY_DELIVERY_REVOKE,)
    ),
    "command.ingress-authority.register": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.INGRESS_AUTHORITY_REGISTER,)
    ),
    "command.ingress-authority.revoke": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.INGRESS_AUTHORITY_REVOKE,)
    ),
    "command.secret-provider.register": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)
    ),
    "command.secret-provider.revoke": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.SECRET_PROVIDER_REVOKE,)
    ),
    "command.secret-reference.register": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.SECRET_PROVIDER_REGISTER,)
    ),
    "command.secret-reference.revoke": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.SECRET_PROVIDER_REVOKE,)
    ),
    "command.gateway-probe.request": RouteAuthorizationPolicy(
        required_scopes=(
            PolicyScope.GATEWAY_PROBE_USE,
            PolicyScope.DELEGATION_KEY_USE,
            PolicyScope.SECRET_PROVIDER_USE,
        )
    ),
    "command.delegation-key.register": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.DELEGATION_KEY_REGISTER,)
    ),
    "command.delegation-key.activate": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.DELEGATION_KEY_ACTIVATE,)
    ),
    "command.delegation-key.retire": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.DELEGATION_KEY_RETIRE,)
    ),
    "command.delegation-key.revoke": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.DELEGATION_KEY_REVOKE,)
    ),
    "command.operation-session.start": _WORKSPACE_EDIT,
    "command.operation-session.close": _WORKSPACE_EDIT,
    "command.operation-session.cancel": _WORKSPACE_EDIT,
    "command.operation-session.record-action": _WORKSPACE_EDIT,
    "command.desired-graph.set": _WORKSPACE_EDIT,
    "command.deployment.plan": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.PLAN_REQUEST,)
    ),
    "command.approval.request": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.PLAN_REQUEST,)
    ),
    "command.approval.decide": RouteAuthorizationPolicy(
        any_scopes=(
            PolicyScope.PLAN_APPROVE,
            PolicyScope.PLAN_APPROVE_DESTRUCTIVE,
        )
    ),
    "command.deployment.admit": RouteAuthorizationPolicy(
        required_scopes=(PolicyScope.PLAN_EXECUTE,)
    ),
    "command.run.claim": _WORKER_OPERATION,
    "command.run.start": _WORKER_OPERATION,
    "command.deployment.execute": _WORKER_OPERATION,
    "command.graph.advance-current": _WORKER_OPERATION,
    "command.recovery.decide": _WORKER_OPERATION,
}


@dataclass(frozen=True)
class CpkServerOperationsApplication:
    """Service map consumed by cpk-server's shared HTTP/MCP boundary."""

    services: Mapping[ControlPlaneServiceRole, CpkServerApplicationService]

    def __post_init__(self) -> None:
        missing = tuple(role for role in ControlPlaneServiceRole if role not in self.services)
        if missing:
            names = ", ".join(role.value for role in missing)
            raise ValueError(f"missing cpk-server services: {names}")

    def handle(self, request: CpkServerRouteRequest) -> Mapping[str, object]:
        try:
            service = self.services[request.service_role]
        except KeyError as error:
            raise CpkServerApplicationError(
                404,
                f"unknown service role {request.service_role.value!r}",
            ) from error
        return service.handle(request)


class CpkServerReadService:
    """Read route interpreter over ``InstanceReadService`` and one request UoW."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        clock: Callable[[], object] | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock

    def handle(self, request: CpkServerRouteRequest) -> Mapping[str, object]:
        read_arguments = (
            _closed_read_arguments(request)
            if request.route_id in _CLOSED_READ_ARGUMENTS
            else None
        )
        _trusted_context(request, values=read_arguments)
        page_request = None
        if request.route_id in _PAGED_READ_COLLECTIONS:
            page_failure = False
            try:
                page_request = _read_page_request_for_route(
                    request.route_id,
                    read_arguments or {},
                )
            except ReadPageError:
                page_failure = True
            if page_failure:
                raise CpkServerApplicationError(400, "read page request is malformed")
        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            kwargs: dict[str, object] = {
                "workspace_store": stores.workspaces,
                "graph_topology_store": stores.graphs,
                "activity_history_store": stores.activity_history,
                "execution_store": stores.execution,
                "observed_state_store": stores.observed_state,
                "runtime_authority_store": stores.runtime_authorities,
                "runtime_authority_delivery_store": (
                    stores.runtime_authority_deliveries
                ),
                "ingress_authority_store": stores.ingress_authorities,
                "secret_provider_store": stores.secret_providers,
                "secret_reference_store": stores.secret_references,
                "gateway_probe_store": stores.gateway_probes,
                "delegation_signing_key_store": stores.delegation_signing_keys,
            }
            if self._clock is not None:
                kwargs["clock"] = self._clock
            service = InstanceReadService(**kwargs)
            failure: tuple[int, str] | None = None
            try:
                model = _read_model(
                    service,
                    request,
                    arguments=read_arguments,
                    page_request=page_request,
                )
            except (ReadModelError, ReadPageError) as error:
                status = 400 if isinstance(error, ReadPageError) else _read_error_status(error)
                failure = (status, str(error))
            if failure is not None:
                raise CpkServerApplicationError(*failure)
            unit_of_work.commit()
            return model.descriptor()


class CpkServerPlanningService:
    def __init__(
        self,
        service: ActivityPlanningCommandService,
        *,
        workspaces: WorkspaceCommandService | None = None,
        products: ProductRegistrationService | None = None,
        image_pull_authorities: ImagePullAuthorityRegistrationService | None = None,
        runtime_authorities: RuntimeAuthorityRegistrationService | None = None,
        ingress_authorities: IngressAuthorityRegistrationService | None = None,
        secret_providers: SecretProviderRegistrationService | None = None,
        delegation_signing_keys: DelegationSigningKeyRegistrationService | None = None,
        desired_graphs: DesiredGraphCommandService | None = None,
    ) -> None:
        self._service = service
        self._workspaces = workspaces
        self._products = products
        self._image_pull_authorities = image_pull_authorities
        self._runtime_authorities = runtime_authorities
        self._ingress_authorities = ingress_authorities
        self._secret_providers = secret_providers
        self._delegation_signing_keys = delegation_signing_keys
        self._desired_graphs = desired_graphs

    def handle(self, request: CpkServerRouteRequest) -> Mapping[str, object]:
        context = _trusted_context(request)
        if request.route_id == "command.workspace.create":
            if self._workspaces is None:
                raise _service_not_configured(request)
            payload = _arguments(request)
            result = self._workspaces.create(
                CreateWorkspace(
                    workspace_id=_text(payload, "workspace_id"),
                    name=_text(payload, "name"),
                    actor_id=context.actor_id,
                    idempotency_key=IdempotencyKey(_text(payload, "idempotency_key")),
                    metadata=_string_mapping(payload, "metadata", default={}),
                )
            )
            return result.descriptor()
        if request.route_id == "command.product.import":
            if self._products is None:
                raise _service_not_configured(request)
            payload = _arguments(request)
            _text(payload, "idempotency_key")
            try:
                document = ProductDescriptorCodec().decode_document(
                    _mapping(payload, "descriptor_document")
                )
                raw_source = payload.get("source")
                source = (
                    InlineDescriptorSource()
                    if raw_source is None
                    else DescriptorSourceCodec().decode(_mapping(payload, "source"))
                )
            except (ProductDescriptorError, ValueError) as error:
                raise CpkServerApplicationError(400, str(error)) from error
            result = self._products.import_descriptor(
                ImportProductDescriptorCommand(
                    workspace_id=_workspace_id(payload),
                    descriptor_document=document,
                    source=source,
                    imported_by=context.actor_id,
                    imported_at=_text(payload, "imported_at"),
                )
            )
            return _registered_product_descriptor(result)
        if request.route_id == "command.image-pull-authority.register":
            if self._image_pull_authorities is None:
                raise _service_not_configured(request)
            payload = _arguments(request)
            _text(payload, "idempotency_key")
            try:
                authority = ImagePullAuthority(
                    registry=_text(payload, "registry"),
                    repository=_optional_text(payload, "repository"),
                    credential_reference=_text(payload, "credential_reference"),
                )
            except (TypeError, ValueError) as error:
                raise CpkServerApplicationError(400, str(error)) from error
            result = self._image_pull_authorities.register(
                RegisterImagePullAuthorityCommand(
                    workspace_id=_workspace_id(payload),
                    authority=authority,
                    admitted_by=context.actor_id,
                    admitted_at=_text(payload, "admitted_at"),
                )
            )
            return _registered_image_pull_authority_descriptor(result)
        if request.route_id == "command.runtime-authority.register":
            if self._runtime_authorities is None:
                raise _service_not_configured(request)
            payload = _arguments(request)
            _text(payload, "idempotency_key")
            try:
                result = self._runtime_authorities.register(
                    RegisterRuntimeAuthorityCommand(
                        workspace_id=_workspace_id(payload),
                        authority_ref=RuntimeAuthorityReference(
                            _text(payload, "authority_ref")
                        ),
                        runtime_kind=RuntimeKind(_text(payload, "runtime_kind")),
                        authority=_runtime_authority(payload),
                        admitted_by=context.actor_id,
                        admitted_at=_text(payload, "admitted_at"),
                        actor_scopes=context.granted_scopes,
                    )
                )
            except RuntimeAuthorityAuthorizationDenied as error:
                raise CpkServerApplicationError(403, str(error)) from error
            except (ValueError, RuntimeAuthorityRegistrationError) as error:
                raise CpkServerApplicationError(400, str(error)) from error
            return _registered_runtime_authority_descriptor(result)
        if request.route_id == "command.runtime-authority.revoke":
            if self._runtime_authorities is None:
                raise _service_not_configured(request)
            payload = _arguments(request)
            _text(payload, "idempotency_key")
            try:
                result = self._runtime_authorities.revoke(
                    RevokeRuntimeAuthorityCommand(
                        workspace_id=_workspace_id(payload),
                        authority_ref=RuntimeAuthorityReference(
                            _path_or_payload(payload, "authority_ref", "authority_ref")
                        ),
                        actor_scopes=context.granted_scopes,
                    )
                )
            except RuntimeAuthorityAuthorizationDenied as error:
                raise CpkServerApplicationError(403, str(error)) from error
            except (ValueError, RuntimeAuthorityRegistrationError) as error:
                raise CpkServerApplicationError(400, str(error)) from error
            return _registered_runtime_authority_descriptor(result)
        if request.route_id == "command.runtime-authority-delivery.register":
            if self._runtime_authorities is None:
                raise _service_not_configured(request)
            payload = _arguments(request)
            _text(payload, "idempotency_key")
            try:
                result = self._runtime_authorities.register_delivery(
                    RegisterRuntimeAuthorityDeliveryCommand(
                        workspace_id=_workspace_id(payload),
                        delivery=RuntimeAuthorityAccessDeliveryCodec().decode(
                            _mapping(payload, "delivery")
                        ),
                        admitted_by=context.actor_id,
                        admitted_at=_text(payload, "admitted_at"),
                        actor_scopes=context.granted_scopes,
                    )
                )
            except RuntimeAuthorityAuthorizationDenied as error:
                raise CpkServerApplicationError(403, str(error)) from error
            except (ValueError, RuntimeAuthorityRegistrationError) as error:
                raise CpkServerApplicationError(400, str(error)) from error
            return _registered_runtime_authority_delivery_descriptor(result)
        if request.route_id == "command.runtime-authority-delivery.revoke":
            if self._runtime_authorities is None:
                raise _service_not_configured(request)
            payload = _arguments(request)
            _text(payload, "idempotency_key")
            try:
                result = self._runtime_authorities.revoke_delivery(
                    RevokeRuntimeAuthorityDeliveryCommand(
                        workspace_id=_workspace_id(payload),
                        authority_ref=RuntimeAuthorityReference(
                            _path_or_payload(payload, "authority_ref", "authority_ref")
                        ),
                        actor_scopes=context.granted_scopes,
                    )
                )
            except RuntimeAuthorityAuthorizationDenied as error:
                raise CpkServerApplicationError(403, str(error)) from error
            except (ValueError, RuntimeAuthorityRegistrationError) as error:
                raise CpkServerApplicationError(400, str(error)) from error
            return _registered_runtime_authority_delivery_descriptor(result)
        if request.route_id == "command.ingress-authority.register":
            if self._ingress_authorities is None:
                raise _service_not_configured(request)
            payload = _arguments(request)
            _text(payload, "idempotency_key")
            try:
                result = self._ingress_authorities.register(
                    RegisterIngressAuthorityCommand(
                        workspace_id=_workspace_id(payload),
                        authority_ref=IngressAuthorityReference(
                            _text(payload, "authority_ref")
                        ),
                        authority=_ingress_authority(payload),
                        admitted_by=context.actor_id,
                        admitted_at=_text(payload, "admitted_at"),
                        actor_scopes=context.granted_scopes,
                    )
                )
            except IngressAuthorityAuthorizationDenied as error:
                raise CpkServerApplicationError(403, str(error)) from error
            except (ValueError, IngressAuthorityRegistrationError) as error:
                raise CpkServerApplicationError(400, str(error)) from error
            return _registered_ingress_authority_descriptor(result)
        if request.route_id == "command.ingress-authority.revoke":
            if self._ingress_authorities is None:
                raise _service_not_configured(request)
            payload = _arguments(request)
            _text(payload, "idempotency_key")
            try:
                result = self._ingress_authorities.revoke(
                    RevokeIngressAuthorityCommand(
                        workspace_id=_workspace_id(payload),
                        authority_ref=IngressAuthorityReference(
                            _path_or_payload(payload, "authority_ref", "authority_ref")
                        ),
                        actor_scopes=context.granted_scopes,
                    )
                )
            except IngressAuthorityAuthorizationDenied as error:
                raise CpkServerApplicationError(403, str(error)) from error
            except (ValueError, IngressAuthorityRegistrationError) as error:
                raise CpkServerApplicationError(400, str(error)) from error
            return _registered_ingress_authority_descriptor(result)
        if request.route_id.startswith(
            (
                "command.secret-provider.",
                "command.secret-reference.",
            )
        ):
            if self._secret_providers is None:
                raise _service_not_configured(request)
            return _handle_secret_provider_command(
                self._secret_providers,
                request,
                context,
            )
        if request.route_id.startswith("command.delegation-key."):
            if self._delegation_signing_keys is None:
                raise _service_not_configured(request)
            return _handle_delegation_signing_key_command(
                self._delegation_signing_keys,
                request,
                context,
            )
        if request.route_id == "command.desired-graph.set":
            if self._desired_graphs is None:
                raise _service_not_configured(request)
            payload = _arguments(request)
            try:
                graph = DEFAULT_GRAPH_CODEC.decode(_mapping(payload, "graph"))
            except GraphDescriptorError as error:
                raise CpkServerApplicationError(400, str(error)) from error
            result = self._desired_graphs.execute(
                SetDesiredGraph(
                    session_id=_text(payload, "session_id"),
                    workspace_id=_workspace_id(payload),
                    actor_id=context.actor_id,
                    graph=graph,
                    expected_desired_graph_id=_optional_text(
                        payload,
                        "expected_desired_graph_id",
                    ),
                    idempotency_key=IdempotencyKey(_text(payload, "idempotency_key")),
                    expected_desired_realized_projection_id=_optional_text(
                        payload,
                        "expected_desired_realized_projection_id",
                    ),
                    expected_desired_graph_revision=(
                        _optional_nonnegative_integer(
                            payload,
                            "expected_desired_graph_revision",
                        )
                        or 0
                    ),
                )
            )
            return result.descriptor()
        if request.route_id != "command.deployment.plan":
            raise _unsupported_route(request)
        payload = _arguments(request)
        result = self._service.execute(
            RequestActivityPlan(
                session_id=_text(payload, "session_id"),
                workspace_id=_workspace_id(payload),
                actor_id=context.actor_id,
                expected_current_graph_id=_text(payload, "expected_current_graph_id"),
                expected_desired_graph_id=_text(payload, "expected_desired_graph_id"),
                idempotency_key=IdempotencyKey(_text(payload, "idempotency_key")),
                expected_current_realized_projection_id=_optional_text(
                    payload,
                    "expected_current_realized_projection_id",
                ),
                expected_desired_realized_projection_id=_optional_text(
                    payload,
                    "expected_desired_realized_projection_id",
                ),
                expected_desired_graph_revision=_optional_nonnegative_integer(
                    payload,
                    "expected_desired_graph_revision",
                ),
            )
        )
        return result.descriptor()


def _handle_secret_provider_command(
    service: SecretProviderRegistrationService,
    request: CpkServerRouteRequest,
    context: TrustedCommandContext,
) -> Mapping[str, object]:
    """Decode secret-free lifecycle metadata into operations commands."""

    payload = _arguments(request)
    _text(payload, "idempotency_key")
    try:
        if request.route_id == "command.secret-provider.register":
            result = service.register_provider(
                RegisterSecretProviderCommand(
                    workspace_id=_workspace_id(payload),
                    provider_id=SecretProviderId(_text(payload, "provider_id")),
                    provider_kind=SecretProviderKind(
                        _text(payload, "provider_kind")
                    ),
                    display_name=_text(payload, "display_name"),
                    endpoint_reference=SecretProviderEndpointReference(
                        _text(payload, "endpoint_reference")
                    ),
                    credential_reference=SecretReference(
                        _text(payload, "credential_reference")
                    ),
                    allowed_reference_prefixes=tuple(
                        SecretReference(value)
                        for value in _text_tuple(
                            payload,
                            "allowed_reference_prefixes",
                        )
                    ),
                    allowed_intents=tuple(
                        SecretUseIntent(value)
                        for value in _text_tuple(payload, "allowed_intents")
                    ),
                    admitted_by=context.actor_id,
                    admitted_at=_text(payload, "admitted_at"),
                    actor_scopes=context.granted_scopes,
                    supersedes_registration_id=_optional_text(
                        payload,
                        "supersedes_registration_id",
                    ),
                    metadata=_mapping(payload, "metadata", default={}),
                )
            )
        elif request.route_id == "command.secret-provider.revoke":
            result = service.revoke_provider(
                RevokeSecretProviderCommand(
                    workspace_id=_workspace_id(payload),
                    provider_id=SecretProviderId(
                        _path_or_payload(
                            payload,
                            "provider_id",
                            "provider_id",
                        )
                    ),
                    revoked_by=context.actor_id,
                    revoked_at=_text(payload, "revoked_at"),
                    actor_scopes=context.granted_scopes,
                )
            )
        elif request.route_id == "command.secret-reference.register":
            result = service.register_reference(
                RegisterSecretReferenceCommand(
                    workspace_id=_workspace_id(payload),
                    reference=SecretReference(_text(payload, "reference")),
                    provider_registration_id=_text(
                        payload,
                        "provider_registration_id",
                    ),
                    allowed_intents=tuple(
                        SecretUseIntent(value)
                        for value in _text_tuple(payload, "allowed_intents")
                    ),
                    admitted_by=context.actor_id,
                    admitted_at=_text(payload, "admitted_at"),
                    actor_scopes=context.granted_scopes,
                    supersedes_registration_id=_optional_text(
                        payload,
                        "supersedes_registration_id",
                    ),
                    metadata=_mapping(payload, "metadata", default={}),
                )
            )
        elif request.route_id == "command.secret-reference.revoke":
            result = service.revoke_reference(
                RevokeSecretReferenceCommand(
                    workspace_id=_workspace_id(payload),
                    registration_id=_path_or_payload(
                        payload,
                        "registration_id",
                        "registration_id",
                    ),
                    revoked_by=context.actor_id,
                    revoked_at=_text(payload, "revoked_at"),
                    actor_scopes=context.granted_scopes,
                )
            )
        else:
            raise _unsupported_route(request)
    except SecretProviderAuthorizationDenied as error:
        raise CpkServerApplicationError(403, str(error)) from error
    except SecretProviderNotFound as error:
        raise CpkServerApplicationError(
            404,
            "secret provider metadata was not found",
        ) from error
    except SecretProviderRegistrationConflict as error:
        raise CpkServerApplicationError(409, str(error)) from error
    except (SecretProviderRegistrationError, TypeError, ValueError) as error:
        raise CpkServerApplicationError(400, str(error)) from error
    return result.descriptor()


def _handle_delegation_signing_key_command(
    service: DelegationSigningKeyRegistrationService,
    request: CpkServerRouteRequest,
    context: TrustedCommandContext,
) -> Mapping[str, object]:
    """Decode public key metadata and secret references into lifecycle commands."""

    payload = _arguments(request)
    _text(payload, "idempotency_key")
    try:
        purpose = DelegationKeyPurpose(
            _optional_text(payload, "purpose")
            or DelegationKeyPurpose.GATEWAY_PROBE.value
        )
        issuer = _path_or_payload(payload, "issuer", "issuer")
        key_id = _path_or_payload(payload, "key_id", "key_id")
        if request.route_id == "command.delegation-key.register":
            result = service.register(
                RegisterDelegationSigningKeyCommand(
                    workspace_id=_workspace_id(payload),
                    purpose=purpose,
                    issuer=issuer,
                    public_key=DelegationPublicKey(
                        key_id=key_id,
                        algorithm=DelegationKeyAlgorithm(
                            _optional_text(payload, "algorithm")
                            or DelegationKeyAlgorithm.ED25519.value
                        ),
                        public_key_pem=_text(payload, "public_key_pem"),
                    ),
                    private_key_reference=SecretReference(
                        _text(payload, "private_key_reference")
                    ),
                    admitted_by=context.actor_id,
                    admitted_at=_text(payload, "admitted_at"),
                    actor_scopes=context.granted_scopes,
                )
            )
        elif request.route_id == "command.delegation-key.activate":
            result = service.activate(
                ActivateDelegationSigningKeyCommand(
                    workspace_id=_workspace_id(payload),
                    purpose=purpose,
                    issuer=issuer,
                    key_id=key_id,
                    activated_by=context.actor_id,
                    activated_at=_text(payload, "activated_at"),
                    actor_scopes=context.granted_scopes,
                )
            )
        elif request.route_id == "command.delegation-key.retire":
            result = service.retire(
                RetireDelegationSigningKeyCommand(
                    workspace_id=_workspace_id(payload),
                    purpose=purpose,
                    issuer=issuer,
                    key_id=key_id,
                    retired_by=context.actor_id,
                    retired_at=_text(payload, "retired_at"),
                    actor_scopes=context.granted_scopes,
                )
            )
        elif request.route_id == "command.delegation-key.revoke":
            result = service.revoke(
                RevokeDelegationSigningKeyCommand(
                    workspace_id=_workspace_id(payload),
                    purpose=purpose,
                    issuer=issuer,
                    key_id=key_id,
                    revoked_by=context.actor_id,
                    revoked_at=_text(payload, "revoked_at"),
                    actor_scopes=context.granted_scopes,
                )
            )
        else:
            raise _unsupported_route(request)
    except DelegationSigningKeyAuthorizationDenied as error:
        raise CpkServerApplicationError(403, str(error)) from error
    except DelegationSigningKeyNotFound as error:
        raise CpkServerApplicationError(404, "delegation signing key was not found") from error
    except DelegationSigningKeyConflict as error:
        raise CpkServerApplicationError(409, str(error)) from error
    except (DelegationSigningKeyError, TypeError, ValueError) as error:
        raise CpkServerApplicationError(400, str(error)) from error
    return result.descriptor()


class CpkServerApprovalService:
    def __init__(self, service: ApprovalCommandService) -> None:
        self._service = service

    def handle(self, request: CpkServerRouteRequest) -> Mapping[str, object]:
        context = _trusted_context(request)
        if request.route_id == "command.approval.request":
            payload = _arguments(request)
            result = self._service.execute(
                RequestApproval(
                    session_id=_text(payload, "session_id"),
                    plan_id=_path_or_payload(payload, "plan_id", "plan_id"),
                    actor_id=context.actor_id,
                    actor_scopes=context.granted_scopes,
                    idempotency_key=IdempotencyKey(_text(payload, "idempotency_key")),
                    comment=_optional_text(payload, "comment"),
                )
            )
            return result.descriptor()
        if request.route_id != "command.approval.decide":
            raise _unsupported_route(request)
        payload = _arguments(request)
        result = self._service.execute(
            DecideApproval(
                session_id=_text(payload, "session_id"),
                request_id=_path_or_payload(payload, "approval_id", "request_id"),
                actor_id=context.actor_id,
                actor_scopes=context.granted_scopes,
                decision=ApprovalDecisionKind(_text(payload, "decision")),
                idempotency_key=IdempotencyKey(_text(payload, "idempotency_key")),
                comment=_optional_text(payload, "comment"),
            )
        )
        return result.descriptor()


class CpkServerAdmissionService:
    def __init__(self, service: ExecutionAdmissionCommandService) -> None:
        self._service = service

    def handle(self, request: CpkServerRouteRequest) -> Mapping[str, object]:
        context = _trusted_context(request)
        if request.route_id != "command.deployment.admit":
            raise _unsupported_route(request)
        payload = _arguments(request)
        result = self._service.execute(
            RequestPlanExecution(
                workspace_id=_workspace_id(payload),
                session_id=_text(payload, "session_id"),
                plan_id=_path_or_payload(payload, "plan_id", "plan_id"),
                approval_request_id=_text(payload, "approval_request_id"),
                actor_id=context.actor_id,
                actor_scopes=context.granted_scopes,
                idempotency_key=IdempotencyKey(_text(payload, "idempotency_key")),
                readiness=_readiness(payload),
            )
        )
        return result.descriptor()


class CpkServerLifecycleService:
    def __init__(
        self,
        service: RunLifecycleCommandService,
        *,
        operations: OperationCommandService | None = None,
        advancement: CurrentGraphAdvancementCommandService | None = None,
    ) -> None:
        self._service = service
        self._operations = operations
        self._advancement = advancement

    def handle(self, request: CpkServerRouteRequest) -> Mapping[str, object]:
        context = _trusted_context(request)
        if request.route_id.startswith("command.operation-session."):
            if self._operations is None:
                raise _service_not_configured(request)
            payload = _arguments(request)
            if request.route_id == "command.operation-session.start":
                result = self._operations.execute(
                    StartOperationSession(
                        workspace_id=_workspace_id(payload),
                        actor_id=context.actor_id,
                        title=_text(payload, "title"),
                        idempotency_key=IdempotencyKey(
                            _text(payload, "idempotency_key")
                        ),
                        metadata=_string_mapping(payload, "metadata", default={}),
                    )
                )
                return result.descriptor()
            if request.route_id == "command.operation-session.close":
                result = self._operations.execute(
                    CloseOperationSession(
                        session_id=_path_or_payload(payload, "session_id", "session_id"),
                        actor_id=context.actor_id,
                        idempotency_key=IdempotencyKey(
                            _text(payload, "idempotency_key")
                        ),
                    )
                )
                return result.descriptor()
            if request.route_id == "command.operation-session.cancel":
                result = self._operations.execute(
                    CancelOperationSession(
                        session_id=_path_or_payload(payload, "session_id", "session_id"),
                        actor_id=context.actor_id,
                        idempotency_key=IdempotencyKey(
                            _text(payload, "idempotency_key")
                        ),
                    )
                )
                return result.descriptor()
            if request.route_id == "command.operation-session.record-action":
                try:
                    action_type = OperatorCommandKind(_text(payload, "action_type"))
                except ValueError as error:
                    raise CpkServerApplicationError(400, "unknown action_type") from error
                result = self._operations.execute(
                    RecordOperationAction(
                        session_id=_path_or_payload(payload, "session_id", "session_id"),
                        actor_id=context.actor_id,
                        action_type=action_type,
                        idempotency_key=IdempotencyKey(
                            _text(payload, "idempotency_key")
                        ),
                        payload=_mapping(payload, "payload", default={}),
                    )
                )
                return result.descriptor()
        if request.route_id == "command.graph.advance-current":
            if self._advancement is None:
                raise _service_not_configured(request)
            payload = _arguments(request)
            result = self._advancement.execute(
                AdvanceCurrentGraph(
                    workspace_id=_workspace_id(payload),
                    run_id=_path_or_payload(payload, "run_id", "run_id"),
                    plan_id=_text(payload, "plan_id"),
                    expected_current_graph_id=_text(
                        payload,
                        "expected_current_graph_id",
                    ),
                    expected_current_realized_projection_id=_text(
                        payload,
                        "expected_current_realized_projection_id",
                    ),
                    desired_graph_id=_text(payload, "desired_graph_id"),
                    desired_realized_projection_id=_text(
                        payload,
                        "desired_realized_projection_id",
                    ),
                    expected_desired_graph_revision=_nonnegative_integer(
                        payload,
                        "expected_desired_graph_revision",
                    ),
                    authority=_worker_authority(context),
                    idempotency_key=IdempotencyKey(_text(payload, "idempotency_key")),
                )
            )
            return result.descriptor()
        if request.route_id != "command.run.claim":
            raise _unsupported_route(request)
        payload = _arguments(request)
        result = self._service.execute(
            ClaimAndOpenActivityRun(
                request_id=_path_or_payload(payload, "run_id", "request_id"),
                authority=_worker_authority(context),
                lease_expires_at=_text(payload, "lease_expires_at"),
                idempotency_key=IdempotencyKey(_text(payload, "idempotency_key")),
            )
        )
        return result.descriptor()


class CpkServerExecutionService:
    def __init__(
        self,
        service: ExecutionCoordinator,
        *,
        lifecycle: RunLifecycleCommandService | None = None,
    ) -> None:
        self._service = service
        self._lifecycle = lifecycle

    def handle(self, request: CpkServerRouteRequest) -> Mapping[str, object]:
        context = _trusted_context(request)
        if request.route_id == "command.run.start":
            if self._lifecycle is None:
                raise _service_not_configured(request)
            payload = _arguments(request)
            result = self._lifecycle.execute(
                StartActivityRun(
                    run_id=_path_or_payload(payload, "run_id", "run_id"),
                    authority=_worker_authority(context),
                    idempotency_key=IdempotencyKey(_text(payload, "idempotency_key")),
                )
            )
            return result.descriptor()
        if request.route_id != "command.deployment.execute":
            raise _unsupported_route(request)
        payload = _arguments(request)
        result = self._service.execute(
            ExecuteActivityRun(
                run_id=_path_or_payload(payload, "run_id", "run_id"),
                authority=_worker_authority(context),
                idempotency_key=IdempotencyKey(_text(payload, "idempotency_key")),
                max_effects=_positive_int(payload, "max_effects", default=1),
            )
        )
        return result.descriptor()


class CpkServerGatewayProbeService:
    """Public route adapter for operations-owned delegated gateway probes."""

    def __init__(self, service: GatewayProbeCommandService) -> None:
        self._service = service

    def handle(self, request: CpkServerRouteRequest) -> Mapping[str, object]:
        if request.route_id != "command.gateway-probe.request":
            raise _unsupported_route(request)
        context = _trusted_context(request)
        payload = _arguments(request)
        raw_kind = _text(payload, "kind")
        try:
            kind = GatewayProbeCommandKind(raw_kind)
            result = self._service.execute(
                RequestGatewayProbe(
                    context=context,
                    request_id=_text(payload, "request_id"),
                    expected_current_graph_id=_text(
                        payload,
                        "expected_current_graph_id",
                    ),
                    gateway_node_id=_path_or_payload(
                        payload,
                        "gateway_node_id",
                        "gateway_node_id",
                    ),
                    request=GatewayProbeRequest(
                        kind=kind,
                        target_id=GatewayTargetId(_text(payload, "target_id")),
                        path=_optional_text(payload, "path"),
                    ),
                    access_path=GatewayProbeAccessPath(
                        _optional_text(payload, "access_path")
                        or GatewayProbeAccessPath.RUNTIME_PRIVATE.value
                    ),
                )
            )
        except GatewayProbeAuthorizationDenied as error:
            raise CpkServerApplicationError(403, str(error)) from error
        except GatewayProbeNotFound as error:
            raise CpkServerApplicationError(404, str(error)) from error
        except GatewayProbeConflict as error:
            raise CpkServerApplicationError(409, str(error)) from error
        except (GatewayProbeError, TypeError, ValueError) as error:
            raise CpkServerApplicationError(400, str(error)) from error
        return result.descriptor()


class CpkServerUnsupportedService:
    """Explicit placeholder for service roles not extracted into operations yet."""

    def __init__(self, role: ControlPlaneServiceRole) -> None:
        self._role = role

    def handle(self, request: CpkServerRouteRequest) -> Mapping[str, object]:
        _trusted_context(request)
        raise CpkServerApplicationError(
            501,
            f"{self._role.value} service is not implemented in operations yet",
        )


def cpk_server_services(
    *,
    unit_of_work_factory: Callable[[], Any],
    planning: ActivityPlanningCommandService,
    approval: ApprovalCommandService,
    admission: ExecutionAdmissionCommandService,
    lifecycle: RunLifecycleCommandService,
    execution: ExecutionCoordinator,
    workspaces: WorkspaceCommandService | None = None,
    products: ProductRegistrationService | None = None,
    image_pull_authorities: ImagePullAuthorityRegistrationService | None = None,
    runtime_authorities: RuntimeAuthorityRegistrationService | None = None,
    ingress_authorities: IngressAuthorityRegistrationService | None = None,
    secret_providers: SecretProviderRegistrationService | None = None,
    delegation_signing_keys: DelegationSigningKeyRegistrationService | None = None,
    desired_graphs: DesiredGraphCommandService | None = None,
    operations: OperationCommandService | None = None,
    advancement: CurrentGraphAdvancementCommandService | None = None,
    gateway_probes: GatewayProbeCommandService | None = None,
    clock: Callable[[], object] | None = None,
) -> Mapping[ControlPlaneServiceRole, CpkServerApplicationService]:
    """Return the complete service map required by cpk-server composition."""

    unsupported = {
        role: CpkServerUnsupportedService(role)
        for role in (
            ControlPlaneServiceRole.RECOVERY,
            ControlPlaneServiceRole.AUTHORIZATION,
        )
    }
    return {
        ControlPlaneServiceRole.PLANNING: CpkServerPlanningService(
            planning,
            workspaces=workspaces,
            products=products,
            image_pull_authorities=image_pull_authorities,
            runtime_authorities=runtime_authorities,
            ingress_authorities=ingress_authorities,
            secret_providers=secret_providers,
            delegation_signing_keys=delegation_signing_keys,
            desired_graphs=desired_graphs,
        ),
        ControlPlaneServiceRole.APPROVAL: CpkServerApprovalService(approval),
        ControlPlaneServiceRole.ADMISSION: CpkServerAdmissionService(admission),
        ControlPlaneServiceRole.LIFECYCLE: CpkServerLifecycleService(
            lifecycle,
            operations=operations,
            advancement=advancement,
        ),
        ControlPlaneServiceRole.EXECUTION: CpkServerExecutionService(
            execution,
            lifecycle=lifecycle,
        ),
        ControlPlaneServiceRole.READS: CpkServerReadService(
            unit_of_work_factory,
            clock=clock,
        ),
        ControlPlaneServiceRole.OBSERVATION: (
            CpkServerUnsupportedService(ControlPlaneServiceRole.OBSERVATION)
            if gateway_probes is None
            else CpkServerGatewayProbeService(gateway_probes)
        ),
        **unsupported,
    }


def _read_model(
    service: InstanceReadService,
    request: CpkServerRouteRequest,
    *,
    arguments: Mapping[str, object] | None = None,
    page_request: ReadPageRequest | None = None,
) -> Any:
    args = _arguments(request) if arguments is None else dict(arguments)
    route_id = request.route_id
    if route_id == "read.workspace":
        return service.workspace(_workspace_id(args))
    if route_id == "read.current-graph":
        return service.current_graph(_workspace_id(args))
    if route_id == "read.desired-graph":
        return service.desired_graph(_workspace_id(args))
    if route_id == "read.operator-graph":
        return service.operator_graph(
            _workspace_id(args),
            pointer=_optional_text(args, "pointer") or "current",
        )
    if route_id == "read.activity":
        return service.activity_sessions(
            _required_page_request(page_request, ReadCollection.ACTIVITY_SESSIONS)
        )
    if route_id == "read.sessions":
        return service.open_sessions(
            _required_page_request(page_request, ReadCollection.OPEN_SESSIONS)
        )
    if route_id == "read.session-detail":
        return service.session_detail(
            _workspace_id(args),
            _text(args, "session_id"),
        )
    if route_id == "read.session-actions":
        return service.session_actions(
            _required_page_request(page_request, ReadCollection.SESSION_ACTIONS)
        )
    if route_id == "read.session-plans":
        return service.session_plans(
            _required_page_request(page_request, ReadCollection.SESSION_PLANS)
        )
    if route_id == "read.session-approvals":
        return service.session_approvals(
            _required_page_request(page_request, ReadCollection.SESSION_APPROVALS)
        )
    if route_id == "read.run-events":
        return service.run_events(
            _required_page_request(page_request, ReadCollection.RUN_EVENTS)
        )
    if route_id == "read.plan-detail":
        return service.plan_detail(
            _workspace_id(args),
            _text(args, "plan_id"),
        )
    if route_id == "read.plan-runs":
        return service.plan_runs(
            _required_page_request(page_request, ReadCollection.PLAN_RUNS)
        )
    if route_id == "read.approval-detail":
        return service.approval_detail(
            _workspace_id(args),
            _text(args, "approval_id"),
        )
    if route_id == "read.pending-approvals":
        return service.pending_approvals(
            _required_page_request(page_request, ReadCollection.PENDING_APPROVALS)
        )
    if route_id == "read.observed-state":
        return service.observed_state(
            _required_page_request(page_request, ReadCollection.LATEST_OBSERVATIONS)
        )
    if route_id == "read.control-surface":
        return service.control_surface(
            _workspace_id(args),
            pointer=_optional_text(args, "pointer") or "current",
        )
    if route_id == "read.runtime-authorities":
        return service.runtime_authorities(
            _required_page_request(page_request, ReadCollection.RUNTIME_AUTHORITIES)
        )
    if route_id == "read.runtime-authority-detail":
        return service.runtime_authority_detail(
            _workspace_id(args),
            RuntimeAuthorityReference(
                _path_or_payload(args, "authority_ref", "authority_ref")
            ),
        )
    if route_id == "read.runtime-authority-deliveries":
        return service.runtime_authority_deliveries(
            _required_page_request(
                page_request,
                ReadCollection.RUNTIME_AUTHORITY_DELIVERIES,
            )
        )
    if route_id == "read.runtime-authority-delivery-detail":
        return service.runtime_authority_delivery_detail(
            _workspace_id(args),
            RuntimeAuthorityReference(
                _path_or_payload(args, "authority_ref", "authority_ref")
            ),
        )
    if route_id == "read.ingress-authorities":
        return service.ingress_authorities(
            _required_page_request(page_request, ReadCollection.INGRESS_AUTHORITIES)
        )
    if route_id == "read.ingress-authority-detail":
        return service.ingress_authority_detail(
            _workspace_id(args),
            IngressAuthorityReference(
                _path_or_payload(args, "authority_ref", "authority_ref")
            ),
        )
    if route_id == "read.secret-providers":
        return service.secret_providers(
            _required_page_request(page_request, ReadCollection.SECRET_PROVIDERS)
        )
    if route_id == "read.secret-provider-detail":
        return service.secret_provider_detail(
            _workspace_id(args),
            SecretProviderId(
                _path_or_payload(args, "provider_id", "provider_id")
            ),
        )
    if route_id == "read.secret-references":
        return service.secret_references(
            _required_page_request(page_request, ReadCollection.SECRET_REFERENCES)
        )
    if route_id == "read.secret-reference-detail":
        return service.secret_reference_detail(
            _workspace_id(args),
            _path_or_payload(
                args,
                "registration_id",
                "registration_id",
            ),
        )
    if route_id == "read.gateway-probe-timeline":
        return service.gateway_probe_timeline(
            _required_page_request(page_request, ReadCollection.GATEWAY_PROBES)
        )
    if route_id == "read.gateway-probe-detail":
        return service.gateway_probe_detail(
            _workspace_id(args),
            _text(args, "probe_id"),
        )
    if route_id == "read.delegation-keys":
        return service.delegation_signing_keys(
            _required_page_request(
                page_request,
                ReadCollection.DELEGATION_SIGNING_KEYS,
            )
        )
    if route_id == "read.gateway-verifier-configuration":
        return service.gateway_verifier_configuration(
            _workspace_id(args),
            _path_or_payload(args, "gateway_node_id", "gateway_node_id"),
        )
    raise _unsupported_route(request)


def _arguments(request: CpkServerRouteRequest) -> dict[str, object]:
    return {
        **dict(request.payload),
        **dict(request.path_parameters),
    }


_CLOSED_READ_ARGUMENTS = {
    "read.activity": (None, True),
    "read.sessions": (None, True),
    "read.session-actions": ("session_id", True),
    "read.session-plans": ("session_id", True),
    "read.session-approvals": ("session_id", True),
    "read.pending-approvals": (None, True),
    "read.plan-runs": ("plan_id", True),
    "read.run-events": ("run_id", True),
    "read.session-detail": ("session_id", False),
    "read.plan-detail": ("plan_id", False),
    "read.approval-detail": ("approval_id", False),
    "read.observed-state": (None, True),
    "read.runtime-authorities": (None, True),
    "read.runtime-authority-deliveries": (None, True),
    "read.ingress-authorities": (None, True),
    "read.secret-providers": (None, True),
    "read.secret-references": (None, True),
    "read.gateway-probe-timeline": (None, True),
    "read.delegation-keys": (None, True),
}

_PAGED_READ_COLLECTIONS = {
    "read.activity": ReadCollection.ACTIVITY_SESSIONS,
    "read.sessions": ReadCollection.OPEN_SESSIONS,
    "read.session-actions": ReadCollection.SESSION_ACTIONS,
    "read.session-plans": ReadCollection.SESSION_PLANS,
    "read.session-approvals": ReadCollection.SESSION_APPROVALS,
    "read.pending-approvals": ReadCollection.PENDING_APPROVALS,
    "read.plan-runs": ReadCollection.PLAN_RUNS,
    "read.run-events": ReadCollection.RUN_EVENTS,
    "read.observed-state": ReadCollection.LATEST_OBSERVATIONS,
    "read.runtime-authorities": ReadCollection.RUNTIME_AUTHORITIES,
    "read.runtime-authority-deliveries": ReadCollection.RUNTIME_AUTHORITY_DELIVERIES,
    "read.ingress-authorities": ReadCollection.INGRESS_AUTHORITIES,
    "read.secret-providers": ReadCollection.SECRET_PROVIDERS,
    "read.secret-references": ReadCollection.SECRET_REFERENCES,
    "read.gateway-probe-timeline": ReadCollection.GATEWAY_PROBES,
    "read.delegation-keys": ReadCollection.DELEGATION_SIGNING_KEYS,
}


def _closed_read_arguments(request: CpkServerRouteRequest) -> dict[str, object]:
    if type(request.path_parameters) is not dict or type(request.payload) is not dict:
        raise CpkServerApplicationError(400, "read arguments are malformed")
    parent, paged = _CLOSED_READ_ARGUMENTS[request.route_id]
    required = {"workspace_id"} | (set() if parent is None else {parent})
    optional = {"limit", "after"} if paged else set()
    path = dict(request.path_parameters)
    payload = dict(request.payload)
    if request.surface == "http":
        valid = set(path) == required and set(payload) <= optional
    elif request.surface == "mcp":
        valid = not path and required <= set(payload) <= required | optional
    else:
        valid = False
    if not valid or set(path) & set(payload):
        raise CpkServerApplicationError(400, "read arguments are malformed")
    return {**path, **payload}


def _read_page_request(
    values: Mapping[str, object],
    *,
    collection: ReadCollection,
    scope: ReadScope,
) -> ReadPageRequest:
    raw_cursor = values.get("after")
    cursor = None if raw_cursor is None else read_cursor_from_mapping(raw_cursor)
    return ReadPageRequest(
        collection,
        scope,
        _positive_int(values, "limit", default=50),
        cursor,
    )


def _read_page_request_for_route(
    route_id: str,
    values: Mapping[str, object],
) -> ReadPageRequest:
    collection = _PAGED_READ_COLLECTIONS[route_id]
    workspace_id = _workspace_id(values)
    if collection in {
        ReadCollection.SESSION_ACTIONS,
        ReadCollection.SESSION_PLANS,
        ReadCollection.SESSION_APPROVALS,
    }:
        scope: ReadScope = SessionReadScope(
            workspace_id,
            _text(values, "session_id"),
        )
    elif collection is ReadCollection.PLAN_RUNS:
        scope = PlanReadScope(workspace_id, _text(values, "plan_id"))
    elif collection is ReadCollection.RUN_EVENTS:
        scope = RunReadScope(workspace_id, _text(values, "run_id"))
    else:
        scope = WorkspaceReadScope(workspace_id)
    return _read_page_request(values, collection=collection, scope=scope)


def _required_page_request(
    request: ReadPageRequest | None,
    collection: ReadCollection,
) -> ReadPageRequest:
    if request is None or request.collection is not collection:
        raise ReadPageError("prepared read page request is incongruent")
    return request


def _trusted_context(
    request: CpkServerRouteRequest,
    *,
    values: Mapping[str, object] | None = None,
) -> TrustedCommandContext:
    values = _arguments(request) if values is None else values
    workspace_id = _workspace_id(values)
    principal = getattr(request, "principal", None)
    if not isinstance(principal, AuthenticatedPrincipal):
        raise CpkServerApplicationError(403, "authenticated principal is required")
    workspace_denied = False
    try:
        context = principal.command_context(workspace_id)
    except IdentityContractError:
        workspace_denied = True
    if workspace_denied:
        raise CpkServerApplicationError(403, "workspace access is denied")
    try:
        policy = _ROUTE_AUTHORIZATION_POLICIES[request.route_id]
    except KeyError as error:
        raise CpkServerApplicationError(404, f"unknown route {request.route_id!r}") from error
    policy.authorize(context)
    return context


def _workspace_id(values: Mapping[str, object]) -> str:
    return _text(values, "workspace_id")


def _path_or_payload(
    values: Mapping[str, object],
    path_name: str,
    payload_name: str,
) -> str:
    if path_name in values:
        return _text(values, path_name)
    return _text(values, payload_name)


def _mapping(
    values: Mapping[str, object],
    name: str,
    *,
    default: Mapping[str, object] | None = None,
) -> Mapping[str, object]:
    value = values.get(name, default)
    if not isinstance(value, Mapping):
        raise CpkServerApplicationError(400, f"{name} must be an object")
    return value


def _string_mapping(
    values: Mapping[str, object],
    name: str,
    *,
    default: Mapping[str, object],
) -> Mapping[str, str]:
    value = _mapping(values, name, default=default)
    if not all(
        isinstance(key, str) and isinstance(item, str)
        for key, item in value.items()
    ):
        raise CpkServerApplicationError(400, f"{name} must be an object of text values")
    return dict(value)


def _text(values: Mapping[str, object], name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value.strip():
        raise CpkServerApplicationError(400, f"{name} is required")
    return value


def _optional_text(values: Mapping[str, object], name: str) -> str | None:
    value = values.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CpkServerApplicationError(400, f"{name} must be text")
    return value


def _optional_nonnegative_integer(
    values: Mapping[str, object],
    name: str,
) -> int | None:
    value = values.get(name)
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise CpkServerApplicationError(400, f"{name} must be nonnegative integer")
    return value


def _nonnegative_integer(values: Mapping[str, object], name: str) -> int:
    value = _optional_nonnegative_integer(values, name)
    if value is None:
        raise CpkServerApplicationError(400, f"{name} is required")
    return value


def _text_tuple(
    values: Mapping[str, object],
    name: str,
) -> tuple[str, ...]:
    value = values.get(name)
    if (
        not isinstance(value, (list, tuple))
        or not value
        or len(value) > 32
        or not all(isinstance(item, str) and item.strip() for item in value)
    ):
        raise CpkServerApplicationError(
            400,
            f"{name} must be a nonempty bounded list of text",
        )
    return tuple(value)


def _positive_int(values: Mapping[str, object], name: str, *, default: int) -> int:
    value = values.get(name, default)
    if type(value) is not int or value < 1:
        raise CpkServerApplicationError(400, f"{name} must be a positive integer")
    return value


def _non_negative_int(values: Mapping[str, object], name: str, *, default: int) -> int:
    value = values.get(name, default)
    if type(value) is not int or value < 0:
        raise CpkServerApplicationError(400, f"{name} must be a non-negative integer")
    return value


def _runtime_authority(values: Mapping[str, object]) -> object:
    raw = _mapping(values, "authority")
    kind = _text(raw, "kind")
    if kind == "local-docker-socket":
        return LocalDockerSocketAuthority()
    if kind == "remote-docker-tls":
        try:
            return RemoteDockerTlsAuthority(
                endpoint=_text(raw, "endpoint"),
                ca_certificate=SecretReference(_text(raw, "ca_certificate")),
                client_certificate=SecretReference(_text(raw, "client_certificate")),
                client_key=SecretReference(_text(raw, "client_key")),
            )
        except (TypeError, ValueError) as error:
            raise CpkServerApplicationError(400, str(error)) from error
    raise CpkServerApplicationError(400, "unsupported runtime authority")


def _ingress_authority(values: Mapping[str, object]) -> object:
    raw = _mapping(values, "authority")
    provider_kind = _text(raw, "provider_kind")
    if provider_kind == "cloudflare":
        try:
            return CloudflareZoneIngressAuthority(
                account_id=_text(raw, "account_id"),
                zone_id=_text(raw, "zone_id"),
                zone_name=_text(raw, "zone_name"),
                api_token_ref=SecretReference(_text(raw, "api_token_ref")),
                allowed_hostname_pattern=_text(raw, "allowed_hostname_pattern"),
                generated_secret_provider_registration_id=_text(
                    raw,
                    "generated_secret_provider_registration_id",
                ),
                generated_secret_reference_prefix=SecretReference(
                    _text(raw, "generated_secret_reference_prefix")
                ),
            )
        except (TypeError, ValueError) as error:
            raise CpkServerApplicationError(400, str(error)) from error
    raise CpkServerApplicationError(400, "unsupported ingress authority provider")


def _worker_authority(context: TrustedCommandContext) -> ExecutionWorkerAuthority:
    return ExecutionWorkerAuthority(
        worker_id=context.actor_id,
        scopes=context.granted_scopes,
    )


def _readiness(values: Mapping[str, object]) -> tuple[ExternalReadinessAttestation, ...]:
    raw = values.get("readiness", [])
    if not isinstance(raw, list):
        raise CpkServerApplicationError(400, "readiness must be a list")
    items: list[ExternalReadinessAttestation] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise CpkServerApplicationError(400, "readiness entries must be objects")
        items.append(
            ExternalReadinessAttestation(
                activity_id=_text(item, "activity_id"),
                evidence_ref=_text(item, "evidence_ref"),
            )
        )
    return tuple(items)


def _unsupported_route(request: CpkServerRouteRequest) -> CpkServerApplicationError:
    return CpkServerApplicationError(404, f"unknown route {request.route_id!r}")


def _service_not_configured(request: CpkServerRouteRequest) -> CpkServerApplicationError:
    return CpkServerApplicationError(
        501,
        f"{request.route_id!r} is not configured in cpk-server operations",
    )


def _registered_image_pull_authority_descriptor(value: Any) -> dict[str, object]:
    return {
        "authority_id": value.authority_id,
        "workspace_id": value.workspace_id,
        "authority": value.authority.descriptor(),
        "admitted_by": value.admitted_by,
        "admitted_at": value.admitted_at,
        "status": value.status.value,
        "metadata": dict(value.metadata),
    }


def _registered_runtime_authority_descriptor(
    value: RegisteredRuntimeAuthority,
) -> dict[str, object]:
    return value.descriptor()


def _registered_runtime_authority_delivery_descriptor(
    value: RegisteredRuntimeAuthorityDelivery,
) -> dict[str, object]:
    return value.descriptor()


def _registered_ingress_authority_descriptor(
    value: RegisteredIngressAuthority,
) -> dict[str, object]:
    return value.descriptor()


def _registered_product_descriptor(value: Any) -> dict[str, object]:
    return {
        "registration_id": value.registration_id,
        "workspace_id": value.workspace_id,
        "reference": value.reference.descriptor(),
        "status": value.status.value,
        "product": {
            "display_name": value.descriptor_document.product.display_name,
            "description": value.descriptor_document.product.description,
        },
    }


def _read_error_status(error: ReadModelError) -> int:
    message = str(error)
    if message.startswith(
        (
            "missing workspace",
            "missing session",
            "missing plan",
            "missing run in workspace",
            "missing runtime authority",
            "missing runtime authority delivery",
            "missing ingress authority",
            "missing secret provider",
            "missing secret reference",
        )
    ):
        return 404
    if "store is not configured" in message:
        return 503
    if (
        "references missing graph truth" in message
        or "references graph truth outside workspace" in message
        or "invalid recovery graph truth" in message
    ):
        return 409
    return 400
