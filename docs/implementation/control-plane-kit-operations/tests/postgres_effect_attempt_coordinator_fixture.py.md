Source: [control-plane-kit-operations/tests/postgres_effect_attempt_coordinator_fixture.py](../../../../control-plane-kit-operations/tests/postgres_effect_attempt_coordinator_fixture.py).
Maintain this document alongside its source file. Recheck inherited seed/cleanup,
real service composition, scripted adapter behavior and snapshot coverage when
these helpers or consuming coordinator tests change.

This 334-line fixture composes the actual PostgreSQL-backed coordinator and
start/fold/reconciliation/lifecycle services while keeping runtime execution and
observation scripted. It supplies recording wrappers, ID factories, a rendezvous
helper, seed adjustments and selected SQL snapshots. It defines no test methods
and does not establish a real Docker/provider result by constructing a harness.

## Inherited database setup and graph preparation

The inheritance chain runs through the
[reconciliation fixture](../../../../control-plane-kit-operations/tests/postgres_effect_attempt_reconciliation_fixture.py),
guarded-observed-fold, fold, start and store fixtures. The inherited start setUp
first invokes the store fixture setup, then dynamically calls reset_start_truth,
which reaches this override. Base setup requires CPK_OPERATIONS_TEST_DATABASE_URL,
opens an autocommit psycopg connection, installs/verifies current schema and
truncates cpk_workspaces CASCADE. Seed resets truncate again. Execution belongs
to the owning isolated Docker/PostgreSQL suite; authoring this note ran none of it.

The inherited reset_start_truth seeds a claimed request and sets run-a to RUNNING
or COMPENSATING. The forward case adds RUN_STARTED in a committed unit of work.
This override then rebuilds graph-current and graph-desired with a Docker-kind
runtime-a and no workload nodes. Each authored row is read through a unit of work;
the new graph descriptor is encoded with Core's graph codec, and
RealizedGraphProjectionRecord.identity_for_authored computes its canonical
projection material and digest.

Two direct autocommit updates per graph replace the authored descriptor and the
descriptor/digest of projections with that source-authored ID. These do not use
normal graph publication, create a new revision, change stored projection IDs,
assert affected row counts or form one atomic multi-graph transaction. The newly
computed projection_id is not written. This is controlled fixture preparation;
it must not be promoted into an application migration or graph-repair method.

The inherited unit_of_work factory opens a fresh ordinary connection per scope.
Actual PostgresUnitOfWork commits on successful exit after a commit request,
otherwise rolls back and closes in finally. The fixture's direct connection has
a different autocommit lifetime. Inherited teardown truncates before closing;
close is not protected by a nested finally if truncation fails. Setup failure
also has no local cleanup wrapper in this fixture.

## Scripted adapters and recording wrappers

GeneratedIds returns prefix-1, prefix-2 and so on, recording each generated value.
Each harness creates four fresh factories with fixed lifecycle/start/fold/direct
prefixes. They measure selected allocations; they are not globally unique across
harnesses, thread-safe allocators or database idempotency keys.

RecordingRuntimeAdapter records legacy and runtime calls separately. Legacy
execute simply pops and returns a queued value, with no exception/callable
interpretation or empty fallback. Runtime execute pops a queued value or defaults
to RuntimeEffectResult.succeeded(request.effect_id) when empty. Only exact
TypeError and RuntimeError instances are raised. Callables receive context and
request; other values, including other exception classes or subclasses, are
returned unchanged. A queued AssertionError therefore does not itself fail on
invocation; consuming tests must inspect calls or the coordinator's wrong-result
handling. This differs from the database-free recording adapter's BaseException
rule. Default success is invented test input, not an observation of a provider.

RecordingService appends a command, invokes optional before_execute with no
arguments, then delegates to the real inner execute or execute_observed method.
The same list records both entry points. A hook error prevents delegation but
leaves the recorded command; call counts alone are not successful commits.
The hook runs before the inner service invocation, not intrinsically under its
transaction. The wrapper adds no retries, validation or independent transaction.

TimeoutRendezvous increments a counter under a lock and waits on a threading
Barrier with a five-second default timeout. A broken/timed-out barrier propagates
its error. The barrier can serve successive cycles; the helper does not impose a
global maximum invocation count or synchronize the other recording lists and ID
factories. It is scheduling apparatus, not proof of database lock behavior.
CoordinatorHarness is a mutable dataclass grouping services, adapter and ID
recorders; it owns no connection cleanup or context-manager lifecycle.

## Actual service composition

coordinator_harness creates the ordinary
[ExecutionCoordinator](../../../../control-plane-kit-operations/src/control_plane_kit_operations/coordinator.py),
not the database-free subclass. Its public command-receipt admission/completion
and fresh _load_context path remain active. Context loading reads durable request,
run, plan, graph projections, active registered material and events through stores,
then projects the journal and derives a schedule after leaving that read scope.

The harness wraps real lifecycle, start and fold services. Lifecycle uses a fixed
2030 timestamp and its own generated IDs; the coordinator has a separate fixed
timestamp and ID factory. Start and fold receive ID factories but no fixture
clock. Selected actual start code obtains a locked lease observation and event
ordinal, then writes event, intent evidence and attempt within one unit of work.
Selected fold code validates current truth, writes event/outcome/observation
material as applicable, compare-and-sets the attempt and requests commit.

