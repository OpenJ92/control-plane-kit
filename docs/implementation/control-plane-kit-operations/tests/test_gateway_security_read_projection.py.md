Source: [test_gateway_security_read_projection.py](../../../../control-plane-kit-operations/tests/test_gateway_security_read_projection.py).
Maintain this companion alongside its source.

The existing suite preserves gateway-probe projection, key catalogue redaction,
bounded failures, owner/facade method partition and inward dependency laws.
#1877 adds only workload_verifier_configuration(workspace_id, purposes) to its
exact owner/facade partition; every old entry and assertion remains. New workload
behavior, public mapping and actual lock coherence belong to the companion
PostgreSQL workload-verifier suite. Existing gateway policy is unchanged.
