"""Immutable execution footprints and internal, nonauthorizing evidence values.

Receiver identities and current graph pointers do not define historical scope.
Only the original stored plan and its pinned material do. None of these values
is permission to mutate a receiver or to omit a request from a later gate.
"""

from dataclasses import dataclass, field
from hashlib import sha256

import rfc8785

from control_plane_kit_core.planning import (
    AddSocketConnection, AllocatePublicIngress, CleanupConfigurationInstances, Compensate,
    CompensationMaterialSource, ObserveManagementBootstrap, ObserveNodeHealth,
    ReconcileNode, ReconcileRuntime, RemoveNodeResource, RemovePublicIngress,
    RemoveRuntimeResource, RemoveSocketConnection, StartNode, StartRuntime,
    StopNode, StopRuntime, SwitchSocketConnection, WaitForHealthy,
)
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
from control_plane_kit_core.operations import ActivityEventKind, ActivityRunStatus
from control_plane_kit_operations.plan_derivation import encode_stored_activity_plan
from control_plane_kit_operations.records import (
    ActivityPlanRecord, ExecutionRequestIdentity, RealizedGraphProjectionKind,
    RealizedGraphProjectionRecord,
)


MAX_SCOPES = 1024
MAX_REQUESTS = 64
MAX_CANDIDATE_ROWS = 4096
MAX_RUNS = 256
MAX_EFFECT_ROWS = 2048
MAX_EVENTS = 8192
MAX_VALUE_BYTES = 16 * 1024 * 1024
MAX_DOCUMENT_BYTES = 1024 * 1024


class ReceiverScopeUnavailable(ValueError):
    """Fixed, candidate-free refusal of incomplete or incongruent evidence."""


class ReceiverScopeCapacity(ValueError):
    """Complete evidence cannot be proved within the frozen finite budgets."""


def _require(condition):
    if condition is not True:
        raise ReceiverScopeUnavailable("receiver scope evidence is unavailable")


def _text(value):
    _require(type(value) is str and bool(value.strip()))
    if len(value.encode("utf-8")) > 2048:
        raise ReceiverScopeCapacity("receiver scope evidence exceeds capacity")


def _capacity(condition):
    if condition is not True:
        raise ReceiverScopeCapacity("receiver scope evidence exceeds capacity")


@dataclass(frozen=True, slots=True)
class ExecutionReceiverScope:
    runtime_id: str
    node_id: str | None = None

    def __post_init__(self):
        _text(self.runtime_id)
        if self.node_id is not None:
            _text(self.node_id)

    @property
    def scope_kind(self):
        return "runtime" if self.node_id is None else "node"


@dataclass(frozen=True, slots=True)
class DerivedExecutionReceiverScopes:
    scopes: tuple[ExecutionReceiverScope, ...]
    source_digest: str


_NODE_OPERATIONS = (StartNode, StopNode, ReconcileNode, RemoveNodeResource)
_RUNTIME_OPERATIONS = (StartRuntime, StopRuntime, ReconcileRuntime, RemoveRuntimeResource)
_BASE_OPERATIONS = (StopNode, RemoveNodeResource, StopRuntime, RemoveRuntimeResource, RemoveSocketConnection, RemovePublicIngress)
_NON_RECEIVER_OPERATIONS = (
    WaitForHealthy, ObserveManagementBootstrap, ObserveNodeHealth,
    AddSocketConnection, SwitchSocketConnection, RemoveSocketConnection,
    AllocatePublicIngress, RemovePublicIngress,
)


def _cleanup_scope(operation, workspace_id, *graphs):
    """Conservative historical runtime conflict, never cleanup authority."""
    _require(type(operation) is CleanupConfigurationInstances)
    # Consume Core's closed candidate law without repairing malformed values.
    _require(CleanupConfigurationInstances(operation.instances) == operation)
    candidate = operation.instances[0]
    _require(candidate.workspace_id == workspace_id)
    runtimes = tuple(graph.runtimes.get(candidate.runtime_id) for graph in graphs)
    _require(bool(runtimes) and all(runtime is not None for runtime in runtimes))
    first = runtimes[0]
    _require(first.authority_ref is not None and all(
        (runtime.kind, runtime.authority_ref) == (first.kind, first.authority_ref) for runtime in runtimes))
    # The node/artifacts may have departed from both graphs. Their exact
    # identities remain in the operation/proposal committed by the scope digest.
    return ExecutionReceiverScope(candidate.runtime_id)


