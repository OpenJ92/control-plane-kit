"""Pure operator command workflow contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from control_plane_kit_core.operations.parity import (
    ActivityHistoryPolicy,
    ApprovalPolicy,
    CommandIdempotencyPolicy,
)
from control_plane_kit_core.operations.services import (
    ControlPlaneServiceRole,
    DeploymentProgramStage,
)


class InvalidCommandWorkflowContract(ValueError):
    """Raised when command workflow contract data is incoherent."""


class OperatorCommandFamily(StrEnum):
    """Closed command families at the operator workflow boundary."""

    WORKSPACE = "workspace"
    PRODUCT_REGISTRATION = "product-registration"
    OPERATION_SESSION = "operation-session"
    DESIRED_GRAPH = "desired-graph"
    ACTIVITY_PLANNING = "activity-planning"
    APPROVAL = "approval"


class OperatorCommandKind(StrEnum):
    """Closed operator command identities independent of stores or routes."""

    CREATE_WORKSPACE = "create-workspace"
    IMPORT_PRODUCT_DESCRIPTOR = "import-product-descriptor"
    START_OPERATION_SESSION = "start-operation-session"
    CLOSE_OPERATION_SESSION = "close-operation-session"
    CANCEL_OPERATION_SESSION = "cancel-operation-session"
    RECORD_OPERATION_ACTION = "record-operation-action"
    SET_DESIRED_GRAPH = "set-desired-graph"
    REQUEST_ACTIVITY_PLAN = "request-activity-plan"
    REQUEST_APPROVAL = "request-approval"
    DECIDE_APPROVAL = "decide-approval"


class CommandPayloadPolicy(StrEnum):
    """Closed durable descriptor policy for command payloads."""

    REDACT_OPERATOR_VALUES = "redact-operator-values"
    GRAPH_DESCRIPTOR_REFERENCE = "graph-descriptor-reference"
    PLAN_DESCRIPTOR_REFERENCE = "plan-descriptor-reference"
    APPROVAL_RISK_EVIDENCE = "approval-risk-evidence"
    PRODUCT_DESCRIPTOR_DOCUMENT = "product-descriptor-document"


_KIND_FAMILY = {
    OperatorCommandKind.CREATE_WORKSPACE: OperatorCommandFamily.WORKSPACE,
    OperatorCommandKind.IMPORT_PRODUCT_DESCRIPTOR: (
        OperatorCommandFamily.PRODUCT_REGISTRATION
    ),
    OperatorCommandKind.START_OPERATION_SESSION: OperatorCommandFamily.OPERATION_SESSION,
    OperatorCommandKind.CLOSE_OPERATION_SESSION: OperatorCommandFamily.OPERATION_SESSION,
    OperatorCommandKind.CANCEL_OPERATION_SESSION: OperatorCommandFamily.OPERATION_SESSION,
    OperatorCommandKind.RECORD_OPERATION_ACTION: OperatorCommandFamily.OPERATION_SESSION,
    OperatorCommandKind.SET_DESIRED_GRAPH: OperatorCommandFamily.DESIRED_GRAPH,
    OperatorCommandKind.REQUEST_ACTIVITY_PLAN: OperatorCommandFamily.ACTIVITY_PLANNING,
    OperatorCommandKind.REQUEST_APPROVAL: OperatorCommandFamily.APPROVAL,
    OperatorCommandKind.DECIDE_APPROVAL: OperatorCommandFamily.APPROVAL,
}


@dataclass(frozen=True)
class OperatorCommandContract:
    """One command shape without a service, store, or execution callback."""

    operation_id: str
    kind: OperatorCommandKind
    family: OperatorCommandFamily
    stage: DeploymentProgramStage
    service_role: ControlPlaneServiceRole
    request_schema: str
    response_schema: str
    idempotency: CommandIdempotencyPolicy
    approval: ApprovalPolicy
    activity_history: ActivityHistoryPolicy
    payload_policy: CommandPayloadPolicy
    requires_open_session: bool
    creates_session: bool
    terminal_session_transition: bool

    def __post_init__(self) -> None:
        _validate_identity(self.operation_id, "operation_id")
        if not isinstance(self.kind, OperatorCommandKind):
            raise InvalidCommandWorkflowContract(
                "kind must be OperatorCommandKind"
            )
        if not isinstance(self.family, OperatorCommandFamily):
            raise InvalidCommandWorkflowContract(
                "family must be OperatorCommandFamily"
            )
        if _KIND_FAMILY[self.kind] is not self.family:
            expected_family = _KIND_FAMILY[self.kind]
            raise InvalidCommandWorkflowContract(
                f"{self.kind.value} must use {expected_family.value} family"
            )
        if not isinstance(self.stage, DeploymentProgramStage):
            raise InvalidCommandWorkflowContract(
                "stage must be DeploymentProgramStage"
            )
        if not isinstance(self.service_role, ControlPlaneServiceRole):
            raise InvalidCommandWorkflowContract(
                "service_role must be ControlPlaneServiceRole"
            )
        _validate_identity(self.request_schema, "request_schema")
        _validate_identity(self.response_schema, "response_schema")
        if not isinstance(self.idempotency, CommandIdempotencyPolicy):
            raise InvalidCommandWorkflowContract(
                "idempotency must be CommandIdempotencyPolicy"
            )
        if self.idempotency is not CommandIdempotencyPolicy.REQUIRED:
            raise InvalidCommandWorkflowContract(
                f"{self.operation_id!r} requires idempotency"
            )
        if not isinstance(self.approval, ApprovalPolicy):
            raise InvalidCommandWorkflowContract("approval must be ApprovalPolicy")
        if not isinstance(self.activity_history, ActivityHistoryPolicy):
            raise InvalidCommandWorkflowContract(
                "activity_history must be ActivityHistoryPolicy"
            )
        if (
            self.activity_history
            is not ActivityHistoryPolicy.RECORD_ACCEPTED_AND_REJECTED_COMMANDS
        ):
            raise InvalidCommandWorkflowContract(
                f"{self.operation_id!r} must record accepted and rejected command history"
            )
        if not isinstance(self.payload_policy, CommandPayloadPolicy):
            raise InvalidCommandWorkflowContract(
                "payload_policy must be CommandPayloadPolicy"
            )
        _validate_bool(self.requires_open_session, "requires_open_session")
        _validate_bool(self.creates_session, "creates_session")
        _validate_bool(
            self.terminal_session_transition,
            "terminal_session_transition",
        )
        if self.creates_session and self.requires_open_session:
            raise InvalidCommandWorkflowContract(
                "session creation cannot require an open session"
            )
        if self.terminal_session_transition and not self.requires_open_session:
            raise InvalidCommandWorkflowContract(
                "terminal session transitions require an open session"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "operation_id": self.operation_id,
            "kind": self.kind.value,
            "family": self.family.value,
            "stage": self.stage.value,
            "service_role": self.service_role.value,
            "request_schema": self.request_schema,
            "response_schema": self.response_schema,
            "idempotency": self.idempotency.value,
            "approval": self.approval.value,
            "activity_history": self.activity_history.value,
            "payload_policy": self.payload_policy.value,
            "requires_open_session": self.requires_open_session,
            "creates_session": self.creates_session,
            "terminal_session_transition": self.terminal_session_transition,
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "OperatorCommandContract":
        if set(value) != {
            "operation_id",
            "kind",
            "family",
            "stage",
            "service_role",
            "request_schema",
            "response_schema",
            "idempotency",
            "approval",
            "activity_history",
            "payload_policy",
            "requires_open_session",
            "creates_session",
            "terminal_session_transition",
        }:
            raise InvalidCommandWorkflowContract(
                "command contract descriptor has unexpected keys"
            )
        try:
            return cls(
                operation_id=_text(value["operation_id"], "operation_id"),
                kind=OperatorCommandKind(_text(value["kind"], "kind")),
                family=OperatorCommandFamily(_text(value["family"], "family")),
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
                activity_history=ActivityHistoryPolicy(
                    _text(value["activity_history"], "activity_history")
                ),
                payload_policy=CommandPayloadPolicy(
                    _text(value["payload_policy"], "payload_policy")
                ),
                requires_open_session=_bool(
                    value["requires_open_session"],
                    "requires_open_session",
                ),
                creates_session=_bool(value["creates_session"], "creates_session"),
                terminal_session_transition=_bool(
                    value["terminal_session_transition"],
                    "terminal_session_transition",
                ),
            )
        except ValueError as error:
            raise InvalidCommandWorkflowContract(str(error)) from error


@dataclass(frozen=True)
class OperatorCommandWorkflowContract:
    """Closed command vocabulary for operator workflow handoffs."""

    commands: tuple[OperatorCommandContract, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.commands, tuple) or not all(
            isinstance(command, OperatorCommandContract)
            for command in self.commands
        ):
            raise InvalidCommandWorkflowContract(
                "commands must be OperatorCommandContract values"
            )
        _reject_duplicates(
            "operation_id",
            (command.operation_id for command in self.commands),
        )
        _reject_duplicates("kind", (command.kind for command in self.commands))
        expected = tuple(_CANONICAL_COMMANDS)
        actual = tuple(command.operation_id for command in self.commands)
        if set(actual) != {
            definition.operation_id
            for definition in expected
        }:
            raise InvalidCommandWorkflowContract(
                "operator command workflow must cover the canonical command set"
            )
        ordered = tuple(
            sorted(self.commands, key=lambda command: command.operation_id)
        )
        object.__setattr__(self, "commands", ordered)

    def command(self, operation_id: str) -> OperatorCommandContract:
        _validate_identity(operation_id, "operation_id")
        for command in self.commands:
            if command.operation_id == operation_id:
                return command
        raise InvalidCommandWorkflowContract(
            f"unknown operation_id {operation_id!r}"
        )

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "operator-command-workflow-contract",
            "commands": [command.descriptor() for command in self.commands],
        }

    @classmethod
    def from_descriptor(
        cls,
        value: Mapping[str, object],
    ) -> "OperatorCommandWorkflowContract":
        if set(value) != {"kind", "commands"}:
            raise InvalidCommandWorkflowContract(
                "operator command workflow descriptor has unexpected keys"
            )
        if value["kind"] != "operator-command-workflow-contract":
            raise InvalidCommandWorkflowContract(
                "operator command workflow descriptor has wrong kind"
            )
        commands = value["commands"]
        if not isinstance(commands, list):
            raise InvalidCommandWorkflowContract("commands must be a list")
        return cls(
            tuple(
                OperatorCommandContract.from_descriptor(
                    _mapping(command, "command")
                )
                for command in commands
            )
        )


def canonical_operator_command_workflow_contract() -> OperatorCommandWorkflowContract:
    """Return the pure operator command contract for deployment workflows."""

    return OperatorCommandWorkflowContract(
        tuple(
            OperatorCommandContract(
                operation_id=definition.operation_id,
                kind=definition.kind,
                family=definition.family,
                stage=definition.stage,
                service_role=definition.service_role,
                request_schema=definition.request_schema,
                response_schema=definition.response_schema,
                idempotency=CommandIdempotencyPolicy.REQUIRED,
                approval=definition.approval,
                activity_history=(
                    ActivityHistoryPolicy.RECORD_ACCEPTED_AND_REJECTED_COMMANDS
                ),
                payload_policy=definition.payload_policy,
                requires_open_session=definition.requires_open_session,
                creates_session=definition.creates_session,
                terminal_session_transition=(
                    definition.terminal_session_transition
                ),
            )
            for definition in _CANONICAL_COMMANDS
        )
    )


@dataclass(frozen=True)
class _CommandDefinition:
    operation_id: str
    kind: OperatorCommandKind
    family: OperatorCommandFamily
    stage: DeploymentProgramStage
    service_role: ControlPlaneServiceRole
    request_schema: str
    response_schema: str
    approval: ApprovalPolicy
    payload_policy: CommandPayloadPolicy
    requires_open_session: bool
    creates_session: bool = False
    terminal_session_transition: bool = False


_CANONICAL_COMMANDS = (
    _CommandDefinition(
        "product-descriptor.import",
        OperatorCommandKind.IMPORT_PRODUCT_DESCRIPTOR,
        OperatorCommandFamily.PRODUCT_REGISTRATION,
        DeploymentProgramStage.PLAN,
        ControlPlaneServiceRole.PLANNING,
        "ImportProductDescriptor",
        "RegisteredProductResponse",
        ApprovalPolicy.NOT_REQUIRED,
        CommandPayloadPolicy.PRODUCT_DESCRIPTOR_DOCUMENT,
        requires_open_session=False,
    ),
    _CommandDefinition(
        "workspace.create",
        OperatorCommandKind.CREATE_WORKSPACE,
        OperatorCommandFamily.WORKSPACE,
        DeploymentProgramStage.PLAN,
        ControlPlaneServiceRole.PLANNING,
        "CreateWorkspace",
        "WorkspaceReadResponse",
        ApprovalPolicy.NOT_REQUIRED,
        CommandPayloadPolicy.REDACT_OPERATOR_VALUES,
        requires_open_session=False,
    ),
    _CommandDefinition(
        "activity-plan.request",
        OperatorCommandKind.REQUEST_ACTIVITY_PLAN,
        OperatorCommandFamily.ACTIVITY_PLANNING,
        DeploymentProgramStage.PLAN,
        ControlPlaneServiceRole.PLANNING,
        "RequestActivityPlan",
        "ActivityPlanningResult",
        ApprovalPolicy.SUBMITS_FOR_APPROVAL,
        CommandPayloadPolicy.PLAN_DESCRIPTOR_REFERENCE,
        requires_open_session=True,
    ),
    _CommandDefinition(
        "approval.decide",
        OperatorCommandKind.DECIDE_APPROVAL,
        OperatorCommandFamily.APPROVAL,
        DeploymentProgramStage.APPROVE,
        ControlPlaneServiceRole.APPROVAL,
        "DecideApproval",
        "ApprovalDecisionResult",
        ApprovalPolicy.DECIDES_APPROVAL,
        CommandPayloadPolicy.APPROVAL_RISK_EVIDENCE,
        requires_open_session=True,
    ),
    _CommandDefinition(
        "approval.request",
        OperatorCommandKind.REQUEST_APPROVAL,
        OperatorCommandFamily.APPROVAL,
        DeploymentProgramStage.PLAN,
        ControlPlaneServiceRole.APPROVAL,
        "RequestApproval",
        "ApprovalRequestResult",
        ApprovalPolicy.SUBMITS_FOR_APPROVAL,
        CommandPayloadPolicy.APPROVAL_RISK_EVIDENCE,
        requires_open_session=True,
    ),
    _CommandDefinition(
        "desired-graph.set",
        OperatorCommandKind.SET_DESIRED_GRAPH,
        OperatorCommandFamily.DESIRED_GRAPH,
        DeploymentProgramStage.PLAN,
        ControlPlaneServiceRole.PLANNING,
        "SetDesiredGraph",
        "DesiredGraphEditResult",
        ApprovalPolicy.SUBMITS_FOR_APPROVAL,
        CommandPayloadPolicy.GRAPH_DESCRIPTOR_REFERENCE,
        requires_open_session=True,
    ),
    _CommandDefinition(
        "operation-session.cancel",
        OperatorCommandKind.CANCEL_OPERATION_SESSION,
        OperatorCommandFamily.OPERATION_SESSION,
        DeploymentProgramStage.PLAN,
        ControlPlaneServiceRole.LIFECYCLE,
        "CancelOperationSession",
        "OperationCommandResult",
        ApprovalPolicy.NOT_REQUIRED,
        CommandPayloadPolicy.REDACT_OPERATOR_VALUES,
        requires_open_session=True,
        terminal_session_transition=True,
    ),
    _CommandDefinition(
        "operation-session.close",
        OperatorCommandKind.CLOSE_OPERATION_SESSION,
        OperatorCommandFamily.OPERATION_SESSION,
        DeploymentProgramStage.PLAN,
        ControlPlaneServiceRole.LIFECYCLE,
        "CloseOperationSession",
        "OperationCommandResult",
        ApprovalPolicy.NOT_REQUIRED,
        CommandPayloadPolicy.REDACT_OPERATOR_VALUES,
        requires_open_session=True,
        terminal_session_transition=True,
    ),
    _CommandDefinition(
        "operation-session.record-action",
        OperatorCommandKind.RECORD_OPERATION_ACTION,
        OperatorCommandFamily.OPERATION_SESSION,
        DeploymentProgramStage.PLAN,
        ControlPlaneServiceRole.LIFECYCLE,
        "RecordOperationAction",
        "OperationCommandResult",
        ApprovalPolicy.NOT_REQUIRED,
        CommandPayloadPolicy.REDACT_OPERATOR_VALUES,
        requires_open_session=True,
    ),
    _CommandDefinition(
        "operation-session.start",
        OperatorCommandKind.START_OPERATION_SESSION,
        OperatorCommandFamily.OPERATION_SESSION,
        DeploymentProgramStage.PLAN,
        ControlPlaneServiceRole.LIFECYCLE,
        "StartOperationSession",
        "OperationCommandResult",
        ApprovalPolicy.NOT_REQUIRED,
        CommandPayloadPolicy.REDACT_OPERATOR_VALUES,
        requires_open_session=False,
        creates_session=True,
    ),
)


def _reject_duplicates(field: str, values: object) -> None:
    sequence = tuple(values)
    if len(sequence) != len(set(sequence)):
        raise InvalidCommandWorkflowContract(f"{field} values must be unique")


def _validate_identity(value: str, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise InvalidCommandWorkflowContract(f"{field} must be non-empty text")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if any(character not in allowed for character in value):
        raise InvalidCommandWorkflowContract(
            f"{field} must contain only letters, numbers, dots, dashes, or underscores"
        )


def _validate_bool(value: object, field: str) -> None:
    if type(value) is not bool:
        raise InvalidCommandWorkflowContract(f"{field} must be bool")


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise InvalidCommandWorkflowContract(f"{field} must be text")
    return value


def _bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise InvalidCommandWorkflowContract(f"{field} must be bool")
    return value


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InvalidCommandWorkflowContract(f"{field} must be a descriptor")
    return value
