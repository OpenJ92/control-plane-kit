"""Pure execution coordinator and verification command contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from control_plane_kit_core.operations.lifecycle import ContractEnforcementOwner
from control_plane_kit_core.operations.parity import (
    ApprovalPolicy,
    CommandIdempotencyPolicy,
)
from control_plane_kit_core.operations.services import (
    ControlPlaneServiceRole,
    DeploymentProgramStage,
)
from control_plane_kit_core.operations.transactions import ExternalEffectPolicy


class InvalidExecutionCoordinatorContract(ValueError):
    """Raised when execution coordinator contract data is incoherent."""


class ExecutionCoordinatorCommandKind(StrEnum):
    """Closed coordinator command identities without a worker loop."""

    EXECUTE_READY_EFFECT = "execute-ready-effect"
    EXECUTE_COMPENSATION_EFFECT = "execute-compensation-effect"
    RESUME_AFTER_RESTART = "resume-after-restart"
    SETTLE_RUN = "settle-run"


class VerificationCommandKind(StrEnum):
    """Closed verification command identities without adapter execution."""

    RUN_READINESS_VERIFICATION = "run-readiness-verification"
    RUN_HEALTH_VERIFICATION = "run-health-verification"
    RUN_DEPENDENCY_VERIFICATION = "run-dependency-verification"
    PROJECT_VERIFICATION_RESULT = "project-verification-result"


class EffectBoundaryKind(StrEnum):
    """Closed external-effect boundary vocabulary."""

    MATERIALIZATION = "materialization"
    INTENT = "intent"
    DISPATCH = "dispatch"
    RESULT = "result"
    OBSERVATION = "observation"
    SETTLEMENT = "settlement"


class EffectResultKind(StrEnum):
    """Closed outcomes visible at the coordinator boundary."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"
    UNCERTAIN = "uncertain"
    IN_FLIGHT = "in-flight"
    LIMITED_PROGRESS = "limited-progress"


class VerificationResultKind(StrEnum):
    """Closed verification outcomes visible to operators."""

    READY = "ready"
    NOT_READY = "not-ready"
    UNREACHABLE = "unreachable"
    UNSUPPORTED = "unsupported"
    UNCERTAIN = "uncertain"
    STALE = "stale"


class EffectMaterialPolicy(StrEnum):
    """Closed policy for where effect material comes from."""

    PINNED_APPROVED_PLAN = "pinned-approved-plan"
    CANONICAL_GRAPH_PROBE = "canonical-graph-probe"


class UncertaintyPolicy(StrEnum):
    """Closed policy for effect-without-result evidence."""

    OPERATOR_REQUIRED = "operator-required"
    NEVER_BLIND_REPLAY = "never-blind-replay"


_COORDINATOR_SCHEMAS = {
    ExecutionCoordinatorCommandKind.EXECUTE_READY_EFFECT: (
        "ExecutionReadyEffectRequest",
        "ExecutionEffectProgress",
    ),
    ExecutionCoordinatorCommandKind.EXECUTE_COMPENSATION_EFFECT: (
        "ExecutionCompensationEffectRequest",
        "ExecutionEffectProgress",
    ),
    ExecutionCoordinatorCommandKind.RESUME_AFTER_RESTART: (
        "ExecutionResumeRequest",
        "ExecutionEffectProgress",
    ),
    ExecutionCoordinatorCommandKind.SETTLE_RUN: (
        "ExecutionSettlementRequest",
        "ExecutionSettlementResult",
    ),
}

_VERIFICATION_SCHEMAS = {
    VerificationCommandKind.RUN_READINESS_VERIFICATION: (
        "ReadinessVerificationRequest",
        "VerificationCommandResult",
    ),
    VerificationCommandKind.RUN_HEALTH_VERIFICATION: (
        "HealthVerificationRequest",
        "VerificationCommandResult",
    ),
    VerificationCommandKind.RUN_DEPENDENCY_VERIFICATION: (
        "DependencyVerificationRequest",
        "VerificationCommandResult",
    ),
    VerificationCommandKind.PROJECT_VERIFICATION_RESULT: (
        "VerificationProjectionRequest",
        "VerificationProjectionResult",
    ),
}


