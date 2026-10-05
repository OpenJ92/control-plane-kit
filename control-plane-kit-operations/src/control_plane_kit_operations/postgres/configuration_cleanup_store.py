"""Bounded composition of retained source, claims, outcome and current owners."""
from dataclasses import asdict
from contextlib import contextmanager
import rfc8785

from control_plane_kit_core.configuration_instances import ConfigurationInstanceRefCodec, ConfigurationInstanceSelection
from control_plane_kit_core.configuration_invocation import (
    ConfigurationInvocationCorrelation, configuration_invocation_completion_for_result,
    configuration_invocation_selection_fingerprint,
)
from control_plane_kit_core.operations import EffectAttemptStatus
from control_plane_kit_core.planning import CleanupConfigurationInstances
from control_plane_kit_core.planning.codec import activity_operation_descriptor
from control_plane_kit_core.runtime_effects import (
    ConfigurationCleanupCapacityError, require_configuration_cleanup_capacity,
)
from control_plane_kit_operations.configuration_cleanup import (
    ConfigurationCleanupInspectionCodec, ConfigurationCleanupInspectionResult,
    ConfigurationCleanupProposalCodec, MAX_CANONICAL_REVISION,
    MAX_CLEANUP_DOCUMENT_BYTES,
    ConfigurationCleanupContractError,
)
from control_plane_kit_operations.records import OperationSessionStatus
from .configuration_evidence import _Capacity, _Unavailable, _composed_read
from .configuration_source import read_original_selection
from .configuration_preparation_store import _decode_ref
from .effect_attempt_store import _COLUMN_NAMES, _record_from_events
from .effect_outcome_store import _configuration_event
from .graph_store import _read_workspace_initialization


def _identity(identity):
    return dict(run_id=identity.run_id.value, activity_id=identity.activity_id, attempt=identity.attempt)


def _locator(identity, artifact):
    return dict(source_identity=_identity(identity), artifact_id=artifact)


def _current_attempt(identity, read):
    key = ("cleanup-current-attempt", identity)
    if key not in read.sources:
        numeric = {"attempt", "fence_generation", "prior_attempt", "original_event_ordinal", "latest_event_ordinal"}
        rows = read.bounded_rows("cpk_effect_attempts",
            tuple((name, "int" if name in numeric else "text", 2048) for name in _COLUMN_NAMES),
            "run_id=%s AND activity_id=%s AND attempt=%s",
            (identity.run_id.value, identity.activity_id, identity.attempt))
        if len(rows) != 1:
            raise _Unavailable
        row = rows[0]
        events = tuple(_configuration_event(read, event_id) for event_id in (row[15], row[18]))
        read.sources[key] = _record_from_events(row, *events)
    return read.sources[key]


def _invocation(stores, source, selected, read):
    key = ("cleanup-invocation", source.identity)
    if key in read.sources:
        return read.sources[key]
    projections = read_original_selection(stores.connection, source.identity, source.ref, read=read)
    selection = ConfigurationInstanceSelection(tuple(value.ref for value in projections))
    fingerprint = configuration_invocation_selection_fingerprint(selection)
    current = _current_attempt(source.identity, read)
    if (current.state.identity != source.identity or current.state.request_fingerprint != source.request_fingerprint
            or current.original_start_event.event_id != source.original_event_id
            or current.original_start_event.ordinal != source.original_event_ordinal):
        raise _Unavailable
    summary = dict(source_identity=_identity(source.identity), original_event_id=source.original_event_id,
        original_event_ordinal=source.original_event_ordinal, request_fingerprint=source.request_fingerprint,
        selection_fingerprint=fingerprint, selection_count=len(selection.instances),
        unselected_count=sum(ref not in selected for ref in selection.instances))
    witness = None
    if current.state.status is EffectAttemptStatus.STARTED:
        # Absence is proven against the complete exact-attempt outcome index.
        rows = read.query("SELECT 1 FROM cpk_effect_attempt_outcomes WHERE run_id=%s AND activity_id=%s AND attempt=%s LIMIT 1",
            (source.identity.run_id.value, source.identity.activity_id, source.identity.attempt),
            records=1, octets=1, cells=1)
        if rows:
            raise _Unavailable
        summary["kind"] = "active"
    else:
        outcome, terminal = stores.effect_outcomes._configuration_terminal(source, read)
        if current != terminal:
            raise _Unavailable
        correlation = ConfigurationInvocationCorrelation(source.request_fingerprint, source.original_event_id,
            source.kind, source.source, source.operation, selection)
        completion = configuration_invocation_completion_for_result(correlation, outcome.result)
        summary.update(kind="terminal-unprofiled" if completion is None else "completed",
            result_kind=outcome.result.kind.value, attempt_status=terminal.state.status.value,
            direct_event_id=terminal.latest_transition_event.event_id,
            direct_event_ordinal=terminal.latest_transition_event.ordinal,
            outcome_fingerprint=terminal.state.outcome_fingerprint)
        if completion is not None:
            witness = {name: summary[name] for name in (
                "source_identity", "original_event_id", "original_event_ordinal", "request_fingerprint",
                "selection_fingerprint", "direct_event_id", "direct_event_ordinal", "result_kind", "outcome_fingerprint")}
            witness.update(effect_kind=source.kind.value, operation=activity_operation_descriptor(source.operation),
                selection_allocations=[ref.allocation_id for ref in selection.instances])
    read.sources[key] = summary, witness
    return summary, witness


