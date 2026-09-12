Source: [control-plane-kit-operations/tests/test_postgres_execution_lease_recovery_store.py](../../../../control-plane-kit-operations/tests/test_postgres_execution_lease_recovery_store.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These nine tests exercise the actual PostgresExecutionStore with small connection
doubles. Despite the filename, they open no PostgreSQL connection, install no schema
and perform no real SQL mutation, lock or transaction. Their boundary is input
admission before execute, selected SQL/parameter shape and missing/error outcomes.
Real request-scoped locking evidence belongs to the
[scoped-run tests](test_postgres_execution_lease_recovery_scoped_run.py.md).

_NoSqlConnection records any execute call and immediately raises AssertionError;
negative tests require OperationsRecordError and an empty calls list. Thus reaching
SQL cannot silently satisfy the expected rejection. _RecordingConnection records
calls and returns a cursor whose fetchone is always None. _FailingConnection raises
the exact supplied error. None of these doubles parses SQL, returns a durable row,
simulates a PostgreSQL transaction or proves a statement is accepted by a server.

require_store_methods checks presence of latest-run selection, claim rotation and
abandonment; require_scoped_selector checks the request/run selector separately.
They check attribute availability, not full signatures, root-export identity or
every member of the store interface. _TextSubclass and _FenceSubclass are deliberate
nominal-boundary inputs, not alternate implementations of production validators.

The scoped-selector negative table contains six invalid request IDs and 45 invalid
run IDs, each paired with a valid other coordinate. Run cases include objects/bool,
text subclass, empty/space, leading punctuation, slash/space, all control codepoints
0..31 plus127, and length201. Request cases include object/bool/subclass, empty,
newline and length513. These 51 configured rows require no execute calls, a bounded
chain-free error and absence of their supplied canaries. They do not exhaust every
Unicode value or cross every invalid request with every invalid run.

Four positive boundary pairs cross request lengths1/512 with run lengths1/200.
Each must reach exactly one execute and receive KeyError from the empty cursor.
This proves those inputs pass local admission; it does not retrieve an existing
record or validate a decoded row. The actual
[store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
uses different contracts: request identity is bounded exact text, while run identity
delegates to the Core RunId grammar. Their shared string representation does not
make their accepted languages identical.

The fixed-miss test uses candidate-bearing valid IDs, requires the exact KeyError
string including its normal quotes, and checks neither candidate appears in the
bounded chain-free rendering. It additionally normalizes whitespace in the recorded
query and requires the substring WHERE request_id = %s AND run_id = %s FOR UPDATE,
with the exact two-parameter tuple. This protects the scoped predicate/parameter
handoff for this statement, not a complete query equality or query-plan/lock proof.
A separate valid-input case requires an injected RuntimeError from execute to
escape as the identical object, intentionally outside categorical-error redaction.

Five latest-run negatives, 15 rotation negatives and nine abandonment negatives
also require rejection before execute. Rotation covers request inputs, exact fence
wrapper types, generation jump/exhaustion, observation type/text and bool/zero/3601
duration. Abandonment covers request, fence subclass and observation type/text.
These are selected independent inputs, not every field crossed with every command.
They do not establish recursive exact typing inside admitted fence objects or
exhaust worker changes, raw fence shapes or all canonical timestamp forms.

Actual request admission rejects non-exact strings, empty/over512 text and characters
below32. Run admission delegates to
[Core RunId](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/run_identity.py).
The rotation fence-pair helper requires exact fence wrappers and a one-generation
increment below exhaustion; it does not decide whether the operation is same-worker
renewal or different-worker takeover. That decision belongs to the command owner.
Observation admission uses the actual canonical UTC
[timestamp codec](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/temporal.py),
and duration requires exact int in1..3600. The timestamp negatives here are only
object, subclass and an invalid string, not the temporal owner's full test matrix.

The two duration endpoint cases pass1 and3600 to rotation, require one execute and
expect None from the empty cursor. They do not assert the exact bound parameter,
updated expiry, successful compare-and-set or returned request decoding. Likewise,
the maximum-length latest-run case requires one execute and a None outcome after
locally converting KeyError to None; it does not assert exact missing-row text or
the latest-run ordering query. No positive abandonment persistence is exercised.

The real rotation SQL conditionally matches claimed status and expected fence;
abandonment additionally matches expiry against supplied observation. This file's
connection doubles do not establish those predicates' behavior against data,
concurrent fencing, commit/rollback, authority freshness or no-provider-side-effects.
The [interpreter](../src/control_plane_kit_operations/execution_lease_recovery_interpreter.py.md)
and its PostgreSQL suites own those distinct orchestration/transaction claims.

_safe_error requires absent cause/context, combined str/repr length at most512 and
absence of the supplied strings. _candidate_canaries selects subclass text, a
generic canary marker or the first32 characters of over200 text; it is not a general
secret detector. The dedicated run table supplies its own canaries. Expected
rejections are tested as candidate-free at those selected boundaries; arbitrary
driver errors are not universally sanitized.

Read depth: full314 source, all nine methods and helpers; selected actual latest/
scoped selectors, claim-update SQL and recovery input helpers, full fence and Core
RunId values and temporal codecs, with retained interpreter context. No full
execution-store/codec suite or live PostgreSQL evidence is claimed. Local links,
whitespace and source guards were checked. No executable tests/imports, database
setup, credentials, provider/runtime work, source changes, staging or publication
occurred during authoring. This note adds no security surface or mutation authority.
