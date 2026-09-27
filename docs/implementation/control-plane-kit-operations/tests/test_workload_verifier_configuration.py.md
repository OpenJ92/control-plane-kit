Source: [test_workload_verifier_configuration.py](../../../../control-plane-kit-operations/tests/test_workload_verifier_configuration.py).
Maintain this companion alongside its source.

The #1877 targets use the ordinary real PostgreSQL fixture and authenticated
Operations read service through both HTTP and MCP request mappings. They prove
closed requested-family selection, exact active-issuer verification membership,
workspace/purpose/issuer isolation, no private-reference or catalogue internals,
missing/ambiguous authority refusal, a bounded store read with overflow sentinel,
full-envelope size refusal, and authority before store acquisition.

The concurrency target observes an actual PostgreSQL lock wait. A workload read
holds the first family's shared purpose lock while another connection tries to
revoke one verification key. The reader then finishes the remaining family and
exits its existing public-read UoW; only then can revocation complete. The first
snapshot includes that key and a fresh read excludes it. Input order is reversed
to exercise canonical lock ordering. Events only coordinate the interleaving;
they neither simulate stores nor decide authority. An initial ordinary read
exposes the missing route before any concurrency apparatus is started.

Synthetic PEM values exercise existing Operations registration/projection laws;
these tests do not claim SDK cryptographic verification or Servers delivery.
Existing gateway and key-lifecycle suites remain the compatibility baseline.
