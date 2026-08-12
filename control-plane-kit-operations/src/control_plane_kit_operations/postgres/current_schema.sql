CREATE TABLE cpk_activity_events (
    event_id text NOT NULL,
    run_id text NOT NULL,
    ordinal integer NOT NULL,
    event_type text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT cpk_activity_events_kind_check CHECK ((event_type = ANY (ARRAY['request_admitted'::text, 'request_claimed'::text, 'request_claim_renewed'::text, 'request_claim_taken_over'::text, 'request_claim_abandoned'::text, 'run_opened'::text, 'run_started'::text, 'run_paused'::text, 'run_resumed'::text, 'step_started'::text, 'step_succeeded'::text, 'step_failed'::text, 'step_unsupported'::text, 'step_uncertain'::text, 'step_uncertainty_resolved_succeeded'::text, 'step_uncertainty_resolved_failed'::text, 'step_compensation_started'::text, 'step_compensation_succeeded'::text, 'step_compensation_failed'::text, 'step_compensation_unsupported'::text, 'step_compensation_uncertain'::text, 'step_compensation_uncertainty_resolved_succeeded'::text, 'step_compensation_uncertainty_resolved_failed'::text, 'recovery_decision_recorded'::text, 'run_compensation_started'::text, 'run_compensation_succeeded'::text, 'run_compensation_failed'::text, 'run_uncompensated_failure_accepted'::text, 'run_succeeded'::text, 'run_failed'::text, 'run_cancelled'::text, 'current_graph_advanced'::text]))),
    CONSTRAINT cpk_activity_events_ordinal_check CHECK ((ordinal > 0)),
    CONSTRAINT cpk_activity_events_shape_check CHECK (((((event_type = ANY (ARRAY['step_started'::text, 'step_succeeded'::text, 'step_failed'::text, 'step_unsupported'::text, 'step_uncertain'::text, 'step_uncertainty_resolved_succeeded'::text, 'step_uncertainty_resolved_failed'::text, 'step_compensation_started'::text, 'step_compensation_succeeded'::text, 'step_compensation_failed'::text, 'step_compensation_unsupported'::text, 'step_compensation_uncertain'::text, 'step_compensation_uncertainty_resolved_succeeded'::text, 'step_compensation_uncertainty_resolved_failed'::text])) AND (NULLIF((payload ->> 'activity_id'::text), ''::text) IS NOT NULL)) OR ((event_type = ANY (ARRAY['request_admitted'::text, 'request_claimed'::text, 'request_claim_renewed'::text, 'request_claim_taken_over'::text, 'request_claim_abandoned'::text, 'run_opened'::text, 'run_started'::text, 'run_paused'::text, 'run_resumed'::text, 'recovery_decision_recorded'::text, 'run_compensation_started'::text, 'run_compensation_succeeded'::text, 'run_compensation_failed'::text, 'run_uncompensated_failure_accepted'::text, 'run_succeeded'::text, 'run_failed'::text, 'run_cancelled'::text, 'current_graph_advanced'::text])) AND ((payload ->> 'activity_id'::text) IS NULL))) AND (((event_type = 'recovery_decision_recorded'::text) AND (payload ? 'recovery'::text) AND (jsonb_typeof((payload -> 'recovery'::text)) = 'object'::text)) OR ((event_type <> 'recovery_decision_recorded'::text) AND ((NOT (payload ? 'recovery'::text)) OR ((payload -> 'recovery'::text) = 'null'::jsonb))))))
);

CREATE TABLE cpk_activity_plans (
    plan_id text NOT NULL,
    session_id text NOT NULL,
    base_graph_id text NOT NULL,
    desired_graph_id text NOT NULL,
    base_realized_projection_id text NOT NULL,
    desired_realized_projection_id text NOT NULL,
    desired_graph_revision bigint DEFAULT 0 NOT NULL,
    status text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT cpk_activity_plans_desired_graph_revision_check CHECK ((desired_graph_revision >= 0)),
    CONSTRAINT cpk_activity_plans_status_check CHECK ((status = ANY (ARRAY['planned'::text, 'superseded'::text, 'cancelled'::text])))
);

CREATE TABLE cpk_activity_runs (
    run_id text NOT NULL,
    plan_id text NOT NULL,
    request_id text NOT NULL,
    attempt integer DEFAULT 1 NOT NULL,
    prior_run_id text,
    status text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    started_at timestamp with time zone,
    settled_at timestamp with time zone,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT cpk_activity_runs_attempt_check CHECK (((attempt > 0) AND (((attempt = 1) AND (prior_run_id IS NULL)) OR ((attempt > 1) AND (prior_run_id IS NOT NULL))) AND (prior_run_id IS DISTINCT FROM run_id))),
    CONSTRAINT cpk_activity_runs_settlement_check CHECK ((((status = ANY (ARRAY['cancelled'::text, 'compensated'::text, 'partially_failed'::text, 'succeeded'::text, 'uncompensated_failure'::text])) AND (settled_at IS NOT NULL)) OR ((status <> ALL (ARRAY['cancelled'::text, 'compensated'::text, 'partially_failed'::text, 'succeeded'::text, 'uncompensated_failure'::text])) AND (settled_at IS NULL)))),
    CONSTRAINT cpk_activity_runs_started_check CHECK ((((status = 'claimed'::text) AND (started_at IS NULL)) OR ((status = ANY (ARRAY['cancelled'::text, 'compensated'::text, 'compensating'::text, 'failed'::text, 'partially_failed'::text, 'paused'::text, 'running'::text, 'succeeded'::text, 'uncompensated_failure'::text])) AND (started_at IS NOT NULL)))),
    CONSTRAINT cpk_activity_runs_status_check CHECK ((status = ANY (ARRAY['claimed'::text, 'running'::text, 'paused'::text, 'succeeded'::text, 'failed'::text, 'compensating'::text, 'compensated'::text, 'partially_failed'::text, 'uncompensated_failure'::text, 'cancelled'::text])))
);

CREATE TABLE cpk_approval_decisions (
    decision_id text NOT NULL,
    request_id text NOT NULL,
    actor_id text NOT NULL,
    decision text NOT NULL,
    scope text NOT NULL,
    decided_at timestamp with time zone NOT NULL,
    comment text,
    idempotency_key text,
    intent_fingerprint text,
    CONSTRAINT cpk_approval_decisions_kind_check CHECK ((decision = ANY (ARRAY['approved'::text, 'rejected'::text]))),
    CONSTRAINT cpk_approval_decisions_scope_check CHECK ((scope = ANY (ARRAY['hub:instance:create'::text, 'hub:instance:read'::text, 'instance:workspace:read'::text, 'instance:workspace:edit'::text, 'plan:request'::text, 'plan:approve'::text, 'plan:approve-destructive'::text, 'plan:execute'::text, 'execution:operate'::text, 'runtime-authority:register'::text, 'runtime-authority:read'::text, 'runtime-authority:revoke'::text, 'runtime-authority:use'::text, 'runtime-authority-delivery:register'::text, 'runtime-authority-delivery:read'::text, 'runtime-authority-delivery:revoke'::text, 'ingress-authority:register'::text, 'ingress-authority:read'::text, 'ingress-authority:revoke'::text, 'ingress-authority:use'::text, 'secret-provider:register'::text, 'secret-provider:read'::text, 'secret-provider:use'::text, 'secret-provider:revoke'::text, 'delegation-key:generate'::text, 'delegation-key:register'::text, 'delegation-key:read'::text, 'delegation-key:activate'::text, 'delegation-key:retire'::text, 'delegation-key:revoke'::text, 'delegation-key:use'::text, 'delegation-key:rotate'::text, 'delegation-key:rotate-approve'::text, 'gateway-probe:use'::text])))
);

