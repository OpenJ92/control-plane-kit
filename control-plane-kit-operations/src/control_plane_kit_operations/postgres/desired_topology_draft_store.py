"""Caller-transaction catalogue truth with tenant-scoped keyset reads."""
from __future__ import annotations

from control_plane_kit_operations.desired_topology_drafts import (
    DesiredTopologyDraftConflict, DesiredTopologyDraftRecord, DesiredTopologyDraftRevisionRecord,
)
from control_plane_kit_operations.read_pages import (
    ReadCollection, ReadPage, ReadPageCandidate, ReadPageError, ReadPageRequest,
    TemporalReadCursor, OrdinalReadCursor,
)
from .schema import PostgresConnection
from .temporal import (encode_postgres_timestamp, decode_postgres_timestamp,
    encode_postgres_cursor_timestamp, decode_postgres_cursor_timestamp)

_DRAFT = "workspace_id, draft_id, title, head_revision, created_by, created_at, deleted_by, deleted_at"
_REVISION = "workspace_id, draft_id, revision, graph_id, created_by, created_at"


def _draft(row):
    values = list(row)
    values[5] = decode_postgres_timestamp(values[5])
    values[7] = None if values[7] is None else decode_postgres_timestamp(values[7])
    return DesiredTopologyDraftRecord(*values)


def _revision(row):
    return DesiredTopologyDraftRevisionRecord(*row[:5], decode_postgres_timestamp(row[5]))


class PostgresDesiredTopologyDraftStore:
    def __init__(self, connection: PostgresConnection):
        self.connection = connection

    def create(self, record: DesiredTopologyDraftRecord) -> None:
        if record.head_revision != 1 or record.deleted_at is not None:
            raise DesiredTopologyDraftConflict("new draft must start at live revision one")
        self.connection.execute(
            "INSERT INTO cpk_desired_topology_drafts (" + _DRAFT + ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (record.workspace_id, record.draft_id, record.title, record.head_revision, record.created_by,
             encode_postgres_timestamp(record.created_at), record.deleted_by,
             None if record.deleted_at is None else encode_postgres_timestamp(record.deleted_at)),
        )

    def get(self, workspace_id: str, draft_id: str, *, for_update: bool = False) -> DesiredTopologyDraftRecord:
        row = self.connection.execute("SELECT " + _DRAFT + " FROM cpk_desired_topology_drafts "
            "WHERE workspace_id=%s AND draft_id=%s" + (" FOR UPDATE" if for_update else ""),
            (workspace_id, draft_id)).fetchone()
        if row is None:
            raise KeyError("draft was not found")
        return _draft(row)

    def append(self, record: DesiredTopologyDraftRevisionRecord, *, expected_head_revision: int | None) -> None:
        if expected_head_revision is not None:
            if record.revision != expected_head_revision + 1:
                raise DesiredTopologyDraftConflict("revision must extend the expected head")
            updated = self.connection.execute("UPDATE cpk_desired_topology_drafts SET head_revision=%s "
                "WHERE workspace_id=%s AND draft_id=%s AND head_revision=%s AND deleted_at IS NULL RETURNING draft_id",
                (record.revision, record.workspace_id, record.draft_id, expected_head_revision)).fetchone()
            if updated is None:
                raise DesiredTopologyDraftConflict("draft head is stale")
        elif record.revision != 1:
            raise DesiredTopologyDraftConflict("initial draft revision must be one")
        self.connection.execute("INSERT INTO cpk_desired_topology_draft_revisions (" + _REVISION + ") "
            "VALUES (%s,%s,%s,%s,%s,%s)", (record.workspace_id, record.draft_id, record.revision,
            record.graph_id, record.created_by, encode_postgres_timestamp(record.created_at)))

    def revision(self, workspace_id: str, draft_id: str, revision: int) -> DesiredTopologyDraftRevisionRecord:
        row = self.connection.execute("SELECT " + _REVISION + " FROM cpk_desired_topology_draft_revisions "
            "WHERE workspace_id=%s AND draft_id=%s AND revision=%s", (workspace_id, draft_id, revision)).fetchone()
        if row is None:
            raise KeyError("draft revision was not found")
        return _revision(row)

    def page(self, request: ReadPageRequest) -> ReadPage:
        cursor = request.cursor
        params = [request.scope.workspace_id]
        if request.collection is ReadCollection.DESIRED_TOPOLOGY_DRAFTS:
            query = "SELECT " + _DRAFT + " FROM cpk_desired_topology_drafts WHERE workspace_id=%s"
            if cursor is not None:
                query += " AND (created_at, draft_id) > (%s,%s)"
                params.extend((encode_postgres_cursor_timestamp(cursor.instant), cursor.item_id))
            query += " ORDER BY created_at, draft_id LIMIT %s"
            params.append(request.limit + 1)
            rows = self.connection.execute(query, tuple(params)).fetchall()
            candidates = tuple(ReadPageCandidate(_draft(row), TemporalReadCursor(request.collection,
                request.scope, decode_postgres_cursor_timestamp(row[5]), row[1])) for row in rows)
        elif request.collection is ReadCollection.DESIRED_TOPOLOGY_DRAFT_REVISIONS:
            query = "SELECT " + _REVISION + " FROM cpk_desired_topology_draft_revisions WHERE workspace_id=%s AND draft_id=%s"
            params.append(request.scope.draft_id)
            if cursor is not None:
                query += " AND (revision, graph_id) > (%s,%s)"
                params.extend((cursor.ordinal, cursor.item_id))
            query += " ORDER BY revision, graph_id LIMIT %s"
            params.append(request.limit + 1)
            rows = self.connection.execute(query, tuple(params)).fetchall()
            candidates = tuple(ReadPageCandidate(_revision(row), OrdinalReadCursor(request.collection,
                request.scope, row[2], row[3])) for row in rows)
        else:
            raise ReadPageError("unsupported catalogue read collection")
        return ReadPage.from_candidates(request, candidates)
