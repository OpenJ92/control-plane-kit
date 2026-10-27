CREATE TABLE cpk_ingress_invocation_contexts (
    context_id text NOT NULL,
    version integer NOT NULL,
    run_id text NOT NULL,
    command_key text NOT NULL,
    parent_fingerprint text NOT NULL,
    workspace_id text NOT NULL,
    ingress_id text NOT NULL,
    activity_id text NOT NULL,
    original_event_id text NOT NULL,
    ingress_registration_id text NOT NULL,
    created_at timestamp(6) with time zone NOT NULL,
    descriptor jsonb NOT NULL,
    CONSTRAINT cpk_ingress_contexts_pkey PRIMARY KEY (context_id),
    CONSTRAINT cpk_ingress_contexts_slot_key
        UNIQUE (run_id, command_key, activity_id),
    CONSTRAINT cpk_ingress_contexts_event_key
        UNIQUE (workspace_id, run_id, original_event_id),
    CONSTRAINT cpk_ingress_contexts_version_check CHECK (version = 1),
    CONSTRAINT cpk_ingress_contexts_identity_check CHECK (
        (context_id COLLATE "C") ~ '^[0-9a-f]{64}$'
        AND (parent_fingerprint COLLATE "C") ~ '^[0-9a-f]{64}$'
        AND (activity_id COLLATE "C") ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$'
        AND octet_length(ingress_id) BETWEEN 1 AND 2048
    ),
    CONSTRAINT cpk_ingress_contexts_descriptor_check CHECK (
        jsonb_typeof(descriptor) = 'object'
        AND octet_length(descriptor::text) <= 32768
    ),
    CONSTRAINT cpk_ingress_contexts_parent_fk
        FOREIGN KEY (run_id, command_key)
        REFERENCES cpk_execution_command_receipts(run_id, idempotency_key),
    CONSTRAINT cpk_ingress_contexts_workspace_fk
        FOREIGN KEY (workspace_id) REFERENCES cpk_workspaces(workspace_id),
    CONSTRAINT cpk_ingress_contexts_event_fk
        FOREIGN KEY (original_event_id) REFERENCES cpk_activity_events(event_id),
    CONSTRAINT cpk_ingress_contexts_registration_fk
        FOREIGN KEY (ingress_registration_id)
        REFERENCES cpk_ingress_authorities(registration_id)
);

CREATE TABLE cpk_ingress_invocation_results (
    invocation_id text NOT NULL,
    context_id text NOT NULL,
    recorded_at timestamp(6) with time zone NOT NULL,
    descriptor jsonb NOT NULL,
    CONSTRAINT cpk_ingress_results_pkey PRIMARY KEY (invocation_id),
    CONSTRAINT cpk_ingress_results_identity_check CHECK (
        (invocation_id COLLATE "C") ~ '^[0-9a-f]{64}$'
        AND (context_id COLLATE "C") ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT cpk_ingress_results_descriptor_check CHECK (
        jsonb_typeof(descriptor) = 'object'
        AND octet_length(descriptor::text) <= 8192
    ),
    CONSTRAINT cpk_ingress_results_invocation_fk
        FOREIGN KEY (invocation_id)
        REFERENCES cpk_execution_command_invocations(invocation_id),
    CONSTRAINT cpk_ingress_results_context_fk
        FOREIGN KEY (context_id)
        REFERENCES cpk_ingress_invocation_contexts(context_id)
);

CREATE INDEX cpk_ingress_results_context_page
    ON cpk_ingress_invocation_results (context_id, invocation_id);