The real
[reconciliation service](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_reconciliation_interpreter.py)
receives the same recording fold wrapper. It calls execute_observed for guarded
observation folding, so those invocations also appear in harness.fold.commands.
Its initial locked read scope ends before fresh observation; folding has a later
transaction. This follows inspected service code. The harness does not install
the parent fixture's UnitOfWorkLedger by default, so it does not independently
assert transaction absence during every adapter/observer call.

Unless supplied, the observer is FailIfObserver, which records and raises on an
unexpected observe. Adapter/observer defaults use truthiness. Synthetic clocks
and authority objects do not bypass the actual services' command/claim checks or
prove a real present-time lease. coordinator_command defaults to run-a, worker-a,
generation seven, execution:operate plus secret-provider:use scopes, one effect
and coordinator-a as idempotency key. These are test inputs; callers can replace
them to exercise rejection or replay. Scope labels do not confer external access.

## Reconciliation and recovery seeds

seed_running_reconciliation selects an observed story and delegates with zero_use
enabled. That inherited path resets start truth, persists a STARTED attempt and
matching intent with no authority reference/products, recomputes its fingerprint
and event commitment, and returns no registered runtime authority. observer_for
then supplies a scripted result adjusted to the attempt event and intent digest.
The helper returns both persisted evidence and this observer; it does not contact
a runtime or resolve secret material. No observer transaction ledger is supplied.

persist_recovery_resolution maps SUCCEEDED to recovered-succeeded and every other
input to recovered-failed. It is not an exhaustive EffectRecoveryResolution
dispatcher: ABANDONED would also select failure. The inherited seed first creates
started truth and then persists an uncertain state/event via Core folding and
store compare-and-set. The real fold service executes the recovery command and
returns its attempt. This is durable fixture state, but its decision/evidence are
synthetic, not an independently authorized operator recovery or provider inquiry.
The helper resets the fixture world rather than extending arbitrary live history.

## Snapshot scope

coordinator_snapshot reads selected request status/claim fields, run status and
settled time, event run/ordinal/type/activity, an inherited attempt-column subset,
and direct outcome event coordinates. Its attempt subset includes status,
outcome fingerprint, recovery decision/resolution and latest event coordinates;
it omits request fingerprint, fence, predecessor and original event columns.
Event payload evidence/failures, command receipts, intent preimages, observations,
authorizations and lifecycle actions are not fully captured by this snapshot.

graph_request_snapshot contains workspace graph pointers/revision, request
workspace/plan/claim coordinates and plan status/base/desired graph IDs. It does
not include authored/realized graph descriptors or projection digests, request
status or the full approval/history state. Equal snapshots therefore do not prove
that all graph data or all durable state stayed unchanged.

The queries order their results but read entire relevant tables without a
workspace/run filter or pagination. On the autocommit fixture connection they are
separate statement snapshots, not one consistent cross-table read transaction.
run_status fetches the hard-coded run-a row and constructs ActivityRunStatus;
missing rows or invalid values are not translated into a fixture-specific error.
These helpers are test observations, not a bounded public projection API.

## Consumer evidence and maintenance boundary

The full 129-line
[budget/lifecycle suite](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_coordinator_budget_lifecycle.py)
uses the harness to count start, adapter, fold, reconciliation and lifecycle
calls. Fresh start/fold/settlement consumes one iteration, existing reconciliation
can consume one with zero runtime calls, and prepared terminal recovery settles
through lifecycle with zero effect budget. Counts describe coordinator selections,
not a tally of external infrastructure mutations.

Selected
[concurrency assertions](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_coordinator_concurrency.py)
hold one scripted adapter call while a second invocation with the same command
key sees an incomplete receipt; after release, replay equals the completed result.
That case asserts one combined start and one adapter call. It does not prove
global once-only dispatch across all command identities or every race. A separate
selected hook pauses lifecycle delegation after outer command admission; its
coordination uses Events rather than this file's TimeoutRendezvous.

Selected
[crash/replay assertions](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_coordinator_crash_rollback.py)
raise after the real start service returns, then use a new harness to observe
uncertain incomplete-command replay without redispatch/reconciliation. This is a
simulated exception at a chosen boundary, not an OS process kill or live-provider
crash. The inspected first-replay tests also call the accepted services directly
and assert reconciliation routing with no runtime adapter calls. These consumers
provide evidence beyond helper construction, but retain scripted provider truth.

Read depth: full 334-line fixture, full parent reconciliation fixture385 and
budget suite129; selected actual start/fold seed, uncertain-state persistence and
snapshot helpers; real start/fold/reconciliation/lifecycle transaction paths,
coordinator store loading and identity-projection factory; selected first-replay,
concurrency and crash assertions. Prior coordinator/public-receipt/inner-loop,
Core value, store fixture and unit-of-work reads were retained. This is not a full
audit of all inherited seeds, service guards or coordinator test suites. No tests,
application imports, database connections, credentials, provider effects or source
changes occurred in authoring. This note adds no security surface and grants no
authority for the destructive fixture setup or any held live attempt.
