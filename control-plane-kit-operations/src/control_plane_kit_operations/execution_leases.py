"""Nominal authority for one durable execution-request lease."""

from __future__ import annotations

from dataclasses import dataclass


class InvalidExecutionLeaseFence(ValueError):
    """Raised when execution lease authority is malformed."""


@dataclass(frozen=True)
class ExecutionLeaseFence:
    """Worker identity plus the exact active request-claim generation."""

    worker_id: str
    generation: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.worker_id, str)
            or not self.worker_id
            or len(self.worker_id) > 512
            or any(ord(character) < 32 for character in self.worker_id)
        ):
            raise InvalidExecutionLeaseFence(
                "execution lease fence worker_id is invalid"
            )
        if (
            type(self.generation) is not int
            or self.generation < 1
            or self.generation > 2**63 - 1
        ):
            raise InvalidExecutionLeaseFence(
                "execution lease fence generation is invalid"
            )

    def descriptor(self) -> dict[str, object]:
        return {
            "worker_id": self.worker_id,
            "generation": self.generation,
        }


__all__ = [
    "ExecutionLeaseFence",
    "InvalidExecutionLeaseFence",
]
