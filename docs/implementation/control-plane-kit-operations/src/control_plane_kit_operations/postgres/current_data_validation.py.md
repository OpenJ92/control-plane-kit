Source: [current_data_validation.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_data_validation.py).
Maintain this companion alongside its source.

Retained approval validation accepts the four legacy rotation purposes, including
`gateway-node-control-transit`. Existing rotation SQL and actual request/approval
services already accepted that value; the previous narrower validator rejected
their valid output on schema reentry. The strengthened real-service test verifies
the exact stored subject and digest before and after query-only reentry.

Health purposes remain absent from rotation storage and workflow admission. This
validator checks retained truth; membership is not authority to generate, approve
or execute a new operation. No row rewrite, backfill or migration occurs. Existing
bounded validation, canonical digest, transaction and error-redaction behavior is
preserved. The new test proves durable consistency, not runtime rotation effects.
