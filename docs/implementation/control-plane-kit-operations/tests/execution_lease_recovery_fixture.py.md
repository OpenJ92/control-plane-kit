Source: [control-plane-kit-operations/tests/execution_lease_recovery_fixture.py](../../../../control-plane-kit-operations/tests/execution_lease_recovery_fixture.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This module supplies a PostgreSQL recovery-test mixin, deterministic ID sequence,
bounded-error assertion helper and a guarded binding to the actual
[recovery interpreter](../src/control_plane_kit_operations/execution_lease_recovery_interpreter.py.md).
It defines no test methods of its own. Consumers combine the mixin with unittest
cases or extend it for retry tests. The helpers construct and persist test truth;
they do not prove that an operator authenticated, a planner produced the plan or
a runtime performed the represented actions.

The import guard catches only ModuleNotFoundError naming the exact interpreter
module. Missing nested dependencies propagate. A missing module or absent service
attribute leaves ExecutionLeaseRecoveryCommandService as None; require_service
turns that into an explicit assertion in a consuming test. This is a missing-
implementation guard, not a fallback mock interpreter.

setUp requires CPK_OPERATIONS_TEST_DATABASE_URL, otherwise raising a message that
directs execution through Docker. It opens a psycopg autocommit connection, calls
the actual [schema installer](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/schema.py),
then runs TRUNCATE TABLE cpk_workspaces CASCADE. The installer creates an object-free
namespace or verifies current schema/data; the subsequent truncate is a separate
fixture action. The fixture does not allocate a unique database/schema, verify
ownership of the supplied URL or isolate concurrent test users of that database.
Its harness must supply the intended test database.

tearDown truncates again and closes the connection when it is still open. Close
follows truncate rather than living in a finally block, so this helper alone does
not guarantee cleanup if that truncate fails. An early setup failure similarly
is not a successful test or proof of teardown. No setup, truncation, connection
or environment-value read was executed during this companion's authoring.

unit_of_work creates an actual
[PostgresUnitOfWork](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
with a new connection to the same database. Stores share that connection, and its
commit request is completed at context exit. This separates command transactions
from the fixture's autocommit setup/observation connection. service_with_sequence
returns the real recovery service and its Sequence; service returns only the
service. The fixture offers no automatic command retry, custom clock or provider.

Sequence removes the first supplied ID and records it in calls. Exhaustion raises
IndexError before appending another call. It does not generate fresh UUIDs or
guarantee uniqueness across tests. safe_error requires no cause/context, combined
str/repr length at most 512 and absence of every supplied canary. It is an assertion
over an already-caught exception, not a serializer or general exception sanitizer.

authority creates RecoveryAuthority from a required scope plus optional extra
scopes, using operator-a and authority-reference-a by default. The actual
[pure language](../src/control_plane_kit_operations/execution_lease_recovery.py.md)
normalizes scopes and hides the reference in repr. command selects the appropriate
renew/takeover/abandon scope and command class, wrapping retained run, fence,
duration and key in their real types. Request-a, run-a and worker-a generation
seven are defaults; takeover targets worker-b, duration defaults to 600 seconds.
These are constructed capabilities and identities, not resolved credentials.
The decision-to-scope dictionary covers the four lease-recovery decisions only.

snapshot returns four separately queried projections: request-a status/claim;
events belonging to its runs ordered by run/ordinal; session-a actions ordered by
ordinal; and request-a runs ordered by attempt. It includes event payloads and
action identity/actor/payload/key/fingerprint, but omits some columns such as run
metadata/creation time and action creation time. It excludes other requests and
many tables. Because these SELECTs use the autocommit connection, they do not form
one transactionally consistent multi-query snapshot under arbitrary concurrent
writes. Consuming tests normally compare them around a completed command; equality
must not be promoted to a byte-for-byte database or global concurrency guarantee.

reset_truth first truncates workspace-owned truth, then calls seed_truth.
seed_truth itself does not reset existing IDs or act as an idempotent upsert.
It directly inserts workspace-a and creates a one-activity plan containing
StartRuntime(RuntimeTarget(runtime-a)) under activity ID start-runtime. Active
renewal selects active-empty history by default; every other decision selects
failed. An explicit truthy history name overrides that choice, even when intended
to produce ineligible history for the selected request/run state.

The full [graph-lineage helper](graph_lineage_fixture.py.md) used here saves two
empty DeploymentGraph values as graph-current and graph-desired at authored
versions one and two, then obtains and saves identity realized projections. The
actual [graph store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/graph_store.py)
checks projection workspace/source ownership; identity material is decoded and
re-encoded from the authored graph, with its projection ID derived from a digest.
The fixture uses the returned projection IDs in plan-a with desired_graph_revision
one. That revision is a fixture value, not the authored graph's version counter.
Graph names alone do not set the workspace's current/desired pointer fields.

The plan is hand-built separately from those empty graphs. Actual Core plan/value
constructors validate their own typed composition, while the actual
[history store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/activity_history.py)
requires complete plan graph lineage and checks projection workspace/source links
against its session. This supports current storage contracts without demonstrating
that a topology planner derived runtime-a or that any runtime node was deployed.
The base fixture calls no lifecycle service to produce its seed run or journal.

The first UoW stores the graphs/projections, an OPEN session and PLANNED plan, then
commits. For activity-plan approval it constructs ActivityPlanApprovalSubject(plan-a),
PLAN_APPROVE, low risk and non-destructive intent. Any approval_subject string other
than activity-plan enters the gateway-key-rotation branch; this parameter is a
fixture selector rather than a strict public enum parser.

The gateway branch directly inserts rotation-a with gateway-probe purpose, fixed
old/new reference and correlation identifiers, lifetime 60, skew five, requested
status/version one and fabricated lowercase-hex intent fingerprint. It constructs
the matching GatewayKeyRotationApprovalSubject with high risk, destructive=True
and DELEGATION_KEY_ROTATE_APPROVE. These values exercise the retained subject shape;
they do not register keys, resolve secret material, execute a rotation or obtain
operator approval from an external system.

In a second UoW, the fixture stores the typed approval request and an APPROVED
decision attributed to manager-a. The actual subject supplies its descriptor and
review digest to the history store; the fixture does not bypass that encoding by
inventing a review digest column. It then adds a CLAIMED execution request linked
to the approval, using a synthetic execution fingerprint and worker-a generation
seven. Active seeds use claim times in 2098/2099, other seeds 1999/2000, creating
deliberately active/expired test worlds around current test time.

The initial run has attempt one and matching attempt metadata. It is CLAIMED with
no start time for active renewal, otherwise FAILED with a start time and no settled
time. The selected history events are inserted in the same second UoW. No admission,
claim/open or recovery service produces those initial rows. Workspace insertion,
first UoW, optional rotation insertion and second UoW are distinct commits; failure
late in seed_truth can leave earlier seed material. That apparatus boundary is
separate from atomicity of the command under test.

history_events defines 13 named event sequences, all on run-a with sequential IDs/
ordinals, fixed timestamps and empty general evidence. Step events name start-runtime
except the intentional foreign-step canary. Their purposes are:

| History | Constructed distinction |
| --- | --- |
| active-empty | Opening only |
| active-corruption-effect | Step started without run start |
| active-run-started | Opened and started active run |
| failed | Opened, started, step start/failure, run failure |
| duplicate-start | Duplicate step start |
| post-terminal-success | Success event after failed history |
| orphan-recovery-consequence | Renewal consequence without decision |
| resolved-forward-failure | Uncertain step followed by failed resolution |
| in-flight | Step start without outcome before run failure |
| uncertain | Uncertain step before run failure |
| compensation-requested | Compensation requested after forward success/failure history |
| compensation-completed | Compensation start/completion events included |
| foreign-step | Step references a different activity |

Unknown history names raise AssertionError with displayed KeyError chaining
suppressed by raise-from-None; __context__ still retains the original KeyError.
Each event passes its intrinsic record constructor, but that does not establish
that the whole sequence is a legal saga journal. Some sequences are deliberately
contradictory. Resolved uncertainty and compensation names describe manually
inserted events, not real resolution or compensation operations. The owning
recovery support interprets these sequences when a consuming test executes a command.

add_newer_failed_run stores run-b at attempt two, linked to run-a, plus a five-event
failed journal in one UoW. It leaves run metadata at its default and creates no
retry decision/action on the predecessor. This is a newer-run stale-target fixture,
not a complete service-produced retry chain or proof that run-b itself is eligible
for a subsequent retry. Its fixed IDs also make repeated use without reset a
collision rather than an idempotent helper call.

Selected consuming first/replay, eligibility, concurrency and scoped-run tests
use this fixture to invoke actual recovery commands, compare selected snapshots,
inject errors or inspect locks. The fully read
[retry fixture](activity_run_retry_interpreter_fixture.py.md) extends it with retry
commands and different seeding steps. Those uses do not turn this helper module
into its own test suite or confer full coverage of every consuming file. Assertions
about commands, transactions or provider behavior belong to those tests and owners.

Read depth: full 572-line fixture and 60-line graph helper; actual selected graph
record/projection factories and stores, plan/approval/request constructors and
inserts, schema installation entry path and Core approval/plan types were inspected.
Full UoW, recovery language/interpreter and retry-fixture context were retained;
consuming PostgreSQL suite coverage remains selected. No source/pin changes,
executable tests/imports, database setup, credentials/private-key access,
provider/runtime actions, staging or publication occurred. This documentation adds
no security surface or permission to run its destructive test setup on live data.
