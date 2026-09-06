"""Public read-model values shared across projection families."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class OperatorOverviewReadModel:
    """Closed envelope assembled only from bounded durable read projections."""

    workspace_id: str
    graphs: Mapping[str, object]
    workflow: Mapping[str, object]
    history: Mapping[str, object]
    next_action: Mapping[str, object]

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "kind": "operator-overview",
            "graphs": dict(self.graphs),
            "workflow": dict(self.workflow),
            "history": dict(self.history),
            "next_action": dict(self.next_action),
        }


@dataclass(frozen=True)
class FocusedDetailReadModel:
    workspace_id: str
    kind: str
    payload: Mapping[str, object]

    def descriptor(self) -> dict[str, object]:
        return {
            "workspace_id": self.workspace_id,
            "kind": self.kind,
            **dict(self.payload),
        }