def _inspect(stores, command, read):
    session = stores.activity_history.get_session(command.session_id)
    if session.workspace_id != command.workspace_id or session.status is not OperationSessionStatus.OPEN:
        raise _Unavailable
    workspace = stores.workspaces.get(command.workspace_id)
    if workspace.desired_graph_revision > MAX_CANONICAL_REVISION:
        raise _Capacity
    pins = dict(base_graph_id=workspace.current_graph_id,
        base_realized_projection_id=workspace.current_realized_projection_id,
        desired_graph_id=workspace.desired_graph_id,
        desired_realized_projection_id=workspace.desired_realized_projection_id,
        desired_graph_revision=workspace.desired_graph_revision)
    if pins != command.expected_context.descriptor():
        raise _Unavailable
    try:
        require_configuration_cleanup_capacity(CleanupConfigurationInstances(
            tuple(selector.expected_ref for selector in command.selectors)))
    except ConfigurationCleanupCapacityError:
        raise _Capacity from None
    receipt = stores.configuration_acceptance._current_manifest(workspace, read)
    if receipt[0]["pinned_revision"] is None:
        occurrence = dict(kind="workspace-initialization",
            **asdict(_read_workspace_initialization(stores.connection, command.workspace_id)))
    else:
        if receipt[0]["pinned_revision"] > MAX_CANONICAL_REVISION:
            raise _Capacity
        occurrence = dict(kind="configuration-acceptance", **receipt[0])
    context = dict(workspace_id=command.workspace_id, session_id=command.session_id,
        current_occurrence=occurrence, **pins)
    selected = {value.expected_ref for value in command.selectors}
    current_refs = set()
    for row in receipt[3]:
        original = stores.configuration_acceptance._ref(read, row[3:7])
        _, ref, _, _ = _decode_ref(original)
        if ref in selected:
            stores.configuration_acceptance._prove_use(read, row, *receipt[4:])
            current_refs.add(ref)
    candidates, proposals, count = [], [], 0
    for selector in sorted(command.selectors, key=lambda item: item.expected_ref.allocation_id):
        ref = selector.expected_ref
        allocation = stores.configuration_preparation._allocation_evidence(ref, read)
        if allocation.state == "capacity":
            raise _Capacity
        if allocation.state != "complete":
            raise _Unavailable
        count += len(allocation.claims)
        if count > 256:
            raise _Capacity
        if not any(value.identity == selector.source_identity and value.ref.artifact_id == selector.artifact_id
                   for value in allocation.claims):
            raise _Unavailable
        summaries, witnesses = [], []
        for claim in allocation.claims:
            summary, witness = _invocation(stores, claim.source, selected, read)
            summaries.append(summary)
            if witness is not None:
                witnesses.append(witness)
        blockers = []
        if ref in current_refs:
            blockers.append("current-selected-use")
        if any(value["unselected_count"] for value in summaries):
            blockers.append("incomplete-invocation-selection")
        if any(value["kind"] != "completed" for value in summaries):
            blockers.append("unresolved-invocation")
        common = dict(ref=ConfigurationInstanceRefCodec().encode(ref),
            seed=_locator(selector.source_identity, selector.artifact_id),
            birth=_locator(allocation.birth.identity, allocation.birth.ref.artifact_id))
        candidates.append(dict(**common, invocations=summaries, blockers=blockers))
        identities = [_identity(value.identity) for value in allocation.claims]
        proposals.append(dict(**common, protecting_uses=identities,
            completion_witnesses=witnesses, proposed_closures=identities))
    document = dict(profile="configuration-cleanup-inspection.v1", context=context, candidates=candidates)
    if len(rfc8785.dumps(document)) > MAX_CLEANUP_DOCUMENT_BYTES:
        raise _Capacity
    inspection = ConfigurationCleanupInspectionCodec().decode(document)
    proposal = None
    if not any(row["blockers"] for row in candidates):
        document = dict(profile="configuration-cleanup-proposal.v1", context=context, candidates=proposals)
        if len(rfc8785.dumps(document)) > MAX_CLEANUP_DOCUMENT_BYTES:
            raise _Capacity
        proposal = ConfigurationCleanupProposalCodec().decode(document)
    return ConfigurationCleanupInspectionResult("complete", inspection), proposal


