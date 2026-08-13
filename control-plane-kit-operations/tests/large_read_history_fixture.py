from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LargeReadHistoryHandles:
    activity_workspace_id: str
    open_workspace_id: str
    actions_workspace_id: str
    actions_session_id: str
    plans_workspace_id: str
    plans_session_id: str
    approvals_workspace_id: str
    approvals_session_id: str
    pending_workspace_id: str
    pending_session_id: str
    runs_workspace_id: str
    runs_plan_id: str
    events_workspace_id: str
    events_run_id: str
    observations_workspace_id: str
    runtime_authorities_workspace_id: str
    runtime_deliveries_workspace_id: str
    ingress_authorities_workspace_id: str
    secret_providers_workspace_id: str
    secret_references_workspace_id: str
    delegation_keys_workspace_id: str
    gateway_probes_workspace_id: str


def seed_large_read_history(
    connection: object,
    *,
    selected_count: int = 201,
) -> LargeReadHistoryHandles:
    del connection, selected_count
    raise NotImplementedError("large read history baseline is not implemented")

