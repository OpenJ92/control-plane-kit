"""Bounded retained cleanup proof. No reserve, release or retirement writer."""
from hashlib import sha256

from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.configuration_instances import ConfigurationInstanceRefCodec
from control_plane_kit_core.operations import EffectAttemptIdentity, EffectAttemptStatus, RunId
from control_plane_kit_core.planning import CleanupConfigurationInstances
from control_plane_kit_core.planning.codec import activity_operation_descriptor
from control_plane_kit_core.policies import ApprovalPolicy
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.runtime_effect_observation import RuntimeEffectIntent, RuntimeEffectIntentSource, runtime_effect_request_for_intent
from control_plane_kit_core.runtime_effects import RuntimeEffectKind, configuration_cleanup_outcomes
from control_plane_kit_core.types import RuntimeKind
from control_plane_kit_operations.configuration_cleanup import configuration_cleanup_proposal_fingerprint
from control_plane_kit_operations.configuration_cleanup_ownership import ConfigurationCleanupReservationRecord
from control_plane_kit_operations.configuration_preparation import _identity
from control_plane_kit_operations.effect_outcome_evidence import ExecutionEffectOutcome, ObservedEffectOutcome
from control_plane_kit_operations.records import OperationsRecordError, ApprovalDecisionKind
from .configuration_evidence import _joined_read, _Capacity, _Unavailable
from .configuration_preparation_store import _SELECT, _decode, _paired_disposition
from .configuration_source import read_original_selection


_C = ("cleanup_run_id", "cleanup_activity_id", "cleanup_attempt")
_O = ("run_id", "activity_id", "attempt")
_A = ("workspace_id", "allocation_id")
_HEADER = _C + ("workspace_id", "request_id", "request_fingerprint", "original_event_id", "plan_id",
    "approval_request_id", "approval_decision_id", "proposal_fingerprint", "runtime_id", "runtime_kind",
    "authority_ref", "registration_id", "candidate_count", "invocation_count", "claim_count")
_MEMBERS = _C + _A + ("birth_run_id", "birth_activity_id", "birth_attempt", "birth_artifact_id", "full_ref_digest")
_INVOCATIONS = _O + _C + ("workspace_id", "request_fingerprint", "selection_fingerprint", "outcome_fingerprint")
_CLAIMS = _O + ("artifact_id",) + _C + _A
_OUTCOMES = _C + _A + ("request_fingerprint", "outcome_fingerprint", "status", "reason")
_TABLE = "cpk_configuration_cleanup_reservations"
_WHERE = "cleanup_run_id=%s AND cleanup_activity_id=%s AND cleanup_attempt=%s"
_ERROR = "configuration cleanup reservation is unavailable"


def _key(identity):
    _identity(identity)
    return identity.run_id.value, identity.activity_id, identity.attempt


def _columns(names):
    def cap(name):
        if name.endswith("attempt") or name.endswith("_count"):
            return "int", 10
        if name in (*_C[:2], *_O[:2], "birth_run_id", "birth_activity_id"):
            return "text", 200
        if name.endswith("fingerprint") or name == "full_ref_digest":
            return "text", 64
        if name in (*_A, "runtime_id", "authority_ref"):
            return "text", 128
        if name in ("artifact_id", "birth_artifact_id", "runtime_kind", "status", "reason"):
            return "text", 63
        return "text", 2048
    return tuple((name, *cap(name)) for name in names)


def _ref(read, key):
    rows = read.query(_SELECT + " WHERE (r.run_id,r.activity_id,r.attempt,r.artifact_id)=(%s,%s,%s,%s)",
        key, records=1, octets=32768, cells=19, identities=2)
    if len(rows) != 1:
        raise _Unavailable
    return _decode(rows[0], read)


