"""Operations command service for grouped operator workflow history."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from control_plane_kit_core.operations.commands import OperatorCommandKind
from control_plane_kit_operations.records import (
    OperationActionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
)


class OperationCommandError(RuntimeError):
    """Base error for transport-neutral operation commands."""


class InvalidOperationCommand(OperationCommandError):
    """Raised when command data does not belong to the command language."""


class OperationIdempotencyConflict(OperationCommandError):
    """Raised when one idempotency key is reused for different intent."""


class OperationSessionNotFound(OperationCommandError):
    """Raised when a command targets an unknown operation session."""


class OperationSessionStateConflict(OperationCommandError):
    """Raised when a command is invalid for the current session state."""


class OperationWorkspaceNotFound(OperationCommandError):
    """Raised when a command targets an unknown workspace."""


@dataclass(frozen=True)
class IdempotencyKey:
    """Non-empty retry identity scoped by the receiving command service."""

    value: str

    def __post_init__(self) -> None:
        _required_text(self.value, "idempotency_key")
        if len(self.value) > 200:
            raise InvalidOperationCommand(
                "idempotency_key must be at most 200 characters"
            )


@dataclass(frozen=True)
class StartOperationSession:
    workspace_id: str
    actor_id: str
    title: str
    idempotency_key: IdempotencyKey
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _required_text(self.workspace_id, "workspace_id")
        _required_text(self.actor_id, "actor_id")
        _required_text(self.title, "title")
        _require_idempotency_key(self.idempotency_key)
        _string_mapping(self.metadata, "metadata")
        _reject_secret_values(self.metadata, "metadata")

    def descriptor(self) -> dict[str, object]:
        return {
            "command": OperatorCommandKind.START_OPERATION_SESSION.value,
            "workspace_id": self.workspace_id,
            "actor_id": self.actor_id,
            "title": self.title,
            "idempotency_key": self.idempotency_key.value,
            "metadata": _redacted_mapping(self.metadata),
        }


@dataclass(frozen=True)
class CloseOperationSession:
    session_id: str
    actor_id: str
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _required_text(self.session_id, "session_id")
        _required_text(self.actor_id, "actor_id")
        _require_idempotency_key(self.idempotency_key)

    def descriptor(self) -> dict[str, object]:
        return _transition_descriptor(
            OperatorCommandKind.CLOSE_OPERATION_SESSION,
            self.session_id,
            self.actor_id,
            self.idempotency_key,
        )


@dataclass(frozen=True)
class CancelOperationSession:
    session_id: str
    actor_id: str
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _required_text(self.session_id, "session_id")
        _required_text(self.actor_id, "actor_id")
        _require_idempotency_key(self.idempotency_key)

    def descriptor(self) -> dict[str, object]:
        return _transition_descriptor(
            OperatorCommandKind.CANCEL_OPERATION_SESSION,
            self.session_id,
            self.actor_id,
            self.idempotency_key,
        )


@dataclass(frozen=True)
class RecordOperationAction:
    session_id: str
    actor_id: str
    action_type: OperatorCommandKind
    idempotency_key: IdempotencyKey
    payload: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _required_text(self.session_id, "session_id")
        _required_text(self.actor_id, "actor_id")
        _require_idempotency_key(self.idempotency_key)
        if not isinstance(self.action_type, OperatorCommandKind):
            raise InvalidOperationCommand("action_type must be OperatorCommandKind")
        if self.action_type in _RESERVED_MANUAL_ACTIONS:
            raise InvalidOperationCommand("reserved operation actions cannot be forged")
        _string_keys(self.payload, "payload")
        _reject_secret_values(self.payload, "payload")

    def descriptor(self) -> dict[str, object]:
        return {
            "command": OperatorCommandKind.RECORD_OPERATION_ACTION.value,
            "session_id": self.session_id,
            "actor_id": self.actor_id,
            "action_type": self.action_type.value,
            "idempotency_key": self.idempotency_key.value,
            "payload": _redacted_mapping(self.payload),
        }


@dataclass(frozen=True)
class OperationCommandResult:
    """One operation command result and durable action evidence."""

    session: OperationSessionRecord
    action: OperationActionRecord
    replayed: bool = False

    def descriptor(self) -> dict[str, object]:
        return {
            "session_id": self.session.session_id,
            "status": self.session.status.value,
            "action_id": self.action.action_id,
            "action_type": self.action.action_type.value,
            "ordinal": self.action.ordinal,
            "replayed": self.replayed,
        }


OperationCommand = (
    StartOperationSession
    | CloseOperationSession
    | CancelOperationSession
    | RecordOperationAction
)


class OperationCommandService:
    """Application service owning operation-session transaction boundaries."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        clock: Callable[[], str],
        id_factory: Callable[[], str],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._id_factory = id_factory

    def execute(self, command: OperationCommand) -> OperationCommandResult:
        if isinstance(command, StartOperationSession):
            return self._start(command)
        if isinstance(command, CloseOperationSession):
            return self._terminal(
                command,
                replacement=OperationSessionStatus.CLOSED,
                action_type=OperatorCommandKind.CLOSE_OPERATION_SESSION,
            )
        if isinstance(command, CancelOperationSession):
            return self._terminal(
                command,
                replacement=OperationSessionStatus.CANCELLED,
                action_type=OperatorCommandKind.CANCEL_OPERATION_SESSION,
            )
        if isinstance(command, RecordOperationAction):
            return self._record(command)
        raise InvalidOperationCommand("unsupported operation command")

    def _start(self, command: StartOperationSession) -> OperationCommandResult:
        fingerprint = _fingerprint(command)
        with self._unit_of_work_factory() as unit_of_work:
            try:
                unit_of_work.stores.workspaces.get(command.workspace_id)
            except KeyError as error:
                raise OperationWorkspaceNotFound("workspace was not found") from error
            unit_of_work.stores.activity_history.lock_session_idempotency(
                command.workspace_id,
                command.idempotency_key.value,
            )
            existing = unit_of_work.stores.activity_history.session_for_idempotency(
                command.workspace_id,
                command.idempotency_key.value,
            )
            if existing is not None:
                if existing.intent_fingerprint != fingerprint:
                    raise OperationIdempotencyConflict(
                        "idempotency key was reused with different intent"
                    )
                action = _require_idempotent_action(
                    unit_of_work,
                    existing.session_id,
                    command.idempotency_key.value,
                    fingerprint,
                )
                unit_of_work.commit()
                return OperationCommandResult(existing, action, replayed=True)
            session = OperationSessionRecord(
                session_id=self._id_factory(),
                workspace_id=command.workspace_id,
                actor_id=command.actor_id,
                title=command.title,
                status=OperationSessionStatus.OPEN,
                created_at=self._clock(),
                metadata=command.metadata,
                idempotency_key=command.idempotency_key.value,
                intent_fingerprint=fingerprint,
            )
            action = OperationActionRecord(
                action_id=self._id_factory(),
                session_id=session.session_id,
                ordinal=1,
                action_type=OperatorCommandKind.START_OPERATION_SESSION,
                actor_id=command.actor_id,
                payload={"workspace_id": command.workspace_id},
                created_at=session.created_at,
                idempotency_key=command.idempotency_key.value,
                intent_fingerprint=fingerprint,
            )
            unit_of_work.stores.activity_history.add_session(session)
            unit_of_work.stores.activity_history.add_action(action)
            unit_of_work.commit()
            return OperationCommandResult(session, action)

    def _terminal(
        self,
        command: CloseOperationSession | CancelOperationSession,
        *,
        replacement: OperationSessionStatus,
        action_type: OperatorCommandKind,
    ) -> OperationCommandResult:
        fingerprint = _fingerprint(command)
        with self._unit_of_work_factory() as unit_of_work:
            session = _get_session(unit_of_work, command.session_id)
            existing = unit_of_work.stores.activity_history.action_for_idempotency(
                command.session_id,
                command.idempotency_key.value,
            )
            if existing is not None:
                if existing.intent_fingerprint != fingerprint:
                    raise OperationIdempotencyConflict(
                        "idempotency key was reused with different intent"
                    )
                unit_of_work.commit()
                return OperationCommandResult(
                    unit_of_work.stores.activity_history.get_session(command.session_id),
                    existing,
                    replayed=True,
                )
            if session.status is not OperationSessionStatus.OPEN:
                raise OperationSessionStateConflict("operation session is not open")
            action = OperationActionRecord(
                action_id=self._id_factory(),
                session_id=session.session_id,
                ordinal=unit_of_work.stores.activity_history.next_action_ordinal(
                    session.session_id
                ),
                action_type=action_type,
                actor_id=command.actor_id,
                payload={"previous_status": session.status.value},
                created_at=self._clock(),
                idempotency_key=command.idempotency_key.value,
                intent_fingerprint=fingerprint,
            )
            updated = unit_of_work.stores.activity_history.transition_open_session(
                session.session_id,
                replacement=replacement,
                closed_at=action.created_at,
            )
            if updated is None:
                raise OperationSessionStateConflict("operation session is not open")
            unit_of_work.stores.activity_history.add_action(action)
            unit_of_work.commit()
            return OperationCommandResult(updated, action)

    def _record(self, command: RecordOperationAction) -> OperationCommandResult:
        fingerprint = _fingerprint(command)
        with self._unit_of_work_factory() as unit_of_work:
            session = _get_session(unit_of_work, command.session_id)
            existing = unit_of_work.stores.activity_history.action_for_idempotency(
                command.session_id,
                command.idempotency_key.value,
            )
            if existing is not None:
                if existing.intent_fingerprint != fingerprint:
                    raise OperationIdempotencyConflict(
                        "idempotency key was reused with different intent"
                    )
                unit_of_work.commit()
                return OperationCommandResult(session, existing, replayed=True)
            if session.status is not OperationSessionStatus.OPEN:
                raise OperationSessionStateConflict("operation session is not open")
            action = OperationActionRecord(
                action_id=self._id_factory(),
                session_id=session.session_id,
                ordinal=unit_of_work.stores.activity_history.next_action_ordinal(
                    session.session_id
                ),
                action_type=command.action_type,
                actor_id=command.actor_id,
                payload=command.payload,
                created_at=self._clock(),
                idempotency_key=command.idempotency_key.value,
                intent_fingerprint=fingerprint,
            )
            unit_of_work.stores.activity_history.add_action(action)
            unit_of_work.commit()
            return OperationCommandResult(session, action)