def _operation_scope(operation, graph):
    if type(operation) is CleanupConfigurationInstances:
        return _cleanup_scope(operation, operation.instances[0].workspace_id, graph)
    if type(operation) in _NODE_OPERATIONS:
        node_id = operation.target.node_id
        _require(node_id in graph.nodes)
        runtime_id = graph.nodes[node_id].runtime_id
        _require(runtime_id in graph.runtimes)
        return ExecutionReceiverScope(runtime_id, node_id)
    if type(operation) in _RUNTIME_OPERATIONS:
        runtime_id = operation.target.runtime_id
        _require(runtime_id in graph.runtimes)
        return ExecutionReceiverScope(runtime_id)
    # An empty receiver footprint is not a declaration of no external effects:
    # ingress retains its independent admission and effect owners.
    _require(type(operation) in _NON_RECEIVER_OPERATIONS)
    return None


def _projection(identity, plan, record, side):
    _require(type(record) is RealizedGraphProjectionRecord)
    _require(record.workspace_id == identity.workspace_id)
    _require(record.source_authored_graph_id == getattr(plan, side + "_graph_id"))
    pinned = getattr(plan, side + "_realized_projection_id")
    if pinned is None:
        _require(record.projection_kind is RealizedGraphProjectionKind.IDENTITY)
        _require(record.projection_key == "identity")
    else:
        _require(pinned == record.projection_id)
    for value in (record.projection_id, record.source_authored_graph_id):
        _text(value)
    _capacity(len(rfc8785.dumps(record.graph_descriptor)) <= MAX_DOCUMENT_BYTES)
    return DEFAULT_GRAPH_CODEC.decode(record.graph_descriptor)


def derive_execution_receiver_scopes(identity, plan, base_projection, desired_projection):
    """Derive forward and inverse coverage from exact original source values."""
    try:
        return _derive(identity, plan, base_projection, desired_projection)
    except ReceiverScopeCapacity:
        raise
    except (ValueError, TypeError, AttributeError, KeyError, OverflowError, RecursionError):
        pass
    raise ReceiverScopeUnavailable("receiver scope evidence is unavailable") from None


def _effect_receiver_scope(identity, original, intent, *, compensation):
    """Associate one actual intent with its immutable forward/inverse material."""
    plan, base, desired = original
    _require(intent.source.workspace_id == identity.workspace_id
             and intent.source.request_id == identity.request_id
             and intent.source.plan_id == plan.plan_id
             and intent.source.base_graph_id == plan.base_graph_id
             and intent.source.desired_graph_id == plan.desired_graph_id)
    activity = plan.plan.activity(intent.activity_id)
    if compensation:
        inverse = activity.compensation
        _require(type(inverse) is Compensate and intent.operation == inverse.operation)
        material = base if inverse.material_source is CompensationMaterialSource.BASE_GRAPH else desired
    else:
        _require(intent.operation == activity.operation)
        material = base if type(intent.operation) in _BASE_OPERATIONS else desired
    graph = DEFAULT_GRAPH_CODEC.decode(material.graph_descriptor)
    if type(intent.operation) is CleanupConfigurationInstances:
        _require(not compensation and identity.plan_id == plan.plan_id and identity.session_id == plan.session_id)
        _cleanup_scope(intent.operation, identity.workspace_id,
            _projection(identity, plan, base, "base"), _projection(identity, plan, desired, "desired"))
    return _operation_scope(intent.operation, graph), graph, material


