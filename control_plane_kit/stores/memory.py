"""In-memory store implementations for tests and early examples."""

from __future__ import annotations

from dataclasses import replace

from control_plane_kit.stores.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityRunRecord,
    ApprovalRecord,
    GraphVersionRecord,
    InstanceRecord,
    ObservationRecord,
    OperationActionRecord,
    OperationSessionRecord,
    SecretReferenceRecord,
    WorkspaceLifecycle,
    WorkspaceRecord,
)


def _missing(kind: str, key: str) -> KeyError:
    return KeyError(f"missing {kind} {key!r}")


def _duplicate(kind: str, key: str) -> ValueError:
    return ValueError(f"duplicate {kind} {key!r}")


class InMemoryWorkspaceStore:
    """Workspace truth store backed by process memory."""

    def __init__(self) -> None:
        self._records: dict[str, WorkspaceRecord] = {}

    def create(self, record: WorkspaceRecord) -> WorkspaceRecord:
        if record.workspace_id in self._records:
            raise _duplicate("workspace", record.workspace_id)
        self._records[record.workspace_id] = record
        return record

    def get(self, workspace_id: str) -> WorkspaceRecord:
        try:
            return self._records[workspace_id]
        except KeyError as exc:
            raise _missing("workspace", workspace_id) from exc

    def set_lifecycle(self, workspace_id: str, lifecycle: WorkspaceLifecycle) -> WorkspaceRecord:
        return self._update(workspace_id, lifecycle=lifecycle)

    def set_current_graph(self, workspace_id: str, graph_id: str) -> WorkspaceRecord:
        return self._update(workspace_id, current_graph_id=graph_id)

    def set_desired_graph(self, workspace_id: str, graph_id: str) -> WorkspaceRecord:
        return self._update(workspace_id, desired_graph_id=graph_id)

    def _update(self, workspace_id: str, **changes: object) -> WorkspaceRecord:
        record = replace(self.get(workspace_id), **changes)
        self._records[workspace_id] = record
        return record


class InMemoryGraphTopologyStore:
    """Graph version store backed by process memory."""

    def __init__(self) -> None:
        self._records: dict[str, GraphVersionRecord] = {}

    def save(self, record: GraphVersionRecord) -> GraphVersionRecord:
        if record.graph_id in self._records:
            raise _duplicate("graph", record.graph_id)
        self._records[record.graph_id] = record
        return record

    def get(self, graph_id: str) -> GraphVersionRecord:
        try:
            return self._records[graph_id]
        except KeyError as exc:
            raise _missing("graph", graph_id) from exc

    def latest_for_workspace(self, workspace_id: str) -> GraphVersionRecord | None:
        records = [record for record in self._records.values() if record.workspace_id == workspace_id]
        if not records:
            return None
        return max(records, key=lambda record: record.version)


