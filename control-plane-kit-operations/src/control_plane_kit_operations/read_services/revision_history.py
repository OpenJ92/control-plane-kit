"""Revision history read service boundary; no durable semantics in adapters."""
from control_plane_kit_operations.revision_history import RevisionHistoryStore
from .errors import ReadModelError


class _RevisionHistoryReadProjection:
    def __init__(self, require_workspace, store: RevisionHistoryStore | None):
        self._workspace = require_workspace
        self._store = store

    def _require_store(self):
        if self._store is None:
            raise ReadModelError("revision history is unavailable")
        return self._store

    def page(self, request):
        self._workspace(request.scope.workspace_id)
        try:
            return self._require_store().page(request)
        except KeyError:
            raise ReadModelError("missing draft revision") from None

    def presence(self, scope):
        try:
            return self._require_store().presence(scope)
        except KeyError:
            raise ReadModelError("missing draft revision") from None
