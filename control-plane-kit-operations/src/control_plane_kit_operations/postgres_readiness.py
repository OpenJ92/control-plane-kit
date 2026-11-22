"""Read-only original-record join, not current authority or SQL dispatch.

The loader makes bounded existing point reads. Its caller owns the transaction
and any reached-check/current permission guards. No fact here proves that the
addressed process still has the recorded realization or qualified bytes.
"""
from datetime import datetime, timezone

from control_plane_kit_core.operations import EffectAttemptIdentity, EffectAttemptStatus
from control_plane_kit_core.planning import ActivityId, NodeTarget, StartNode, WaitForHealthy
from control_plane_kit_core.postgres_readiness import PostgresReadinessBinding
from control_plane_kit_core.receiver_configuration import (
    ReceiverNodeControlConfigurationCodec, select_receiver_node_control_configuration_artifact,
)
from control_plane_kit_core.runtime_effect_observation import runtime_effect_request_for_intent
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, validate_graph
from control_plane_kit_operations.effect_attempt_intent_evidence import EffectAttemptIntentRecord
from control_plane_kit_operations.effect_attempts import EffectAttemptRecord
from control_plane_kit_operations.effect_outcome_evidence import EffectAttemptOutcomeRecord, _require_original_installation_outcome
from control_plane_kit_operations.products import RegisteredProduct
from control_plane_kit_operations.graph_authoring import product_reference_in_node
from control_plane_kit_operations.records import (
    ActivityPlanRecord, ActivityPlanStatus, RealizedGraphProjectionRecord, ExecutionRequestRecord,
    _canonical_timestamp_instant,
)
from control_plane_kit_operations.runtime_management_admission import _node_matches_product


class PostgresReadinessProvenanceUnavailable(ValueError):
    """Missing or incongruent original facts; no partial binding or candidate text."""


def _require(condition):
    if condition is not True:
        raise ValueError


def _checked(action):
    try:
        return action()
    except (ValueError, TypeError, AttributeError, KeyError, IndexError, OverflowError, RecursionError):
        pass
    raise PostgresReadinessProvenanceUnavailable("original PostgreSQL readiness evidence is unavailable")


def _start_activity(plan, wait_original):
    _require(type(plan) is ActivityPlanRecord and type(wait_original) is EffectAttemptIntentRecord)
    ActivityPlanRecord.__post_init__(plan)
    EffectAttemptIntentRecord.__post_init__(wait_original)
    intent = wait_original.intent
    _require(type(intent.operation) is WaitForHealthy and type(intent.operation.target) is NodeTarget)
    waiter = plan.plan.activity(ActivityId(wait_original.identity.activity_id))
    _require(waiter.operation == intent.operation)
    starts = tuple(activity for activity in plan.plan.activities
        if type(activity.operation) is StartNode and activity.operation.target == intent.operation.target)
    _require(len(starts) == 1 and starts[0].activity_id in tuple(value.predecessor for value in waiter.dependencies))
    return starts[0]


def bind_original_postgres_readiness(*, plan, request, current, desired, product,
        wait_original, wait_attempt, install_original, install_attempt, install_outcome,
        observer, check_id):
    """Congruence of supplied owner records; neither loading nor a fresh permit."""
    return _checked(lambda: _bind(plan, request, current, desired, product, wait_original,
        wait_attempt, install_original, install_attempt, install_outcome, observer, check_id))


