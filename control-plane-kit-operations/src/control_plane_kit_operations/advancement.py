"""Guarded current-graph advancement from durable execution evidence."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from control_plane_kit_core.operations import RunId
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
    LifecycleOperationKind,
)
from control_plane_kit_core.planning import (
    ActivityPlan,
    SagaJournalError,
    SagaStateError,
    ScheduleEvidenceError,
    derive_schedule,
    project_activity_journal,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.activity_journal import activity_journal_events
from control_plane_kit_operations.execution_leases import ExecutionLeaseFence
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityRunRecord,
    BoundedEvidence,
    ExecutionRequestRecord,
    OperationActionRecord,
    OperationSessionStatus,
    RealizedGraphProjectionRecord,
    WorkspaceRecord,
)
from control_plane_kit_operations.workflows import (
    IdempotencyKey,
    InvalidOperationCommand,
)


class CurrentGraphAdvancementError(RuntimeError):
    """Base error for guarded current-graph advancement."""


class CurrentGraphAdvancementNotFound(CurrentGraphAdvancementError):
    """Raised when required workspace, graph, plan, request, run, or event is absent."""


class CurrentGraphAdvancementConflict(CurrentGraphAdvancementError):
    """Raised when pinned graph, plan, request, or workspace truth disagrees."""


class CurrentGraphAdvancementDenied(CurrentGraphAdvancementError):
    """Raised when worker authority cannot advance this run."""


class CurrentGraphAdvancementIncomplete(CurrentGraphAdvancementError):
    """Raised when durable activity evidence does not prove successful realization."""


class CurrentGraphAdvancementIdempotencyConflict(CurrentGraphAdvancementError):
    """Raised when one idempotency key is reused for a different advancement."""


@dataclass(frozen=True)
class AdvanceCurrentGraph:
    """Request one guarded projection advance from complete run evidence."""

    workspace_id: str
    run_id: str
    plan_id: str
    expected_current_graph_id: str
    expected_current_realized_projection_id: str
    desired_graph_id: str
    desired_realized_projection_id: str
    expected_desired_graph_revision: int
    authority: ExecutionWorkerAuthority
    fence: ExecutionLeaseFence
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        _required_text(self.workspace_id, "workspace_id")
        _require_run_id(self.run_id)
        _required_text(self.plan_id, "plan_id")
        _required_text(self.expected_current_graph_id, "expected_current_graph_id")
        _required_text(
            self.expected_current_realized_projection_id,
            "expected_current_realized_projection_id",
        )
        _required_text(self.desired_graph_id, "desired_graph_id")
        _required_text(
            self.desired_realized_projection_id,
            "desired_realized_projection_id",
        )
        if (
            type(self.expected_desired_graph_revision) is not int
            or self.expected_desired_graph_revision < 0
        ):
            raise InvalidOperationCommand(
                "expected_desired_graph_revision must be nonnegative"
            )
        if (
            self.expected_current_graph_id == self.desired_graph_id
            and self.expected_current_realized_projection_id
            == self.desired_realized_projection_id
        ):
            raise InvalidOperationCommand(
                "current graph advancement requires distinct realized lineage"
            )
        if not isinstance(self.authority, ExecutionWorkerAuthority):
            raise InvalidOperationCommand("authority must be ExecutionWorkerAuthority")
        if type(self.fence) is not ExecutionLeaseFence:
            raise InvalidOperationCommand("fence must be ExecutionLeaseFence")
        if self.authority.worker_id != self.fence.worker_id:
            raise InvalidOperationCommand("authority and fence must agree")
        if not isinstance(self.idempotency_key, IdempotencyKey):
            raise InvalidOperationCommand("idempotency_key must be IdempotencyKey")


@dataclass(frozen=True)
class CurrentGraphAdvancementResult:
    """Stable evidence returned for an original command or exact replay."""

    workspace_id: str
    from_authored_graph_id: str
    from_realized_projection_id: str
    to_authored_graph_id: str
    to_realized_projection_id: str
    to_realized_projection_digest: str
    desired_graph_revision: int
    run_id: str
    plan_id: str
    event: ActivityEventRecord
    action: OperationActionRecord
    replayed: bool = False

    def __post_init__(self) -> None:
        for value, name in (
            (self.workspace_id, "workspace_id"),
            (self.from_authored_graph_id, "from_authored_graph_id"),
            (self.from_realized_projection_id, "from_realized_projection_id"),
            (self.to_authored_graph_id, "to_authored_graph_id"),
            (self.to_realized_projection_id, "to_realized_projection_id"),
        ):
            _required_text(value, name)
        _require_run_id(self.run_id)
        _required_text(self.plan_id, "plan_id")
        if (
            len(self.to_realized_projection_digest) != 64
            or any(
                value not in "0123456789abcdef"
                for value in self.to_realized_projection_digest
            )
        ):
            raise CurrentGraphAdvancementError(
                "advancement result requires a lowercase sha256 projection digest"
            )
        if type(self.desired_graph_revision) is not int or self.desired_graph_revision < 0:
            raise CurrentGraphAdvancementError(
                "advancement result requires a nonnegative desired revision"
            )
        if self.event.kind is not ActivityEventKind.CURRENT_GRAPH_ADVANCED:
            raise CurrentGraphAdvancementError(
                "advancement result requires current-graph activity evidence"
            )
        if self.event.failure is not None:
            raise CurrentGraphAdvancementError(
                "advancement event cannot carry failure evidence"
            )
        if self.action.action_type is not LifecycleOperationKind.ADVANCE_CURRENT_GRAPH:
            raise CurrentGraphAdvancementError(
                "advancement result requires current-graph operation evidence"
            )
        if self.action.payload.get("event_id") != self.event.event_id:
            raise CurrentGraphAdvancementError("advancement event/action disagree")
        _payload_text(self.action.payload, "execution_request_id")
        expected = {
            "workspace_id": self.workspace_id,
            "plan_id": self.plan_id,
            "run_id": self.run_id,
            "from_authored_graph_id": self.from_authored_graph_id,
            "from_realized_projection_id": self.from_realized_projection_id,
            "to_authored_graph_id": self.to_authored_graph_id,
            "to_realized_projection_id": self.to_realized_projection_id,
            "to_realized_projection_digest": self.to_realized_projection_digest,
            "desired_graph_revision": self.desired_graph_revision,
        }
        action_transition = {
            "workspace_id": _payload_text(self.action.payload, "workspace_id"),
            "plan_id": _payload_text(self.action.payload, "plan_id"),
            "run_id": _payload_text(self.action.payload, "run_id"),
            "from_authored_graph_id": _payload_text(
                self.action.payload,
                "from_authored_graph_id",
            ),
            "from_realized_projection_id": _payload_text(
                self.action.payload,
                "from_realized_projection_id",
            ),
            "to_authored_graph_id": _payload_text(
                self.action.payload,
                "to_authored_graph_id",
            ),
            "to_realized_projection_id": _payload_text(
                self.action.payload,
                "to_realized_projection_id",
            ),
            "to_realized_projection_digest": _payload_text(
                self.action.payload,
                "to_realized_projection_digest",
            ),
            "desired_graph_revision": _payload_nonnegative_integer(
                self.action.payload,
                "desired_graph_revision",
            ),
        }
        if action_transition != expected:
            raise CurrentGraphAdvancementError(
                "advancement action does not encode the claimed graph transition"
            )
        _payload_positive_integer(self.action.payload, "claim_generation")
        if self.event.run_id != self.run_id:
            raise CurrentGraphAdvancementError("advancement event belongs elsewhere")
        if self.event.evidence.descriptor() != expected:
            raise CurrentGraphAdvancementError(
                "advancement event does not encode the claimed graph transition"
            )

    @property
    def from_graph_id(self) -> str:
        """Compatibility alias for the authored source graph identity."""

        return self.from_authored_graph_id

    @property
    def to_graph_id(self) -> str:
        """Compatibility alias for the authored destination graph identity."""

        return self.to_authored_graph_id

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "from_authored_graph_id": self.from_authored_graph_id,
            "from_realized_projection_id": self.from_realized_projection_id,
            "to_authored_graph_id": self.to_authored_graph_id,
            "to_realized_projection_id": self.to_realized_projection_id,
            "to_realized_projection_digest": self.to_realized_projection_digest,
            "desired_graph_revision": self.desired_graph_revision,
            "from_graph_id": self.from_authored_graph_id,
            "to_graph_id": self.to_authored_graph_id,
            "run_id": self.run_id,
            "plan_id": self.plan_id,
            "event_id": self.event.event_id,
            "action_id": self.action.action_id,
            "replayed": self.replayed,
        }


class CurrentGraphAdvancementCommandService:
    """Advance a workspace current-graph projection in one transaction."""

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

    def execute(
        self,
        command: AdvanceCurrentGraph,
    ) -> CurrentGraphAdvancementResult:
        _require_operate_scope(command.authority)
        fingerprint = _fingerprint(command)
        with self._unit_of_work_factory() as unit_of_work, \
                unit_of_work.stores.configuration_preparation._advancement_evidence(
                    command.workspace_id, command.run_id):
            stores = unit_of_work.stores
            locator_run = _get_run(stores, command.run_id)
            locator_request = _get_request(
                stores,
                locator_run.admission.request_id,
            )

            history = stores.activity_history
            history.lock_action_idempotency(
                locator_request.identity.session_id,
                command.idempotency_key.value,
            )
            existing = _action_for_idempotency(
                history,
                locator_request.identity.session_id,
                command.idempotency_key.value,
            )
            if existing is not None:
                _require_replay_intent(existing, fingerprint)
                request = _get_request_for_update(
                    stores,
                    locator_request.identity.request_id,
                )
                run = _get_run_for_update(stores, command.run_id)
                _require_run_request_linkage(run, request)
                _require_worker_owns(request, command.authority, command.fence)
                result = _replay(
                    stores,
                    command,
                    request,
                    run,
                    existing,
                    fingerprint,
                )
                stores.configuration_acceptance._verify_replay(result)
                unit_of_work.commit()
                return result
            guard = stores.graphs.lock_receiver_lifecycle(command.workspace_id)
            request = _get_request_for_update(
                stores,
                locator_run.admission.request_id,
            )
            run = _get_run_for_update(stores, command.run_id)
            session = _get_session_for_update(
                history,
                locator_request.identity.session_id,
            )
            workspace = _get_workspace_for_update(stores, command.workspace_id)
            plan = _get_plan(history, command.plan_id)
            _require_run_request_linkage(run, request)
            if session.status is not OperationSessionStatus.OPEN:
                raise CurrentGraphAdvancementConflict(
                    "current graph advancement requires an open session"
                )
            if request.identity.session_id != session.session_id:
                raise CurrentGraphAdvancementConflict(
                    "activity run session linkage changed"
                )

            _require_worker_owns(request, command.authority, command.fence)
            current_projection, desired_projection = _require_identity(
                command,
                workspace,
                request,
                run,
                plan,
                stores.realized_graphs,
            )
            _require_graph_ownership(
                stores.graphs,
                command.workspace_id,
                command.expected_current_graph_id,
                command.desired_graph_id,
            )
            events = stores.execution.events_for_run(command.run_id)
            _require_complete_success(plan.plan, run, events)
            receiver_truth = _prepare_receiver_advancement(stores, workspace, request, run, guard)
            prepared = stores.configuration_acceptance._prepare(stores, workspace, request, run, plan, guard,
                current_projection, desired_projection)

            occurred_at = self._clock()
            evidence = BoundedEvidence.from_mapping(
                {
                    "workspace_id": command.workspace_id,
                    "plan_id": command.plan_id,
                    "run_id": command.run_id,
                    "from_authored_graph_id": command.expected_current_graph_id,
                    "from_realized_projection_id": current_projection.projection_id,
                    "to_authored_graph_id": command.desired_graph_id,
                    "to_realized_projection_id": desired_projection.projection_id,
                    "to_realized_projection_digest": (
                        desired_projection.projection_digest
                    ),
                    "desired_graph_revision": command.expected_desired_graph_revision,
                }
            )
            event = ActivityEventRecord(
                    self._id_factory(),
                    command.run_id,
                    stores.execution.next_event_ordinal(command.run_id),
                    ActivityEventKind.CURRENT_GRAPH_ADVANCED,
                    occurred_at,
                    evidence=evidence,
                )
            action = OperationActionRecord(
                    self._id_factory(),
                    request.identity.session_id,
                    history.next_action_ordinal(
                        request.identity.session_id
                    ),
                    LifecycleOperationKind.ADVANCE_CURRENT_GRAPH,
                    command.authority.worker_id,
                    payload={
                        **evidence.descriptor(),
                        "execution_request_id": request.identity.request_id,
                        "claim_generation": command.fence.generation,
                        "event_id": event.event_id,
                    },
                    created_at=occurred_at,
                    idempotency_key=command.idempotency_key.value,
                    intent_fingerprint=fingerprint,
                )
            prepared = prepared.with_records(event, action)
            stores.configuration_acceptance._preflight(prepared)
            advanced = stores.workspaces._compare_and_set_current_graph(
                command.workspace_id,
                prepared_receipt=prepared,
                expected_graph_id=command.expected_current_graph_id,
                replacement_graph_id=command.desired_graph_id,
                expected_realized_projection_id=(
                    command.expected_current_realized_projection_id
                ),
                replacement_realized_projection_id=(
                    command.desired_realized_projection_id
                ),
                expected_desired_graph_id=command.desired_graph_id,
                expected_desired_realized_projection_id=(
                    command.desired_realized_projection_id
                ),
                expected_desired_graph_revision=(
                    command.expected_desired_graph_revision
                ),
            )
            if advanced is None:
                raise CurrentGraphAdvancementConflict(
                    "workspace current graph changed concurrently"
                )

            event = stores.execution._add_advancement_event(event, prepared)
            action = history._add_advancement_action(action, prepared)
            stores.configuration_acceptance._insert(prepared)
            _finish_receiver_advancement(stores, request, run, guard, receiver_truth, action, advanced)
            unit_of_work.commit()
            return _result(event, action)


def _prepare_receiver_advancement(stores, workspace, request, run, guard):
    from control_plane_kit_operations.receiver_lifecycle import (
        _retained_receiver_material, _receiver_origin, _validate_receiver_execution,
        _validate_receiver_execution_approval,
    )
    from control_plane_kit_operations.receiver_execution_scopes import ExecutionReceiverScope
    try:
        before = _retained_receiver_material(stores, workspace.workspace_id,
            workspace.current_graph_id, workspace.current_realized_projection_id)
        after = _retained_receiver_material(stores, workspace.workspace_id,
            workspace.desired_graph_id, workspace.desired_realized_projection_id)
        if not before and not after:
            return None
        _validate_receiver_execution(stores, request, guard)
        _validate_receiver_execution_approval(stores, request)
        original, derived = stores.execution._receiver_execution_material(request.identity, guard)
        scopes = tuple(set(derived.scopes) | {ExecutionReceiverScope(item.runtime_id, item.node_id)
                                            for item in before + after})
        origins = {item.receiver_id: _receiver_origin(stores, item) for item in before + after}
        evidence = stores.execution.receiver_scope_evidence(workspace.workspace_id, scopes, guard)
        if evidence.state != "complete":
            raise ValueError("incomplete receiver execution evidence")
        _validate_advancing_receiver_material(request, run, original, derived, evidence)
        return before, after, origins, scopes, original, derived
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    raise CurrentGraphAdvancementConflict("receiver advancement evidence is unavailable")


def _finish_receiver_advancement(stores, request, run, guard, prepared, action, advanced):
    if prepared is None:
        return
    from dataclasses import replace
    from control_plane_kit_core.planning import RemoveNodeResource, RemoveRuntimeResource
    from control_plane_kit_operations.receiver_lifecycle import (
        _retained_receiver_material, _receiver_sources,
    )
    from control_plane_kit_operations.receiver_execution_scopes import classify_receiver_scope_evidence
    before, after, origins, scopes, original, derived = prepared
    plan, base, desired = original
    try:
        # Read the REAL tentative receipt through unchanged bounded accounting.
        # Every prior/other run remains visible; there is no own-run exclusion.
        evidence = stores.execution.receiver_scope_evidence(request.identity.workspace_id, scopes, guard)
        if classify_receiver_scope_evidence(evidence).disposition not in (
                "nonconflicting", "requires-fresh-gate-closure"):
            raise ValueError("conflicting receiver history")
        if (stores.execution.get_request(request.identity.request_id) != request
                or stores.execution.get_run(run.run_id) != run
                or stores.workspaces.get(request.identity.workspace_id) != advanced
                or stores.execution._receiver_execution_material(request.identity, guard)[0] != original
                or _retained_receiver_material(stores, request.identity.workspace_id,
                    plan.base_graph_id, base.projection_id) != before
                or _retained_receiver_material(stores, request.identity.workspace_id,
                    plan.desired_graph_id, desired.projection_id) != after
                or any(stores.graphs.receiver_introduction(request.identity.workspace_id, key) != value
                       for key, value in origins.items())):
            raise ValueError("receiver advancement truth changed")
        expected_origins = dict(origins)
        for item in after:
            origin = origins[item.receiver_id]
            if origin.first_accepted_action_id is None:
                stores.graphs._record_receiver_first_acceptance(request.identity.workspace_id, item.receiver_id,
                    action_id=action.action_id, session_id=action.session_id, lifecycle_guard=guard)
                expected_origins[item.receiver_id] = replace(origin, first_accepted_action_id=action.action_id,
                    first_accepted_session_id=action.session_id)
        remaining = {item.receiver_id for item in after}
        for item in before:
            if item.receiver_id in remaining:
                continue
            removed = any((type(activity.operation) is RemoveNodeResource
                           and activity.operation.target.node_id == item.node_id)
                          or (type(activity.operation) is RemoveRuntimeResource
                              and activity.operation.target.runtime_id == item.runtime_id)
                          for activity in plan.plan.activities)
            if not removed:
                raise ValueError("receiver removal is not complete")
            stores.graphs._record_receiver_retirement(request.identity.workspace_id, item.receiver_id,
                action_id=action.action_id, session_id=action.session_id, lifecycle_guard=guard)
            expected_origins[item.receiver_id] = replace(origins[item.receiver_id],
                retired_action_id=action.action_id, retired_session_id=action.session_id)
        _receiver_sources(stores, advanced)
        if (stores.execution.get_request(request.identity.request_id) != request
                or stores.execution.get_run(run.run_id) != run
                or stores.workspaces.get(request.identity.workspace_id) != advanced
                or stores.execution._receiver_execution_material(request.identity, guard)[0] != original
                or _retained_receiver_material(stores, request.identity.workspace_id,
                    plan.base_graph_id, base.projection_id) != before
                or _retained_receiver_material(stores, request.identity.workspace_id,
                    plan.desired_graph_id, desired.projection_id) != after
                or any(stores.graphs.receiver_introduction(request.identity.workspace_id, key) != value
                       for key, value in expected_origins.items())):
            raise ValueError("receiver witnesses changed")
        final_evidence = stores.execution.receiver_scope_evidence(request.identity.workspace_id, scopes, guard)
        if classify_receiver_scope_evidence(final_evidence).disposition not in (
                "nonconflicting", "requires-fresh-gate-closure"):
            raise ValueError("receiver final history changed")
        _validate_advancing_receiver_material(request, run, original, derived, final_evidence)
        return
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    raise CurrentGraphAdvancementConflict("receiver advancement evidence is unavailable")


def _validate_advancing_receiver_material(request, run, original, derived, evidence):
    from control_plane_kit_operations.receiver_execution_scopes import _validate_effect_receiver_material
    for candidate in evidence.requests:
        if candidate.request.identity == request.identity:
            for retained in candidate.runs:
                if retained.run.run_id == run.run_id:
                    for intent in retained.intents:
                        _validate_effect_receiver_material(request.identity, original, derived, intent.intent,
                            compensation=intent.original_start_event.kind is ActivityEventKind.STEP_COMPENSATION_STARTED)


def _get_run(stores: Any, run_id: str) -> ActivityRunRecord:
    missing_run = False
    try:
        run = stores.execution.get_run(run_id)
    except KeyError:
        missing_run = True
    if missing_run:
        raise CurrentGraphAdvancementNotFound("activity run was not found")
    return run


def _action_for_idempotency(
    history: Any,
    session_id: str,
    idempotency_key: str,
) -> OperationActionRecord | None:
    malformed_action = False
    try:
        action = history.action_for_idempotency(session_id, idempotency_key)
    except ValueError:
        malformed_action = True
    if malformed_action:
        raise CurrentGraphAdvancementError(
            "advancement operation evidence is malformed"
        )
    return action


def _get_run_for_update(stores: Any, run_id: str) -> ActivityRunRecord:
    missing_run = False
    try:
        run = stores.execution.get_run_for_update(run_id)
    except KeyError:
        missing_run = True
    if missing_run:
        raise CurrentGraphAdvancementNotFound("activity run was not found")
    return run


def _get_request(stores: Any, request_id: str) -> ExecutionRequestRecord:
    missing_request = False
    try:
        request = stores.execution.get_request(request_id)
    except KeyError:
        missing_request = True
    if missing_request:
        raise CurrentGraphAdvancementNotFound("execution request was not found")
    return request


def _get_request_for_update(
    stores: Any,
    request_id: str,
) -> ExecutionRequestRecord:
    missing_request = False
    try:
        request = stores.execution.get_request_for_update(request_id)
    except KeyError:
        missing_request = True
    if missing_request:
        raise CurrentGraphAdvancementNotFound("execution request was not found")
    return request


def _get_session_for_update(history: Any, session_id: str) -> Any:
    missing_session = False
    try:
        session = history.get_session_for_update(session_id)
    except KeyError:
        missing_session = True
    if missing_session:
        raise CurrentGraphAdvancementNotFound("operation session was not found")
    return session


def _get_workspace_for_update(stores: Any, workspace_id: str) -> WorkspaceRecord:
    missing_workspace = False
    try:
        workspace = stores.workspaces.get_for_update(workspace_id)
    except KeyError:
        missing_workspace = True
    if missing_workspace:
        raise CurrentGraphAdvancementNotFound("workspace was not found")
    return workspace


def _get_plan(history: Any, plan_id: str) -> ActivityPlanRecord:
    missing_plan = False
    try:
        plan = history.get_plan(plan_id)
    except KeyError:
        missing_plan = True
    if missing_plan:
        raise CurrentGraphAdvancementNotFound("activity plan was not found")
    return plan


def _require_run_request_linkage(
    run: ActivityRunRecord,
    request: ExecutionRequestRecord,
) -> None:
    if run.admission.request_id != request.identity.request_id:
        raise CurrentGraphAdvancementConflict(
            "activity run request linkage changed"
        )


def _require_identity(
    command: AdvanceCurrentGraph,
    workspace: WorkspaceRecord,
    request: ExecutionRequestRecord,
    run: ActivityRunRecord,
    plan: ActivityPlanRecord,
    realized_graph_store: Any,
) -> tuple[RealizedGraphProjectionRecord, RealizedGraphProjectionRecord]:
    if request.identity.workspace_id != command.workspace_id:
        raise CurrentGraphAdvancementConflict("execution request belongs elsewhere")
    if request.identity.plan_id != command.plan_id or run.plan_id != command.plan_id:
        raise CurrentGraphAdvancementConflict("run is not pinned to the supplied plan")
    if plan.session_id != request.identity.session_id:
        raise CurrentGraphAdvancementConflict("plan and request session do not agree")
    if plan.base_graph_id != command.expected_current_graph_id:
        raise CurrentGraphAdvancementConflict("plan base graph does not match command")
    if plan.desired_graph_id != command.desired_graph_id:
        raise CurrentGraphAdvancementConflict("plan desired graph does not match command")
    if (
        plan.base_realized_projection_id
        != command.expected_current_realized_projection_id
        or plan.desired_realized_projection_id
        != command.desired_realized_projection_id
        or plan.desired_graph_revision != command.expected_desired_graph_revision
    ):
        raise CurrentGraphAdvancementConflict(
            "plan realized lineage does not match command"
        )
    if workspace.current_graph_id != command.expected_current_graph_id:
        raise CurrentGraphAdvancementConflict("workspace current graph is stale")
    if workspace.desired_graph_id != command.desired_graph_id:
        raise CurrentGraphAdvancementConflict("workspace desired graph changed")
    if (
        workspace.current_realized_projection_id
        != command.expected_current_realized_projection_id
        or workspace.desired_realized_projection_id
        != command.desired_realized_projection_id
        or workspace.desired_graph_revision
        != command.expected_desired_graph_revision
    ):
        raise CurrentGraphAdvancementConflict("workspace realized lineage changed")
    current = _projection(
        realized_graph_store,
        projection_id=command.expected_current_realized_projection_id,
        workspace_id=command.workspace_id,
        source_authored_graph_id=command.expected_current_graph_id,
    )
    desired = _projection(
        realized_graph_store,
        projection_id=command.desired_realized_projection_id,
        workspace_id=command.workspace_id,
        source_authored_graph_id=command.desired_graph_id,
    )
    return current, desired


def _projection(
    store: Any,
    *,
    projection_id: str,
    workspace_id: str,
    source_authored_graph_id: str,
) -> RealizedGraphProjectionRecord:
    missing_projection = False
    try:
        record = store.get(projection_id)
    except KeyError:
        missing_projection = True
    if missing_projection:
        raise CurrentGraphAdvancementConflict(
            "realized graph projection is stale or missing"
        )
    if (
        record.workspace_id != workspace_id
        or record.source_authored_graph_id != source_authored_graph_id
    ):
        raise CurrentGraphAdvancementConflict(
            "realized graph projection belongs to different authored truth"
        )
    return record


def _require_graph_ownership(
    graph_store: Any,
    workspace_id: str,
    *graph_ids: str,
) -> None:
    missing_graph = False
    try:
        records = tuple(graph_store.get(graph_id) for graph_id in graph_ids)
    except KeyError:
        missing_graph = True
    if missing_graph:
        raise CurrentGraphAdvancementNotFound("authored graph was not found")
    if any(record.workspace_id != workspace_id for record in records):
        raise CurrentGraphAdvancementConflict("plan graph belongs to another workspace")


def _require_worker_owns(
    request: ExecutionRequestRecord,
    authority: ExecutionWorkerAuthority,
    fence: ExecutionLeaseFence,
) -> None:
    if (
        request.status is not ExecutionRequestStatus.CLAIMED
        or request.claim is None
        or request.claim.fence != fence
        or authority.worker_id != fence.worker_id
    ):
        raise CurrentGraphAdvancementDenied(
            "worker does not own the execution request claim"
        )


def _require_complete_success(
    plan: ActivityPlan,
    run: ActivityRunRecord,
    events: tuple[ActivityEventRecord, ...],
) -> None:
    if run.status is not ActivityRunStatus.SUCCEEDED or run.settled_at is None:
        raise CurrentGraphAdvancementIncomplete("run is not settled as succeeded")
    if not events or events[-1].kind is not ActivityEventKind.RUN_SUCCEEDED:
        raise CurrentGraphAdvancementIncomplete(
            "run success must be latest durable execution event"
        )
    if sum(event.kind is ActivityEventKind.RUN_SUCCEEDED for event in events) != 1:
        raise CurrentGraphAdvancementIncomplete(
            "run success requires exactly one terminal success event"
        )
    disqualifying = {
        ActivityEventKind.STEP_FAILED,
        ActivityEventKind.STEP_UNSUPPORTED,
        ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_FAILED,
        ActivityEventKind.STEP_COMPENSATION_STARTED,
        ActivityEventKind.STEP_COMPENSATION_SUCCEEDED,
        ActivityEventKind.STEP_COMPENSATION_FAILED,
        ActivityEventKind.RUN_COMPENSATION_STARTED,
        ActivityEventKind.RUN_COMPENSATION_SUCCEEDED,
        ActivityEventKind.RUN_COMPENSATION_FAILED,
        ActivityEventKind.RUN_UNCOMPENSATED_FAILURE_ACCEPTED,
        ActivityEventKind.RUN_FAILED,
        ActivityEventKind.RUN_CANCELLED,
    }
    if any(event.kind in disqualifying for event in events):
        raise CurrentGraphAdvancementIncomplete(
            "failed, unsupported, or compensating history cannot advance truth"
        )
    incoherent = False
    try:
        projection = project_activity_journal(plan, activity_journal_events(events))
        schedule = derive_schedule(plan, projection.state)
    except (SagaJournalError, SagaStateError, ScheduleEvidenceError):
        incoherent = True
    if incoherent:
        raise CurrentGraphAdvancementIncomplete(
            "durable saga evidence is structurally incoherent"
        )
    if projection.in_flight or projection.uncertain or not schedule.successful:
        raise CurrentGraphAdvancementIncomplete(
            "durable saga evidence is not a complete successful schedule"
        )
    expected = Counter(activity.activity_id.value for activity in plan.activities)
    succeeded = Counter(
        event.activity_id
        for event in events
        if event.kind
        in {
            ActivityEventKind.STEP_SUCCEEDED,
            ActivityEventKind.STEP_UNCERTAINTY_RESOLVED_SUCCEEDED,
        }
    )
    if succeeded != expected:
        raise CurrentGraphAdvancementIncomplete(
            "durable step-success evidence does not exactly cover the activity plan"
        )


def _require_replay_intent(
    action: OperationActionRecord,
    fingerprint: str,
) -> None:
    if action.action_type is not LifecycleOperationKind.ADVANCE_CURRENT_GRAPH:
        raise CurrentGraphAdvancementIdempotencyConflict(
            "idempotency key already belongs to another operation"
        )
    if action.intent_fingerprint != fingerprint:
        raise CurrentGraphAdvancementIdempotencyConflict(
            "idempotency key was reused with different advancement intent"
        )


def _replay(
    stores: Any,
    command: AdvanceCurrentGraph,
    request: ExecutionRequestRecord,
    run: ActivityRunRecord,
    action: OperationActionRecord,
    fingerprint: str,
) -> CurrentGraphAdvancementResult:
    _require_replay_intent(action, fingerprint)
    if (
        action.session_id != request.identity.session_id
        or action.actor_id != command.fence.worker_id
        or request.identity.workspace_id != command.workspace_id
        or request.identity.plan_id != command.plan_id
        or run.run_id != command.run_id
        or run.plan_id != command.plan_id
        or run.admission.request_id != request.identity.request_id
        or _payload_text(action.payload, "execution_request_id")
        != request.identity.request_id
        or _payload_text(action.payload, "workspace_id") != command.workspace_id
        or _payload_text(action.payload, "plan_id") != command.plan_id
        or _payload_text(action.payload, "run_id") != command.run_id
        or _payload_text(action.payload, "from_authored_graph_id")
        != command.expected_current_graph_id
        or _payload_text(action.payload, "from_realized_projection_id")
        != command.expected_current_realized_projection_id
        or _payload_text(action.payload, "to_authored_graph_id")
        != command.desired_graph_id
        or _payload_text(action.payload, "to_realized_projection_id")
        != command.desired_realized_projection_id
        or _payload_nonnegative_integer(
            action.payload,
            "desired_graph_revision",
        )
        != command.expected_desired_graph_revision
        or _payload_positive_integer(action.payload, "claim_generation")
        != command.fence.generation
    ):
        raise CurrentGraphAdvancementError(
            "advancement action evidence is incongruent"
        )
    _require_graph_ownership(
        stores.graphs,
        command.workspace_id,
        command.expected_current_graph_id,
        command.desired_graph_id,
    )
    _projection(
        stores.realized_graphs,
        projection_id=command.expected_current_realized_projection_id,
        workspace_id=command.workspace_id,
        source_authored_graph_id=command.expected_current_graph_id,
    )
    desired_projection = _projection(
        stores.realized_graphs,
        projection_id=command.desired_realized_projection_id,
        workspace_id=command.workspace_id,
        source_authored_graph_id=command.desired_graph_id,
    )
    if (
        _payload_text(action.payload, "to_realized_projection_digest")
        != desired_projection.projection_digest
    ):
        raise CurrentGraphAdvancementError(
            "advancement action evidence is incongruent"
        )
    event_id = _payload_text(action.payload, "event_id")
    missing_event = False
    malformed_event = False
    try:
        event = stores.execution.get_event(event_id)
    except KeyError:
        missing_event = True
    except ValueError:
        malformed_event = True
    if missing_event:
        raise CurrentGraphAdvancementError(
            "advancement operation evidence is missing its event"
        )
    if malformed_event:
        raise CurrentGraphAdvancementError(
            "advancement operation evidence is malformed"
        )
    return _result(event, action, replayed=True)


def _result(
    event: ActivityEventRecord,
    action: OperationActionRecord,
    *,
    replayed: bool = False,
) -> CurrentGraphAdvancementResult:
    return CurrentGraphAdvancementResult(
        workspace_id=_payload_text(action.payload, "workspace_id"),
        from_authored_graph_id=_payload_text(
            action.payload,
            "from_authored_graph_id",
        ),
        from_realized_projection_id=_payload_text(
            action.payload,
            "from_realized_projection_id",
        ),
        to_authored_graph_id=_payload_text(action.payload, "to_authored_graph_id"),
        to_realized_projection_id=_payload_text(
            action.payload,
            "to_realized_projection_id",
        ),
        to_realized_projection_digest=_payload_text(
            action.payload,
            "to_realized_projection_digest",
        ),
        desired_graph_revision=_payload_nonnegative_integer(
            action.payload,
            "desired_graph_revision",
        ),
        run_id=_payload_text(action.payload, "run_id"),
        plan_id=_payload_text(action.payload, "plan_id"),
        event=event,
        action=action,
        replayed=replayed,
    )


def _fingerprint(command: AdvanceCurrentGraph) -> str:
    value: Mapping[str, object] = {
        "command": LifecycleOperationKind.ADVANCE_CURRENT_GRAPH.value,
        "workspace_id": command.workspace_id,
        "run_id": command.run_id,
        "plan_id": command.plan_id,
        "expected_current_graph_id": command.expected_current_graph_id,
        "expected_current_realized_projection_id": (
            command.expected_current_realized_projection_id
        ),
        "desired_graph_id": command.desired_graph_id,
        "desired_realized_projection_id": command.desired_realized_projection_id,
        "expected_desired_graph_revision": command.expected_desired_graph_revision,
        "worker_id": command.authority.worker_id,
        "claim_generation": command.fence.generation,
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _payload_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise CurrentGraphAdvancementError(f"advancement action payload lacks {key}")
    return value


def _payload_nonnegative_integer(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if type(value) is not int or value < 0:
        raise CurrentGraphAdvancementError(f"advancement action payload lacks {key}")
    return value


def _payload_positive_integer(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise CurrentGraphAdvancementError(
            f"advancement action payload lacks valid {key}"
        )
    return value


def _require_operate_scope(authority: ExecutionWorkerAuthority) -> None:
    if PolicyScope.EXECUTION_OPERATE not in authority.scopes:
        raise CurrentGraphAdvancementDenied("scope execution:operate is missing")


def _required_text(value: object, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOperationCommand(f"{field} must not be empty")


def _require_run_id(value: object) -> None:
    try:
        RunId(value)  # type: ignore[arg-type]
    except ValueError:
        valid = False
    else:
        valid = True
    if not valid:
        raise InvalidOperationCommand("run_id is malformed")
