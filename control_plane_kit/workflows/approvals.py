"""Transactional approval requests and decisions over canonical plans."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from uuid import uuid4

from control_plane_kit.policies import ApprovalPolicy
from control_plane_kit.stores import (
    ActivityHistoryStore,
    ApprovalDecisionKind,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    OperationActionKind,
    OperationActionRecord,
    OperationSessionStatus,
    PostgresUnitOfWork,
)
from control_plane_kit.workflows.commands import IdempotencyKey, InvalidOperationCommand


@dataclass(frozen=True)
class RequestPlanApproval:
    session_id: str
    plan_id: str
    actor_id: str
    actor_scopes: tuple[str, ...]
    idempotency_key: IdempotencyKey
    comment: str | None = None

    def __post_init__(self) -> None:
        _required("session_id", self.session_id)
        _required("plan_id", self.plan_id)
        _required("actor_id", self.actor_id)
        if not isinstance(self.idempotency_key, IdempotencyKey):
            raise InvalidOperationCommand("idempotency_key must be IdempotencyKey")
        object.__setattr__(self, "actor_scopes", _scopes(self.actor_scopes))


@dataclass(frozen=True)
class DecidePlanApproval:
    session_id: str
    request_id: str
    actor_id: str
    actor_scopes: tuple[str, ...]
    decision: ApprovalDecisionKind
    idempotency_key: IdempotencyKey
    comment: str | None = None

    def __post_init__(self) -> None:
        _required("session_id", self.session_id)
        _required("request_id", self.request_id)
        _required("actor_id", self.actor_id)
        if not isinstance(self.decision, ApprovalDecisionKind):
            raise InvalidOperationCommand("decision must be ApprovalDecisionKind")
        if not isinstance(self.idempotency_key, IdempotencyKey):
            raise InvalidOperationCommand("idempotency_key must be IdempotencyKey")
        object.__setattr__(self, "actor_scopes", _scopes(self.actor_scopes))


@dataclass(frozen=True)
class ApprovalRequestResult:
    request: ApprovalRequestRecord
    action: OperationActionRecord
    replayed: bool = False

    def __post_init__(self) -> None:
        if self.action.action_type is not OperationActionKind.APPROVAL_REQUESTED:
            raise InvalidOperationCommand("request result requires request action evidence")
        if self.action.session_id != self.request.session_id:
            raise InvalidOperationCommand("request and action must share a session")
        if self.action.payload.get("request_id") != self.request.request_id:
            raise InvalidOperationCommand("request action must reference request truth")
        if self.action.payload.get("plan_id") != self.request.plan_id:
            raise InvalidOperationCommand("request action must reference plan truth")

    def descriptor(self) -> dict[str, object]:
        return {
            "request_id": self.request.request_id,
            "session_id": self.request.session_id,
            "plan_id": self.request.plan_id,
            "state": "pending",
            "required_scope": self.request.required_scope,
            "max_risk": self.request.max_risk.value,
            "destructive": self.request.destructive,
            "requested_by": self.request.requested_by,
            "requested_at": self.request.requested_at,
            "action_id": self.action.action_id,
            "replayed": self.replayed,
        }


@dataclass(frozen=True)
class ApprovalDecisionResult:
    request: ApprovalRequestRecord
    decision: ApprovalDecisionRecord
    action: OperationActionRecord
    replayed: bool = False

    def __post_init__(self) -> None:
        if self.decision.request_id != self.request.request_id:
            raise InvalidOperationCommand("decision must answer the supplied request")
        if self.decision.scope != self.request.required_scope:
            raise InvalidOperationCommand("decision scope must satisfy request evidence")
        if self.action.action_type is not OperationActionKind.APPROVAL_DECIDED:
            raise InvalidOperationCommand("decision result requires decision action evidence")
        if self.action.session_id != self.request.session_id:
            raise InvalidOperationCommand("decision action and request must share a session")
        if self.action.payload.get("request_id") != self.request.request_id:
            raise InvalidOperationCommand("decision action must reference request truth")
        if self.action.payload.get("decision_id") != self.decision.decision_id:
            raise InvalidOperationCommand("decision action must reference decision truth")

    def descriptor(self) -> dict[str, object]:
        return {
            "request_id": self.request.request_id,
            "plan_id": self.request.plan_id,
            "state": self.decision.decision.value,
            "decision_id": self.decision.decision_id,
            "decided_by": self.decision.actor_id,
            "scope": self.decision.scope,
            "decided_at": self.decision.decided_at,
            "destructive": self.request.destructive,
            "action_id": self.action.action_id,
            "replayed": self.replayed,
        }


class ApprovalWorkflowError(RuntimeError):
    pass


class ApprovalAuthorizationDenied(ApprovalWorkflowError):
    pass


class ApprovalTargetNotFound(ApprovalWorkflowError):
    pass


class ApprovalStateConflict(ApprovalWorkflowError):
    pass


class ApprovalIdempotencyConflict(ApprovalWorkflowError):
    pass


Clock = Callable[[], str]
IdFactory = Callable[[], str]
UnitOfWorkFactory = Callable[[], PostgresUnitOfWork]


def _uuid() -> str:
    return uuid4().hex


class ApprovalCommandService:
    """Interpret approval commands inside one caller-owned transaction each."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        clock: Clock,
        id_factory: IdFactory = _uuid,
        policy: ApprovalPolicy | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._id_factory = id_factory
        self._policy = policy or ApprovalPolicy()

    def execute(
        self,
        command: RequestPlanApproval | DecidePlanApproval,
    ) -> ApprovalRequestResult | ApprovalDecisionResult:
        match command:
            case RequestPlanApproval():
                return self._request(command)
            case DecidePlanApproval():
                return self._decide(command)
        raise InvalidOperationCommand(
            f"unsupported approval command {type(command).__name__}"
        )

    def _request(self, command: RequestPlanApproval) -> ApprovalRequestResult:
        fingerprint = _fingerprint(command)
        with self._unit_of_work_factory() as work:
            history = work.stores.activity_history
            session = _session(history, command.session_id)
            replay = history.approval_request_for_idempotency(
                command.session_id, command.idempotency_key.value
            )
            if replay is not None:
                return _request_replay(history, replay, fingerprint)
            _require_unused_action_key(
                history, command.session_id, command.idempotency_key.value
            )
            _require_open(session)
            try:
                plan = history.get_plan(command.plan_id)
            except KeyError as error:
                raise ApprovalTargetNotFound(
                    f"activity plan {command.plan_id!r} does not exist"
                ) from error
            if plan.session_id != command.session_id:
                raise ApprovalStateConflict("plan and approval request must share a session")
            if not plan.plan.ready_for_execution:
                raise ApprovalStateConflict(
                    "plan contains review blockers and cannot be requested for approval"
                )
            authority = self._policy.can_request_plan(command.actor_scopes)
            if not authority.allowed:
                raise ApprovalAuthorizationDenied(authority.reason)
            requirement = self._policy.requirement_for(plan.plan)

            ordinal = history.next_action_ordinal(command.session_id)
            locked_session = _session(history, command.session_id)
            replay = history.approval_request_for_idempotency(
                command.session_id, command.idempotency_key.value
            )
            if replay is not None:
                return _request_replay(history, replay, fingerprint)
            _require_unused_action_key(
                history, command.session_id, command.idempotency_key.value
            )
            _require_open(locked_session)
            created_at = self._clock()
            request = history.add_approval_request(
                ApprovalRequestRecord(
                    request_id=self._id_factory(),
                    session_id=command.session_id,
                    plan_id=command.plan_id,
                    requested_by=command.actor_id,
                    requested_at=created_at,
                    required_scope=requirement.required_scope,
                    max_risk=requirement.max_risk,
                    destructive=requirement.destructive,
                    comment=command.comment,
                    idempotency_key=command.idempotency_key.value,
                    intent_fingerprint=fingerprint,
                )
            )
            action = history.add_action(
                OperationActionRecord(
                    action_id=self._id_factory(),
                    session_id=command.session_id,
                    ordinal=ordinal,
                    action_type=OperationActionKind.APPROVAL_REQUESTED,
                    actor_id=command.actor_id,
                    payload=_request_evidence(request),
                    created_at=created_at,
                    idempotency_key=command.idempotency_key.value,
                    intent_fingerprint=fingerprint,
                )
            )
            work.commit()
            return ApprovalRequestResult(request, action)

    def _decide(self, command: DecidePlanApproval) -> ApprovalDecisionResult:
        fingerprint = _fingerprint(command)
        with self._unit_of_work_factory() as work:
            history = work.stores.activity_history
            session = _session(history, command.session_id)
            try:
                request = history.get_approval_request(command.request_id)
            except KeyError as error:
                raise ApprovalTargetNotFound(
                    f"approval request {command.request_id!r} does not exist"
                ) from error
            if request.session_id != command.session_id:
                raise ApprovalStateConflict("request and decision must share a session")
            replay = history.approval_decision_for_idempotency(
                command.request_id, command.idempotency_key.value
            )
            if replay is not None:
                return _decision_replay(history, request, replay, fingerprint)
            _require_unused_action_key(
                history, command.session_id, command.idempotency_key.value
            )
            _require_open(session)
            if history.approval_decision_for_request(command.request_id) is not None:
                raise ApprovalStateConflict("approval request already has a decision")
            authority = self._policy.can_approve_plan(
                command.actor_scopes,
                destructive=request.destructive,
            )
            if not authority.allowed:
                raise ApprovalAuthorizationDenied(authority.reason)

            ordinal = history.next_action_ordinal(command.session_id)
            locked_session = _session(history, command.session_id)
            replay = history.approval_decision_for_idempotency(
                command.request_id, command.idempotency_key.value
            )
            if replay is not None:
                return _decision_replay(history, request, replay, fingerprint)
            _require_unused_action_key(
                history, command.session_id, command.idempotency_key.value
            )
            _require_open(locked_session)
            if history.approval_decision_for_request(command.request_id) is not None:
                raise ApprovalStateConflict("approval request already has a decision")
            decided_at = self._clock()
            decision = history.add_approval_decision(
                ApprovalDecisionRecord(
                    decision_id=self._id_factory(),
                    request_id=command.request_id,
                    actor_id=command.actor_id,
                    decision=command.decision,
                    scope=request.required_scope,
                    decided_at=decided_at,
                    comment=command.comment,
                    idempotency_key=command.idempotency_key.value,
                    intent_fingerprint=fingerprint,
                )
            )
            action = history.add_action(
                OperationActionRecord(
                    action_id=self._id_factory(),
                    session_id=command.session_id,
                    ordinal=ordinal,
                    action_type=OperationActionKind.APPROVAL_DECIDED,
                    actor_id=command.actor_id,
                    payload={
                        **_request_evidence(request),
                        "decision_id": decision.decision_id,
                        "decision": decision.decision.value,
                        "scope": decision.scope,
                    },
                    created_at=decided_at,
                    idempotency_key=command.idempotency_key.value,
                    intent_fingerprint=fingerprint,
                )
            )
            work.commit()
            return ApprovalDecisionResult(request, decision, action)


