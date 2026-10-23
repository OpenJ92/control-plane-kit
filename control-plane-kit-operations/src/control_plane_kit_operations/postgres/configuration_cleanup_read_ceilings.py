"""Issue transaction-local ceilings for a closed set of cleanup originals."""
from contextlib import contextmanager

from control_plane_kit_core.approval_subjects import ActivityPlanApprovalSubject
from control_plane_kit_core.operations import EffectAttemptIdentity
from control_plane_kit_core.planning import CleanupConfigurationInstances
from control_plane_kit_core.policies import ApprovalPolicy
from control_plane_kit_core.runtime_effect_observation import RuntimeEffectIntent, RuntimeEffectIntentSource
from control_plane_kit_core.runtime_effects import RuntimeEffectKind
from control_plane_kit_operations._configuration_cleanup_read_ceilings import (
    _BOUND_CLEANUP_ORIGINALS, _CleanupOriginalReadCeilings,
)
from control_plane_kit_operations._configuration_preparation import _ACCOUNTING, _execution_context
from control_plane_kit_operations.configuration_cleanup import configuration_cleanup_proposal_fingerprint
from control_plane_kit_operations.effect_attempt_intent_evidence import _encode_runtime_effect_intent
from control_plane_kit_operations.records import ActivityPlanStatus, ApprovalDecisionKind
from .configuration_evidence import _active_read, _Unavailable, _Capacity


def _require(condition):
    if not condition:
        raise _Unavailable


def _transaction(read):
    rows = read.query("SELECT txid_current()", (), records=1, octets=20, cells=1)
    _require(len(rows) == 1 and type(rows[0][0]) is int)
    return rows[0][0]


def _cleanup_original_limits(connection, kind, identity):
    """Exactly one charged txid check per matching entrance; no fallback."""
    issued = _BOUND_CLEANUP_ORIGINALS.get()
    if issued is None:
        return None
    _require(type(issued) is _CleanupOriginalReadCeilings)
    matched = next((columns for item_kind, key, columns in issued.originals
        if item_kind == kind and key == identity), None)
    if matched is None:
        return None
    owner = issued.owner
    _require(type(owner) is _CleanupOriginalReadCeilingsOwner)
    owner._require(issued, connection)
    read = _active_read(connection)
    _require(read is not None and _transaction(read) == issued.transaction_id)
    return dict(matched)


def _cleanup_original_columns(connection, kind, identity, columns):
    limits = _cleanup_original_limits(connection, kind, identity)
    if limits is None:
        return columns
    # These callers use the fixed raw column names; receiver expressions use
    # the same names followed by ::text in their own existing _columns owner.
    return tuple((name, native, min(cap, limits.get(name, cap))) for name, native, cap in columns)


