"""Fixed cleanup proof readers, with single-use transaction-local bounds.

The finite role branches below own their SQL. No caller supplies a relation,
selector, expression, width, or registration. Existing readers still own all
decoding and semantic proofs; a bound only restricts transport.
"""
from contextlib import contextmanager

from control_plane_kit_operations._configuration_cleanup_phase_read_bounds import (
    _BOUND_CLEANUP_PHASE, _CleanupPhaseReadBounds, _PhasePoint, _PhaseCollection,
)
from control_plane_kit_operations._configuration_cleanup_read_ceilings import _BOUND_CLEANUP_ORIGINALS
from control_plane_kit_operations._configuration_preparation import _ACCOUNTING, _execution_context
from .configuration_cleanup_read_ceilings import _require, _transaction
from .configuration_evidence import _active_read, _EvidenceRead, _Unavailable, _Capacity


def _entries(value, role):
    # Explicit closed alternatives, deliberately not an extensible registry.
    match role:
        case "plan": return value.plans
        case "graph": return value.graphs
        case "projection": return value.projections
        case "raw-graph": return value.raw_graphs
        case "raw-projection": return value.raw_projections
        case "introduction": return value.introductions
        case "origin-action": return value.origin_actions
        case "acceptance-action": return value.acceptance_actions
        case "receipt-action": return value.receipt_actions
        case "receipt-event": return value.receipt_events
        case "request": return value.requests
        case "run": return value.runs
        case "session": return value.sessions
        case "header": return value.headers
        case "scopes": return value.scopes
        case "runs": return value.run_histories
        case "events": return value.event_histories
        case "advancement-actions": return value.advancement_actions
        case "bindings": return value.bindings
        case "slots": return value.slots
        case "allocation-refs": return value.allocation_refs
        case "allocation-claims": return value.allocation_claims
        case "invocation-refs": return value.invocation_refs
    raise _Unavailable


def _phase_context(connection, *, accounting=None):
    issued = _BOUND_CLEANUP_PHASE.get()
    if issued is not None:
        _require(type(issued) is _CleanupPhaseReadBounds
            and type(issued.owner) is _CleanupPhaseReadBoundsOwner)
        issued.owner._require(issued, connection)
        _require(accounting is None or accounting is issued.owner._accounting)
    return issued


def _bound(connection, role, identity):
    issued = _phase_context(connection)
    if issued is None:
        return None
    matched = next((entry for entry in _entries(issued, role) if entry.identity == identity), None)
    if matched is None:
        return None
    read = _active_read(connection)
    _require(read is not None and _transaction(read) == issued.transaction_id)
    return matched


def _contains(issued, role, identity):
    if role == "retained":
        return issued.retained_identity == identity
    # Independently derived original IDs, never copied #1939 widths. The
    # original reader still applies its own sole SQL guard and cap contract.
    if role == "plan" and identity == (issued.original_plan,):
        return True
    if role == "graph" and identity in tuple((key,) for key in issued.original_graphs):
        return True
    if role == "projection" and identity in tuple((key,) for key in issued.original_projections):
        return True
    return any(entry.identity == identity for entry in _entries(issued, role))


def _phase_require(connection, parent_role, parent_identity, child_role, child_identity):
    """A captured traversal cannot redirect into an uncaptured native read.

    Direct unrelated lookups retain ordinary defaults. This is an identity
    check, not a query or semantic permission; the child still runs its guard
    and cold owner proof.
    """
    issued = _phase_context(connection)
    if issued is not None and _contains(issued, parent_role, parent_identity):
        _require(_contains(issued, child_role, child_identity))


def _phase_columns(connection, role, identity, columns):
    bound = _bound(connection, role, identity)
    if bound is None:
        return columns
    _require(type(bound) is _PhasePoint and len(columns) == len(bound.widths))
    return tuple((name, kind, min(cap, width))
        for (name, kind, cap), width in zip(columns, bound.widths, strict=True))


