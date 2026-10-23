Source: [effect_attempt_reconciliation_interpreter.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_reconciliation_interpreter.py).
Maintain this companion alongside its source.


B1 / #1923 reconciliation joins the configuration command ledger before its
original request/run/attempt/outcome reads. The active route uses existing
bounded store decoders. The observer runs outside a database transaction, and
fresh outcome folding acquires its own lifecycle-first transaction prefix.
Original proof does not grant current runtime authority or permission to repeat
a possibly completed effect.
