"""Publish immutable desired realized material without changing authored truth."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable

from control_plane_kit_core.operations.commands import OperatorCommandKind
from control_plane_kit_operations.records import (
    OperationActionRecord,
    OperationSessionStatus,
    RealizedGraphProjectionRecord,
)
from control_plane_kit_operations.workflows import IdempotencyKey, InvalidOperationCommand


class DesiredRealizedProjectionPublicationError(RuntimeError):
    """Base error for desired realized-projection publication."""


class DesiredRealizedProjectionPublicationConflict(
    DesiredRealizedProjectionPublicationError
):
    """Raised when session, lineage, idempotency, or pointer truth disagrees."""


class DesiredRealizedProjectionPublicationNotFound(
    DesiredRealizedProjectionPublicationError
):
    """Raised when required durable truth does not exist."""


@dataclass(frozen=True)
class PublishDesiredRealizedProjection:
    """Publish one pre-derived immutable projection for unchanged authored truth."""

    session_id: str
    workspace_id: str
    actor_id: str
    expected_authored_graph_id: str
    expected_realized_projection_id: str
    expected_desired_graph_revision: int
    projection: RealizedGraphProjectionRecord
    source_operation_id: str
    source_operation_version: int
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        for value, field in (
            (self.session_id, "session_id"),
            (self.workspace_id, "workspace_id"),
            (self.actor_id, "actor_id"),
            (self.expected_authored_graph_id, "expected_authored_graph_id"),
            (
                self.expected_realized_projection_id,
                "expected_realized_projection_id",
            ),
            (self.source_operation_id, "source_operation_id"),
        ):
            _required_text(value, field)
        if (
            type(self.expected_desired_graph_revision) is not int
            or self.expected_desired_graph_revision < 0
        ):
            raise InvalidOperationCommand(
                "expected_desired_graph_revision must be nonnegative"
            )
        if type(self.source_operation_version) is not int or self.source_operation_version < 1:
            raise InvalidOperationCommand("source_operation_version must be positive")
        if not isinstance(self.projection, RealizedGraphProjectionRecord):
            raise InvalidOperationCommand(
                "projection must be RealizedGraphProjectionRecord"
            )
        if (
            self.projection.workspace_id != self.workspace_id
            or self.projection.source_authored_graph_id
            != self.expected_authored_graph_id
        ):
            raise InvalidOperationCommand(
                "projection must belong to the expected workspace authored graph"
            )
        if not isinstance(self.idempotency_key, IdempotencyKey):
            raise InvalidOperationCommand("idempotency_key must be IdempotencyKey")

    def descriptor(self) -> dict[str, object]:
        return {
            "command": (
                OperatorCommandKind.PUBLISH_DESIRED_REALIZED_PROJECTION.value
            ),
            "session_id": self.session_id,
            "workspace_id": self.workspace_id,
            "actor_id": self.actor_id,
            "expected_authored_graph_id": self.expected_authored_graph_id,
            "expected_realized_projection_id": (
                self.expected_realized_projection_id
            ),
            "expected_desired_graph_revision": (
                self.expected_desired_graph_revision
            ),
            "desired_realized_projection_id": self.projection.projection_id,
            "desired_realized_projection_digest": self.projection.projection_digest,
            "projection_kind": self.projection.projection_kind.value,
            "projection_key": self.projection.projection_key,
            "source_operation_id": self.source_operation_id,
            "source_operation_version": self.source_operation_version,
            "idempotency_key": self.idempotency_key.value,
        }


@dataclass(frozen=True)
class DesiredRealizedProjectionPublicationResult:
    """Committed desired pointer and action evidence."""

    workspace_id: str
    authored_graph_id: str
    previous_realized_projection_id: str
    desired_realized_projection_id: str
    desired_graph_revision: int
    projection_digest: str
    action: OperationActionRecord
    replayed: bool = False

    def __post_init__(self) -> None:
        if (
            self.action.action_type
            is not OperatorCommandKind.PUBLISH_DESIRED_REALIZED_PROJECTION
        ):
            raise InvalidOperationCommand(
                "publication result requires realized-projection action evidence"
            )
        evidence = self.action.payload
        expected = {
            "workspace_id": self.workspace_id,
            "authored_graph_id": self.authored_graph_id,
            "previous_realized_projection_id": self.previous_realized_projection_id,
            "desired_realized_projection_id": self.desired_realized_projection_id,
            "desired_graph_revision": self.desired_graph_revision,
            "desired_realized_projection_digest": self.projection_digest,
        }
        if any(evidence.get(key) != value for key, value in expected.items()):
            raise InvalidOperationCommand(
                "publication action evidence does not match result"
            )


class DesiredRealizedProjectionCommandService:
    """Own the transaction for generic desired realized-material publication."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], Any],
        *,
        clock: Callable[[], str],
        action_id_factory: Callable[[], str],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock
        self._action_id_factory = action_id_factory

    def execute(
        self,
        command: PublishDesiredRealizedProjection,
    ) -> DesiredRealizedProjectionPublicationResult:
        if not isinstance(command, PublishDesiredRealizedProjection):
            raise TypeError("command must be PublishDesiredRealizedProjection")
        with self._unit_of_work_factory() as unit_of_work:
            result = publish_desired_realized_projection_in_unit_of_work(
                unit_of_work,
                command,
                created_at=self._clock(),
                action_id=self._action_id_factory(),
            )
            unit_of_work.commit()
            return result


