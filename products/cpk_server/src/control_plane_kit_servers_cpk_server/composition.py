"""Product-local cpk-server process composition root."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from control_plane_kit_core.operations import (
    AdapterCommandParityContract,
    AdapterOperationSecurityParityContract,
    AdapterParityContract,
    ApplicationServiceBinding,
    ControlPlaneProcessContract,
    ControlPlaneServiceRole,
    CpkServerEntrypointHandoffContract,
    DependencyReadinessKind,
    ExternalEffectPolicy,
    HttpApiContract,
    McpStreamableHttpContract,
    ReadinessDependency,
    ServiceTransactionBoundary,
    StoreParticipation,
    UnitOfWorkBoundary,
    canonical_cpk_server_entrypoint_handoff,
    operator_adapter_security_parity,
    operator_command_http_routes,
    operator_command_parity,
    operator_read_http_routes,
    operator_read_projection_parity,
    DeploymentProgramBoundary,
)


class CpkServerCompositionError(ValueError):
    """Raised when the cpk-server composition root is incoherent."""


class UnknownTargetError(CpkServerCompositionError):
    """Raised when process-local target state names an unknown target."""


@dataclass(frozen=True, slots=True)
class CpkServerProcessConfiguration:
    """Process-local bootstrap mode for the cpk-server wrapper."""

    execution_enabled: bool
    authentication_required: bool
    mode: str

    def __post_init__(self) -> None:
        if type(self.execution_enabled) is not bool:
            raise CpkServerCompositionError("execution_enabled must be bool")
        if type(self.authentication_required) is not bool:
            raise CpkServerCompositionError("authentication_required must be bool")
        if self.mode not in {"execution-capable", "local-read-only"}:
            raise CpkServerCompositionError("unknown cpk-server mode")
        if self.execution_enabled and not self.authentication_required:
            raise CpkServerCompositionError(
                "execution-capable composition requires authentication"
            )

    @classmethod
    def execution_capable(
        cls,
        *,
        authentication_required: bool,
    ) -> "CpkServerProcessConfiguration":
        return cls(
            execution_enabled=True,
            authentication_required=authentication_required,
            mode="execution-capable",
        )

    @classmethod
    def local_read_only(cls) -> "CpkServerProcessConfiguration":
        return cls(
            execution_enabled=False,
            authentication_required=False,
            mode="local-read-only",
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "execution_enabled": self.execution_enabled,
            "authentication_required": self.authentication_required,
        }


@dataclass(frozen=True, slots=True)
class ObserverState:
    """Bounded process-local observer evidence."""

    observer_id: str
    descriptor_items: tuple[tuple[str, str], ...]

    @classmethod
    def from_mapping(cls, observer_id: str, descriptor: Mapping[str, object]) -> "ObserverState":
        _validate_identity(observer_id, "observer_id")
        if not isinstance(descriptor, Mapping):
            raise CpkServerCompositionError("observer descriptor must be a mapping")
        items: list[tuple[str, str]] = []
        for key, value in sorted(descriptor.items()):
            _validate_identity(str(key), "observer descriptor key")
            if not isinstance(value, (str, int, float, bool)) or value is None:
                raise CpkServerCompositionError(
                    "observer descriptor values must be bounded scalar values"
                )
            text = str(value)
            if len(text) > 256:
                raise CpkServerCompositionError("observer descriptor value is too large")
            items.append((str(key), text))
        return cls(observer_id=observer_id, descriptor_items=tuple(items))

    def descriptor(self) -> dict[str, object]:
        return {
            "observer_id": self.observer_id,
            "descriptor": {key: value for key, value in self.descriptor_items},
        }


@dataclass(frozen=True, slots=True)
class CpkServerProcessState:
    """Immutable process-local state that never owns graph truth."""

    targets: tuple[str, ...] = ()
    active_target: str | None = None
    observers: tuple[ObserverState, ...] = ()
    graph_truth_policy: str = "process-state-never-owns-graph-truth"

    def __post_init__(self) -> None:
        _validate_identities(self.targets, "targets")
        if len(set(self.targets)) != len(self.targets):
            raise CpkServerCompositionError("targets must be unique")
        if self.active_target is not None:
            _validate_identity(self.active_target, "active_target")
            if self.active_target not in self.targets:
                raise UnknownTargetError(f"unknown target: {self.active_target}")
        if not isinstance(self.observers, tuple) or not all(
            isinstance(observer, ObserverState) for observer in self.observers
        ):
            raise CpkServerCompositionError("observers must be ObserverState values")
        if self.graph_truth_policy != "process-state-never-owns-graph-truth":
            raise CpkServerCompositionError("process state must not own graph truth")

    def record_observer(
        self,
        observer_id: str,
        descriptor: Mapping[str, object],
    ) -> "CpkServerProcessState":
        observer = ObserverState.from_mapping(observer_id, descriptor)
        remaining = tuple(
            item for item in self.observers if item.observer_id != observer.observer_id
        )
        return CpkServerProcessState(
            targets=self.targets,
            active_target=self.active_target,
            observers=remaining + (observer,),
        )

    def replace_targets(self, targets: tuple[str, ...]) -> "CpkServerProcessState":
        active = self.active_target if self.active_target in targets else None
        return CpkServerProcessState(
            targets=targets,
            active_target=active,
            observers=self.observers,
        )

    def switch_active_target(self, target: str) -> "CpkServerProcessState":
        _validate_identity(target, "target")
        if target not in self.targets:
            raise UnknownTargetError(f"unknown target: {target}")
        return CpkServerProcessState(
            targets=self.targets,
            active_target=target,
            observers=self.observers,
        )


@dataclass(frozen=True, slots=True)
class CpkServerComposition:
    """One cpk-server process wrapper over one core handoff boundary."""

    configuration: CpkServerProcessConfiguration
    handoff: CpkServerEntrypointHandoffContract
    process_state: CpkServerProcessState = field(default_factory=CpkServerProcessState)
    command_identity_policy: str = "single-application-boundary"

    def __post_init__(self) -> None:
        if not isinstance(self.configuration, CpkServerProcessConfiguration):
            raise CpkServerCompositionError(
                "configuration must be CpkServerProcessConfiguration"
            )
        if self.command_identity_policy != "single-application-boundary":
            raise CpkServerCompositionError(
                "commands must pass through one application boundary"
            )
        if not isinstance(self.process_state, CpkServerProcessState):
            raise CpkServerCompositionError("process_state must be CpkServerProcessState")

    @property
    def program(self) -> DeploymentProgramBoundary:
        return self.handoff.program

    @property
    def http_api(self) -> HttpApiContract:
        return self.handoff.http_api

    @property
    def mcp(self) -> McpStreamableHttpContract:
        return self.handoff.mcp

    def service_binding(self, role: ControlPlaneServiceRole) -> ApplicationServiceBinding:
        return self.program.service(role)

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "cpk-server-composition",
            "configuration": self.configuration.descriptor(),
            "handoff": self.handoff.descriptor(),
            "command_identity_policy": self.command_identity_policy,
            "process_state_policy": self.process_state.graph_truth_policy,
        }


def create_cpk_server_composition(
    configuration: CpkServerProcessConfiguration | None = None,
) -> CpkServerComposition:
    """Create the first product-local process composition over core contracts."""

    config = configuration or CpkServerProcessConfiguration.execution_capable(
        authentication_required=True
    )
    return CpkServerComposition(
        configuration=config,
        handoff=_canonical_handoff(),
    )


def _canonical_handoff() -> CpkServerEntrypointHandoffContract:
    mcp = McpStreamableHttpContract()
    http_api = HttpApiContract(operator_read_http_routes() + operator_command_http_routes())
    program = _program()
    unit_of_work = _unit_of_work(program)
    projection_parity: AdapterParityContract = operator_read_projection_parity(http_api, mcp)
    command_parity: AdapterCommandParityContract = operator_command_parity(
        http_api,
        mcp,
        unit_of_work,
    )
    security_parity: AdapterOperationSecurityParityContract = operator_adapter_security_parity(
        projection_parity=projection_parity,
        command_parity=command_parity,
    )
    return canonical_cpk_server_entrypoint_handoff(
        process=ControlPlaneProcessContract(
            dependencies=tuple(
                ReadinessDependency(kind)
                for kind in (
                    DependencyReadinessKind.STORE,
                    DependencyReadinessKind.RUNTIME_AUTHORITY,
                    DependencyReadinessKind.WORKER,
                    DependencyReadinessKind.HTTP_API,
                    DependencyReadinessKind.MCP_STREAMABLE_HTTP,
                    DependencyReadinessKind.OBSERVATION,
                )
            ),
            http_api=http_api,
            mcp=mcp,
        ),
        program=program,
        unit_of_work=unit_of_work,
        projection_parity=projection_parity,
        command_parity=command_parity,
        security_parity=security_parity,
    )


def _program() -> DeploymentProgramBoundary:
    return DeploymentProgramBoundary(
        tuple(
            ApplicationServiceBinding(
                role=role,
                service_name=f"{role.value}-service",
            )
            for role in ControlPlaneServiceRole
        )
    )


def _unit_of_work(program: DeploymentProgramBoundary) -> UnitOfWorkBoundary:
    return UnitOfWorkBoundary(
        program=program,
        services=tuple(_transaction_rule(role) for role in ControlPlaneServiceRole),
    )


def _transaction_rule(role: ControlPlaneServiceRole) -> ServiceTransactionBoundary:
    if role is ControlPlaneServiceRole.READS:
        return ServiceTransactionBoundary(role, StoreParticipation.READ_ONLY)
    if role is ControlPlaneServiceRole.AUTHORIZATION:
        return ServiceTransactionBoundary(role, StoreParticipation.NONE)
    if role is ControlPlaneServiceRole.EXECUTION:
        return ServiceTransactionBoundary(
            role,
            StoreParticipation.READ_WRITE,
            owns_transaction=True,
            external_effect_policy=ExternalEffectPolicy.AFTER_COMMIT,
            uses_worker=True,
            uses_runtime_authority=True,
        )
    return ServiceTransactionBoundary(
        role,
        StoreParticipation.READ_WRITE,
        owns_transaction=True,
    )


def _validate_identities(values: tuple[str, ...], label: str) -> None:
    if not isinstance(values, tuple):
        raise CpkServerCompositionError(f"{label} must be a tuple")
    for value in values:
        _validate_identity(value, label)


def _validate_identity(value: str, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise CpkServerCompositionError(f"{label} must be non-empty text")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    if any(char not in allowed for char in value):
        raise CpkServerCompositionError(f"{label} has invalid characters")
