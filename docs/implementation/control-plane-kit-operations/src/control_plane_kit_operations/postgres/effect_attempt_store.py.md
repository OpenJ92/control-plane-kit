Source: [effect_attempt_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_attempt_store.py).
Maintain this companion alongside its source.

B1 configuration attempt insertion requires the same prepared owner value as
its original intent. Complete bounded attempt reads preserve the existing
original/latest event and state-commitment decoders, sharing the command budget.
No independent commit, alternative state machine or physical-use permission is
introduced.

Insert-if-absent and compare-and-set also reserve their bounded RETURNING row
before mutation under an active configuration ledger. Successful rows and empty
conflict results both charge the SQL statement; returned values/markers/identity
are charged when present. Exhausted capacity prevents the statement, and the
caller still owns commit/rollback. Cursor-observed owner tests cover success,
zero-row replay/conflict and unchanged durable truth after refusal/rollback.
