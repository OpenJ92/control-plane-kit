"""Canonical run identity for pure control-plane languages."""

from __future__ import annotations

from dataclasses import dataclass

from control_plane_kit_core._run_identity import _is_canonical_run_identity


@dataclass(frozen=True, order=True, slots=True)
class RunId:
    value: str

    def __post_init__(self) -> None:
        if not _is_canonical_run_identity(self.value):
            raise ValueError("run id is malformed")


__all__ = ["RunId"]
