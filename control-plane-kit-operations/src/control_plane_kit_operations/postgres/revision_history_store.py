"""Set-based bounded revision history over one read-committed statement per page."""
from __future__ import annotations

from dataclasses import replace

from control_plane_kit_core.operations import ActivityRunStatus
from control_plane_kit_operations.desired_topology_drafts import DesiredTopologyDraftRevisionRecord
from control_plane_kit_operations.records import OperationSessionStatus, RetryIdentity, SavedPreparationSourceRecord
from control_plane_kit_operations.read_pages import (
    ReadCollection, ReadPage, ReadPageCandidate, ReadPageError, ReadPageRequest,
    RevisionReadScope, TemporalReadCursor, _canonical_instant, _general_identifier, _run_identifier,
)
from control_plane_kit_operations.revision_history import (
    HISTORY_SCOPE, historical_advancement, saved_revision_source,
)
from .activity_history import _action_record, _session_record
from .execution import _activity_event
from .temporal import decode_postgres_timestamp, encode_postgres_cursor_timestamp


_PREPARATIONS = ReadCollection.DESIRED_TOPOLOGY_DRAFT_REVISION_PREPARATIONS
_ATTEMPTS = ReadCollection.DESIRED_TOPOLOGY_DRAFT_REVISION_ATTEMPTS


def _text(column):
    return f"CASE WHEN octet_length({column}) BETWEEN 1 AND 2048 THEN {column} END"


def _blob(column):
    return f"CASE WHEN jsonb_typeof({column})='object' AND octet_length({column}::text)<=65536 THEN {column} END"


def _time(column):
    return f"to_char({column} AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')"


def _array(*columns):
    return "jsonb_build_array(" + ",".join(columns) + ")"


def _object(**columns):
    # Keys and column expressions are source constants, never request values.
    return "jsonb_build_object(" + ",".join(f"'{key}',{column}" for key, column in columns.items()) + ")"


def _action(alias):
    return _array(_text(alias + ".action_id"), _text(alias + ".session_id"), alias + ".ordinal",
        _text(alias + ".action_type"), _text(alias + ".actor_id"), _blob(alias + ".payload"),
        _time(alias + ".created_at"), _text(alias + ".idempotency_key"), _text(alias + ".intent_fingerprint"))


# The target relation is qualified per plan, independently of source membership.
# UNION deduplicates sessions before chronological seek/order/limit. NOT
# MATERIALIZED permits the planner to push exact session/plan predicates inward.
_RELATIONS = """
WITH revision AS (
  SELECT workspace_id,draft_id,revision,graph_id FROM cpk_desired_topology_draft_revisions
  WHERE workspace_id=%s AND draft_id=%s AND revision=%s
), target_plans AS NOT MATERIALIZED (
  SELECT p.*,s.workspace_id FROM revision v
  JOIN cpk_activity_plans p ON p.desired_graph_id=v.graph_id
  JOIN cpk_operation_sessions s ON s.session_id=p.session_id AND s.workspace_id=v.workspace_id
), associated_sessions AS (
  SELECT src.session_id FROM revision v JOIN cpk_saved_preparation_sources src
    ON (src.workspace_id,src.draft_id,src.revision)=(v.workspace_id,v.draft_id,v.revision)
  JOIN cpk_operation_sessions s ON (s.workspace_id,s.session_id)=(src.workspace_id,src.session_id)
  UNION SELECT session_id FROM target_plans
), target_requests AS NOT MATERIALIZED (
  SELECT req.* FROM target_plans p JOIN cpk_execution_requests req
    ON (req.workspace_id,req.session_id,req.plan_id)=(p.workspace_id,p.session_id,p.plan_id)
), target_runs AS NOT MATERIALIZED (
  SELECT r.*,req.session_id,req.workspace_id FROM target_requests req JOIN cpk_activity_runs r
    ON (r.request_id,r.plan_id)=(req.request_id,req.plan_id)
)
"""
_SOURCE_LINKED = "EXISTS(SELECT 1 FROM revision v JOIN cpk_saved_preparation_sources src ON " \
    "(src.workspace_id,src.draft_id,src.revision)=(v.workspace_id,v.draft_id,v.revision) WHERE src.session_id=s.session_id)"
