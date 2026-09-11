Source: [control-plane-kit-operations/tests/test_gateway_key_rotation_deployment_lock_order.py](../../../../control-plane-kit-operations/tests/test_gateway_key_rotation_deployment_lock_order.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These three tests verify actual PostgreSQL row-lock acquisition/retention for
the rotation fenced writer's first blocked transition and its semantic replay.
They use separate blocker, worker and probe connections plus a single worker
thread. There is no fake lock model. They complement the SQL-text observation in
the [fencing suite](test_gateway_key_rotation_deployment_fencing.py.md), rather
than deriving concurrency behavior from a recorded method-call order.

setUp requires CPK_OPERATIONS_TEST_DATABASE_URL, installs/verifies schema and
truncates workspaces CASCADE before seeding the
[overlap fixture](gateway_rotation_overlap_fixture.py.md) and running actual
overlap preparation. Deterministic timestamps/IDs and epoch clocks accompany a
worker-a execution claim with a 1,800-second lease. Synthetic key material and
real stored approval/admission/run truth are used; no runtime adapter or provider
is invoked. tearDown closes the main connection but does not truncate; the next
setUp resets fixture truth. These tests require an isolated database, and none
were executed for this documentation.

Every case uses one prepared-overlap to BLOCKED command with rotate scope,
original checkpoint/fence, a fixed transition ID/time and overlap-effect-failed.
The blocker connection takes FOR UPDATE on one exact request, run or rotation
row and keeps its transaction open. A ThreadPoolExecutor runs
GatewayKeyRotationService.advance_deployment using a separate connection; its
backend PID is passed through a queue. The main connection polls
pg_blocking_pids until that worker is blocked by the intended blocker.

At this observed boundary, independent probe transactions try FOR UPDATE NOWAIT.
A lockable row must be immediately obtainable; a retained row must raise
psycopg.errors.LockNotAvailable. The fixed table/column mapping only allows the
fixture's request, run and rotation targets, with row values bound as parameters.
The three expected boundaries are:

| Blocker holds | Earlier locks retained by the writer | Later rows still lockable |
| --- | --- | --- |
| Request | None asserted | Run and rotation |
| Run | Request | Rotation |
| Rotation | Request and run | None asserted |

These observations distinguish acquisition order and retention while waiting.
The request case checks that the writer has not locked run/rotation first; the
run case checks that request remains held while rotation is free; the rotation
case checks that request/run remain held. An observed blocker PID establishes
that the worker reached the selected wait, rather than relying on a guessed sleep
or merely submitting a thread and immediately probing.

The blocker is rolled back and closed in finally after boundary assertions, then
the future must return BLOCKED. Each test repeats the same boundary for the exact
same command, requires replay result equality and counts exactly one transition
with that ID. This establishes that replay still acquires the tested locks before
returning stored semantics. It does not change the fence, actor, clock, failure
code or transition intent between first call and replay.

Actual [rotation-service source](../src/control_plane_kit_operations/gateway_key_rotations.py.md)
locks request, then run, reads the plan without FOR UPDATE, then locks rotation.
It validates linkage/current claim and checkpoint before its transition replay
lookup. Actual [execution selectors](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
use request/run FOR UPDATE, and the rotation store supplies the final row lock.
The [UoW](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
keeps those stores on one connection and physically commits only on successful
exit after commit is requested; errors roll back and close it. The tests observe
that transaction's row-lock boundaries, not Python lock behavior.

The queue wait, blocker polling deadline and future-result wait use five-second
bounds. Polling uses a monotonic deadline and repeated pg_blocking_pids queries,
not provider health checks. Releasing the blocker is explicit cleanup. There is
no process kill, database connection loss, injected commit failure or provider
retry here; those forms of interruption must not be credited to this suite.

Concurrency is deliberately bounded to one writer waiting on one held row in
each case. The suite does not create a cyclic wait between two real commands or
prove absence of all deadlocks across the package. It does not inspect advisory
locks, every store, plan mutation, accepted/prepared folds, retirement-phase
behavior or interactions with unrelated workspace/key/provider transactions.
Nor does it test stale/expired claims, lease takeover or outer authentication.
The related fencing tests cover selected stale-authority and rollback behavior
separately; shared-kernel/provider guarantees require their own evidence.

Read depth: full 262-line test and companion fencing657, retained full shared
execution651/rotation1381/fixture620 contexts. Actual fenced-writer ordering,
request/run/rotation FOR UPDATE selectors, replay placement and full 102-line
PostgresUnitOfWork were inspected. This source review establishes what the tests
assert, not a new passing execution result. No credentials, source mutation,
database/provider/runtime action or cleanup was performed for the documentation;
it adds no security or mutation surface.
