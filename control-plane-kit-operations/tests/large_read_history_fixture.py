from __future__ import annotations

from dataclasses import dataclass
import json

from tests.graph_lineage_fixture import seed_identity_graphs

from control_plane_kit_core.planning import ActivityPlan, DEFAULT_ACTIVITY_PLAN_CODEC
from control_plane_kit_operations.postgres import PostgresStoreBundle


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
    if type(selected_count) is not int or selected_count < 1:
        raise ValueError("selected_count must be a positive integer")

    handles = LargeReadHistoryHandles(
        activity_workspace_id="workspace-activity",
        open_workspace_id="workspace-open",
        actions_workspace_id="workspace-actions",
        actions_session_id="actions-parent",
        plans_workspace_id="workspace-plans",
        plans_session_id="plans-parent",
        approvals_workspace_id="workspace-approvals",
        approvals_session_id="approvals-parent",
        pending_workspace_id="workspace-pending",
        pending_session_id="pending-parent",
        runs_workspace_id="workspace-runs",
        runs_plan_id="runs-parent-plan",
        events_workspace_id="workspace-events",
        events_run_id="events-parent-run",
        observations_workspace_id="workspace-observations",
        runtime_authorities_workspace_id="workspace-runtime-authorities",
        runtime_deliveries_workspace_id="workspace-runtime-deliveries",
        ingress_authorities_workspace_id="workspace-ingress-authorities",
        secret_providers_workspace_id="workspace-secret-providers",
        secret_references_workspace_id="workspace-secret-references",
        delegation_keys_workspace_id="workspace-delegation-keys",
        gateway_probes_workspace_id="workspace-gateway-probes",
    )
    workspace_ids = tuple(
        value
        for field in handles.__dataclass_fields__
        if field.endswith("workspace_id")
        for value in (getattr(handles, field),)
    )
    connection.execute(
        """
        INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
        SELECT value, value, 'running'
        FROM unnest(%s::text[]) AS value
        """,
        (list(workspace_ids),),
    )

    _seed_sessions(connection, handles, selected_count)
    _seed_actions(connection, handles, selected_count)
    _seed_plans(connection, handles, selected_count)
    _seed_approvals(connection, handles, selected_count)
    _seed_pending_approvals(connection, handles, selected_count)
    _seed_runs(connection, handles, selected_count)
    _seed_events(connection, handles, selected_count)
    _seed_current_metadata(connection, handles, selected_count)
    _seed_delegation_keys(connection, handles, selected_count)
    _seed_gateway_probes(connection, handles, selected_count)
    return handles


