Source: [secret_provider_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/secret_provider_store.py).

Provider/reference/use stores share the caller's transaction and do not commit.
Existing active row locks and correlation locks remain authoritative primitives.
`SecretUseAuthorizationStore.for_correlations` accepts exactly two distinct
workspace correlations and reads both under one statement snapshot, bounded by
their unique identities plus one overflow witness. It does not classify two
separate READ COMMITTED observations as a partial pair or change either row.

#2089 adds `for_three_correlations` for the original SQL password/transit/workload
roles. Three distinct exact correlations are selected in one statement snapshot,
with the existing unique identity and bounded decoder plus a four-row overflow
witness. The existing two-role contract and query remain literal. No schema,
write, lock or general list API changes are introduced by this read.
