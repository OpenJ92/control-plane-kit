"""Approval-stage operation commands."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Callable

from control_plane_kit_core.operations.commands import OperatorCommandKind
from control_plane_kit_core.policies import ApprovalPolicy, PolicyScope
from control_plane_kit_operations.records import (
    ApprovalDecisionKind,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    OperationActionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
)
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    InvalidOperationCommand,
)


class ApprovalWorkflowError(RuntimeError):
    """Base error for approval command interpretation."""


class ApprovalAuthorizationDenied(ApprovalWorkflowError):
    """Raised when the actor lacks authority for an approval command."""


class ApprovalIdempotencyConflict(ApprovalWorkflowError):
    """Raised when one idempotency key is reused for different intent."""


class ApprovalStateConflict(ApprovalWorkflowError):
    """Raised when approval truth is not in the required state."""


class ApprovalTargetNotFound(ApprovalWorkflowError):
    """Raised when approval command target truth is missing."""


@dataclass(frozen=True)
class RequestApproval:
    """Request authority over a persisted activity plan."""

    session_id: str
    plan_id: str
    actor_id: str
    actor_scopes: tuple[PolicyScope, ...]
    idempotency_key: IdempotencyKey
    comment: str | None = None

    def __post_init__(self) -> None:
        _required_text(self.session_id, "session_id")
        _required_text(self.plan_id, "plan_id")
        _required_text(self.actor_id, "actor_id")
        _require_idempotency_key(self.idempotency_key)
        object.__setattr__(self, "actor_scopes", _scopes(self.actor_scopes))
        _optional_text(self.comment, "comment")

    def descriptor(self) -> dict[str, object]:
        return {
            "command": OperatorCommandKind.REQUEST_APPROVAL.value,
            "session_id": self.session_id,
            "plan_id": self.plan_id,
            "actor_id": self.actor_id,
            "actor_scopes": tuple(scope.value for scope in self.actor_scopes),
            "idempotency_key": self.idempotency_key.value,
            "comment_present": self.comment is not None,
        }


@dataclass(frozen=True)
class DecideApproval:
    """Record an immutable answer to one approval request."""

    session_id: str
    request_id: str
    actor_id: str
    actor_scopes: tuple[PolicyScope, ...]
    decision: ApprovalDecisionKind
    idempotency_key: IdempotencyKey
    comment: str | None = None

    def __post_init__(self) -> None:
        _required_text(self.session_id, "session_id")
        _required_text(self.request_id, "request_id")
        _required_text(self.actor_id, "actor_id")
        _require_idempotency_key(self.idempotency_key)
        object.__setattr__(self, "actor_scopes", _scopes(self.actor_scopes))
        if not isinstance(self.decision, ApprovalDecisionKind):
            raise InvalidOperationCommand("decision must be ApprovalDecisionKind")
        _optional_text(self.comment, "comment")

    def descriptor(self) -> dict[str, object]:
        return {
            "command": OperatorCommandKind.DECIDE_APPROVAL.value,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "actor_id": self.actor_id,
            "actor_scopes": tuple(scope.value for scope in self.actor_scopes),
            "decision": self.decision.value,
            "idempotency_key": self.idempotency_key.value,
            "comment_present": self.comment is not None,
        }


@dataclass(frozen=True)
class ApprovalRequestResult:
    """Approval request result plus ordered operation-action evidence."""

    request: ApprovalRequestRecord
    action: OperationActionRecord
    replayed: bool = False

    def __post_init__(self) -> None:
        if self.action.action_type is not OperatorCommandKind.REQUEST_APPROVAL:
            raise InvalidOperationCommand(
                "request result requires REQUEST_APPROVAL action evidence"
            )
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
            "required_scope": self.request.required_scope.value,
            "max_risk": self.request.max_risk.value,
            "destructive": self.request.destructive,
            "requested_by": self.request.requested_by,
            "requested_at": self.request.requested_at,
            "action_id": self.action.action_id,
            "action_ordinal": self.action.ordinal,
            "replayed": self.replayed,
        }


@dataclass(frozen=True)
class ApprovalDecisionResult:
    """Approval decision result plus ordered operation-action evidence."""

    request: ApprovalRequestRecord
    decision: ApprovalDecisionRecord
    action: OperationActionRecord
    replayed: bool = False

    def __post_init__(self) -> None:
        if self.decision.request_id != self.request.request_id:
            raise InvalidOperationCommand("decision must answer the supplied request")
        if self.decision.scope != self.request.required_scope:
            raise InvalidOperationCommand("decision scope must satisfy request truth")
        if self.action.action_type is not OperatorCommandKind.DECIDE_APPROVAL:
            raise InvalidOperationCommand(
                "decision result requires DECIDE_APPROVAL action evidence"
            )
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
            "scope": self.decision.scope.value,
            "decided_at": self.decision.decided_at,
            "destructive": self.request.destructive,
            "action_id": self.action.action_id,
            "action_ordinal": self.action.ordinal,
            "replayed": self.replayed,
        }


ApprovalCommand = RequestApproval | DecideApproval


class ApprovalCommandService:
    """Application service owning approval transaction boundaries."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        clock: Callable[[], str],
        id_factory: Callable[[], str],
        policy: ApprovalPolicy | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._id_factory = id_factory
        self._policy = policy or ApprovalPolicy()

    def execute(
        self,
        command: ApprovalCommand,
    ) -> ApprovalRequestResult | ApprovalDecisionResult:
        if isinstance(command, RequestApproval):
            return self._request(command)
        if isinstance(command, DecideApproval):
            return self._decide(command)
        raise InvalidOperationCommand("unsupported approval command")

    def _request(self, command: RequestApproval) -> ApprovalRequestResult:
        fingerprint = _fingerprint(command)
        with self._unit_of_work_factory() as unit_of_work:
            history = unit_of_work.stores.activity_history
            session = _session(history, command.session_id)
            replay = history.approval_request_for_idempotency(
                command.session_id,
                command.idempotency_key.value,
            )
            if replay is not None:
                result = _request_replay(history, replay, fingerprint)
                unit_of_work.commit()
                return result
            _require_unused_action_key(
                history,
                command.session_id,
                command.idempotency_key.value,
            )
            _require_open(session)
            try:
                plan = history.get_plan(command.plan_id)
            except KeyError as error:
                raise ApprovalTargetNotFound("activity plan was not found") from error
            if plan.session_id != command.session_id:
                raise ApprovalStateConflict("plan and request must share a session")
            if not plan.plan.ready_for_execution:
                raise ApprovalStateConflict(
                    "plan contains review blockers and cannot be requested"
                )
            authority = self._policy.can_request_plan(command.actor_scopes)
            if not authority.allowed:
                raise ApprovalAuthorizationDenied(authority.reason)
            requirement = self._policy.requirement_for(plan.plan)

            ordinal = history.next_action_ordinal(command.session_id)
            locked = _session(history, command.session_id)
            replay = history.approval_request_for_idempotency(
                command.session_id,
                command.idempotency_key.value,
            )
            if replay is not None:
                result = _request_replay(history, replay, fingerprint)
                unit_of_work.commit()
                return result
            _require_unused_action_key(
                history,
                command.session_id,
                command.idempotency_key.value,
            )
            _require_open(locked)
            requested_at = self._clock()
            request = history.add_approval_request(
                ApprovalRequestRecord(
                    request_id=self._id_factory(),
                    session_id=command.session_id,
                    plan_id=command.plan_id,
                    requested_by=command.actor_id,
                    requested_at=requested_at,
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
                    action_type=OperatorCommandKind.REQUEST_APPROVAL,
                    actor_id=command.actor_id,
                    payload=_request_evidence(request),
                    created_at=requested_at,
                    idempotency_key=command.idempotency_key.value,
                    intent_fingerprint=fingerprint,
                )
            )
            unit_of_work.commit()
            return ApprovalRequestResult(request, action)

    def _decide(self, command: DecideApproval) -> ApprovalDecisionResult:
        fingerprint = _fingerprint(command)
        with self._unit_of_work_factory() as unit_of_work:
            history = unit_of_work.stores.activity_history
            session = _session(history, command.session_id)
            try:
                request = history.get_approval_request(command.request_id)
            except KeyError as error:
                raise ApprovalTargetNotFound("approval request was not found") from error
            if request.session_id != command.session_id:
                raise ApprovalStateConflict("request and decision must share a session")
            replay = history.approval_decision_for_idempotency(
                command.request_id,
                command.idempotency_key.value,
            )
            if replay is not None:
                result = _decision_replay(history, request, replay, fingerprint)
                unit_of_work.commit()
                return result
            _require_unused_action_key(
                history,
                command.session_id,
                command.idempotency_key.value,
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
            locked = _session(history, command.session_id)
            replay = history.approval_decision_for_idempotency(
                command.request_id,
                command.idempotency_key.value,
            )
            if replay is not None:
                result = _decision_replay(history, request, replay, fingerprint)
                unit_of_work.commit()
                return result
            _require_unused_action_key(
                history,
                command.session_id,
                command.idempotency_key.value,
            )
            _require_open(locked)
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
                    action_type=OperatorCommandKind.DECIDE_APPROVAL,
                    actor_id=command.actor_id,
                    payload={
                        **_request_evidence(request),
                        "decision_id": decision.decision_id,
                        "decision": decision.decision.value,
                        "scope": decision.scope.value,
                    },
                    created_at=decided_at,
                    idempotency_key=command.idempotency_key.value,
                    intent_fingerprint=fingerprint,
                )
            )
            unit_of_work.commit()
            return ApprovalDecisionResult(request, decision, action)


