Source: [test_postgres_http_sample_secret_uses.py](../../../../control-plane-kit-operations/tests/test_postgres_http_sample_secret_uses.py).

These tests use the established PostgreSQL/UoW and registration owners. Supplied
plan/check context is not admitted execution history. They prove atomic pair
visibility, real rollback on a second driver insert fault, exact sample-specific
reauthorization without a third row, partial/missing refusal, original-key
rotation to verify-only then retirement while the replacement remains active,
refusal to substitute that replacement and exact original history lookup, and overlap with a normal single-role
authorizer holding the second reference. Same-key, same-effect valid sibling-check
pairs cannot substitute for one another. No resolver or external effect runs.
Normal current-command/permission/lease and public-API producer acceptance remain
owned by ExecutionInvocationService and the composed source-built installation.
