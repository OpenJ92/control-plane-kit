"""Receiver facts and private graph-owner checks within a held transaction.

Expectations describe caller-observed truth. They confer no admission authority.
"""

from dataclasses import dataclass
import json
import re

from control_plane_kit_core.receiver_configuration import (
    ReceiverNodeControlConfigurationCodec, select_receiver_node_control_configuration_artifact,
)
from control_plane_kit_core.receiver_identity import NodeControlReceiverTargetCodec
from control_plane_kit_core.node_control import NodeControlGraphReference, NodeControlGraphReferenceRole
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_core.wrapper_configuration import (
    MAX_WRAPPER_CONFIGURATION_BYTES, WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT,
)


class ReceiverLifecycleStorageError(ValueError):
    """Bounded material or storage-integrity refusal without caller data."""


class ReceiverLifecycleStorageConflict(ReceiverLifecycleStorageError):
    """A reserved identity or immutable witness cannot be replaced."""


@dataclass(frozen=True, slots=True)
class ReceiverLifecycleExpectation:
    """Original caller expectations, never a credential or admission token."""

    current_graph_id: str
    current_realized_projection_id: str
    desired_graph_id: str | None
    desired_realized_projection_id: str | None
    desired_graph_revision: int

    def __post_init__(self):
        for name in ("current_graph_id", "current_realized_projection_id",
                     "desired_graph_id", "desired_realized_projection_id"):
            value = getattr(self, name)
            if value is None and name.startswith("desired_"):
                continue
            if type(value) is not str:
                raise ValueError("receiver expectation requires public graph references")
            NodeControlGraphReference(NodeControlGraphReferenceRole.GRAPH_REVISION, value)
        if ((self.desired_graph_id is None) != (self.desired_realized_projection_id is None)
                or type(self.desired_graph_revision) is not int
                or not 0 <= self.desired_graph_revision <= 9_223_372_036_854_775_807
                or (self.desired_graph_id is None) != (self.desired_graph_revision == 0)):
            raise ValueError("receiver expectation requires paired desired lineage and generation")

    def descriptor(self):
        return {name: getattr(self, name) for name in (
            "current_graph_id", "current_realized_projection_id", "desired_graph_id",
            "desired_realized_projection_id", "desired_graph_revision",
        )}


def _expectation_member(value):
    if value is None:
        return {}
    if type(value) is not ReceiverLifecycleExpectation:
        raise ReceiverLifecycleStorageError("receiver expectation is malformed")
    return {"receiver_lifecycle": value.descriptor()}


