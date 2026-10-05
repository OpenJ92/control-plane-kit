"""Complete cleanup ownership proof and caller-transactional compound writes."""
from hashlib import sha256
from contextlib import contextmanager, ExitStack

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


def _expected_rows(record):
    cleanup = _key(record.identity)
    header = (*cleanup, record.workspace_id, record.request_id, record.request_fingerprint,
        record.original_event_id, record.plan_id, record.approval_request_id, record.approval_decision_id,
        record.proposal_fingerprint, record.runtime_id, record.runtime_kind.value,
        record.authority_ref.reference_id, record.registration_id,
        len(record.members), len(record.completions), len(record.claims))
    members = tuple((*cleanup, value.ref.workspace_id, value.ref.allocation_id, *_key(value.identity),
        value.ref.artifact_id, sha256(ConfigurationInstanceRefCodec().encode_canonical_bytes(value.ref)).hexdigest())
        for value in record.members)
    invocations = tuple((*_key(value.identity), *cleanup, value.workspace_id, value.request_fingerprint,
        value.selection_fingerprint, value.outcome_fingerprint) for value in record.completions)
    claims = tuple((*_key(value.identity), value.ref.artifact_id, *cleanup,
        value.ref.workspace_id, value.ref.allocation_id) for value in record.claims)
    return header, members, invocations, claims, ()


def _prepared_owner(prepared, store):
    from control_plane_kit_operations._configuration_cleanup_ownership import (
        _CleanupPreparationOwner, _PreparedCleanupStart, _PreparedCleanupFold,
    )
    if (type(prepared) not in (_PreparedCleanupStart, _PreparedCleanupFold)
            or type(prepared.owner) is not _CleanupPreparationOwner or prepared.owner.store is not store):
        raise OperationsRecordError(_ERROR)
    return prepared.owner