_RESERVED_MANUAL_ACTIONS = frozenset(
    {
        OperatorCommandKind.START_OPERATION_SESSION,
        OperatorCommandKind.CLOSE_OPERATION_SESSION,
        OperatorCommandKind.CANCEL_OPERATION_SESSION,
        OperatorCommandKind.RECORD_OPERATION_ACTION,
    }
)


def _get_session(unit_of_work: Any, session_id: str) -> OperationSessionRecord:
    try:
        return unit_of_work.stores.activity_history.get_session(session_id)
    except KeyError as error:
        raise OperationSessionNotFound("operation session was not found") from error


def _require_idempotent_action(
    unit_of_work: Any,
    session_id: str,
    key: str,
    fingerprint: str,
) -> OperationActionRecord:
    action = unit_of_work.stores.activity_history.action_for_idempotency(
        session_id,
        key,
    )
    if action is None:
        raise OperationIdempotencyConflict("idempotent session has no action evidence")
    if action.intent_fingerprint != fingerprint:
        raise OperationIdempotencyConflict(
            "idempotency key was reused with different action intent"
        )
    return action


def _fingerprint(command: OperationCommand) -> str:
    if isinstance(command, StartOperationSession):
        intent: Mapping[str, object] = {
            "command": "start",
            "workspace_id": command.workspace_id,
            "actor_id": command.actor_id,
            "title": command.title,
            "metadata": command.metadata,
        }
    elif isinstance(command, CloseOperationSession):
        intent = {
            "command": "close",
            "session_id": command.session_id,
            "actor_id": command.actor_id,
        }
    elif isinstance(command, CancelOperationSession):
        intent = {
            "command": "cancel",
            "session_id": command.session_id,
            "actor_id": command.actor_id,
        }
    elif isinstance(command, RecordOperationAction):
        intent = {
            "command": "record",
            "session_id": command.session_id,
            "actor_id": command.actor_id,
            "action_type": command.action_type.value,
            "payload": command.payload,
        }
    else:
        raise InvalidOperationCommand("unsupported operation command")
    try:
        encoded = json.dumps(intent, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise InvalidOperationCommand("command intent must be JSON serializable") from error
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _require_idempotency_key(value: IdempotencyKey) -> None:
    if not isinstance(value, IdempotencyKey):
        raise InvalidOperationCommand("idempotency_key must be IdempotencyKey")


def _required_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOperationCommand(f"{field} must not be empty")


def _string_mapping(value: Mapping[object, object], field: str) -> None:
    _string_keys(value, field)
    if not all(isinstance(item, str) for item in value.values()):
        raise InvalidOperationCommand(f"{field} values must be strings")


def _string_keys(value: Mapping[object, object], field: str) -> None:
    if not isinstance(value, Mapping):
        raise InvalidOperationCommand(f"{field} must be a mapping")
    if not all(isinstance(key, str) for key in value):
        raise InvalidOperationCommand(f"{field} keys must be strings")


def _redacted_mapping(value: Mapping[str, object]) -> dict[str, str]:
    return {key: "<redacted>" for key in sorted(value)}


_SECRET_MARKERS = ("secret", "token", "password", "private_key", "credential", "api_key")


def _reject_secret_values(value: object, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            normalized = str(key).lower()
            if any(marker in normalized for marker in _SECRET_MARKERS) and not (
                normalized.endswith("_ref")
            ):
                raise InvalidOperationCommand(
                    f"{child_path} cannot contain a secret value; persist a secret reference"
                )
            _reject_secret_values(child, child_path)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_secret_values(child, f"{path}[{index}]")


def _transition_descriptor(
    command: OperatorCommandKind,
    session_id: str,
    actor_id: str,
    idempotency_key: IdempotencyKey,
) -> dict[str, object]:
    return {
        "command": command.value,
        "session_id": session_id,
        "actor_id": actor_id,
        "idempotency_key": idempotency_key.value,
    }