class ConfigurationCleanupOwnershipStore:
    def __init__(self, stores):
        self._stores = stores
        self._connection = stores.connection

    def get(self, identity):
        try:
            with _joined_read(self._connection) as read:
                return self._get(identity, read)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            raise OperationsRecordError(_ERROR) from None

    def _get(self, identity, read):
        cleanup = _key(identity)
        from .configuration_cleanup_phase_read_bounds import _phase_context, _phase_require
        _phase_context(self._connection, read=read)
        headers = read.bounded_rows(_TABLE, _columns(_HEADER), _WHERE, cleanup)
        if not headers:
            return None
        h = dict(zip(_HEADER, headers[0], strict=True))
        _phase_require(self._connection, "retained", cleanup, "plan", (h["plan_id"],))
        if (tuple(headers[0][:3]) != cleanup or any(value is None for value in headers[0])
                or not 1 <= h["candidate_count"] <= 32 or not 1 <= h["invocation_count"] <= h["claim_count"] <= 256
                or not h["candidate_count"] <= h["claim_count"]):
            raise _Unavailable
        children = []
        for table, names, count, order in (
            ("cpk_configuration_cleanup_members", _MEMBERS, h["candidate_count"], "workspace_id,allocation_id"),
            ("cpk_configuration_invocation_closures", _INVOCATIONS, h["invocation_count"], "run_id,activity_id,attempt"),
            ("cpk_configuration_claim_closures", _CLAIMS, h["claim_count"], "run_id,activity_id,attempt,artifact_id"),
            ("cpk_configuration_cleanup_member_outcomes", _OUTCOMES, h["candidate_count"], "workspace_id,allocation_id")):
            rows = read.bounded_rows(table, _columns(names), _WHERE, cleanup,
                maximum=count, point=False, order=order)
            if table != "cpk_configuration_cleanup_member_outcomes" and len(rows) != count:
                raise _Unavailable
            children.append(rows)
        members, invocations, closures, member_outcomes = children
        original, attempt, plan = self._original(identity, h, read)
        roots = tuple(_ref(read, row[5:9]) for row in members)
        for row, root in zip(members, roots, strict=True):
            if (row[:5] != (*cleanup, root.ref.workspace_id, root.ref.allocation_id)
                    or root.identity != root.birth_identity or root.ref.artifact_id != root.birth_artifact_id
                    or root.ref.runtime_id != h["runtime_id"]
                    or sha256(ConfigurationInstanceRefCodec().encode_canonical_bytes(root.ref)).hexdigest() != row[9]):
                raise _Unavailable
        claims = tuple(_ref(read, row[:4]) for row in closures)
        by_allocation = {root.ref.allocation_id: root for root in roots}
        for row, claim in zip(closures, claims, strict=True):
            root = by_allocation.get(claim.ref.allocation_id)
            if (row[4:] != (*cleanup, h["workspace_id"], claim.ref.allocation_id) or root is None
                    or claim.ref != root.ref or claim.birth_identity != root.identity
                    or claim.birth_artifact_id != root.ref.artifact_id):
                raise _Unavailable
            _paired_disposition(read, row[:4], claim.ref, expected=cleanup)
        completions = []
        for row in invocations:
            ordinary = EffectAttemptIdentity(RunId(row[0]), row[1], row[2])
            _phase_require(self._connection, "retained", cleanup, "invocation-refs", _key(ordinary))
            # Each distinct O occurs once in this bounded proof phase.
            completion = self._stores.configuration_completions._get(ordinary, read)
            if (completion is None or row != (*_key(ordinary), *cleanup, completion.workspace_id,
                    completion.request_fingerprint, completion.selection_fingerprint, completion.outcome_fingerprint)
                    or completion.workspace_id != h["workspace_id"]):
                raise _Unavailable
            selected = tuple(claim for claim in claims if claim.identity == ordinary)
            if not selected:
                raise _Unavailable
            source = read_original_selection(self._connection, ordinary, selected[0].ref, read=read)
            if tuple(claim.ref for claim in selected) != tuple(value.ref for value in source):
                raise _Unavailable
            completions.append(completion)
        self._proposal(plan, h, roots, claims, completions, read)
        outcome, outcomes = self._outcome(original, attempt, h, member_outcomes, read)
        return ConfigurationCleanupReservationRecord(identity=identity, workspace_id=h["workspace_id"],
            request_id=h["request_id"], request_fingerprint=h["request_fingerprint"], original_event_id=h["original_event_id"],
            plan_id=h["plan_id"], approval_request_id=h["approval_request_id"], approval_decision_id=h["approval_decision_id"],
            proposal_fingerprint=h["proposal_fingerprint"], runtime_id=h["runtime_id"], runtime_kind=RuntimeKind(h["runtime_kind"]),
            authority_ref=RuntimeAuthorityReference(h["authority_ref"]), registration_id=h["registration_id"],
            members=roots, claims=claims, completions=tuple(completions), status=attempt.state.status,
            outcome_fingerprint=None if outcome is None else outcome.outcome_fingerprint,
            outcome_profile=None if outcome is None else outcome.profile, outcomes=outcomes)

    def _original(self, identity, h, read):
        stores = self._stores
        original = stores.effect_attempt_intents.get(identity)
        attempt = stores.effect_attempts.get(identity)
        request = stores.execution.get_request(h["request_id"])
        run = stores.execution.get_run(identity.run_id.value)
        history = stores.activity_history
        plan = history.get_plan(h["plan_id"])
        approval = history.get_approval_request(h["approval_request_id"])
        decision = history.approval_decision_for_request(h["approval_request_id"])
        requirement = ApprovalPolicy().requirement_for(plan.plan)
        if (plan.cleanup_proposal is None or configuration_cleanup_proposal_fingerprint(plan.cleanup_proposal) != h["proposal_fingerprint"]
                or (request.identity.workspace_id, request.identity.plan_id, request.identity.session_id,
                    request.approval_request_id, request.approval_decision_id)
                != (h["workspace_id"], plan.plan_id, plan.session_id, approval.request_id, h["approval_decision_id"])
                or run.plan_id != plan.plan_id or run.admission.request_id != request.identity.request_id
                or history.get_session(plan.session_id).workspace_id != h["workspace_id"]
                or approval.session_id != plan.session_id
                or approval.subject != ActivityPlanApprovalSubject(plan.plan_id, proposal_fingerprint=h["proposal_fingerprint"])
                or (approval.required_scope, approval.max_risk, approval.destructive)
                    != (requirement.required_scope, requirement.max_risk, requirement.destructive)
                or decision is None or decision.decision_id != h["approval_decision_id"]
                or decision.decision is not ApprovalDecisionKind.APPROVED or decision.scope != approval.required_scope):
            raise _Unavailable
        kind, authority = RuntimeKind(h["runtime_kind"]), RuntimeAuthorityReference(h["authority_ref"])
        for side in ("base", "desired"):
            _, _, graph = stores.configuration_acceptance._projection(getattr(plan, side + "_graph_id"),
                getattr(plan, side + "_realized_projection_id"), h["workspace_id"])
            runtime = graph.runtimes.get(h["runtime_id"])
            if runtime is None or runtime.kind is not kind or runtime.authority_ref != authority:
                raise _Unavailable
        registration = (h["registration_id"], h["workspace_id"], h["authority_ref"], h["runtime_kind"])
        if read.query("SELECT 1 FROM cpk_runtime_authorities WHERE "
                "(registration_id,workspace_id,authority_ref,runtime_kind)=(%s,%s,%s,%s)",
                registration, records=1, octets=1, cells=1) != [(1,)]:
            raise _Unavailable
        activity = plan.plan.activity(original.intent.activity_id)
        if type(activity.operation) is not CleanupConfigurationInstances:
            raise _Unavailable
        expected = RuntimeEffectIntent(RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1, kind,
            RuntimeEffectIntentSource(h["workspace_id"], h["request_id"], identity.run_id, plan.plan_id,
                plan.base_graph_id, plan.desired_graph_id), activity.activity_id, activity.operation, authority, (), ())
        if (original.intent != expected or original.request_fingerprint != h["request_fingerprint"]
                or original.original_start_event.event_id != h["original_event_id"]
                or attempt.original_start_event != original.original_start_event
                or attempt.state.request_fingerprint != original.request_fingerprint):
            raise _Unavailable
        return original, attempt, plan

    def _proposal(self, plan, h, roots, claims, completions, read):
        proposal = plan.cleanup_proposal.descriptor()
        if (proposal["context"]["workspace_id"] != h["workspace_id"]
                or tuple(ConfigurationInstanceRefCodec().decode(row["ref"]) for row in proposal["candidates"])
                != tuple(root.ref for root in roots)):
            raise _Unavailable
        completion_by_identity = {value.identity: value for value in completions}
        def identity(value):
            return dict(run_id=value.run_id.value, activity_id=value.activity_id, attempt=value.attempt)
        for candidate, root in zip(proposal["candidates"], roots, strict=True):
            uses = tuple(claim for claim in claims if claim.ref == root.ref)
            identities = [identity(claim.identity) for claim in uses]
            locators = [dict(source_identity=identity(claim.identity), artifact_id=claim.ref.artifact_id) for claim in uses]
            if (candidate["birth"] != dict(source_identity=identity(root.identity), artifact_id=root.ref.artifact_id)
                    or candidate["seed"] not in locators or candidate["protecting_uses"] != identities
                    or candidate["proposed_closures"] != identities):
                raise _Unavailable
            witnesses = []
            for claim in uses:
                completion = completion_by_identity[claim.identity]
                outcome, _ = self._stores.effect_outcomes._configuration_terminal(claim.source, read)
                selected = read_original_selection(self._connection, claim.identity, claim.ref, read=read)
                witnesses.append(dict(source_identity=identity(claim.identity), effect_kind=claim.source.kind.value,
                    operation=activity_operation_descriptor(claim.source.operation), original_event_id=completion.original_event_id,
                    original_event_ordinal=completion.original_event_ordinal, request_fingerprint=completion.request_fingerprint,
                    selection_fingerprint=completion.selection_fingerprint,
                    selection_allocations=[value.ref.allocation_id for value in selected],
                    direct_event_id=completion.direct_event_id, direct_event_ordinal=completion.direct_event_ordinal,
                    result_kind=outcome.result.kind.value, outcome_fingerprint=completion.outcome_fingerprint))
            if candidate["completion_witnesses"] != witnesses:
                raise _Unavailable

    def _outcome(self, original, attempt, h, members, read):
        rows = read.bounded_rows("cpk_effect_attempt_outcomes", (("direct_event_id", "text", 2048),),
            "run_id=%s AND activity_id=%s AND attempt=%s", _key(original.identity))
        if attempt.state.status is EffectAttemptStatus.STARTED:
            if rows or members:
                raise _Unavailable
            return None, None
        if len(rows) != 1:
            raise _Unavailable
        retained = self._stores.effect_outcomes.get(original.identity, rows[0][0])
        if (retained.workspace_id != h["workspace_id"] or retained.attempt != attempt
                or retained.outcome.request_fingerprint != original.request_fingerprint):
            raise _Unavailable
        outcome = retained.outcome
        if type(outcome) is ExecutionEffectOutcome:
            request = runtime_effect_request_for_intent(original.intent, effect_id=original.original_start_event.event_id)
            outcomes = configuration_cleanup_outcomes(request, outcome.result)
            expected = tuple((*_key(original.identity), value.ref.workspace_id, value.ref.allocation_id,
                original.request_fingerprint, outcome.outcome_fingerprint, value.status.value,
                None if value.reason is None else value.reason.value) for value in outcomes.outcomes)
            if members != expected:
                raise _Unavailable
            return outcome, outcomes
        if type(outcome) is not ObservedEffectOutcome or members:
            raise _Unavailable
        return outcome, None