_HAS_PLAN = "EXISTS(SELECT 1 FROM target_plans p WHERE p.session_id=s.session_id)"
_HAS_REQUEST = "EXISTS(SELECT 1 FROM target_requests req WHERE req.session_id=s.session_id)"
_HAS_RUN = "EXISTS(SELECT 1 FROM target_runs r WHERE r.session_id=s.session_id)"
_SESSION = _array(_text("s.session_id"), _text("s.workspace_id"), _text("s.actor_id"), _text("s.title"),
    _text("s.status"), _time("s.created_at"), _time("s.closed_at"), _blob("s.metadata"),
    _text("s.idempotency_key"), _text("s.intent_fingerprint"))
_SOURCE = "CASE WHEN src.session_id IS NOT NULL THEN " + _array(_text("src.session_id"),
    _text("src.workspace_id"), _text("src.draft_id"), "src.revision") + " END"
_REVISION = "CASE WHEN sr.graph_id IS NOT NULL THEN " + _array(_text("sr.workspace_id"),
    _text("sr.draft_id"), "sr.revision", _text("sr.graph_id"), _text("sr.created_by"), _time("sr.created_at")) + " END"
_EVIDENCE_JOINS = """
LEFT JOIN cpk_saved_preparation_sources src
  ON (src.session_id,src.workspace_id)=(s.session_id,s.workspace_id)
LEFT JOIN cpk_desired_topology_draft_revisions sr
  ON (sr.workspace_id,sr.draft_id,sr.revision)=(src.workspace_id,src.draft_id,src.revision)
LEFT JOIN cpk_operation_actions start_action ON start_action.session_id=s.session_id AND start_action.ordinal=1
"""
_COMMON = dict(session_id=_text("s.session_id"), workspace_id=_text("s.workspace_id"),
    source_linked=_SOURCE_LINKED, session=_SESSION, source=_SOURCE, source_revision=_REVISION,
    start="CASE WHEN start_action.action_id IS NOT NULL THEN " + _action("start_action") + " END")
_PREPARATION_ITEM = _object(**_COMMON, created_at=_time("s.created_at"), session_status=_text("s.status"),
    has_target_plan=_HAS_PLAN, target_execution_requests_present=_HAS_REQUEST, target_attempts_present=_HAS_RUN)
_PLAN = _object(base_graph_id=_text("p.base_graph_id"), base_realized_projection_id=_text("p.base_realized_projection_id"),
    desired_graph_id=_text("p.desired_graph_id"), desired_realized_projection_id=_text("p.desired_realized_projection_id"),
    desired_graph_revision="p.desired_graph_revision")
_EVENT = _array(_text("e.event_id"), _text("e.run_id"), "e.ordinal", _text("e.event_type"),
               _time("e.occurred_at"), _blob("e.payload"))
_ATTEMPT_ITEM = _object(**_COMMON, plan_id=_text("p.plan_id"), request_id=_text("c.request_id"),
    run_id=_text("c.run_id"), prior_run_id=_text("c.prior_run_id"), attempt="c.attempt",
    prior_present="c.prior_run_id IS NOT NULL", created_at=_time("c.created_at"), status=_text("c.status"), plan=_PLAN,
    prior="CASE WHEN prior.run_id IS NOT NULL THEN " + _array(_text("prior.run_id"), _text("prior.plan_id"),
        _text("prior.request_id"), "prior.attempt", _time("prior.created_at")) + " END",
    plan_tenant_valid="EXISTS(SELECT 1 FROM cpk_realized_graph_projections bp WHERE "
        "bp.projection_id=p.base_realized_projection_id AND bp.source_authored_graph_id=p.base_graph_id "
        "AND bp.workspace_id=s.workspace_id) AND dp.workspace_id=s.workspace_id",
    projection_digest=_text("dp.projection_digest"), events="events.items", actions="actions.items")
