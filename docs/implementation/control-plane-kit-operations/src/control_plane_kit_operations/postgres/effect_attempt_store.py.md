Source: [effect_attempt_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_attempt_store.py).
Maintain this companion alongside its source.

B1 configuration attempt insertion requires the same prepared owner value as
its original intent. Complete bounded attempt reads preserve the existing
original/latest event and state-commitment decoders, sharing the command budget.
No independent commit, alternative state machine or physical-use permission is
introduced.
