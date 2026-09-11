Source: [control-plane-kit-operations/tests/test_gateway_key_rotation_deployment_fencing.py](../../../../control-plane-kit-operations/tests/test_gateway_key_rotation_deployment_fencing.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These nine tests protect the rotation service's nominal deployment handoff,
fenced writer, replay and selected rejection/rollback behavior. They use real
PostgreSQL, not a fake store. setUp requires CPK_OPERATIONS_TEST_DATABASE_URL,
installs/verifies schema, truncates workspaces CASCADE, seeds the
[overlap fixture](gateway_rotation_overlap_fixture.py.md), and runs actual overlap
preparation. tearDown truncates again and closes the connection. These are
database-mutating tests for an isolated target; none were executed for this note.

Preparation creates an ordinary plan, admission, claimed/started run and stored
overlap checkpoint through the reviewed
[preparation program](../src/control_plane_kit_operations/gateway_key_rotation_overlap_program.py.md).
The fixture uses synthetic public key material and actual approval records,
worker-a generation 1, a 1,800-second lease, deterministic textual timestamps/IDs
and injected epoch clocks. It does not deploy a provider/gateway. Commands in
this suite chiefly call the rotation service directly to block the prepared
overlap child; they do not invoke the shared execution coordinator.

The public-language test checks that three root exports are the exact nominal
types from gateway_key_rotations: handoff, read-handoff and fenced advance. The
prepared handoff must contain the rotation/checkpoint and generation-1 fence,
while checkpoint has neither fence nor claim_generation attributes. Handoff repr
must omit worker-a and generation. A HostileFence subclass is rejected with
cause/context-free conflict because handoff requires the exact fence type.
Despite the test's bounded name, it does not exhaust identifier/length limits,
all hostile subclasses, serialization or arbitrary secret canaries.

Handoff reading returns the current stored claim for the authenticated worker
identity supplied in ExecutionWorkerAuthority. A foreign-worker canary gets the
fixed worker-is-not-current denial without echoing that value or retaining
cause/context. Direct SQL changes claim_generation to 2; a fresh handoff then
has the new fence and unchanged checkpoint. This models changed durable claim
truth, not an actual lease renewal, expiry, takeover or outer authentication flow.
The test does not vary worker scope or exercise a missing claim.

The generic-writer test supplies all six deployment-owned transition shapes:
prepare, accept and block for overlap and retirement. Each must fail with the
exact requires-fenced-writer conflict and no cause/context, leaving rotation and
transitions equal. These are shape-guard tests, not six fully valid phase-state
fixtures: retirement checkpoints are derived by replacing overlap phase fields
and the database remains prepared overlap. The
[service](../src/control_plane_kit_operations/gateway_key_rotations.py.md)
rejects these shapes before its generic writer loads/mutates the rotation.

Two tests simulate the first prepared write by directly deleting the prior
overlap-deploying transition/checkpoint and decrementing rotation version back to
KEY_GENERATED. The admitted child remains. One inserts a foreign activity-plan
approval request/decision copied from fixture rows and changes both execution
request and supplied checkpoint to those foreign IDs. Their internal agreement
must still fail against the rotation's original approval IDs. The other changes
only checkpoint desired revision and must fail immutable plan linkage. Both
require exact cause/context-free conflicts and unchanged rotation/transition
count. This SQL rewinding and foreign evidence are test corruption, not a public
rollback or approval-management procedure.

The acceptance test fabricates accepted graph/projection/time fields from the
prepared checkpoint without executing or advancing current graph. Direct fenced
acceptance must reject missing advancement evidence with the exact incongruent-
evidence conflict, no cause/context, unchanged rotation/transitions and unchanged
global activity-event/action counts. This protects the final writer's evidence
gate; it does not cover all possible evidence mutations, accepted replay, or a
full database snapshot. Broader phase execution assertions remain in their own
reviewed [overlap execution suite](test_gateway_key_rotation_overlap_execution.py.md).

RecordingConnection delegates SQL and transaction methods to a real psycopg
connection. It normalizes query text and records SELECT ... FOR UPDATE references
to three named tables. A successful blocked transition must record request,
run, rotation in that order. This is a syntactic observation of issued SQL,
not a fake lock implementation or proof of which locks were retained while a
worker waited. It does not inspect every advisory/table lock. The separate
[lock-order suite](test_gateway_key_rotation_deployment_lock_order.py.md)
tests actual contention and retention.

That same test changes generation to 2 after blocking. Reusing the old command
must deny authority; substituting a freshly read handoff must replay the same
blocked result and retain exactly one transition of that identity. Changing
failure code with the same transition ID must conflict. Actual writer order
checks current request/run/plan/rotation linkage and fence before looking up the
transition fingerprint. Fingerprints bind transition semantics rather than the
handoff fence, permitting fresh-authority replay of unchanged semantics. This
is not permission to replay changed intent or reuse stale authority.

A dedicated stale-fence test increments generation before the first block and
requires the exact stale-authority denial, without worker/run IDs or cause/context.
The rotation remains OVERLAP_DEPLOYING and transition count stays unchanged.
These assertions concern this direct writer boundary; they do not establish
all shared-kernel exception translations or that no provider effect could have
preceded a later stale fold in another workflow.

The late-failure test makes RecordingConnection raise RuntimeError immediately
before the transition INSERT delegates to PostgreSQL. Earlier rotation/store
writes in that UoW have already occurred; the real
[PostgresUnitOfWork](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
rolls back on exceptional exit and closes the connection. The returned rotation
record, including retained checkpoint, and transition count must equal the prior
state. This exercises transaction rollback for a blocked fold, not process loss
after commit, a failed physical commit, or rollback of a newly accepted checkpoint
and its separately committed advancement. It does not compare every table.

Actual [execution selectors](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
issue request/run FOR UPDATE; the rotation selector issues its lock afterward,
with a non-locking plan read between run and rotation. The UoW defers physical
commit until successful exit. Fence equality is worker/generation equality; this
writer/read-handoff path does not independently observe lease expiry. Lock order
and current-fence validation should not be described as a universal fresh-lease,
deadlock-freedom or exactly-once provider guarantee.

Read depth: full 657-line test, retained full rotation1381, shared kernel651,
overlap fixture620 and reviewed preparation/execution context. Actual handoff and
fenced writer, linkage/approval/acceptance/fingerprint paths, nominal fence types,
PostgreSQL request/run/rotation locks and full 102-line UoW were inspected.
No live provider, concurrent worker, real process interruption, credential or
database action was executed for this documentation. It introduces no security
or durable-mutation surface; authentication, redaction and rollback claims remain
limited to the source and assertions described above.
