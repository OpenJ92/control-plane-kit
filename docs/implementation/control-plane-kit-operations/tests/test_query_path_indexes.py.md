Source: [control-plane-kit-operations/tests/test_query_path_indexes.py](../../../../control-plane-kit-operations/tests/test_query_path_indexes.py).
Maintain this document alongside its source file. Recheck the actual query owners,
schema declarations and planner assertions when this test changes.

This 967-line module contains five test methods: two static schema-contract tests
and three PostgreSQL planner tests with multiple cases. It checks selected index
declarations and the plans chosen for actual store SQL under synthetic cardinalities.
It is not a production latency benchmark, a complete index audit or a functional
proof of decoding the seeded rows into valid Operations records.

_EXPECTED_QUERY_PATH_INDEXES names ten nonunique btree indexes. They cover activity
plan base/desired graph lookup, desired-draft chronology, all/open session chronology,
session plans, session approvals, global pending-approval chronology, plan runs and
active secret-reference registration order. The first contract test requires the
complete contract's index count to be 131, but compares only these ten entries for
relation, ordered key entries, predicate, uniqueness, empty INCLUDE entries and
btree access method. It does not compare every property of all 131 indexes.

The second test constructs exact CREATE INDEX statement strings and requires each
to occur once in schema._CURRENT_SCHEMA_SQL. It separately excludes an obsolete
pending-approval index shape containing session_id after requested_at/request_id.
This is a selected source-text comparison, not SQL parsing, database catalog
inspection or semantic equivalence of every declaration.

The actual [schema loader](../src/control_plane_kit_operations/postgres/schema.py.md)
reads packaged current_schema.sql and composes direct installation with current
schema/row verification. The ten selected entries in
[current_schema_contract.py](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema_contract.py)
and declarations in [current_schema.sql](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
match the test's intended surface. In particular, the pending timeline is the
unconditional (requested_at, request_id) index; pending eligibility is determined
by the query's decision join, not a partial index predicate. Open sessions and
active references do use status predicates. Plan base/desired and draft chronology
indexes receive declaration checks here, without dedicated planner cases.

QueryPathIndexContractTests has no database setup. QueryPathPlannerTests requires
CPK_OPERATIONS_TEST_DATABASE_URL, opens one class-level autocommit psycopg connection
and invokes install_schema. Each test truncates cpk_workspaces CASCADE; case loops
and sparse-to-dense changes also truncate. tearDownClass closes the connection;
there is no final truncation in this class. Seeds issue direct SQL and ANALYZE.
These operations belong only to the isolated owning Operations Docker apparatus.
None was executed while writing this companion.

_ObservingConnection records the latest statement and parameter tuple supplied to
execute, and returns _NoRows whose fetchall is empty. Each store method is invoked
against that double, and the test asserts the resulting page is empty. This captures
the query produced by the real method, including its cursor parameter conversion,
without running its row decoder on synthetic database contents. The double does
not count calls or reject extra SQL; a future multi-query method could overwrite
the captured statement. The inspected methods each issue one page SELECT.

_explain_observed requires captured SQL/parameters, checks normalized SQL contains
LIMIT %s and that the last parameter is 101, then runs that exact SQL and parameters
through EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) on the real fixture connection.
ANALYZE in this EXPLAIN executes the SELECT; this is stronger than a cost-only
explanation. It returns the root Plan from the first JSON result. The code requests
buffers but does not assert buffer counts, elapsed time, planner cost or planning
time. No query results are fed back through the store's row decoder.

All requests use page limit 100 with a supplied continuation cursor. The captured
101-row SQL limit includes the page sentinel. Root Actual Rows must be at most 101;
there is no positive lower bound or exact result-identity assertion. An output-row
cap is not a cap on scanned rows, join work, filtering, sorting or I/O.

The six temporal/identity cases invoke these actual query paths:

| Case | Store method | Expected index suffix | Seek/order |
| --- | --- | --- | --- |
| All sessions | activity_history.session_page | operation_sessions_workspace_timeline | created_at, session_id ascending |
| Open sessions | activity_history.session_page | operation_sessions_open_timeline | same tuple, status=open |
| Session plans | activity_history.plan_page | activity_plans_session_timeline | created_at, plan_id ascending |
| Session approvals | activity_history.approval_page | approval_requests_session_timeline | requested_at, request_id ascending |
| Plan runs | execution.run_page | activity_runs_plan_timeline | created_at, run_id ascending |
| Active references | secret_provider_store.active_page on SecretReferenceStore | secret_references_active_registration | registration_id ascending |

Every suffix in this table has the cpk_ prefix. Expected index names must be included
somewhere in the recursive plan nodes, and selected qualification field names must
appear in the matching nodes' condition text. Additional indexes or other plan nodes
are allowed. Session and plan seeds have 10,000 rows; session approvals use 201 target
requests against 20,000 foreign requests; references likewise use 201 target and
20,000 foreign rows with interleaved registration IDs. Runs seed 10,000 requests/runs
and a minimal linked session/plan so the current production joins can participate.

The actual [history queries](../src/control_plane_kit_operations/postgres/activity_history.py.md)
filter sessions by workspace and optionally OPEN. Session-plan and action queries
filter by session identity. Approval pages share a helper joining sessions and
left-joining decisions; session pages filter request.session_id, while pending pages
filter session.workspace_id and require no decision row. That pending condition
does not independently require the session to be open or the plan to remain current.

