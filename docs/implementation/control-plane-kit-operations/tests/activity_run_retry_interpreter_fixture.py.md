Source: [control-plane-kit-operations/tests/activity_run_retry_interpreter_fixture.py](../../../../control-plane-kit-operations/tests/activity_run_retry_interpreter_fixture.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 218-line fixture adds retry commands and seed helpers to the existing
[Postgres recovery fixture](../../../../control-plane-kit-operations/tests/execution_lease_recovery_fixture.py).
It is a mixin, not a standalone unittest.TestCase or an acceptance result. Consumers
supply assertion methods and, when needed, inherited database setup. The
[contract test](test_activity_run_retry_interpreter_contract.py.md) also constructs
it without setup solely to build a command; that use opens no fixture connection.

The import guard treats only absence of the exact retry-interpreter module as
missing language. Other ModuleNotFoundError values propagate. A missing service
export becomes None and require_retry_service asserts its presence before service
construction. This is not a fake implementation or a way to run without package
dependencies: the inherited fixture imports psycopg and actual Operations stores.

retry_service_with_sequence constructs the actual ActivityRunRetryCommandService
with this fixture's UoW factory and an inherited Sequence. Sequence removes the
first supplied string and appends it to calls; exhaustion raises rather than
generating a fallback ID. retry_service discards only the returned tracker handle.
The actual interpreter allocates successor, decision-event, opened-event and action
IDs in that order on its fresh path; replay has a separate retained-history path.
The fixture does not itself assert allocation counts or prove replay behavior.

retry_command wraps request/prior-run text, an exact fence, RecoveryAuthority and
IdempotencyKey using the actual pure command constructor. Defaults are request-a,
run-a, worker-a generation 7, operate scope and synthetic operator/reference/key
strings. Scopes are caller-selectable, including empty or renewal-only tuples for
denial tests. Constructing RecoveryAuthority normalizes its closed scope values;
it neither authenticates the reference nor proves an approved operation. See the
[pure retry owner](../src/control_plane_kit_operations/activity_run_retry.py.md).

Inherited setUp requires CPK_OPERATIONS_TEST_DATABASE_URL, connects with autocommit,
installs the current schema and truncates cpk_workspaces CASCADE. reset_truth repeats
the truncate before seeding. tearDown truncates and then closes if the connection
is open; those calls are sequential, not a cleanup guarantee after every exception.
The fixture trusts the configured URL: it does not create a unique schema, verify
database ownership or protect unrelated workspace rows. The established
[package suite](../../../../control-plane-kit-operations/test.sh) supplies disposable
Docker PostgreSQL and the environment; these destructive helpers are not permission
to point tests at a shared or operator database.

The parent seed builds a workspace, session, one-activity StartRuntime plan,
approval records, claimed request, run and selected synthetic journal. Its
[graph helper](../../../../control-plane-kit-operations/tests/graph_lineage_fixture.py)
saves empty authored graphs and their identity projections. Records are inserted
through stores and direct SQL, not through the whole public preparation/approval/
execution chain. A gateway-rotation approval-subject alternative is an intentional
fixture variation, not evidence that retry may consume an unrelated approval.

reset_retry_truth uses the parent's expired-renewal seed selection so the default
run is FAILED with the chosen history, then directly updates claim timestamps to
2098/2099. It does not renew a lease through a service. These fixed dates establish
test input, not a mocked or permanently valid database clock. The seed consists of
multiple committed UoWs and autocommit writes; it is not one atomic retry operation.

seed_foreign_run copies plan-a into plan-b and request-a into request-b through
INSERT SELECT, replacing selected IDs and the execution idempotency key. It then
adds a claimed run under request-b in a committed UoW. Other copied fields, including
session and approval references, remain fixture data. The helper has fixed plan/
request identities and no idempotent repeat/reset guard. It creates a row foreign
to request-a for scoped lookup tests, not an independently admitted second deployment.

_fail_run_for_retry combines actual lifecycle StartActivityRun and FailActivityRun
commands with a separately committed manual insertion of STEP_STARTED and
STEP_FAILED for start-runtime. It uses one synthetic time, fixed run-b-prefixed
event/action/key identities and a terminal adapter-error message. The run parameter
does not make those generated identifiers reusable across arbitrary runs. No adapter
actually failed: the manually seeded step events and supplied FailureEvidence are
test evidence. Start, step insertion and fail are separate transactions, not one
all-or-nothing helper. The actual
[UoW](../src/control_plane_kit_operations/postgres/unit_of_work.py.md) commits on
successful exit after commit is requested.

Selected first/replay consumers use that helper to construct a second linked retry,
and the foreign-run helper to challenge request-scoped lookup. An effect-attempt
fixture also borrows these methods by supplying an existing connection/UoW. Their
assertions, not this support file alone, establish the tested behavior. Inherited
snapshot compares selected request claim fields, events, session actions and run
fields; it is not a complete database inventory or a provider observation.

Read depth: full fixture, full 572-line parent, full 466-line retry interpreter,
full 188-line contract test, graph seed helper and UoW; selected first/replay and
effect-attempt consumers and package-suite setup. Pure retry owner/tests were fully
read in the preceding review; not all fixture-consuming integration tests were read
for this note. No executable validation, database connection, source/pin change,
credential access or provider/runtime action occurred. Documentation grants no
cleanup, recovery or retry authority.
