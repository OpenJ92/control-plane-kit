"""Atomic interpreter for the typed operation command language."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from typing import Mapping
from uuid import uuid4

from control_plane_kit.stores import (
    ActivityHistoryStore,
    OperationActionKind,
    OperationActionRecord,
    OperationSessionRecord,
    OperationSessionStatus,
    PostgresUnitOfWork,
)
from control_plane_kit.workflows.commands import (
    CancelOperationSession,
    CloseOperationSession,
    InvalidOperationCommand,
    OperationCommandError,
    OperationCommandResult,
    OperationIdempotencyConflict,
    OperationSessionNotFound,
    OperationSessionStateConflict,
    OperationWorkspaceNotFound,
    RecordOperationAction,
    StartOperationSession,
)


Clock = Callable[[], str]
IdFactory = Callable[[], str]
UnitOfWorkFactory = Callable[[], PostgresUnitOfWork]
OperationCommand = (
    StartOperationSession
    | CloseOperationSession
    | CancelOperationSession
    | RecordOperationAction
)


def _uuid() -> str:
    return uuid4().hex


class OperationCommandService:
    """Interpret one operator command inside one Postgres transaction."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        clock: Clock,
        id_factory: IdFactory = _uuid,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._id_factory = id_factory

    def execute(self, command: OperationCommand) -> OperationCommandResult:
        match command:
            case StartOperationSession():
                return self._start(command)
            case CloseOperationSession():
                return self._transition(
                    command,
                    status=OperationSessionStatus.CLOSED,
                    action_type=OperationActionKind.SESSION_CLOSED,
                )
            case CancelOperationSession():
                return self._transition(
                    command,
                    status=OperationSessionStatus.CANCELLED,
                    action_type=OperationActionKind.SESSION_CANCELLED,
                )
            case RecordOperationAction():
                return self._record(command)
        raise InvalidOperationCommand(f"unsupported operation command {type(command).__name__}")

    def _start(self, command: StartOperationSession) -> OperationCommandResult:
        fingerprint = _fingerprint(command)
        with self._unit_of_work_factory() as work:
            history = work.stores.activity_history
            try:
                work.stores.workspace.get(command.workspace_id)
            except KeyError as error:
                raise OperationWorkspaceNotFound(
                    f"workspace {command.workspace_id!r} does not exist"
                ) from error
            history.lock_session_idempotency(
                command.workspace_id, command.idempotency_key.value
            )
            replay = history.session_for_idempotency(
                command.workspace_id, command.idempotency_key.value
            )
            if replay is not None:
                return _session_replay(history, replay, command.idempotency_key.value, fingerprint)

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
            history.add_session(session)
            action = self._append_locked(
                history,
                session=session,
                actor_id=command.actor_id,
                action_type=OperationActionKind.SESSION_STARTED,
                idempotency_key=command.idempotency_key.value,
                fingerprint=fingerprint,
                payload={"title": command.title},
            )
            work.commit()
            return OperationCommandResult(session=session, action=action)

    def _transition(
        self,
        command: CloseOperationSession | CancelOperationSession,
        *,
        status: OperationSessionStatus,
        action_type: OperationActionKind,
    ) -> OperationCommandResult:
        fingerprint = _fingerprint(command)
        with self._unit_of_work_factory() as work:
            history = work.stores.activity_history
            session = _get_session(history, command.session_id)
            replay = history.action_for_idempotency(
                command.session_id, command.idempotency_key.value
            )
            if replay is not None:
                return _action_replay(session, replay, fingerprint)
            _require_open(session)

            ordinal = history.next_action_ordinal(command.session_id)
            locked_session = _get_session(history, command.session_id)
            replay = history.action_for_idempotency(
                command.session_id, command.idempotency_key.value
            )
            if replay is not None:
                return _action_replay(locked_session, replay, fingerprint)
            _require_open(locked_session)

            changed = history.transition_open_session(
                command.session_id,
                replacement=status,
                closed_at=self._clock(),
            )
            if changed is None:
                raise OperationSessionStateConflict(
                    f"operation session {command.session_id!r} is no longer open"
                )
            action = self._add_action(
                history,
                ordinal=ordinal,
                session_id=command.session_id,
                actor_id=command.actor_id,
                action_type=action_type,
                idempotency_key=command.idempotency_key.value,
                fingerprint=fingerprint,
                payload={"closed_at": changed.closed_at},
            )
            work.commit()
            return OperationCommandResult(session=changed, action=action)

    def _record(self, command: RecordOperationAction) -> OperationCommandResult:
        if command.action_type in _SERVICE_LIFECYCLE_ACTIONS:
            raise InvalidOperationCommand(
                f"{command.action_type.value} is reserved for session lifecycle commands"
            )
        fingerprint = _fingerprint(command)
        with self._unit_of_work_factory() as work:
            history = work.stores.activity_history
            session = _get_session(history, command.session_id)
            replay = history.action_for_idempotency(
                command.session_id, command.idempotency_key.value
            )
            if replay is not None:
                return _action_replay(session, replay, fingerprint)
            _require_open(session)

            ordinal = history.next_action_ordinal(command.session_id)
            locked_session = _get_session(history, command.session_id)
            replay = history.action_for_idempotency(
                command.session_id, command.idempotency_key.value
            )
            if replay is not None:
                return _action_replay(locked_session, replay, fingerprint)
            _require_open(locked_session)

            action = self._add_action(
                history,
                ordinal=ordinal,
                session_id=command.session_id,
                actor_id=command.actor_id,
                action_type=command.action_type,
                idempotency_key=command.idempotency_key.value,
                fingerprint=fingerprint,
                payload=command.payload,
            )
            work.commit()
            return OperationCommandResult(session=locked_session, action=action)

    def _append_locked(
        self,
        history: ActivityHistoryStore,
        *,
        session: OperationSessionRecord,
        actor_id: str,
        action_type: OperationActionKind,
        idempotency_key: str,
        fingerprint: str,
        payload: dict[str, object],
    ) -> OperationActionRecord:
        ordinal = history.next_action_ordinal(session.session_id)
        return self._add_action(
            history,
            ordinal=ordinal,
            session_id=session.session_id,
            actor_id=actor_id,
            action_type=action_type,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            payload=payload,
        )

    def _add_action(
        self,
        history: ActivityHistoryStore,
        *,
        ordinal: int,
        session_id: str,
        actor_id: str,
        action_type: OperationActionKind,
        idempotency_key: str,
        fingerprint: str,
        payload: Mapping[str, object] | None = None,
    ) -> OperationActionRecord:
        action = OperationActionRecord(
            action_id=self._id_factory(),
            session_id=session_id,
            ordinal=ordinal,
            action_type=action_type,
            actor_id=actor_id,
            payload={} if payload is None else payload,
            created_at=self._clock(),
            idempotency_key=idempotency_key,
            intent_fingerprint=fingerprint,
        )
        return history.add_action(action)