def _session(history: ActivityHistoryStore, session_id: str):
    try:
        return history.get_session(session_id)
    except KeyError as error:
        raise ApprovalTargetNotFound(
            f"operation session {session_id!r} does not exist"
        ) from error


def _require_open(session: object) -> None:
    if getattr(session, "status") is not OperationSessionStatus.OPEN:
        raise ApprovalStateConflict("approval commands require an open operation session")


def _request_replay(
    history: ActivityHistoryStore,
    request: ApprovalRequestRecord,
    fingerprint: str,
) -> ApprovalRequestResult:
    if request.intent_fingerprint != fingerprint:
        raise ApprovalIdempotencyConflict(
            "idempotency key was used for different approval-request intent"
        )
    action = history.action_for_idempotency(
        request.session_id,
        request.idempotency_key or "",
    )
    if action is None or action.action_type is not OperationActionKind.APPROVAL_REQUESTED:
        raise ApprovalWorkflowError("approval request is missing operation evidence")
    if action.intent_fingerprint != fingerprint:
        raise ApprovalWorkflowError("approval request action fingerprint is inconsistent")
    return ApprovalRequestResult(request, action, replayed=True)


def _decision_replay(
    history: ActivityHistoryStore,
    request: ApprovalRequestRecord,
    decision: ApprovalDecisionRecord,
    fingerprint: str,
) -> ApprovalDecisionResult:
    if decision.intent_fingerprint != fingerprint:
        raise ApprovalIdempotencyConflict(
            "idempotency key was used for different approval-decision intent"
        )
    action = history.action_for_idempotency(
        request.session_id,
        decision.idempotency_key or "",
    )
    if action is None or action.action_type is not OperationActionKind.APPROVAL_DECIDED:
        raise ApprovalWorkflowError("approval decision is missing operation evidence")
    if action.intent_fingerprint != fingerprint:
        raise ApprovalWorkflowError("approval decision action fingerprint is inconsistent")
    return ApprovalDecisionResult(request, decision, action, replayed=True)


