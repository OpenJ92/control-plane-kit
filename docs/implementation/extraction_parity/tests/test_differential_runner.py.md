Source: [extraction_parity/tests/test_differential_runner.py](../../../../extraction_parity/tests/test_differential_runner.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These tests execute small Python subprocesses through sys.executable. The helper
writes JSON to CPK_PARITY_BEHAVIOR_PATH and supplies a synthetic source digest;
it does not authenticate a repository revision or build a container. Completed
capture assertions check process exit zero, behavior and executable identity.
A sleeping child and a large print exercise timeout and retained-output limits.

Declared-secret cases check exact environment-value redaction in stdout and
rejection of that literal value in structured behavior. Repeated secrets exercise
redaction expansion, including the fallback marker and the empty payload needed
for a smaller bound. Missing or malformed behavior becomes infrastructure-failure.
The capture CLI case has a child create a declared artifact and checks the written
observation's identity, artifact name and decoded bytes.

Two captures with matching behavior produce repeatable passing evidence with
canonical SHA-256 syntax; different response values produce failed evidence.
Those tests rely on the [comparison language](../differential.py.md) and exercise
the [runner](../runner.py.md) as the effect boundary. They do not independently
verify source provenance or the evidence digest against an external producer.

Coverage does not establish a process-tree or universal wall-time bound,
exceptional cleanup, artifact containment/freshness, ambient-environment secrecy,
partial or encoded secret handling, or atomic result/evidence publication. The
compare CLI's partial-write behavior is not exercised here. The complete 177-line
test owner and 301-line runner were read for this companion; no subprocess,
capture command, test or artifact generation was executed during that review.