class _CleanupOriginalReadCeilingsOwner:
    def __init__(self, unit_of_work):
        self._uow = unit_of_work
        self._stores = unit_of_work.stores
        self._connection = self._stores.connection
        self._accounting = _ACCOUNTING.get()
        self._context = _execution_context()
        self._issued = None
        self._spent = False

    def _require(self, issued, connection):
        try:
            stores = self._uow.stores
        except RuntimeError:
            raise _Unavailable from None
        _require(type(issued) is _CleanupOriginalReadCeilings
            and issued.owner is self and issued is self._issued and not self._spent
            and stores is self._stores and not self._uow._commit_requested
            and self._stores.connection is connection
            and connection is self._connection and _ACCOUNTING.get() is self._accounting
            and self._accounting is not None and self._accounting.active
            and self._context == _execution_context()
            and self._accounting.execution_context == self._context)

    def capture(self, guard, prefix, approved_plan, *, intent_identity, prospective_intent):
        from .graph_store import WorkspaceLifecycleGuard
        from .receiver_execution_scopes import _ExecutionScopeStorage, _PLAN, _GRAPH, _PROJECTION
        _require(self._issued is None and not self._spent and _BOUND_CLEANUP_ORIGINALS.get() is None)
        _require(self._accounting is not None and self._accounting is _ACCOUNTING.get()
            and self._accounting.active and self._context == _execution_context())
        read = _active_read(self._connection)
        _require(read is not None and type(guard) is WorkspaceLifecycleGuard
            and guard._owner is self._stores.graphs
            and type(intent_identity) is EffectAttemptIdentity and intent_identity.attempt > 0
            and type(prospective_intent) is RuntimeEffectIntent)
        transaction = _transaction(read)
        _require(transaction == guard._transaction_id)
        request = prefix.request
        _require(self._stores.execution.get_request_for_update(request.identity.request_id) == request)
        _require(guard.workspace_id == request.identity.workspace_id
            and request.status.value == "claimed" and prefix.requested_run.status.value == "running"
            and prefix.latest_run == prefix.requested_run)
        prefix.require(self._uow, request, intent_identity.run_id.value, latest_required=True)
        plan, base, desired = _ExecutionScopeStorage(self._connection, read).verify(request.identity)[0]
        _require(plan == approved_plan and plan.status is ActivityPlanStatus.PLANNED and plan.cleanup_proposal is not None
            and base.projection_id == plan.base_realized_projection_id
            and desired.projection_id == plan.desired_realized_projection_id)
        approval = self._stores.activity_history.get_approval_request(request.approval_request_id)
        decision = self._stores.activity_history.approval_decision_for_request(approval.request_id)
        policy = ApprovalPolicy().requirement_for(plan.plan)
        _require(approval.session_id == plan.session_id
            and approval.subject == ActivityPlanApprovalSubject(plan.plan_id,
                proposal_fingerprint=configuration_cleanup_proposal_fingerprint(plan.cleanup_proposal))
            and (approval.required_scope, approval.max_risk, approval.destructive)
                == (policy.required_scope, policy.max_risk, policy.destructive)
            and decision is not None and decision.decision_id == request.approval_decision_id
            and decision.decision is ApprovalDecisionKind.APPROVED and decision.scope == approval.required_scope)
        activity = plan.plan.activity(prospective_intent.activity_id)
        _require(type(activity.operation) is CleanupConfigurationInstances)
        from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC
        runtime_id = activity.operation.instances[0].runtime_id
        runtimes = tuple(DEFAULT_GRAPH_CODEC.decode(p.graph_descriptor).runtimes.get(runtime_id)
            for p in (base, desired))
        _require(all(r is not None and r.authority_ref is not None for r in runtimes))
        runtime = runtimes[0]
        _require((runtime.kind, runtime.authority_ref) == (runtimes[1].kind, runtimes[1].authority_ref))
        expected = RuntimeEffectIntent(RuntimeEffectKind.CONFIGURATION_ACTIVITY_V1, runtime.kind,
            RuntimeEffectIntentSource(request.identity.workspace_id, request.identity.request_id,
                intent_identity.run_id, plan.plan_id, plan.base_graph_id, plan.desired_graph_id),
            activity.activity_id, activity.operation, runtime.authority_ref, (), ())
        _require(prospective_intent == expected and intent_identity.activity_id == activity.activity_id.value)
        originals = [("plan", plan.plan_id, self._lengths(read, "cpk_activity_plans", _PLAN,
            ("payload",), "plan_id=%s AND session_id=%s", (plan.plan_id, plan.session_id)))]
        seen = set()
        for projection in (base, desired):
            graph_id = projection.source_authored_graph_id
            if ("graph", graph_id) not in seen:
                originals.append(("graph", graph_id, self._lengths(read, "cpk_graph_versions", _GRAPH,
                    ("graph_descriptor", "metadata"), "graph_id=%s AND workspace_id=%s",
                    (graph_id, request.identity.workspace_id))))
                seen.add(("graph", graph_id))
            if ("projection", projection.projection_id) not in seen:
                originals.append(("projection", projection.projection_id, self._lengths(read,
                    "cpk_realized_graph_projections", _PROJECTION, ("graph_descriptor",),
                    "projection_id=%s AND workspace_id=%s AND source_authored_graph_id=%s",
                    (projection.projection_id, request.identity.workspace_id, graph_id))))
                seen.add(("projection", projection.projection_id))
        originals.append(("intent", (intent_identity.run_id.value, intent_identity.activity_id,
            intent_identity.attempt), (("preimage", len(_encode_runtime_effect_intent(expected))),)))
        issued = _CleanupOriginalReadCeilings(self, tuple(originals), transaction)
        self._issued = issued
        return issued

    @staticmethod
    def _lengths(read, table, names, documents, where, params):
        # The same fixed column::text expressions as all later point readers.
        rows = read.query("SELECT " + ",".join(f"octet_length({name}::text)" for name in names)
            + " FROM " + table + " WHERE " + where + " LIMIT 1", params,
            records=1, octets=12 * len(names), cells=len(names))
        _require(len(rows) == 1)
        result = []
        for name, width in zip(names, rows[0], strict=True):
            _require(width is None or type(width) is int and width >= 0)
            if width is not None and width > (1048576 if name in documents else 2048):
                raise _Capacity
            result.append((name, 0 if width is None else width))
        return tuple(result)

    @contextmanager
    def bind(self, issued):
        self._require(issued, self._connection)
        _require(_BOUND_CLEANUP_ORIGINALS.get() is None)
        token = _BOUND_CLEANUP_ORIGINALS.set(issued)
        try:
            yield
        finally:
            _BOUND_CLEANUP_ORIGINALS.reset(token)
            self._spent = True
            _require(not self._uow._commit_requested)
