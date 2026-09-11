Source: [control-plane-kit-operations/tests/test_workflows.py](../../../../control-plane-kit-operations/tests/test_workflows.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 504-line unittest suite contains fifteen tests for grouped operator sessions
and ordered action history. Every test belongs to OperationWorkflowTests and runs
its PostgreSQL setup, including the two bodies that only check constructor guards.
The suite uses the actual command service, stores and transaction owner rather than
a fake history implementation. It does not deploy resources, generate activity
plans or change desired topology merely by recording those command kinds.

setUp requires CPK_OPERATIONS_TEST_DATABASE_URL; absence raises RuntimeError with
the package test-script instruction, not a skip. It opens an autocommit psycopg
connection, calls actual install_schema, truncates cpk_workspaces CASCADE and
commits a WorkspaceRecord for workspace-a through a separate PostgresUnitOfWork.
The fixture therefore mutates the supplied database and is not isolated per test
by a rollback-only outer transaction. tearDown closes the setup connection.
Each service unit of work opens a fresh non-autocommit connection to the same
configured database. The direct module guard invokes unittest.main.

The actual [schema installer](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/schema.py)
creates an empty owned namespace or verifies the current schema and rows under
its transaction/locks. This fixture does not implement a migration or schema-drift
oracle; installer dependencies have their own tests. The truncate happens after
installation/verification, not before it.

Sequence supplies IDs by popping a local list; exhaustion raises the ordinary
list error. Each constructed service owns its own sequence. service supplies a
fixed 2026-07-22T10:00:00Z clock unless given another callable. start constructs
the same workspace/actor/title command with a configurable start key. These are
deterministic fixtures, not UUID, clock-monotonicity or concurrent-ID-generation
tests. Supplying unused IDs on replay is not an explicit call-counter assertion.

BlockingClock announces entry through one threading.Event and waits at most five
seconds for its release event, otherwise raising TimeoutError. In the actual
[workflow owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/workflows.py),
fresh manual and terminal actions call the clock after their session row is
locked and their next ordinal selected. Blocking at that point holds the command
transaction open; the clock does not itself emulate a database lock.

Start's happy-path test requires a non-replayed OPEN session with the supplied ID,
a START_OPERATION_SESSION action at ordinal one, and a subsequent transaction
finding the start key and exactly the expected action ID. It establishes stored
session/action composition for that case. The separate late-failure test supplies
the rollback witness; the happy path alone does not prove atomic failure behavior.

The actual start path checks workspace existence, takes the workspace/key advisory
lock, then looks for a session with that key. It compares intent fingerprints and
requires matching initial action evidence on replay. A fresh session and action
are inserted in one caller transaction. execute requests its commit;
start_in_unit_of_work itself leaves the caller's transaction uncommitted. This
suite calls execute and does not independently test the caller-composed entry point.

The sequential start replay test compares both returned records with the first
result and requires replayed=True. Reusing the same key with a different title
must raise OperationIdempotencyConflict. It does not vary actor, metadata or every
fingerprint field, corrupt stored evidence or replay a start after session closure.
The missing-workspace test requires OperationWorkspaceNotFound but does not count
all remaining rows or assert exception-chain redaction.

The identical-start race uses a two-party Barrier with a five-second wait and
ThreadPoolExecutor.map over two services with different proposed IDs. Both results
must name the same session and action, and exactly one must be marked replayed.
This coordinates entry and tests returned convergence. It does not inspect lock
catalogues, require a particular winning ID or count the whole database afterward.
The map has no result timeout; the barrier timeout is not an end-to-end deadline.

Manual history testing records SET_DESIRED_GRAPH with graph_id=graph-a, replays that
command and records REQUEST_ACTIVITY_PLAN next. The first and replay actions must
be equal, replay flags must differ, and fresh ordinals must be two and three.
Changing graph_id to graph-b under the same key must conflict. There is no actual
graph-a fixture or planner invocation: these payloads name recorded intent, and
RecordOperationAction does not implement the named operation.

The owner fingerprints compact, sorted JSON containing each command's relevant
intent; the key scopes lookup separately. Start includes workspace/actor/title/
metadata, manual actions include session/actor/kind/payload, and close/cancel have
distinct tags plus session/actor. Generated IDs and timestamps are not fingerprint
inputs. These are source facts explaining the selected replay tests, not a claim
that this suite checks all serialization or fingerprint laws.

Fresh manual and terminal commands take the session/key action advisory lock,
inspect existing evidence, then lock the session row before checking OPEN and
allocating an ordinal. Existing matching evidence returns a replay with the current
session record before the fresh-action OPEN check. Close and cancel use an
OPEN-to-terminal conditional update and append the lifecycle action in the same
transaction. They do not cancel an external execution or delete any resources.

The [history store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/activity_history.py)
implements transaction-scoped advisory locks for operation-session:workspace:key
and operation-action:session:key, plus SELECT FOR UPDATE on the session row.
next_action_ordinal locks that row and computes MAX(ordinal)+1; safety depends on
retaining the transaction through insertion. The conditional terminal update only
matches status=open. The store uses the caller's connection and does not commit.
The [store bundle](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/stores.py)
binds that history adapter to the unit of work's connection.

The [transaction owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
records a commit request and performs the actual connection commit only when the
whole context exits successfully. Otherwise it rolls back, and it always closes
the connection on exit. Thus reads after unit_of_work.commit() inside a service
still occur before the context's final commit. No test here injects connection
commit failure, process death or an ambiguous network outcome.

The closed-session case checks CLOSED and ordinal two, then rejects one fresh
manual SET_DESIRED_GRAPH action. The cancelled case checks CANCELLED, replays the
same cancel with equal session/action records and rejects one new manual action.
Together these are selected terminal/replay laws, not an exhaustive matrix of
close replay, changed terminal intent, cross-terminal keys, missing sessions or
manual replay after closure. The owner supports replay before the fresh OPEN
check; not every consequence of that ordering is asserted here.

The manual-first overlap blocks the manual command at its clock, submits close
and requires close_future.result(timeout=0.1) to time out. Releasing the clock
must let both finish within their five-second result waits with ordinals two and
three respectively. The close-first overlap reverses the ordering: the manual
future initially times out, then raises OperationSessionStateConflict after close
finishes. A fresh query must show exactly start and close action kinds, excluding
partial manual history in this case.

Those overlaps combine a known first-command checkpoint with observed later
completion. The short timeout alone does not prove that the second worker reached
a specific SQL lock rather than awaiting scheduling. There is no second-worker
entry event, pg_locks inspection or independent NOWAIT probe. These tests support
the intended lock ordering together with the actual owner/store reads; they are
not a measurement of lock wait duration or a proof of all schedules. Fixed clocks
also do not establish timestamp ordering across the concurrent commands.

The close-versus-cancel test runs eight fresh-session races. A two-party barrier
coordinates each pair; both futures have five-second result waits. The worker
returns OperationSessionStateConflict as an outcome and lets other failures escape.
Exactly one returned outcome must be that conflict. Stored history must have
ordinals (1, 2), with the second kind either close or cancel. The test allows either
winner, does not require both winners across the eight attempts, and does not
separately compare the final stored session status with the winning action. Its
without_deadlock name describes the exercised races, not a universal guarantee.

The independent-session test creates two sessions in the same workspace, blocks
a manual action in session-a at its clock, and requires session-b's manual action
to complete within two seconds with ordinal two before releasing session-a. The
released action must also return ordinal two within five seconds. This is a
positive progress witness for distinct sessions/keys, not exhaustive independence
across every scope or advisory-lock input. All executor context managers wait for
workers on exit; individual future timeouts do not bound total shutdown time if
a worker remains stuck. BlockingClock's five-second release timeout only bounds
its own wait.

The reserved-action test tries only CLOSE_OPERATION_SESSION through
RecordOperationAction and expects InvalidOperationCommand matching reserved. The
owner reserves start, close, cancel and record-action; this test is not exhaustive
over that set. It prevents this manual constructor route from forging the selected
lifecycle action, not direct store access or arbitrary hostile Python mutation.

The late-action rollback test first commits an existing session/action, then creates
workspace-b. It starts a new session there using the existing action ID and expects
psycopg.errors.UniqueViolation. A subsequent get_session for the proposed new
session must raise KeyError. The actual [schema](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
has a global action primary key, unique session/ordinal, scoped non-null idempotency
indexes and session/workspace foreign keys. The collision occurs after add_session
but during add_action in the same transaction. The assertion proves that new
session is absent; it does not compare every table, test terminal-action rollback
or prescribe recovery from a commit whose outcome is unknown.

The deterministic-query test creates session-b at 10:00 and session-a at 09:00,
then requires sessions_for_workspace to return (session-a, session-b). Despite its
session_and_action_queries name, this test does not query action ordering. The
store orders sessions by created_at then session_id and actions by ordinal. These
fixtures would also pass an ID-only session ordering because time and ID order
agree; they do not distinguish that regression, test equal-time tie breaking or
exercise pagination. Other tests assert selected action ordinals/history tuples.

The final test passes raw string status open and raw action kind set-desired-graph
to the [record constructors](../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py).
It matches diagnostic substrings using assertRaisesRegex(Exception, ...), not an
exact exception class. The actual records require OperationSessionStatus and a
closed OperatorCommandKind or LifecycleOperationKind, respectively. The broader
record invariants, including positive exact-int ordinals and terminal timestamps,
are not all covered here. Selected kinds come from the actual
[Core command vocabulary](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/commands.py),
not strings invented by the fixture.

Security and evidence boundary: actor IDs here are data, not authenticated principals
or tested authorization decisions. The suite has no credential/provider access
beyond its configured test database, no descriptor canaries, and no secret-redaction
assertions. The owner redacts command descriptor mapping values and rejects certain
secret-shaped keys in metadata/payload, but those guards are outside these selected
tests; raw allowed payloads are stored as JSON. This note introduces no new runtime
surface. History/replay assertions do not certify provider outcomes, plan approval,
graph advancement, execution-run cancellation or safe external retry.

Read depth: complete 504-line suite, all fifteen tests and every helper/setup path;
complete 512-line workflows owner and complete PostgresUnitOfWork and schema
installer entry module. The history store was previously read in full for its
owner companion; session/action locks, inserts, lookups, conditional transition,
ordinal selection, queries and decoders were checked again here. Selected actual
record constructors/status, Core command enum, store-bundle binding and schema
constraints/indexes were inspected. No full records, Core commands, schema SQL,
schema-verification or data-validation review is claimed. Documentation validation
only: local links, whitespace and frozen source/test comparison. No application
imports, executable tests, database/provider calls, credential access, source or
inventory edits, publication or merge were performed for this note.