def validate_cleanup_ownership_rows(connection):
    """Exhaustive retained owner traversal, without repairs or fresh authority."""
    from .stores import PostgresStoreBundle
    owner = PostgresStoreBundle(connection).configuration_cleanup_ownership
    cursor = ("", "", 0)
    while True:
        with _joined_read(connection) as read:
            keys = read.bounded_rows(_TABLE, _columns(_C),
                "(cleanup_run_id,cleanup_activity_id,cleanup_attempt)>(%s,%s,%s)", cursor,
                maximum=8, order="cleanup_run_id,cleanup_activity_id,cleanup_attempt")
        if not keys:
            break
        for run, activity, attempt in keys:
            owner.get(EffectAttemptIdentity(RunId(run), activity, attempt))
        cursor = keys[-1]
    # Validated FKs enforce this in committed data; also detect deferred/orphan
    # rows in the caller's current transaction without trusting header counts.
    for table in ("cpk_configuration_cleanup_members", "cpk_configuration_invocation_closures",
            "cpk_configuration_claim_closures", "cpk_configuration_cleanup_member_outcomes"):
        with _joined_read(connection) as read:
            if read.query("SELECT 1 FROM " + table + " c WHERE NOT EXISTS (SELECT 1 FROM " + _TABLE + " h WHERE "
                    "(h.cleanup_run_id,h.cleanup_activity_id,h.cleanup_attempt)="
                    "(c.cleanup_run_id,c.cleanup_activity_id,c.cleanup_attempt)) LIMIT 1",
                    (), records=1, octets=1, cells=1):
                raise OperationsRecordError(_ERROR)