def _request_evidence(request: ApprovalRequestRecord) -> dict[str, object]:
    return {
        "request_id": request.request_id,
        "plan_id": request.plan_id,
        "required_scope": request.required_scope,
        "max_risk": request.max_risk.value,
        "destructive": request.destructive,
    }


def _require_unused_action_key(
    history: ActivityHistoryStore,
    session_id: str,
    idempotency_key: str,
) -> None:
    if history.action_for_idempotency(session_id, idempotency_key) is not None:
        raise ApprovalIdempotencyConflict(
            "idempotency key was already used for another operation action"
        )


def _fingerprint(command: RequestPlanApproval | DecidePlanApproval) -> str:
    match command:
        case RequestPlanApproval():
            intent = {
                "command": "request_plan_approval",
                "session_id": command.session_id,
                "plan_id": command.plan_id,
                "actor_id": command.actor_id,
                "comment": command.comment,
            }
        case DecidePlanApproval():
            intent = {
                "command": "decide_plan_approval",
                "session_id": command.session_id,
                "request_id": command.request_id,
                "actor_id": command.actor_id,
                "decision": command.decision.value,
                "comment": command.comment,
            }
    encoded = json.dumps(intent, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _required(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOperationCommand(f"{name} must not be empty")


def _scopes(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise InvalidOperationCommand("actor scopes must be an iterable of scope strings")
    scopes = tuple(sorted(set(values)))
    if not all(isinstance(value, str) and value.strip() for value in scopes):
        raise InvalidOperationCommand("actor scopes must be non-empty strings")
    return scopes