def inspect_cleanup(stores, command):
    """Same composition for a read snapshot and a serialized command UoW."""
    state = "unavailable"
    try:
        with _composed_read(stores.connection) as read:
            return _inspect(stores, command, read)
    except _Capacity:
        state = "capacity"
    except (_Unavailable, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        pass
    return ConfigurationCleanupInspectionResult(state, reason="evidence-" + state), None


class ConfigurationCleanupSnapshot:
    """Narrow read facade; no mutation or promotion to command authority."""
    def __init__(self, stores):
        self._stores = stores

    def inspect(self, command):
        return inspect_cleanup(self._stores, command)[0]


class ConfigurationCleanupStore:
    def __init__(self, stores):
        self._stores = stores

    def read(self, command):
        return inspect_cleanup(self._stores, command)

    @contextmanager
    def evidence(self):
        with _composed_read(self._stores.connection):
            yield

    @contextmanager
    def approval_evidence(self, command):
        """Bound A and exact routing; retain legacy bodies only when proven."""
        failed = False
        try:
            with _composed_read(self._stores.connection) as read:
                self._stores.activity_history.lock_action_idempotency(command.session_id, command.idempotency_key.value)
                if _bounded_approval_route(read, command):
                    yield
                    return
        except (ValueError, KeyError, TypeError):
            failed = True
        if failed:
            raise ConfigurationCleanupContractError("configuration cleanup approval evidence is unavailable")
        # The A lock belongs to the transaction; it survives releasing only the
        # optional accounting context. Do not reacquire A or change legacy reads.
        yield

    def preflight_tail(self, *, publication=False):
        from .configuration_evidence import _active_read
        from control_plane_kit_operations.configuration_preparation import (
            ConfigurationEvidenceFootprint, ConfigurationCapacityDecision, configuration_evidence_capacity,
        )
        read = _active_read(self._stores.connection)
        if read is None:
            return
        # Publication includes add_plan's bounded owner recheck (64KiB metadata,
        # bounded text), ordinal locks and returned insert scalars. Approval has
        # only ordinal reads and request/decision + action returned scalars.
        tail = (ConfigurationEvidenceFootprint(12, 128 * 1024, 128, 12) if publication else
            ConfigurationEvidenceFootprint(8, 4096, 16, 8))
        if configuration_evidence_capacity(read.used.plus(tail)) is not ConfigurationCapacityDecision.WITHIN_LIMITS:
            raise _Capacity


def _bounded_approval_route(read, command):
    """Only fixed booleans cross this exact-key, nonauthorizing routing read."""
    # An unfamiliar, partial, contradictory or absent row is bounded. A subject
    # cannot downgrade a plan marker, nor can a submitted plan hide the original
    # idempotency target. Missing targets proceed to existing missing-owner APIs.
    plan_legacy = """(CASE WHEN jsonb_typeof(p.payload)='object' THEN (
        ((p.payload->>'schema'='control-plane-kit.activity-plan'
          AND p.payload->'version'='1'::jsonb
          AND jsonb_typeof(p.payload->'activities')='array'
          AND p.payload - ARRAY['schema','version','activities']::text[]='{}'::jsonb)
         OR (p.payload->>'schema'='control-plane-kit.operations.activity-plan-record'
             AND p.payload->'version'='1'::jsonb
             AND p.payload->>'derivation_profile' IN ('structural-v1','management-graph-pair-v1')
             AND p.payload - ARRAY['schema','version','derivation_profile','plan']::text[]='{}'::jsonb
             AND CASE WHEN jsonb_typeof(p.payload->'plan')='object' THEN (
             p.payload->'plan'->>'schema'='control-plane-kit.activity-plan'
             AND p.payload->'plan'->'version'='1'::jsonb
             AND jsonb_typeof(p.payload->'plan'->'activities')='array'
             AND (p.payload->'plan') - ARRAY['schema','version','activities']::text[]='{}'::jsonb
             ) ELSE false END))
        AND NOT (p.payload ? 'cleanup_proposal' OR p.payload ? 'cleanup_proposal_fingerprint')
        AND NOT jsonb_path_exists(p.payload, '$.**.operation ? (@.kind == "cleanup-configuration-instances")')
    ) ELSE false END)"""
    subject_legacy = """(a.subject_payload=jsonb_build_object('kind','activity-plan','plan_id',a.plan_id)
        AND a.subject_kind='activity-plan' AND a.rotation_id IS NULL)"""
    if hasattr(command, "plan_id"):
        rows = read.query(f"SELECT {plan_legacy} FROM cpk_activity_plans p WHERE p.plan_id=%s",
            (command.plan_id,), records=1, octets=1, cells=1)
        requested_legacy = rows == [(True,)]
        retained = read.query(f"SELECT ({plan_legacy} AND {subject_legacy}) "
            "FROM cpk_approval_requests a LEFT JOIN cpk_activity_plans p ON p.plan_id=a.plan_id "
            "WHERE a.session_id=%s AND a.idempotency_key=%s",
            (command.session_id, command.idempotency_key.value), records=1, octets=1, cells=1)
        return not (requested_legacy and (not retained or retained == [(True,)]))
    rows = read.query(f"SELECT CASE WHEN a.subject_kind='gateway-key-rotation' "
        "AND a.plan_id IS NULL AND a.rotation_id IS NOT NULL "
        "AND a.subject_payload->>'kind'='gateway-key-rotation' "
        "AND jsonb_typeof(a.subject_payload)='object' AND octet_length(a.subject_payload::text)<=16384 "
        "AND NOT (a.subject_payload ? 'proposal_fingerprint' OR a.subject_payload ? 'profile') "
        f"THEN true ELSE ({plan_legacy} AND {subject_legacy}) END "
        "FROM cpk_approval_requests a LEFT JOIN cpk_activity_plans p ON p.plan_id=a.plan_id "
        "WHERE a.request_id=%s", (command.request_id,), records=1, octets=1, cells=1)
    return rows != [(True,)]


def validate_cleanup_rows(connection):
    """Current-schema verification of stored meaning; never fresh approval."""
    from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
    from control_plane_kit_core.policies import ApprovalPolicy
    from control_plane_kit_operations.configuration_cleanup import configuration_cleanup_proposal_fingerprint
    from .activity_history import PostgresActivityHistoryStore
    history = PostgresActivityHistoryStore(connection)
    last = ""
    while True:
        with _composed_read(connection) as read:
            rows = read.bounded_rows("cpk_activity_plans", (("plan_id", "text", 2048),),
                "plan_id>%s AND (payload->>'derivation_profile'='configuration-cleanup-v1' "
                "OR (payload->>'schema'='control-plane-kit.operations.activity-plan-record' "
                "AND payload->'version' IS DISTINCT FROM '1'::jsonb))",
                (last,), maximum=4, order="plan_id")
            for (plan_id,) in rows:
                plan = history.get_plan(plan_id)
                context = plan.cleanup_proposal.descriptor()["context"]
                if history.get_session(plan.session_id).workspace_id != context["workspace_id"]:
                    raise _Unavailable
        if len(rows) < 4:
            break
        last = rows[-1][0]
    last = ""
    while True:
        with _composed_read(connection) as read:
            rows = read.bounded_rows("cpk_approval_requests", (("request_id", "text", 2048),),
                "request_id>%s AND subject_kind='activity-plan'", (last,), maximum=4, order="request_id")
            for (request_id,) in rows:
                request = history.get_approval_request(request_id)
                plan = history.get_plan(request.subject.plan_id)
                fingerprint = (None if plan.cleanup_proposal is None else
                    configuration_cleanup_proposal_fingerprint(plan.cleanup_proposal))
                if (request.session_id != plan.session_id
                        or request.subject != ActivityPlanApprovalSubject(plan.plan_id, proposal_fingerprint=fingerprint)):
                    raise _Unavailable
                if plan.cleanup_proposal is not None:
                    requirement = ApprovalPolicy().requirement_for(plan.plan)
                    if (request.required_scope, request.max_risk, request.destructive) != (
                            requirement.required_scope, requirement.max_risk, requirement.destructive):
                        raise _Unavailable
        if len(rows) < 4:
            break
        last = rows[-1][0]
