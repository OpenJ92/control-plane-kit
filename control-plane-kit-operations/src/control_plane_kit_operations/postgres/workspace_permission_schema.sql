CREATE TABLE cpk_workspace_permission_installation (
    singleton boolean NOT NULL,
    installation_id text NOT NULL,
    receipt jsonb NOT NULL,
    CONSTRAINT cpk_workspace_permission_installation_pkey PRIMARY KEY (singleton),
    CONSTRAINT cpk_workspace_permission_installation_unique_0 UNIQUE (installation_id),
    CONSTRAINT cpk_workspace_permission_installation_singleton_check CHECK (singleton),
    CONSTRAINT cpk_workspace_permission_installation_receipt_check CHECK (((jsonb_typeof(receipt) = 'object'::text) AND (octet_length((receipt)::text) <= 32768))),
    CONSTRAINT cpk_workspace_permission_installation_installation_id_check CHECK (((octet_length(installation_id) >= 1) AND (octet_length(installation_id) <= 512)))
);

CREATE TABLE cpk_workspace_permissions (
    installation_id text NOT NULL,
    workspace_id text NOT NULL,
    issuer text NOT NULL,
    subject_id text NOT NULL,
    kind text NOT NULL,
    scopes jsonb NOT NULL,
    revision bigint NOT NULL,
    CONSTRAINT cpk_workspace_permissions_pkey PRIMARY KEY (installation_id, workspace_id, issuer, subject_id, kind),
    CONSTRAINT cpk_workspace_permissions_kind_check CHECK ((kind = ANY (ARRAY['operator'::text, 'service'::text, 'worker'::text]))),
    CONSTRAINT cpk_workspace_permissions_scopes_check CHECK (((jsonb_typeof(scopes) = 'array'::text) AND (octet_length((scopes)::text) <= 8192))),
    CONSTRAINT cpk_workspace_permissions_revision_check CHECK ((revision >= 0)),
    CONSTRAINT cpk_workspace_permissions_installation_id_check CHECK (((octet_length(installation_id) >= 1) AND (octet_length(installation_id) <= 512))),
    CONSTRAINT cpk_workspace_permissions_workspace_id_check CHECK (((octet_length(workspace_id) >= 1) AND (octet_length(workspace_id) <= 512))),
    CONSTRAINT cpk_workspace_permissions_issuer_check CHECK (((octet_length(issuer) >= 1) AND (octet_length(issuer) <= 512))),
    CONSTRAINT cpk_workspace_permissions_subject_id_check CHECK (((octet_length(subject_id) >= 1) AND (octet_length(subject_id) <= 512))),
    CONSTRAINT cpk_workspace_permissions_kind_bound_check CHECK (((octet_length(kind) >= 1) AND (octet_length(kind) <= 512))),
    CONSTRAINT cpk_workspace_permissions_fk_0 FOREIGN KEY (installation_id) REFERENCES cpk_workspace_permission_installation (installation_id),
    CONSTRAINT cpk_workspace_permissions_fk_1 FOREIGN KEY (workspace_id) REFERENCES cpk_workspaces (workspace_id)
);