class InMemoryActivityHistoryStore:
    """Activity history store backed by process memory."""

    def __init__(self) -> None:
        self._sessions: dict[str, OperationSessionRecord] = {}
        self._actions: dict[str, OperationActionRecord] = {}
        self._approvals: dict[str, ApprovalRecord] = {}
        self._plans: dict[str, ActivityPlanRecord] = {}
        self._runs: dict[str, ActivityRunRecord] = {}
        self._events: dict[str, ActivityEventRecord] = {}

    def add_session(self, record: OperationSessionRecord) -> OperationSessionRecord:
        if record.session_id in self._sessions:
            raise _duplicate("session", record.session_id)
        self._sessions[record.session_id] = record
        return record

    def get_session(self, session_id: str) -> OperationSessionRecord:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise _missing("session", session_id) from exc

    def add_action(self, record: OperationActionRecord) -> OperationActionRecord:
        if record.action_id in self._actions:
            raise _duplicate("action", record.action_id)
        self._actions[record.action_id] = record
        return record

    def actions_for_session(self, session_id: str) -> tuple[OperationActionRecord, ...]:
        records = [record for record in self._actions.values() if record.session_id == session_id]
        return tuple(sorted(records, key=lambda record: record.ordinal))

    def add_approval(self, record: ApprovalRecord) -> ApprovalRecord:
        if record.approval_id in self._approvals:
            raise _duplicate("approval", record.approval_id)
        self._approvals[record.approval_id] = record
        return record

    def approvals_for_session(self, session_id: str) -> tuple[ApprovalRecord, ...]:
        records = [record for record in self._approvals.values() if record.session_id == session_id]
        return tuple(sorted(records, key=lambda record: record.decided_at))

    def add_plan(self, record: ActivityPlanRecord) -> ActivityPlanRecord:
        if record.plan_id in self._plans:
            raise _duplicate("plan", record.plan_id)
        self._plans[record.plan_id] = record
        return record

    def get_plan(self, plan_id: str) -> ActivityPlanRecord:
        try:
            return self._plans[plan_id]
        except KeyError as exc:
            raise _missing("plan", plan_id) from exc

    def add_run(self, record: ActivityRunRecord) -> ActivityRunRecord:
        if record.run_id in self._runs:
            raise _duplicate("run", record.run_id)
        self._runs[record.run_id] = record
        return record

    def add_event(self, record: ActivityEventRecord) -> ActivityEventRecord:
        if record.event_id in self._events:
            raise _duplicate("event", record.event_id)
        self._events[record.event_id] = record
        return record

    def events_for_run(self, run_id: str) -> tuple[ActivityEventRecord, ...]:
        records = [record for record in self._events.values() if record.run_id == run_id]
        return tuple(sorted(records, key=lambda record: record.ordinal))


class InMemoryObservedStateStore:
    """Observed-state store backed by process memory."""

    def __init__(self) -> None:
        self._records: dict[str, ObservationRecord] = {}

    def put(self, record: ObservationRecord) -> ObservationRecord:
        if record.observation_id in self._records:
            raise _duplicate("observation", record.observation_id)
        self._records[record.observation_id] = record
        return record

    def latest(self, workspace_id: str, subject_id: str) -> ObservationRecord | None:
        records = [
            record
            for record in self._records.values()
            if record.workspace_id == workspace_id and record.subject_id == subject_id
        ]
        if not records:
            return None
        return max(records, key=lambda record: record.observed_at)

    def history(self, workspace_id: str, subject_id: str) -> tuple[ObservationRecord, ...]:
        records = [
            record
            for record in self._records.values()
            if record.workspace_id == workspace_id and record.subject_id == subject_id
        ]
        return tuple(sorted(records, key=lambda record: record.observed_at))


class InMemoryInstanceRegistryStore:
    """Instance registry store backed by process memory."""

    def __init__(self) -> None:
        self._records: dict[str, InstanceRecord] = {}

    def register(self, record: InstanceRecord) -> InstanceRecord:
        if record.instance_id in self._records:
            raise _duplicate("instance", record.instance_id)
        self._records[record.instance_id] = record
        return record

    def get(self, instance_id: str) -> InstanceRecord:
        try:
            return self._records[instance_id]
        except KeyError as exc:
            raise _missing("instance", instance_id) from exc

    def set_lifecycle(self, instance_id: str, lifecycle: WorkspaceLifecycle) -> InstanceRecord:
        record = replace(self.get(instance_id), lifecycle=lifecycle)
        self._records[instance_id] = record
        return record

    def list_for_owner(self, owner_id: str) -> tuple[InstanceRecord, ...]:
        records = [record for record in self._records.values() if record.owner_id == owner_id]
        return tuple(sorted(records, key=lambda record: record.instance_id))


class InMemorySecretReferenceStore:
    """Secret reference store that never accepts or returns secret values."""

    def __init__(self) -> None:
        self._records: dict[str, SecretReferenceRecord] = {}

    def assign(self, record: SecretReferenceRecord) -> SecretReferenceRecord:
        if record.secret_ref in self._records:
            raise _duplicate("secret reference", record.secret_ref)
        self._records[record.secret_ref] = record
        return record

    def get(self, secret_ref: str) -> SecretReferenceRecord:
        try:
            return self._records[secret_ref]
        except KeyError as exc:
            raise _missing("secret reference", secret_ref) from exc

    def exists(self, secret_ref: str) -> bool:
        return secret_ref in self._records
