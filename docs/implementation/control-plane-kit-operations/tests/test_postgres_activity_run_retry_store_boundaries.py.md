Source: [control-plane-kit-operations/tests/test_postgres_activity_run_retry_store_boundaries.py](../../../../control-plane-kit-operations/tests/test_postgres_activity_run_retry_store_boundaries.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These three tests exercise six read-error boundaries in the actual
[retry interpreter](../src/control_plane_kit_operations/activity_run_retry_interpreter.py.md).
They run against the PostgreSQL
[retry fixture](activity_run_retry_interpreter_fixture.py.md), but replace the
selected store method with a function that raises a supplied exception before its
original implementation runs. They test interpreter handling of store outcomes,
not whether a real malformed row triggers that exception in the store decoder.

EXPECTED_BOUNDARIES lists the following intercepted reads and required messages
for ValueError/OperationsRecordError. Its tuple order is test enumeration, not
the interpreter's actual read order; the locator request is read before the action.

| Boundary | Store method | Required conflict text |
| --- | --- | --- |
| Action | PostgresActivityHistoryStore.action_for_idempotency | operation action history is invalid |
| Session | PostgresActivityHistoryStore.get_session_for_update | operation session history is invalid |
| Locator request | PostgresExecutionStore.get_request | execution request history is invalid |
| Locked request | PostgresExecutionStore.get_request_for_update | execution request history is invalid |
| Prior run | PostgresExecutionStore.get_run_for_request_for_update | activity run history is invalid |
| Latest run | PostgresExecutionStore.get_latest_run_for_request_for_update | activity run history is invalid |

The decoder-category test crosses all six boundaries with ValueError and
OperationsRecordError: twelve cases requiring RunLifecycleConflict and exact
message equality. Each supplied error contains a boundary-specific canary.
safe_error requires absent cause/context, combined str/repr length at most 512
and absence of that canary. The source raises its translated errors outside the
handlers, preserving these categorical failures without retained decoder chains.
This is local expected-error redaction, not a universal exception/log guarantee.

The missing-row test injects KeyError at five boundaries, excluding action lookup,
and requires RunLifecycleNotFound. Unlike the decoder-category test, it does not
assert exact message text or call safe_error for these cases. The actual
[history store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/activity_history.py)
returns None when an idempotent action is absent; that permits first execution.
The test does not inject KeyError into that optional lookup or prove it would
be translated to NotFound. Nor does it delete rows to test actual selector behavior.

The unexpected-error test crosses all six boundaries with TypeError and RuntimeError:
twelve cases requiring the same exception class and exact injected object identity.
It intentionally preserves these unexpected errors rather than converting them
to history conflicts. It does not assert their text is bounded/redacted or test
every database-driver exception class. Together the three methods specify 29
selected injected cases, not a general catch-all exception taxonomy.

_capture_store_failure resets coherent retry truth and snapshots it before
patching. It replaces the selected class method and separately forbids
observe_request_lease_for_update, executes a normal retry command, restores both
methods in finally, and compares the snapshot before returning the captured error.
Unpatched reads and the UoW still use real PostgreSQL, so preceding lookup/locking
work may occur before the injected failure. No existing retry action is seeded;
these are first-execution reads, not six separately exercised historical replay
failure stages.

The observation sentinel proves the failure precedes lease-time observation.
The fixture supplies four IDs named unused-a through unused-d, but this helper
does not instrument their call count or use an ID-factory failure sentinel.
Their labels alone are not evidence of zero allocation. The actual interpreter
places these six reads before result planning and writes; this test's independent
assertions are the error, forbidden observation and selected snapshot equality.

The actual [base-fixture snapshot](../../../../control-plane-kit-operations/tests/execution_lease_recovery_fixture.py)
covers request status/claim fields, events for the request's runs, session actions
and selected run identity/attempt/status/timing fields. It omits some columns and
other tables. These assertions preserve the represented truth without proving a
complete database diff or rollback after a successful insert. The neighboring
eligibility/rollback tests own failures after real retry writes; this file's
selected failures occur before those writes in the actual source.

The fixture constructs operator authority, approval and failed-run history with
synthetic future claim times. Its inherited schema installation and workspace
TRUNCATE operate on the supplied test database URL; this file supplies no separate
database-ownership guard. No caller authentication, provider execution, ambiguous
commit handling, automatic retry, compensation or live-resource cleanup is tested.
Neither the fixture values nor this companion authorize those operations.

Read depth: full 135-line source, all three methods and shared capture helper,
full 218-line retry fixture, retained full 466-line interpreter and actual UoW,
with selected base-fixture setup/snapshot and actual history optional-action lookup.
No source/pin changes, executable tests, database setup, credentials/private-key
access, provider/runtime actions or publication occurred. Documentation adds no
security surface and makes no claim that this suite ran or passed during authoring.
