"""Authenticated exact cleanup inspection and atomic publication; no execution."""
from dataclasses import dataclass
from hashlib import sha256

import rfc8785

from control_plane_kit_core.identity import TrustedCommandContext
from control_plane_kit_core.operations.commands import OperatorCommandKind
from control_plane_kit_core.planning import (
    ActivityId, ActivityImpact, ActivityPlan, CleanupConfigurationInstances, PlannedActivity, RiskLevel,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_operations.configuration_cleanup import (
    ConfigurationCleanupExpectedContext, ConfigurationCleanupSourceSelector,
    ConfigurationCleanupProposalCodec, ConfigurationCleanupInspectionCodec, _text, _scope, _digest, _require,
)
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile, encode_stored_activity_plan
from control_plane_kit_operations.records import (
    ActivityPlanRecord, ActivityPlanStatus, OperationActionRecord, OperationSessionStatus,
)
from control_plane_kit_operations.workflows import IdempotencyKey


class ConfigurationCleanupCommandError(ValueError):
    """Redacted refusal; no partial plan or authority is returned."""


@dataclass(frozen=True)
class ConfigurationCleanupPlanningResult:
    """Exact plan/action receipt; cleanup has no invented graph transition."""
    plan_record: ActivityPlanRecord
    action: OperationActionRecord
    replayed: bool = False

    def __post_init__(self):
        self.plan_record.__post_init__()
        context = self.plan_record.cleanup_proposal.descriptor()["context"]
        _require(self.action.session_id == self.plan_record.session_id
            and self.action.action_type is OperatorCommandKind.REQUEST_ACTIVITY_PLAN
            and self.action.payload == _payload(self.plan_record, context["workspace_id"])
            and type(self.replayed) is bool)


def _selectors(session, workspace, context, selectors):
    _text(session)
    _scope(workspace)
    _require(type(context) is ConfigurationCleanupExpectedContext)
    context.__post_init__()
    _require(type(selectors) is tuple and 1 <= len(selectors) <= 32)
    for value in selectors:
        _require(type(value) is ConfigurationCleanupSourceSelector)
        value.__post_init__()
        _require(value.expected_ref.workspace_id == workspace)
    _require(len({(v.source_identity, v.artifact_id) for v in selectors}) == len(selectors))
    _require(len({v.expected_ref.allocation_id for v in selectors}) == len(selectors))
    _require(len({(v.expected_ref.runtime_id, v.expected_ref.node_id) for v in selectors}) == 1)
    return tuple(sorted(selectors, key=lambda v: v.expected_ref.allocation_id))


@dataclass(frozen=True)
class InspectConfigurationCleanup:
    session_id: str
    workspace_id: str
    expected_context: ConfigurationCleanupExpectedContext
    selectors: tuple[ConfigurationCleanupSourceSelector, ...]

    def __post_init__(self):
        object.__setattr__(self, "selectors", _selectors(self.session_id, self.workspace_id,
            self.expected_context, self.selectors))


@dataclass(frozen=True)
class RequestConfigurationCleanupPlan:
    session_id: str
    workspace_id: str
    idempotency_key: IdempotencyKey
    expected_context: ConfigurationCleanupExpectedContext
    selectors: tuple[ConfigurationCleanupSourceSelector, ...]
    expected_inspection_fingerprint: str

    def __post_init__(self):
        object.__setattr__(self, "selectors", _selectors(self.session_id, self.workspace_id,
            self.expected_context, self.selectors))
        _require(type(self.idempotency_key) is IdempotencyKey)
        self.idempotency_key.__post_init__()
        _digest(self.expected_inspection_fingerprint)


def _authorize(command, context, *, publish):
    admitted = False
    try:
        expected_type = RequestConfigurationCleanupPlan if publish else InspectConfigurationCleanup
        _require(type(command) is expected_type and type(context) is TrustedCommandContext)
        command.__post_init__()
        principal = context.principal
        principal.identity.__post_init__()
        for grant in principal.workspace_grants:
            grant.__post_init__()
        principal.__post_init__()
        context.__post_init__()
        _require(context.workspace_id == command.workspace_id)
        required = {PolicyScope.INSTANCE_WORKSPACE_READ}
        if publish:
            required.add(PolicyScope.PLAN_REQUEST)
        _require(required <= set(context.granted_scopes))
        admitted = True
    except (ValueError, TypeError, AttributeError, KeyError):
        pass
    if not admitted:
        raise ConfigurationCleanupCommandError("configuration cleanup command is not authorized")


def _fingerprint(command, actor):
    value = dict(profile="configuration-cleanup-command.v1", session_id=command.session_id,
        workspace_id=command.workspace_id, actor_id=actor, idempotency_key=command.idempotency_key.value,
        expected_context=command.expected_context.descriptor(),
        selectors=[value.descriptor() for value in command.selectors],
        expected_inspection_fingerprint=command.expected_inspection_fingerprint)
    encoded = None
    try:
        encoded = rfc8785.dumps(value)
    except (ValueError, TypeError, OverflowError):
        pass
    if encoded is None:
        raise ConfigurationCleanupCommandError("configuration cleanup command exceeds canonical capacity")
    return sha256(encoded).hexdigest()


def _proposal_matches_command(proposal, command):
    """Reconstruct only the immutable original review, never current authority."""
    document = proposal.descriptor()
    context = document["context"]
    if (context["workspace_id"] != command.workspace_id or context["session_id"] != command.session_id
            or any(context[name] != value for name, value in command.expected_context.descriptor().items())):
        return False
    selectors, candidates = [], []
    for row in document["candidates"]:
        selectors.append(dict(**row["seed"], expected_ref=row["ref"]))
        summaries = []
        for witness in row["completion_witnesses"]:
            summary = {name: witness[name] for name in (
                "source_identity", "original_event_id", "original_event_ordinal", "request_fingerprint",
                "selection_fingerprint", "direct_event_id", "direct_event_ordinal", "result_kind", "outcome_fingerprint")}
            summary.update(kind="completed", attempt_status=witness["result_kind"],
                selection_count=len(witness["selection_allocations"]), unselected_count=0)
            summaries.append(summary)
        candidates.append(dict(ref=row["ref"], seed=row["seed"], birth=row["birth"], invocations=summaries, blockers=[]))
    inspection = ConfigurationCleanupInspectionCodec().decode(dict(
        profile="configuration-cleanup-inspection.v1", context=context, candidates=candidates))
    return (selectors == [selector.descriptor() for selector in command.selectors]
        and inspection.evidence_digest == command.expected_inspection_fingerprint)


def _plan(proposal):
    from control_plane_kit_core.configuration_instances import ConfigurationInstanceRefCodec
    refs = tuple(ConfigurationInstanceRefCodec().decode(row["ref"]) for row in proposal.descriptor()["candidates"])
    return ActivityPlan((PlannedActivity(ActivityId("cleanup-configuration"), CleanupConfigurationInstances(refs),
        risk=RiskLevel.CRITICAL, impact=ActivityImpact.DESTRUCTIVE),))


def _payload(record, workspace):
    from control_plane_kit_operations.configuration_cleanup import configuration_cleanup_proposal_fingerprint
    return dict(workspace_id=workspace, plan_id=record.plan_id,
        derivation_profile=record.derivation_profile.value,
        cleanup_proposal_fingerprint=configuration_cleanup_proposal_fingerprint(record.cleanup_proposal))


def revalidate_cleanup_proposal(stores, record):
    """Fresh original evidence under the caller's existing lifecycle lock."""
    from control_plane_kit_core.configuration_instances import ConfigurationInstanceRefCodec
    from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
    document = ConfigurationCleanupProposalCodec().encode(record.cleanup_proposal)
    context = document["context"]
    pins = ConfigurationCleanupExpectedContext(**{name: context[name] for name in (
        "base_graph_id", "base_realized_projection_id", "desired_graph_id",
        "desired_realized_projection_id", "desired_graph_revision")})
    selectors = []
    for row in document["candidates"]:
        source = row["seed"]["source_identity"]
        selectors.append(ConfigurationCleanupSourceSelector(EffectAttemptIdentity(
            RunId(source["run_id"]), source["activity_id"], source["attempt"]), row["seed"]["artifact_id"],
            ConfigurationInstanceRefCodec().decode(row["ref"])))
    query = InspectConfigurationCleanup(context["session_id"], context["workspace_id"], pins, tuple(selectors))
    result, proposal = stores.configuration_cleanup.read(query)
    if result.state != "complete" or proposal != record.cleanup_proposal:
        raise ConfigurationCleanupCommandError("configuration cleanup evidence changed")


class ConfigurationCleanupPlanningService:
    def __init__(self, unit_of_work_factory, *, clock, id_factory):
        self._unit_of_work_factory = unit_of_work_factory
        self._clock, self._id_factory = clock, id_factory

    def inspect(self, command, *, context):
        _authorize(command, context, publish=False)
        with self._unit_of_work_factory().configuration_cleanup_snapshot() as snapshot:
            return snapshot.inspect(command)

    def request_plan(self, command, *, context):
        _authorize(command, context, publish=True)
        fingerprint = _fingerprint(command, context.actor_id)
        with self._unit_of_work_factory() as uow:
            history = uow.stores.activity_history
            history.lock_action_idempotency(command.session_id, command.idempotency_key.value)
            existing = history.action_for_idempotency(command.session_id, command.idempotency_key.value)
            if existing is not None:
                valid = False
                try:
                    session = history.get_session(command.session_id)
                    record = history.get_plan(existing.payload["plan_id"])
                    valid = (session.workspace_id == command.workspace_id
                        and record.session_id == command.session_id and existing.intent_fingerprint == fingerprint
                        and existing.actor_id == context.actor_id
                        and existing.action_type is OperatorCommandKind.REQUEST_ACTIVITY_PLAN
                        and existing.payload == _payload(record, command.workspace_id)
                        and _proposal_matches_command(record.cleanup_proposal, command))
                except (ValueError, KeyError, TypeError, AttributeError):
                    pass
                if not valid:
                    raise ConfigurationCleanupCommandError("configuration cleanup replay is inconsistent")
                uow.commit()
                return ConfigurationCleanupPlanningResult(record, existing, replayed=True)
            uow.stores.graphs.lock_receiver_lifecycle(command.workspace_id)
            session = history.get_session_for_update(command.session_id)
            if session.workspace_id != command.workspace_id or session.status is not OperationSessionStatus.OPEN:
                raise ConfigurationCleanupCommandError("configuration cleanup session is unavailable")
            uow.stores.workspaces.get_for_update(command.workspace_id)
            result, proposal = uow.stores.configuration_cleanup.read(command)
            if (result.state != "complete" or proposal is None
                    or result.inspection.evidence_digest != command.expected_inspection_fingerprint):
                raise ConfigurationCleanupCommandError("configuration cleanup evidence is unavailable")
            plan = _plan(proposal)
            profile = PlanDerivationProfile.CONFIGURATION_CLEANUP_V1
            encode_stored_activity_plan(plan, profile=profile, cleanup_proposal=proposal)
            timestamp = self._clock()
            record = ActivityPlanRecord(plan_id=self._id_factory(), session_id=command.session_id,
                status=ActivityPlanStatus.PLANNED, created_at=timestamp, plan=plan,
                derivation_profile=profile, cleanup_proposal=proposal, **command.expected_context.descriptor())
            history.add_plan(record)
            action = OperationActionRecord(action_id=self._id_factory(), session_id=command.session_id,
                ordinal=history.next_action_ordinal(command.session_id),
                action_type=OperatorCommandKind.REQUEST_ACTIVITY_PLAN, actor_id=context.actor_id,
                payload=_payload(record, command.workspace_id), created_at=timestamp,
                idempotency_key=command.idempotency_key.value, intent_fingerprint=fingerprint)
            history.add_action(action)
            uow.commit()
            return ConfigurationCleanupPlanningResult(record, action)
