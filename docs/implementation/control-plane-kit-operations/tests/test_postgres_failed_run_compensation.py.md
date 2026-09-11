Source: [control-plane-kit-operations/tests/test_postgres_failed_run_compensation.py](../../../../control-plane-kit-operations/tests/test_postgres_failed_run_compensation.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 569-line PostgreSQL suite contains twelve tests for failed-run compensation
admission. It checks reverse-order program construction, persisted replay,
provenance and relational drift, transactional rollback, two concurrent callers
and selected admission rejections. Admission records an inverse program and marks
the run COMPENSATING; these tests do not execute its StopNode or StopRuntime steps.
The direct-execution guard invokes unittest.main.

The inherited
[FailedRunCompensationFixture](failed_run_compensation_fixture.py.md)
requires CPK_OPERATIONS_TEST_DATABASE_URL, installs schema and truncates workspaces
with CASCADE through an autocommit connection. Each service transaction receives
a separate psycopg connection. Tests explicitly seed a FAILED run with successful
runtime/node effects and a failed health-check event history. The successful
effects use typed synthetic results and the actual Core fold; no provider created
them. Setup spans separate commits, and teardown truncates before closing. This
documentation review did not run that setup or access a database.

The first-admission test asserts replayed=False, exactly three allocated IDs in
program/event/action order, COMPENSATING run status, RUN_COMPENSATION_STARTED at
ordinal ten and the begin-compensation action type. Program steps must reference
start-node then start-runtime, with exact StopNode/StopRuntime types and completion
ordinals six/four. Fresh SQL reads check the run status, one parent program and
both ordered relational step identities. The record's program fingerprint equals
the program's own fingerprint; evidence and authority-reference fingerprints are
checked for lowercase 64-character hex shape, without independent expected hashes.
Selected original-effect rows must remain equal to their pre-admission values.

The actual
[command service](../../../../control-plane-kit-operations/src/control_plane_kit_operations/failed_run_compensation.py)
locks request and action-idempotency truth before choosing replay or fresh admission.
Fresh admission checks session/plan/run/workspace lineage, terminal failure and
complete successful-effect evidence, derives plan-declared inverses, writes event,
action and program, and conditionally changes FAILED to COMPENSATING in one unit
of work. The first test observes the successful result and committed selected rows;
the separate injected-failure test supplies rollback evidence. Neither test is a
complete inventory of every admission guard or writer acknowledgement.

Exact replay first admits a program and then creates a new service using the same
unit-of-work factory, with clock and ID callables that count and raise on any call.
The replay result must equal dataclasses.replace(first, replayed=True), both counts
must remain zero and snapshot_all must remain equal. A new transaction reads the
persisted result, but no process or PostgreSQL server is restarted. Replay is not
proved to issue no SQL: the service still reads/locks truth and requests transaction
commit. Write freedom here is inferred from equality of the selected snapshots.

Changing only the private authority reference under the same idempotency key must
raise FailedRunCompensationIdempotencyConflict and preserve the snapshot. Separately,
direct SQL replaces the stored authority-reference fingerprint with f-times-64;
replay must raise RunLifecycleConflict and leave that already-tampered snapshot
unchanged. These test provenance sensitivity, not authentication of the reference
or absence of arbitrary secrets from exception text, logs or all persisted fields.

Three relational drift subcases delete step two, change step one's material source
to base-graph, or reorder the two steps using a temporary position three. Each
resets and admits the fixed program before applying separately autocommitted SQL.
Five further subcases change parent plan lineage, parent source-failure code,
action actor, event program ID or run status. The plan helper copies plan-a to
plan-b with the listed columns before changing the compensation parent reference.
Each replay accepts either RunLifecycleConflict or OperationsRecordError and must
preserve the post-mutation snapshot. These broad exception tuples do not identify
one exact rejection layer, and the tests do not repair the injected corruption.

The selected actual
[compensation store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/failed_run_compensation_store.py)
persists a canonical JSON program preimage plus parent and relational step rows.
Readback reconstructs the program and record, compares lineage/fingerprints and
compares ordered relational step descriptors with the program's steps. The command
replay path additionally binds action, event, actor, private-reference fingerprint
and COMPENSATING run truth. Those mechanisms explain the selected drift cases;
the cases do not enumerate every malformed preimage, field or imported constructor.

The rollback test resets and reseeds for seven boundaries: event, action, program,
step one, step two, run fold and commit. Its connection wrapper first executes SQL
on a real psycopg connection, then normalizes query text and counts only the listed
INSERT/UPDATE prefixes. At the selected count it raises the exact supplied
RawDependencyFailure. The seventh case raises in the connection's commit method
before calling the underlying psycopg commit. Each case asserts exception identity
and unchanged snapshot_all; it does not assert ID counts, exception redaction or
every table/column in the database.

The actual
[PostgresUnitOfWork](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
defers commit until successful context exit, rolls back an exception or a failed
connection commit, and closes in finally. Thus the first six injections occur
after their selected SQL statement but before commit, while the seventh exercises
a pre-commit connection failure. This does not model a lost acknowledgement after
a successful server commit, process termination or rollback/close failures. The
write-count mapping depends on the current SQL order and exact recognized prefixes.

Both concurrency tests use two ThreadPoolExecutor workers and a two-party barrier
with a five-second timeout before service execution. Each worker receives a fresh
service/connection and its own three suffixed IDs; each future has a fifteen-second
result timeout. Same-key callers must return one fresh and one replayed result,
equal programs and records, and one persisted parent. Competing keys must return
one non-exception result and one RunLifecycleConflict, with one persisted parent.
Only RunLifecycleConflict is converted into a returned value in that worker.

The barrier aligns entry into service work; it does not force both callers to
reach a particular database read or inspect backend lock waits. Either suffix may
win, and the suite does not require both winner orders, compare every event/action
or step in these cases, or count allocations. The future timeouts do not cancel
database operations, and executor context exit waits for workers; the fixture
does not configure database statement/lock timeouts. These are static boundaries
of the tests, not observed failures from this documentation review.

The rejection matrix covers wrong scope, foreign workspace/request/plan, stale
current graph and changed execution-intent fingerprint. Wrong scope accepts
ValueError or RunLifecycleDenied around command construction plus service execution;
the other cases require RunLifecycleConflict. Each case compares a snapshot that
omits compensation tables and separately requires zero compensation parent rows.
There is no exact error-message, cause/context or allocation assertion. These six
examples do not exhaust every command field or fresh-admission invariant.

The uncertain/fabricated/already-started test has two mutation subcases: mark the
node attempt uncertain with a changed outcome fingerprint, or delete its outcome
observations and outcome row. Each requires RunLifecycleConflict. A final fresh
seed admits once and requires conflict for a second idempotency key. Unlike the
rejection matrix, these checks have no before/after snapshot or zero-row assertion.
The method's word "missing" refers to missing outcome evidence in this body; it
does not add a separate missing-run/request test.

The final test obtains only the command module's source through inspect.getsource
and rejects six literal substrings: docker, RuntimeEffectInterpreter, .execute(,
prune, cleanup and retry. It also requires the two compensation table names among
pg_tables in the current schema. This finite text check is neither an AST/import
graph analysis nor a proof that dependencies cannot call providers. Table-name
presence does not establish complete schema, constraint or migration parity.

snapshot_all combines selected run fields, selected event fields, selected action
fields and the inherited original_truth attempt/outcome projections; by default
it adds all columns of compensation parents and ordered steps. It omits, among
other data, event timestamps, action actor/creation time, intent evidence, request
claims and plan/workspace state. The action-actor and copied-plan mutations therefore
are not themselves fully represented by the snapshot, although replay rejection
is asserted. Separate autocommit queries are not one consistent database snapshot.
Queries cover whole tables, so their ordering and completeness are tailored to
the isolated fixture, not an arbitrary populated deployment. Raw payload/preimage
values are not redacted by this helper; the suite defines no redaction assertion.

Read depth: the complete 569-line suite, all twelve tests and all local helpers
were read. The complete 459-line inherited fixture and 529-line command owner
were previously read; the admission/replay paths were refreshed for this companion.
The full unit-of-work owner and selected compensation-store projection, insert,
readback and comparison paths were checked. No full store/dependency review or
live restart/provider validation is claimed. Validation was documentation-only:
local links, whitespace and frozen-source comparison. No application imports,
tests, database/provider calls, credential access, source/inventory edits or
publication were performed.
