"""Public read-model values shared across projection families."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


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
