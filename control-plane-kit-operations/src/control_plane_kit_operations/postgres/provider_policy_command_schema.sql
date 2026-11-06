CREATE TABLE cpk_provider_policy_commands (
    command_id text NOT NULL,
    installation_id text NOT NULL,
    caller_issuer text NOT NULL,
    caller_subject_id text NOT NULL,
    caller_kind text NOT NULL,
    logical_provider_id text NOT NULL,
    binding_id text NOT NULL,
    provider_id text NOT NULL,
    idempotency_key text NOT NULL,
    request_fingerprint text NOT NULL,
    provider_correlation text NOT NULL,
    operation text NOT NULL,
    intent_descriptor jsonb NOT NULL,
    dispatch_claimed_at timestamp(6) with time zone,
    created_at timestamp(6) with time zone NOT NULL,
    CONSTRAINT cpk_provider_policy_commands_pkey PRIMARY KEY (command_id),
    CONSTRAINT cpk_provider_policy_commands_scope_key UNIQUE
        (installation_id, caller_issuer, caller_subject_id, caller_kind,
         logical_provider_id, idempotency_key),
    CONSTRAINT cpk_provider_policy_commands_correlation_key UNIQUE (provider_correlation),
    CONSTRAINT cpk_provider_policy_commands_identity_check CHECK (
        (command_id COLLATE "C") ~ '^[0-9a-f]{64}$'
        AND octet_length(installation_id) BETWEEN 1 AND 512
        AND octet_length(caller_issuer) BETWEEN 1 AND 512
        AND octet_length(caller_subject_id) BETWEEN 1 AND 512
        AND caller_kind IN ('operator', 'service', 'worker')
        AND octet_length(logical_provider_id) BETWEEN 1 AND 128
        AND octet_length(binding_id) BETWEEN 1 AND 200
        AND octet_length(provider_id) BETWEEN 1 AND 512
        AND octet_length(idempotency_key) BETWEEN 1 AND 200
        AND (request_fingerprint COLLATE "C") ~ '^[0-9a-f]{64}$'
        AND octet_length(provider_correlation) BETWEEN 1 AND 128
    ),
    CONSTRAINT cpk_provider_policy_commands_operation_check CHECK (
        operation IN ('grant', 'revoke')
    ),
    CONSTRAINT cpk_provider_policy_commands_intent_check CHECK (
        jsonb_typeof(intent_descriptor) = 'object'
        AND octet_length(intent_descriptor::text) <= 8192
    ),
    CONSTRAINT cpk_provider_policy_commands_time_check CHECK (
        dispatch_claimed_at IS NULL OR dispatch_claimed_at >= created_at
    )
);

CREATE TABLE cpk_provider_policy_outcome_observations (
    observation_id text NOT NULL,
    command_id text NOT NULL,
    source text NOT NULL,
    evidence_fingerprint text NOT NULL,
    descriptor jsonb NOT NULL,
    observed_at timestamp(6) with time zone NOT NULL,
    CONSTRAINT cpk_provider_policy_observations_pkey PRIMARY KEY (observation_id),
    CONSTRAINT cpk_provider_policy_observations_evidence_key
        UNIQUE (command_id, evidence_fingerprint),
    CONSTRAINT cpk_provider_policy_observations_identity_check CHECK (
        (observation_id COLLATE "C") ~ '^[0-9a-f]{64}$'
        AND (evidence_fingerprint COLLATE "C") ~ '^[0-9a-f]{64}$'
    ),
    CONSTRAINT cpk_provider_policy_observations_source_check CHECK (
        source IN ('initial-response', 'command-result-read')
    ),
    CONSTRAINT cpk_provider_policy_observations_descriptor_check CHECK (
        jsonb_typeof(descriptor) = 'object'
        AND octet_length(descriptor::text) <= 8192
    ),
    CONSTRAINT cpk_provider_policy_observations_command_fk FOREIGN KEY (command_id)
        REFERENCES cpk_provider_policy_commands(command_id)
);

CREATE INDEX cpk_provider_policy_observations_command_page
    ON cpk_provider_policy_outcome_observations (command_id, observed_at, observation_id);
