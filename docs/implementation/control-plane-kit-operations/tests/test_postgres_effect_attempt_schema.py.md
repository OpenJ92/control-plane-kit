Source: [control-plane-kit-operations/tests/test_postgres_effect_attempt_schema.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_schema.py).
Maintain this document alongside its source file. When assertions, SQL contracts,
decoding or installer dependencies change, verify and update this companion in
the same change.

Seven tests in this 629-line suite cover selected effect-attempt SQL constraints,
current-row reconstruction, bounded paging and refusal of incompatible existing
schema/data. EXPECTED_CHECKS pins seven complete normalized check expressions.
The tests exercise durable representation and schema admission; synthetic started,
failed and recovered-failed records do not establish executed provider outcomes
or authorization to retry an attempt.

## Fixture, connections and evidence ownership

The class inherits
[PostgresEffectAttemptStoreFixture](../../../../control-plane-kit-operations/tests/postgres_effect_attempt_store_fixture.py),
which combines record builders with PostgreSQL lease-recovery setup. Its optional
store import tolerates only that exact missing module; require_store asserts
presence rather than skipping. The suite's record, event, intent, persistence,
unit-of-work and safe-error helpers come from these inherited fixtures.

Base setup requires CPK_OPERATIONS_TEST_DATABASE_URL, opens an autocommit
connection, installs/verifies schema and truncates cpk_workspaces CASCADE before
reset_truth seeds active-empty execution truth. Every method inherits this setup,
including the method whose assertions inspect the static schema contract.
Execution therefore belongs to the isolated owning Docker/PostgreSQL suite, not
a host-only static-test shortcut. No such execution was performed for this note.

