"""Postgres stores for workspace truth and graph versions."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from contextlib import contextmanager
from typing import Any

from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, GraphDescriptorError
from control_plane_kit_core.types import WorkspaceLifecycle
from control_plane_kit_operations.graph_authoring import GraphIdentityConflict
from control_plane_kit_operations.receiver_lifecycle import (
    ReceiverLifecycleStorageError, _require, derive_receiver_bindings,
)
from control_plane_kit_operations.postgres.schema import PostgresConnection
from control_plane_kit_operations.postgres.receiver_lifecycle_store import (
    _ReceiverStorage, _ReceiverAuthoringSnapshot, _validate_receiver_origin_action,
)
from control_plane_kit_operations.postgres.temporal import (
    decode_postgres_timestamp,
    encode_postgres_timestamp,
)
from control_plane_kit_operations.records import (
    GraphVersionRecord,
    RealizedGraphProjectionKind,
    RealizedGraphProjectionRecord,
    WorkspaceRecord,
)


class RealizedGraphProjectionConflict(ValueError):
    """Raised when one projection identity is reused for different material."""


class PostgresWorkspaceStore:
    """Postgres-backed workspace truth store."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    @contextmanager
    def _initialization_evidence(self, workspace_id):
        from control_plane_kit_operations._configuration_preparation import _configuration_accounting
        from control_plane_kit_operations.workspaces import WorkspaceCommandError
        from .configuration_evidence import _Capacity, _Unavailable
        try:
            with _configuration_accounting(("workspace-create", workspace_id), join=True):
                yield
        except (_Capacity, _Unavailable):
            pass
        else:
            return
        raise WorkspaceCommandError("workspace initialization evidence is unavailable") from None

    def _create_for_initialization(self, record, prepared):
        from control_plane_kit_operations.workspaces import _require_prepared_creation
        _require_prepared_creation(prepared, self._connection, record.workspace_id)
        command = prepared.command
        if record != WorkspaceRecord(command.workspace_id, command.name, metadata=command.metadata):
            raise ValueError("workspace initialization is incongruent")
        return self.create(record)

    def _set_initial_current_graph(self, prepared):
        from control_plane_kit_operations.workspaces import (
            _PreparedWorkspaceCreation, _require_prepared_creation, WorkspaceCommandError,
        )
        if type(prepared) is not _PreparedWorkspaceCreation:
            raise WorkspaceCommandError("workspace initialization requires original owner")
        workspace_id = prepared.command.workspace_id
        _require_prepared_creation(prepared, self._connection, workspace_id)
        workspace = self.get_for_update(workspace_id)
        if any(value is not None for value in (workspace.current_graph_id, workspace.desired_graph_id,
                workspace.current_realized_projection_id, workspace.desired_realized_projection_id)):
            raise ValueError("workspace initialization requires an unset pointer")
        # Use the existing identity projection owner once, in this same UoW.
        projection_id = self._projection_for_source(workspace_id, prepared.graph.graph_id, None)
        from .configuration_evidence import _EvidenceRead
        _EvidenceRead(self._connection).query("UPDATE cpk_workspaces SET current_graph_id=%s, "
            "current_realized_projection_id=%s WHERE workspace_id=%s RETURNING 1",
            (prepared.graph.graph_id, projection_id, workspace_id), records=1, octets=1, cells=1)
        return self.get(workspace_id)

    def _insert_workspace_initialization(self, receipt, prepared):
        from dataclasses import astuple
        from control_plane_kit_operations.workspaces import (
            _WorkspaceInitialization, _require_prepared_creation,
        )
        if type(receipt) is not _WorkspaceInitialization:
            raise ValueError("workspace initialization is invalid")
        _require_prepared_creation(prepared, self._connection, receipt.workspace_id)
        projection = prepared.stores.realized_graphs.get(receipt.initial_projection_id)
        workspace = self.get(receipt.workspace_id)
        if (receipt != prepared.receipt(projection)
                or (workspace.current_graph_id, workspace.current_realized_projection_id)
                != (receipt.initial_graph_id, receipt.initial_projection_id)):
            raise ValueError("workspace initialization is incongruent")
        from .configuration_evidence import _EvidenceRead
        _EvidenceRead(self._connection).query("INSERT INTO cpk_workspace_initializations "
            "(workspace_id,profile,initial_graph_id,initial_projection_id,graph_descriptor_sha256,"
            "projection_digest,configuration_slot_count,created_by,creation_idempotency_key) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (workspace_id) DO NOTHING RETURNING 1",
            astuple(receipt), records=1, octets=1, cells=1)
        if self._require_workspace_initialization(receipt.workspace_id) != receipt:
            raise ValueError("workspace initialization differs from its original")

    def _require_workspace_initialization(self, workspace_id):
        """Verify the immutable original, independently of today's pointer."""
        from control_plane_kit_operations.workspaces import WorkspaceCommandError
        try:
            return _read_workspace_initialization(self._connection, workspace_id)
        except (KeyError, ValueError, TypeError, AttributeError):
            pass
        raise WorkspaceCommandError("workspace initialization evidence is unavailable") from None

    def create(self, record: WorkspaceRecord) -> WorkspaceRecord:
        # A pointer-bearing bootstrap cannot borrow foreign receiver material.
        for graph_id, projection_id in (
            (record.current_graph_id, record.current_realized_projection_id),
            (record.desired_graph_id, record.desired_realized_projection_id),
        ):
            if graph_id is not None:
                graph = PostgresGraphTopologyStore(self._connection).get(graph_id)
                _require(graph.workspace_id == record.workspace_id)
                self._require_legacy_pointer_material(record.workspace_id, graph_id, projection_id)
        query = """
            INSERT INTO cpk_workspaces
              (workspace_id, name, lifecycle, current_graph_id, desired_graph_id,
               metadata, current_realized_projection_id,
               desired_realized_projection_id, desired_graph_revision)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
        values = (
                record.workspace_id,
                record.name,
                record.lifecycle.value,
                record.current_graph_id,
                record.desired_graph_id,
                Jsonb(record.metadata),
                record.current_realized_projection_id,
                record.desired_realized_projection_id,
                record.desired_graph_revision,
            )
        from .configuration_evidence import _active_read
        if (read := _active_read(self._connection)) is None:
            self._connection.execute(query, values)
        else:
            read.query(query + " RETURNING 1", values, records=1, octets=1, cells=1)
        return record

    def get(self, workspace_id: str) -> WorkspaceRecord:
        return self._get(workspace_id, for_update=False)

    def get_for_update(self, workspace_id: str) -> WorkspaceRecord:
        return self._get(workspace_id, for_update=True)

    def set_lifecycle(
        self,
        workspace_id: str,
        lifecycle: WorkspaceLifecycle,
    ) -> WorkspaceRecord:
        record = replace(self.get(workspace_id), lifecycle=lifecycle)
        self._connection.execute(
            "UPDATE cpk_workspaces SET lifecycle = %s WHERE workspace_id = %s",
            (lifecycle.value, workspace_id),
        )
        return record

    def set_current_graph(
        self,
        workspace_id: str,
        graph_id: str,
        realized_projection_id: str | None = None,
    ) -> WorkspaceRecord:
        self._require_legacy_pointer_change(workspace_id, ((graph_id, realized_projection_id),), configuration_free=True)
        projection_id = self._projection_for_source(
            workspace_id,
            graph_id,
            realized_projection_id,
        )
        self._connection.execute(
            """
            UPDATE cpk_workspaces
            SET current_graph_id = %s, current_realized_projection_id = %s
            WHERE workspace_id = %s
            """,
            (graph_id, projection_id, workspace_id),
        )
        return self.get(workspace_id)

    def compare_and_set_current_graph(
        self,
        workspace_id: str,
        *,
        expected_graph_id: str,
        replacement_graph_id: str,
        expected_realized_projection_id: str,
        replacement_realized_projection_id: str,
        expected_desired_graph_id: str,
        expected_desired_realized_projection_id: str,
        expected_desired_graph_revision: int,
    ) -> WorkspaceRecord | None:
        self._require_legacy_pointer_change(workspace_id, (
            (replacement_graph_id, replacement_realized_projection_id),
        ), configuration_free=True)
        return self._compare_and_set_current_graph(workspace_id, expected_graph_id=expected_graph_id,
            replacement_graph_id=replacement_graph_id,
            expected_realized_projection_id=expected_realized_projection_id,
            replacement_realized_projection_id=replacement_realized_projection_id,
            expected_desired_graph_id=expected_desired_graph_id,
            expected_desired_realized_projection_id=expected_desired_realized_projection_id,
            expected_desired_graph_revision=expected_desired_graph_revision)

    def _compare_and_set_current_graph(self, workspace_id, *, expected_graph_id,
            replacement_graph_id, expected_realized_projection_id, replacement_realized_projection_id,
            expected_desired_graph_id, expected_desired_realized_projection_id, expected_desired_graph_revision,
            prepared_receipt=None):
        """Private advancement write, following complete owner validation."""
        from control_plane_kit_operations._configuration_acceptance import _require_prepared_advancement
        if prepared_receipt is None:
            self._require_legacy_pointer_change(workspace_id,
                ((replacement_graph_id, replacement_realized_projection_id),), configuration_free=True)
        else:
            _require_prepared_advancement(prepared_receipt, self._connection, workspace_id)
        if prepared_receipt is not None and (expected_graph_id, replacement_graph_id, expected_realized_projection_id, replacement_realized_projection_id,
                expected_desired_graph_id, expected_desired_realized_projection_id, expected_desired_graph_revision) != (
                prepared_receipt.plan.base_graph_id, prepared_receipt.plan.desired_graph_id,
                prepared_receipt.current_projection.projection_id, prepared_receipt.desired_projection.projection_id,
                prepared_receipt.plan.desired_graph_id, prepared_receipt.desired_projection.projection_id,
                prepared_receipt.plan.desired_graph_revision):
            raise RealizedGraphProjectionConflict("current graph replacement differs from original owner")
        try:
            expected_projection_id = self._projection_for_source(
                workspace_id,
                expected_graph_id,
                expected_realized_projection_id,
            )
        except (KeyError, RealizedGraphProjectionConflict):
            return None
        replacement_projection_id = self._projection_for_source(
            workspace_id,
            replacement_graph_id,
            replacement_realized_projection_id,
        )
        desired_projection_id = self._projection_for_source(
            workspace_id,
            expected_desired_graph_id,
            expected_desired_realized_projection_id,
        )
        if (
            replacement_graph_id != expected_desired_graph_id
            or replacement_projection_id != desired_projection_id
        ):
            raise RealizedGraphProjectionConflict(
                "current graph replacement must be the exact desired lineage"
            )
        from .configuration_evidence import _active_read
        if (read := _active_read(self._connection)) is not None:
            rows = read.query("UPDATE cpk_workspaces SET current_graph_id=%s, current_realized_projection_id=%s "
                "WHERE workspace_id=%s AND current_graph_id=%s AND current_realized_projection_id=%s "
                "AND desired_graph_id=%s AND desired_realized_projection_id=%s AND desired_graph_revision=%s RETURNING 1",
                (replacement_graph_id, replacement_projection_id, workspace_id, expected_graph_id,
                    expected_projection_id, expected_desired_graph_id, desired_projection_id, expected_desired_graph_revision),
                records=1, octets=1, cells=1)
            return self.get(workspace_id) if rows else None
        row = self._connection.execute(
            """
            UPDATE cpk_workspaces
            SET current_graph_id = %s, current_realized_projection_id = %s
            WHERE workspace_id = %s
              AND current_graph_id = %s
              AND current_realized_projection_id = %s
              AND desired_graph_id = %s
              AND desired_realized_projection_id = %s
              AND desired_graph_revision = %s
            RETURNING workspace_id, name, lifecycle, current_graph_id,
                      desired_graph_id, metadata, current_realized_projection_id,
                      desired_realized_projection_id, desired_graph_revision
            """,
            (
                replacement_graph_id,
                replacement_projection_id,
                workspace_id,
                expected_graph_id,
                expected_projection_id,
                expected_desired_graph_id,
                desired_projection_id,
                expected_desired_graph_revision,
            ),
        ).fetchone()
        if row is None:
            return None
        return _workspace_record(row)

    def set_desired_graph(
        self,
        workspace_id: str,
        graph_id: str,
        realized_projection_id: str | None = None,
    ) -> WorkspaceRecord:
        self._require_legacy_pointer_change(workspace_id, ((graph_id, realized_projection_id),))
        return self._set_desired_graph(workspace_id, graph_id, realized_projection_id)

    def _set_desired_graph(self, workspace_id, graph_id, realized_projection_id):
        projection_id = self._projection_for_source(
            workspace_id,
            graph_id,
            realized_projection_id,
        )
        self._connection.execute(
            """
            UPDATE cpk_workspaces
            SET desired_graph_id = %s,
                desired_realized_projection_id = %s,
                desired_graph_revision = desired_graph_revision + 1
            WHERE workspace_id = %s
            """,
            (graph_id, projection_id, workspace_id),
        )
        return self.get(workspace_id)

    def compare_and_set_desired_projection(
        self,
        workspace_id: str,
        *,
        expected_authored_graph_id: str,
        expected_realized_projection_id: str,
        expected_revision: int,
        replacement_realized_projection_id: str,
    ) -> WorkspaceRecord | None:
        self._require_legacy_pointer_change(workspace_id, (
            (expected_authored_graph_id, expected_realized_projection_id),
            (expected_authored_graph_id, replacement_realized_projection_id),
        ))
        return self._compare_and_set_desired_projection(workspace_id,
            expected_authored_graph_id=expected_authored_graph_id,
            expected_realized_projection_id=expected_realized_projection_id,
            expected_revision=expected_revision,
            replacement_realized_projection_id=replacement_realized_projection_id)

    def _compare_and_set_desired_projection(self, workspace_id, *, expected_authored_graph_id,
            expected_realized_projection_id, expected_revision, replacement_realized_projection_id):
        replacement = self._projection_for_source(
            workspace_id,
            expected_authored_graph_id,
            replacement_realized_projection_id,
        )
        row = self._connection.execute(
            """
            UPDATE cpk_workspaces
            SET desired_realized_projection_id = %s,
                desired_graph_revision = desired_graph_revision + 1
            WHERE workspace_id = %s
              AND desired_graph_id = %s
              AND desired_realized_projection_id = %s
              AND desired_graph_revision = %s
            RETURNING workspace_id, name, lifecycle, current_graph_id,
                      desired_graph_id, metadata, current_realized_projection_id,
                      desired_realized_projection_id, desired_graph_revision
            """,
            (
                replacement,
                workspace_id,
                expected_authored_graph_id,
                expected_realized_projection_id,
                expected_revision,
            ),
        ).fetchone()
        return None if row is None else _workspace_record(row)

    def _require_legacy_pointer_material(self, workspace_id, graph_id, projection_id, *, configuration_free=False):
        if graph_id is None and projection_id is None:
            return
        graphs = PostgresGraphTopologyStore(self._connection)
        projections = PostgresRealizedGraphProjectionStore(self._connection)
        graph = graphs.get(graph_id)
        try:
            projection = (projections.identity_for_authored(workspace_id, graph_id)
                          if projection_id is None else projections.get(projection_id))
        except GraphDescriptorError as error:
            raise RealizedGraphProjectionConflict(
                "workspace graph pointer requires valid realized graph material") from error
        if not (graph.workspace_id == projection.workspace_id == workspace_id
                and projection.source_authored_graph_id == graph_id):
            raise RealizedGraphProjectionConflict("realized projection source does not match workspace graph")
        _require(not derive_receiver_bindings(workspace_id, graph_id, projection.projection_id,
                                             projection.graph_descriptor))
        _require(not derive_receiver_bindings(workspace_id, graph_id, projection.projection_id,
                                             graph.graph_descriptor))
        if configuration_free:
            for descriptor in (graph.graph_descriptor, projection.graph_descriptor):
                _require(not any(node.configuration_artifacts
                    for node in DEFAULT_GRAPH_CODEC.decode(descriptor).nodes.values()))

    def _require_legacy_pointer_change(self, workspace_id, proposed, *, configuration_free=False):
        # Public single-record writes never acquire lifecycle after workspace.
        PostgresGraphTopologyStore(self._connection).lock_receiver_lifecycle(workspace_id)
        workspace = self.get_for_update(workspace_id)
        for graph_id, projection_id in ((workspace.current_graph_id, workspace.current_realized_projection_id),
                (workspace.desired_graph_id, workspace.desired_realized_projection_id), *proposed):
            self._require_legacy_pointer_material(workspace_id, graph_id, projection_id,
                configuration_free=configuration_free)

    def _get(self, workspace_id: str, *, for_update: bool) -> WorkspaceRecord:
        from .configuration_evidence import _active_read
        if (read := _active_read(self._connection)) is not None:
            if for_update:
                read.query("SELECT 1 FROM cpk_workspaces WHERE workspace_id=%s FOR UPDATE",
                    (workspace_id,), records=1, octets=1, cells=1)
            names = ("workspace_id", "name", "lifecycle", "current_graph_id", "desired_graph_id",
                "metadata", "current_realized_projection_id", "desired_realized_projection_id", "desired_graph_revision")
            columns = tuple((name, "json" if name == "metadata" else "int" if name == "desired_graph_revision"
                else "text", 65536 if name == "metadata" else 2048) for name in names)
            rows = read.bounded_rows("cpk_workspaces", columns, "workspace_id=%s", (workspace_id,))
            if not rows:
                raise KeyError("missing workspace")
            return _workspace_record(rows[0])
        lock = " FOR UPDATE" if for_update else ""
        row = self._connection.execute(
            f"""
            SELECT workspace_id, name, lifecycle, current_graph_id, desired_graph_id,
                   metadata, current_realized_projection_id,
                   desired_realized_projection_id, desired_graph_revision
            FROM cpk_workspaces WHERE workspace_id = %s{lock}
            """,
            (workspace_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"missing workspace {workspace_id!r}")
        return _workspace_record(row)

    def _projection_for_source(
        self,
        workspace_id: str,
        authored_graph_id: str,
        projection_id: str | None,
    ) -> str:
        if projection_id is None:
            store = PostgresRealizedGraphProjectionStore(self._connection)
            try:
                identity = store.identity_for_authored(
                    workspace_id,
                    authored_graph_id,
                )
            except GraphDescriptorError as error:
                raise RealizedGraphProjectionConflict(
                    "workspace graph pointer requires valid realized graph material"
                ) from error
            row = (store.save(identity).projection_id,)
        else:
            from .configuration_evidence import _active_read
            if (read := _active_read(self._connection)) is not None:
                rows = read.bounded_rows("cpk_realized_graph_projections", (("projection_id", "text", 2048),),
                    "projection_id=%s AND workspace_id=%s AND source_authored_graph_id=%s",
                    (projection_id, workspace_id, authored_graph_id))
                if not rows:
                    raise RealizedGraphProjectionConflict("workspace graph pointer requires a matching realized projection")
                return rows[0][0]
            row = self._connection.execute(
                """
                SELECT projection_id
                FROM cpk_realized_graph_projections
                WHERE projection_id = %s
                  AND workspace_id = %s
                  AND source_authored_graph_id = %s
                """,
                (projection_id, workspace_id, authored_graph_id),
            ).fetchone()
        if row is None:
            raise RealizedGraphProjectionConflict(
                "workspace graph pointer requires a matching realized projection"
            )
        return str(row[0])


@dataclass(frozen=True, eq=False)
class WorkspaceLifecycleGuard:
    """Internal evidence of a guard held by one transaction-owned graph store."""

    workspace_id: str
    _owner: object = field(repr=False)
    _transaction_id: int = field(repr=False)


class PostgresGraphTopologyStore:
    """Postgres-backed immutable graph topology-version store."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection
        self._receivers = _ReceiverStorage(connection)

    def lock_receiver_lifecycle(self, workspace_id: str) -> WorkspaceLifecycleGuard:
        """Enter before existing rows; exact-key transaction reentry is legal."""
        from .configuration_evidence import _active_read
        if (read := _active_read(self._connection)) is not None:
            read.query("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"receiver-lifecycle:{workspace_id}",), records=1, octets=1, cells=1)
            transaction_id = read.query("SELECT txid_current()", (), records=1, octets=20, cells=1)[0][0]
            return WorkspaceLifecycleGuard(workspace_id, self, transaction_id)
        self._connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"receiver-lifecycle:{workspace_id}",),
        )
        transaction_id = self._connection.execute("SELECT txid_current()").fetchone()[0]
        return WorkspaceLifecycleGuard(workspace_id, self, transaction_id)

    def owns_receiver_lifecycle(self, guard: object, workspace_id: str) -> bool:
        return (
            type(guard) is WorkspaceLifecycleGuard
            and guard._owner is self
            and guard.workspace_id == workspace_id
        )

    def _require_receiver_lifecycle(self, guard: object, workspace_id: str) -> None:
        """Revalidate a held guard without acquiring lifecycle after row locks."""
        self._receivers.guard(self, guard, workspace_id)

    def receiver_introduction(self, workspace_id: str, receiver_id: str):
        return self._receivers.introduction(workspace_id, receiver_id)

    def receiver_bindings(self, workspace_id: str, graph_id: str, realized_projection_id: str):
        return self._receivers.bindings(workspace_id, graph_id, realized_projection_id)

    def receiver_authoring_snapshot(self):
        """Graph-owned bounded selectors for the caller's read-only snapshot."""
        return _ReceiverAuthoringSnapshot(self._connection)

    def _require_receiver_origin_action(self, origin):
        from control_plane_kit_operations.postgres.activity_history import _action_record
        from .configuration_evidence import _active_read
        read = _active_read(self._connection)
        if read is not None:
            names = ("action_id", "session_id", "ordinal", "action_type", "actor_id", "payload",
                     "created_at", "idempotency_key", "intent_fingerprint")
            columns = tuple(("a." + name, "json" if name == "payload" else "int" if name == "ordinal"
                else "time" if name == "created_at" else "text", 65536 if name == "payload" else 2048)
                for name in names)
            rows = read.bounded_rows("cpk_operation_actions a JOIN cpk_operation_sessions s ON s.session_id=a.session_id",
                columns, "a.action_id=%s AND a.session_id=%s AND s.workspace_id=%s",
                (origin.introducing_action_id, origin.introducing_session_id, origin.workspace_id), identities=2)
            row = rows[0] if rows else None
        else:
            row = self._connection.execute(
                "SELECT a.action_id,a.session_id,a.ordinal,a.action_type,a.actor_id,"
                "CASE WHEN octet_length(a.payload::text)<=65536 THEN a.payload END,"
                "a.created_at,a.idempotency_key,a.intent_fingerprint FROM cpk_operation_actions a "
                "JOIN cpk_operation_sessions s ON s.session_id=a.session_id "
                "WHERE a.action_id=%s AND a.session_id=%s AND s.workspace_id=%s",
                (origin.introducing_action_id, origin.introducing_session_id, origin.workspace_id),
            ).fetchone()
        _require(row is not None and row[5] is not None)
        action = _action_record(row)
        projection = PostgresRealizedGraphProjectionStore(self._connection).get(
            origin.introducing_realized_projection_id)
        graph = self.get(origin.introducing_graph_id)
        revision = _validate_receiver_origin_action(origin, action, graph, projection)
        if revision is not None:
            sql = ("SELECT EXISTS(SELECT 1 FROM cpk_desired_topology_draft_revisions "
                "WHERE workspace_id=%s AND draft_id=%s AND revision=%s AND graph_id=%s)")
            params = (origin.workspace_id, origin.introducing_draft_id, revision, graph.graph_id)
            row = (self._connection.execute(sql, params).fetchone() if read is None else
                read.query(sql, params, records=1, octets=1, cells=1)[0])
            _require(row == (True,))

    def _reserve_receiver_introductions(self, graph, projection, *, action_id, session_id,
                                       draft_id=None, lifecycle_guard):
        return self._receivers.reserve(self, graph, projection, action_id=action_id,
            session_id=session_id, draft_id=draft_id, lifecycle_guard=lifecycle_guard)

    def _reserve_new_receiver_introductions(self, graph, projection, *, action_id, session_id,
                                          new_receiver_ids, draft_id=None, lifecycle_guard):
        return self._receivers.reserve(self, graph, projection, action_id=action_id,
            session_id=session_id, draft_id=draft_id, lifecycle_guard=lifecycle_guard,
            new_receiver_ids=new_receiver_ids)

    def _persist_receiver_bindings(self, graph, projection, *, lifecycle_guard):
        return self._receivers.persist(self, graph, projection, lifecycle_guard=lifecycle_guard)

    def _record_receiver_first_acceptance(self, workspace_id, receiver_id, *, action_id,
                                         session_id, lifecycle_guard):
        return self._receivers.record_witness(self, workspace_id, receiver_id, action_id=action_id,
            session_id=session_id, lifecycle_guard=lifecycle_guard, retirement=False)

    def _record_receiver_retirement(self, workspace_id, receiver_id, *, action_id,
                                   session_id, lifecycle_guard):
        return self._receivers.record_witness(self, workspace_id, receiver_id, action_id=action_id,
            session_id=session_id, lifecycle_guard=lifecycle_guard, retirement=True)

    def _save_workspace_initialization(self, record, prepared):
        from control_plane_kit_operations.workspaces import _require_prepared_creation
        _require_prepared_creation(prepared, self._connection, record.workspace_id)
        if record != prepared.graph:
            raise ValueError("workspace initialization graph is incongruent")
        return self.save(record)

    def save(self, record: GraphVersionRecord) -> GraphVersionRecord:
        if derive_receiver_bindings(record.workspace_id, record.graph_id,
                                    "proposed-identity", record.graph_descriptor):
            if self._connection.execute("SELECT EXISTS(SELECT 1 FROM cpk_graph_versions WHERE graph_id=%s)",
                                        (record.graph_id,)).fetchone() == (True,):
                raise GraphIdentityConflict("graph identity is unavailable")
            _require(False)
        return self._save(record)

    def _save(self, record: GraphVersionRecord) -> GraphVersionRecord:
        encoded_created_at = encode_postgres_timestamp(record.created_at)
        query = """
                INSERT INTO cpk_graph_versions
                  (graph_id, workspace_id, version, graph_descriptor, created_by, created_at, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """
        values = (
                    record.graph_id,
                    record.workspace_id,
                    record.version,
                    Jsonb(record.graph_descriptor),
                    record.created_by,
                    encoded_created_at,
                    Jsonb(record.metadata),
                )
        from .configuration_evidence import _active_read
        try:
            if (read := _active_read(self._connection)) is None:
                self._connection.execute(query, values)
            else:
                read.query(query + " RETURNING 1", values, records=1, octets=1, cells=1)
        except UniqueViolation as error:
            if error.diag.constraint_name != "cpk_graph_versions_pkey":
                raise
        else:
            return record
        # Detach the database detail, including another workspace's graph name.
        # The caller's unit of work owns rollback of the failed transaction.
        raise GraphIdentityConflict("graph identity is unavailable")

    def get(self, graph_id: str) -> GraphVersionRecord:
        from .configuration_evidence import _active_read
        from .configuration_cleanup_read_ceilings import _cleanup_original_columns
        columns = _cleanup_original_columns(self._connection, "graph", graph_id,
            (("graph_id", "text", 2048), ("workspace_id", "text", 2048), ("version", "int", 32),
             ("graph_descriptor", "json", 1048576), ("created_by", "text", 2048),
             ("created_at", "time", 64), ("metadata", "json", 1048576)))
        if (read := _active_read(self._connection)) is not None:
            rows = read.bounded_rows("cpk_graph_versions", columns, "graph_id=%s", (graph_id,))
            if not rows:
                raise KeyError("graph is unavailable")
            return _graph_record(rows[0])
        row = self._connection.execute(
            """
            SELECT graph_id, workspace_id, version, graph_descriptor, created_by, created_at, metadata
            FROM cpk_graph_versions WHERE graph_id = %s
            """,
            (graph_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"missing graph {graph_id!r}")
        return _graph_record(row)

    def latest_for_workspace(self, workspace_id: str) -> GraphVersionRecord | None:
        row = self._connection.execute(
            """
            SELECT graph_id, workspace_id, version, graph_descriptor, created_by, created_at, metadata
            FROM cpk_graph_versions
            WHERE workspace_id = %s
            ORDER BY version DESC
            LIMIT 1
            """,
            (workspace_id,),
        ).fetchone()
        if row is None:
            return None
        return _graph_record(row)

    def next_version_for_workspace(self, workspace_id: str) -> int:
        row = self._connection.execute(
            """
            SELECT COALESCE(MAX(version), 0) + 1
            FROM cpk_graph_versions
            WHERE workspace_id = %s
            """,
            (workspace_id,),
        ).fetchone()
        return int(row[0])


class PostgresRealizedGraphProjectionStore:
    """Postgres-backed immutable authored-to-realized graph lineage."""

    def __init__(self, connection: PostgresConnection) -> None:
        self._connection = connection

    def save(
        self,
        record: RealizedGraphProjectionRecord,
    ) -> RealizedGraphProjectionRecord:
        try:
            authored = PostgresGraphTopologyStore(self._connection).get(record.source_authored_graph_id)
        except KeyError:
            raise RealizedGraphProjectionConflict(
                "realized projection source is not an authored graph in its workspace") from None
        if authored.workspace_id != record.workspace_id:
            raise RealizedGraphProjectionConflict(
                "realized projection source is not an authored graph in its workspace")
        if (derive_receiver_bindings(record.workspace_id, record.source_authored_graph_id,
                                     record.projection_id, record.graph_descriptor)
                or derive_receiver_bindings(record.workspace_id, record.source_authored_graph_id,
                                            record.projection_id, authored.graph_descriptor)):
            try:
                existing = self.get(record.projection_id)
            except KeyError:
                raise ReceiverLifecycleStorageError("receiver storage is unavailable") from None
            _require(existing == record)
            _ReceiverStorage(self._connection).bindings(record.workspace_id,
                record.source_authored_graph_id, record.projection_id)
            return existing
        return self._save(record)

    def _save(self, record: RealizedGraphProjectionRecord) -> RealizedGraphProjectionRecord:
        from .configuration_evidence import _active_read, _Unavailable
        encoded_created_at = encode_postgres_timestamp(record.created_at)
        read = _active_read(self._connection)
        if read is None:
            source = self._connection.execute(
                "SELECT workspace_id FROM cpk_graph_versions WHERE graph_id=%s",
                (record.source_authored_graph_id,)).fetchone()
        else:
            sources = read.bounded_rows("cpk_graph_versions", (("workspace_id", "text", 2048),),
                "graph_id=%s", (record.source_authored_graph_id,))
            source = sources[0] if sources else None
        if source is None or source[0] != record.workspace_id:
            raise RealizedGraphProjectionConflict(
                "realized projection source is not an authored graph in its workspace")
        query = """
            INSERT INTO cpk_realized_graph_projections
              (projection_id, workspace_id, source_authored_graph_id,
               projection_kind, projection_key, projection_digest,
               graph_descriptor, created_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """
        values = (record.projection_id, record.workspace_id, record.source_authored_graph_id,
            record.projection_kind.value, record.projection_key, record.projection_digest,
            Jsonb(record.graph_descriptor), record.created_by, encoded_created_at)
        if read is None:
            inserted = self._connection.execute(query + """
                RETURNING projection_id, workspace_id, source_authored_graph_id,
                          projection_kind, projection_key, projection_digest,
                          graph_descriptor, created_by, created_at
                """, values).fetchone()
            if inserted is not None:
                return _realized_graph_projection_record(inserted)
        else:
            # Reserve the mutation return before executing. The complete row is
            # subsequently read through this owner's bounded getter in the same UoW.
            inserted = read.query(query + " RETURNING CASE WHEN octet_length(projection_id)<=2048 "
                "THEN projection_id ELSE NULL END", values, records=1, octets=2048, cells=1)
            if inserted:
                if inserted[0][0] is None:
                    raise _Unavailable
                return self.get(inserted[0][0])
        existing = self._by_identity(workspace_id=record.workspace_id,
            source_authored_graph_id=record.source_authored_graph_id,
            projection_kind=record.projection_kind, projection_key=record.projection_key)
        if existing is None or existing.projection_digest != record.projection_digest:
            raise RealizedGraphProjectionConflict(
                "realized projection identity is already bound to different material")
        return existing
    def get(self, projection_id: str) -> RealizedGraphProjectionRecord:
        from .configuration_evidence import _active_read
        from .configuration_cleanup_read_ceilings import _cleanup_original_columns
        names = ("projection_id", "workspace_id", "source_authored_graph_id", "projection_kind",
            "projection_key", "projection_digest", "graph_descriptor", "created_by", "created_at")
        columns = _cleanup_original_columns(self._connection, "projection", projection_id,
            tuple((name, "json" if name == "graph_descriptor" else "time" if name == "created_at"
                else "text", 1048576 if name == "graph_descriptor" else 2048) for name in names))
        if (read := _active_read(self._connection)) is not None:
            rows = read.bounded_rows("cpk_realized_graph_projections", columns, "projection_id=%s", (projection_id,))
            if not rows:
                raise KeyError("realized graph projection is unavailable")
            return _realized_graph_projection_record(rows[0])
        row = self._connection.execute(
            """
            SELECT projection_id, workspace_id, source_authored_graph_id,
                   projection_kind, projection_key, projection_digest,
                   graph_descriptor, created_by, created_at
            FROM cpk_realized_graph_projections
            WHERE projection_id = %s
            """,
            (projection_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"missing realized graph projection {projection_id!r}")
        return _realized_graph_projection_record(row)

    def identity_for_authored(
        self,
        workspace_id: str,
        authored_graph_id: str,
    ) -> RealizedGraphProjectionRecord:
        existing = self._by_identity(workspace_id=workspace_id,
            source_authored_graph_id=authored_graph_id,
            projection_kind=RealizedGraphProjectionKind.IDENTITY, projection_key="identity")
        if existing is not None:
            return existing
        source = PostgresGraphTopologyStore(self._connection).get(authored_graph_id)
        if source.workspace_id != workspace_id:
            raise KeyError("authored graph is unavailable")
        return RealizedGraphProjectionRecord.identity_for_authored(authored_record=source)
    def _by_identity(
        self,
        *,
        workspace_id: str,
        source_authored_graph_id: str,
        projection_kind: RealizedGraphProjectionKind,
        projection_key: str,
    ) -> RealizedGraphProjectionRecord | None:
        from .configuration_evidence import _active_read
        if (read := _active_read(self._connection)) is not None:
            names = ("projection_id", "workspace_id", "source_authored_graph_id", "projection_kind",
                "projection_key", "projection_digest", "graph_descriptor", "created_by", "created_at")
            columns = tuple((name, "json" if name == "graph_descriptor" else "time" if name == "created_at"
                else "text", 1048576 if name == "graph_descriptor" else 2048) for name in names)
            rows = read.bounded_rows("cpk_realized_graph_projections", columns,
                "workspace_id=%s AND source_authored_graph_id=%s AND projection_kind=%s AND projection_key=%s",
                (workspace_id, source_authored_graph_id, projection_kind.value, projection_key))
            return _realized_graph_projection_record(rows[0]) if rows else None
        row = self._connection.execute(
            """
            SELECT projection_id, workspace_id, source_authored_graph_id,
                   projection_kind, projection_key, projection_digest,
                   graph_descriptor, created_by, created_at
            FROM cpk_realized_graph_projections
            WHERE workspace_id = %s
              AND source_authored_graph_id = %s
              AND projection_kind = %s
              AND projection_key = %s
            """,
            (
                workspace_id,
                source_authored_graph_id,
                projection_kind.value,
                projection_key,
            ),
        ).fetchone()
        if row is None:
            return None
        return _realized_graph_projection_record(row)


def _read_workspace_initialization(connection, workspace_id):
    from hashlib import sha256
    import rfc8785
    from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, DeploymentGraph
    from control_plane_kit_operations.workspaces import _WorkspaceInitialization
    from control_plane_kit_operations.workflows import IdempotencyKey
    from .configuration_evidence import _active_read, _EvidenceRead, _Unavailable

    read = _active_read(connection) or _EvidenceRead(connection, standalone=True)
    names = ("workspace_id", "profile", "initial_graph_id", "initial_projection_id",
        "graph_descriptor_sha256", "projection_digest", "configuration_slot_count",
        "created_by", "creation_idempotency_key")
    columns = tuple((name, "int" if name == "configuration_slot_count" else "text",
        32 if name == "configuration_slot_count" else 64 if name in
        ("profile", "graph_descriptor_sha256", "projection_digest") else 2048) for name in names)
    rows = read.bounded_rows("cpk_workspace_initializations", columns,
        "workspace_id=%s", (workspace_id,))
    if len(rows) != 1:
        raise _Unavailable
    receipt = _WorkspaceInitialization(*rows[0])
    IdempotencyKey(receipt.creation_idempotency_key)
    graph_columns = (("graph_id", "text", 2048), ("workspace_id", "text", 2048),
        ("version", "int", 32), ("graph_descriptor", "json", 1048576),
        ("created_by", "text", 2048), ("created_at", "time", 64), ("metadata", "json", 16384))
    graphs = read.bounded_rows("cpk_graph_versions", graph_columns,
        "graph_id=%s", (receipt.initial_graph_id,))
    projection_names = ("projection_id", "workspace_id", "source_authored_graph_id",
        "projection_kind", "projection_key", "projection_digest", "graph_descriptor",
        "created_by", "created_at")
    projection_columns = tuple((name, "json" if name == "graph_descriptor" else
        "time" if name == "created_at" else "text",
        1048576 if name == "graph_descriptor" else 2048) for name in projection_names)
    projections = read.bounded_rows("cpk_realized_graph_projections", projection_columns,
        "projection_id=%s", (receipt.initial_projection_id,))
    if len(graphs) != 1 or len(projections) != 1:
        raise _Unavailable
    graph, projection = _graph_record(graphs[0]), _realized_graph_projection_record(projections[0])
    if (receipt.workspace_id != workspace_id or receipt.profile != "workspace-initialization.v1"
            or type(receipt.configuration_slot_count) is not int or receipt.configuration_slot_count != 0
            or graph.workspace_id != workspace_id or graph.version != 1
            or DEFAULT_GRAPH_CODEC.decode(graph.graph_descriptor) != DeploymentGraph("empty")
            or receipt.graph_descriptor_sha256 != sha256(rfc8785.dumps(graph.graph_descriptor)).hexdigest()
            or graph.created_by != receipt.created_by
            or graph.metadata != {"bootstrap": "empty-current-graph",
                "idempotency_key": receipt.creation_idempotency_key}
            or projection != RealizedGraphProjectionRecord.identity_for_authored(authored_record=graph)
            or receipt.projection_digest != projection.projection_digest):
        raise _Unavailable
    return receipt


def _validate_workspace_initializations(connection):
    """Verify retained original facts in bounded keyset pages, without repair."""
    from .configuration_evidence import _EvidenceRead
    cursor = ""
    while True:
        read = _EvidenceRead(connection, standalone=True)
        rows = read.bounded_rows("cpk_workspace_initializations", (("workspace_id", "text", 2048),),
            "workspace_id>%s", (cursor,), maximum=32, order="workspace_id")
        if not rows:
            return
        for (workspace_id,) in rows:
            _read_workspace_initialization(connection, workspace_id)
        cursor = rows[-1][0]


def _workspace_record(row: tuple[Any, ...]) -> WorkspaceRecord:
    return WorkspaceRecord(
        workspace_id=row[0],
        name=row[1],
        lifecycle=WorkspaceLifecycle(row[2]),
        current_graph_id=row[3],
        desired_graph_id=row[4],
        metadata=row[5],
        current_realized_projection_id=row[6],
        desired_realized_projection_id=row[7],
        desired_graph_revision=row[8],
    )


def _graph_record(row: tuple[Any, ...]) -> GraphVersionRecord:
    return GraphVersionRecord(
        graph_id=row[0],
        workspace_id=row[1],
        version=row[2],
        graph_descriptor=row[3],
        created_by=row[4],
        created_at=decode_postgres_timestamp(row[5]),
        metadata=row[6],
    )


def _realized_graph_projection_record(
    row: tuple[Any, ...],
) -> RealizedGraphProjectionRecord:
    return RealizedGraphProjectionRecord(
        projection_id=row[0],
        workspace_id=row[1],
        source_authored_graph_id=row[2],
        projection_kind=RealizedGraphProjectionKind(row[3]),
        projection_key=row[4],
        projection_digest=row[5],
        graph_descriptor=row[6],
        created_by=row[7],
        created_at=decode_postgres_timestamp(row[8]),
    )
