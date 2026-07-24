"""Operations-backed application services for the cpk-server wrapper."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from control_plane_kit_core.operations import ControlPlaneServiceRole
from control_plane_kit_core.operations.commands import OperatorCommandKind
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.runtime_effects import ImagePullAuthority
from control_plane_kit_core.products import ProductDescriptorCodec, ProductDescriptorError
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, GraphDescriptorError

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
from control_plane_kit_operations.records import ApprovalDecisionKind
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
        with self._unit_of_work_factory() as unit_of_work:
            stores = unit_of_work.stores
            kwargs: dict[str, object] = {
                "workspace_store": stores.workspaces,
                "graph_topology_store": stores.graphs,
                "activity_history_store": stores.activity_history,
                "execution_store": stores.execution,
                "observed_state_store": stores.observed_state,
            }
            if self._clock is not None:
                kwargs["clock"] = self._clock
            service = InstanceReadService(**kwargs)
            try:
                model = _read_model(service, request)
            except ReadModelError as error:
                raise CpkServerApplicationError(_read_error_status(error), str(error)) from error
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
        desired_graphs: DesiredGraphCommandService | None = None,
    ) -> None:
        self._service = service
        self._workspaces = workspaces
        self._products = products
        self._image_pull_authorities = image_pull_authorities
        self._desired_graphs = desired_graphs

    def handle(self, request: CpkServerRouteRequest) -> Mapping[str, object]:
        if request.route_id == "command.workspace.create":
            if self._workspaces is None:
                raise _service_not_configured(request)
            payload = _arguments(request)
            result = self._workspaces.create(
                CreateWorkspace(
                    workspace_id=_text(payload, "workspace_id"),
                    name=_text(payload, "name"),
                    actor_id=_text(payload, "actor_id"),
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
                    imported_by=_text(payload, "actor_id"),
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
                    admitted_by=_text(payload, "actor_id"),
                    admitted_at=_text(payload, "admitted_at"),
                )
            )
            return _registered_image_pull_authority_descriptor(result)
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
                    actor_id=_text(payload, "actor_id"),
                    graph=graph,
                    expected_desired_graph_id=_optional_text(
                        payload,
                        "expected_desired_graph_id",
                    ),
                    idempotency_key=IdempotencyKey(_text(payload, "idempotency_key")),
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
                actor_id=_text(payload, "actor_id"),
                expected_current_graph_id=_text(payload, "expected_current_graph_id"),
                expected_desired_graph_id=_text(payload, "expected_desired_graph_id"),
                idempotency_key=IdempotencyKey(_text(payload, "idempotency_key")),
            )
        )
        return result.descriptor()


class CpkServerApprovalService:
    def __init__(self, service: ApprovalCommandService) -> None:
        self._service = service

    def handle(self, request: CpkServerRouteRequest) -> Mapping[str, object]:
        if request.route_id == "command.approval.request":
            payload = _arguments(request)
            result = self._service.execute(
                RequestApproval(
                    session_id=_text(payload, "session_id"),
                    plan_id=_path_or_payload(payload, "plan_id", "plan_id"),
                    actor_id=_text(payload, "actor_id"),
                    actor_scopes=_scopes(payload),
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
                actor_id=_text(payload, "actor_id"),
                actor_scopes=_scopes(payload),
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
        if request.route_id != "command.deployment.admit":
            raise _unsupported_route(request)
        payload = _arguments(request)
        result = self._service.execute(
            RequestPlanExecution(
                workspace_id=_workspace_id(payload),
                session_id=_text(payload, "session_id"),
                plan_id=_path_or_payload(payload, "plan_id", "plan_id"),
                approval_request_id=_text(payload, "approval_request_id"),
                actor_id=_text(payload, "actor_id"),
                actor_scopes=_scopes(payload),
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
        if request.route_id.startswith("command.operation-session."):
            if self._operations is None:
                raise _service_not_configured(request)
            payload = _arguments(request)
            if request.route_id == "command.operation-session.start":
                result = self._operations.execute(
                    StartOperationSession(
                        workspace_id=_workspace_id(payload),
                        actor_id=_text(payload, "actor_id"),
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
                        actor_id=_text(payload, "actor_id"),
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
                        actor_id=_text(payload, "actor_id"),
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
                        actor_id=_text(payload, "actor_id"),
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
                    desired_graph_id=_text(payload, "desired_graph_id"),
                    authority=_worker_authority(payload),
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
                authority=_worker_authority(payload),
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
        if request.route_id == "command.run.start":
            if self._lifecycle is None:
                raise _service_not_configured(request)
            payload = _arguments(request)
            result = self._lifecycle.execute(
                StartActivityRun(
                    run_id=_path_or_payload(payload, "run_id", "run_id"),
                    authority=_worker_authority(payload),
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
                authority=_worker_authority(payload),
                idempotency_key=IdempotencyKey(_text(payload, "idempotency_key")),
                max_effects=_positive_int(payload, "max_effects", default=1),
            )
        )
        return result.descriptor()


class CpkServerUnsupportedService:
    """Explicit placeholder for service roles not extracted into operations yet."""

    def __init__(self, role: ControlPlaneServiceRole) -> None:
        self._role = role

    def handle(self, request: CpkServerRouteRequest) -> Mapping[str, object]:
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
    desired_graphs: DesiredGraphCommandService | None = None,
    operations: OperationCommandService | None = None,
    advancement: CurrentGraphAdvancementCommandService | None = None,
    clock: Callable[[], object] | None = None,
) -> Mapping[ControlPlaneServiceRole, CpkServerApplicationService]:
    """Return the complete service map required by cpk-server composition."""

    unsupported = {
        role: CpkServerUnsupportedService(role)
        for role in (
            ControlPlaneServiceRole.RECOVERY,
            ControlPlaneServiceRole.OBSERVATION,
            ControlPlaneServiceRole.AUTHORIZATION,
        )
    }
    return {
        ControlPlaneServiceRole.PLANNING: CpkServerPlanningService(
            planning,
            workspaces=workspaces,
            products=products,
            image_pull_authorities=image_pull_authorities,
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
        **unsupported,
    }


def _read_model(service: InstanceReadService, request: CpkServerRouteRequest) -> Any:
    args = _arguments(request)
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
        return service.activity_timeline(
            _workspace_id(args),
            limit=_positive_int(args, "limit", default=50),
        )
    if route_id == "read.sessions":
        return service.open_sessions(
            _workspace_id(args),
            limit=_positive_int(args, "limit", default=50),
            offset=_non_negative_int(args, "offset", default=0),
        )
    if route_id == "read.session-detail":
        return service.session_detail(
            _workspace_id(args),
            _text(args, "session_id"),
            limit=_positive_int(args, "limit", default=50),
        )
    if route_id == "read.plan-detail":
        return service.plan_detail(
            _workspace_id(args),
            _text(args, "plan_id"),
            limit=_positive_int(args, "limit", default=50),
        )
    if route_id == "read.approval-detail":
        return service.approval_detail(
            _workspace_id(args),
            _text(args, "approval_id"),
            limit=_positive_int(args, "limit", default=50),
        )
    if route_id == "read.pending-approvals":
        return service.pending_approvals(
            _workspace_id(args),
            limit=_positive_int(args, "limit", default=50),
            offset=_non_negative_int(args, "offset", default=0),
        )
    if route_id == "read.observed-state":
        return service.observed_state(_workspace_id(args))
    if route_id == "read.control-surface":
        return service.control_surface(
            _workspace_id(args),
            pointer=_optional_text(args, "pointer") or "current",
        )
    raise _unsupported_route(request)


def _arguments(request: CpkServerRouteRequest) -> dict[str, object]:
    return {
        **dict(request.payload),
        **dict(request.path_parameters),
    }


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


def _scopes(values: Mapping[str, object]) -> tuple[PolicyScope, ...]:
    raw = values.get("actor_scopes", ())
    if not isinstance(raw, list):
        raise CpkServerApplicationError(400, "actor_scopes must be a list")
    if not all(isinstance(item, str) for item in raw):
        raise CpkServerApplicationError(400, "actor_scopes entries must be text")
    try:
        return tuple(PolicyScope(item) for item in raw)
    except ValueError as error:
        raise CpkServerApplicationError(400, "actor_scopes contains an unknown scope") from error


def _worker_authority(values: Mapping[str, object]) -> ExecutionWorkerAuthority:
    return ExecutionWorkerAuthority(
        worker_id=_text(values, "worker_id"),
        scopes=_scopes(values),
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
    if message.startswith(("missing workspace", "missing session", "missing plan")):
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