def _validate_effect_receiver_material(identity, original, derived, intent, *, compensation):
    """Receiver/coordinate proof, not a second runtime/secret translator."""
    from dataclasses import replace
    from control_plane_kit_operations.receiver_lifecycle import derive_receiver_bindings

    scope, graph, projection = _effect_receiver_scope(identity, original, intent, compensation=compensation)
    if scope is None:
        return
    _require(scope in derived.scopes)
    _require(intent.runtime_kind is graph.runtimes[scope.runtime_id].kind)
    _require(intent.authority_ref == graph.runtimes[scope.runtime_id].authority_ref)
    for material in intent.products:
        _require(material.node_id in graph.nodes)
        node = graph.nodes[material.node_id]
        _require(material.runtime_id == node.runtime_id == scope.runtime_id
                 and (scope.node_id is None or material.node_id == scope.node_id))
    bindings = derive_receiver_bindings(identity.workspace_id, projection.source_authored_graph_id,
        projection.projection_id, projection.graph_descriptor)
    relevant = tuple(item for item in bindings if item.runtime_id == scope.runtime_id
                     and (scope.node_id is None or item.node_id == scope.node_id))
    # Runtime operations have no product material. Their exact runtime target,
    # kind and immutable coverage still include every node on that runtime.
    if scope.node_id is None:
        _require(not intent.products)
        return
    if relevant or intent.products:
        _require(len(intent.products) == 1)
        material, = intent.products
        node = graph.nodes[scope.node_id]
        _require(material.reference.identity.key == node.metadata.get("product_identity")
                 and material.reference.descriptor_sha256.value == node.metadata.get("product_descriptor_digest"))
        _require(material.product.runtime_contract.control_surfaces == node.block_spec.control_surfaces)
        actual = replace(node, configuration_artifacts=material.product.runtime_contract.configuration_artifacts,
            public_environment=material.public_environment, socket_environment=material.socket_environment)
        candidate = replace(graph, nodes={**graph.nodes, scope.node_id: actual})
        actual_bindings = derive_receiver_bindings(identity.workspace_id, projection.source_authored_graph_id,
            projection.projection_id, DEFAULT_GRAPH_CODEC.encode(candidate))
        _require(actual_bindings == bindings)


def _derive(identity, plan, base_projection, desired_projection):
    _require(type(identity) is ExecutionRequestIdentity and type(plan) is ActivityPlanRecord)
    for value in (identity.workspace_id, identity.request_id, identity.session_id, identity.plan_id):
        _text(value)
    _require(identity.plan_id == plan.plan_id and identity.session_id == plan.session_id)
    _capacity(len(plan.plan.activities) <= MAX_SCOPES)
    base = _projection(identity, plan, base_projection, "base")
    desired = _projection(identity, plan, desired_projection, "desired")
    scopes = set()
    for activity in plan.plan.activities:
        operation = activity.operation
        graph = base if type(operation) in _BASE_OPERATIONS else desired
        if type(operation) is CleanupConfigurationInstances:
            _cleanup_scope(operation, identity.workspace_id, base, desired)
        scope = _operation_scope(operation, graph)
        if scope is not None:
            scopes.add(scope)
        inverse = activity.compensation
        if type(inverse) is Compensate:
            _require(type(inverse.operation) is not CleanupConfigurationInstances)
            _require(type(inverse.material_source) is CompensationMaterialSource)
            material = base if inverse.material_source is CompensationMaterialSource.BASE_GRAPH else desired
            scope = _operation_scope(inverse.operation, material)
            if scope is not None:
                scopes.add(scope)
        _capacity(len(scopes) <= MAX_SCOPES)
    ordered = tuple(sorted(scopes, key=lambda scope: (scope.runtime_id, scope.scope_kind, scope.node_id or "")))
    for scope in ordered:
        _capacity(sum(len(value.encode("utf-8")) for value in (
            identity.workspace_id, scope.runtime_id, identity.request_id, scope.node_id or "",
        )) <= 1024)
    payload = rfc8785.dumps(encode_stored_activity_plan(plan.plan, profile=plan.derivation_profile,
        cleanup_proposal=plan.cleanup_proposal))
    _capacity(len(payload) <= MAX_DOCUMENT_BYTES)
    witness = {
        "profile": "receiver-execution-scopes.v1",
        "workspace_id": identity.workspace_id, "request_id": identity.request_id,
        "plan_id": plan.plan_id, "base_graph_id": plan.base_graph_id,
        "base_realized_projection_id": base_projection.projection_id,
        "base_realized_projection_digest": base_projection.projection_digest,
        "desired_graph_id": plan.desired_graph_id,
        "desired_realized_projection_id": desired_projection.projection_id,
        "desired_realized_projection_digest": desired_projection.projection_digest,
        "desired_graph_revision": plan.desired_graph_revision,
        "plan_digest": sha256(payload).hexdigest(),
        "scopes": [{"scope_kind": scope.scope_kind, "runtime_id": scope.runtime_id,
                    "node_id": scope.node_id} for scope in ordered],
    }
    return DerivedExecutionReceiverScopes(ordered, sha256(rfc8785.dumps(witness)).hexdigest())