_INSTANT = "2026-08-12T12:00:00Z"
_EPOCH = 1_786_534_400
_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=
-----END PUBLIC KEY-----
"""


def _seed_sessions(connection: object, handles: LargeReadHistoryHandles, count: int) -> None:
    for workspace_id, prefix in (
        (handles.activity_workspace_id, "activity-session"),
        (handles.open_workspace_id, "open-session"),
    ):
        connection.execute(
            """
            INSERT INTO cpk_operation_sessions
              (session_id, workspace_id, actor_id, title, status, created_at)
            SELECT %s || '-' || lpad(value::text, 4, '0'), %s, 'operator',
                   'Synthetic session', 'open', %s::timestamptz
            FROM generate_series(1, %s) AS value
            """,
            (prefix, workspace_id, _INSTANT, count),
        )


def _parent_session(connection: object, session_id: str, workspace_id: str) -> None:
    connection.execute(
        """
        INSERT INTO cpk_operation_sessions
          (session_id, workspace_id, actor_id, title, status, created_at)
        VALUES (%s, %s, 'operator', 'Synthetic parent', 'open', %s)
        """,
        (session_id, workspace_id, _INSTANT),
    )


def _graphs(connection: object, workspace_id: str, prefix: str) -> tuple[str, str, str, str]:
    base_graph = f"{prefix}-base-graph"
    desired_graph = f"{prefix}-desired-graph"
    lineage = seed_identity_graphs(
        PostgresStoreBundle(connection),
        workspace_id=workspace_id,
        graph_ids=(base_graph, desired_graph),
    )
    return base_graph, desired_graph, lineage[base_graph], lineage[desired_graph]


def _plan_payload() -> str:
    return json.dumps(DEFAULT_ACTIVITY_PLAN_CODEC.encode(ActivityPlan(())))


def _seed_actions(connection: object, handles: LargeReadHistoryHandles, count: int) -> None:
    _parent_session(connection, handles.actions_session_id, handles.actions_workspace_id)
    connection.execute(
        """
        INSERT INTO cpk_operation_actions
          (action_id, session_id, ordinal, action_type, actor_id, payload, created_at)
        SELECT 'action-' || lpad(value::text, 4, '0'), %s, value,
               'record-operation-action', 'operator', '{}'::jsonb, %s::timestamptz
        FROM generate_series(1, %s) AS value
        """,
        (handles.actions_session_id, _INSTANT, count),
    )


def _seed_plans(connection: object, handles: LargeReadHistoryHandles, count: int) -> None:
    _parent_session(connection, handles.plans_session_id, handles.plans_workspace_id)
    base, desired, base_projection, desired_projection = _graphs(
        connection, handles.plans_workspace_id, "plans"
    )
    connection.execute(
        """
        INSERT INTO cpk_activity_plans
          (plan_id, session_id, base_graph_id, desired_graph_id,
           base_realized_projection_id, desired_realized_projection_id,
           status, created_at, payload)
        SELECT 'plan-' || lpad(value::text, 4, '0'), %s, %s, %s, %s, %s,
               'planned', %s::timestamptz, %s::jsonb
        FROM generate_series(1, %s) AS value
        """,
        (
            handles.plans_session_id,
            base,
            desired,
            base_projection,
            desired_projection,
            _INSTANT,
            _plan_payload(),
            count,
        ),
    )


def _one_plan(
    connection: object,
    *,
    workspace_id: str,
    session_id: str,
    plan_id: str,
    prefix: str,
) -> None:
    _parent_session(connection, session_id, workspace_id)
    base, desired, base_projection, desired_projection = _graphs(
        connection, workspace_id, prefix
    )
    connection.execute(
        """
        INSERT INTO cpk_activity_plans
          (plan_id, session_id, base_graph_id, desired_graph_id,
           base_realized_projection_id, desired_realized_projection_id,
           status, created_at, payload)
        VALUES (%s, %s, %s, %s, %s, %s, 'planned', %s, %s::jsonb)
        """,
        (
            plan_id,
            session_id,
            base,
            desired,
            base_projection,
            desired_projection,
            _INSTANT,
            _plan_payload(),
        ),
    )


def _approval_requests(
    connection: object,
    *,
    prefix: str,
    session_id: str,
    plan_id: str,
    count: int,
) -> None:
    connection.execute(
        """
        INSERT INTO cpk_approval_requests
          (request_id, session_id, plan_id, subject_kind, subject_payload,
           review_digest, requested_by, requested_at, required_scope,
           max_risk, destructive)
        SELECT %s || '-' || lpad(value::text, 4, '0'), %s, %s,
               'activity-plan',
               jsonb_build_object('kind', 'activity-plan', 'plan_id', %s),
               repeat('a', 64), 'operator', %s::timestamptz,
               'plan:approve', 'low', false
        FROM generate_series(1, %s) AS value
        """,
        (prefix, session_id, plan_id, plan_id, _INSTANT, count),
    )


def _seed_approvals(connection: object, handles: LargeReadHistoryHandles, count: int) -> None:
    plan_id = "approvals-parent-plan"
    _one_plan(
        connection,
        workspace_id=handles.approvals_workspace_id,
        session_id=handles.approvals_session_id,
        plan_id=plan_id,
        prefix="approvals",
    )
    _approval_requests(
        connection,
        prefix="approval-request",
        session_id=handles.approvals_session_id,
        plan_id=plan_id,
        count=count,
    )


def _seed_pending_approvals(
    connection: object,
    handles: LargeReadHistoryHandles,
    count: int,
) -> None:
    plan_id = "pending-parent-plan"
    _one_plan(
        connection,
        workspace_id=handles.pending_workspace_id,
        session_id=handles.pending_session_id,
        plan_id=plan_id,
        prefix="pending",
    )
    _approval_requests(
        connection,
        prefix="pending-request",
        session_id=handles.pending_session_id,
        plan_id=plan_id,
        count=count,
    )


def _seed_runs(connection: object, handles: LargeReadHistoryHandles, count: int) -> None:
    session_id = "runs-parent-session"
    _one_plan(
        connection,
        workspace_id=handles.runs_workspace_id,
        session_id=session_id,
        plan_id=handles.runs_plan_id,
        prefix="runs",
    )
    _approval_requests(
        connection,
        prefix="runs-approval",
        session_id=session_id,
        plan_id=handles.runs_plan_id,
        count=count,
    )
    connection.execute(
        """
        INSERT INTO cpk_approval_decisions
          (decision_id, request_id, actor_id, decision, scope, decided_at)
        SELECT 'runs-decision-' || lpad(value::text, 4, '0'),
               'runs-approval-' || lpad(value::text, 4, '0'),
               'reviewer', 'approved', 'plan:approve', %s::timestamptz
        FROM generate_series(1, %s) AS value;
        INSERT INTO cpk_execution_requests
          (request_id, workspace_id, session_id, plan_id, status, requested_by,
           requested_at, approval_request_id, approval_decision_id,
           idempotency_key, intent_fingerprint)
        SELECT 'runs-execution-' || lpad(value::text, 4, '0'), %s, %s, %s,
               'cancelled', 'operator', %s::timestamptz,
               'runs-approval-' || lpad(value::text, 4, '0'),
               'runs-decision-' || lpad(value::text, 4, '0'),
               'runs-execution-' || lpad(value::text, 4, '0'),
               'runs-fingerprint-' || lpad(value::text, 4, '0')
        FROM generate_series(1, %s) AS value;
        INSERT INTO cpk_activity_runs
          (run_id, plan_id, request_id, attempt, status, created_at,
           started_at, settled_at, metadata)
        SELECT 'run-' || lpad(value::text, 4, '0'), %s,
               'runs-execution-' || lpad(value::text, 4, '0'), 1,
               'succeeded', %s::timestamptz, %s::timestamptz,
               %s::timestamptz, '{}'::jsonb
        FROM generate_series(1, %s) AS value
        """,
        (
            _INSTANT,
            count,
            handles.runs_workspace_id,
            session_id,
            handles.runs_plan_id,
            _INSTANT,
            count,
            handles.runs_plan_id,
            _INSTANT,
            _INSTANT,
            _INSTANT,
            count,
        ),
    )


def _seed_events(connection: object, handles: LargeReadHistoryHandles, count: int) -> None:
    session_id = "events-parent-session"
    plan_id = "events-parent-plan"
    _one_plan(
        connection,
        workspace_id=handles.events_workspace_id,
        session_id=session_id,
        plan_id=plan_id,
        prefix="events",
    )
    _approval_requests(
        connection,
        prefix="events-approval",
        session_id=session_id,
        plan_id=plan_id,
        count=1,
    )
    connection.execute(
        """
        INSERT INTO cpk_approval_decisions
          (decision_id, request_id, actor_id, decision, scope, decided_at)
        VALUES ('events-decision', 'events-approval-0001', 'reviewer',
                'approved', 'plan:approve', %s);
        INSERT INTO cpk_execution_requests
          (request_id, workspace_id, session_id, plan_id, status, requested_by,
           requested_at, approval_request_id, approval_decision_id,
           idempotency_key, intent_fingerprint)
        VALUES ('events-execution', %s, %s, %s, 'cancelled', 'operator', %s,
                'events-approval-0001', 'events-decision', 'events-execution',
                'events-fingerprint');
        INSERT INTO cpk_activity_runs
          (run_id, plan_id, request_id, attempt, status, created_at,
           started_at, settled_at, metadata)
        VALUES (%s, %s, 'events-execution', 1, 'succeeded', %s, %s, %s,
                '{}'::jsonb);
        INSERT INTO cpk_activity_events
          (event_id, run_id, ordinal, event_type, occurred_at, payload)
        SELECT 'event-' || lpad(value::text, 4, '0'), %s, value,
               'run_started', %s::timestamptz, '{}'::jsonb
        FROM generate_series(1, %s) AS value
        """,
        (
            _INSTANT,
            handles.events_workspace_id,
            session_id,
            plan_id,
            _INSTANT,
            handles.events_run_id,
            plan_id,
            _INSTANT,
            _INSTANT,
            _INSTANT,
            handles.events_run_id,
            _INSTANT,
            count,
        ),
    )


def _seed_current_metadata(
    connection: object,
    handles: LargeReadHistoryHandles,
    count: int,
) -> None:
    connection.execute(
        """
        INSERT INTO cpk_observations
          (observation_id, workspace_id, subject_id, status, observed_at,
           evidence, freshness)
        SELECT 'observation-' || lpad(value::text, 4, '0'), %s,
               'subject-' || lpad(value::text, 4, '0'), 'healthy',
               %s::timestamptz, '{}'::jsonb, 'fresh'
        FROM generate_series(1, %s) AS value;
        INSERT INTO cpk_runtime_authorities
          (registration_id, workspace_id, authority_ref, runtime_kind,
           authority_kind, authority, admitted_by, admitted_at, status, metadata)
        SELECT 'runtime-registration-' || lpad(value::text, 4, '0'), %s,
               'runtime-authority-' || lpad(value::text, 4, '0'), 'docker',
               'local-docker-socket',
               jsonb_build_object('kind', 'local-docker-socket'),
               'operator', %s::timestamptz, 'active', '{}'::jsonb
        FROM generate_series(1, %s) AS value;
        INSERT INTO cpk_runtime_authority_deliveries
          (delivery_id, workspace_id, authority_ref, delivery_kind, delivery,
           secret_references, admitted_by, admitted_at, status, metadata)
        SELECT 'delivery-' || lpad(value::text, 4, '0'), %s,
               'runtime-delivery-' || lpad(value::text, 4, '0'),
               'local-docker-socket-mount',
               jsonb_build_object(
                 'authority_ref', jsonb_build_object(
                   'reference_id', 'runtime-delivery-' || lpad(value::text, 4, '0')
                 ),
                 'delivery_kind', 'local-docker-socket-mount',
                 'secret_references', '[]'::jsonb
               ),
               '[]'::jsonb, 'operator', %s::timestamptz, 'active', '{}'::jsonb
        FROM generate_series(1, %s) AS value;
        INSERT INTO cpk_ingress_authorities
          (registration_id, workspace_id, authority_ref, provider_kind,
           authority, credential_references, allowed_hostname_pattern,
           admitted_by, admitted_at, status, metadata)
        SELECT 'ingress-registration-' || lpad(value::text, 4, '0'), %s,
               'ingress-authority-' || lpad(value::text, 4, '0'), 'cloudflare',
               jsonb_build_object(
                 'provider_kind', 'cloudflare', 'account_id', 'synthetic-account',
                 'zone_id', 'synthetic-zone', 'zone_name', 'invalid.test',
                 'api_token_ref', 'secret://synthetic/cloudflare/token',
                 'allowed_hostname_pattern', '*.invalid.test',
                 'generated_secret_provider_registration_id', 'synthetic-provider',
                 'generated_secret_reference_prefix', 'secret://synthetic/ingress'
               ),
               jsonb_build_object(
                 'api_token_ref', 'secret://synthetic/cloudflare/token'
               ),
               '*.invalid.test', 'operator', %s::timestamptz, 'active', '{}'::jsonb
        FROM generate_series(1, %s) AS value;
        INSERT INTO cpk_secret_providers
          (registration_id, workspace_id, provider_id, provider_kind,
           display_name, endpoint_reference, credential_reference,
           allowed_reference_prefixes, allowed_intents, admitted_by,
           admitted_at, status, metadata)
        SELECT 'provider-registration-' || lpad(value::text, 4, '0'), %s,
               'provider-' || lpad(value::text, 4, '0'),
               'control-plane-kit-secrets', 'Synthetic provider',
               'synthetic-endpoint-' || lpad(value::text, 4, '0'),
               'secret://synthetic/provider/credential-' || value,
               jsonb_build_array('secret://synthetic/workspace'),
               jsonb_build_array('postgres-password'), 'operator',
               %s::timestamptz, 'active', '{}'::jsonb
        FROM generate_series(1, %s) AS value
        """,
        (
            handles.observations_workspace_id,
            _INSTANT,
            count,
            handles.runtime_authorities_workspace_id,
            _INSTANT,
            count,
            handles.runtime_deliveries_workspace_id,
            _INSTANT,
            count,
            handles.ingress_authorities_workspace_id,
            _INSTANT,
            count,
            handles.secret_providers_workspace_id,
            _INSTANT,
            count,
        ),
    )
    connection.execute(
        """
        INSERT INTO cpk_secret_providers
          (registration_id, workspace_id, provider_id, provider_kind,
           display_name, endpoint_reference, credential_reference,
           allowed_reference_prefixes, allowed_intents, admitted_by,
           admitted_at, status, metadata)
        VALUES ('reference-parent-provider', %s, 'reference-parent',
                'control-plane-kit-secrets', 'Synthetic reference parent',
                'synthetic-reference-endpoint',
                'secret://synthetic/reference/credential',
                '["secret://synthetic/reference"]'::jsonb,
                '["postgres-password"]'::jsonb, 'operator', %s, 'active',
                '{}'::jsonb);
        INSERT INTO cpk_secret_references
          (registration_id, workspace_id, secret_reference,
           provider_registration_id, allowed_intents, admitted_by, admitted_at,
           status, metadata)
        SELECT 'reference-' || lpad(value::text, 4, '0'), %s,
               'secret://synthetic/reference/value-' || value,
               'reference-parent-provider', '["postgres-password"]'::jsonb,
               'operator', %s::timestamptz, 'active', '{}'::jsonb
        FROM generate_series(1, %s) AS value
        """,
        (
            handles.secret_references_workspace_id,
            _INSTANT,
            handles.secret_references_workspace_id,
            _INSTANT,
            count,
        ),
    )


def _seed_delegation_keys(
    connection: object,
    handles: LargeReadHistoryHandles,
    count: int,
) -> None:
    connection.execute(
        """
        INSERT INTO cpk_delegation_signing_keys
          (registration_id, workspace_id, purpose, issuer, key_id, algorithm,
           public_key_pem, public_fingerprint_sha256, private_key_reference,
           admitted_by, admitted_at, status)
        SELECT 'dkey_' || encode(
                 sha256(convert_to('registration-' || value, 'UTF8')), 'hex'
               ),
               %s, 'gateway-probe', 'issuer',
               'key-' || lpad(value::text, 4, '0'), 'ed25519', %s,
               encode(sha256(convert_to(%s, 'UTF8')), 'hex'),
               'secret://synthetic/delegation/private-' || value,
               'operator', %s::timestamptz, 'verify-only'
        FROM generate_series(1, %s) AS value
        """,
        (
            handles.delegation_keys_workspace_id,
            _PUBLIC_KEY,
            _PUBLIC_KEY,
            _INSTANT,
            count,
        ),
    )


def _seed_gateway_probes(
    connection: object,
    handles: LargeReadHistoryHandles,
    count: int,
) -> None:
    seed_identity_graphs(
        PostgresStoreBundle(connection),
        workspace_id=handles.gateway_probes_workspace_id,
        graph_ids=("probe-graph",),
    )
    connection.execute(
        """
        INSERT INTO cpk_gateway_probe_attempts
          (probe_id, workspace_id, request_id, actor_id, current_graph_id,
           gateway_node_id, gateway_runtime_id, access_path, probe_kind,
           target_id, request_digest, issuer, key_id, audience, grant_jti,
           issued_at, expires_at, status, requested_at, intent_fingerprint,
           evidence)
        SELECT 'probe-' || lpad(value::text, 4, '0'), %s,
               'probe-request-' || lpad(value::text, 4, '0'), 'operator',
               'probe-graph', 'gateway', 'runtime', 'runtime-private',
               'http-status', 'target-' || value,
               encode(sha256(convert_to('probe-request-' || value, 'UTF8')), 'hex'),
               'issuer', 'key', 'audience',
               'grant-' || lpad(value::text, 4, '0'), %s, %s + 300,
               'intended', %s::timestamptz,
               'probe-fingerprint-' || lpad(value::text, 4, '0'), '{}'::jsonb
        FROM generate_series(1, %s) AS value
        """,
        (
            handles.gateway_probes_workspace_id,
            _EPOCH,
            _EPOCH,
            _INSTANT,
            count,
        ),
    )
