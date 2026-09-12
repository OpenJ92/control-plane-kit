"""Admit one saved preparation and bind its existing operation session atomically."""
from __future__ import annotations

import hashlib
import json
import re

from control_plane_kit_core.operations import OperatorCommandKind
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, validate_graph
from control_plane_kit_operations.deployment_program import PrepareDeploymentProgram, SavedDesiredTopologyRevision
from control_plane_kit_operations.graph_authoring import product_references_in_graph
from control_plane_kit_operations.products import RegisteredProductStatus
from control_plane_kit_operations.records import RealizedGraphProjectionRecord, SavedPreparationSourceRecord
from control_plane_kit_operations.workflows import IdempotencyKey, OperationCommandError, StartOperationSession, _fingerprint


class SavedPreparationError(ValueError):
    """Saved preparation evidence is missing or incongruent."""


_SAVED_KEYS = frozenset({
    "deployment_prepare_saved_draft_id", "deployment_prepare_saved_revision",
    "deployment_prepare_saved_graph_id", "deployment_prepare_saved_current_graph_id",
    "deployment_prepare_saved_current_projection_id", "deployment_prepare_saved_desired_projection_id",
    "deployment_prepare_saved_desired_generation",
})
_METADATA_KEYS = _SAVED_KEYS | {"deployment_prepare_source", "deployment_prepare_intent_sha256"}


def saved_preparation_metadata(metadata):
    """Return closed saved evidence, or None for legacy; never infer erased evidence."""
    if not isinstance(metadata, dict):
        raise SavedPreparationError("saved preparation evidence is unavailable")
    if "deployment_prepare_source" not in metadata:
        if any(key in _SAVED_KEYS or key.startswith("deployment_prepare_saved_") for key in metadata):
            raise SavedPreparationError("saved preparation evidence is unavailable")
        return None
    if set(metadata) != _METADATA_KEYS or metadata["deployment_prepare_source"] != "saved-revision.v1":
        raise SavedPreparationError("saved preparation evidence is unavailable")
    for value in metadata.values():
        if type(value) is not str or not value or len(value) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise SavedPreparationError("saved preparation evidence is unavailable")
    if re.fullmatch(r"[0-9a-f]{64}", metadata["deployment_prepare_intent_sha256"]) is None:
        raise SavedPreparationError("saved preparation evidence is unavailable")
    for key in ("deployment_prepare_saved_revision", "deployment_prepare_saved_desired_generation"):
        value = metadata[key]
        if re.fullmatch(r"[1-9][0-9]{0,18}", value) is None or int(value) > 9223372036854775807:
            raise SavedPreparationError("saved preparation evidence is unavailable")
    return dict(metadata)



def saved_preparation_session_metadata(session):
    """Check the immutable session commitment before exposing saved coordinates."""
    metadata = saved_preparation_metadata(dict(session.metadata))
    if metadata is None:
        return None
    try:
        command = StartOperationSession(session.workspace_id, session.actor_id, session.title,
                                        IdempotencyKey(session.idempotency_key), metadata)
    except (OperationCommandError, TypeError, ValueError):
        raise SavedPreparationError("saved preparation evidence is unavailable") from None
    if session.intent_fingerprint != _fingerprint(command):
        raise SavedPreparationError("saved preparation evidence is unavailable")
    return metadata


def validate_saved_preparation_source(source, session, revision):
    """Correlate the relational identity with its immutable admission commitment."""
    metadata = saved_preparation_session_metadata(session)
    if (type(source) is not SavedPreparationSourceRecord or metadata is None
        or source.session_id != session.session_id or source.workspace_id != session.workspace_id
        or source.workspace_id != revision.workspace_id or source.draft_id != revision.draft_id
        or source.revision != revision.revision
        or source.draft_id != metadata["deployment_prepare_saved_draft_id"]
        or str(source.revision) != metadata["deployment_prepare_saved_revision"]
        or revision.graph_id != metadata["deployment_prepare_saved_graph_id"]):
        raise SavedPreparationError("saved preparation source is unavailable")
    return metadata