def publish_desired_realized_projection_in_unit_of_work(
    unit_of_work: Any,
    command: PublishDesiredRealizedProjection,
    *,
    created_at: str,
    action_id: str,
) -> DesiredRealizedProjectionPublicationResult:
    """Publish one projection and action on the caller-owned transaction."""

    if not isinstance(command, PublishDesiredRealizedProjection):
        raise TypeError("command must be PublishDesiredRealizedProjection")
    _required_text(created_at, "created_at")
    _required_text(action_id, "action_id")
    stores = unit_of_work.stores
    history = stores.activity_history
    history.lock_action_idempotency(
        command.session_id,
        command.idempotency_key.value,
    )
    existing = history.action_for_idempotency(
        command.session_id,
        command.idempotency_key.value,
    )
    fingerprint = _fingerprint(command)
    if existing is not None:
        return _replay(stores, existing, fingerprint)
    try:
        session = history.get_session_for_update(command.session_id)
    except KeyError as error:
        raise DesiredRealizedProjectionPublicationNotFound(
            "operation session was not found"
        ) from error
    if session.workspace_id != command.workspace_id:
        raise DesiredRealizedProjectionPublicationConflict(
            "operation session and projection must belong to one workspace"
        )
    if session.status is not OperationSessionStatus.OPEN:
        raise DesiredRealizedProjectionPublicationConflict(
            "operation session is not open"
        )
    try:
        workspace = stores.workspaces.get_for_update(command.workspace_id)
    except KeyError as error:
        raise DesiredRealizedProjectionPublicationNotFound(
            "workspace was not found"
        ) from error
    if (
        workspace.desired_graph_id != command.expected_authored_graph_id
        or workspace.desired_realized_projection_id
        != command.expected_realized_projection_id
        or workspace.desired_graph_revision
        != command.expected_desired_graph_revision
    ):
        raise DesiredRealizedProjectionPublicationConflict(
            "workspace desired realized lineage changed"
        )
    try:
        projection = stores.realized_graphs.save(command.projection)
    except ValueError as error:
        raise DesiredRealizedProjectionPublicationConflict(str(error)) from error
    updated = stores.workspaces.compare_and_set_desired_projection(
        command.workspace_id,
        expected_authored_graph_id=command.expected_authored_graph_id,
        expected_realized_projection_id=command.expected_realized_projection_id,
        expected_revision=command.expected_desired_graph_revision,
        replacement_realized_projection_id=projection.projection_id,
    )
    if updated is None:
        raise DesiredRealizedProjectionPublicationConflict(
            "workspace desired realized lineage changed concurrently"
        )
    action = OperationActionRecord(
        action_id=action_id,
        session_id=command.session_id,
        ordinal=stores.activity_history.next_action_ordinal(command.session_id),
        action_type=OperatorCommandKind.PUBLISH_DESIRED_REALIZED_PROJECTION,
        actor_id=command.actor_id,
        payload={
            "workspace_id": command.workspace_id,
            "authored_graph_id": command.expected_authored_graph_id,
            "previous_realized_projection_id": (
                command.expected_realized_projection_id
            ),
            "desired_realized_projection_id": projection.projection_id,
            "desired_realized_projection_digest": projection.projection_digest,
            "desired_graph_revision": updated.desired_graph_revision,
            "projection_kind": projection.projection_kind.value,
            "projection_key": projection.projection_key,
            "source_operation_id": command.source_operation_id,
            "source_operation_version": command.source_operation_version,
        },
        created_at=created_at,
        idempotency_key=command.idempotency_key.value,
        intent_fingerprint=fingerprint,
    )
    stores.activity_history.add_action(action)
    return DesiredRealizedProjectionPublicationResult(
        workspace_id=command.workspace_id,
        authored_graph_id=command.expected_authored_graph_id,
        previous_realized_projection_id=command.expected_realized_projection_id,
        desired_realized_projection_id=projection.projection_id,
        desired_graph_revision=updated.desired_graph_revision,
        projection_digest=projection.projection_digest,
        action=action,
    )


