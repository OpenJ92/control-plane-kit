Source: [control-plane-kit-operations/tests/test_postgres_effect_attempt_store_contract.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_store_contract.py).
Maintain this document alongside its source file. Recheck the actual store,
record constructors and fixture when changing these assertions or their imports.

This 444-line suite has eight tests for the private effect-attempt store contract.
It uses connection doubles, reflected Python signatures and source AST checks.
No test here connects to PostgreSQL, installs a schema, commits a transaction,
restarts a process or calls a runtime provider. Importing psycopg's UniqueViolation
supplies an exception class, not database evidence.

The module loader tolerates ModuleNotFoundError only when its name exactly matches
the target store module; missing transitive dependencies propagate. The optional
store symbol is captured at module import and require_store asserts its presence.
The Operations bundle and record fixture are unconditional imports. This is not a
general dependency-skipping mechanism or an isolated importability test.

The shared [record fixture](effect_attempt_record_fixture.py.md) supplies typed
synthetic states, events and commitments. Its states are arranged directly, not
produced by durable execution or recovery. The local _HostileIdentity and
_HostileRecord are empty nominal subclasses, not hostile-dispatch spies. Candidate
construction uses ordinary constructors and dataclasses.replace; no exact-type
objects are forged by bypassing their constructors.

_NoSqlConnection records an execute call and then raises. Its empty calls list
therefore provides a direct SQL-boundary assertion for the selected invalid
inputs. _RecordingConnection records queries/parameters and always returns a
cursor whose fetchone is None. _FailingConnection raises the supplied exception
object from execute. None of these doubles interprets SQL, locks a row, simulates
transaction isolation or decodes a returned database row.

The surface test requires exactly four directly declared public callable names:
get, get_for_update, insert_absent and compare_and_set. It compares parameter-name
tuples, not annotations, defaults or parameter kinds. It requires an effect_attempts
dataclass field on PostgresStoreBundle, constructs the bundle with a recording
connection and checks the exact store type. A missing get then produces one SQL
call. The two package roots must not expose EffectAttemptStore as an attribute;
the module itself remains importable and exports the class. The actual
[bundle](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/stores.py)
constructs EffectAttemptStore with its supplied connection.

That test also requires CPK_PACKAGE_MODULE_INVENTORY and reads the named JSON
file. It checks one matching module row, owner "operation", destination, source
path and the exact ordered three protecting-test paths. It does not assert every
inventory field, canonical export or dependency list. The inspected repository
inventory has the matching row; the test's environment-selected input remains
part of its execution context.

Read admission tests object() and an identity subclass against both get variants.
Eight mutation cases cover object/subclass insertion, invalid current and
replacement outer values, changed identity, changed fence, changed original event
and a decreasing latest ordinal. The changed-fence record has fresh matching
commitments. The regression record's latest ordinal 15 exceeds its own original
3, but is below the current record's latest 20: the failure belongs to replacement
admission, not ordinary record ordering. Each case requires the exact fixed input
error and no SQL calls. The inherited error helper checks absent cause/context,
combined str/repr length at most 256 and absence of the supplied canaries. These
checks do not sanitize arbitrary SQL errors or test-runner diagnostics.

The actual [store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_attempt_store.py)
checks exact outer identity/record types. Replacement admission additionally
preserves identity, request fingerprint, fence, prior attempt and original event,
and forbids a lower latest ordinal. These tests do not independently vary request
fingerprint or prior attempt while retaining identity, nor test equal replacement
ordinals or every nested malformed value. Outer type checks do not reconstruct
the whole record or prove resistance to raw exact-type forgeries.

The complete-prior test arranges an uncertain second attempt and a recovered
success replacement. It requires one recorded UPDATE and a None return from the
empty cursor. Its SQL-text assertions look for eighteen named nonidentity columns
using IS NOT DISTINCT FROM and three identity equality predicates. This protects
the listed query fragments. It does not compare the complete SQL statement,
parameter order/values, SET assignments, predicate multiplicity or actual
concurrent compare-and-set behavior. The inspected store generates those eighteen
predicates from its 21-column representation and binds replacement values,
identity and prior values; that source reading is separate from test assertions.

The read-miss test checks both get methods for the fixed KeyError text, bounded
candidate-free rendering, one SQL call, the identity predicate, exact parameters
("run-a", "activity-a", 1) and presence/absence of FOR UPDATE. The double cannot
prove that PostgreSQL acquires or retains a lock. Successful reads and row
reconstruction are outside this file's exercised paths.

The insert test checks None for an empty RETURNING result and the explicit
ON CONFLICT (run_id, activity_id, attempt) DO NOTHING query fragment. A supplied
UniqueViolation escapes with object identity. It does not create an actual
primary-key or event-role collision, inspect a database constraint name, or cover
the successful insert acknowledgement. The unexpected-error test similarly
requires the same RuntimeError object to escape all four methods when execute
raises. These paths intentionally preserve provider/SQL exception identity;
they do not establish universally redacted errors.

The final AST test prohibits selected attribute-call names (including commit,
rollback, event/ID allocation and transaction), selected exact import names, and
selected identifier/attribute/imported names for transition and UoW authority.
Despite the variable name forbidden_import_roots, its import comparison is exact
set intersection, not prefix matching or transitive import analysis. It does not
resolve aliases, indirect calls or string-based dispatch, and attribute-call
inspection does not cover every possible bare function call. These are bounded
lexical ownership checks, not a proof that arbitrary implementations are pure.

The [record contract](../src/control_plane_kit_operations/effect_attempts.py.md)
owns typed state/event coherence; the store owns PostgreSQL representation and
caller-transactional writes. This suite leaves real constraints, row decoding,
locking, durability, rollback and concurrency to the separate store/schema suites
and their owning integration contexts. It should not grow a fake database or a
second implementation of Core transitions to claim that evidence here.

Read depth: all 444 source lines, eight tests and local loader/doubles/helpers;
the full 280-line store; retained full record fixture and record owner; selected
bundle wiring, package exports and actual inventory row. Adjacent live store and
schema suites were not reviewed for this note. Static validation covers local
links, whitespace and selected frozen-source consistency. No application imports,
executable tests, database/provider calls, source changes or credential access
were performed. This companion changes no authorization, transaction, network or
history behavior and grants no authority to execute the documented operations.