@dataclass(frozen=True)
class EffectBoundaryContract:
    """One effect boundary law without dispatching an effect."""

    boundary: EffectBoundaryKind
    external_effect_policy: ExternalEffectPolicy
    durable_before_effect: bool
    durable_after_effect: bool
    may_leave_uncertainty: bool
    enforcement_owner: ContractEnforcementOwner

    def __post_init__(self) -> None:
        if not isinstance(self.boundary, EffectBoundaryKind):
            raise InvalidExecutionCoordinatorContract(
                "boundary must be EffectBoundaryKind"
            )
        if not isinstance(self.external_effect_policy, ExternalEffectPolicy):
            raise InvalidExecutionCoordinatorContract(
                "external_effect_policy must be ExternalEffectPolicy"
            )
        _validate_bool(self.durable_before_effect, "durable_before_effect")
        _validate_bool(self.durable_after_effect, "durable_after_effect")
        _validate_bool(self.may_leave_uncertainty, "may_leave_uncertainty")
        if not isinstance(self.enforcement_owner, ContractEnforcementOwner):
            raise InvalidExecutionCoordinatorContract(
                "enforcement_owner must be ContractEnforcementOwner"
            )
        if self.external_effect_policy is ExternalEffectPolicy.INSIDE_TRANSACTION:
            raise InvalidExecutionCoordinatorContract(
                "external effects must not run inside a transaction"
            )
        if (
            self.boundary is EffectBoundaryKind.DISPATCH
            and self.external_effect_policy is not ExternalEffectPolicy.AFTER_COMMIT
        ):
            raise InvalidExecutionCoordinatorContract(
                "dispatch must occur after commit"
            )
        if self.boundary is EffectBoundaryKind.INTENT and not self.durable_before_effect:
            raise InvalidExecutionCoordinatorContract(
                "intent boundary must be durable before effect"
            )
        if self.boundary in {
            EffectBoundaryKind.RESULT,
            EffectBoundaryKind.OBSERVATION,
            EffectBoundaryKind.SETTLEMENT,
        } and not self.durable_after_effect:
            raise InvalidExecutionCoordinatorContract(
                f"{self.boundary.value} must be durable after effect"
            )
        if self.enforcement_owner is not ContractEnforcementOwner.OPERATIONS:
            raise InvalidExecutionCoordinatorContract(
                "effect boundary enforcement belongs to operations"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "boundary": self.boundary.value,
            "external_effect_policy": self.external_effect_policy.value,
            "durable_before_effect": self.durable_before_effect,
            "durable_after_effect": self.durable_after_effect,
            "may_leave_uncertainty": self.may_leave_uncertainty,
            "enforcement_owner": self.enforcement_owner.value,
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "EffectBoundaryContract":
        if set(value) != {
            "boundary",
            "external_effect_policy",
            "durable_before_effect",
            "durable_after_effect",
            "may_leave_uncertainty",
            "enforcement_owner",
        }:
            raise InvalidExecutionCoordinatorContract(
                "effect boundary descriptor has unexpected keys"
            )
        try:
            return cls(
                boundary=EffectBoundaryKind(_text(value["boundary"], "boundary")),
                external_effect_policy=ExternalEffectPolicy(
                    _text(value["external_effect_policy"], "external_effect_policy")
                ),
                durable_before_effect=_bool(
                    value["durable_before_effect"],
                    "durable_before_effect",
                ),
                durable_after_effect=_bool(
                    value["durable_after_effect"],
                    "durable_after_effect",
                ),
                may_leave_uncertainty=_bool(
                    value["may_leave_uncertainty"],
                    "may_leave_uncertainty",
                ),
                enforcement_owner=ContractEnforcementOwner(
                    _text(value["enforcement_owner"], "enforcement_owner")
                ),
            )
        except ValueError as error:
            raise InvalidExecutionCoordinatorContract(str(error)) from error


@dataclass(frozen=True)
class ExecutionCoordinatorCommandContract:
    """One coordinator command handoff without scheduling or adapter calls."""

    operation_id: str
    kind: ExecutionCoordinatorCommandKind
    stage: DeploymentProgramStage
    service_role: ControlPlaneServiceRole
    request_schema: str
    response_schema: str
    idempotency: CommandIdempotencyPolicy
    approval: ApprovalPolicy
    material_policy: EffectMaterialPolicy
    uncertainty_policy: UncertaintyPolicy
    external_effect_policy: ExternalEffectPolicy
    requires_worker: bool
    requires_pinned_plan: bool
    records_intent_before_effect: bool
    records_result_after_effect: bool
    enforcement_owner: ContractEnforcementOwner

    def __post_init__(self) -> None:
        _validate_identity(self.operation_id, "operation_id")
        if not isinstance(self.kind, ExecutionCoordinatorCommandKind):
            raise InvalidExecutionCoordinatorContract(
                "kind must be ExecutionCoordinatorCommandKind"
            )
        if self.stage is not DeploymentProgramStage.EXECUTE:
            raise InvalidExecutionCoordinatorContract(
                "coordinator commands belong to execute stage"
            )
        if self.service_role is not ControlPlaneServiceRole.EXECUTION:
            raise InvalidExecutionCoordinatorContract(
                "coordinator commands use execution service"
            )
        expected_request, expected_response = _COORDINATOR_SCHEMAS[self.kind]
        if self.request_schema != expected_request:
            raise InvalidExecutionCoordinatorContract(
                f"{self.kind.value} has wrong request schema"
            )
        if self.response_schema != expected_response:
            raise InvalidExecutionCoordinatorContract(
                f"{self.kind.value} has wrong response schema"
            )
        if self.idempotency is not CommandIdempotencyPolicy.REQUIRED:
            raise InvalidExecutionCoordinatorContract(
                "coordinator commands require idempotency"
            )
        if self.approval is not ApprovalPolicy.REQUIRES_CURRENT_APPROVAL:
            raise InvalidExecutionCoordinatorContract(
                "coordinator commands require current approval"
            )
        if not isinstance(self.material_policy, EffectMaterialPolicy):
            raise InvalidExecutionCoordinatorContract(
                "material_policy must be EffectMaterialPolicy"
            )
        if self.material_policy is not EffectMaterialPolicy.PINNED_APPROVED_PLAN:
            raise InvalidExecutionCoordinatorContract(
                "coordinator effects use pinned approved plan material"
            )
        if not isinstance(self.uncertainty_policy, UncertaintyPolicy):
            raise InvalidExecutionCoordinatorContract(
                "uncertainty_policy must be UncertaintyPolicy"
            )
        if self.uncertainty_policy is not UncertaintyPolicy.NEVER_BLIND_REPLAY:
            raise InvalidExecutionCoordinatorContract(
                "coordinator uncertainty must never blind replay"
            )
        if self.external_effect_policy is not ExternalEffectPolicy.AFTER_COMMIT:
            raise InvalidExecutionCoordinatorContract(
                "coordinator effects run after commit"
            )
        _validate_bool(self.requires_worker, "requires_worker")
        _validate_bool(self.requires_pinned_plan, "requires_pinned_plan")
        _validate_bool(
            self.records_intent_before_effect,
            "records_intent_before_effect",
        )
        _validate_bool(
            self.records_result_after_effect,
            "records_result_after_effect",
        )
        if not self.requires_worker:
            raise InvalidExecutionCoordinatorContract(
                "coordinator commands require worker ownership"
            )
        if not self.requires_pinned_plan:
            raise InvalidExecutionCoordinatorContract(
                "coordinator commands require pinned plan material"
            )
        if self.kind is not ExecutionCoordinatorCommandKind.SETTLE_RUN and (
            not self.records_intent_before_effect
        ):
            raise InvalidExecutionCoordinatorContract(
                "effect commands must record intent before effect"
            )
        if self.kind is not ExecutionCoordinatorCommandKind.RESUME_AFTER_RESTART and (
            not self.records_result_after_effect
        ):
            raise InvalidExecutionCoordinatorContract(
                "coordinator commands must record result after effect"
            )
        if self.enforcement_owner is not ContractEnforcementOwner.OPERATIONS:
            raise InvalidExecutionCoordinatorContract(
                "coordinator execution is operations-owned"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "kind": self.kind.value,
            "stage": self.stage.value,
            "service_role": self.service_role.value,
            "request_schema": self.request_schema,
            "response_schema": self.response_schema,
            "idempotency": self.idempotency.value,
            "approval": self.approval.value,
            "material_policy": self.material_policy.value,
            "uncertainty_policy": self.uncertainty_policy.value,
            "external_effect_policy": self.external_effect_policy.value,
            "requires_worker": self.requires_worker,
            "requires_pinned_plan": self.requires_pinned_plan,
            "records_intent_before_effect": self.records_intent_before_effect,
            "records_result_after_effect": self.records_result_after_effect,
            "enforcement_owner": self.enforcement_owner.value,
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "ExecutionCoordinatorCommandContract":
        if set(value) != {
            "operation_id",
            "kind",
            "stage",
            "service_role",
            "request_schema",
            "response_schema",
            "idempotency",
            "approval",
            "material_policy",
            "uncertainty_policy",
            "external_effect_policy",
            "requires_worker",
            "requires_pinned_plan",
            "records_intent_before_effect",
            "records_result_after_effect",
            "enforcement_owner",
        }:
            raise InvalidExecutionCoordinatorContract(
                "coordinator command descriptor has unexpected keys"
            )
        try:
            return cls(
                operation_id=_text(value["operation_id"], "operation_id"),
                kind=ExecutionCoordinatorCommandKind(_text(value["kind"], "kind")),
                stage=DeploymentProgramStage(_text(value["stage"], "stage")),
                service_role=ControlPlaneServiceRole(
                    _text(value["service_role"], "service_role")
                ),
                request_schema=_text(value["request_schema"], "request_schema"),
                response_schema=_text(value["response_schema"], "response_schema"),
                idempotency=CommandIdempotencyPolicy(
                    _text(value["idempotency"], "idempotency")
                ),
                approval=ApprovalPolicy(_text(value["approval"], "approval")),
                material_policy=EffectMaterialPolicy(
                    _text(value["material_policy"], "material_policy")
                ),
                uncertainty_policy=UncertaintyPolicy(
                    _text(value["uncertainty_policy"], "uncertainty_policy")
                ),
                external_effect_policy=ExternalEffectPolicy(
                    _text(value["external_effect_policy"], "external_effect_policy")
                ),
                requires_worker=_bool(value["requires_worker"], "requires_worker"),
                requires_pinned_plan=_bool(
                    value["requires_pinned_plan"],
                    "requires_pinned_plan",
                ),
                records_intent_before_effect=_bool(
                    value["records_intent_before_effect"],
                    "records_intent_before_effect",
                ),
                records_result_after_effect=_bool(
                    value["records_result_after_effect"],
                    "records_result_after_effect",
                ),
                enforcement_owner=ContractEnforcementOwner(
                    _text(value["enforcement_owner"], "enforcement_owner")
                ),
            )
        except ValueError as error:
            raise InvalidExecutionCoordinatorContract(str(error)) from error


@dataclass(frozen=True)
class VerificationCommandContract:
    """One verification command handoff without probe execution."""

    operation_id: str
    kind: VerificationCommandKind
    service_role: ControlPlaneServiceRole
    request_schema: str
    response_schema: str
    material_policy: EffectMaterialPolicy
    result_kinds: tuple[VerificationResultKind, ...]
    requires_graph_ownership: bool
    stale_on_graph_change: bool
    redacted_projection: bool
    unsupported_is_durable: bool
    enforcement_owner: ContractEnforcementOwner

    def __post_init__(self) -> None:
        _validate_identity(self.operation_id, "operation_id")
        if not isinstance(self.kind, VerificationCommandKind):
            raise InvalidExecutionCoordinatorContract(
                "kind must be VerificationCommandKind"
            )
        if self.service_role is not ControlPlaneServiceRole.OBSERVATION:
            raise InvalidExecutionCoordinatorContract(
                "verification commands use observation service"
            )
        expected_request, expected_response = _VERIFICATION_SCHEMAS[self.kind]
        if self.request_schema != expected_request:
            raise InvalidExecutionCoordinatorContract(
                f"{self.kind.value} has wrong request schema"
            )
        if self.response_schema != expected_response:
            raise InvalidExecutionCoordinatorContract(
                f"{self.kind.value} has wrong response schema"
            )
        if self.material_policy is not EffectMaterialPolicy.CANONICAL_GRAPH_PROBE:
            raise InvalidExecutionCoordinatorContract(
                "verification commands consume canonical graph probe descriptors"
            )
        if not isinstance(self.result_kinds, tuple) or not all(
            isinstance(result, VerificationResultKind)
            for result in self.result_kinds
        ):
            raise InvalidExecutionCoordinatorContract(
                "result_kinds must be VerificationResultKind values"
            )
        if set(self.result_kinds) != set(VerificationResultKind):
            raise InvalidExecutionCoordinatorContract(
                "verification result set must cover every closed result kind"
            )
        _validate_bool(self.requires_graph_ownership, "requires_graph_ownership")
        _validate_bool(self.stale_on_graph_change, "stale_on_graph_change")
        _validate_bool(self.redacted_projection, "redacted_projection")
        _validate_bool(self.unsupported_is_durable, "unsupported_is_durable")
        if not self.requires_graph_ownership:
            raise InvalidExecutionCoordinatorContract(
                "verification requires graph ownership"
            )
        if not self.stale_on_graph_change:
            raise InvalidExecutionCoordinatorContract(
                "verification becomes stale when graph truth changes"
            )
        if not self.redacted_projection:
            raise InvalidExecutionCoordinatorContract(
                "verification projections must be redacted"
            )
        if not self.unsupported_is_durable:
            raise InvalidExecutionCoordinatorContract(
                "unsupported verification must be durable"
            )
        if self.enforcement_owner is not ContractEnforcementOwner.OPERATIONS:
            raise InvalidExecutionCoordinatorContract(
                "verification execution is operations-owned"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "kind": self.kind.value,
            "service_role": self.service_role.value,
            "request_schema": self.request_schema,
            "response_schema": self.response_schema,
            "material_policy": self.material_policy.value,
            "result_kinds": [result.value for result in self.result_kinds],
            "requires_graph_ownership": self.requires_graph_ownership,
            "stale_on_graph_change": self.stale_on_graph_change,
            "redacted_projection": self.redacted_projection,
            "unsupported_is_durable": self.unsupported_is_durable,
            "enforcement_owner": self.enforcement_owner.value,
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "VerificationCommandContract":
        if set(value) != {
            "operation_id",
            "kind",
            "service_role",
            "request_schema",
            "response_schema",
            "material_policy",
            "result_kinds",
            "requires_graph_ownership",
            "stale_on_graph_change",
            "redacted_projection",
            "unsupported_is_durable",
            "enforcement_owner",
        }:
            raise InvalidExecutionCoordinatorContract(
                "verification command descriptor has unexpected keys"
            )
        result_kinds = value["result_kinds"]
        if not isinstance(result_kinds, list):
            raise InvalidExecutionCoordinatorContract("result_kinds must be a list")
        try:
            return cls(
                operation_id=_text(value["operation_id"], "operation_id"),
                kind=VerificationCommandKind(_text(value["kind"], "kind")),
                service_role=ControlPlaneServiceRole(
                    _text(value["service_role"], "service_role")
                ),
                request_schema=_text(value["request_schema"], "request_schema"),
                response_schema=_text(value["response_schema"], "response_schema"),
                material_policy=EffectMaterialPolicy(
                    _text(value["material_policy"], "material_policy")
                ),
                result_kinds=tuple(
                    VerificationResultKind(_text(result, "result_kind"))
                    for result in result_kinds
                ),
                requires_graph_ownership=_bool(
                    value["requires_graph_ownership"],
                    "requires_graph_ownership",
                ),
                stale_on_graph_change=_bool(
                    value["stale_on_graph_change"],
                    "stale_on_graph_change",
                ),
                redacted_projection=_bool(
                    value["redacted_projection"],
                    "redacted_projection",
                ),
                unsupported_is_durable=_bool(
                    value["unsupported_is_durable"],
                    "unsupported_is_durable",
                ),
                enforcement_owner=ContractEnforcementOwner(
                    _text(value["enforcement_owner"], "enforcement_owner")
                ),
            )
        except ValueError as error:
            raise InvalidExecutionCoordinatorContract(str(error)) from error


@dataclass(frozen=True)
class ExecutionCoordinatorContractSet:
    """Closed execution/verification contract vocabulary for handoff."""

    coordinator_commands: tuple[ExecutionCoordinatorCommandContract, ...]
    verification_commands: tuple[VerificationCommandContract, ...]
    effect_boundaries: tuple[EffectBoundaryContract, ...]
    effect_result_kinds: tuple[EffectResultKind, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.coordinator_commands, tuple) or not all(
            isinstance(command, ExecutionCoordinatorCommandContract)
            for command in self.coordinator_commands
        ):
            raise InvalidExecutionCoordinatorContract(
                "coordinator_commands must be coordinator command contracts"
            )
        if not isinstance(self.verification_commands, tuple) or not all(
            isinstance(command, VerificationCommandContract)
            for command in self.verification_commands
        ):
            raise InvalidExecutionCoordinatorContract(
                "verification_commands must be verification command contracts"
            )
        if not isinstance(self.effect_boundaries, tuple) or not all(
            isinstance(boundary, EffectBoundaryContract)
            for boundary in self.effect_boundaries
        ):
            raise InvalidExecutionCoordinatorContract(
                "effect_boundaries must be effect boundary contracts"
            )
        if not isinstance(self.effect_result_kinds, tuple) or not all(
            isinstance(result, EffectResultKind)
            for result in self.effect_result_kinds
        ):
            raise InvalidExecutionCoordinatorContract(
                "effect_result_kinds must be EffectResultKind values"
            )
        if {
            command.kind
            for command in self.coordinator_commands
        } != set(ExecutionCoordinatorCommandKind):
            raise InvalidExecutionCoordinatorContract(
                "coordinator command set must cover every command kind"
            )
        if {
            command.kind
            for command in self.verification_commands
        } != set(VerificationCommandKind):
            raise InvalidExecutionCoordinatorContract(
                "verification command set must cover every command kind"
            )
        if {boundary.boundary for boundary in self.effect_boundaries} != set(
            EffectBoundaryKind
        ):
            raise InvalidExecutionCoordinatorContract(
                "effect boundary set must cover every boundary"
            )
        if set(self.effect_result_kinds) != set(EffectResultKind):
            raise InvalidExecutionCoordinatorContract(
                "effect result set must cover every result kind"
            )
        _reject_duplicates(
            "coordinator operation_id",
            (command.operation_id for command in self.coordinator_commands),
        )
        _reject_duplicates(
            "verification operation_id",
            (command.operation_id for command in self.verification_commands),
        )
        object.__setattr__(
            self,
            "coordinator_commands",
            tuple(
                sorted(
                    self.coordinator_commands,
                    key=lambda command: command.operation_id,
                )
            ),
        )
        object.__setattr__(
            self,
            "verification_commands",
            tuple(
                sorted(
                    self.verification_commands,
                    key=lambda command: command.operation_id,
                )
            ),
        )
        object.__setattr__(
            self,
            "effect_boundaries",
            tuple(
                sorted(
                    self.effect_boundaries,
                    key=lambda boundary: boundary.boundary.value,
                )
            ),
        )
        object.__setattr__(
            self,
            "effect_result_kinds",
            tuple(sorted(self.effect_result_kinds, key=lambda result: result.value)),
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "execution-coordinator-contract-set",
            "coordinator_commands": [
                command.descriptor() for command in self.coordinator_commands
            ],
            "verification_commands": [
                command.descriptor() for command in self.verification_commands
            ],
            "effect_boundaries": [
                boundary.descriptor() for boundary in self.effect_boundaries
            ],
            "effect_result_kinds": [
                result.value for result in self.effect_result_kinds
            ],
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "ExecutionCoordinatorContractSet":
        if set(value) != {
            "kind",
            "coordinator_commands",
            "verification_commands",
            "effect_boundaries",
            "effect_result_kinds",
        }:
            raise InvalidExecutionCoordinatorContract(
                "execution coordinator contract set descriptor has unexpected keys"
            )
        if value["kind"] != "execution-coordinator-contract-set":
            raise InvalidExecutionCoordinatorContract(
                "execution coordinator contract set descriptor has wrong kind"
            )
        coordinator_commands = value["coordinator_commands"]
        verification_commands = value["verification_commands"]
        effect_boundaries = value["effect_boundaries"]
        effect_result_kinds = value["effect_result_kinds"]
        if not isinstance(coordinator_commands, list):
            raise InvalidExecutionCoordinatorContract(
                "coordinator_commands must be a list"
            )
        if not isinstance(verification_commands, list):
            raise InvalidExecutionCoordinatorContract(
                "verification_commands must be a list"
            )
        if not isinstance(effect_boundaries, list):
            raise InvalidExecutionCoordinatorContract(
                "effect_boundaries must be a list"
            )
        if not isinstance(effect_result_kinds, list):
            raise InvalidExecutionCoordinatorContract(
                "effect_result_kinds must be a list"
            )
        try:
            return cls(
                coordinator_commands=tuple(
                    ExecutionCoordinatorCommandContract.from_descriptor(
                        _mapping(command, "coordinator_command")
                    )
                    for command in coordinator_commands
                ),
                verification_commands=tuple(
                    VerificationCommandContract.from_descriptor(
                        _mapping(command, "verification_command")
                    )
                    for command in verification_commands
                ),
                effect_boundaries=tuple(
                    EffectBoundaryContract.from_descriptor(
                        _mapping(boundary, "effect_boundary")
                    )
                    for boundary in effect_boundaries
                ),
                effect_result_kinds=tuple(
                    EffectResultKind(_text(result, "effect_result_kind"))
                    for result in effect_result_kinds
                ),
            )
        except ValueError as error:
            raise InvalidExecutionCoordinatorContract(str(error)) from error


def canonical_execution_coordinator_contract_set() -> ExecutionCoordinatorContractSet:
    """Return the pure coordinator/verification handoff contract."""

    return ExecutionCoordinatorContractSet(
        coordinator_commands=tuple(
            ExecutionCoordinatorCommandContract(
                operation_id=f"execution.{kind.value}",
                kind=kind,
                stage=DeploymentProgramStage.EXECUTE,
                service_role=ControlPlaneServiceRole.EXECUTION,
                request_schema=request,
                response_schema=response,
                idempotency=CommandIdempotencyPolicy.REQUIRED,
                approval=ApprovalPolicy.REQUIRES_CURRENT_APPROVAL,
                material_policy=EffectMaterialPolicy.PINNED_APPROVED_PLAN,
                uncertainty_policy=UncertaintyPolicy.NEVER_BLIND_REPLAY,
                external_effect_policy=ExternalEffectPolicy.AFTER_COMMIT,
                requires_worker=True,
                requires_pinned_plan=True,
                records_intent_before_effect=(
                    kind is not ExecutionCoordinatorCommandKind.SETTLE_RUN
                ),
                records_result_after_effect=(
                    kind
                    is not ExecutionCoordinatorCommandKind.RESUME_AFTER_RESTART
                ),
                enforcement_owner=ContractEnforcementOwner.OPERATIONS,
            )
            for kind, (request, response) in _COORDINATOR_SCHEMAS.items()
        ),
        verification_commands=tuple(
            VerificationCommandContract(
                operation_id=f"verification.{kind.value}",
                kind=kind,
                service_role=ControlPlaneServiceRole.OBSERVATION,
                request_schema=request,
                response_schema=response,
                material_policy=EffectMaterialPolicy.CANONICAL_GRAPH_PROBE,
                result_kinds=tuple(VerificationResultKind),
                requires_graph_ownership=True,
                stale_on_graph_change=True,
                redacted_projection=True,
                unsupported_is_durable=True,
                enforcement_owner=ContractEnforcementOwner.OPERATIONS,
            )
            for kind, (request, response) in _VERIFICATION_SCHEMAS.items()
        ),
        effect_boundaries=(
            EffectBoundaryContract(
                boundary=EffectBoundaryKind.MATERIALIZATION,
                external_effect_policy=ExternalEffectPolicy.FORBIDDEN,
                durable_before_effect=False,
                durable_after_effect=False,
                may_leave_uncertainty=False,
                enforcement_owner=ContractEnforcementOwner.OPERATIONS,
            ),
            EffectBoundaryContract(
                boundary=EffectBoundaryKind.INTENT,
                external_effect_policy=ExternalEffectPolicy.FORBIDDEN,
                durable_before_effect=True,
                durable_after_effect=False,
                may_leave_uncertainty=False,
                enforcement_owner=ContractEnforcementOwner.OPERATIONS,
            ),
            EffectBoundaryContract(
                boundary=EffectBoundaryKind.DISPATCH,
                external_effect_policy=ExternalEffectPolicy.AFTER_COMMIT,
                durable_before_effect=True,
                durable_after_effect=False,
                may_leave_uncertainty=True,
                enforcement_owner=ContractEnforcementOwner.OPERATIONS,
            ),
            EffectBoundaryContract(
                boundary=EffectBoundaryKind.RESULT,
                external_effect_policy=ExternalEffectPolicy.FORBIDDEN,
                durable_before_effect=False,
                durable_after_effect=True,
                may_leave_uncertainty=True,
                enforcement_owner=ContractEnforcementOwner.OPERATIONS,
            ),
            EffectBoundaryContract(
                boundary=EffectBoundaryKind.OBSERVATION,
                external_effect_policy=ExternalEffectPolicy.FORBIDDEN,
                durable_before_effect=False,
                durable_after_effect=True,
                may_leave_uncertainty=False,
                enforcement_owner=ContractEnforcementOwner.OPERATIONS,
            ),
            EffectBoundaryContract(
                boundary=EffectBoundaryKind.SETTLEMENT,
                external_effect_policy=ExternalEffectPolicy.FORBIDDEN,
                durable_before_effect=False,
                durable_after_effect=True,
                may_leave_uncertainty=False,
                enforcement_owner=ContractEnforcementOwner.OPERATIONS,
            ),
        ),
        effect_result_kinds=tuple(EffectResultKind),
    )


def _validate_identity(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidExecutionCoordinatorContract(f"{field} must be non-empty text")
    if value != value.strip():
        raise InvalidExecutionCoordinatorContract(f"{field} must be normalized")


def _validate_bool(value: bool, field: str) -> None:
    if type(value) is not bool:
        raise InvalidExecutionCoordinatorContract(f"{field} must be bool")


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise InvalidExecutionCoordinatorContract(f"{field} must be text")
    return value


def _bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise InvalidExecutionCoordinatorContract(f"{field} must be bool")
    return value


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InvalidExecutionCoordinatorContract(f"{field} must be a descriptor")
    return value


def _reject_duplicates(field: str, values: object) -> None:
    seen: set[object] = set()
    for value in values:
        if value in seen:
            raise InvalidExecutionCoordinatorContract(f"duplicate {field}: {value}")
        seen.add(value)
