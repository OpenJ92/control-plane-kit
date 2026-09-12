Source: [control-plane-kit-operations/tests/test_postgres_execution_lease_recovery_scoped_run.py](../../../../control-plane-kit-operations/tests/test_postgres_execution_lease_recovery_scoped_run.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These five PostgreSQL tests protect request-scoped retained-run selection and
selected read-error boundaries of the
[recovery interpreter](../src/control_plane_kit_operations/execution_lease_recovery_interpreter.py.md).
They exercise a direct store miss and service replay/fresh paths, not HTTP/MCP,
credential authentication, provider effects or a full recovery race matrix.

The inherited [base fixture](../../../../control-plane-kit-operations/tests/execution_lease_recovery_fixture.py)
requires CPK_OPERATIONS_TEST_DATABASE_URL, connects with autocommit, installs the
schema and truncates cpk_workspaces CASCADE during setup/reset/teardown. It trusts
the supplied URL; the test file does not create a unique database or discover a
safe one. A service UoW uses a separate connection. Seeded plans, approvals, leases
and history are synthetic durable fixtures, not observations of running workers.
This companion describes the tests; it does not authorize that destructive setup.

seed_foreign_run copies plan-a into plan-b and request-a into request-b through
direct INSERT...SELECT, substituting selected IDs/key but retaining the other
columns. It then adds a typed CLAIMED run-b admitted to request-b in a separate
UoW. This is deliberately constructed foreign-request truth, not a public plan/
approval/admission workflow for a second independently authorized deployment.

The direct-selector test asks for request-a/run-b and requires KeyError. While
the selector transaction remains open, another connection must acquire run-b
FOR UPDATE NOWAIT and return its ID. That probe occurs after the failed selector,
before either transaction rollback, so it tests that this mismatched lookup did
not retain a conflicting row lock on the foreign run. Both connections close in
finally. The actual [store query](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
filters request_id and run_id together before FOR UPDATE. The test covers one
foreign pairing, not every identity boundary, lock type or concurrent writer.

The missing-selector replay test first commits a real active renewal, then replaces
get_run_for_request_for_update with a KeyError injection. Replay must return
RunLifecycleNotFound with exactly recovery retained run was not found. Clock and
ID factories are failing sentinels; the selected fixture snapshot must not change.
This proves service translation at that read boundary, not a real missing-row race.

The next test injects OperationsRecordError and RuntimeError at the same replay
selector. The former becomes RunLifecycleConflict with exactly recovery retained
run history is invalid; the latter must escape as the identical exception object.
Expected categorical errors are bounded, chain-free and exclude the supplied
canary. Unexpected-error identity is intentional, not a promise that all exceptions
are sanitized. Both paths forbid lease observation/ID allocation and compare the
selected snapshot. The injections replace the read method before its original
SQL/decoder executes; they are not malformed database-row reproductions.

The decoder-boundary table covers six store methods: optional action lookup,
locked session, request locator, locked request, latest run, and request-scoped
retained run. Action lookup and scoped retained run are exercised after a committed
renewal; the other four use fresh execution. Each receives ValueError, TypeError
and RuntimeError, with one additional OperationsRecordError at action lookup:
19 configured cases, not an exhaustive decoder/type cross-product.

For those cases, ValueError and the added record error require the exact fixed
Conflict message associated with their boundary. TypeError/RuntimeError must
escape unchanged. The helper installs a no-clock sentinel and a failing ID factory,
restores both patched methods in finally, and compares the before/after snapshot.
Actual interpreter read helpers raise translated errors outside their handlers;
optional action lookup does not translate KeyError. This table does not test every
missing-key branch, event/approval read, real decoder failure or write-stage rollback.

The final public-service replay test creates a valid renewal, seeds run-b, then
submits a same-key command naming that foreign run. An action-lookup wrapper changes
the returned action's retained-run payload, nested recovery coordinate and intent
fingerprint to match the foreign command. It does not persist this altered action.
This deliberately bypasses ordinary changed-intent rejection to reach the scoped
selector, rather than proving an attacker can create that durable action through
the public command surface.

Its selector wrapper records exactly (request-a, run-b) and probes run-b with NOWAIT
before calling the original selector. The service must then return the exact fixed
NotFound, without clock/IDs or selected snapshot changes. This probe demonstrates
the foreign row was free on entry, not by itself that the selector left it free;
the earlier direct-selector test supplies the stronger after-call evidence. Public
here means the service execute interface, not a remote authenticated transport.

safe_error checks absent cause/context, combined str/repr length at most512 and
absence of supplied canaries. The inherited snapshot records request-a claim/status,
events for its runs, selected session-a action columns, and selected request-a run
columns. It omits other columns/tables and foreign request-b/run-b state; equality
is not a complete database or foreign-resource immutability proof. Class-method
injections are process-global during each test and restored afterward. NOWAIT makes
the specified row-lock probe immediate; connections and other SQL have no explicit
whole-test timeout here.

Read depth: full453-line source, all five methods and helpers, retained full572
base fixture with fresh setup/snapshot reads, selected actual interpreter entry,
replay and read-error helpers, scoped selector SQL and action-lookup SQL. Existing
pure recovery/support/UoW context was retained; no codec/store suite or whole
concurrency-suite review is claimed. Local links, whitespace and source guards
were checked. No executable tests/imports, database setup, credentials, provider/
runtime actions, source changes or publication occurred during authoring. This
documentation adds no security surface or authority to recover live work.