def _receiver_action_payload(payload, expectation):
    result = dict(payload) | _expectation_member(expectation)
    _require(len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")) <= 65536)
    return result


def _receiver_replay_payload(payload, expectation, keys):
    member = _expectation_member(expectation)
    _require(set(payload) == set(keys) | set(member))
    if member:
        _require(payload["receiver_lifecycle"] == member["receiver_lifecycle"])
        _require(type(payload["receiver_lifecycle"]) is dict)
        _require(ReceiverLifecycleExpectation(**payload["receiver_lifecycle"]) == expectation)
    _receiver_action_payload({key: payload[key] for key in keys}, expectation)
    return {key: payload[key] for key in keys}


def _receiver_workspace_pins(workspace):
    return {name: getattr(workspace, name) for name in (
        "current_graph_id", "current_realized_projection_id", "desired_graph_id",
        "desired_realized_projection_id", "desired_graph_revision",
    )}


def _check_receiver_expectation(workspace, expectation):
    if expectation is not None:
        _expectation_member(expectation)
        _require(expectation.descriptor() == _receiver_workspace_pins(workspace))


def _receiver_scope(binding):
    return (binding.workspace_id, binding.runtime_id, binding.node_id,
            binding.provider_socket_name, binding.receiver_id)


def _retained_receiver_material(stores, workspace_id, graph_id, projection_id):
    if graph_id is None and projection_id is None:
        return ()
    _require(graph_id is not None and projection_id is not None)
    graph = stores.graphs.get(graph_id)
    projection = stores.realized_graphs.get(projection_id)
    _require(graph.workspace_id == projection.workspace_id == workspace_id
             and projection.source_authored_graph_id == graph_id)
    bindings = derive_receiver_bindings(workspace_id, graph_id, projection_id, projection.graph_descriptor)
    _require(stores.graphs.receiver_bindings(workspace_id, graph_id, projection_id) == bindings)
    return bindings


def _receiver_origin(stores, binding):
    origin = stores.graphs.receiver_introduction(binding.workspace_id, binding.receiver_id)
    if origin is None:
        return None
    _require(_receiver_scope(origin) == _receiver_scope(binding) and origin.retired_action_id is None)
    original = stores.graphs.receiver_bindings(origin.workspace_id,
        origin.introducing_graph_id, origin.introducing_realized_projection_id)
    _require(any(_receiver_scope(item) == _receiver_scope(binding) for item in original))
    try:
        stores.graphs._require_receiver_origin_action(origin)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ReceiverLifecycleStorageError("receiver origin evidence is unavailable") from None
    return origin


def _receiver_sources(stores, workspace, *, draft_head=None):
    """Rederive complete retained membership; no returned permission survives a write."""
    current = _retained_receiver_material(stores, workspace.workspace_id,
        workspace.current_graph_id, workspace.current_realized_projection_id)
    desired = _retained_receiver_material(stores, workspace.workspace_id,
        workspace.desired_graph_id, workspace.desired_realized_projection_id)
    head = ()
    if draft_head is not None:
        draft_id, revision = draft_head
        draft = stores.desired_topology_drafts.get(workspace.workspace_id, draft_id)
        _require(draft.deleted_at is None and draft.head_revision == revision)
        record = stores.desired_topology_drafts.revision(workspace.workspace_id, draft_id, revision)
        projection = stores.realized_graphs.identity_for_authored(workspace.workspace_id, record.graph_id)
        # Historical legacy drafts did not persist an identity projection.
        # Derivation is read-only; receiver heads still require actual material
        # and the complete durable binding set.
        head = derive_receiver_bindings(workspace.workspace_id, record.graph_id,
            projection.projection_id, projection.graph_descriptor)
        try:
            stores.realized_graphs.get(projection.projection_id)
        except KeyError:
            _require(not head)
        else:
            head = _retained_receiver_material(stores, workspace.workspace_id,
                record.graph_id, projection.projection_id)
    accepted = {}
    for binding in current:
        origin = _receiver_origin(stores, binding)
        _require(origin is not None and origin.first_accepted_action_id is not None)
        accepted[binding.receiver_id] = origin
    if accepted:
        stores.execution._receiver_acceptance_evidence(tuple(accepted.values()))
    pending = {}
    for binding in desired + head:
        origin = _receiver_origin(stores, binding)
        _require(origin is not None)
        if origin.first_accepted_action_id is not None:
            _require(accepted.get(binding.receiver_id) == origin)
        else:
            prior = pending.setdefault(binding.receiver_id, origin)
            _require(prior == origin)
    return current + desired + head, accepted | pending


def _validate_receiver_admission(stores, workspace, expectation, proposed, guard, *, draft_head=None):
    """Fresh owner check, repeated after action writes against the same held sources."""
    from control_plane_kit_operations.receiver_execution_scopes import (
        ExecutionReceiverScope, classify_receiver_scope_evidence,
    )

    _check_receiver_expectation(workspace, expectation)
    retained, sources = _receiver_sources(stores, workspace, draft_head=draft_head)
    if retained or proposed:
        _require(expectation is not None)
    accepted_scopes = {_receiver_scope(origin)[:-1]: origin.receiver_id
                       for origin in sources.values() if origin.first_accepted_action_id is not None}
    introduced = []
    for binding in proposed:
        occupied = accepted_scopes.get(_receiver_scope(binding)[:-1])
        _require(occupied is None or occupied == binding.receiver_id)
        origin = _receiver_origin(stores, binding)
        if origin is None:
            introduced.append(binding.receiver_id)
        else:
            _require(sources.get(binding.receiver_id) == origin)
    scopes = tuple(sorted({ExecutionReceiverScope(item.runtime_id, item.node_id)
                           for item in retained + proposed}, key=lambda item: (item.runtime_id, item.node_id)))
    if scopes:
        evidence = stores.execution.receiver_scope_evidence(workspace.workspace_id, scopes, guard)
        # C3 closes every fresh/direct execution path. The historical result
        # stays unchanged; this consumer may now use its complete cancellation
        # proof without a caller flag, ignored request or disposal policy.
        _require(classify_receiver_scope_evidence(evidence).disposition in (
            "nonconflicting", "requires-fresh-gate-closure"))
    return tuple(sorted(introduced))


def _validate_receiver_reference(stores, workspace, graph_id, projection_id, *, draft_head=None):
    _, sources = _receiver_sources(stores, workspace, draft_head=draft_head)
    for binding in _retained_receiver_material(stores, workspace.workspace_id, graph_id, projection_id):
        origin = _receiver_origin(stores, binding)
        _require(origin is not None and sources.get(binding.receiver_id) == origin)


def _validate_receiver_execution(stores, request, guard):
    """Check original execution against held current truth; return no grant.

    The command owner enters L and its request/run/session prefix first. This
    workspace suffix never asks whether the original work is nonconflicting:
    that work may lawfully retain occupied scope while retrying or compensating.
    """
    from control_plane_kit_operations.records import OperationSessionStatus
    _require(stores.execution.get_request(request.identity.request_id) == request)
    session = stores.activity_history.get_session(request.identity.session_id)
    _require(session.workspace_id == request.identity.workspace_id
             and session.status is OperationSessionStatus.OPEN)
    workspace = stores.workspaces.get_for_update(request.identity.workspace_id)
    original, _ = stores.execution._receiver_execution_material(request.identity, guard)
    plan, base, desired = original
    _require((workspace.current_graph_id, workspace.current_realized_projection_id,
              workspace.desired_graph_id, workspace.desired_realized_projection_id,
              workspace.desired_graph_revision) ==
             (plan.base_graph_id, base.projection_id, plan.desired_graph_id,
              desired.projection_id, plan.desired_graph_revision))
    # Legacy material remains legitimate. Receiver-bearing originals require
    # complete retained binding sets and lawful, still-selected origins.
    memberships = []
    for item in (base, desired):
        bindings = derive_receiver_bindings(workspace.workspace_id, item.source_authored_graph_id,
            item.projection_id, item.graph_descriptor)
        _require(stores.graphs.receiver_bindings(workspace.workspace_id,
            item.source_authored_graph_id, item.projection_id) == bindings)
        memberships.append(bindings)
    if any(memberships):
        for item in (base, desired):
            _validate_receiver_reference(stores, workspace,
                item.source_authored_graph_id, item.projection_id)


def _validate_receiver_execution_approval(stores, request):
    """Retain the exact original approval for owners without recovery checks."""
    from control_plane_kit_core.approval_subjects import (
        ActivityPlanApprovalSubject, GatewayKeyRotationApprovalSubject,
    )
    from control_plane_kit_operations.records import ApprovalDecisionKind

    approval = stores.activity_history.get_approval_request(request.approval_request_id)
    decision = stores.activity_history.approval_decision_for_request(approval.request_id)
    _require(decision is not None and decision.decision is ApprovalDecisionKind.APPROVED
             and decision.decision_id == request.approval_decision_id
             and decision.request_id == request.approval_request_id
             and decision.scope is approval.required_scope
             and approval.session_id == request.identity.session_id)
    subject = approval.subject
    _require((type(subject) is ActivityPlanApprovalSubject and subject.plan_id == request.identity.plan_id)
             or type(subject) is GatewayKeyRotationApprovalSubject)


def _require(condition):
    if condition is not True:
        raise ReceiverLifecycleStorageError("receiver storage is unavailable")


def _text(value):
    _require(type(value) is str and bool(value.strip()) and len(value.encode("utf-8")) <= 2048)


def _scope(record):
    NodeControlReceiverTargetCodec().decode({name: getattr(record, name) for name in (
        "workspace_id", "runtime_id", "node_id", "provider_socket_name", "receiver_id",
    )})


@dataclass(frozen=True, slots=True)
class ReceiverIntroduction:
    workspace_id: str
    receiver_id: str
    runtime_id: str
    node_id: str
    provider_socket_name: str
    introducing_graph_id: str
    introducing_realized_projection_id: str
    introducing_action_id: str
    introducing_session_id: str
    introducing_draft_id: str | None = None
    first_accepted_action_id: str | None = None
    first_accepted_session_id: str | None = None
    retired_action_id: str | None = None
    retired_session_id: str | None = None

    def __post_init__(self):
        _scope(self)
        for name in ("introducing_graph_id", "introducing_realized_projection_id",
                     "introducing_action_id", "introducing_session_id"):
            _text(getattr(self, name))
        for name in ("introducing_draft_id", "first_accepted_action_id", "first_accepted_session_id",
                     "retired_action_id", "retired_session_id"):
            if getattr(self, name) is not None:
                _text(getattr(self, name))
        _require((self.first_accepted_action_id is None) == (self.first_accepted_session_id is None))
        _require((self.retired_action_id is None) == (self.retired_session_id is None))
        _require(self.retired_action_id is None or (self.first_accepted_action_id is not None
                                                  and self.retired_action_id != self.first_accepted_action_id))


@dataclass(frozen=True, slots=True)
class ReceiverBinding:
    workspace_id: str
    graph_id: str
    realized_projection_id: str
    runtime_id: str
    node_id: str
    provider_socket_name: str
    receiver_id: str
    selected_configuration_digest: str
    declaration_identity: str

    def __post_init__(self):
        _scope(self)
        _text(self.graph_id)
        _text(self.realized_projection_id)
        for value in (self.selected_configuration_digest, self.declaration_identity):
            _require(type(value) is str and re.fullmatch("[0-9a-f]{64}", value) is not None)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def derive_receiver_bindings(workspace_id, graph_id, projection_id, descriptor):
    """Index explicit selected V2 material, never infer authority from its presence.

    Other application artifacts are not searched. C owns the admission/profile
    decision; this function checks the representation C supplies and readers use.
    """
    try:
        graph = DEFAULT_GRAPH_CODEC.decode(descriptor)
        result, receiver_ids = [], set()
        for node in graph.nodes.values():
            environment = node.public_environment + node.socket_environment
            slots = tuple(item for item in environment
                          if item.name == WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT)
            if not slots:
                continue
            _require(len(slots) == 1)
            artifacts = tuple(item for item in node.configuration_artifacts if item.target_path == slots[0].value)
            _require(len(artifacts) == 1)
            raw = artifacts[0].content.encode("utf-8")
            _require(len(raw) <= MAX_WRAPPER_CONFIGURATION_BYTES)
            document = json.loads(raw, object_pairs_hook=_unique_object)
            _require(type(document) is dict)
            if document.get("profile") != "workload-node-control-configuration.v2":
                continue
            artifact = select_receiver_node_control_configuration_artifact(
                artifacts=node.configuration_artifacts, environment=environment,
                control_surfaces=node.block_spec.control_surfaces,
            )
            configured = ReceiverNodeControlConfigurationCodec().decode_bytes(artifact.content.encode("utf-8"))
            target = configured.target
            _require((target.workspace_id.value, target.runtime_id.value, target.node_id.value,
                      target.provider_socket_name.value) ==
                     (workspace_id, node.runtime_id, node.node_id, configured.declaration.surface.provider_socket_name.value))
            _require(target.receiver_id not in receiver_ids)
            receiver_ids.add(target.receiver_id)
            result.append(ReceiverBinding(workspace_id, graph_id, projection_id,
                node.runtime_id, node.node_id, target.provider_socket_name.value,
                target.receiver_id, artifact.content_digest, configured.declaration.identity().value))
        return tuple(sorted(result, key=lambda item: (item.node_id, item.provider_socket_name)))
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        failure = ReceiverLifecycleStorageError("receiver storage is unavailable")
    raise failure
