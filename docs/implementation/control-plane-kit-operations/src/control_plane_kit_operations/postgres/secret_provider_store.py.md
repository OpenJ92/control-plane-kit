Source: [secret_provider_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/secret_provider_store.py).

Provider/reference/use stores share the caller's transaction and do not commit.
Existing active row locks and correlation locks remain authoritative primitives.
`SecretUseAuthorizationStore.for_correlations` accepts exactly two distinct
workspace correlations and reads both under one statement snapshot, bounded by
their unique identities plus one overflow witness. It does not classify two
separate READ COMMITTED observations as a partial pair or change either row.