def _phase_rows(read, role, identity, *, maximum=None, text=False):
    _phase_context(read.connection, accounting=read.accounting)
    bound = _bound(read.connection, role, identity)
    if bound is None:
        return None
    _require(type(bound) is _PhaseCollection)
    table, columns, where, order, native_maximum, keys, identities = _shape(role)
    count = len(bound.keys)
    if native_maximum is not None and count > native_maximum or maximum is not None and count > maximum:
        raise _Capacity
    columns = tuple((name, "text" if text and kind != "bytes" else kind, min(cap, width))
        for (name, kind, cap), width in zip(columns, bound.widths, strict=True))
    rows = read.bounded_rows(table, columns, where, identity,
        maximum=count, point=False, order=order, identities=identities)
    _require(tuple(tuple(None if row[i] is None else str(row[i]) for i in keys) for row in rows) == bound.keys)
    return rows


def _shape(role):
    """Finite package-owned selectors and native declarations in reader order."""
    from .receiver_execution_scopes import _PLAN, _GRAPH, _PROJECTION, _REQUEST, _RUN, _EVENT, _ACTION, _SCOPE
    from .receiver_lifecycle_store import _INTRO_COLUMNS, _BIND_COLUMNS
    from .configuration_acceptance_store import _HEADER, _LOCATOR, _SLOT, _columns
    from .configuration_preparation_store import _PHASE_REF_TABLE, _PHASE_REF_COLUMNS

    def columns(names, documents=(), *, document_cap=65536):
        return tuple((name, "json" if name in documents else "int" if name in (
            "version", "ordinal", "attempt", "desired_graph_revision", "claim_generation", "scope_ordinal")
            else "time" if name in ("created_at", "closed_at", "requested_at", "claimed_at", "lease_expires_at",
                "started_at", "settled_at", "occurred_at") else "text",
            document_cap if name in documents else 2048) for name in names)

    match role:
        case "plan":
            return "cpk_activity_plans", columns(_PLAN, ("payload",), document_cap=1048576), "plan_id=%s", "", 1, (), 1
        case "graph" | "raw-graph":
            where = "graph_id=%s" if role == "graph" else "workspace_id=%s AND graph_id=%s"
            return "cpk_graph_versions", columns(_GRAPH, ("graph_descriptor", "metadata"), document_cap=1048576), where, "", 1, (), 1
        case "projection" | "raw-projection":
            where = "projection_id=%s" if role == "projection" else "workspace_id=%s AND projection_id=%s AND source_authored_graph_id=%s"
            return "cpk_realized_graph_projections", columns(_PROJECTION, ("graph_descriptor",), document_cap=1048576), where, "", 1, (), 1
        case "introduction":
            return "cpk_graph_receiver_introductions", columns(_INTRO_COLUMNS), "workspace_id=%s AND receiver_id=%s", "", 1, (), 1
        case "origin-action":
            declared = tuple(("a."+name, kind, cap) for name, kind, cap in columns(_ACTION, ("payload",)))
            return "cpk_operation_actions a JOIN cpk_operation_sessions s ON s.session_id=a.session_id", declared, "a.action_id=%s AND a.session_id=%s AND s.workspace_id=%s", "", 1, (), 2
        case "acceptance-action":
            return "cpk_operation_actions", columns(_ACTION, ("payload",)), "action_id=%s AND session_id=%s", "", 1, (), 1
        case "receipt-action":
            return "cpk_operation_actions", _columns(_ACTION + _LOCATOR + ("advancement_run_id",)), "action_id=%s", "", 1, (), 1
        case "receipt-event":
            return "cpk_activity_events", _columns(_EVENT + _LOCATOR), "event_id=%s", "", 1, (), 1
        case "request":
            return "cpk_execution_requests", columns(_REQUEST), "request_id=%s", "", 1, (), 1
        case "run":
            return "cpk_activity_runs", columns(_RUN, ("metadata",)), "run_id=%s", "", 1, (), 1
        case "session":
            names = ("session_id", "workspace_id", "actor_id", "title", "status", "created_at", "closed_at", "metadata", "idempotency_key", "intent_fingerprint")
            return "cpk_operation_sessions", columns(names, ("metadata",)), "session_id=%s", "", 1, (), 1
        case "header":
            return "cpk_configuration_acceptances", _columns(_HEADER), "workspace_id=%s AND pinned_revision=%s", "", 1, (), 1
        case "scopes":
            return "cpk_execution_receiver_scopes", columns(_SCOPE), "request_id=%s", "scope_ordinal", 1024, (2,), 1
        case "runs":
            return "cpk_activity_runs", columns(_RUN, ("metadata",)), "request_id=%s", "attempt", 256, (0,), 1
        case "events":
            return "cpk_activity_events", columns(_EVENT, ("payload",)), "run_id=%s", "ordinal", 8192, (0,), 1
        case "advancement-actions":
            return "cpk_operation_actions", columns(_ACTION, ("payload",)), "session_id=%s AND payload->>'run_id'=%s AND action_type='advance-current-graph'", "action_id", 1, (0,), 1
        case "bindings":
            return "cpk_graph_receiver_bindings", columns(_BIND_COLUMNS), "workspace_id=%s AND graph_id=%s AND realized_projection_id=%s", "node_id,provider_socket_name", None, (4, 5), 1
        case "slots":
            return "cpk_configuration_accepted_slots", _columns(_SLOT), "workspace_id=%s AND pinned_revision=%s", "runtime_id,node_id,artifact_id", 256, (0, 1, 2), 1
        case "allocation-refs":
            return _PHASE_REF_TABLE, _PHASE_REF_COLUMNS, "r.workspace_id=%s AND r.allocation_id=%s", "r.run_id,r.activity_id,r.attempt,r.artifact_id", 64, (0, 1, 2, 3), 2
        case "allocation-claims":
            return "cpk_configuration_claims", (("run_id", "text", 200), ("activity_id", "text", 200), ("attempt", "int", 10), ("artifact_id", "text", 63)), "workspace_id=%s AND allocation_id=%s", "run_id,activity_id,attempt,artifact_id", 64, (0, 1, 2, 3), 1
        case "invocation-refs":
            return _PHASE_REF_TABLE, _PHASE_REF_COLUMNS, "r.run_id=%s AND r.activity_id=%s AND r.attempt=%s", "r.artifact_id", 32, (3,), 2
    raise _Unavailable