@dataclass(frozen=True, slots=True, repr=False)
class _RunEvidence:
    run: object
    events: tuple
    attempts: tuple
    intents: tuple
    outcomes: tuple
    compensations: tuple
    bindings: tuple
    cancellation_actions: tuple
    advancement: object


@dataclass(frozen=True, slots=True, repr=False)
class _RequestEvidence:
    request: object
    original: tuple
    runs: tuple[_RunEvidence, ...]


@dataclass(frozen=True, slots=True)
class ReceiverScopeEvidence:
    """Closed internal result; retained source material is never a default repr."""

    state: str
    requests: tuple[_RequestEvidence, ...] = field(default=(), repr=False)

    def __post_init__(self):
        _require(self.state in ("complete", "unavailable", "capacity"))
        _require(self.state == "complete" or self.requests == ())


@dataclass(frozen=True, slots=True)
class ReceiverScopeClassification:
    disposition: str
    reason: str
    request_ids: tuple[str, ...] = ()
    run_ids: tuple[str, ...] = ()


def _cancel_pair(request, evidence):
    run = evidence.run
    events = tuple(event for event in evidence.events if event.kind is ActivityEventKind.RUN_CANCELLED)
    _require(len(events) == 1 and len(evidence.cancellation_actions) == 1)
    event, action = events[0], evidence.cancellation_actions[0]
    _require(action.session_id == request.identity.session_id)
    _require(action.action_type.value == "cancel-run")
    _require(event.run_id == run.run_id and event.occurred_at == action.created_at == run.settled_at)
    expected = {
        "execution_request_id": request.identity.request_id,
        "plan_id": request.identity.plan_id, "run_id": run.run_id,
        "run_status": "cancelled", "event_id": event.event_id,
        "event_type": event.kind.value, "event_ordinal": event.ordinal,
    }
    _require(all(type(action.payload.get(key)) is type(value) and action.payload[key] == value
                 for key, value in expected.items()))


def _classify_complete(evidence):
    conflicts, conflict_runs, closure, closure_runs = set(), set(), set(), set()
    for candidate in evidence.requests:
        request = candidate.request
        identity = request.identity
        if not candidate.runs:
            conflicts.add(identity.request_id)
            continue
        dispatched = any(run.attempts or run.intents or run.compensations or run.bindings
                         or any(event.kind.value.startswith("step_") for event in run.events)
                         for run in candidate.runs)
        for retained in candidate.runs:
            run = retained.run
            _require(run.admission.request_id == identity.request_id and run.plan_id == identity.plan_id)
            _require(tuple(event.ordinal for event in retained.events) == tuple(range(1, len(retained.events) + 1)))
            _require(all(event.run_id == run.run_id for event in retained.events))
            _require(len({event.event_id for event in retained.events}) == len(retained.events))
            _require(retained.advancement["state"] in ("none-recorded", "accepted"))
            if retained.advancement["state"] == "accepted":
                _require(run.status is ActivityRunStatus.SUCCEEDED)
                continue
            if run.status is ActivityRunStatus.CANCELLED:
                _cancel_pair(request, retained)
                if not dispatched:
                    closure.add(identity.request_id)
                    closure_runs.add(run.run_id)
                    continue
            # Complete failures, recovered failures and successful inverses are
            # retained affecting history, not malformed uncertainty or disposal.
            conflicts.add(identity.request_id)
            conflict_runs.add(run.run_id)
    if conflicts:
        return ReceiverScopeClassification("conflict", "retained affecting execution",
            tuple(sorted(conflicts)), tuple(sorted(conflict_runs)))
    if closure:
        return ReceiverScopeClassification("requires-fresh-gate-closure", "complete cancellation requires fresh gate closure",
            tuple(sorted(closure)), tuple(sorted(closure_runs)))
    return ReceiverScopeClassification("nonconflicting", "complete accounted history")


def classify_receiver_scope_evidence(evidence):
    """Pure accounting only: no exclusion parameter, closure flag or authority."""
    try:
        _require(type(evidence) is ReceiverScopeEvidence)
        if evidence.state == "capacity":
            return ReceiverScopeClassification("capacity", "receiver scope evidence exceeds capacity")
        if evidence.state == "unavailable":
            return ReceiverScopeClassification("unavailable", "receiver scope evidence is unavailable")
        return _classify_complete(evidence)
    except (ValueError, TypeError, AttributeError, KeyError):
        pass
    return ReceiverScopeClassification("unavailable", "receiver scope evidence is unavailable")
