Source: [control-plane-kit-operations/tests/test_postgres_effect_attempt_intent_store_contract.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_intent_store_contract.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

These tests protect the private [intent store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_attempt_intent_store.py) boundary using recording/failing connections and shared intent fixtures. The important behavioral laws are invalid exact-value inputs stopping before SQL, bounded exact-key reads, caller-owned transaction behavior, fixed expected errors and unmodified unexpected faults.

This file also contains explicit surface/import/call-shape and historical module-inventory witnesses. Those are existing architectural tests, not a requirement to reproduce their machinery in new application tests. When changing ownership deliberately, update the owning policy rather than disguising a new owner behind the old shape.

The fakes do not exercise Postgres constraints, rollback or competing transactions. Those claims belong to the adjacent store/schema/start integration tests and the established Docker-backed Operations suite. Fixture values demonstrate correlation laws; their literal identities are not deployment invariants.