def _capture_point(read, role, identity):
    table, columns, where, _, _, _, identities = _shape(role)
    expressions = tuple(name if kind == "bytes" else "("+name+")::text" for name, kind, _ in columns)
    rows = read.query("SELECT " + ",".join("octet_length("+value+")" for value in expressions)
        + " FROM " + table + " WHERE " + where + " LIMIT 1", identity,
        records=1, octets=12*len(columns), cells=len(columns), identities=identities)
    _require(len(rows) == 1)
    return _PhasePoint(identity, _widths(columns, rows))


def _widths(columns, rows):
    result = []
    for index, (_, _, cap) in enumerate(columns):
        values = tuple(row[index] for row in rows if row[index] is not None)
        _require(all(type(value) is int and value >= 0 for value in values))
        if any(value > cap for value in values):
            raise _Capacity
        result.append(max(values, default=0))
    return tuple(result)


def _capture_collection(read, role, identity, expected):
    table, columns, where, order, maximum, keys, identities = _shape(role)
    # Binding cardinality belongs to the complete derived graph, not a new cap.
    if role == "bindings":
        maximum = len(expected)
    suffix = " FROM " + table + " WHERE " + where + " ORDER BY " + order
    counted = read.query("SELECT COUNT(*) FROM (SELECT 1" + suffix + " LIMIT %s) AS bounded_candidates",
        (*identity, maximum+1), records=1, octets=20, cells=1)
    _require(len(counted) == 1 and type(counted[0][0]) is int and counted[0][0] >= 0)
    count = counted[0][0]
    if count > maximum:
        raise _Capacity
    expressions = tuple(name if kind == "bytes" else "("+name+")::text" for name, kind, _ in columns)
    key_expressions = tuple(f"CASE WHEN octet_length({expressions[i]})<={columns[i][2]} THEN {expressions[i]} END" for i in keys)
    limit = count + 1
    rows = read.query("SELECT " + ",".join((*key_expressions, *("octet_length("+value+")" for value in expressions)))
        + suffix + " LIMIT %s", (*identity, limit), records=limit,
        octets=limit*(sum(columns[i][2] for i in keys) + 12*len(columns)), cells=len(keys)+len(columns), identities=identities)
    actual = tuple(tuple(row[:len(keys)]) for row in rows)
    _require(len(rows) == count and len(set(actual)) == count
        and all(all(value is not None for value in key) for key in actual)
        and actual == expected)
    return _PhaseCollection(identity, _widths(columns, tuple(row[len(keys):] for row in rows)), actual)