persist commits event/intent/attempt material through one fresh unit of work.
The first test instead inserts all 130 records in one unit of work and requests
commit after the loop. Actual
[PostgresUnitOfWork](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
commits on successful exit only when requested, otherwise rolls back, and closes
in finally. Subsequent SQL corruption and checks use the separate autocommit
fixture connection. Fixture teardown truncates and then closes; it has no nested
finally guaranteeing close if truncation fails. This is test-namespace cleanup,
not a production recovery or migration mechanism.

assert_safe_error checks absent cause/context and at most 256 characters in the
combined str/repr rendering, excluding supplied canaries. Its calls here protect
selected row/installer diagnostics, not every SQL error or log. The internal
FailureEvidence monkeypatch and _TracingConnection are test-local observation
tools; neither is an application adapter or substitute schema implementation.

## Late payload drift and paged current-row validation

The first test persists 130 distinct activity attempts with matching events and
intent evidence, then changes the final activity's start-event state_fingerprint
inside JSONB to another 64-hex string. A direct attempt-store get must raise the
exact categorical OperationsRecordError, omitting the event ID and altered hash.
This is semantic commitment drift even though its text remains fingerprint-shaped.

The actual
[attempt store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_attempt_store.py)
loads scalar columns and the original/latest events, checks event coordinates,
and constructs EffectAttemptRecord. Selected
[record validation](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempts.py)
recomputes the state commitment and compares the expected event evidence.
The store converts selected ValueError/OperationsRecordError failures into the
categorical row error outside their handlers. SQL foreign keys alone do not
establish that JSON event evidence agrees with reconstructed state.

_TracingConnection records execute calls and delegates other attributes to the
real connection. install_schema must reject with the exact reset-required message
and safe-error checks. Filtering traced statements containing FROM
cpk_effect_attempts must find exactly three scans. Each must order by the composite
run/activity/attempt key, have LIMIT %s with final parameter 64, and omit FOR
UPDATE. The first has no keyset predicate; the next two have a tuple-greater-than
predicate and four parameters. Actual scanner source performs those paged reads
and decodes each row, reaching the corrupted record on the third page.

These assertions check statement shape, query count and page-limit parameters;
they do not check exact cursor values, EXPLAIN plans, index selection, elapsed
time, total query count or concurrent readers/writers. Paging bounds rows per
scan, not the overall amount of validation. Event reconstruction performs further
reads. Absence of FOR UPDATE in those scans does not mean installation is
lock-free: the installer takes namespace advisory and SHARE relation locks.

Attempt/event row counts and the selected already-corrupted payload must be
unchanged after rejected installation. This establishes those concrete preserved
observations, not a snapshot comparison of every row, object or attempted SQL
write. The test expects refusal; it does not repair the altered evidence.

## Malformed failure data versus unexpected implementation errors

The second test persists a failed record with explicit terminal FailureEvidence.
Before corrupting data it temporarily replaces execution_module.FailureEvidence
with a function raising a particular KeyError. Direct get_event must propagate
that exact object, and finally restores the constructor. Unexpected constructor
errors must not be mistaken for missing persisted keys in this selected path.
This assertion does not establish every exception policy in the store/installer.

The test then replaces the event's failure object with empty JSON. Direct
[execution-store decoding](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
must raise ValueError with the exact malformed-failure message and clean bounded
diagnostics. Actual _failure_evidence explicitly checks the required category,
code and message keys before constructing FailureEvidence; details defaults to
an object. Both get and get_for_update on the attempt store must translate that
failure to the categorical row error. No contention is introduced, so the second
read is not evidence of lock scheduling or isolation under concurrency.

Installation must then report reset required with safe diagnostics, leaving the
selected malformed payload unchanged. The current-row entry calls the attempt
scanner and translates selected validation failures to CurrentRowDrift; the
installer interprets that as incompatible current truth. This is a layered
representation check, not a service-level failure fold or runtime retry.

## Static contract and selected SQL rejection laws

The large contract method examines the imported
[current schema contract](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema_contract.py).
It checks relation presence, all 21 attempt column names in physical order,
their formatted types/nullability, and absent identity/generated/default values.
It requires exactly fifteen named constraints: seven check expressions plus the
primary key, two event uniqueness keys and five foreign keys. Key/FK endpoint
columns are ordered; FK update/delete/match codes must be a/a/s (NO ACTION and
SIMPLE). The event table's supporting event/run/ordinal unique key is checked
separately. This method does not independently assert every constraint flag,
column collation or relation property available in the contract object.

The seven check expressions cover positive/canonical attempt identity, fence
bounds, fingerprint grammar, complete immediate predecessor coordinates,
status/outcome shape, complete matching recovery alternatives and original/latest
event progression. The actual
[current DDL](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
declares those checks and relational keys. Schema typing and FK/check structure
are distinct from the record-level JSON commitment validation above.

The method also requires exactly three attempt indexes, all unique, with exact
owning constraints, primary flags, ordered key entries and no include entries;
it checks the supporting event index's ownership and key entries. It does not
assert every index property or benchmark any access path. Its final atlas check
extracts the cpk_effect_attempts section and forbids the word migration; it does
not validate every statement in the atlas or execute restore/teardown.

Three further tests persist valid fixture records and deliberately issue SQL
updates. Fence generation zero must produce raw psycopg CheckViolation naming
the fence check. Nulling one prior coordinate on attempt two must name the prior
check. Nulling recovery_resolution on a recovered-failed record must name the
recovery check. These are isolated examples of PostgreSQL enforcing those laws,
not an exhaustive malformed-row matrix, authorization check or redaction claim
for raw database diagnostics. They do not use compare-and-set or race two writers.

## Existing namespace refusal and cleanup

The final test creates a UUID-named schema on a new autocommit connection, sets
its search_path and creates only a minimal cpk_workspaces table. install_schema
must report reset required with no cause/context, and to_regclass must find no
cpk_effect_attempts table. This proves the selected partial namespace is refused
without creating that relation; it does not establish an automated reset path or
compare all schema objects after refusal.

The actual [installer](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/schema.py)
uses a transaction and namespace advisory lock. It installs bundled DDL only for
an object-free namespace; existing namespaces must have expected relations and
pass contract/adjunct/current-row verification under relation locks. Its bounded
reset-required error requests an external decision; it does not execute a reset.
Unexpected installation exceptions instead receive a different generic failure.

Finally the test closes the additional connection and drops its UUID schema via
the base connection with CASCADE. Those operations are sequential, so a close
failure can prevent the drop; inherited teardown still has its separate limits.
No retry, fallback database, privileged provider operation or live cleanup is
part of this suite's assertions.

Read depth: full 629-line suite, seven tests, local tracing/patch helper bodies,
full 126-line store fixture, full 280-line attempt store and 105-line installer,
actual selected lease/record fixture helpers, record commitment, execution decoder,
current-row/contract-verifier entry points, named contract/DDL entries, atlas
section and full unit of work. Imported Core state contracts are retained context;
this pass is not a full audit of every seed builder, verifier SQL query, state
owner or schema relation. No tests, application imports, database connections,
credentials, source changes or provider actions ran in authoring. This note adds
no security surface and does not authorize the destructive fixture operations it
describes.