def _session(history: Any, session_id: str) -> OperationSessionRecord:
    try:
        return history.get_session(session_id)
    except KeyError as error:
        raise ApprovalTargetNotFound("operation session was not found") from error


def _require_open(session: OperationSessionRecord) -> None:
    if session.status is not OperationSessionStatus.OPEN:
        raise ApprovalStateConflict("approval commands require an open session")


def _request_replay(
    history: Any,
    request: ApprovalRequestRecord,
    fingerprint: str,
) -> ApprovalRequestResult:
    if request.intent_fingerprint != fingerprint:
        raise ApprovalIdempotencyConflict(
            "idempotency key was used for different approval request intent"
        )
    action = history.action_for_idempotency(
        request.session_id,
        request.idempotency_key or "",
    )
    if action is None or action.action_type is not OperatorCommandKind.REQUEST_APPROVAL:
        raise ApprovalWorkflowError("approval request is missing action evidence")
    if action.intent_fingerprint != fingerprint:
        raise ApprovalWorkflowError("approval request action fingerprint is inconsistent")
    return ApprovalRequestResult(request, action, replayed=True)


def _decision_replay(
    history: Any,
    request: ApprovalRequestRecord,
    decision: ApprovalDecisionRecord,
    fingerprint: str,
) -> ApprovalDecisionResult:
    if decision.intent_fingerprint != fingerprint:
        raise ApprovalIdempotencyConflict(
            "idempotency key was used for different approval decision intent"
        )
    action = history.action_for_idempotency(
        request.session_id,
        decision.idempotency_key or "",
    )
    if action is None or action.action_type is not OperatorCommandKind.DECIDE_APPROVAL:
        raise ApprovalWorkflowError("approval decision is missing action evidence")
    if action.intent_fingerprint != fingerprint:
        raise ApprovalWorkflowError("approval decision action fingerprint is inconsistent")
    return ApprovalDecisionResult(request, decision, action, replayed=True)