class _CleanupPhaseReadBoundsOwner:
    def __init__(self, unit_of_work):
        self._uow = unit_of_work
        self._stores = unit_of_work.stores
        self._connection = self._stores.connection
        self._accounting = _ACCOUNTING.get()
        self._context = _execution_context()
        self._issued = None
        self._spent = False
        self._original = _BOUND_CLEANUP_ORIGINALS.get()

    def _require_context(self, connection):
        try:
            stores = self._uow.stores
        except RuntimeError:
            raise _Unavailable from None
        _require(not self._spent and stores is self._stores
            and not self._uow._commit_requested and connection is self._connection
            and stores.connection is connection and _ACCOUNTING.get() is self._accounting
            and self._accounting is not None and self._accounting.active
            and self._context == _execution_context() == self._accounting.execution_context
            and self._original is not None and _BOUND_CLEANUP_ORIGINALS.get() is self._original)
        self._original.owner._require(self._original, connection)
        _require(self._original.owner._uow is self._uow
            and self._original.owner._stores is self._stores)

    def _require(self, issued, connection):
        _require(type(issued) is _CleanupPhaseReadBounds and issued is self._issued and issued.owner is self)
        self._require_context(connection)

    def _require_original_identities(self, plan, identity):
        # Check correspondence to the already-issued original scope. Do not
        # copy its widths into C or admit another original identity.
        for role, key in (("plan", plan.plan_id), ("graph", plan.base_graph_id),
                ("graph", plan.desired_graph_id), ("projection", plan.base_realized_projection_id),
                ("projection", plan.desired_realized_projection_id),
                ("intent", (identity.run_id.value, identity.activity_id, identity.attempt))):
            self._require_original_identity(role, key)

    def _require_original_identity(self, role, key):
        _require(any(kind == role and original_key == key
            for kind, original_key, _ in self._original.originals))

    @contextmanager
    def bind(self, issued):
        self._require(issued, self._connection)
        _require(_BOUND_CLEANUP_PHASE.get() is None)
        _require(_transaction(_active_read(self._connection)) == issued.transaction_id)
        token = _BOUND_CLEANUP_PHASE.set(issued)
        try:
            yield
        finally:
            _BOUND_CLEANUP_PHASE.reset(token)
            self._spent = True
            _require(not self._uow._commit_requested)

    def capture(self, guard, prefix, approved_plan, *, intent_identity, prospective_intent):
        from control_plane_kit_core.configuration_instances import ConfigurationInstanceRefCodec
        from control_plane_kit_core.operations import EffectAttemptIdentity, RunId
        from control_plane_kit_core.planning import CleanupConfigurationInstances
        from control_plane_kit_operations.configuration_cleanup import (
            ConfigurationCleanupProposalCodec, ConfigurationCleanupSourceSelector, ConfigurationCleanupExpectedContext,
        )
        from control_plane_kit_operations.configuration_cleanup_planning import InspectConfigurationCleanup
        from control_plane_kit_operations.effect_attempt_start_interpreter import _require_fresh_effect_receiver_permission
        from control_plane_kit_operations.receiver_lifecycle import _receiver_origin
        from .configuration_cleanup_store import _inspect
        from .receiver_execution_scopes import _ExecutionScopeStorage
        from .graph_store import WorkspaceLifecycleGuard

        _require(self._issued is None and not self._spent and _BOUND_CLEANUP_PHASE.get() is None
            and self._original is not None and _BOUND_CLEANUP_ORIGINALS.get() is self._original)
        self._require_context(self._connection)
        _require(type(guard) is WorkspaceLifecycleGuard and guard._owner is self._stores.graphs)
        read = _active_read(self._connection)
        transaction = _transaction(read)
        _require(transaction == guard._transaction_id == self._original.transaction_id)
        stores, request = self._stores, prefix.request
        _require(type(intent_identity) is EffectAttemptIdentity and intent_identity.attempt > 0
            and intent_identity.run_id.value == prefix.requested_run.run_id
            and intent_identity.activity_id == prospective_intent.activity_id.value
            and prospective_intent.source.request_id == request.identity.request_id
            and prospective_intent.source.run_id == intent_identity.run_id)
        self._require_original_identity("plan", request.identity.plan_id)
        self._require_original_identity("intent", (intent_identity.run_id.value, intent_identity.activity_id, intent_identity.attempt))
        prefix.require(self._uow, request, intent_identity.run_id.value, latest_required=True)
        plan = stores.activity_history.get_plan(request.identity.plan_id)
        self._require_original_identities(plan, intent_identity)
        _require(plan == approved_plan and plan.cleanup_proposal is not None
            and type(prospective_intent.operation) is CleanupConfigurationInstances
            and plan.plan.activity(prospective_intent.activity_id).operation == prospective_intent.operation)
        _require_fresh_effect_receiver_permission(stores, request, guard, prospective_intent, compensation=False)

        document = ConfigurationCleanupProposalCodec().encode(plan.cleanup_proposal)
        context = document["context"]
        pins = ConfigurationCleanupExpectedContext(**{name: context[name] for name in (
            "base_graph_id", "base_realized_projection_id", "desired_graph_id", "desired_realized_projection_id", "desired_graph_revision")})
        def identity(value):
            return EffectAttemptIdentity(RunId(value["run_id"]), value["activity_id"], value["attempt"])
        selectors = tuple(ConfigurationCleanupSourceSelector(identity(row["seed"]["source_identity"]),
            row["seed"]["artifact_id"], ConfigurationInstanceRefCodec().decode(row["ref"])) for row in document["candidates"])
        fresh = _EvidenceRead(self._connection)
        result, proposal = _inspect(stores, InspectConfigurationCleanup(context["session_id"], context["workspace_id"], pins, selectors), fresh)
        _require(result.state == "complete" and proposal == plan.cleanup_proposal)

        # Collect only identities and complete keysets from cold owner proofs.
        workspace = request.identity.workspace_id
        scope_reader = _ExecutionScopeStorage(self._connection, read)
        original, derived = scope_reader.verify(request.identity)
        scope_sets = {request.identity.request_id: tuple((str(i),) for i in range(len(derived.scopes)))}
        graph_pairs = {(p.source_authored_graph_id, p.projection_id) for p in original[1:]}
        origins = {}
        binding_sets = {}
        for graph_id, projection_id in sorted(graph_pairs):
            bindings = stores.graphs.receiver_bindings(workspace, graph_id, projection_id)
            binding_sets[(workspace, graph_id, projection_id)] = tuple((b.node_id, b.provider_socket_name) for b in bindings)
            for binding in bindings:
                origin = _receiver_origin(stores, binding)
                _require(origin is not None)
                origins[(workspace, origin.receiver_id)] = origin
        receipts = stores.execution._receiver_acceptance_evidence(tuple(origins.values())) if origins else ()
        for origin in origins.values():
            graph_pairs.add((origin.introducing_graph_id, origin.introducing_realized_projection_id))
        for graph_id, projection_id in sorted(graph_pairs):
            if (workspace, graph_id, projection_id) not in binding_sets:
                bindings = stores.graphs.receiver_bindings(workspace, graph_id, projection_id)
                binding_sets[(workspace, graph_id, projection_id)] = tuple((b.node_id, b.provider_socket_name) for b in bindings)

        history_plans, history_requests, history_runs, sessions = {}, {}, {}, set()
        run_sets, event_sets, action_sets = {}, {}, {}
        for action, event, historical_plan, run, desired, bindings in receipts:
            historical_request = stores.execution.get_request(action.payload["execution_request_id"])
            historical_original, historical_derived = scope_reader.verify(historical_request.identity)
            history_plans[historical_plan.plan_id] = historical_original
            history_requests[historical_request.identity.request_id] = historical_request
            history_runs[run.run_id] = run
            sessions.add(action.session_id)
            scope_sets[historical_request.identity.request_id] = tuple((str(i),) for i in range(len(historical_derived.scopes)))
            run_sets[historical_request.identity.request_id] = tuple((r.run_id,) for r in scope_reader.runs(historical_request))
            event_sets[run.run_id] = tuple((e.event_id,) for e in scope_reader.events(run.run_id))
            action_sets[(action.session_id, run.run_id)] = ((action.action_id,),)
            binding_sets[(workspace, desired.source_authored_graph_id, desired.projection_id)] = tuple((b.node_id, b.provider_socket_name) for b in bindings)

        # Receipt contexts already fully proved by the fresh inspection retain
        # their exact original selectors, including source acceptance contexts.
        contexts = [value for key, value in fresh.sources.items() if key[0] == "configuration-receipt-context"]
        for header, action, event, historical_plan, historical_request, run, material in contexts:
            history_requests[historical_request.identity.request_id] = historical_request
            history_runs[run.run_id] = run
            sessions.add(historical_plan.session_id)
            if historical_plan.plan_id not in history_plans:
                history_plans[historical_plan.plan_id] = (historical_plan,
                    stores.realized_graphs.get(historical_plan.base_realized_projection_id),
                    stores.realized_graphs.get(historical_plan.desired_realized_projection_id))
        allocation_sets, invocation_sets = {}, {}
        for selector in selectors:
            allocation = stores.configuration_preparation._allocation_evidence(selector.expected_ref, fresh)
            _require(allocation.state == "complete")
            allocation_sets[(workspace, selector.expected_ref.allocation_id)] = tuple(
                (claim.identity.run_id.value, claim.identity.activity_id, str(claim.identity.attempt), claim.ref.artifact_id)
                for claim in allocation.claims)
            for claim in allocation.claims:
                key = (claim.identity.run_id.value, claim.identity.activity_id, claim.identity.attempt)
                if key not in invocation_sets:
                    _require(stores.configuration_completions._get(claim.identity, fresh) is not None)
                    from .configuration_source import read_original_selection
                    selection = read_original_selection(self._connection, claim.identity, claim.ref, read=fresh)
                    invocation_sets[key] = tuple((value.ref.artifact_id,) for value in selection)

        # Capture after every semantic prerequisite. All work shares read.accounting.
        def points(role, keys):
            return tuple(_capture_point(read, role, key) for key in sorted(set(keys)))
        def collections(role, items):
            return tuple(_capture_collection(read, role, key, expected) for key, expected in sorted(items.items()))
        old_graphs = {p.source_authored_graph_id for _, base, desired in history_plans.values() for p in (base, desired)}
        old_graphs.update(graph_id for graph_id, _ in graph_pairs)
        old_projections = {p.projection_id for _, base, desired in history_plans.values() for p in (base, desired)}
        old_projections.update(projection_id for _, projection_id in graph_pairs)
        current_receipt = context["current_occurrence"]
        slots = {}
        if current_receipt["kind"] == "configuration-acceptance":
            receipt = stores.configuration_acceptance._receipt_manifest(workspace, current_receipt["pinned_revision"], fresh)
            slots[(workspace, current_receipt["pinned_revision"])] = tuple(tuple(str(v) for v in row[:3]) for row in receipt[3])
        issued = _CleanupPhaseReadBounds(self, transaction, plan.plan_id,
            tuple(sorted({plan.base_graph_id, plan.desired_graph_id})),
            tuple(sorted({plan.base_realized_projection_id, plan.desired_realized_projection_id})), None,
            points("plan", ((key,) for key in history_plans if key != plan.plan_id)),
            points("graph", ((key,) for key in old_graphs - {plan.base_graph_id, plan.desired_graph_id})),
            points("projection", ((key,) for key in old_projections - {plan.base_realized_projection_id, plan.desired_realized_projection_id})),
            points("raw-graph", ((workspace, graph) for graph, _ in graph_pairs)),
            points("raw-projection", ((workspace, projection, graph) for graph, projection in graph_pairs)),
            points("introduction", origins),
            points("origin-action", ((o.introducing_action_id, o.introducing_session_id, workspace) for o in origins.values())),
            points("acceptance-action", ((a.action_id, a.session_id) for a, *_ in receipts)),
            points("receipt-action", ((a.action_id,) for _, a, *_ in contexts)),
            points("receipt-event", ((e.event_id,) for _, _, e, *_ in contexts)),
            points("request", ((key,) for key in history_requests)), points("run", ((key,) for key in history_runs)),
            points("session", ((key,) for key in sessions)),
            points("header", ((h["workspace_id"], h["pinned_revision"]) for h, *_ in contexts)),
            collections("scopes", {(key,): value for key, value in scope_sets.items()}),
            collections("runs", {(key,): value for key, value in run_sets.items()}),
            collections("events", {(key,): value for key, value in event_sets.items()}),
            collections("advancement-actions", action_sets), collections("bindings", binding_sets),
            collections("slots", slots), collections("allocation-refs", allocation_sets),
            collections("allocation-claims", allocation_sets), collections("invocation-refs", invocation_sets))
        self._issued = issued
        self._require(issued, self._connection)
        return issued

    def capture_retained(self, guard, prefix, approved_plan, *, original_identity):
        """Bound a retained STARTED proof without re-authorizing fresh work.

        B owns the original, approval, registration identity, closed claims and
        whole invocation correspondence. Only its complete invocation-ref
        reader needs C bounds; no current selection or live permission is read.
        The later fold owner still owns all fencing, result checks and writes.
        """
        from control_plane_kit_core.operations import EffectAttemptIdentity, EffectAttemptStatus
        from .graph_store import WorkspaceLifecycleGuard
        _require(self._issued is None and not self._spent and _BOUND_CLEANUP_PHASE.get() is None)
        self._require_context(self._connection)
        _require(type(original_identity) is EffectAttemptIdentity and type(guard) is WorkspaceLifecycleGuard
            and guard._owner is self._stores.graphs)
        self._require_original_identity("plan", approved_plan.plan_id)
        self._require_original_identity("plan", prefix.request.identity.plan_id)
        self._require_original_identity("intent", (original_identity.run_id.value, original_identity.activity_id, original_identity.attempt))
        read = _active_read(self._connection)
        transaction = _transaction(read)
        _require(transaction == guard._transaction_id == self._original.transaction_id)
        prefix.require(self._uow, prefix.request, original_identity.run_id.value, latest_required=True)
        fresh = _EvidenceRead(self._connection)
        retained = self._stores.configuration_cleanup_ownership._get(original_identity, fresh)
        _require(retained is not None and retained.status is EffectAttemptStatus.STARTED)
        original = self._stores.effect_attempt_intents.get(original_identity)
        plan = self._stores.activity_history.get_plan(retained.plan_id)
        self._require_original_identities(plan, original_identity)
        _require(plan == approved_plan and plan.cleanup_proposal is not None
            and original.identity == retained.identity == original_identity
            and original.intent.source.request_id == retained.request_id == prefix.request.identity.request_id
            and original.intent.source.plan_id == retained.plan_id == plan.plan_id == prefix.request.identity.plan_id
            and original.intent.source.workspace_id == retained.workspace_id == guard.workspace_id
            and original.request_fingerprint == retained.request_fingerprint
            and original.original_start_event.event_id == retained.original_event_id)
        invocations = []
        for completion in retained.completions:
            identity = completion.identity
            key = (identity.run_id.value, identity.activity_id, identity.attempt)
            expected = tuple((claim.ref.artifact_id,) for claim in retained.claims if claim.identity == identity)
            _require(bool(expected))
            invocations.append(_capture_collection(read, "invocation-refs", key, expected))
        issued = _CleanupPhaseReadBounds(owner=self, transaction_id=transaction,
            original_plan=plan.plan_id, original_graphs=tuple(sorted({plan.base_graph_id, plan.desired_graph_id})),
            original_projections=tuple(sorted({plan.base_realized_projection_id, plan.desired_realized_projection_id})),
            retained_identity=(original_identity.run_id.value, original_identity.activity_id, original_identity.attempt),
            plans=(), graphs=(), projections=(), raw_graphs=(), raw_projections=(), introductions=(),
            origin_actions=(), acceptance_actions=(), receipt_actions=(), receipt_events=(), requests=(), runs=(),
            sessions=(), headers=(), scopes=(), run_histories=(), event_histories=(), advancement_actions=(),
            bindings=(), slots=(), allocation_refs=(), allocation_claims=(), invocation_refs=tuple(invocations))
        self._issued = issued
        self._require(issued, self._connection)
        return issued