CREATE TABLE cpk_workspace_permission_receipts (
    installation_id text NOT NULL,
    workspace_id text NOT NULL,
    issuer text NOT NULL,
    subject_id text NOT NULL,
    kind text NOT NULL,
    idempotency_key text NOT NULL,
    revision bigint NOT NULL,
    intent_fingerprint text NOT NULL,
    receipt jsonb NOT NULL,
    CONSTRAINT cpk_workspace_permission_receipts_pkey PRIMARY KEY (installation_id, workspace_id, issuer, subject_id, kind, idempotency_key),
    CONSTRAINT cpk_workspace_permission_receipts_unique_0 UNIQUE (installation_id, workspace_id, issuer, subject_id, kind, revision),
    CONSTRAINT cpk_workspace_permission_receipts_receipt_check CHECK (((jsonb_typeof(receipt) = 'object'::text) AND (octet_length((receipt)::text) <= 16384))),
    CONSTRAINT cpk_workspace_permission_receipts_installation_id_check CHECK (((octet_length(installation_id) >= 1) AND (octet_length(installation_id) <= 512))),
    CONSTRAINT cpk_workspace_permission_receipts_workspace_id_check CHECK (((octet_length(workspace_id) >= 1) AND (octet_length(workspace_id) <= 512))),
    CONSTRAINT cpk_workspace_permission_receipts_issuer_check CHECK (((octet_length(issuer) >= 1) AND (octet_length(issuer) <= 512))),
    CONSTRAINT cpk_workspace_permission_receipts_subject_id_check CHECK (((octet_length(subject_id) >= 1) AND (octet_length(subject_id) <= 512))),
    CONSTRAINT cpk_workspace_permission_receipts_kind_check CHECK (((octet_length(kind) >= 1) AND (octet_length(kind) <= 512))),
    CONSTRAINT cpk_workspace_permission_receipts_idempotency_key_check CHECK (((octet_length(idempotency_key) >= 1) AND (octet_length(idempotency_key) <= 200))),
    CONSTRAINT cpk_workspace_permission_receipts_intent_fingerprint_check CHECK (((octet_length(intent_fingerprint) >= 1) AND (octet_length(intent_fingerprint) <= 512))),
    CONSTRAINT cpk_workspace_permission_receipts_fk_0 FOREIGN KEY (installation_id, workspace_id, issuer, subject_id, kind) REFERENCES cpk_workspace_permissions (installation_id, workspace_id, issuer, subject_id, kind)
);

CREATE TABLE cpk_workspace_administration_receipts (
    installation_id text NOT NULL,
    issuer text NOT NULL,
    subject_id text NOT NULL,
    kind text NOT NULL,
    idempotency_key text NOT NULL,
    workspace_id text NOT NULL,
    intent_fingerprint text NOT NULL,
    receipt jsonb NOT NULL,
    CONSTRAINT cpk_workspace_administration_receipts_pkey PRIMARY KEY (installation_id, issuer, subject_id, kind, idempotency_key),
    CONSTRAINT cpk_workspace_administration_receipts_receipt_check CHECK (((jsonb_typeof(receipt) = 'object'::text) AND (octet_length((receipt)::text) <= 16384))),
    CONSTRAINT cpk_workspace_administration_receipts_installation_id_check CHECK (((octet_length(installation_id) >= 1) AND (octet_length(installation_id) <= 512))),
    CONSTRAINT cpk_workspace_administration_receipts_issuer_check CHECK (((octet_length(issuer) >= 1) AND (octet_length(issuer) <= 512))),
    CONSTRAINT cpk_workspace_administration_receipts_subject_id_check CHECK (((octet_length(subject_id) >= 1) AND (octet_length(subject_id) <= 512))),
    CONSTRAINT cpk_workspace_administration_receipts_kind_check CHECK (((octet_length(kind) >= 1) AND (octet_length(kind) <= 512))),
    CONSTRAINT cpk_workspace_administration_receipts_idempotency_key_check CHECK (((octet_length(idempotency_key) >= 1) AND (octet_length(idempotency_key) <= 200))),
    CONSTRAINT cpk_workspace_administration_receipts_workspace_id_check CHECK (((octet_length(workspace_id) >= 1) AND (octet_length(workspace_id) <= 512))),
    CONSTRAINT cpk_workspace_administration_receipts_intent_fingerprint_check CHECK (((octet_length(intent_fingerprint) >= 1) AND (octet_length(intent_fingerprint) <= 512))),
    CONSTRAINT cpk_workspace_administration_receipts_fk_0 FOREIGN KEY (installation_id) REFERENCES cpk_workspace_permission_installation (installation_id),
    CONSTRAINT cpk_workspace_administration_receipts_fk_1 FOREIGN KEY (workspace_id) REFERENCES cpk_workspaces (workspace_id)
);
