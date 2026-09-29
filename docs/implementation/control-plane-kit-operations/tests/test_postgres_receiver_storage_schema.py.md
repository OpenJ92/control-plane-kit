Source: [test_postgres_receiver_storage_schema.py](../../../../control-plane-kit-operations/tests/test_postgres_receiver_storage_schema.py).
Maintain this companion alongside its source.

Real PostgreSQL tests protect exact receiver catalog ownership and restrictive workspace/source/action/session/draft/scope references, paired checks, no-repair current verification and rejection of the exact pre-B schema. They preserve the installer policy: empty namespace or exact current contract, otherwise refuse unchanged.