_SERVICE_LIFECYCLE_ACTIONS = frozenset(
    {
        OperationActionKind.SESSION_STARTED,
        OperationActionKind.SESSION_CLOSED,
        OperationActionKind.SESSION_CANCELLED,
    }
)


def _fingerprint(command: OperationCommand) -> str:
    match command:
        case StartOperationSession():
            intent = {
                "command": "start",
                "workspace_id": command.workspace_id,
                "actor_id": command.actor_id,
                "title": command.title,
                "metadata": command.metadata,
            }
        case CloseOperationSession():
            intent = {"command": "close", "session_id": command.session_id, "actor_id": command.actor_id}
        case CancelOperationSession():
            intent = {"command": "cancel", "session_id": command.session_id, "actor_id": command.actor_id}
        case RecordOperationAction():
            intent = {
                "command": "record",
                "session_id": command.session_id,
                "actor_id": command.actor_id,
                "action_type": command.action_type.value,
                "payload": command.payload,
            }
        case _:
            raise InvalidOperationCommand(
                f"unsupported operation command {type(command).__name__}"
            )
    try:
        encoded = json.dumps(intent, sort_keys=True, separators=(",", ":")).encode()
    except (TypeError, ValueError) as error:
        raise InvalidOperationCommand("command intent must be JSON serializable") from error
    return hashlib.sha256(encoded).hexdigest()


def _get_session(history: ActivityHistoryStore, session_id: str) -> OperationSessionRecord:
    try:
        return history.get_session(session_id)
    except KeyError as error:
        raise OperationSessionNotFound(f"operation session {session_id!r} does not exist") from error


def _require_open(session: OperationSessionRecord) -> None:
    if session.status is not OperationSessionStatus.OPEN:
        raise OperationSessionStateConflict(
            f"operation session {session.session_id!r} is {session.status.value}, not open"
        )


def _session_replay(
    history: ActivityHistoryStore,
    session: OperationSessionRecord,
    idempotency_key: str,
    fingerprint: str,
) -> OperationCommandResult:
    _require_fingerprint(session.intent_fingerprint, fingerprint)
    action = history.action_for_idempotency(session.session_id, idempotency_key)
    if action is None:
        raise OperationCommandError("idempotent session is missing its initial action evidence")
    _require_fingerprint(action.intent_fingerprint, fingerprint)
    original = replace(session, status=OperationSessionStatus.OPEN, closed_at=None)
    return OperationCommandResult(session=original, action=action, replayed=True)


def _action_replay(
    session: OperationSessionRecord,
    action: OperationActionRecord,
    fingerprint: str,
) -> OperationCommandResult:
    _require_fingerprint(action.intent_fingerprint, fingerprint)
    match action.action_type:
        case OperationActionKind.SESSION_CLOSED:
            original = replace(
                session,
                status=OperationSessionStatus.CLOSED,
                closed_at=_closed_at(action),
            )
        case OperationActionKind.SESSION_CANCELLED:
            original = replace(
                session,
                status=OperationSessionStatus.CANCELLED,
                closed_at=_closed_at(action),
            )
        case _:
            original = replace(session, status=OperationSessionStatus.OPEN, closed_at=None)
    return OperationCommandResult(session=original, action=action, replayed=True)


def _closed_at(action: OperationActionRecord) -> str:
    value = action.payload.get("closed_at")
    if not isinstance(value, str) or not value:
        raise OperationCommandError("terminal action is missing its closed_at evidence")
    return value


def _require_fingerprint(existing: str | None, requested: str) -> None:
    if existing != requested:
        raise OperationIdempotencyConflict(
            "idempotency key was already used for different operation intent"
        )