class ConfigurationCleanupOwnershipStore:
    def __init__(self, stores):
        self._stores = stores
        self._connection = stores.connection

    @contextmanager
    def _start_scope(self, unit_of_work, command, request, plan, guard, prefix):
        from control_plane_kit_core.runtime_effect_observation import runtime_effect_intent_fingerprint
        from control_plane_kit_operations._configuration_cleanup_ownership import (
            _CleanupPreparationOwner, _PreparedCleanupStart,
        )
        from .configuration_cleanup_read_ceilings import _CleanupOriginalReadCeilingsOwner
        from .configuration_cleanup_phase_read_bounds import _CleanupPhaseReadBoundsOwner
        owner = _CleanupPreparationOwner(unit_of_work, self, guard, prefix)
        if (request != prefix.request or command.transition.request_fingerprint
                != runtime_effect_intent_fingerprint(command.intent)):
            raise _Unavailable
        owner.plan = plan
        prepared = _PreparedCleanupStart(owner, command.transition.identity, command.intent)
        owner.issued = prepared
        try:
            with ExitStack() as contexts:
                contexts.enter_context(_joined_read(self._connection))
                original_owner = _CleanupOriginalReadCeilingsOwner(unit_of_work)
                original_bounds = original_owner.capture(guard, prefix, plan,
                    intent_identity=prepared.identity, prospective_intent=prepared.intent)
                contexts.enter_context(original_owner.bind(original_bounds))
                phase_owner = _CleanupPhaseReadBoundsOwner(unit_of_work)
                phase_bounds = phase_owner.capture(guard, prefix, plan,
                    intent_identity=prepared.identity, prospective_intent=prepared.intent)
                contexts.enter_context(phase_owner.bind(phase_bounds))
                owner.original_bounds, owner.phase_bounds = original_bounds, phase_bounds
                owner.require(prepared, self._connection, write=True)
                owner.fresh = self._fresh_start(prepared)
                from control_plane_kit_operations._configuration_cleanup_ownership import _preflight_start
                _preflight_start(prepared)
                yield prepared
        finally:
            owner.close()

    def _require_current(self, prepared):
        if self._fresh_start(prepared) != prepared.owner.fresh:
            raise OperationsRecordError(_ERROR)

    def _fresh_start(self, prepared):
        from control_plane_kit_operations.configuration_cleanup import (
            ConfigurationCleanupExpectedContext, ConfigurationCleanupSourceSelector,
        )
        from control_plane_kit_operations.configuration_cleanup_planning import InspectConfigurationCleanup
        from control_plane_kit_core.operations.lifecycle import ActivityRunStatus, ExecutionRequestStatus
        from .configuration_cleanup_store import _inspect
        from .configuration_evidence import _EvidenceRead, _COMPOSED_READ
        owner = prepared.owner
        stores, request, plan = self._stores, owner.prefix.request, owner.plan
        read = _EvidenceRead(self._connection)
        token = _COMPOSED_READ.set(read)
        try:
            owner.prefix.require(owner.uow, request, prepared.identity.run_id.value, latest_required=True)
            if (owner.prefix.latest_run != owner.prefix.requested_run
                    or owner.prefix.requested_run.status is not ActivityRunStatus.RUNNING
                    or request.status is not ExecutionRequestStatus.CLAIMED
                    or stores.execution.get_request(request.identity.request_id) != request
                    or stores.activity_history.get_plan(plan.plan_id) != plan):
                raise _Unavailable
            approval = stores.activity_history.get_approval_request(request.approval_request_id)
            decision = stores.activity_history.approval_decision_for_request(request.approval_request_id)
            requirement = ApprovalPolicy().requirement_for(plan.plan)
            if (approval.session_id != plan.session_id
                    or approval.subject != ActivityPlanApprovalSubject(plan.plan_id,
                        proposal_fingerprint=configuration_cleanup_proposal_fingerprint(plan.cleanup_proposal))
                    or (approval.required_scope, approval.max_risk, approval.destructive)
                        != (requirement.required_scope, requirement.max_risk, requirement.destructive)
                    or decision is None or decision.decision_id != request.approval_decision_id
                    or decision.decision is not ApprovalDecisionKind.APPROVED or decision.scope != approval.required_scope):
                raise _Unavailable
            registration = stores.runtime_authorities.get_active_for_update(
                request.identity.workspace_id, prepared.intent.authority_ref)
            if (registration.runtime_kind is not prepared.intent.runtime_kind
                    or registration.authority_ref != prepared.intent.authority_ref):
                raise _Unavailable
            document = plan.cleanup_proposal.descriptor()
            context = document["context"]
            def identity(value):
                return EffectAttemptIdentity(RunId(value["run_id"]), value["activity_id"], value["attempt"])
            selectors = tuple(ConfigurationCleanupSourceSelector(identity(row["seed"]["source_identity"]),
                row["seed"]["artifact_id"], ConfigurationInstanceRefCodec().decode(row["ref"]))
                for row in document["candidates"])
            pins = ConfigurationCleanupExpectedContext(**{name: context[name] for name in (
                "base_graph_id", "base_realized_projection_id", "desired_graph_id",
                "desired_realized_projection_id", "desired_graph_revision")})
            result, proposal = _inspect(stores, InspectConfigurationCleanup(context["session_id"],
                context["workspace_id"], pins, selectors), read)
            if (result.state != "complete" or proposal != plan.cleanup_proposal
                    or tuple(value.expected_ref for value in selectors) != prepared.intent.operation.instances
                    or prepared.identity.activity_id != prepared.intent.activity_id.value):
                raise _Unavailable
            # Every candidate's complete claim set and every whole admitted D1
            # are required; a terminal provider profile alone is insufficient.
            # _inspect just proved these complete allocations into this new
            # read's immutable ref cache. Project its exact proposal keys;
            # never reuse the previous F's mutable eligibility or rowsets.
            def evidence(locator, artifact):
                return _decode(read.refs[(identity(locator), artifact)], read)
            members = tuple(evidence(row["birth"]["source_identity"], row["birth"]["artifact_id"])
                for row in document["candidates"])
            claims = tuple(sorted((evidence(value, selector.artifact_id)
                for row, selector in zip(document["candidates"], selectors, strict=True)
                for value in row["protecting_uses"]),
                key=lambda value: (*_key(value.identity), value.ref.artifact_id)))
            identities = tuple(sorted({claim.identity for claim in claims}, key=_key))
            completions = tuple(read.sources.get(("cleanup-admitted-completion", value)) for value in identities)
            if any(value is None for value in completions):
                raise _Unavailable
            for completion in completions:
                selected = tuple(claim for claim in claims if claim.identity == completion.identity)
                whole = read_original_selection(self._connection, completion.identity, selected[0].ref, read=read)
                if tuple(value.ref for value in selected) != tuple(value.ref for value in whole):
                    raise _Unavailable
            for value in members:
                self._require_absent(read, "cpk_configuration_cleanup_members",
                    "workspace_id=%s AND allocation_id=%s", (value.ref.workspace_id, value.ref.allocation_id))
            for value in completions:
                self._require_absent(read, "cpk_configuration_invocation_closures",
                    "run_id=%s AND activity_id=%s AND attempt=%s", _key(value.identity))
            for value in claims:
                key = (*_key(value.identity), value.ref.artifact_id)
                self._require_absent(read, "cpk_configuration_claim_closures",
                    "run_id=%s AND activity_id=%s AND attempt=%s AND artifact_id=%s", key)
                _paired_disposition(read, key, value.ref, protective=True)
            return registration, members, claims, completions
        finally:
            _COMPOSED_READ.reset(token)

    @staticmethod
    def _require_absent(read, table, where, values):
        if read.query("SELECT 1 FROM " + table + " WHERE " + where + " LIMIT 1", values,
                records=1, octets=1, cells=1):
            raise _Unavailable

    def get(self, identity):
        try:
            with _joined_read(self._connection) as read:
                return self._get(identity, read)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            raise OperationsRecordError(_ERROR) from None

    def _rows(self, identity, read, *, expected=None):
        cleanup = _key(identity)
        from .configuration_cleanup_phase_read_bounds import _phase_context, _phase_require
        _phase_context(self._connection, read=read)
        headers = read.bounded_rows(_TABLE, _columns(_HEADER), _WHERE, cleanup)
        if not headers:
            if expected is not None:
                raise _Unavailable
            return None
        if expected is not None and headers != (expected[0],):
            raise _Unavailable
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
        if expected is not None and tuple(tuple(rows) for rows in children) != expected[1:]:
            raise _Unavailable
        return h, children

    def _get(self, identity, read, *, expected=None):
        retained = self._rows(identity, read, expected=expected)
        if retained is None:
            return None
        h, children = retained
        cleanup = _key(identity)
        from .configuration_cleanup_phase_read_bounds import _phase_require
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

    def _bind_start(self, prepared, original):
        from control_plane_kit_operations._configuration_cleanup_ownership import _PreparedCleanupStart
        if type(prepared) is not _PreparedCleanupStart or prepared.owner.store is not self:
            raise OperationsRecordError(_ERROR)
        owner = _prepared_owner(prepared, self)
        # Binding is in-memory only. The next write entrance performs the
        # third cold fresh proof; it cannot substitute this record for it.
        owner.require_context(prepared, self._connection, write=True)
        if (owner.bound is not None or original.identity != prepared.identity
                or original.intent != prepared.intent):
            raise OperationsRecordError(_ERROR)
        request, plan = owner.prefix.request, owner.plan
        registration, members, claims, completions = owner.fresh
        owner.record = ConfigurationCleanupReservationRecord(
            identity=original.identity, workspace_id=original.intent.source.workspace_id,
            request_id=request.identity.request_id, request_fingerprint=original.request_fingerprint,
            original_event_id=original.original_start_event.event_id, plan_id=plan.plan_id,
            approval_request_id=request.approval_request_id, approval_decision_id=request.approval_decision_id,
            proposal_fingerprint=configuration_cleanup_proposal_fingerprint(plan.cleanup_proposal),
            runtime_id=original.intent.operation.instances[0].runtime_id, runtime_kind=original.intent.runtime_kind,
            authority_ref=original.intent.authority_ref, registration_id=registration.registration_id,
            members=members, claims=claims, completions=completions, status=EffectAttemptStatus.STARTED)
        owner.bound = original

    def _insert_start(self, prepared, original):
        owner = _prepared_owner(prepared, self)
        owner.require(prepared, self._connection, write=True)
        if owner.store is not self or owner.bound is not original:
            raise OperationsRecordError(_ERROR)
        self._require_current(prepared)
        from .configuration_evidence import _active_read
        read = _active_read(self._connection)
        rows = _expected_rows(owner.record)
        for table, columns, values in (
                (_TABLE, _HEADER, (rows[0],)),
                ("cpk_configuration_cleanup_members", _MEMBERS, rows[1]),
                ("cpk_configuration_invocation_closures", _INVOCATIONS, rows[2]),
                ("cpk_configuration_claim_closures", _CLAIMS, rows[3])):
            for value in values:
                self._insert_row(read, table, columns, value)
        cleanup = _key(owner.record.identity)
        for claim in owner.record.claims:
            ref = claim.ref
            for table in ("cpk_effect_configuration_refs", "cpk_configuration_claims"):
                changed = read.query("UPDATE " + table + " SET protective=false,cleanup_run_id=%s,"
                    "cleanup_activity_id=%s,cleanup_attempt=%s WHERE "
                    "(run_id,activity_id,attempt,artifact_id)=(%s,%s,%s,%s) "
                    "AND (workspace_id,allocation_id,runtime_id,node_id)=(%s,%s,%s,%s) "
                    "AND protective AND cleanup_run_id IS NULL AND cleanup_activity_id IS NULL "
                    "AND cleanup_attempt IS NULL RETURNING 1",
                    (*cleanup, *_key(claim.identity), ref.artifact_id,
                        ref.workspace_id, ref.allocation_id, ref.runtime_id, ref.node_id),
                    records=1, octets=1, cells=1)
                if changed != [(1,)]:
                    raise _Unavailable
        owner.spent = True

    @staticmethod
    def _insert_row(read, table, columns, values):
        rows = read.query("INSERT INTO " + table + " (" + ",".join(columns) + ") VALUES ("
            + ",".join("%s" for _ in columns) + ") RETURNING 1", values,
            records=1, octets=1, cells=1)
        if rows != [(1,)]:
            raise _Unavailable

    def _verify_start(self, prepared):
        owner = _prepared_owner(prepared, self)
        owner.require(prepared, self._connection)
        if owner.store is not self or not owner.spent or owner.record is None:
            raise OperationsRecordError(_ERROR)
        with _joined_read(self._connection) as read:
            actual = self._get(prepared.identity, read, expected=_expected_rows(owner.record))
        if actual != owner.record:
            raise _Unavailable
        return actual

    @contextmanager
    def _fold_scope(self, unit_of_work, guard, prefix, original, attempt, outcome, request, fence):
        from control_plane_kit_operations._configuration_cleanup_ownership import (
            _CleanupPreparationOwner, _PreparedCleanupFold, _preflight_fold,
        )
        from control_plane_kit_core.operations.lifecycle import ActivityRunStatus, ExecutionRequestStatus
        from .configuration_cleanup_read_ceilings import _CleanupOriginalReadCeilingsOwner
        from .configuration_cleanup_phase_read_bounds import _CleanupPhaseReadBoundsOwner
        owner = _CleanupPreparationOwner(unit_of_work, self, guard, prefix)
        prepared = _PreparedCleanupFold(owner, original, attempt, outcome, request, fence)
        owner.issued = prepared
        try:
            with ExitStack() as contexts:
                read = contexts.enter_context(_joined_read(self._connection))
                owner.plan = self._stores.activity_history.get_plan(original.intent.source.plan_id)
                original_owner = _CleanupOriginalReadCeilingsOwner(unit_of_work)
                owner.original_bounds = original_owner.capture(guard, prefix, owner.plan,
                    intent_identity=original.identity, prospective_intent=original.intent)
                contexts.enter_context(original_owner.bind(owner.original_bounds))
                phase_owner = _CleanupPhaseReadBoundsOwner(unit_of_work)
                owner.phase_bounds = phase_owner.capture_retained(guard, prefix, owner.plan,
                    original_identity=original.identity)
                contexts.enter_context(phase_owner.bind(owner.phase_bounds))
                owner.require(prepared, self._connection, write=True)
                prefix.require(unit_of_work, request, original.identity.run_id.value, latest_required=True)
                if (prefix.request != request or prefix.latest_run != prefix.requested_run
                        or prefix.requested_run.status is not ActivityRunStatus.RUNNING
                        or request.status is not ExecutionRequestStatus.CLAIMED or request.claim is None
                        or (request.claim.worker_id, request.claim.generation) != (fence.worker_id, fence.generation)
                        or self._stores.execution.get_request(request.identity.request_id) != request
                        or attempt.state.status is not EffectAttemptStatus.STARTED or attempt.state.fence != fence
                        or original.identity != attempt.state.identity
                        or original.original_start_event != attempt.original_start_event
                        or original.request_fingerprint != attempt.state.request_fingerprint
                        or self._stores.effect_attempt_intents.get(original.identity) != original
                        or self._stores.effect_attempts.get(original.identity) != attempt
                        or type(outcome) not in (ExecutionEffectOutcome, ObservedEffectOutcome)
                        or outcome.identity != original.identity
                        or outcome.request_fingerprint != original.request_fingerprint):
                    raise _Unavailable
                owner.record = self._get(original.identity, read)
                if owner.record is None or owner.record.status is not EffectAttemptStatus.STARTED:
                    raise _Unavailable
                owner.outcomes = (configuration_cleanup_outcomes(runtime_effect_request_for_intent(original.intent,
                    effect_id=original.original_start_event.event_id), outcome.result)
                    if type(outcome) is ExecutionEffectOutcome else None)
                _preflight_fold(prepared)
                yield prepared
        finally:
            owner.close()

    def _bind_fold(self, prepared, outcome_record):
        from control_plane_kit_core.operations import fold_effect_attempt
        from control_plane_kit_operations._configuration_cleanup_ownership import _PreparedCleanupFold
        from control_plane_kit_operations.effect_outcome_evidence import EffectAttemptOutcomeRecord, effect_outcome_transition
        owner = _prepared_owner(prepared, self)
        owner.require(prepared, self._connection, write=True)
        if (type(prepared) is not _PreparedCleanupFold or owner.store is not self or owner.bound is not None
                or type(outcome_record) is not EffectAttemptOutcomeRecord
                or outcome_record.outcome != prepared.outcome
                or outcome_record.workspace_id != owner.record.workspace_id
                or outcome_record.attempt.original_start_event != prepared.attempt.original_start_event
                or outcome_record.attempt.state != fold_effect_attempt(prepared.attempt.state,
                    effect_outcome_transition(prepared.outcome), fence=prepared.fence)):
            raise OperationsRecordError(_ERROR)
        EffectAttemptOutcomeRecord.__post_init__(outcome_record)
        owner.bound = outcome_record

    def _insert_fold(self, prepared, outcome_record):
        from .configuration_evidence import _active_read
        owner = _prepared_owner(prepared, self)
        owner.require(prepared, self._connection, write=True)
        if owner.store is not self or owner.bound is not outcome_record:
            raise OperationsRecordError(_ERROR)
        read = _active_read(self._connection)
        record, terminal = owner.record, outcome_record.attempt
        # Normal outcome/CAS now exist, but member results do not. Compare
        # the prepared rows and reciprocal closure without invoking terminal B.
        self._rows(record.identity, read, expected=_expected_rows(record))
        for claim in record.claims:
            _paired_disposition(read, (*_key(claim.identity), claim.ref.artifact_id), claim.ref,
                expected=_key(record.identity))
        rows = read.query("""
            SELECT 1 FROM cpk_configuration_cleanup_reservations c
            JOIN cpk_execution_requests q ON q.request_id=c.request_id
            JOIN LATERAL (SELECT * FROM cpk_activity_runs
                WHERE request_id=q.request_id ORDER BY attempt DESC LIMIT 1) r ON r.run_id=c.cleanup_run_id
            JOIN cpk_effect_attempts a ON (a.run_id,a.activity_id,a.attempt)=
                (c.cleanup_run_id,c.cleanup_activity_id,c.cleanup_attempt)
            JOIN cpk_effect_attempt_intents i ON (i.run_id,i.activity_id,i.attempt)=(a.run_id,a.activity_id,a.attempt)
            JOIN cpk_activity_events e0 ON e0.event_id=a.original_event_id
            JOIN cpk_activity_events e1 ON e1.event_id=a.latest_event_id
            JOIN cpk_effect_attempt_outcomes o ON (o.run_id,o.activity_id,o.attempt)=(a.run_id,a.activity_id,a.attempt)
            WHERE (c.cleanup_run_id,c.cleanup_activity_id,c.cleanup_attempt)=(%s,%s,%s)
              AND a.request_fingerprint=%s AND a.outcome_fingerprint=%s AND a.status=%s
              AND a.fence_worker_id=%s AND a.fence_generation=%s
              AND a.original_event_id=%s AND a.original_event_ordinal=%s
              AND a.latest_event_id=%s AND a.latest_event_ordinal=%s
              AND o.workspace_id=%s AND o.request_fingerprint=a.request_fingerprint
              AND o.outcome_fingerprint=a.outcome_fingerprint
              AND o.original_event_id=a.original_event_id AND o.original_event_ordinal=a.original_event_ordinal
              AND o.direct_event_id=a.latest_event_id AND o.direct_event_ordinal=a.latest_event_ordinal
              AND q.request_id=%s AND q.workspace_id=o.workspace_id AND q.status='claimed'
              AND q.claim_worker_id=a.fence_worker_id AND q.claim_generation=a.fence_generation
              AND r.status='running' AND r.plan_id=q.plan_id
              AND i.request_id=q.request_id AND i.workspace_id=q.workspace_id
              AND i.request_fingerprint=a.request_fingerprint AND i.original_event_id=a.original_event_id
              AND i.original_event_run_id=a.run_id AND i.original_event_ordinal=a.original_event_ordinal
              AND e0.run_id=a.run_id AND e0.ordinal=a.original_event_ordinal
              AND e1.run_id=a.run_id AND e1.ordinal=a.latest_event_ordinal
              AND c.workspace_id=q.workspace_id AND c.plan_id=q.plan_id
              AND c.original_event_id=a.original_event_id AND c.request_fingerprint=a.request_fingerprint
            LIMIT 1
            """, (*_key(record.identity), record.request_fingerprint, terminal.state.outcome_fingerprint,
                terminal.state.status.value, prepared.fence.worker_id, prepared.fence.generation,
                terminal.original_start_event.event_id, terminal.original_start_event.ordinal,
                terminal.latest_transition_event.event_id, terminal.latest_transition_event.ordinal,
                record.workspace_id, record.request_id), records=1, octets=1, cells=1, identities=8)
        if rows != [(1,)]:
            raise _Unavailable
        if owner.outcomes is not None:
            for value in owner.outcomes.outcomes:
                self._insert_row(read, "cpk_configuration_cleanup_member_outcomes", _OUTCOMES,
                    (*_key(record.identity), value.ref.workspace_id, value.ref.allocation_id,
                        record.request_fingerprint, terminal.state.outcome_fingerprint, value.status.value,
                        None if value.reason is None else value.reason.value))
        owner.spent = True
        # Full B proves the completed aggregate exactly once after all K rows.
        retained = self._get(record.identity, read)
        if (retained is None or retained.status is not terminal.state.status
                or retained.outcome_fingerprint != terminal.state.outcome_fingerprint
                or retained.outcomes != owner.outcomes):
            raise _Unavailable
        return retained

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