CREATE TABLE cpk_approval_requests (
    request_id text NOT NULL,
    session_id text NOT NULL,
    plan_id text,
    rotation_id text,
    subject_kind text NOT NULL,
    subject_payload jsonb NOT NULL,
    review_digest text NOT NULL,
    requested_by text NOT NULL,
    requested_at timestamp with time zone NOT NULL,
    required_scope text NOT NULL,
    max_risk text NOT NULL,
    destructive boolean NOT NULL,
    comment text,
    idempotency_key text,
    intent_fingerprint text,
    CONSTRAINT cpk_approval_requests_review_digest_check CHECK (((review_digest COLLATE "C") ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT cpk_approval_requests_risk_check CHECK ((max_risk = ANY (ARRAY['informational'::text, 'low'::text, 'medium'::text, 'high'::text, 'critical'::text]))),
    CONSTRAINT cpk_approval_requests_scope_check CHECK ((required_scope = ANY (ARRAY['hub:instance:create'::text, 'hub:instance:read'::text, 'instance:workspace:read'::text, 'instance:workspace:edit'::text, 'plan:request'::text, 'plan:approve'::text, 'plan:approve-destructive'::text, 'plan:execute'::text, 'execution:operate'::text, 'runtime-authority:register'::text, 'runtime-authority:read'::text, 'runtime-authority:revoke'::text, 'runtime-authority:use'::text, 'runtime-authority-delivery:register'::text, 'runtime-authority-delivery:read'::text, 'runtime-authority-delivery:revoke'::text, 'ingress-authority:register'::text, 'ingress-authority:read'::text, 'ingress-authority:revoke'::text, 'ingress-authority:use'::text, 'secret-provider:register'::text, 'secret-provider:read'::text, 'secret-provider:use'::text, 'secret-provider:revoke'::text, 'delegation-key:generate'::text, 'delegation-key:register'::text, 'delegation-key:read'::text, 'delegation-key:activate'::text, 'delegation-key:retire'::text, 'delegation-key:revoke'::text, 'delegation-key:use'::text, 'delegation-key:rotate'::text, 'delegation-key:rotate-approve'::text, 'gateway-probe:use'::text]))),
    CONSTRAINT cpk_approval_requests_subject_identity_check CHECK ((((subject_kind = 'activity-plan'::text) AND (plan_id IS NOT NULL) AND (rotation_id IS NULL)) OR ((subject_kind = 'gateway-key-rotation'::text) AND (plan_id IS NULL) AND (rotation_id IS NOT NULL)))),
    CONSTRAINT cpk_approval_requests_subject_kind_check CHECK ((subject_kind = ANY (ARRAY['activity-plan'::text, 'gateway-key-rotation'::text])))
);

CREATE TABLE cpk_cloudflare_ingress_resources (
    workspace_id text NOT NULL,
    runtime_id text NOT NULL,
    ingress_id text NOT NULL,
    epoch integer DEFAULT 1 NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    authority_ref text NOT NULL,
    provider_kind text NOT NULL,
    tunnel_name text NOT NULL,
    tunnel_id text NOT NULL,
    dns_record_id text NOT NULL,
    hostname text NOT NULL,
    zone_id text NOT NULL,
    lifecycle text NOT NULL,
    created_at timestamp(6) with time zone NOT NULL,
    observed_at timestamp(6) with time zone NOT NULL,
    source_run_id text NOT NULL,
    source_activity_id text NOT NULL,
    source_event_id text NOT NULL,
    removed_at timestamp(6) with time zone,
    removed_by_run_id text,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT cpk_cloudflare_ingress_resources_authority_ref_check CHECK ((authority_ref ~ '^[a-z][a-z0-9._-]{0,127}$'::text)),
    CONSTRAINT cpk_cloudflare_ingress_resources_epoch_check CHECK ((epoch > 0)),
    CONSTRAINT cpk_cloudflare_ingress_resources_lifecycle_check CHECK ((lifecycle = ANY (ARRAY['ephemeral'::text, 'retained'::text, 'external'::text]))),
    CONSTRAINT cpk_cloudflare_ingress_resources_metadata_shape_check CHECK ((jsonb_typeof(metadata) = 'object'::text)),
    CONSTRAINT cpk_cloudflare_ingress_resources_provider_kind_check CHECK ((provider_kind = 'cloudflare'::text)),
    CONSTRAINT cpk_cloudflare_ingress_resources_removed_evidence_check CHECK ((((status = 'removed'::text) AND (removed_at IS NOT NULL) AND (removed_by_run_id IS NOT NULL)) OR ((status <> 'removed'::text) AND (removed_at IS NULL) AND (removed_by_run_id IS NULL)))),
    CONSTRAINT cpk_cloudflare_ingress_resources_status_check CHECK ((status = ANY (ARRAY['allocating'::text, 'active'::text, 'removing'::text, 'removed'::text, 'uncertain'::text, 'orphaned'::text])))
);

CREATE TABLE cpk_delegation_signing_keys (
    registration_id text NOT NULL,
    workspace_id text NOT NULL,
    purpose text NOT NULL,
    issuer text NOT NULL,
    key_id text NOT NULL,
    algorithm text NOT NULL,
    public_key_pem text NOT NULL,
    public_fingerprint_sha256 text NOT NULL,
    private_key_reference text NOT NULL,
    admitted_by text NOT NULL,
    admitted_at timestamp(6) with time zone NOT NULL,
    status text NOT NULL,
    activated_by text,
    activated_at timestamp(6) with time zone,
    retired_by text,
    retired_at timestamp(6) with time zone,
    revoked_by text,
    revoked_at timestamp(6) with time zone,
    CONSTRAINT cpk_delegation_signing_keys_activation_evidence_check CHECK (((activated_by IS NULL) = (activated_at IS NULL))),
    CONSTRAINT cpk_delegation_signing_keys_algorithm_check CHECK ((algorithm = 'ed25519'::text)),
    CONSTRAINT cpk_delegation_signing_keys_fingerprint_check CHECK ((public_fingerprint_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT cpk_delegation_signing_keys_issuer_check CHECK ((issuer ~ '^[a-z][a-z0-9._-]{0,127}$'::text)),
    CONSTRAINT cpk_delegation_signing_keys_key_id_check CHECK ((key_id ~ '^[a-z][a-z0-9._-]{0,127}$'::text)),
    CONSTRAINT cpk_delegation_signing_keys_private_reference_check CHECK ((private_key_reference ~ '^secret://[a-z][a-z0-9-]{0,62}/[A-Za-z0-9._/-]+$'::text)),
    CONSTRAINT cpk_delegation_signing_keys_purpose_check CHECK ((purpose = ANY (ARRAY['gateway-probe'::text, 'workload-node-control'::text, 'workload-node-control-surface-read'::text]))),
    CONSTRAINT cpk_delegation_signing_keys_registration_check CHECK ((registration_id ~ '^dkey_[0-9a-f]{64}$'::text)),
    CONSTRAINT cpk_delegation_signing_keys_retirement_evidence_check CHECK (((retired_by IS NULL) = (retired_at IS NULL))),
    CONSTRAINT cpk_delegation_signing_keys_revocation_evidence_check CHECK (((revoked_by IS NULL) = (revoked_at IS NULL))),
    CONSTRAINT cpk_delegation_signing_keys_status_check CHECK ((status = ANY (ARRAY['verify-only'::text, 'active'::text, 'retired'::text, 'revoked'::text])))
);

CREATE TABLE cpk_execution_requests (
    request_id text NOT NULL,
    workspace_id text NOT NULL,
    session_id text NOT NULL,
    plan_id text NOT NULL,
    status text NOT NULL,
    requested_by text NOT NULL,
    requested_at timestamp with time zone NOT NULL,
    approval_request_id text NOT NULL,
    approval_decision_id text NOT NULL,
    idempotency_key text NOT NULL,
    intent_fingerprint text NOT NULL,
    claim_worker_id text,
    claimed_at timestamp with time zone,
    lease_expires_at timestamp with time zone,
    CONSTRAINT cpk_execution_requests_claim_check CHECK ((((status = 'claimed'::text) AND (claim_worker_id IS NOT NULL) AND (claimed_at IS NOT NULL) AND (lease_expires_at IS NOT NULL)) OR ((status <> 'claimed'::text) AND (claim_worker_id IS NULL) AND (claimed_at IS NULL) AND (lease_expires_at IS NULL)))),
    CONSTRAINT cpk_execution_requests_status_check CHECK ((status = ANY (ARRAY['queued'::text, 'claimed'::text, 'cancelled'::text, 'abandoned'::text])))
);

CREATE TABLE cpk_gateway_key_rotation_deployments (
    rotation_id text NOT NULL,
    phase text NOT NULL,
    status text NOT NULL,
    session_id text NOT NULL,
    plan_id text NOT NULL,
    approval_request_id text NOT NULL,
    approval_decision_id text NOT NULL,
    execution_request_id text NOT NULL,
    run_id text NOT NULL,
    base_authored_graph_id text NOT NULL,
    base_realized_projection_id text NOT NULL,
    desired_authored_graph_id text NOT NULL,
    desired_realized_projection_id text NOT NULL,
    desired_revision integer NOT NULL,
    prepared_at timestamp(6) with time zone NOT NULL,
    accepted_current_graph_id text,
    accepted_current_projection_id text,
    accepted_at timestamp(6) with time zone,
    CONSTRAINT cpk_gateway_key_rotation_deployments_acceptance_check CHECK ((((status = 'accepted'::text) AND (accepted_current_graph_id IS NOT NULL) AND (accepted_current_projection_id IS NOT NULL) AND (accepted_at IS NOT NULL)) OR ((status = 'prepared'::text) AND (accepted_current_graph_id IS NULL) AND (accepted_current_projection_id IS NULL) AND (accepted_at IS NULL)))),
    CONSTRAINT cpk_gateway_key_rotation_deployments_phase_check CHECK ((phase = ANY (ARRAY['overlap'::text, 'retirement'::text]))),
    CONSTRAINT cpk_gateway_key_rotation_deployments_revision_check CHECK ((desired_revision >= 0)),
    CONSTRAINT cpk_gateway_key_rotation_deployments_status_check CHECK ((status = ANY (ARRAY['prepared'::text, 'accepted'::text])))
);

CREATE TABLE cpk_gateway_key_rotation_revocations (
    rotation_id text NOT NULL,
    provider_registration_id text NOT NULL,
    secret_reference text NOT NULL,
    provider_version_id text NOT NULL,
    provider_version_number integer NOT NULL,
    revocation_id text NOT NULL,
    correlation_id text NOT NULL,
    action_digest text NOT NULL,
    prepared_at timestamp(6) with time zone NOT NULL,
    CONSTRAINT cpk_gateway_key_rotation_revocations_digest_check CHECK ((action_digest ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT cpk_gateway_key_rotation_revocations_reference_check CHECK ((secret_reference ~ '^secret://[a-z][a-z0-9-]{0,62}/[A-Za-z0-9._/-]+$'::text)),
    CONSTRAINT cpk_gateway_key_rotation_revocations_version_check CHECK ((provider_version_number > 0))
);

CREATE TABLE cpk_gateway_key_rotation_transitions (
    rotation_id text NOT NULL,
    transition_id text NOT NULL,
    from_status text NOT NULL,
    to_status text NOT NULL,
    from_version integer NOT NULL,
    to_version integer NOT NULL,
    transition_fingerprint text NOT NULL,
    advanced_by text NOT NULL,
    advanced_at timestamp(6) with time zone NOT NULL,
    failure_code text,
    CONSTRAINT cpk_gateway_key_rotation_transitions_fingerprint_check CHECK ((transition_fingerprint ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT cpk_gateway_key_rotation_transitions_from_status_check CHECK ((from_status = ANY (ARRAY['requested'::text, 'awaiting-approval'::text, 'approved'::text, 'generation-prepared'::text, 'key-generated'::text, 'overlap-deploying'::text, 'overlap-ready'::text, 'new-key-active'::text, 'draining-old-grants'::text, 'retirement-deploying'::text, 'retirement-ready'::text, 'old-key-retired'::text, 'revocation-prepared'::text, 'completed'::text, 'blocked'::text, 'rejected'::text]))),
    CONSTRAINT cpk_gateway_key_rotation_transitions_to_status_check CHECK ((to_status = ANY (ARRAY['requested'::text, 'awaiting-approval'::text, 'approved'::text, 'generation-prepared'::text, 'key-generated'::text, 'overlap-deploying'::text, 'overlap-ready'::text, 'new-key-active'::text, 'draining-old-grants'::text, 'retirement-deploying'::text, 'retirement-ready'::text, 'old-key-retired'::text, 'revocation-prepared'::text, 'completed'::text, 'blocked'::text, 'rejected'::text]))),
    CONSTRAINT cpk_gateway_key_rotation_transitions_version_check CHECK (((from_version > 0) AND (to_version = (from_version + 1))))
);

CREATE TABLE cpk_gateway_key_rotations (
    rotation_id text NOT NULL,
    workspace_id text NOT NULL,
    gateway_node_id text NOT NULL,
    purpose text NOT NULL,
    issuer text NOT NULL,
    old_key_id text NOT NULL,
    new_secret_reference text NOT NULL,
    key_generation_correlation text NOT NULL,
    maximum_grant_lifetime_seconds integer NOT NULL,
    clock_skew_seconds integer NOT NULL,
    correlation_id text NOT NULL,
    requested_by text NOT NULL,
    requested_at timestamp(6) with time zone NOT NULL,
    intent_fingerprint text NOT NULL,
    status text NOT NULL,
    version integer NOT NULL,
    approval_request_id text,
    approval_decision_id text,
    generation_provider_registration_id text,
    generation_action_digest text,
    new_key_id text,
    new_secret_version_id text,
    new_secret_version_number integer,
    new_key_activated_at timestamp(6) with time zone,
    drain_deadline_epoch bigint,
    old_key_retired_at timestamp(6) with time zone,
    old_secret_revoked_at timestamp(6) with time zone,
    failure_code text,
    updated_by text,
    updated_at timestamp(6) with time zone,
    CONSTRAINT cpk_gateway_key_rotations_activation_check CHECK (((new_key_activated_at IS NULL) = (drain_deadline_epoch IS NULL))),
    CONSTRAINT cpk_gateway_key_rotations_failure_check CHECK (((status = ANY (ARRAY['blocked'::text, 'rejected'::text])) = (failure_code IS NOT NULL))),
    CONSTRAINT cpk_gateway_key_rotations_fingerprint_check CHECK ((intent_fingerprint ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT cpk_gateway_key_rotations_generation_checkpoint_check CHECK (((generation_provider_registration_id IS NULL) = (generation_action_digest IS NULL))),
    CONSTRAINT cpk_gateway_key_rotations_generation_digest_check CHECK (((generation_action_digest IS NULL) OR ((generation_action_digest COLLATE "C") ~ '^[0-9a-f]{64}$'::text))),
    CONSTRAINT cpk_gateway_key_rotations_generation_provider_check CHECK (((generation_provider_registration_id IS NULL) OR ((octet_length(generation_provider_registration_id) BETWEEN 1 AND 200) AND ((generation_provider_registration_id COLLATE "C") ~ '^[A-Za-z0-9]'::text) AND ((generation_provider_registration_id COLLATE "C") !~ '[^A-Za-z0-9._:-]'::text)))),
    CONSTRAINT cpk_gateway_key_rotations_lifetime_check CHECK (((maximum_grant_lifetime_seconds >= 1) AND (maximum_grant_lifetime_seconds <= 300))),
    CONSTRAINT cpk_gateway_key_rotations_purpose_check CHECK ((purpose = ANY (ARRAY['gateway-probe'::text, 'workload-node-control'::text, 'workload-node-control-surface-read'::text]))),
    CONSTRAINT cpk_gateway_key_rotations_retirement_check CHECK (((old_secret_revoked_at IS NULL) OR (old_key_retired_at IS NOT NULL))),
    CONSTRAINT cpk_gateway_key_rotations_secret_version_check CHECK ((((new_key_id IS NULL) = (new_secret_version_id IS NULL)) AND ((new_secret_version_id IS NULL) = (new_secret_version_number IS NULL)))),
    CONSTRAINT cpk_gateway_key_rotations_skew_check CHECK (((clock_skew_seconds >= 0) AND (clock_skew_seconds <= 60))),
    CONSTRAINT cpk_gateway_key_rotations_status_check CHECK ((status = ANY (ARRAY['requested'::text, 'awaiting-approval'::text, 'approved'::text, 'generation-prepared'::text, 'key-generated'::text, 'overlap-deploying'::text, 'overlap-ready'::text, 'new-key-active'::text, 'draining-old-grants'::text, 'retirement-deploying'::text, 'retirement-ready'::text, 'old-key-retired'::text, 'revocation-prepared'::text, 'completed'::text, 'blocked'::text, 'rejected'::text]))),
    CONSTRAINT cpk_gateway_key_rotations_version_check CHECK ((version > 0))
);

CREATE TABLE cpk_gateway_probe_attempts (
    probe_id text NOT NULL,
    workspace_id text NOT NULL,
    request_id text NOT NULL,
    actor_id text NOT NULL,
    current_graph_id text NOT NULL,
    gateway_node_id text NOT NULL,
    gateway_runtime_id text NOT NULL,
    access_path text DEFAULT 'runtime-private'::text NOT NULL,
    probe_kind text NOT NULL,
    target_id text NOT NULL,
    request_digest text NOT NULL,
    issuer text NOT NULL,
    key_id text NOT NULL,
    audience text NOT NULL,
    grant_jti text NOT NULL,
    issued_at bigint NOT NULL,
    expires_at bigint NOT NULL,
    status text NOT NULL,
    requested_at timestamp(6) with time zone NOT NULL,
    intent_fingerprint text NOT NULL,
    completed_at timestamp(6) with time zone,
    result_code text,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT cpk_gateway_probe_access_path_check CHECK ((access_path = ANY (ARRAY['runtime-private'::text, 'named-public-ingress'::text]))),
    CONSTRAINT cpk_gateway_probe_completion_check CHECK ((((status = 'intended'::text) AND (completed_at IS NULL) AND (result_code IS NULL)) OR ((status <> 'intended'::text) AND (completed_at IS NOT NULL) AND (result_code IS NOT NULL)))),
    CONSTRAINT cpk_gateway_probe_digest_check CHECK ((request_digest ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT cpk_gateway_probe_kind_check CHECK ((probe_kind = ANY (ARRAY['http-status'::text, 'postgres-select-one'::text]))),
    CONSTRAINT cpk_gateway_probe_status_check CHECK ((status = ANY (ARRAY['intended'::text, 'succeeded'::text, 'rejected'::text, 'failed'::text]))),
    CONSTRAINT cpk_gateway_probe_time_check CHECK (((issued_at >= 0) AND (expires_at > issued_at)))
);

CREATE TABLE cpk_generated_ingress_secret_references (
    workspace_id text NOT NULL,
    purpose text NOT NULL,
    secret_ref text NOT NULL,
    recorded_at timestamp(6) with time zone NOT NULL,
    source_run_id text NOT NULL,
    source_activity_id text NOT NULL,
    source_event_id text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT cpk_generated_ingress_secret_references_metadata_shape_check CHECK ((jsonb_typeof(metadata) = 'object'::text)),
    CONSTRAINT cpk_generated_ingress_secret_references_purpose_check CHECK ((purpose = 'cloudflared-tunnel-token'::text)),
    CONSTRAINT cpk_generated_ingress_secret_references_ref_check CHECK ((secret_ref ~ '^secret://[a-z][a-z0-9-]{0,62}/[A-Za-z0-9._/-]+$'::text))
);

CREATE TABLE cpk_graph_versions (
    graph_id text NOT NULL,
    workspace_id text NOT NULL,
    version integer NOT NULL,
    graph_descriptor jsonb NOT NULL,
    created_by text NOT NULL,
    created_at timestamp(6) with time zone NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT cpk_graph_versions_version_check CHECK ((version > 0))
);

CREATE TABLE cpk_image_pull_authorities (
    authority_id text NOT NULL,
    workspace_id text NOT NULL,
    authority jsonb NOT NULL,
    registry text NOT NULL,
    repository text,
    credential_reference text NOT NULL,
    admitted_by text NOT NULL,
    admitted_at timestamp(6) with time zone NOT NULL,
    status text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT cpk_image_pull_authorities_reference_check CHECK ((credential_reference ~~ 'secret://%'::text)),
    CONSTRAINT cpk_image_pull_authorities_status_check CHECK ((status = ANY (ARRAY['active'::text, 'revoked'::text])))
);

CREATE TABLE cpk_ingress_authorities (
    registration_id text NOT NULL,
    workspace_id text NOT NULL,
    authority_ref text NOT NULL,
    provider_kind text NOT NULL,
    authority jsonb NOT NULL,
    credential_references jsonb DEFAULT '{}'::jsonb NOT NULL,
    allowed_hostname_pattern text NOT NULL,
    admitted_by text NOT NULL,
    admitted_at timestamp(6) with time zone NOT NULL,
    status text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT cpk_ingress_authorities_authority_shape_check CHECK ((jsonb_typeof(authority) = 'object'::text)),
    CONSTRAINT cpk_ingress_authorities_credential_shape_check CHECK ((jsonb_typeof(credential_references) = 'object'::text)),
    CONSTRAINT cpk_ingress_authorities_provider_kind_check CHECK ((provider_kind = 'cloudflare'::text)),
    CONSTRAINT cpk_ingress_authorities_reference_check CHECK ((authority_ref ~ '^[a-z][a-z0-9._-]{0,127}$'::text)),
    CONSTRAINT cpk_ingress_authorities_status_check CHECK ((status = ANY (ARRAY['active'::text, 'revoked'::text])))
);

CREATE TABLE cpk_observations (
    observation_id text NOT NULL,
    workspace_id text NOT NULL,
    subject_id text NOT NULL,
    status text NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    freshness text NOT NULL,
    graph_id text,
    probe_kind text,
    probe_outcome text,
    endpoint_context text,
    CONSTRAINT cpk_observations_correlation_check CHECK ((((graph_id IS NULL) AND (probe_kind IS NULL) AND (probe_outcome IS NULL)) OR ((graph_id IS NOT NULL) AND (probe_kind IS NOT NULL) AND (probe_outcome IS NOT NULL)))),
    CONSTRAINT cpk_observations_endpoint_context_check CHECK (((endpoint_context IS NULL) OR (endpoint_context = ANY (ARRAY['runtime-private'::text, 'host-local'::text, 'public'::text])))),
    CONSTRAINT cpk_observations_freshness_check CHECK ((freshness = ANY (ARRAY['fresh'::text, 'stale'::text]))),
    CONSTRAINT cpk_observations_probe_kind_check CHECK (((probe_kind IS NULL) OR (probe_kind = ANY (ARRAY['process'::text, 'transport'::text, 'application-health'::text, 'readiness'::text])))),
    CONSTRAINT cpk_observations_probe_outcome_check CHECK (((probe_outcome IS NULL) OR (probe_outcome = ANY (ARRAY['process-running'::text, 'process-stopped'::text, 'reachable'::text, 'refused'::text, 'timed-out'::text, 'healthy'::text, 'unhealthy'::text, 'malformed'::text, 'ready'::text, 'not-ready'::text, 'unknown'::text])))),
    CONSTRAINT cpk_observations_process_endpoint_check CHECK (((endpoint_context IS NULL) OR (probe_kind <> ALL (ARRAY['process'::text, 'readiness'::text])))),
    CONSTRAINT cpk_observations_status_check CHECK ((status = ANY (ARRAY['starting'::text, 'process_started'::text, 'reachable'::text, 'healthy'::text, 'unhealthy'::text, 'timed_out'::text, 'verified'::text, 'verification_failed'::text, 'unsupported'::text, 'rejected'::text, 'malformed'::text, 'unknown'::text])))
);

CREATE TABLE cpk_operation_actions (
    action_id text NOT NULL,
    session_id text NOT NULL,
    ordinal integer NOT NULL,
    action_type text NOT NULL,
    actor_id text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL,
    idempotency_key text,
    intent_fingerprint text,
    CONSTRAINT cpk_operation_actions_ordinal_check CHECK ((ordinal > 0)),
    CONSTRAINT cpk_operation_actions_type_check CHECK ((action_type = ANY (ARRAY['create-workspace'::text, 'import-product-descriptor'::text, 'register-image-pull-authority'::text, 'register-runtime-authority'::text, 'revoke-runtime-authority'::text, 'register-runtime-authority-delivery'::text, 'revoke-runtime-authority-delivery'::text, 'register-ingress-authority'::text, 'revoke-ingress-authority'::text, 'register-secret-provider'::text, 'revoke-secret-provider'::text, 'register-secret-reference'::text, 'revoke-secret-reference'::text, 'register-delegation-key'::text, 'activate-delegation-key'::text, 'retire-delegation-key'::text, 'revoke-delegation-key'::text, 'start-operation-session'::text, 'close-operation-session'::text, 'cancel-operation-session'::text, 'record-operation-action'::text, 'set-desired-graph'::text, 'publish-desired-realized-projection'::text, 'request-activity-plan'::text, 'request-approval'::text, 'decide-approval'::text, 'request-gateway-probe'::text, 'admit-execution'::text, 'claim-run'::text, 'start-run'::text, 'pause-run'::text, 'resume-run'::text, 'complete-run'::text, 'fail-run'::text, 'complete-compensation'::text, 'fail-compensation'::text, 'cancel-run'::text, 'record-recovery-decision'::text, 'advance-current-graph'::text])))
);

CREATE TABLE cpk_operation_sessions (
    session_id text NOT NULL,
    workspace_id text NOT NULL,
    actor_id text NOT NULL,
    title text NOT NULL,
    status text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    closed_at timestamp with time zone,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    idempotency_key text,
    intent_fingerprint text,
    CONSTRAINT cpk_operation_sessions_closed_check CHECK ((((status = 'open'::text) AND (closed_at IS NULL)) OR ((status = ANY (ARRAY['closed'::text, 'cancelled'::text])) AND (closed_at IS NOT NULL)))),
    CONSTRAINT cpk_operation_sessions_status_check CHECK ((status = ANY (ARRAY['open'::text, 'closed'::text, 'cancelled'::text])))
);

CREATE TABLE cpk_realized_graph_projections (
    projection_id text NOT NULL,
    workspace_id text NOT NULL,
    source_authored_graph_id text NOT NULL,
    projection_kind text NOT NULL,
    projection_key text NOT NULL,
    projection_digest text NOT NULL,
    graph_descriptor jsonb NOT NULL,
    created_by text NOT NULL,
    created_at timestamp(6) with time zone NOT NULL,
    CONSTRAINT cpk_realized_graph_projection_digest_check CHECK ((projection_digest ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT cpk_realized_graph_projection_kind_check CHECK ((projection_kind = ANY (ARRAY['identity'::text, 'delegation-verifier'::text])))
);

CREATE TABLE cpk_registered_products (
    registration_id text NOT NULL,
    workspace_id text NOT NULL,
    product_reference jsonb NOT NULL,
    descriptor_sha256 text NOT NULL,
    descriptor_document jsonb NOT NULL,
    descriptor_content text NOT NULL,
    source jsonb NOT NULL,
    imported_by text NOT NULL,
    imported_at timestamp(6) with time zone NOT NULL,
    status text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT cpk_registered_products_content_digest_check CHECK ((descriptor_sha256 = encode(sha256(convert_to(descriptor_content, 'UTF8'::name)), 'hex'::text))),
    CONSTRAINT cpk_registered_products_digest_check CHECK ((descriptor_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT cpk_registered_products_status_check CHECK ((status = ANY (ARRAY['active'::text, 'revoked'::text])))
);

CREATE TABLE cpk_runtime_authorities (
    registration_id text NOT NULL,
    workspace_id text NOT NULL,
    authority_ref text NOT NULL,
    runtime_kind text NOT NULL,
    authority_kind text NOT NULL,
    authority jsonb NOT NULL,
    credential_references jsonb DEFAULT '{}'::jsonb NOT NULL,
    admitted_by text NOT NULL,
    admitted_at timestamp(6) with time zone NOT NULL,
    status text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT cpk_runtime_authorities_authority_kind_check CHECK ((authority_kind = ANY (ARRAY['local-docker-socket'::text, 'remote-docker-tls'::text]))),
    CONSTRAINT cpk_runtime_authorities_credential_shape_check CHECK ((jsonb_typeof(credential_references) = 'object'::text)),
    CONSTRAINT cpk_runtime_authorities_reference_check CHECK ((authority_ref ~ '^[a-z][a-z0-9._-]{0,127}$'::text)),
    CONSTRAINT cpk_runtime_authorities_runtime_kind_check CHECK ((runtime_kind = ANY (ARRAY['docker'::text, 'external'::text, 'dry-run'::text, 'aws'::text, 'kubernetes'::text]))),
    CONSTRAINT cpk_runtime_authorities_status_check CHECK ((status = ANY (ARRAY['active'::text, 'revoked'::text])))
);

CREATE TABLE cpk_runtime_authority_deliveries (
    delivery_id text NOT NULL,
    workspace_id text NOT NULL,
    authority_ref text NOT NULL,
    delivery_kind text NOT NULL,
    delivery jsonb NOT NULL,
    secret_references jsonb DEFAULT '[]'::jsonb NOT NULL,
    admitted_by text NOT NULL,
    admitted_at timestamp(6) with time zone NOT NULL,
    status text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT cpk_runtime_authority_deliveries_delivery_shape_check CHECK ((jsonb_typeof(delivery) = 'object'::text)),
    CONSTRAINT cpk_runtime_authority_deliveries_reference_check CHECK ((authority_ref ~ '^[a-z][a-z0-9._-]{0,127}$'::text)),
    CONSTRAINT cpk_runtime_authority_deliveries_secret_refs_shape_check CHECK ((jsonb_typeof(secret_references) = 'array'::text)),
    CONSTRAINT cpk_runtime_authority_deliveries_status_check CHECK ((status = ANY (ARRAY['active'::text, 'revoked'::text])))
);

CREATE TABLE cpk_secret_providers (
    registration_id text NOT NULL,
    workspace_id text NOT NULL,
    provider_id text NOT NULL,
    provider_kind text NOT NULL,
    display_name text NOT NULL,
    endpoint_reference text NOT NULL,
    credential_reference text NOT NULL,
    allowed_reference_prefixes jsonb NOT NULL,
    allowed_intents jsonb NOT NULL,
    admitted_by text NOT NULL,
    admitted_at timestamp(6) with time zone NOT NULL,
    status text NOT NULL,
    supersedes_registration_id text,
    revoked_by text,
    revoked_at timestamp(6) with time zone,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT cpk_secret_providers_credential_reference_check CHECK ((credential_reference ~ '^secret://[a-z][a-z0-9-]{0,62}/[A-Za-z0-9._/-]+$'::text)),
    CONSTRAINT cpk_secret_providers_endpoint_reference_check CHECK ((endpoint_reference ~ '^[a-z][a-z0-9._-]{0,127}$'::text)),
    CONSTRAINT cpk_secret_providers_id_check CHECK ((provider_id ~ '^[a-z][a-z0-9-]{0,62}$'::text)),
    CONSTRAINT cpk_secret_providers_intents_shape_check CHECK ((jsonb_typeof(allowed_intents) = 'array'::text)),
    CONSTRAINT cpk_secret_providers_kind_check CHECK ((provider_kind = 'control-plane-kit-secrets'::text)),
    CONSTRAINT cpk_secret_providers_metadata_shape_check CHECK ((jsonb_typeof(metadata) = 'object'::text)),
    CONSTRAINT cpk_secret_providers_prefixes_shape_check CHECK ((jsonb_typeof(allowed_reference_prefixes) = 'array'::text)),
    CONSTRAINT cpk_secret_providers_revocation_evidence_check CHECK ((((status = 'revoked'::text) AND (revoked_by IS NOT NULL) AND (revoked_at IS NOT NULL)) OR ((status <> 'revoked'::text) AND (revoked_by IS NULL) AND (revoked_at IS NULL)))),
    CONSTRAINT cpk_secret_providers_status_check CHECK ((status = ANY (ARRAY['active'::text, 'revoked'::text, 'superseded'::text])))
);

CREATE TABLE cpk_secret_references (
    registration_id text NOT NULL,
    workspace_id text NOT NULL,
    secret_reference text NOT NULL,
    provider_registration_id text NOT NULL,
    allowed_intents jsonb NOT NULL,
    admitted_by text NOT NULL,
    admitted_at timestamp(6) with time zone NOT NULL,
    status text NOT NULL,
    supersedes_registration_id text,
    revoked_by text,
    revoked_at timestamp(6) with time zone,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT cpk_secret_references_intents_shape_check CHECK ((jsonb_typeof(allowed_intents) = 'array'::text)),
    CONSTRAINT cpk_secret_references_metadata_shape_check CHECK ((jsonb_typeof(metadata) = 'object'::text)),
    CONSTRAINT cpk_secret_references_reference_check CHECK ((secret_reference ~ '^secret://[a-z][a-z0-9-]{0,62}/[A-Za-z0-9._/-]+$'::text)),
    CONSTRAINT cpk_secret_references_revocation_evidence_check CHECK ((((status = 'revoked'::text) AND (revoked_by IS NOT NULL) AND (revoked_at IS NOT NULL)) OR ((status <> 'revoked'::text) AND (revoked_by IS NULL) AND (revoked_at IS NULL)))),
    CONSTRAINT cpk_secret_references_status_check CHECK ((status = ANY (ARRAY['active'::text, 'revoked'::text, 'superseded'::text])))
);

CREATE TABLE cpk_secret_use_authorizations (
    authorization_id text NOT NULL,
    workspace_id text NOT NULL,
    reference_registration_id text NOT NULL,
    provider_registration_id text NOT NULL,
    secret_reference text NOT NULL,
    use_intent text NOT NULL,
    actor_subject text NOT NULL,
    correlation_id text NOT NULL,
    requested_at timestamp(6) with time zone NOT NULL,
    intent_fingerprint text NOT NULL,
    operation_id text,
    session_id text,
    run_id text,
    activity_id text,
    effect_id text,
    probe_id text,
    CONSTRAINT cpk_secret_use_authorizations_activity_check CHECK (((activity_id IS NULL) OR (activity_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'::text))),
    CONSTRAINT cpk_secret_use_authorizations_actor_check CHECK ((actor_subject ~ '^[a-z][a-z0-9._-]{0,127}$'::text)),
    CONSTRAINT cpk_secret_use_authorizations_correlation_check CHECK ((correlation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'::text)),
    CONSTRAINT cpk_secret_use_authorizations_effect_check CHECK (((effect_id IS NULL) OR (effect_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'::text))),
    CONSTRAINT cpk_secret_use_authorizations_fingerprint_check CHECK ((intent_fingerprint ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT cpk_secret_use_authorizations_id_check CHECK ((authorization_id ~ '^suse_[0-9a-f]{64}$'::text)),
    CONSTRAINT cpk_secret_use_authorizations_intent_check CHECK ((use_intent = ANY (ARRAY['application.control-token'::text, 'cloudflare.api-token'::text, 'cloudflare.tunnel-token'::text, 'docker.local-socket-access-marker'::text, 'docker.remote-tls.ca-certificate'::text, 'docker.remote-tls.client-certificate'::text, 'docker.remote-tls.client-key'::text, 'gateway.probe-signing-key'::text, 'oci.pull-credential'::text, 'postgres.password'::text]))),
    CONSTRAINT cpk_secret_use_authorizations_operation_check CHECK (((operation_id IS NULL) OR (operation_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'::text))),
    CONSTRAINT cpk_secret_use_authorizations_probe_check CHECK (((probe_id IS NULL) OR (probe_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'::text))),
    CONSTRAINT cpk_secret_use_authorizations_reference_check CHECK ((secret_reference ~ '^secret://[a-z][a-z0-9-]{0,62}/[A-Za-z0-9._/-]+$'::text)),
    CONSTRAINT cpk_secret_use_authorizations_run_check CHECK (((run_id IS NULL) OR (run_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'::text))),
    CONSTRAINT cpk_secret_use_authorizations_session_check CHECK (((session_id IS NULL) OR (session_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'::text)))
);

CREATE TABLE cpk_workspaces (
    workspace_id text NOT NULL,
    name text NOT NULL,
    lifecycle text NOT NULL,
    current_graph_id text,
    desired_graph_id text,
    current_realized_projection_id text,
    desired_realized_projection_id text,
    desired_graph_revision bigint DEFAULT 0 NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT cpk_workspaces_current_lineage_check CHECK (((current_graph_id IS NULL) = (current_realized_projection_id IS NULL))),
    CONSTRAINT cpk_workspaces_desired_graph_revision_check CHECK ((desired_graph_revision >= 0)),
    CONSTRAINT cpk_workspaces_desired_lineage_check CHECK (((desired_graph_id IS NULL) = (desired_realized_projection_id IS NULL))),
    CONSTRAINT cpk_workspaces_lifecycle_check CHECK ((lifecycle = ANY (ARRAY['created'::text, 'running'::text, 'paused'::text, 'stopped'::text, 'archived'::text, 'deconstructed'::text, 'deleted'::text, 'failed'::text])))
);

ALTER TABLE ONLY cpk_activity_events
    ADD CONSTRAINT cpk_activity_events_pkey PRIMARY KEY (event_id);

ALTER TABLE ONLY cpk_activity_events
    ADD CONSTRAINT cpk_activity_events_run_id_ordinal_key UNIQUE (run_id, ordinal);

ALTER TABLE ONLY cpk_activity_plans
    ADD CONSTRAINT cpk_activity_plans_pkey PRIMARY KEY (plan_id);

ALTER TABLE ONLY cpk_activity_plans
    ADD CONSTRAINT cpk_activity_plans_session_identity UNIQUE (plan_id, session_id);

ALTER TABLE ONLY cpk_activity_runs
    ADD CONSTRAINT cpk_activity_runs_pkey PRIMARY KEY (run_id);

ALTER TABLE ONLY cpk_approval_decisions
    ADD CONSTRAINT cpk_approval_decisions_pkey PRIMARY KEY (decision_id);

ALTER TABLE ONLY cpk_approval_decisions
    ADD CONSTRAINT cpk_approval_decisions_request_id_key UNIQUE (request_id);

ALTER TABLE ONLY cpk_approval_decisions
    ADD CONSTRAINT cpk_approval_decisions_request_identity UNIQUE (decision_id, request_id);

ALTER TABLE ONLY cpk_approval_requests
    ADD CONSTRAINT cpk_approval_requests_pkey PRIMARY KEY (request_id);

ALTER TABLE ONLY cpk_cloudflare_ingress_resources
    ADD CONSTRAINT cpk_cloudflare_ingress_resources_pkey PRIMARY KEY (workspace_id, ingress_id, epoch);

ALTER TABLE ONLY cpk_delegation_signing_keys
    ADD CONSTRAINT cpk_delegation_signing_keys_pkey PRIMARY KEY (registration_id);

ALTER TABLE ONLY cpk_delegation_signing_keys
    ADD CONSTRAINT cpk_delegation_signing_keys_registration_id_workspace_id_key UNIQUE (registration_id, workspace_id);

ALTER TABLE ONLY cpk_delegation_signing_keys
    ADD CONSTRAINT cpk_delegation_signing_keys_workspace_id_purpose_issuer_key_key UNIQUE (workspace_id, purpose, issuer, key_id);

ALTER TABLE ONLY cpk_execution_requests
    ADD CONSTRAINT cpk_execution_requests_pkey PRIMARY KEY (request_id);

ALTER TABLE ONLY cpk_execution_requests
    ADD CONSTRAINT cpk_execution_requests_plan_identity UNIQUE (request_id, plan_id);

ALTER TABLE ONLY cpk_execution_requests
    ADD CONSTRAINT cpk_execution_requests_workspace_id_idempotency_key_key UNIQUE (workspace_id, idempotency_key);

ALTER TABLE ONLY cpk_gateway_key_rotation_deployments
    ADD CONSTRAINT cpk_gateway_key_rotation_deployments_pkey PRIMARY KEY (rotation_id, phase);

ALTER TABLE ONLY cpk_gateway_key_rotation_revocations
    ADD CONSTRAINT cpk_gateway_key_rotation_revocations_pkey PRIMARY KEY (rotation_id);

ALTER TABLE ONLY cpk_gateway_key_rotation_transitions
    ADD CONSTRAINT cpk_gateway_key_rotation_transitions_pkey PRIMARY KEY (rotation_id, transition_id);

ALTER TABLE ONLY cpk_gateway_key_rotation_transitions
    ADD CONSTRAINT cpk_gateway_key_rotation_transitions_rotation_id_to_version_key UNIQUE (rotation_id, to_version);

ALTER TABLE ONLY cpk_gateway_key_rotations
    ADD CONSTRAINT cpk_gateway_key_rotations_pkey PRIMARY KEY (rotation_id);

ALTER TABLE ONLY cpk_gateway_key_rotations
    ADD CONSTRAINT cpk_gateway_key_rotations_workspace_id_correlation_id_key UNIQUE (workspace_id, correlation_id);

ALTER TABLE ONLY cpk_gateway_probe_attempts
    ADD CONSTRAINT cpk_gateway_probe_attempts_grant_jti_key UNIQUE (grant_jti);

ALTER TABLE ONLY cpk_gateway_probe_attempts
    ADD CONSTRAINT cpk_gateway_probe_attempts_pkey PRIMARY KEY (probe_id);

ALTER TABLE ONLY cpk_gateway_probe_attempts
    ADD CONSTRAINT cpk_gateway_probe_request_identity UNIQUE (workspace_id, request_id);

ALTER TABLE ONLY cpk_generated_ingress_secret_references
    ADD CONSTRAINT cpk_generated_ingress_secret_references_pkey PRIMARY KEY (workspace_id, purpose, source_run_id, source_activity_id, source_event_id);

ALTER TABLE ONLY cpk_graph_versions
    ADD CONSTRAINT cpk_graph_versions_pkey PRIMARY KEY (graph_id);

ALTER TABLE ONLY cpk_graph_versions
    ADD CONSTRAINT cpk_graph_versions_workspace_id_version_key UNIQUE (workspace_id, version);

ALTER TABLE ONLY cpk_graph_versions
    ADD CONSTRAINT cpk_graph_versions_workspace_identity UNIQUE (graph_id, workspace_id);

ALTER TABLE ONLY cpk_image_pull_authorities
    ADD CONSTRAINT cpk_image_pull_authorities_pkey PRIMARY KEY (authority_id);

ALTER TABLE ONLY cpk_ingress_authorities
    ADD CONSTRAINT cpk_ingress_authorities_pkey PRIMARY KEY (registration_id);

ALTER TABLE ONLY cpk_observations
    ADD CONSTRAINT cpk_observations_pkey PRIMARY KEY (observation_id);

ALTER TABLE ONLY cpk_operation_actions
    ADD CONSTRAINT cpk_operation_actions_pkey PRIMARY KEY (action_id);

ALTER TABLE ONLY cpk_operation_actions
    ADD CONSTRAINT cpk_operation_actions_session_id_ordinal_key UNIQUE (session_id, ordinal);

ALTER TABLE ONLY cpk_operation_sessions
    ADD CONSTRAINT cpk_operation_sessions_pkey PRIMARY KEY (session_id);

ALTER TABLE ONLY cpk_operation_sessions
    ADD CONSTRAINT cpk_operation_sessions_workspace_identity UNIQUE (session_id, workspace_id);

ALTER TABLE ONLY cpk_realized_graph_projections
    ADD CONSTRAINT cpk_realized_graph_projection_identity UNIQUE (workspace_id, source_authored_graph_id, projection_kind, projection_key);

ALTER TABLE ONLY cpk_realized_graph_projections
    ADD CONSTRAINT cpk_realized_graph_projection_source_identity UNIQUE (projection_id, source_authored_graph_id);

ALTER TABLE ONLY cpk_realized_graph_projections
    ADD CONSTRAINT cpk_realized_graph_projection_workspace_identity UNIQUE (projection_id, workspace_id);

ALTER TABLE ONLY cpk_realized_graph_projections
    ADD CONSTRAINT cpk_realized_graph_projections_pkey PRIMARY KEY (projection_id);

ALTER TABLE ONLY cpk_registered_products
    ADD CONSTRAINT cpk_registered_products_pkey PRIMARY KEY (registration_id);

ALTER TABLE ONLY cpk_registered_products
    ADD CONSTRAINT cpk_registered_products_workspace_id_descriptor_sha256_key UNIQUE (workspace_id, descriptor_sha256);

ALTER TABLE ONLY cpk_runtime_authorities
    ADD CONSTRAINT cpk_runtime_authorities_pkey PRIMARY KEY (registration_id);

ALTER TABLE ONLY cpk_runtime_authority_deliveries
    ADD CONSTRAINT cpk_runtime_authority_deliveries_pkey PRIMARY KEY (delivery_id);

ALTER TABLE ONLY cpk_secret_providers
    ADD CONSTRAINT cpk_secret_providers_pkey PRIMARY KEY (registration_id);

ALTER TABLE ONLY cpk_secret_providers
    ADD CONSTRAINT cpk_secret_providers_registration_id_workspace_id_key UNIQUE (registration_id, workspace_id);

ALTER TABLE ONLY cpk_secret_references
    ADD CONSTRAINT cpk_secret_references_pkey PRIMARY KEY (registration_id);

ALTER TABLE ONLY cpk_secret_references
    ADD CONSTRAINT cpk_secret_references_registration_id_workspace_id_key UNIQUE (registration_id, workspace_id);

ALTER TABLE ONLY cpk_secret_use_authorizations
    ADD CONSTRAINT cpk_secret_use_authorizations_authorization_id_workspace_id_key UNIQUE (authorization_id, workspace_id);

ALTER TABLE ONLY cpk_secret_use_authorizations
    ADD CONSTRAINT cpk_secret_use_authorizations_pkey PRIMARY KEY (authorization_id);

ALTER TABLE ONLY cpk_secret_use_authorizations
    ADD CONSTRAINT cpk_secret_use_authorizations_workspace_id_correlation_id_key UNIQUE (workspace_id, correlation_id);

ALTER TABLE ONLY cpk_workspaces
    ADD CONSTRAINT cpk_workspaces_pkey PRIMARY KEY (workspace_id);

CREATE UNIQUE INDEX cpk_activity_runs_active_request ON cpk_activity_runs USING btree (request_id) WHERE (status = ANY (ARRAY['claimed'::text, 'running'::text, 'paused'::text, 'compensating'::text]));

CREATE UNIQUE INDEX cpk_activity_runs_request_attempt ON cpk_activity_runs USING btree (request_id, attempt);

CREATE UNIQUE INDEX cpk_approval_decisions_idempotency ON cpk_approval_decisions USING btree (request_id, idempotency_key) WHERE (idempotency_key IS NOT NULL);

CREATE UNIQUE INDEX cpk_approval_requests_idempotency ON cpk_approval_requests USING btree (session_id, idempotency_key) WHERE (idempotency_key IS NOT NULL);

CREATE UNIQUE INDEX cpk_approval_requests_rotation_identity ON cpk_approval_requests USING btree (rotation_id) WHERE (rotation_id IS NOT NULL);

CREATE UNIQUE INDEX cpk_cloudflare_ingress_resources_active_key ON cpk_cloudflare_ingress_resources USING btree (workspace_id, ingress_id) WHERE (status = ANY (ARRAY['allocating'::text, 'active'::text, 'removing'::text]));

CREATE INDEX cpk_cloudflare_ingress_resources_workspace ON cpk_cloudflare_ingress_resources USING btree (workspace_id, observed_at DESC, ingress_id, epoch);

CREATE UNIQUE INDEX cpk_delegation_signing_keys_active_scope ON cpk_delegation_signing_keys USING btree (workspace_id, purpose, issuer) WHERE (status = 'active'::text);

CREATE INDEX cpk_delegation_signing_keys_verifier_set ON cpk_delegation_signing_keys USING btree (workspace_id, purpose, issuer, key_id) WHERE (status = ANY (ARRAY['active'::text, 'verify-only'::text]));

CREATE UNIQUE INDEX cpk_execution_requests_active_plan ON cpk_execution_requests USING btree (plan_id) WHERE (status = ANY (ARRAY['queued'::text, 'claimed'::text]));

CREATE UNIQUE INDEX cpk_gateway_key_rotations_nonterminal_binding ON cpk_gateway_key_rotations USING btree (workspace_id, gateway_node_id, purpose, issuer) WHERE (status <> ALL (ARRAY['completed'::text, 'blocked'::text, 'rejected'::text]));

CREATE INDEX cpk_gateway_probe_workspace_timeline ON cpk_gateway_probe_attempts USING btree (workspace_id, issued_at DESC, probe_id DESC);

CREATE UNIQUE INDEX cpk_generated_ingress_secret_references_secret_ref ON cpk_generated_ingress_secret_references USING btree (workspace_id, secret_ref);

CREATE INDEX cpk_image_pull_authorities_active_scope ON cpk_image_pull_authorities USING btree (workspace_id, registry, repository, status);

CREATE UNIQUE INDEX cpk_ingress_authorities_active_ref ON cpk_ingress_authorities USING btree (workspace_id, authority_ref) WHERE (status = 'active'::text);

CREATE INDEX cpk_observations_latest_subject ON cpk_observations USING btree (workspace_id, subject_id, observed_at DESC, observation_id DESC);

CREATE UNIQUE INDEX cpk_operation_actions_idempotency ON cpk_operation_actions USING btree (session_id, idempotency_key) WHERE (idempotency_key IS NOT NULL);

CREATE UNIQUE INDEX cpk_operation_sessions_idempotency ON cpk_operation_sessions USING btree (workspace_id, idempotency_key) WHERE (idempotency_key IS NOT NULL);

CREATE UNIQUE INDEX cpk_runtime_authorities_active_ref ON cpk_runtime_authorities USING btree (workspace_id, authority_ref) WHERE (status = 'active'::text);

CREATE UNIQUE INDEX cpk_runtime_authority_deliveries_active_ref ON cpk_runtime_authority_deliveries USING btree (workspace_id, authority_ref) WHERE (status = 'active'::text);

CREATE UNIQUE INDEX cpk_secret_providers_active_identity ON cpk_secret_providers USING btree (workspace_id, provider_id) WHERE (status = 'active'::text);

CREATE INDEX cpk_secret_providers_history ON cpk_secret_providers USING btree (workspace_id, provider_id, admitted_at, registration_id);

CREATE UNIQUE INDEX cpk_secret_references_active_reference ON cpk_secret_references USING btree (workspace_id, secret_reference) WHERE (status = 'active'::text);

CREATE INDEX cpk_secret_references_history ON cpk_secret_references USING btree (workspace_id, secret_reference, admitted_at, registration_id);

CREATE INDEX cpk_secret_use_authorizations_reference_history ON cpk_secret_use_authorizations USING btree (workspace_id, reference_registration_id, requested_at, authorization_id);

ALTER TABLE ONLY cpk_activity_events
    ADD CONSTRAINT cpk_activity_events_run_id_fkey FOREIGN KEY (run_id) REFERENCES cpk_activity_runs(run_id);

ALTER TABLE ONLY cpk_activity_plans
    ADD CONSTRAINT cpk_activity_plans_base_projection_source_fk FOREIGN KEY (base_realized_projection_id, base_graph_id) REFERENCES cpk_realized_graph_projections(projection_id, source_authored_graph_id);

ALTER TABLE ONLY cpk_activity_plans
    ADD CONSTRAINT cpk_activity_plans_desired_projection_source_fk FOREIGN KEY (desired_realized_projection_id, desired_graph_id) REFERENCES cpk_realized_graph_projections(projection_id, source_authored_graph_id);

ALTER TABLE ONLY cpk_activity_plans
    ADD CONSTRAINT cpk_activity_plans_session_id_fkey FOREIGN KEY (session_id) REFERENCES cpk_operation_sessions(session_id);

ALTER TABLE ONLY cpk_activity_runs
    ADD CONSTRAINT cpk_activity_runs_prior_run_id_fkey FOREIGN KEY (prior_run_id) REFERENCES cpk_activity_runs(run_id);

ALTER TABLE ONLY cpk_activity_runs
    ADD CONSTRAINT cpk_activity_runs_request_plan_fk FOREIGN KEY (request_id, plan_id) REFERENCES cpk_execution_requests(request_id, plan_id);

ALTER TABLE ONLY cpk_approval_decisions
    ADD CONSTRAINT cpk_approval_decisions_request_id_fkey FOREIGN KEY (request_id) REFERENCES cpk_approval_requests(request_id);

ALTER TABLE ONLY cpk_approval_requests
    ADD CONSTRAINT cpk_approval_requests_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES cpk_activity_plans(plan_id);

ALTER TABLE ONLY cpk_approval_requests
    ADD CONSTRAINT cpk_approval_requests_rotation_fk FOREIGN KEY (rotation_id) REFERENCES cpk_gateway_key_rotations(rotation_id);

ALTER TABLE ONLY cpk_approval_requests
    ADD CONSTRAINT cpk_approval_requests_session_id_fkey FOREIGN KEY (session_id) REFERENCES cpk_operation_sessions(session_id);

ALTER TABLE ONLY cpk_cloudflare_ingress_resources
    ADD CONSTRAINT cpk_cloudflare_ingress_resources_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES cpk_workspaces(workspace_id);

ALTER TABLE ONLY cpk_delegation_signing_keys
    ADD CONSTRAINT cpk_delegation_signing_keys_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES cpk_workspaces(workspace_id);

ALTER TABLE ONLY cpk_execution_requests
    ADD CONSTRAINT cpk_execution_requests_approval_identity_fk FOREIGN KEY (approval_decision_id, approval_request_id) REFERENCES cpk_approval_decisions(decision_id, request_id);

ALTER TABLE ONLY cpk_execution_requests
    ADD CONSTRAINT cpk_execution_requests_approval_request_id_fkey FOREIGN KEY (approval_request_id) REFERENCES cpk_approval_requests(request_id);

ALTER TABLE ONLY cpk_execution_requests
    ADD CONSTRAINT cpk_execution_requests_plan_session_fk FOREIGN KEY (plan_id, session_id) REFERENCES cpk_activity_plans(plan_id, session_id);

ALTER TABLE ONLY cpk_execution_requests
    ADD CONSTRAINT cpk_execution_requests_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES cpk_workspaces(workspace_id);

ALTER TABLE ONLY cpk_execution_requests
    ADD CONSTRAINT cpk_execution_requests_workspace_session_fk FOREIGN KEY (session_id, workspace_id) REFERENCES cpk_operation_sessions(session_id, workspace_id);

ALTER TABLE ONLY cpk_gateway_key_rotation_deployments
    ADD CONSTRAINT cpk_gateway_key_rotation_deployments_rotation_id_fkey FOREIGN KEY (rotation_id) REFERENCES cpk_gateway_key_rotations(rotation_id);

ALTER TABLE ONLY cpk_gateway_key_rotation_revocations
    ADD CONSTRAINT cpk_gateway_key_rotation_revocations_rotation_id_fkey FOREIGN KEY (rotation_id) REFERENCES cpk_gateway_key_rotations(rotation_id);

ALTER TABLE ONLY cpk_gateway_key_rotation_transitions
    ADD CONSTRAINT cpk_gateway_key_rotation_transitions_rotation_id_fkey FOREIGN KEY (rotation_id) REFERENCES cpk_gateway_key_rotations(rotation_id);

ALTER TABLE ONLY cpk_gateway_key_rotations
    ADD CONSTRAINT cpk_gateway_key_rotations_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES cpk_workspaces(workspace_id);

ALTER TABLE ONLY cpk_gateway_probe_attempts
    ADD CONSTRAINT cpk_gateway_probe_attempts_current_graph_id_fkey FOREIGN KEY (current_graph_id) REFERENCES cpk_graph_versions(graph_id);

ALTER TABLE ONLY cpk_gateway_probe_attempts
    ADD CONSTRAINT cpk_gateway_probe_attempts_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES cpk_workspaces(workspace_id);

ALTER TABLE ONLY cpk_generated_ingress_secret_references
    ADD CONSTRAINT cpk_generated_ingress_secret_references_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES cpk_workspaces(workspace_id);

ALTER TABLE ONLY cpk_graph_versions
    ADD CONSTRAINT cpk_graph_versions_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES cpk_workspaces(workspace_id);

ALTER TABLE ONLY cpk_image_pull_authorities
    ADD CONSTRAINT cpk_image_pull_authorities_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES cpk_workspaces(workspace_id);

ALTER TABLE ONLY cpk_ingress_authorities
    ADD CONSTRAINT cpk_ingress_authorities_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES cpk_workspaces(workspace_id);

ALTER TABLE ONLY cpk_observations
    ADD CONSTRAINT cpk_observations_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES cpk_workspaces(workspace_id);

ALTER TABLE ONLY cpk_operation_actions
    ADD CONSTRAINT cpk_operation_actions_session_id_fkey FOREIGN KEY (session_id) REFERENCES cpk_operation_sessions(session_id);

ALTER TABLE ONLY cpk_operation_sessions
    ADD CONSTRAINT cpk_operation_sessions_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES cpk_workspaces(workspace_id);

ALTER TABLE ONLY cpk_realized_graph_projections
    ADD CONSTRAINT cpk_realized_graph_projection_source FOREIGN KEY (source_authored_graph_id, workspace_id) REFERENCES cpk_graph_versions(graph_id, workspace_id);

ALTER TABLE ONLY cpk_realized_graph_projections
    ADD CONSTRAINT cpk_realized_graph_projections_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES cpk_workspaces(workspace_id);

ALTER TABLE ONLY cpk_registered_products
    ADD CONSTRAINT cpk_registered_products_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES cpk_workspaces(workspace_id);

ALTER TABLE ONLY cpk_runtime_authorities
    ADD CONSTRAINT cpk_runtime_authorities_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES cpk_workspaces(workspace_id);

ALTER TABLE ONLY cpk_runtime_authority_deliveries
    ADD CONSTRAINT cpk_runtime_authority_deliveries_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES cpk_workspaces(workspace_id);

ALTER TABLE ONLY cpk_secret_providers
    ADD CONSTRAINT cpk_secret_providers_supersedes_fk FOREIGN KEY (supersedes_registration_id, workspace_id) REFERENCES cpk_secret_providers(registration_id, workspace_id);

ALTER TABLE ONLY cpk_secret_providers
    ADD CONSTRAINT cpk_secret_providers_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES cpk_workspaces(workspace_id);

ALTER TABLE ONLY cpk_secret_references
    ADD CONSTRAINT cpk_secret_references_provider_fk FOREIGN KEY (provider_registration_id, workspace_id) REFERENCES cpk_secret_providers(registration_id, workspace_id);

ALTER TABLE ONLY cpk_secret_references
    ADD CONSTRAINT cpk_secret_references_supersedes_fk FOREIGN KEY (supersedes_registration_id, workspace_id) REFERENCES cpk_secret_references(registration_id, workspace_id);

ALTER TABLE ONLY cpk_secret_references
    ADD CONSTRAINT cpk_secret_references_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES cpk_workspaces(workspace_id);

ALTER TABLE ONLY cpk_secret_use_authorizations
    ADD CONSTRAINT cpk_secret_use_authorizations_provider_fk FOREIGN KEY (provider_registration_id, workspace_id) REFERENCES cpk_secret_providers(registration_id, workspace_id);

ALTER TABLE ONLY cpk_secret_use_authorizations
    ADD CONSTRAINT cpk_secret_use_authorizations_reference_fk FOREIGN KEY (reference_registration_id, workspace_id) REFERENCES cpk_secret_references(registration_id, workspace_id);

ALTER TABLE ONLY cpk_secret_use_authorizations
    ADD CONSTRAINT cpk_secret_use_authorizations_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES cpk_workspaces(workspace_id);

ALTER TABLE ONLY cpk_workspaces
    ADD CONSTRAINT cpk_workspaces_current_projection_source_fk FOREIGN KEY (current_realized_projection_id, current_graph_id) REFERENCES cpk_realized_graph_projections(projection_id, source_authored_graph_id);

ALTER TABLE ONLY cpk_workspaces
    ADD CONSTRAINT cpk_workspaces_current_realized_projection_fk FOREIGN KEY (current_realized_projection_id, workspace_id) REFERENCES cpk_realized_graph_projections(projection_id, workspace_id);

ALTER TABLE ONLY cpk_workspaces
    ADD CONSTRAINT cpk_workspaces_desired_projection_source_fk FOREIGN KEY (desired_realized_projection_id, desired_graph_id) REFERENCES cpk_realized_graph_projections(projection_id, source_authored_graph_id);

ALTER TABLE ONLY cpk_workspaces
    ADD CONSTRAINT cpk_workspaces_desired_realized_projection_fk FOREIGN KEY (desired_realized_projection_id, workspace_id) REFERENCES cpk_realized_graph_projections(projection_id, workspace_id);