def _metadata(command):
    current = {"authored_graph_id": command.expected_current.authored_graph_id,
               "realized_projection_id": command.expected_current.realized_projection_id}
    desired = {"authored_graph_id": command.expected_desired.authored_graph_id,
               "realized_projection_id": command.expected_desired.realized_projection_id}
    intent = {"profile": "deployment-program-prepare-saved.v1",
              "workspace_id": command.context.workspace_id, "actor_id": command.context.actor_id,
              "desired": {"draft_id": command.desired.draft_id, "revision": command.desired.revision},
              "expected_current": current, "expected_desired": desired,
              "expected_desired_graph_revision": command.expected_desired_graph_revision,
              "title": command.title, "approval_comment": command.approval_comment}
    digest = hashlib.sha256(json.dumps(intent, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"deployment_prepare_source": "saved-revision.v1", "deployment_prepare_intent_sha256": digest,
            "deployment_prepare_saved_draft_id": command.desired.draft_id,
            "deployment_prepare_saved_revision": str(command.desired.revision),
            "deployment_prepare_saved_graph_id": command.expected_desired.authored_graph_id,
            "deployment_prepare_saved_current_graph_id": command.expected_current.authored_graph_id,
            "deployment_prepare_saved_current_projection_id": command.expected_current.realized_projection_id,
            "deployment_prepare_saved_desired_projection_id": command.expected_desired.realized_projection_id,
            "deployment_prepare_saved_desired_generation": str(command.expected_desired_graph_revision)}


class SavedDeploymentPreparationService:
    """One admission/session transaction, followed elsewhere by existing planning."""

    def __init__(self, unit_of_work_factory, operations):
        self._unit_of_work_factory = unit_of_work_factory
        self._operations = operations

    def start(self, command: PrepareDeploymentProgram, *, session_key):
        if type(command) is not PrepareDeploymentProgram or type(command.desired) is not SavedDesiredTopologyRevision:
            raise SavedPreparationError("saved preparation input is unavailable")
        if not {PolicyScope.INSTANCE_WORKSPACE_EDIT, PolicyScope.PLAN_REQUEST} <= set(command.context.granted_scopes):
            raise SavedPreparationError("saved preparation is not authorized")
        metadata = saved_preparation_metadata(_metadata(command))
        start = StartOperationSession(command.context.workspace_id, command.context.actor_id,
                                      command.title, session_key, metadata)
        try:
            with self._unit_of_work_factory() as uow:
                history = uow.stores.activity_history
                history.lock_session_idempotency(command.context.workspace_id, session_key.value)
                existing = history.session_for_idempotency(command.context.workspace_id, session_key.value)
                if existing is not None:
                    if saved_preparation_metadata(dict(existing.metadata)) != metadata:
                        raise SavedPreparationError("saved preparation evidence is unavailable")
                    result = self._operations.start_in_unit_of_work(uow, start)
                    _validate_start(result, start)
                    _immutable_graphs(uow.stores, command)
                    source = uow.stores.saved_preparation_sources.get(
                        command.context.workspace_id, result.session.session_id)
                    revision = uow.stores.desired_topology_drafts.revision(
                        command.context.workspace_id, command.desired.draft_id, command.desired.revision)
                    validate_saved_preparation_source(source, result.session, revision)
                    uow.commit()
                    return result
                # No existing session row: key -> workspace -> draft precedes inserts.
                workspace = uow.stores.workspaces.get_for_update(command.context.workspace_id)
                draft = uow.stores.desired_topology_drafts.get(
                    command.context.workspace_id, command.desired.draft_id, for_update=True)
                if (workspace.workspace_id != command.context.workspace_id
                    or draft.workspace_id != command.context.workspace_id
                    or draft.draft_id != command.desired.draft_id or draft.deleted_at is not None
                    or workspace.current_lineage != command.expected_current
                    or workspace.desired_lineage != command.expected_desired
                    or workspace.desired_graph_revision != command.expected_desired_graph_revision):
                    raise SavedPreparationError("saved preparation state is unavailable")
                graph = _immutable_graphs(uow.stores, command)
                for reference in product_references_in_graph(graph):
                    if uow.stores.registered_products.get(command.context.workspace_id, reference).status is not RegisteredProductStatus.ACTIVE:
                        raise SavedPreparationError("saved preparation state is unavailable")
                result = self._operations.start_in_unit_of_work(uow, start)
                _validate_start(result, start)
                uow.stores.saved_preparation_sources.insert(SavedPreparationSourceRecord(
                    result.session.session_id, command.context.workspace_id,
                    command.desired.draft_id, command.desired.revision))
                uow.commit()
                return result
        except (KeyError, ValueError, TypeError, AttributeError, OperationCommandError):
            pass
        raise SavedPreparationError("saved preparation state is unavailable")


def _validate_start(result, command):
    session, action = result.session, result.action
    if (session.workspace_id != command.workspace_id or session.actor_id != command.actor_id
        or session.title != command.title or session.idempotency_key != command.idempotency_key.value
        or dict(session.metadata) != dict(command.metadata)):
        raise SavedPreparationError("saved preparation evidence is unavailable")
    validate_saved_preparation_start(session, action)


def validate_saved_preparation_start(session, action):
    """Validate retained start evidence without replaying an operation command."""
    if (action.session_id != session.session_id or action.ordinal != 1
        or action.action_type is not OperatorCommandKind.START_OPERATION_SESSION
        or action.actor_id != session.actor_id or dict(action.payload) != {"workspace_id": session.workspace_id}
        or action.idempotency_key != session.idempotency_key
        or action.intent_fingerprint != session.intent_fingerprint or action.created_at != session.created_at):
        raise SavedPreparationError("saved preparation evidence is unavailable")


def _immutable_graphs(stores, command):
    workspace_id = command.context.workspace_id
    revision = stores.desired_topology_drafts.revision(workspace_id, command.desired.draft_id, command.desired.revision)
    if (revision.workspace_id != workspace_id or revision.draft_id != command.desired.draft_id
        or revision.revision != command.desired.revision or revision.graph_id != command.expected_desired.authored_graph_id):
        raise SavedPreparationError("saved preparation evidence is unavailable")
    authored = stores.graphs.get(revision.graph_id)
    if authored.workspace_id != workspace_id or authored.graph_id != revision.graph_id:
        raise SavedPreparationError("saved preparation evidence is unavailable")
    graph = DEFAULT_GRAPH_CODEC.decode(authored.graph_descriptor)
    validate_graph(graph).require_valid()
    desired = stores.realized_graphs.get(command.expected_desired.realized_projection_id)
    if desired != RealizedGraphProjectionRecord.identity_for_authored(authored_record=authored):
        raise SavedPreparationError("saved preparation evidence is unavailable")
    current = stores.realized_graphs.get(command.expected_current.realized_projection_id)
    current_authored = stores.graphs.get(command.expected_current.authored_graph_id)
    if (current.workspace_id != workspace_id or current_authored.workspace_id != workspace_id
        or current_authored.graph_id != command.expected_current.authored_graph_id
        or current.projection_id != command.expected_current.realized_projection_id
        or current.source_authored_graph_id != current_authored.graph_id):
        raise SavedPreparationError("saved preparation evidence is unavailable")
    validate_graph(DEFAULT_GRAPH_CODEC.decode(current.graph_descriptor)).require_valid()
    validate_graph(DEFAULT_GRAPH_CODEC.decode(current_authored.graph_descriptor)).require_valid()
    return graph


__all__ = ["SavedDeploymentPreparationService", "SavedPreparationError", "saved_preparation_metadata", "saved_preparation_session_metadata"]
