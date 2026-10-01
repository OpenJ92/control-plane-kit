Source: [test_postgres_native_connection_fold.py](../../../../control-plane-kit-operations/tests/test_postgres_native_connection_fold.py).

Native `CONNECTOR_CONNECTED` observation deliberately has no signed-health
target. Its start-value override explicitly uses the original
`HealthEffectStartValues.health_start_value` builder rather than the shared
signed-health fixture's product enrichment. The override still binds the actual
runtime authority and recomputes the request fingerprint.

Real workspace/desired/admission setup, actual generic first-start and native
fold, authority/approval checks, clock and lock-order observations, replay,
rollback, codec and negative assertions remain unchanged. No signed target is
manufactured, no shared fallback is added, and no production rule changes.
Recorded predecessor events and recording-port observations remain fixture
premises, not deployment or provider evidence.

At `611a3eab`, all eleven methods failed in shared setup while dereferencing
the absent signed-health target, before their generic start or fold. The explicit
builder correction remains unvalidated until an authorized owning gate reaches
those bodies and completes compilation/import.
