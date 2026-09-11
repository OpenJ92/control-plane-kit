Source: [control-plane-kit-operations/tests/test_postgres_activity_run_retry_eligibility_rollback.py](../../../../control-plane-kit-operations/tests/test_postgres_activity_run_retry_eligibility_rollback.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These nine tests protect fresh-retry eligibility ordering, complete result planning
and selected transaction failures in the actual
[retry interpreter](../src/control_plane_kit_operations/activity_run_retry_interpreter.py.md).
They use PostgreSQL stores and PostgresUnitOfWork through the
[retry fixture](activity_run_retry_interpreter_fixture.py.md), with wrappers and
injected failures at specific boundaries. They are not replay, concurrent-schedule,
process-crash or external-provider recovery tests. RawDependencyFailure is a local
RuntimeError subclass used to distinguish unexpected dependency failures from
categorical RunLifecycleConflict.

The fixture constructs an approved, claimed request and failed-run history with
synthetic future lease timestamps. Its inherited setup installs schema and
truncates workspace-owned truth in the supplied test database; the fixture itself
does not establish exclusive database ownership. Source review did not execute
that setup or any of the mutations described here.

The first test rejects four selected non-temporal cases: changed request fence,
RUNNING instead of FAILED prior, a duplicate-start journal, and exhausted attempt
counter. It replaces lease observation and add_run with forbidden-call sentinels,
and uses an ID factory that must not run. Each command must raise bounded,
chain-free RunLifecycleConflict without selected authority/worker/run canaries,
and preserve the snapshot taken after fixture corruption. This distinguishes
pre-observation rejection from a later rollback after IDs or the first run write.
It does not independently instrument every possible store mutation method.

For exhaustion, the test inserts run-z, moves run-a to attempt 2,147,483,647 with
run-z as predecessor and matching metadata, then moves run-z to attempt one with
no predecessor. This makes the presented failed run the latest at the integer
ceiling. It is a constructed boundary state, not a history produced by billions
of successful retries or proof of every predecessor-chain law. The actual
interpreter rejects the ceiling before planning a successor.

The expired-claim test sets lease expiry to the year 2000, wraps the actual store
observation to count calls and requires exactly one observation before conflict.
The ID sentinel stays unused and selected truth is unchanged. The equality test
also calls the real observation, then substitutes its observed_at with the claim's
expiry and sets expired=True. It proves the interpreter honors that returned
expiry classification before IDs; it does not arrange for PostgreSQL's clock to
land exactly on the expiry instant. The actual
[execution store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
uses lease_expires_at <= clock_timestamp observation, supplying the source-side
equality rule. Neither test proves arbitrary observer implementations trustworthy.

The four-identities test supplies three IDs, then raises one specific raw error
on the fourth factory call. add_run is forbidden, the raw exception object must
escape unchanged, and selected truth must match its prior snapshot. Thus a fourth
identity failure cannot leave the first record written. It does not separately
try failure at every earlier factory call, validate every malformed generated ID
or undo consumption of IDs outside the database transaction.

The persistence-order test wraps actual add_run, add_event and add_action methods,
retaining their database behavior. Its exact trace is run, recovery decision event,
opened event, action. A separate test traces observation, event ordinal, action
ordinal, all four named ID calls, complete ActivityRunRetryResult construction and
first run write. Those wrappers call the original implementations; this trace
asserts that the complete typed result is constructed before the first write.
It does not independently trace every SQL statement or connection commit.

The result-construction-failure test substitutes the interpreter's result binding
with a function that raises one raw error, and forbids add_run. It requires exact
exception identity and unchanged selected truth. This verifies placement of the
construction boundary; the substitute does not exercise a particular real result
validator rejection. The actual result's lineage, metadata and action laws belong
to the [pure result tests](test_activity_run_retry_result.py.md).

Unequal-return coverage tests four stages: run, decision, opened and action. Each
wrapper first calls the original store method, allowing that stage's insert, then
returns an unrelated object for the selected stage. The interpreter must raise
RunLifecycleConflict and the selected snapshot must return to its prior contents.
This is meaningful rollback after actual writes, including all four records for
the action case. It protects the adapter return-equality check, not an independent
read-back after each insert. It also does not simulate a store committing outside
the shared UoW.

Late-failure coverage uses the same four stages plus connection commit. For a
selected write stage, its wrapper raises the raw error before calling that stage's
original method; preceding stages use real inserts. All cases require the exact
raw exception object and unchanged selected truth. The run case has no preceding
insert; decision follows the run, opened follows run/decision, and action follows
run/both events. These are injected application-boundary exceptions, not PostgreSQL
constraint, serialization or connection-loss failures after every SQL statement.

commit_failing_unit_of_work creates a wrapper around a real psycopg connection:
execute, rollback and close delegate, but commit raises the supplied error without
calling the underlying connection's commit. All four real inserts therefore occur
before this failure while remaining uncommitted. The actual
[PostgresUnitOfWork](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
only records a commit request in commit(); context exit attempts the connection
commit, catches its failure, rolls back and closes. The test checks raw identity
and selected persisted state rather than a separate rollback/close call trace.
It does not reproduce an acknowledged server commit, lost commit acknowledgment,
rollback failure or a process dying during transaction completion.

Across rejection tests, the actual
[base-fixture snapshot](../../../../control-plane-kit-operations/tests/execution_lease_recovery_fixture.py)
compares request status/claim fields, events for that request's runs, session
actions and selected run identity/attempt/status/timing fields. It omits some
columns, including run metadata/creation time and action creation time, and other
tables. Equality is strong enough to catch the represented extra run/event/action
rows, but is not a complete database diff. Baseline snapshots are taken after each
intentional fixture edit, so rejection must preserve that existing state rather
than restore the original seed or repair corruption.

safe_error checks absent cause/context, combined str/repr length at most 512 and
selected canary absence for expected conflicts. Unexpected factory, construction
and late errors instead preserve identity, including their supplied text; these
tests do not promise universal exception sanitization. Monkeypatched methods are
restored with finally blocks. There are no credentials or provider response bodies
in the intended durable retry evidence.

The ownership law protected here is that eligibility and complete planning precede
new durable work, and all four writes participate in the caller-owned transaction.
The tests do not grant authority to retry an ambiguous effect, implement automatic
retry/compensation, or establish safety of an arbitrary injected UoW. Scope and
replay rules, competing commands and later execution of the new run have separate
governing tests and owners.

Read depth: full 477-line source, all nine tests and commit wrapper, full 218-line
retry fixture and actual PostgresUnitOfWork; retained full 466-line interpreter,
299-line pure owner and its result tests, with selected actual lease observation
and base-fixture setup, snapshot and failed-run seed. No source/pin changes,
executable tests, database setup, credentials/private-key access, provider/runtime
actions or publication occurred. This companion adds no security surface and makes
no claim that the tests ran or passed during authoring.