def _request_evidence(request: ApprovalRequestRecord) -> dict[str, object]:
    return {
        "request_id": request.request_id,
        "plan_id": request.plan_id,
        "required_scope": request.required_scope.value,
        "max_risk": request.max_risk.value,
        "destructive": request.destructive,
    }


def _require_unused_action_key(
    history: Any,
    session_id: str,
    idempotency_key: str,
) -> None:
    if history.action_for_idempotency(session_id, idempotency_key) is not None:
        raise ApprovalIdempotencyConflict(
            "idempotency key was already used for another operation action"
        )


def _fingerprint(command: ApprovalCommand) -> str:
    if isinstance(command, RequestApproval):
        intent = {
            "command": "request-approval",
            "session_id": command.session_id,
            "plan_id": command.plan_id,
            "actor_id": command.actor_id,
            "comment": command.comment,
        }
    elif isinstance(command, DecideApproval):
        intent = {
            "command": "decide-approval",
            "session_id": command.session_id,
            "request_id": command.request_id,
            "actor_id": command.actor_id,
            "decision": command.decision.value,
            "comment": command.comment,
        }
    else:
        raise InvalidOperationCommand("unsupported approval command")
    encoded = json.dumps(intent, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _scopes(values: Iterable[PolicyScope]) -> tuple[PolicyScope, ...]:
    if isinstance(values, (str, bytes)):
        raise InvalidOperationCommand(
            "actor_scopes must be an iterable of PolicyScope"
        )
    scopes = tuple(sorted(set(values), key=lambda scope: scope.value))
    if not all(isinstance(scope, PolicyScope) for scope in scopes):
        raise InvalidOperationCommand("actor_scopes must contain only PolicyScope")
    return scopes


def _require_idempotency_key(value: IdempotencyKey) -> None:
    if not isinstance(value, IdempotencyKey):
        raise InvalidOperationCommand("idempotency_key must be IdempotencyKey")


def _required_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOperationCommand(f"{field} must not be empty")


def _optional_text(value: object, field: str) -> None:
    if value is None:
        return
    _required_text(value, field)
