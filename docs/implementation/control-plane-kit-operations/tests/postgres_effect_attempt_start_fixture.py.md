Source: [control-plane-kit-operations/tests/postgres_effect_attempt_start_fixture.py](../../../../control-plane-kit-operations/tests/postgres_effect_attempt_start_fixture.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 416-line fixture builds PostgreSQL test worlds for ordinary and compensation
effect-attempt starts. It supplies coherent runtime intents, start services,
selected-table snapshots, direct attempt/event persistence, claim mutations,
linked-run retry preparation and foreign-request data. It contains no test methods.
Unlike the pure start fixture, several builders here read or mutate the database;
their availability is not evidence that a consumer exercised or verified them.

The inheritance order places
[EffectAttemptStartFixture](effect_attempt_start_fixture.py.md) before
[PostgresEffectAttemptStoreFixture](postgres_effect_attempt_store_fixture.py.md).
The latter brings record builders and the
[execution-lease recovery fixture](execution_lease_recovery_fixture.py.md).
The start fixture therefore supplies command, transition, authority, fence,
presence checks and the 512-character safe-error helper with truthy canaries.
This file overrides intent and intent_for_attempt. transition_record explicitly
calls the store fixture's record-transition builder to avoid resolving to the
start fixture's differently shaped transition method.

setUp explicitly runs the store fixture setup and then reset_start_truth. The
inherited base requires CPK_OPERATIONS_TEST_DATABASE_URL, opens an autocommit
connection, installs schema and truncates cpk_workspaces with CASCADE. Store setup
then resets/seeds active truth; reset_start_truth resets/seeds it again. Thus setup
includes repeated truncation and seeding, not one atomic initialization. These
operations target the configured test database when executed; none ran during
this documentation task.

The base unit_of_work factory creates a fresh psycopg connection for each actual
[PostgresUnitOfWork](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py).
Its commit method requests commit on successful context exit; exceptional or
uncommitted exit rolls back, and the connection is closed. Direct SQL through
self.connection is separately autocommitted. The fixture sets no explicit local
lock/statement timeout. tearDown delegates to the base, which truncates and then
closes an open connection. Those two cleanup calls are sequential, so a truncation
failure can prevent the close; this is not unconditional finally-based cleanup.

reset_start_truth stores the compensation flag and selects active-empty or
compensation-requested history under RENEW_ACTIVE_CLAIM. The inherited seed builds
workspace/session, authored and identity-realized graphs, a one-activity
StartRuntime plan, approval records, request-a and run-a across several committed
steps. The claim is worker-a/generation seven with a far-future expiry. This file
then directly sets run status to RUNNING or COMPENSATING and sets started_at.
For ordinary execution it separately commits RUN_STARTED at ordinal two.

The compensation history already contains six events: run opened/started, a
forward step started/succeeded, run failed and compensation started. It does not
create the forward effect-attempt row through the start interpreter. This prepares
an event history for a compensation start at ordinal seven; ordinary preparation
leaves the next ordinal at three. These are synthetic setup histories with fixed
timestamps, not records of runtime effects actually performed by this helper.

intent first delegates to the pure fixture's imported intent builder, then uses
dataclasses.replace to set workspace-a, the requested request/plan IDs,
graph-current/graph-desired and a runtime-target operation. Ordinary intent uses
StartRuntime(runtime-a); compensation uses StopRuntime(runtime-a). It defaults
process_delivery to false, unlike the underlying helper, while preserving the
underlying product/reference material and authority reference. Explicit options
still pass through actual constructors and their validation; the helper does not
promise every combination is admissible or resolve any secret reference.

The intent method's own compensation default remains false. Only start_command
automatically consults _start_compensation; persisted_started also has its own
explicit false default. A caller preparing a compensation world must therefore
pass compensation to those direct builders when appropriate rather than assume
the stored flag changes all fixture methods.

start_command removes compensation, transition and intent overrides. An explicit
transition supplies the identity; otherwise the helper uses default run-a,
start-runtime, attempt one. When intent is absent or None, it is built with the
chosen compensation, request override and identity's run/activity. If transition
is absent, it is built from that identity and intent. The helper then calls the
inherited command constructor with those values and remaining overrides. An
explicit intent is not rewritten, and custom plan coordinates are not inferred
from a supplied transition. The inherited command helper still evaluates its
default intent eagerly even when this method provides one.

intent_for_attempt is a database-reading override: it selects request_id and
plan_id from cpk_activity_runs for the supplied run, then builds the corresponding
intent. It directly unpacks fetchone(), without a fixture-specific missing-row
error. Consequently, inherited state/record builders that compute their request
fingerprint now depend on an existing run and its stored coordinates. This also
lets foreign-run records bind to request-b/plan-b rather than the default request.

start_service_with_sequence constructs the actual EffectAttemptStartService with
self.unit_of_work and a Sequence, returning both. start_service returns only the
service, and start_service_with_id_factory accepts a caller-supplied allocator.
Sequence pops supplied IDs in order and records successful pops in calls; exhaustion
raises IndexError before appending a call. The wrapper does not check expected
allocation counts or execute the service. Consumer tests own those assertions.

attempt_snapshot returns three components: the inherited operational snapshot,
ordered effect-attempt rows and ordered intent-evidence rows. The inherited part
selects request-a's status/claim fields, events for its runs, session-a actions
and selected run fields. The attempt part includes the 21 state, fence, lineage,
recovery and original/latest-event coordinate columns. The intent part includes
ten selected fields, including fingerprint and preimage, or an empty tuple when
to_regclass reports the intent relation absent.

The intent query is evaluated first, then the inherited snapshot and attempt query.
These are multiple reads on the autocommit connection without an encompassing
snapshot transaction. Intent/attempt queries cover their whole tables, whereas
the inherited history queries are scoped to request-a/session-a. This is a useful
selected-field equality observation in controlled tests, not a consistent
whole-database snapshot or complete history inventory. It neither bounds nor
redacts the selected rows/preimage and does not itself print them.

persisted_started directly constructs a STARTED record for run-a/start-runtime,
using ordinal three or seven and a fixed 2030 timestamp. It rebuilds the state's
request fingerprint from the runtime intent and regenerates the start-event
commitment, then uses the same event as original and latest. event_id is converted
to an event prefix by removing a trailing -start; the resulting event ID must
equal the requested value before persistence. Arbitrary names without that suffix
are not silently honored by this helper.

Inherited persist writes original/latest events, intent evidence when the store
bundle exposes that adapter, and the attempt in one unit of work. It checks intent
fingerprint/insert acknowledgement and requests commit, returning insert_absent's
result. persisted_started asserts that result equals its constructed record.
This bypasses the actual start service's eligibility and lease checks. It is not
an idempotent replay helper: duplicate event/intent insertion may fail before
attempt insertion, and the inherited method requests commit even if insert_absent
returns None rather than asserting success itself.

fold_persisted_attempt similarly constructs a replacement through transition_record,
using a story-specific fixed event ID and the next ordinal. It writes the new
event, asserts compare_and_set returns the replacement and requests commit in
one unit of work. The event acknowledgement is not checked here. The inherited
builder constructs story state directly, preserves current identity, request
fingerprint, fence, prior attempt and original event, and selects latest-event
phase from the original start. It does not call the Core fold or outcome service.
Repeated calls can collide on the fixed event IDs; no retry loop is provided.

expire_claim directly moves request-a's lease expiry into the past. replace_claim
directly replaces worker/generation and fixed claimed/expiry timestamps, defaulting
to worker-b/eight. Neither checks affected-row count, invokes a lease-recovery
command nor emits a corresponding operational action. These mutations prepare
test conditions; they are not examples of normal operator claim management.

add_lawful_linked_retry reads the next event ordinal outside a unit of work, then
commits STEP_FAILED and RUN_FAILED events and separately marks run-a failed.
It creates an auxiliary
[retry fixture](activity_run_retry_interpreter_fixture.py.md) sharing the URL and
connection without running its setup, obtains its default retry command, and
calls the actual
[retry interpreter](../../../../control-plane-kit-operations/src/control_plane_kit_operations/activity_run_retry_interpreter.py)
with four fixed IDs for run-b, decision event, opened event and action. The
inspected service performs its own admission/history checks and transactional
linked-run writes. This helper discards the result. Its preparatory commits are
not rolled back if that later retry fails, and the ordinal read is not itself a
concurrency protocol. It assumes a history eligible for the appended failure.

seed_foreign_run uses an auxiliary retry fixture with the same connection and
unit-of-work factory. Its actual helper copies plan-a to plan-b and request-a to
request-b with separate SQL statements, then commits a claimed run-foreign.
seed_foreign_attempt assumes that run already exists, directly marks it running,
commits opened/started events, builds its STARTED record and persists it separately.
The overridden intent_for_attempt binds the record to the copied plan/request.
These helpers share workspace/session and copied claim metadata; they introduce
different request/run coordinates, not a separate tenant or live runtime.

reject_database_observation is a context manager that patches the class method
PostgresExecutionStore.observe_request_lease_for_update to raise AssertionError
with the supplied message. mock.patch restores the binding when the context exits.
It forbids that particular method across instances during the patch; it does not
block every SQL read, database-time expression or provider observation. Like other
class-level patches, it does not isolate concurrent callers.

The selected
[first/replay consumers](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_start_first_replay.py)
assert ordinary/compensation start shape and fresh-connection readback, then use
persisted_started, claim expiry, synthetic evolution and linked retry for replay
checks. Their selected replay paths assert no ID consumption and equal snapshots
while the lease-observation method is forbidden. The restart-named case creates
a new service with fresh transaction connections; it does not restart a process
or database. Selected eligibility and concurrency consumers use phase mutations
and foreign records. None of those full suites is reviewed or executed here.

The six exported error-text constants support consumer expectations; they do not
catch or redact exceptions. The actual start service, rather than this fixture,
owns admission, durable replay checks, transactional start writes and return-value
semantics. Direct fixture setup and synthetic fold helpers must not be credited
as successful executions of those service paths.

Read depth: the complete 416-line fixture and every helper were read, together
with the full 126-line inherited store fixture and retained pure start/record
fixtures. Inherited setup, teardown, unit-of-work, snapshot, seed/history and ID
sequence implementations were checked, as were the actual PostgresUnitOfWork and
selected retry helper/service paths. Actual start execute/planning context and
selected consumer paths were inspected; full consumer suites and the full retry
interpreter were not reviewed. Validation was documentation-only: local links,
whitespace and frozen-source comparison. No application imports, tests, database/
provider calls, credential access, source/inventory edits or publication occurred.
