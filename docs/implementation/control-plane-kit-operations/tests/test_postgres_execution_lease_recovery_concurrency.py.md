Source: [control-plane-kit-operations/tests/test_postgres_execution_lease_recovery_concurrency.py](../../../../control-plane-kit-operations/tests/test_postgres_execution_lease_recovery_concurrency.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These two tests exercise selected PostgreSQL command races and recovery clock
placement through the actual
[recovery interpreter](../src/control_plane_kit_operations/execution_lease_recovery_interpreter.py.md)
and lifecycle StartActivityRun. They use real separate blocker, worker and probe
connections, not a fake lock store. The
[base fixture](execution_lease_recovery_fixture.py.md) supplies constructed approval,
request/run/journal state and synthetic active/expired lease times. No provider,
real caller authentication or deployed runtime is involved.

_contender creates a new PostgresUnitOfWork connection factory that reports its
backend PID through a queue and gives the service a deterministic Sequence. Recovery
contenders cover active/expired renewal, takeover to worker-b or worker-c, and
abandonment. Start uses the actual
[lifecycle service](../../../../control-plane-kit-operations/src/control_plane_kit_operations/lifecycle.py)
with a fixed caller clock and execution-operate authority. Recovery instead uses
its database observation. All presented commands initially target worker-a
generation seven and use distinct label-derived idempotency keys. This file does
not test two callers replaying the same key.

The race helper first locks request-a with a dedicated blocker connection. It
starts the intended winner and polls pg_blocking_pids until that backend is blocked
by the blocker. Only then does it start the intended loser and require the loser
to be blocked by the winner. Releasing the blocker lets the winner complete; the
loser must raise RunLifecycleError. This stages a causal winner order rather than
relying on whichever thread happens to run fastest.

The actual source explains the coordination: fresh recovery and lifecycle start
both take session/key advisory coordination, an OPEN-session row lock, then request
and run locks. Different keys do not share the same key lock, while the session
row can serialize their fresh paths. pg_blocking_pids verifies backend relationships,
not the exact relation/tuple responsible for every wait. The interpretation of the
second wait as session coordination is supported by the inspected source, not a
test query identifying that lock object.

The nine prescribed schedules cover both orders of active renewal versus start,
one active-renewal pair, both orders of expired renewal versus takeover, each
takeover worker winning, and both orders of abandonment versus takeover. The two
abandonment/takeover cases select gateway-rotation approval; the others use
activity-plan approval. The fixture routes its non-activity-plan selector into
the gateway branch. These are selected schedules over one request/run and fixed
inputs, not an exhaustive concurrency state space or every pairing implied by
the test title.

Winner assertions require one added action, unchanged run count and no ID calls
by the loser. Start uses two IDs, adds one RUN_STARTED event, keeps worker-a
generation seven and changes the run to RUNNING. Recovery uses three IDs and adds
decision/consequence events: renewal/takeover leaves a CLAIMED request at generation
eight on the selected worker; abandonment leaves ABANDONED with worker/generation
cleared. Active renewal retains a CLAIMED run; the other recovery winners retain
FAILED. The final action ID must match the winner's returned action.

The loser assertion accepts the RunLifecycleError base class and applies bounded,
chain-free/canary checks. It does not require one exact failure subtype or message
for every schedule. The resulting state supports rejection of the losing stale
intent with no duplicate history/ID allocation, not a separate read-back of every
field of each result or a universal automatic retry policy.

_complete_snapshot extends the base selected request/event/action/run snapshot
with workspace current/desired graph and realized-projection pointers/revision,
plus rotation IDs and SQL MD5 digests of row_to_json for that workspace. Those
additional projections must remain unchanged, and no CURRENT_GRAPH_ADVANCED event
may occur in the selected event list. The helper's complete name does not imply
every table/column is observed or that separate autocommit SELECTs are a globally
consistent database snapshot. Its graph/rotation checks protect the represented
unrelated state; they do not prove that no external effect was attempted.

The second test isolates active-renewal clock placement at two barriers. With
request-a held, the worker must be observed blocked while run-a is still available
to a NOWAIT probe. With run-a held, request-a must already reject a NOWAIT probe.
These two observations support request-before-run locking for this path. After
the worker reaches the barrier, the blocker samples clock_timestamp into released_at
and then commits. Despite the variable name, this is a marker taken immediately
before release, not a measurement after commit completes.

The returned claim time must be at least that marker and equal decision,
consequence and action creation times. A separate SQL query joins the four
persisted timestamps by the returned event/action IDs and requires all four to
agree and be at least the marker. Three IDs must be allocated and request/run must
remain CLAIMED. The actual
[execution store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
observes clock_timestamp after request locking, and the interpreter invokes it
after run/eligibility checks. Together source and assertions support fresh
post-lock recovery timing for these cases.

The clock test does not count every observation call, measure exact lock-release
latency, force the expiry-equality boundary or check every lease duration. It does
not apply its database-clock law to lifecycle start, whose supplied clock is
sampled by a different owner. Both tests depend on the real PostgreSQL harness and
its connection/transaction behavior rather than simulated scheduler time.

PID queue and blocker-poll waits use five-second limits; future result waits use
ten seconds. The polling helper repeatedly queries PostgreSQL until the expected
blocker appears or its monotonic deadline is reached. These are apparatus bounds
for expected progress, not a hard deadline for the entire test: blocking database
calls are not interrupted by that loop, this file sets no SQL statement timeout,
and cancelling an already running future does not stop its thread. Executor context
exit can still wait for running work.

Blocker transactions are rolled back/closed in finally blocks, probes use their
connection contexts or explicit cleanup, and command connections follow the actual
[UoW](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py).
The helper attempts to cancel an unfinished loser after releasing the blocker.
This cleanup structure is not evidence for process-kill recovery, universal deadlock
freedom or handling every database outage. Fixture schema installation/truncation
still requires an appropriate isolated test database.

The tests protect participating-command serialization, selected loser rejection,
unrelated stored-state preservation and post-lock observation placement. They do
not establish same-key replay, unbounded fairness, arbitrary cross-service locking,
ambiguous commit reconciliation, provider retry safety or live-resource cleanup.
No new schedule matrix or executable validation was added for this companion.

Read depth: full 435-line source, both tests and every helper; full base fixture
572, recovery interpreter 599 and actual UoW context retained. Selected lifecycle
Start dispatch and full _transition path, history advisory/session locks and
execution observation/selectors were inspected. Other consuming suites remain
separate evidence. No source/pin changes, executable tests/imports, database setup,
credentials/private-key access, provider/runtime actions, staging or publication
occurred. This note adds no security surface or live mutation authority.
