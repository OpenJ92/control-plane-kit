"""Bounded saved-intent projections; graph redaction stays with graph reads."""
from control_plane_kit_operations.desired_topology_drafts import DesiredTopologyDraftStore
from control_plane_kit_operations.read_pages import ReadCollection, ReadPageRequest, RevisionReadScope
from .revision_history import _RevisionHistoryReadProjection
from .errors import ReadModelError
from .models import FocusedDetailReadModel
from .workspace_graph import _redact_graph_descriptor, _decode_valid_graph
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC


class _DesiredTopologyDraftReadProjection:
    def __init__(self, require_workspace, graphs, store: DesiredTopologyDraftStore | None, history_store=None):
        self._workspace = require_workspace
        self._graphs = graphs
        self._store = store
        self._history = _RevisionHistoryReadProjection(require_workspace, history_store)

    def _require_store(self):
        if self._store is None:
            raise ReadModelError("draft catalogue is unavailable")
        return self._store

    def page(self, request: ReadPageRequest):
        self._workspace(request.scope.workspace_id)
        store = self._require_store()
        if request.collection is ReadCollection.DESIRED_TOPOLOGY_DRAFT_REVISIONS:
            try:
                store.get(request.scope.workspace_id, request.scope.draft_id)
            except KeyError:
                raise ReadModelError("missing draft") from None
        return store.page(request).map(lambda row: row.descriptor())

    def _revision_graph(self, workspace_id: str, draft_id: str, revision: int):
        self._workspace(workspace_id)
        store = self._require_store()
        try:
            record = store.revision(workspace_id, draft_id, revision)
            graph = self._graphs.get(record.graph_id)
        except KeyError:
            raise ReadModelError("missing draft revision") from None
        if graph.workspace_id != workspace_id:
            raise ReadModelError("invalid draft graph reference")
        _decode_valid_graph(DEFAULT_GRAPH_CODEC, graph.graph_descriptor)
        return record, graph.graph_descriptor

    def detail(self, workspace_id: str, draft_id: str, revision: int):
        record, descriptor = self._revision_graph(workspace_id, draft_id, revision)
        return FocusedDetailReadModel(workspace_id, "desired-topology-draft-revision",
            {**record.descriptor(), "graph_descriptor": _redact_graph_descriptor(descriptor),
             "history": self._history.presence(RevisionReadScope(workspace_id, draft_id, revision))})

    def metadata(self, workspace_id: str, draft_id: str, revision: int):
        record, descriptor = self._revision_graph(workspace_id, draft_id, revision)
        fields = ("artifact_id", "target_path", "media_type", "file_mode", "content_digest", "source_digest")
        files = {
            node_id: [{field: artifact[field] for field in fields} for artifact in node["configuration_artifacts"]]
            for node_id, node in sorted(descriptor["nodes"].items())
            if node.get("configuration_artifacts")
        }
        return FocusedDetailReadModel(workspace_id, "desired-topology-draft-revision-metadata", {
            "draft_id": record.draft_id, "revision": record.revision, "graph_id": record.graph_id,
            "configuration_artifacts": files,
        })