def prepare_desired_realized_projection_publication(
    unit_of_work: Any,
    session_id: str,
    idempotency_key: str,
) -> OperationActionRecord | None:
    """Serialize publication identity and fence new work on the owning session."""

    history = unit_of_work.stores.activity_history
    history.lock_action_idempotency(session_id, idempotency_key)
    existing = history.action_for_idempotency(session_id, idempotency_key)
    if existing is not None:
        return existing
    try:
        session = history.get_session_for_update(session_id)
    except KeyError as error:
        raise DesiredRealizedProjectionPublicationNotFound(
            "operation session was not found"
        ) from error
    if session.status is not OperationSessionStatus.OPEN:
        raise DesiredRealizedProjectionPublicationConflict(
            "operation session is not open"
        )
    return None


def _replay(
    stores: Any,
    action: OperationActionRecord,
    fingerprint: str,
) -> DesiredRealizedProjectionPublicationResult:
    if (
        action.action_type
        is not OperatorCommandKind.PUBLISH_DESIRED_REALIZED_PROJECTION
        or action.intent_fingerprint != fingerprint
    ):
        raise DesiredRealizedProjectionPublicationConflict(
            "idempotency key was already used for different projection intent"
        )
    evidence = action.payload
    required_text = (
        "workspace_id",
        "authored_graph_id",
        "previous_realized_projection_id",
        "desired_realized_projection_id",
        "desired_realized_projection_digest",
    )
    if any(not isinstance(evidence.get(key), str) for key in required_text):
        raise DesiredRealizedProjectionPublicationConflict(
            "publication action evidence is incomplete"
        )
    revision = evidence.get("desired_graph_revision")
    if type(revision) is not int or revision < 1:
        raise DesiredRealizedProjectionPublicationConflict(
            "publication action revision evidence is incomplete"
        )
    try:
        projection = stores.realized_graphs.get(
            evidence["desired_realized_projection_id"]
        )
    except KeyError as error:
        raise DesiredRealizedProjectionPublicationConflict(
            "publication action projection evidence is missing"
        ) from error
    if projection.projection_digest != evidence["desired_realized_projection_digest"]:
        raise DesiredRealizedProjectionPublicationConflict(
            "publication action projection evidence changed"
        )
    return DesiredRealizedProjectionPublicationResult(
        workspace_id=evidence["workspace_id"],
        authored_graph_id=evidence["authored_graph_id"],
        previous_realized_projection_id=evidence["previous_realized_projection_id"],
        desired_realized_projection_id=evidence["desired_realized_projection_id"],
        desired_graph_revision=revision,
        projection_digest=evidence["desired_realized_projection_digest"],
        action=action,
        replayed=True,
    )


def _fingerprint(command: PublishDesiredRealizedProjection) -> str:
    encoded = json.dumps(command.descriptor(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _required_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOperationCommand(f"{field} must not be empty")


__all__ = [
    "DesiredRealizedProjectionCommandService",
    "DesiredRealizedProjectionPublicationConflict",
    "DesiredRealizedProjectionPublicationError",
    "DesiredRealizedProjectionPublicationNotFound",
    "DesiredRealizedProjectionPublicationResult",
    "PublishDesiredRealizedProjection",
    "publish_desired_realized_projection_in_unit_of_work",
    "prepare_desired_realized_projection_publication",
]
