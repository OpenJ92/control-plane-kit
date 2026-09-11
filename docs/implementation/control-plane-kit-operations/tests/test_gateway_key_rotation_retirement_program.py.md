Source: [control-plane-kit-operations/tests/test_gateway_key_rotation_retirement_program.py](../../../../control-plane-kit-operations/tests/test_gateway_key_rotation_retirement_program.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These five tests exercise
[retirement preparation](../src/control_plane_kit_operations/gateway_key_rotation_retirement_program.py.md)
through real PostgreSQL and Operations services. The inherited
[retirement fixture](gateway_rotation_retirement_fixture.py.md) requires an
isolated operations test database, installs/verifies schema and truncates/seeds
workspace truth. It already contains an accepted simulated overlap child and a
draining rotation with deadline 1065. Setup activates B directly through the key
store and uses literal rotation transitions, not the activation program or
provider key/reference admission. No tests were run for these notes.

The happy path requires PREPARED and retirement-deploying, checkpoint/handoff
equality and a worker-a generation-1 fence. It checks unchanged authored graph
IDs, base overlap projection, canonical retirement desired projection and
revision 3. Decoding the stored desired graph must show only key-b at the target
and retain key-other on the unrelated gateway. It also checks one authored graph
row and zero observations. A fresh program call must return PREPARED_REPLAY with
the same checkpoint/handoff. These are prepared child/material facts, not
executed retirement or old-key removal; the test does not directly compare the
full authored descriptor, every current-pointer field or all history tables.

At epoch 1064, one second before the retained deadline, preparation must conflict
without changing child session/plan/request/run counts or desired pointer/revision.
The normal factory epoch 1065 exercises the equality boundary in the happy path.
The tests do not sleep or measure grants, and do not vary the clock between the
program, publication and final rotation deadline checks. The initial read commit
is not a child mutation, so it is compatible with those unchanged-state assertions.

Stale version, forged settled projection IDs and rotate-only actor scope are
rejected. The same test then corrupts review_digest in the original approval
request action and requires authorization denial. This exercises admission's
durable review-action check, not only non-null approval IDs. Those cases do not
assert rollback of every earlier child stage; forged lineage can follow a
committed session, and review-action failure can follow publication/planning.
Cases share one fixture state within that test rather than resetting between
every rejection.

The extra-key test registers another verify-only key using synthetic public PEM,
requires preparation conflict and asserts unchanged desired pointer/revision.
It checks the exact verification-key collection requirement before publication.
It does not assert that no session exists, prove provider refusal, or cover key
role permutations, replacement reference mismatch, issuer/audience drift or the
confirmed lexical key-order edge case. The fixture's key-a then key-b order
matches the builder's unsorted role tuple and masks that limitation.

The restart-named test resets truth for each commit number 1..8, wraps real UoWs
with CrashAfterCommitUnitOfWork and requires SimulatedProcessLoss after the
selected physical commit. The stages are read, session, publication, plan,
admission, claim, start and final checkpoint. Recovery uses fresh program objects
and ID prefixes, requiring prepared or prepared-replay and retirement-deploying.
Exactly two plans, requests and runs must exist: the seeded overlap child and
one retirement child. This checks cardinality after durable prefixes, not every
precrash identity, action/event or outcome. It neither kills a process nor
interrupts the database commit. Same-checkpoint identity is asserted separately
in the happy-path replay test.

No test here covers completed ALREADY_ADVANCED classification, rejected
retirement-ready/intermediate-cleanup/blocked states, expired or changed worker
claims, malformed clocks, concurrency, different actors/lease durations on
prepared replay or conditional equivalent-material/different-ID projection
reuse. That reuse caveat requires a preexisting equivalent phase row under a
different physical ID; routine public provenance is unestablished. It can yield
late admission rejection and publication replay conflict according to inspected
source, not an executed case in this file.

The fixture supplies scopes or defaults, so an empty scopes tuple would restore
default authority; this test correctly uses a nonempty rotate-only tuple to
exercise missing plan-execute. Worker scope remains fixed and is not separately
tested. Query helpers count selected rows and pointers, not SQL writes or a
complete database snapshot. Simulated overlap success and synthetic PEM framing
do not establish live signing, provider custody, deployment acceptance, key
retirement, secret revocation or cleanup.

Read depth: full 152-line test and 470-line owner with retained full retirement
fixture366/publication183/shared helper145 and rotation/overlap context; selected
actual admission/clock/fenced-fold contracts were rechecked. Separate retirement
execution tests remain a later group. This documentation adds no executed
validation result or security/mutation surface. No credentials, key/provider,
database or runtime actions were performed.
