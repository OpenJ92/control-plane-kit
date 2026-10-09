Source: [test_effect_attempt_coordinator_contract.py](../../../../control-plane-kit-operations/tests/test_effect_attempt_coordinator_contract.py).

The O2 coordinator consumes Core `ReceiverHealthReadResult` for signed health
while retaining `NodeHealthReadOutcome` as the existing outcome vocabulary.
Its exact import expectation therefore substitutes the receiver result import,
and its expected inventory dependencies add `receiver_health_read_results`.
The older node-health result module remains a dependency for the outcome enum.
The exact call policy and all behavioral assertions remain unchanged.

The source gate at `6c51f008` exposed the stale import and dependency assertions.
The correction at `611a3eab` passed the dependency assertion but raised during
import-policy construction: the substituted row was not in canonical module
order. The row now sits after policies and before runtime-effect imports, as
required by the pinned architecture policy. Its value and every other entry,
the order-preserving helper, exact policies and assertions remain unchanged.
Correction validation is pending; the 43 receiver-health target passes do not
replace the full Operations package gate or downstream adoption evidence.
