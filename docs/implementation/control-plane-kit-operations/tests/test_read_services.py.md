Source: [control-plane-kit-operations/tests/test_read_services.py](../../../../control-plane-kit-operations/tests/test_read_services.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 1,421-line unittest suite contains 24 tests for InstanceReadService composition:
operator overview, graph/socket views, separate history pages/details, observation
freshness and static node-control declarations. All tests use the same PostgreSQL
setup, even the test body that only constructs invalid observation records. These
are read projections over seeded durable data, not end-to-end execution, live
health probes, provider reconciliation or proof an application is deployed.

setUp requires CPK_OPERATIONS_TEST_DATABASE_URL, raises RuntimeError rather than
skipping when absent, opens an autocommit psycopg connection, calls install_schema
and truncates cpk_workspaces CASCADE. tearDown closes that connection. The fixture
mutates its supplied database and does not isolate each test with an outer rollback.
unit_of_work opens separate connections for committed setup. service binds an
actual PostgresStoreBundle on the autocommit setup connection to workspace, graph,
draft, activity-history, execution and observed-state inputs. It supplies no runtime,
authority, secret, gateway or saved-preparation service. The default read clock is
2026-07-22 13:05 UTC and the freshness policy is the actual five-minute default.

The actual [facade](../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/instance.py)
delegates to projection owners; it does not invent parallel store semantics. Reads
in this fixture can span several autocommit statements. The overview rechecks
selected anchors/evidence, but the suite contains no concurrent writer, shared
snapshot assertion or proof that every possible interleaving is detected. The
module's direct guard invokes unittest.main; no test was executed for this note.

product_graph builds a typed OCI ContainerServerProduct with a synthetic repeated-a
digest, encodes its descriptor, instantiates hello and compiles it under DockerRuntime.
It always declares HTTP; adding control surfaces also declares a control HTTP socket
and NODE_CONTROLLABLE capability. Actual [product instantiation/materialization](../../../../control-plane-kit-core/src/control_plane_kit_core/products.py)
constructs an ordinary ApplicationBlock, deterministic private endpoint values and
product metadata. It does not pull the named image or contact those endpoints.
The actual [topology compiler](../../../../control-plane-kit-core/src/control_plane_kit_core/topology/compiler.py)
materializes those values and runtime membership without a Docker provider call.

seed_graphs stores a current product graph and an empty desired graph in workspace-a,
whose lifecycle is manually marked RUNNING. Before saving current, it injects
api_token=do-not-disclose and public_note=visible into node metadata. It assigns
current and desired graph pointers through actual stores. The selected
[pointer setters](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/graph_store.py)
associate realized projection IDs and increment the desired revision, which is one
in these fixtures. RUNNING and assigned pointers here are seeded CPK records, not
provider evidence. Tests read generated projection IDs from storage rather than
hard-coding their construction algorithm.

seed_activity adds one OPEN session, a SET_DESIRED_GRAPH action containing another
raw token canary, an empty ActivityPlan pinned to current/desired graph and projection
IDs/revision, and an informational nondestructive plan-approval request. It writes
records directly, bypassing ordinary command admission. The empty plan is not a
compiler-produced implementation of the fixture's differing graphs. The tests
exercise projection of that stored combination, not planning correctness.

seed_overview_request_and_runs writes an approved decision, a claimed request and
two runs directly with SQL: failed run-1 at attempt one and running run-2 pointing
to it at attempt two, or attempt three when broken=True. It supplies a synthetic
admission fingerprint, worker/generation and a lease ending at 12:05, before the
default 13:05 read clock. No claim/retry/coordinator service ran. Overview suggestions
therefore are navigation over selected records, not proof the lease is live or a
caller is authorized to execute.

Seven tests exercise the overview. The pending-workflow case asserts the complete
six-key envelope and exact graphs/workflow/history/next-action values: diverged
lineage, selected OPEN session and planned plan, one pending approval, no run or
receipt, empty no-run history, and command.approval.decide with plan:approve and
exact workspace/session/plan/request coordinates. Its rendered overview must omit
the token canary and the words metadata and payload. This exclusion applies to the
empty-history fixture, not to all overview envelopes containing event payloads.

The stale-base case assigns a new current graph after plan creation and requires
unavailable workflow selection, no plan and no available next action. The plural-plan
case adds a second eligible plan in another OPEN session and requires ambiguous
workflow/next-action, no selected session/plan and unavailable run/history. Neither
test elects a winner by timestamp. They cover those concrete stale/plural cases,
not every mismatched projection, revision, session or approval combination.

The gapped-lineage case uses attempts one and three. Workflow selection remains
selected, but run selection, history and next action become unavailable with no
run. The actual [overview owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/operator_overview.py)
requires a coherent single-request predecessor chain with one leaf and consecutive
attempts. This test does not separately exercise forks, duplicate IDs, multiple
requests, missing predecessors or run-count overflow.

The incomplete-receipt case adds one typed receipt with no result and no events.
It requires receipt_state=incomplete with unknown coordinator/effect/activity
fields, available-but-empty history for run-2, and no available next action. Empty
history is therefore not evidence that an admitted command completed or had zero
effects. No external interrupted operation or recovery is simulated.

The sequential-completion case adds two completed PROGRESSED receipts at different
times with different activity IDs and deliberately opposing key names. The later
completion's activity-b must be selected, and the suggested operation is deployment
execute for run-2. Chronology, not a preferred idempotency-key winner, is the source
rule; this fixture does not exhaust all key/time permutations or equal-time ties.
PROGRESSED is the recorded command result, not a claim that the run succeeded.

The bounded-history case adds one completed receipt and 101 raw step_started event
rows for the same activity, with nested evidence containing a private URL and note.
It requires run-2/attempt two/RUNNING and the exact completed command summary. History
must be nonempty, contain at most 100 items, have a next cursor, contain the first
consecutive event IDs in order and name run-2 throughout; each projected payload URL
must be <redacted>. The call uses the facade's default limit of 50. The assertion
does not demand exactly 50, follow the next cursor, count all pages, or prove that
the repeated step-start rows form an executable journal.

That same test then adds two unresolved receipts with a larger effect budget and
requires an ambiguous command summary with unknown fields and no available next
action. The actual [execution store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
selects at most two unresolved receipts preferentially, otherwise the newest two
completions; the overview treats equal latest completion times as ambiguous. Its
run query inspects overflow beyond 100 and returns unknown rather than silently
truncating a lineage. The [history store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/activity_history.py)
returns at most two eligible plans and two pending approvals. These selected source
bounds explain the projection design; this suite does not individually test every
limit or verify query work with SQL instrumentation.

Overview validates graph/workspace lineage, plan/session/request congruence and
selected retained receipt identities; it rereads selected anchors before returning.
Event history validates run identity and order and uses ordinary bounded read pages.
Malformed/incongruent evidence produces unavailable summaries for handled failures.
next_action carries required scopes and coordinates, not a grant or dispatch. In
particular the owner does not inspect lease expiry or run a policy decision before
offering an execution link. No test invokes that suggested operation.

The workspace/graph test checks current/desired authored IDs, corresponding realized
projection IDs and desired revision, then requires the token metadata value to be
redacted while public_note stays visible. It does not assert redaction of every
workspace field, a modified runtime projection or all possible secret placements.
The operator-graph test checks graph name and one provider's name/HTTP application
protocol. Its one-node fixture does not test requirement edges, ordering, every
socket field, both pointer choices or provider health.

The open-session test requests limit one, checks the five-key page envelope and
session-a, then requires a readable ReadModelError for a missing workspace. The
timeline test checks that session summaries do not embed actions/plans/approvals
and that the separate pending-approval page names the pending request. The child-page
test seeds an approved decision, queued request and claimed run directly, then
reads separate session-plan, session-approval and plan-run pages. Parent session,
plan and approval details must omit nested plan/approval/run collections as asserted.
These small pages do not establish cursor continuation or a whole-dataset byte bound.

The actual [history projection](../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/operations_history.py)
requires workspace existence and checks session containment for child reads, follows
plans/approvals through their sessions, and requires an approval's plan to belong
to the same session. Plan recovery also checks both pinned graphs' workspace. Those
are implementation boundaries; this suite seeds only workspace-a and has no explicit
foreign-session/plan/approval rejection matrix. The missing-workspace case is not
substitute evidence for cross-workspace isolation. These checks are containment,
not authentication or authorization of a principal.

Plan-detail and approval-detail tests check plan identity, projection/revision fields
where asserted, the Core activity-plan schema tag, ready_for_execution=True and
reverse-transition recovery mode. They use the empty stored plan. The actual
[ActivityPlan readiness property](../../../../control-plane-kit-core/src/control_plane_kit_core/planning/activity_plan.py)
means absence of ReviewChange blockers, not provider readiness, completed approval
or effect success. The projection encodes the stored plan with the Core codec and
computes recovery from desired graph back to pinned base via
[plan_recovery_transition](../../../../control-plane-kit-core/src/control_plane_kit_core/planning/recovery.py).
This is a proposed reverse graph transition, not an inverse-effects guarantee or
automatic compensation. The tests do not move workspace pointers before detail
reads to distinguish pinned recovery from accidental current-pointer use.

The public-ingress detail test uses a second seeding path: empty base, desired
hello/gateway/cloudflared product graph, one runtime-control connection from hello's
internal provider to the gateway requirement, and named ingress targeting gateway
control with a connector and hostname. Repeated a/b/c image digests and the authority
reference are fixture values. The test checks recovery mode and desired/base source
and target names, not ingress change contents, credentials, DNS publication, connector
readiness or actual teardown. No registry, Cloudflare or hostname is contacted.

Six tests cover observations/freshness and typed correlation. observation constructs
records with BoundedEvidence, APPLICATION_HEALTH/HEALTHY probe identity, runtime-private
endpoint context and configurable status/time/freshness/graph. Even when status is
STARTING or UNKNOWN, the helper retains HEALTHY as probe_outcome; do not interpret
it as an end-to-end health-probe conversion fixture.

The latest-per-subject case stores old/new hello records plus a worker record for
graph-old. It requires obs-new then obs-other, fresh/redacted hello evidence, and
stale graph-changed worker evidence. The actual
[observation store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/observed_state.py)
uses workspace-filtered DISTINCT ON subject_id ordered by subject then descending
observation time/ID and fetches limit+1. This test does not distinguish equal-time
ties or traverse a subject cursor. Its does_not_rewrite_graph_truth assertion checks
a workspace value captured before the read, not a newly fetched pointer afterward.
The source projection has no graph mutation call; the test alone is not a post-read
database-invariance proof.

Recorded STALE remains stale with recorded-stale reason. The exact-age test requires
fresh at five minutes and stale/expired one microsecond later. The malformed-time
test constructs an otherwise valid record with not-a-timestamp, expects canonical-UTC
ValueError from the store put, then reads an empty page and verifies the original
record's timestamp and freshness are unchanged. Actual timestamp encoding precedes
the SQL execute call; this is not an exercise of the read projection's fallback for
malformed stored time. The future-time test checks future-timestamp reason for an
instant one microsecond after the clock, without separately asserting every freshness
field. The correlation test rejects graph-only identity and PROCESS/HEALTHY pairing;
it does not enumerate the complete probe-kind/outcome/endpoint matrix.

The full [observation projection](../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/observations.py)
interprets records at one clock instant with precedence: recorded stale, uncorrelated,
graph changed, malformed timestamp, future timestamp, age beyond the maximum, then
fresh. It returns derived freshness/reason without rewriting the record. Some of
these branches, invalid clocks and custom maximum-age policies are outside this
suite. Latest persisted evidence and clock freshness are not a fresh provider probe
or comprehensive truth about every node.

Three tests cover control surfaces. The baseline checks graph/node IDs and exclusion
of capabilities from node metadata; despite its without_endpoint_leakage name, that
test does not scan the complete response for endpoint strings. The declaration test
constructs one SCALAR routing variable with READ_STATE/STATE_V1 and
APPLY_COMMAND/REPLACE_SCALAR_V1/TRANSITION_V1 contracts. It requires equality with the
typed declaration descriptor and scans that declaration JSON for http://, the token
canary and exact state/version/status keys. It does not invoke either operation,
read live variable state or scan every field outside the declarations list.

The malformed-declaration test has four subcases: add unknown state containing the
canary, multiply the surface list to 17, reference a missing control socket, or
change provider and endpoint protocol together to PostgreSQL/TCP. Each subcase
reseeds, copies the valid descriptor, disables autocommit and updates the stored
authored descriptor within that connection's transaction. control_surface must raise
exactly invalid stored graph descriptor with both cause and context None and no
canary in its message. Finally it rolls back, restores autocommit, compares the
stored descriptor to the valid copy and calls install_schema again. This deliberately
tests uncommitted malformed storage on the same connection, not a successful durable
corruption or a concurrent recovery/migration workflow.

The actual [workspace/graph projection](../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/workspace_graph.py)
decodes and validates graph data for operator/control views, handles selected codec/
validation failures outside the caught exception, re-encodes and redacts control
data, then projects declaration fields without endpoint state. Selected Core
[graph codec](../../../../control-plane-kit-core/src/control_plane_kit_core/topology/codec.py),
[node-control codec](../../../../control-plane-kit-core/src/control_plane_kit_core/node_control.py)
and [graph validation](../../../../control-plane-kit-core/src/control_plane_kit_core/topology/validation.py)
enforce closed surface fields, the 16-surface cap and existing HTTP provider sockets.
The ordinary workspace graph descriptor path redacts stored data without this same
decode/validate sequence; the malformed test specifically calls control_surface.

Security and size boundaries: [read redaction](../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_services/_redaction.py)
recurses through mappings/lists/tuples, masking secret-shaped keys, address/URL/
environment keys and selected environment-binding values. It is key-based, not a
universal scanner for sensitive text under harmless keys. The selected
[record values](../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
bound evidence JSON to 4096 bytes, depth four, 32 items/fields per container and
512-character strings, with typed/probe correlation validation; mapping metadata
and raw action payloads are different record surfaces. Canary assertions in this
suite do not exhaust those size or rejection laws. The actual
[page values](../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_pages.py)
validate scope/cursor/limit and preserve cursor metadata when mapping selected items.
Row-count bounds must not be promoted into a universal graph/detail payload budget.

Read depth: complete 1,421-line suite, all 24 tests and every helper/mutator/setup;
complete facade, workspace_graph, operations_history, observations, operator_overview,
models and _redaction modules, plus complete Postgres observed-state store. Previously
read full history/execution stores, UoW and schema installer informed the fixture;
selected overview SQL, event decoder/page, pointer setters, record/evidence/probe
validators and read-page construction were checked again. Selected actual Core
product instantiation/materialization, plan readiness/codec/recovery and graph/
node-control codec/validation paths were inspected; the topology compiler was
previously read in full. No complete Core products, node-control, planning, graph
validator, records, graph-store or read-page audit is claimed. Static validation
only: links, whitespace and frozen source/test comparison. No application imports,
executable tests, database/provider calls, credential access, source/inventory edits,
publication, merge or live acceptance were performed for this companion.
