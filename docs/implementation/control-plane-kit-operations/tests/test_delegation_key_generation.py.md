Source: [control-plane-kit-operations/tests/test_delegation_key_generation.py](../../../../control-plane-kit-operations/tests/test_delegation_key_generation.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These are real Postgres service tests using the Operations suite's database URL.
setUp installs the current schema, truncates cpk_workspaces CASCADE, inserts a
workspace and admits a provider/reference prefix. They require the isolated
owning suite context; the source is not a read-only diagnostic against an existing
database. Provider output is a local structural fixture with synthetic public
PEM/version values, not an invoked generator or custody service.

The [generation service](../src/control_plane_kit_operations/delegation_key_generation.py.md)
cases require generation scope separately from provider use, registration and key
use; admission separately requires registration scope. Successful folding reads
both stored reference and public identity through a new unit of work, then repeats
admission with replayed=true and expects the same records. A second-write key-ID
conflict checks that the newly attempted reference rolls back. Mismatch cases
alter workspace, issuer, correlation and reference and leave no admitted reference.

The grant repr checks exclude selected private/token markers for these fixtures;
they do not establish general redaction or cryptographic validity. Despite the
mismatch test's unadmitted-provider wording, its cases do not revoke/change the
provider between prepare and fold. The file also does not exercise a real
generation effect, malformed structural-result branches, provider concurrency,
uncertain effect recovery or process restart. New unit-of-work reads prove the
fixture's durable records, not end-to-end provider custody.

The complete 328-line test and 406-line service were read with actual selected
custody, signing/reference store and unit-of-work contracts. No test, database
connection, key generation, credential lookup or provider action ran for this
companion. The suite selects Core from this repository's checkout; no newer
external Core implementation was substituted in the review.