def _bind(plan, request, current, desired, product, wait_original, wait_attempt,
        install_original, install_attempt, install_outcome, observer, check_id):
    start = _start_activity(plan, wait_original)
    _require(type(request) is ExecutionRequestRecord and type(product) is RegisteredProduct
        and type(wait_attempt) is type(install_attempt) is EffectAttemptRecord
        and type(install_original) is EffectAttemptIntentRecord
        and type(install_outcome) is EffectAttemptOutcomeRecord)
    for value in (request, product, wait_attempt, install_attempt, install_original, install_outcome):
        type(value).__post_init__(value)
    source = wait_original.intent.source
    _require(install_outcome.attempt == install_attempt and install_outcome.workspace_id == source.workspace_id)
    outcome = install_outcome.outcome
    _require(plan.status is ActivityPlanStatus.PLANNED
        and (plan.plan_id, plan.base_graph_id, plan.desired_graph_id, plan.session_id)
            == (source.plan_id, source.base_graph_id, source.desired_graph_id, request.identity.session_id)
        and (request.identity.workspace_id, request.identity.request_id, request.identity.plan_id)
            == (wait_original.workspace_id, wait_original.request_id, plan.plan_id)
        and wait_original.identity == wait_attempt.state.identity
        and wait_original.original_start_event == wait_attempt.original_start_event
        and wait_original.request_fingerprint == wait_attempt.state.request_fingerprint
        and wait_attempt.state.status is EffectAttemptStatus.STARTED
        and wait_attempt.state.prior_attempt is None
        and wait_attempt.original_start_event == wait_attempt.latest_transition_event)
    _require(install_original.identity == EffectAttemptIdentity(wait_original.identity.run_id, start.activity_id.value, 1)
        and install_original.intent.operation == start.operation
        and install_attempt.state.status is EffectAttemptStatus.SUCCEEDED
        and outcome.status is EffectAttemptStatus.SUCCEEDED
        and outcome.outcome_fingerprint == install_attempt.state.outcome_fingerprint
        and install_attempt.state.prior_attempt is None
        and install_attempt.latest_transition_event.ordinal < wait_attempt.original_start_event.ordinal)
    receipt = _require_original_installation_outcome(install_original, install_attempt, outcome,
        original_plan=plan, request=request)
    _require(receipt is not None)
    graphs = []
    for projection, identity, authored in ((current, plan.base_realized_projection_id, plan.base_graph_id),
            (desired, plan.desired_realized_projection_id, plan.desired_graph_id)):
        _require(type(projection) is RealizedGraphProjectionRecord)
        RealizedGraphProjectionRecord.__post_init__(projection)
        _require((projection.workspace_id, projection.projection_id, projection.source_authored_graph_id)
            == (source.workspace_id, identity, authored))
        graph = validate_graph(DEFAULT_GRAPH_CODEC.decode(projection.graph_descriptor))
        graph.require_valid()
        graphs.append(graph.graph)
    _require(not graphs[0].nodes and not graphs[0].runtimes and not graphs[0].public_ingresses)
    node = graphs[1].node(wait_original.intent.operation.target.node_id)
    material, = wait_original.intent.products
    _require(product.workspace_id == source.workspace_id and product.reference == material.reference
        and product_reference_in_node(node) == material.reference
        and product.descriptor_document.product == material.product
        and _node_matches_product(node, product)
        and node.secret_selections == material.secret_selections
        and node.public_environment == material.public_environment
        and node.socket_environment == material.socket_environment
        and node.runtime_id == material.runtime_id)
    artifact = select_receiver_node_control_configuration_artifact(artifacts=node.configuration_artifacts,
        environment=node.public_environment + node.socket_environment, control_surfaces=node.block_spec.control_surfaces)
    receiver = ReceiverNodeControlConfigurationCodec().decode_bytes(artifact.content.encode())
    started = _canonical_timestamp_instant(wait_attempt.original_start_event.occurred_at, "original SQL start")
    delta = started - datetime(1970, 1, 1, tzinfo=timezone.utc)
    # Integer arithmetic avoids extending the original bound via float rounding.
    started_ms = (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000
    return PostgresReadinessBinding(
        wait=runtime_effect_request_for_intent(wait_original.intent, effect_id=wait_original.original_start_event.event_id),
        attempt=wait_original.identity,
        installation=runtime_effect_request_for_intent(install_original.intent, effect_id=install_original.original_start_event.event_id),
        receipt=receipt, observer=observer, receiver=receiver,
        base_projection_id=current.projection_id, desired_projection_id=desired.projection_id,
        check_id=check_id, started_at_epoch_ms=started_ms)


def load_original_postgres_readiness(stores, *, identity, observer, check_id):
    """Read one exact original Wait and its unique first StartNode dependency.

    Reconstructs a deterministic request from retained facts. It creates no
    invocation, signs nothing and must never resolve an ambiguous send by replay.
    """
    def load():
        _require(type(identity) is EffectAttemptIdentity)
        original = stores.effect_attempt_intents.get(identity)
        plan = stores.activity_history.get_plan(original.intent.source.plan_id)
        start = _start_activity(plan, original)
        predecessor = EffectAttemptIdentity(identity.run_id, start.activity_id.value, 1)
        installed = stores.effect_attempts.get(predecessor)
        retained = stores.effect_outcomes.get(predecessor, installed.latest_transition_event.event_id)
        _require(retained.attempt == installed and retained.workspace_id == original.workspace_id)
        return bind_original_postgres_readiness(plan=plan,
            request=stores.execution.get_request(original.request_id),
            current=stores.realized_graphs.get(plan.base_realized_projection_id),
            desired=stores.realized_graphs.get(plan.desired_realized_projection_id),
            product=stores.registered_products.get(original.workspace_id, original.intent.products[0].reference),
            wait_original=original, wait_attempt=stores.effect_attempts.get(identity),
            install_original=stores.effect_attempt_intents.get(predecessor), install_attempt=installed,
            install_outcome=retained, observer=observer, check_id=check_id)
    return _checked(load)


__all__ = ["PostgresReadinessProvenanceUnavailable", "bind_original_postgres_readiness", "load_original_postgres_readiness"]
