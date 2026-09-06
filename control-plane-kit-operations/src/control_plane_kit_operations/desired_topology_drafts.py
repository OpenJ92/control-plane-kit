"""Authenticated, atomic publication of saved graph intent, without selection."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable, Protocol
import unicodedata

from control_plane_kit_core.identity import TrustedCommandContext
from control_plane_kit_core.operations import OperatorCommandKind
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph, validate_graph
from control_plane_kit_operations._temporal import validate_canonical_utc_timestamp
from control_plane_kit_operations.graph_authoring import product_references_in_graph
from control_plane_kit_operations.products import RegisteredProductStatus
from control_plane_kit_operations.records import GraphVersionRecord, OperationActionRecord, OperationSessionStatus
from control_plane_kit_operations.workflows import IdempotencyKey


class DesiredTopologyDraftError(ValueError):
    """Bounded catalogue input or admission failure."""


class DesiredTopologyDraftConflict(DesiredTopologyDraftError):
    """Saved intent conflicts with existing action evidence or draft head."""


def _text(value: object, name: str) -> None:
    if (type(value) is not str or not value.strip() or len(value) > 512
            or any(unicodedata.category(c).startswith("C") for c in value)):
        raise DesiredTopologyDraftError(f"{name} must be bounded nonempty text without controls")


def _revision_number(value: object) -> None:
    if type(value) is not int or not 1 <= value <= 9_223_372_036_854_775_807:
        raise DesiredTopologyDraftError("draft revision must be positive and bounded")


@dataclass(frozen=True)
class DesiredTopologyDraftRecord:
    workspace_id: str
    draft_id: str
    title: str
    head_revision: int
    created_by: str
    created_at: str
    deleted_by: str | None = None
    deleted_at: str | None = None

    def __post_init__(self) -> None:
        for name in ("workspace_id", "draft_id", "title", "created_by"):
            _text(getattr(self, name), name)
        _revision_number(self.head_revision)
        validate_canonical_utc_timestamp(self.created_at)
        if (self.deleted_by is None) != (self.deleted_at is None):
            raise DesiredTopologyDraftError("draft retirement evidence must be paired")
        if self.deleted_by is not None:
            _text(self.deleted_by, "deleted_by")
            validate_canonical_utc_timestamp(self.deleted_at)

    def descriptor(self) -> dict[str, object]:
        return dict(vars(self))


@dataclass(frozen=True)
class DesiredTopologyDraftRevisionRecord:
    workspace_id: str
    draft_id: str
    revision: int
    graph_id: str
    created_by: str
    created_at: str

    def __post_init__(self) -> None:
        for name in ("workspace_id", "draft_id", "graph_id", "created_by"):
            _text(getattr(self, name), name)
        _revision_number(self.revision)
        validate_canonical_utc_timestamp(self.created_at)

    def descriptor(self) -> dict[str, object]:
        return dict(vars(self))


class DesiredTopologyDraftStore(Protocol):
    def create(self, record: DesiredTopologyDraftRecord) -> None: ...
    def get(self, workspace_id: str, draft_id: str, *, for_update: bool = False) -> DesiredTopologyDraftRecord: ...
    def append(self, record: DesiredTopologyDraftRevisionRecord, *, expected_head_revision: int | None) -> None: ...
    def revision(self, workspace_id: str, draft_id: str, revision: int) -> DesiredTopologyDraftRevisionRecord: ...
    def page(self, request: Any) -> Any: ...


@dataclass(frozen=True)
class CreateDesiredTopologyDraft:
    context: TrustedCommandContext
    session_id: str
    title: str
    graph: DeploymentGraph
    idempotency_key: IdempotencyKey


@dataclass(frozen=True)
class ReviseDesiredTopologyDraft:
    context: TrustedCommandContext
    session_id: str
    draft_id: str
    expected_head_revision: int
    graph: DeploymentGraph
    idempotency_key: IdempotencyKey


@dataclass(frozen=True)
class DesiredTopologyDraftResult:
    workspace_id: str
    draft_id: str
    revision: int
    graph_id: str

    def __post_init__(self) -> None:
        for name in ("workspace_id", "draft_id", "graph_id"):
            _text(getattr(self, name), name)
        _revision_number(self.revision)

    def descriptor(self) -> dict[str, object]:
        return dict(vars(self))


class DesiredTopologyDraftCommandService:
    """One UoW: action key -> session -> workspace -> draft -> atomic writes.

    Replay is resolved before current-state admission and identity allocation.
    The typed command is the inspectable intent; no runtime plan is created.
    """
    def __init__(self, unit_of_work_factory: Callable[[], Any], *, clock: Callable[[], str], id_factory: Callable[[], str]):
        self._uow = unit_of_work_factory
        self._clock = clock
        self._id = id_factory

    def execute(self, command: CreateDesiredTopologyDraft | ReviseDesiredTopologyDraft) -> DesiredTopologyDraftResult:
        if type(command) not in (CreateDesiredTopologyDraft, ReviseDesiredTopologyDraft):
            raise DesiredTopologyDraftError("unsupported draft command")
        context = command.context
        if not isinstance(context, TrustedCommandContext) or PolicyScope.INSTANCE_WORKSPACE_EDIT not in context.granted_scopes:
            raise DesiredTopologyDraftError("workspace edit authority is required")
        _text(command.session_id, "session_id")
        _text(context.workspace_id, "workspace_id")
        _text(context.actor_id, "actor_id")
        if not isinstance(command.idempotency_key, IdempotencyKey):
            raise DesiredTopologyDraftError("idempotency key is required")
        creating = isinstance(command, CreateDesiredTopologyDraft)
        if creating:
            _text(command.title, "title")
        else:
            _text(command.draft_id, "draft_id")
            if type(command.expected_head_revision) is not int or not 1 <= command.expected_head_revision < 9_223_372_036_854_775_807:
                raise DesiredTopologyDraftError("expected head revision must be positive and bounded")
        try:
            descriptor = DEFAULT_GRAPH_CODEC.encode(command.graph)
            encoded = json.dumps(descriptor, ensure_ascii=False, allow_nan=False)
            # JSONB transport uses spaces after separators; reject before storage.
            if len(encoded.encode("utf-8")) > 1_048_576:
                raise ValueError
        except (TypeError, ValueError, AttributeError):
            raise DesiredTopologyDraftError("draft graph must be a bounded graph descriptor") from None
        kind = (OperatorCommandKind.CREATE_DESIRED_TOPOLOGY_DRAFT if creating
                else OperatorCommandKind.REVISE_DESIRED_TOPOLOGY_DRAFT)
        intent = {"kind": kind.value, "context": context.descriptor(), "session_id": command.session_id,
                  "graph": descriptor, "title": command.title if creating else None,
                  "draft_id": None if creating else command.draft_id,
                  "expected_head_revision": None if creating else command.expected_head_revision}
        fingerprint = hashlib.sha256(json.dumps(intent, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        with self._uow() as uow:
            history = uow.stores.activity_history
            history.lock_action_idempotency(command.session_id, command.idempotency_key.value)
            try:
                existing = history.action_for_idempotency(command.session_id, command.idempotency_key.value)
            except (ValueError, TypeError):
                raise DesiredTopologyDraftConflict("draft replay evidence is malformed") from None

            if existing is not None:
                if existing.action_type != kind or existing.intent_fingerprint != fingerprint:
                    raise DesiredTopologyDraftConflict("idempotency key already records different intent")
                result = _replay_result(uow, command, existing, descriptor)
                uow.commit()
                return result
            try:
                session = history.get_session_for_update(command.session_id)
            except KeyError:
                raise DesiredTopologyDraftError("operation session was not found") from None
            if session.workspace_id != context.workspace_id or session.status is not OperationSessionStatus.OPEN:
                raise DesiredTopologyDraftError("an open workspace operation session is required")
            try:
                uow.stores.workspaces.get_for_update(context.workspace_id)
            except KeyError:
                raise DesiredTopologyDraftError("workspace was not found") from None
            store = uow.stores.desired_topology_drafts
            if not creating:
                try:
                    draft = store.get(context.workspace_id, command.draft_id, for_update=True)
                except KeyError:
                    raise DesiredTopologyDraftError("draft was not found") from None
                if draft.deleted_at is not None or draft.head_revision != command.expected_head_revision:
                    raise DesiredTopologyDraftConflict("draft head is stale or retired")
            try:
                validate_graph(command.graph).require_valid()
                for reference in product_references_in_graph(command.graph):
                    registered = uow.stores.registered_products.get(context.workspace_id, reference)
                    if registered.status is not RegisteredProductStatus.ACTIVE:
                        raise ValueError
            except (KeyError, ValueError):
                raise DesiredTopologyDraftError("draft graph must be valid with workspace-active products") from None
            draft_id = self._id() if creating else command.draft_id
            graph_id = self._id()
            action_id = self._id()
            for name, value in (("draft_id", draft_id), ("graph_id", graph_id), ("action_id", action_id)):
                _text(value, name)
            now = self._clock()
            revision = 1 if creating else command.expected_head_revision + 1
            graph = GraphVersionRecord.from_graph(graph_id=graph_id, workspace_id=context.workspace_id,
                version=uow.stores.graphs.next_version_for_workspace(context.workspace_id), graph=command.graph,
                created_by=context.actor_id, created_at=now)
            uow.stores.graphs.save(graph)
            if creating:
                store.create(DesiredTopologyDraftRecord(context.workspace_id, draft_id, command.title, 1, context.actor_id, now))
            store.append(DesiredTopologyDraftRevisionRecord(context.workspace_id, draft_id, revision, graph_id, context.actor_id, now),
                         expected_head_revision=None if creating else command.expected_head_revision)
            result = DesiredTopologyDraftResult(context.workspace_id, draft_id, revision, graph_id)
            history.add_action(OperationActionRecord(action_id=action_id, session_id=command.session_id,
                ordinal=history.next_action_ordinal(command.session_id), action_type=kind, actor_id=context.actor_id,
                payload=result.descriptor(), created_at=now, idempotency_key=command.idempotency_key.value,
                intent_fingerprint=fingerprint))
            uow.commit()
            return result


def _replay_result(uow, command, action, graph_descriptor) -> DesiredTopologyDraftResult:
    """Resolve closed action coordinates through retained immutable evidence only."""
    try:
        payload = action.payload
        if set(payload) != {"workspace_id", "draft_id", "revision", "graph_id"}:
            raise ValueError
        result = DesiredTopologyDraftResult(**dict(payload))
        creating = isinstance(command, CreateDesiredTopologyDraft)
        expected_revision = 1 if creating else command.expected_head_revision + 1
        if (result.workspace_id != command.context.workspace_id
                or result.revision != expected_revision
                or (not creating and result.draft_id != command.draft_id)):
            raise ValueError
        revision = uow.stores.desired_topology_drafts.revision(
            result.workspace_id, result.draft_id, result.revision)
        graph = uow.stores.graphs.get(result.graph_id)
        if (revision.workspace_id != result.workspace_id
                or revision.draft_id != result.draft_id
                or revision.revision != result.revision
                or revision.graph_id != result.graph_id
                or graph.workspace_id != result.workspace_id
                or graph.graph_id != result.graph_id
                or graph.graph_descriptor != graph_descriptor
                or action.actor_id != command.context.actor_id
                or revision.created_by != action.actor_id
                or graph.created_by != action.actor_id
                or revision.created_at != action.created_at
                or graph.created_at != action.created_at):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise DesiredTopologyDraftConflict("draft replay evidence is incongruent") from None
    return result
