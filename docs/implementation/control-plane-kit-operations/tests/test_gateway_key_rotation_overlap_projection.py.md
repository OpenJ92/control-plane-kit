Source: [control-plane-kit-operations/tests/test_gateway_key_rotation_overlap_projection.py](../../../../control-plane-kit-operations/tests/test_gateway_key_rotation_overlap_projection.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These ten tests exercise
[overlap desired-projection publication](../src/control_plane_kit_operations/gateway_key_rotation_overlap.py.md)
with real PostgreSQL/UoW persistence. setUp requires the operations test database,
installs/verifies the schema, truncates workspaces CASCADE and seeds new truth;
tearDown closes the connection. This file requires an isolated test database and
mutates it. The tests were inspected, not executed for this documentation change.

The local fixture builds an authored graph with two gateways and delegation
bindings, a realized A projection with an unrelated gateway verifier, matching
current/desired pointers, an open session, active key-a and verify-only key-b.
It directly inserts a key-generated rotation at version 5 with placeholder
approval/generation/version identities and fixed digest text. It does not execute
the rotation request/approval/generation chain or create the referenced approval
records. Synthetic public PEM strings satisfy the Core text contract but are not
cryptographically generated keys. There is no product registration in this local
fixture and no provider custody or runtime adapter execution.

This file does not use
[GatewayRotationOverlapFixture](gateway_rotation_overlap_fixture.py.md); that
shared fixture is used by other rotation tests and has stronger seeded product
and approval context. Keeping those fixtures distinct prevents a publication
test from being mistaken for evidence of execution admission or approved live
key rotation.

The main publication test checks that the target verifier contains key-a/key-b,
the other verifier retains key-other, authored/current graph identity and current
projection remain unchanged, desired projection changes and revision increments
from 1 to 2. It checks the authored descriptor is unchanged, exactly one authored
graph row remains, and the session receives one ordinal-1 publication action.
It checks absence of PRIVATE KEY text in the projection descriptor, a focused
public-material assertion rather than exhaustive secret/error-path redaction.

Replay returns the original action and projection identity with replayed=True,
while reuse with another expected rotation version conflicts. Tests do not
advance key/rotation/workspace truth after the first publication and retry, nor
check changed actors, closed-session replay, stale action evidence or concurrent
publication. The service's receipt-based replay semantics are therefore described
from the actual source rather than inferred from this limited replay assertion.
There is also no equivalent-material, different-stored-projection-ID replay case.
In that case source inspection shows first publication can reuse the row, while
replay conflicts: the retained fingerprint used the original candidate ID and
the replay command uses the returned row ID. The changed-material collision test
below does not cover this same-material identity mismatch.

Negative source-truth cases reject a stale rotation version, an old key changed
from active to verify-only, an extra verification key, a missing replacement key,
a nonexistent target binding, wrong issuer and an authored graph with bindings
removed. The stale-workspace matrix covers expected authored/current/desired
projection IDs and desired revision, followed by an actual changed revision.
An empty scope tuple is denied. These cases do not exhaust replacement reference,
audience, public-key material, extra unbound verifier, purpose or key-lifecycle
race variations checked or exposed by the implementation.

The closed-session test verifies conflict and an unchanged desired pointer and
revision. The stale-key case also reads those pointer fields back unchanged.
Other failure tests generally assert the exception, not a complete absence of
every possible partial row. A preexisting different projection under the exact
deterministic overlap identity conflicts, protecting against binding the same
projection identity/key to changed material.

The late-failure test first inserts an action in another session with the action
ID that publication will attempt to use. A real psycopg UniqueViolation occurs
at the final action insert. A fresh UoW then verifies the overlap projection is
absent and the workspace desired pointer/revision are still projection-a/1.
This is concrete transaction rollback evidence across projection save, pointer
CAS and history insertion. It does not simulate connection loss during commit,
an uncertain commit outcome or external compensation.

Test clocks and action IDs are deterministic. Assertions query committed rows
through fresh UoWs or the autocommit test connection; none start a new operating
system process. No deployment plan/run, worker claim, accepted current graph,
new active signing key, grant drain or revocation is established by this file.
The runtime named docker is a topology value, and the tests issue no provider
or gateway request. Publication is local desired state and operation history.

Read depth: full 539-line test and 216-line overlap owner; full actual 344-line
shared projection builder and 384-line desired-publication owner; retained full
620-line shared fixture for comparison. Selected actual Core materializer/key/
projection-record and PostgreSQL graph/history/signing-key contracts were read.
The service's error wrapping can retain original messages and causes; the test's
expected raw UniqueViolation also shows not every failure is normalized. This
documentation adds no security or durable-mutation surface and claims no new
executable or live validation result.
