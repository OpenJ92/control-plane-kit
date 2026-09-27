Source: [delegation_signing_key_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/delegation_signing_key_store.py).
Maintain this companion alongside its source.

list_for_verification retains the exact workspace/purpose/issuer and active/verify-only predicate and deterministic key-id order. Its optional integer limit (1 through 100) is passed as a SQL LIMIT parameter before fetchall; omission keeps prior unbounded behavior. The workload read requests 17 and refuses overflow. Existing shared purpose selection locks and exclusive lifecycle locks remain transaction-scoped and unchanged. Stores do not commit independently.