The selected [execution queries](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
join runs through execution request, plan and session workspace ownership. Event
pages use run identity and ordinal/event-ID order. These SQL shapes are different
ownership boundaries; this suite's plan-name/qualification assertions do not prove
end-to-end tenant authorization or parent validation for every route.

The pending-approval test deliberately changes cardinality. Sparse mode has one
target session with 201 target requests spaced 1000 seconds apart, against 1000
foreign sessions containing 20,000 requests. The cursor is at target request one.
It requires workspace-session and session-approval timeline indexes and forbids the
global pending timeline index in that plan. Dense mode uses 1000 sessions per tenant
with 20,000 requests each, at one-second spacing, and a cursor at target request 100.
It requires the global pending timeline index and a selected work estimate at most
303, as well as the root row cap. Both modes seed no approval decisions.

_insert_approvals calls its parameter offset_seconds but multiplies the row number
by that value (or one when zero). It is a spacing factor, not an additive offset.
Target approvals are distributed cyclically among 1000 sessions in dense mode.
The sparse/dense test observes PostgreSQL choosing different paths for these data
distributions after ANALYZE. It does not add application dispatch logic or guarantee
the same planner choice under every server version, statistic sample or workload.

_index_work sums Actual Rows multiplied by Actual Loops for nodes bearing the named
index. It does not include Rows Removed by Filter, heap visits, buffers, sibling join
work or wall time. The dense threshold is a narrow emitted-row work witness, not a
bound on total query work. The sparse case has no equivalent numerical index-work
assertion.

Five existing indexes serve as controls. Session actions and run events use their
unique parent/ordinal indexes, while the query's seek tuple also includes item ID.
Latest observations use cpk_observations_latest_subject; delegation keys use the
workspace/purpose/issuer/key-ID unique index; gateway probes use their descending
workspace/issued_at/probe-ID timeline index. The same output-row and qualification
checks apply; these controls do not compare before/after performance measurements.

The actual [observation query](../src/control_plane_kit_operations/postgres/observed_state.py.md)
uses DISTINCT ON subject_id, subject-ID seek, and newest timestamp/observation-ID
within each subject. Its seed has 5000 subjects with three revisions each. The
[delegation-key query](../src/control_plane_kit_operations/postgres/delegation_signing_key_store.py.md)
uses ascending purpose/issuer/key-ID seek across 10,000 verify-only records and
100 issuer labels. GatewayProbeStore.page uses strict less-than epoch/probe-ID seek
and descending order; its seed has 10,000 increasing epoch values and a cursor at
9000. Action/event control seeds each contain 10,000 ordinal rows.

_plan_nodes recursively follows each node's Plans list. _assert_index_qualifications
collects matching index nodes and concatenates Index Cond, Recheck Cond and Filter
text. Each expected field need only occur as a substring somewhere in that combined
text. It does not require every field to be an index access condition, parse an
expression, verify an exact operator, exclude residual filters or ensure all fields
appear together in every matching node. Thus an action_id filter can satisfy the
assertion without action_id being a column of the parent/ordinal unique index.

The seeds intentionally bypass normal service/record construction. Most temporarily
set session_replication_role to replica and restore origin in finally so synthetic
parents and payloads can be inserted without normal foreign-key trigger enforcement.
They do not disable all database constraints. Some rows are knowingly unsuitable
for normal domain decoding: plans have empty JSON payloads, and delegation rows
contain the literal public-key as PEM with unrelated synthetic fingerprints. That
is possible because the real SELECT is explained without decoding its returned rows.
These fixtures must not be presented as valid deployable state or real key material.

The seed helper names and relation identifiers interpolated into SQL are fixed
test inputs. The module uses no provider credentials or secret resolution. Its
database role needs privileges for schema installation, fixture insertion,
truncation, replication-role changes, ANALYZE and EXPLAIN ANALYZE; this is not evidence
that a restricted production reader has those privileges or should acquire them.
Autocommit means fixture phases are not one rollback-only transaction. Finally
restores replication role in the relevant seed helpers, but no general fixture
cleanup or execution-time bound is proved by this file.

The [page language](../../../../control-plane-kit-operations/src/control_plane_kit_operations/read_pages.py)
owns scope/cursor congruence and page bounds. Temporal cursors are converted through
the actual PostgreSQL cursor codec before capture. Existing page/record suites own
row ordering, output contents, negative cursor cases and public projections; this
module does not repeat those contracts. It also has no live mutation, retry,
approval, concurrency, secret-redaction or provider-evidence acceptance matrix.

Read depth: all 967 test lines, five methods, case matrices and seed/helpers were
read. Actual page methods were read in history, execution, secret-reference and
gateway-probe stores; full observed-state and delegation-store context was retained.
Selected current-schema SQL/index contracts, schema loading/installation owner and
page/cursor/temporal contracts were inspected or retained. Full history owner context
was retained from its earlier companion, but no full execution, secret-provider,
gateway-probe, schema-contract/verifier or transitive dependency review is claimed.
Security: this note introduces no runtime, database, network or credential surface.
Validation was static links, whitespace and frozen-source comparison; no application
imports, executable tests, database queries, EXPLAIN, providers or credentials were
used. Inventory and publication remain with the assigned publisher.
