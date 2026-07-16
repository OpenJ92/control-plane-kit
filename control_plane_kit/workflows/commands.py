"""Typed command language for grouped operator intent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from control_plane_kit.stores.records import (
    OperationActionKind,
    OperationActionRecord,
    OperationSessionRecord,
)


class OperationCommandError(RuntimeError):
    """Base error for transport-neutral operation commands."""


class InvalidOperationCommand(OperationCommandError):
    """Raised when command data does not belong to the command language."""


class OperationSessionNotFound(OperationCommandError):
    """Raised when a command targets an unknown operation session."""


class OperationSessionStateConflict(OperationCommandError):
    """Raised when a command is invalid for the current session state."""


class OperationIdempotencyConflict(OperationCommandError):
    """Raised when one idempotency key is reused for different intent."""


@dataclass(frozen=True)
class IdempotencyKey:
    """Non-empty retry identity scoped by the receiving command service."""

    value: str

    def __post_init__(self) -> None:
        _required("idempotency_key", self.value)
        if len(self.value) > 200:
            raise InvalidOperationCommand("idempotency_key must be at most 200 characters")


@dataclass(frozen=True)
class StartOperationSession:
    workspace_id: str
    actor_id: str
    title: str
    idempotency_key: IdempotencyKey
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _required("workspace_id", self.workspace_id)
        _required("actor_id", self.actor_id)
        _required("title", self.title)
        _string_mapping("metadata", self.metadata)

    def descriptor(self) -> dict[str, object]:
        return {
            "command": "start_operation_session",
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
        _required("session_id", self.session_id)
        _required("actor_id", self.actor_id)

    def descriptor(self) -> dict[str, object]:
        return _transition_descriptor("close_operation_session", self)


@dataclass(frozen=True)
class CancelOperationSession:
    session_id: str
    actor_id: str
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _required("session_id", self.session_id)
        _required("actor_id", self.actor_id)

    def descriptor(self) -> dict[str, object]:
        return _transition_descriptor("cancel_operation_session", self)


@dataclass(frozen=True)
class RecordOperationAction:
    session_id: str
    actor_id: str
    action_type: OperationActionKind
    idempotency_key: IdempotencyKey
    payload: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _required("session_id", self.session_id)
        _required("actor_id", self.actor_id)
        if not isinstance(self.action_type, OperationActionKind):
            raise InvalidOperationCommand("action_type must be OperationActionKind")
        _string_keys("payload", self.payload)

    def descriptor(self) -> dict[str, object]:
        return {
            "command": "record_operation_action",
            "session_id": self.session_id,
            "actor_id": self.actor_id,
            "action_type": self.action_type.value,
            "idempotency_key": self.idempotency_key.value,
            "payload": _redacted_mapping(self.payload),
        }


@dataclass(frozen=True)
class OperationCommandResult:
    """One session command result and its durable action evidence."""

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


def _required(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOperationCommand(f"{name} must not be empty")


def _string_mapping(name: str, value: Mapping[object, object]) -> None:
    _string_keys(name, value)
    if not all(isinstance(item, str) for item in value.values()):
        raise InvalidOperationCommand(f"{name} values must be strings")


def _string_keys(name: str, value: Mapping[object, object]) -> None:
    if not all(isinstance(key, str) for key in value):
        raise InvalidOperationCommand(f"{name} keys must be strings")


def _redacted_mapping(value: Mapping[str, object]) -> dict[str, str]:
    """Describe command shape without publishing operator-supplied values."""

    return {key: "<redacted>" for key in sorted(value)}


def _transition_descriptor(
    command: str,
    value: CloseOperationSession | CancelOperationSession,
) -> dict[str, object]:
    return {
        "command": command,
        "session_id": value.session_id,
        "actor_id": value.actor_id,
        "idempotency_key": value.idempotency_key.value,
    }