_RECEIPT_JOINS = """
LEFT JOIN cpk_activity_runs prior ON prior.run_id=c.prior_run_id
LEFT JOIN cpk_realized_graph_projections dp ON dp.projection_id=p.desired_realized_projection_id
LEFT JOIN LATERAL (
  SELECT jsonb_agg(evidence) AS items FROM (
    SELECT """ + _EVENT + """ AS evidence FROM cpk_activity_events e
    WHERE e.run_id=c.run_id AND e.event_type='current_graph_advanced'
    ORDER BY e.event_id LIMIT 2
  ) bounded
) events ON true
LEFT JOIN LATERAL (
  SELECT jsonb_agg(evidence) AS items FROM (
    SELECT """ + _action("a") + """ AS evidence FROM cpk_operation_actions a
    WHERE a.session_id=s.session_id AND a.payload->>'run_id'=c.run_id
      AND a.action_type='advance-current-graph'
    ORDER BY a.action_id LIMIT 2
  ) bounded
) actions ON true
"""


def _decode_optional(decoder, values, date_positions):
    if values is None:
        return None
    try:
        row = list(values)
        for position in date_positions:
            if row[position] is not None:
                row[position] = encode_postgres_cursor_timestamp(row[position])
        return decoder(row)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        return None


def _source(scope, row, *, plan=None):
    source = _decode_optional(lambda value: SavedPreparationSourceRecord(*value), row["source"], ())
    revision = _decode_optional(lambda value: DesiredTopologyDraftRevisionRecord(
        *value[:5], decode_postgres_timestamp(value[5])), row["source_revision"], (5,))
    session = _decode_optional(_session_record, row["session"], (5, 6))
    start = _decode_optional(_action_record, row["start"], (6,))
    return saved_revision_source(scope, source, session, revision, start, plan=plan)


def _receipt(scope, row):
    events, actions = [], []
    for values in row["events"] or ():
        event = _decode_optional(_activity_event, values, (4,))
        events.append(None if event is None else replace(event, occurred_at=values[4]))
    for values in row["actions"] or ():
        action = _decode_optional(_action_record, values, (6,))
        actions.append(None if action is None else replace(action, created_at=values[6]))
    return historical_advancement(workspace_id=scope.workspace_id, session_id=row["session_id"],
        plan_id=row["plan_id"], plan=row["plan"], request_id=row["request_id"], run_id=row["run_id"],
        projection_digest=row["projection_digest"], events=events, actions=actions)


