"""Activity AST used to move one graph toward another."""

from __future__ import annotations

from dataclasses import dataclass


class Activity:
    """Base class for planned work.

    Activities are values, not effects.  A runtime interpreter decides whether
    ``StartNode`` means ``docker run``, ``kubectl apply``, ``terraform apply``,
    or simply a line in a dry-run plan.
    """

    def to_text(self) -> str:
        """Return one readable line."""

        raise NotImplementedError


@dataclass(frozen=True)
class StartNode(Activity):
    """Start, create, or register a node."""

    node_id: str

    def to_text(self) -> str:
        return f"StartNode({self.node_id})"


@dataclass(frozen=True)
class StopNode(Activity):
    """Stop, remove, or retire a node."""

    node_id: str
    policy: str = "after_verification"

    def to_text(self) -> str:
        return f"StopNode({self.node_id}, policy={self.policy})"


@dataclass(frozen=True)
class SwitchEdge(Activity):
    """Change a mutable edge from one target to another."""

    edge_id: str
    source: str
    before_target: str
    after_target: str

    def to_text(self) -> str:
        return (
            f"SwitchEdge({self.edge_id}: "
            f"{self.before_target} -> {self.after_target})"
        )


@dataclass(frozen=True)
class AddEdge(Activity):
    """Create a new edge."""

    edge_id: str
    source: str
    target: str

    def to_text(self) -> str:
        return f"AddEdge({self.edge_id}: {self.source} -> {self.target})"


@dataclass(frozen=True)
class RemoveEdge(Activity):
    """Remove an existing edge."""

    edge_id: str
    source: str
    target: str

    def to_text(self) -> str:
        return f"RemoveEdge({self.edge_id}: {self.source} -> {self.target})"


@dataclass(frozen=True)
class VerifyNode(Activity):
    """Verify a node after startup or mutation."""

    node_id: str
    check: str = "health"

    def to_text(self) -> str:
        return f"VerifyNode({self.node_id}, check={self.check})"


@dataclass(frozen=True)
class ActivityPlan:
    """A linearized first version of an activity graph."""

    activities: tuple[Activity, ...]

    def to_text(self) -> str:
        """Return a numbered execution plan."""

        if not self.activities:
            return "No activities."
        return "\n".join(
            f"{index}. {activity.to_text()}"
            for index, activity in enumerate(self.activities, start=1)
        )
