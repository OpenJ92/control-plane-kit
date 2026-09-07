"""Caller-transaction saved source facts and bounded retained-row verification."""
from __future__ import annotations

from control_plane_kit_operations.desired_topology_drafts import DesiredTopologyDraftRevisionRecord
from control_plane_kit_operations.records import SavedPreparationSourceRecord
from control_plane_kit_operations.saved_deployment_preparation import validate_saved_preparation_source
from .activity_history import _session_record
from .schema import PostgresConnection
from .temporal import decode_postgres_timestamp


class PostgresSavedPreparationSourceStore:
    def __init__(self, connection: PostgresConnection):
        self._connection = connection

    def get(self, workspace_id: str, session_id: str) -> SavedPreparationSourceRecord | None:
        for value in (workspace_id, session_id):
            if (type(value) is not str or not value.strip() or len(value) > 512
                    or any(ord(char) < 32 or ord(char) == 127 for char in value)):
                raise ValueError("saved preparation source scope is malformed")
        row = self._connection.execute(
            "SELECT session_id,workspace_id,CASE WHEN octet_length(draft_id)<=2048 THEN draft_id END,revision "
            "FROM cpk_saved_preparation_sources "
            "WHERE workspace_id=%s AND session_id=%s", (workspace_id, session_id)).fetchone()
        return None if row is None else SavedPreparationSourceRecord(*row)

    def insert(self, record: SavedPreparationSourceRecord) -> SavedPreparationSourceRecord:
        if type(record) is not SavedPreparationSourceRecord:
            raise ValueError("saved preparation source is malformed")
        self._connection.execute("INSERT INTO cpk_saved_preparation_sources "
            "(session_id,workspace_id,draft_id,revision) VALUES (%s,%s,%s,%s)",
            (record.session_id, record.workspace_id, record.draft_id, record.revision))
        return record


# Guard transport before Python decoding. Batches bound memory, not total scan time.
_BATCH_SIZE = 64
_TEXT_BYTES = 2048
_METADATA_BYTES = 65536


def _bounded_text(column):
    return f"CASE WHEN octet_length({column}) BETWEEN 1 AND {_TEXT_BYTES} THEN {column} END"


_SESSION_TEXT = ("session.session_id", "session.workspace_id", "session.actor_id", "session.title", "session.status")
_SOURCE_TEXT = ("source.session_id", "source.workspace_id", "source.draft_id")
_REVISION_TEXT = ("revision.workspace_id", "revision.draft_id", "revision.graph_id", "revision.created_by")
_SELECT = ",".join(_bounded_text(column) for column in _SESSION_TEXT) + "," + ",".join((
    "session.created_at", "session.closed_at",
    f"CASE WHEN jsonb_typeof(session.metadata)='object' AND octet_length(session.metadata::text)<={_METADATA_BYTES} THEN session.metadata END",
    _bounded_text("session.idempotency_key"), _bounded_text("session.intent_fingerprint"),
)) + "," + ",".join(_bounded_text(column) for column in _SOURCE_TEXT) + ",source.revision," + ",".join(
    _bounded_text(column) for column in _REVISION_TEXT) + ",revision.revision,revision.created_at"
_SCAN = """
SELECT """ + _SELECT + """
FROM cpk_operation_sessions AS session
LEFT JOIN cpk_saved_preparation_sources AS source ON source.session_id=session.session_id
LEFT JOIN cpk_desired_topology_draft_revisions AS revision
  ON (revision.workspace_id,revision.draft_id,revision.revision)=
     (source.workspace_id,source.draft_id,source.revision)
WHERE session.session_id > %s AND (
  source.session_id IS NOT NULL OR session.metadata ? 'deployment_prepare_source'
  OR EXISTS (SELECT 1 FROM jsonb_object_keys(
    CASE WHEN jsonb_typeof(session.metadata)='object' THEN session.metadata ELSE '{}'::jsonb END
  ) AS member(name) WHERE starts_with(member.name, 'deployment_prepare_saved_'))
)
ORDER BY session.session_id LIMIT %s
"""


def _validate_current_rows(connection: PostgresConnection) -> None:
    last_session = ""
    while True:
        rows = connection.execute(_SCAN, (last_session, _BATCH_SIZE)).fetchall()
        if not rows:
            return
        if len(rows) > _BATCH_SIZE:
            raise ValueError("saved preparation source batch is malformed")
        for row in rows:
            session = _session_record(row[:10])
            source = SavedPreparationSourceRecord(*row[10:14])
            revision = DesiredTopologyDraftRevisionRecord(
                row[14], row[15], row[18], row[16], row[17], decode_postgres_timestamp(row[19]))
            validate_saved_preparation_source(source, session, revision)
            last_session = session.session_id