class PostgresRevisionHistoryStore:
    def __init__(self, connection):
        self.connection = connection

    def _parent(self, scope):
        if type(scope) is not RevisionReadScope:
            raise ReadPageError("revision history scope is malformed")
        row = self.connection.execute("SELECT " + _text("graph_id") +
            " FROM cpk_desired_topology_draft_revisions WHERE workspace_id=%s AND draft_id=%s AND revision=%s",
            (scope.workspace_id, scope.draft_id, scope.revision)).fetchone()
        if row is None:
            raise KeyError("draft revision was not found")
        _general_identifier(row[0])
        return row[0]

    def presence(self, scope):
        self._parent(scope)
        row = self.connection.execute(_RELATIONS + "SELECT EXISTS(SELECT 1 FROM associated_sessions),"
            "EXISTS(SELECT 1 FROM target_runs)", (scope.workspace_id, scope.draft_id, scope.revision)).fetchone()
        return {"scope": HISTORY_SCOPE, "preparations_present": row[0], "attempts_present": row[1],
                "completeness": "association-records-only"}

    def page(self, request):
        if type(request) is not ReadPageRequest or request.collection not in (_PREPARATIONS, _ATTEMPTS):
            raise ReadPageError("revision history request is malformed")
        # Revalidate the closed request at the owner boundary as well as adapters.
        ReadPageRequest(request.collection, request.scope, request.limit, request.cursor)
        graph_id = self._parent(request.scope)
        preparation = request.collection is _PREPARATIONS
        identity = "session_id" if preparation else "run_id"
        relation = "cpk_operation_sessions" if preparation else "target_runs"
        membership = "JOIN associated_sessions member ON member.session_id=c.session_id" if preparation else ""
        params = [request.scope.workspace_id, request.scope.draft_id, request.scope.revision]
        seek = ""
        if request.cursor is not None:
            seek = f"WHERE (c.created_at,c.{identity}) > (%s,%s)"
            params.extend((encode_postgres_cursor_timestamp(request.cursor.instant), request.cursor.item_id))
        params.append(request.limit + 1)
        candidates = f", candidates AS MATERIALIZED (SELECT c.* FROM {relation} c {membership} {seek} " \
            f"ORDER BY c.created_at,c.{identity} LIMIT %s) "
        if preparation:
            query = _RELATIONS + candidates + "SELECT " + _PREPARATION_ITEM + \
                " FROM candidates s " + _EVIDENCE_JOINS + " ORDER BY s.created_at,s.session_id"
        else:
            query = _RELATIONS + candidates + "SELECT " + _ATTEMPT_ITEM + \
                " FROM candidates c JOIN cpk_operation_sessions s ON (s.session_id,s.workspace_id)=" \
                "(c.session_id,c.workspace_id) JOIN target_plans p ON p.plan_id=c.plan_id " + \
                _EVIDENCE_JOINS + _RECEIPT_JOINS + " ORDER BY c.created_at,c.run_id"
        rows = self.connection.execute(query, tuple(params)).fetchall()
        projected = []
        for (row,) in rows:
            try:
                item = self._preparation(request.scope, row) if preparation else self._attempt(request.scope, row, graph_id)
                cursor = TemporalReadCursor(request.collection, request.scope, item["created_at"], item[identity])
            except (ValueError, TypeError, KeyError, IndexError, AttributeError):
                raise ReadPageError("revision history evidence is malformed") from None
            projected.append(ReadPageCandidate(item, cursor))
        page = ReadPage.from_candidates(request, tuple(projected))
        page.descriptor()  # Enforce the owner cap even for direct service callers.
        return page

    @staticmethod
    def _common(scope, row):
        _general_identifier(row["session_id"])
        _canonical_instant(row["created_at"])
        if row["workspace_id"] != scope.workspace_id or type(row["source_linked"]) is not bool:
            raise ReadPageError("revision history identity is incongruent")

    @classmethod
    def _preparation(cls, scope, row):
        cls._common(scope, row)
        status = OperationSessionStatus(row["session_status"])
        for key in ("has_target_plan", "target_execution_requests_present", "target_attempts_present"):
            if type(row[key]) is not bool:
                raise ReadPageError("revision history presence is malformed")
        return {"session_id": row["session_id"], "created_at": row["created_at"], "session_status": status.value,
            "association": {"source_linked": row["source_linked"], "has_target_plan": row["has_target_plan"]},
            "source": _source(scope, row), "target_plans_present": row["has_target_plan"],
            "target_execution_requests_present": row["target_execution_requests_present"],
            "target_attempts_present": row["target_attempts_present"]}

    @classmethod
    def _attempt(cls, scope, row, graph_id):
        cls._common(scope, row)
        for key in ("plan_id", "request_id"):
            _general_identifier(row[key])
        _run_identifier(row["run_id"])
        if row["prior_present"] and row["prior_run_id"] is None:
            raise ReadPageError("revision history prior identity is malformed")
        retry = RetryIdentity(row["attempt"], row["prior_run_id"])
        if retry.prior_run_id is not None:
            prior = row["prior"]
            if prior is None or tuple(prior[:4]) != (retry.prior_run_id, row["plan_id"], row["request_id"], retry.attempt - 1):
                raise ReadPageError("revision history retry lineage is incongruent")
            _canonical_instant(prior[4])
            if prior[4] > row["created_at"]:
                raise ReadPageError("revision history retry chronology is incongruent")
        plan = row["plan"]
        for key in ("base_graph_id", "base_realized_projection_id", "desired_graph_id", "desired_realized_projection_id"):
            _general_identifier(plan[key])
        if (not row["plan_tenant_valid"] or plan["desired_graph_id"] != graph_id
                or type(plan["desired_graph_revision"]) is not int or not 0 <= plan["desired_graph_revision"] <= 2**63 - 1):
            raise ReadPageError("revision history plan is incongruent")
        status = ActivityRunStatus(row["status"])
        return {"session_id": row["session_id"], "plan_id": row["plan_id"], "request_id": row["request_id"],
            "run_id": row["run_id"], "prior_run_id": retry.prior_run_id, "attempt": retry.attempt,
            "created_at": row["created_at"], "status": status.value,
            "association": {"source_linked": row["source_linked"], "target_graph_matches": True},
            "source": _source(scope, row, plan=plan), "plan": plan, "advancement": _receipt(scope, row)}
