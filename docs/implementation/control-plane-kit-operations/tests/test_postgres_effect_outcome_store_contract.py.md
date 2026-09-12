Source: [control-plane-kit-operations/tests/test_postgres_effect_outcome_store_contract.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_outcome_store_contract.py).
Maintain this document alongside its source and recheck the actual store, fixture
and inventory contracts when its assertions change.

This 382-line suite contains seven contract tests for the private PostgreSQL outcome
store. It exercises real constructors and store methods with no-SQL, recording and
failing connection doubles. It does not open PostgreSQL, persist an outcome, perform
schema installation or measure query work. Those responsibilities have separate
PostgreSQL and schema suites. Nothing was imported or executed for this companion.

The test class inherits the pure
[EffectOutcomeEvidenceFixture](effect_outcome_evidence_fixture.py.md) and unittest,
not the PostgreSQL fixture class. It nevertheless imports module-loading helpers
from [postgres_effect_outcome_store_fixture.py](postgres_effect_outcome_store_fixture.py.md),
so importing the test still requires that module's dependency graph. The loader
converts ModuleNotFoundError to None only when its name is the target store module;
missing transitive dependencies escape. require_store makes a missing target class
an assertion, not a skipped test. This does not authorize a host-runtime fallback;
executable validation belongs to the established Operations Docker suite.

_NoSqlConnection appends every execute argument tuple then raises AssertionError.
Invalid-input cases must instead raise the fixed OperationsRecordError and leave
the call list empty, so reaching SQL cannot satisfy those cases. _RecordingConnection
records statements and returns one empty cursor: fetchone is None and fetchall is
an empty tuple. _FailingConnection raises the supplied exception object immediately.
None implements commits, transaction isolation, schema constraints or a SQL engine.

record_for constructs the real EffectAttemptOutcomeRecord from a synthetic story,
the fixture's outcome constructor and its expected observation projection. The first
test requires exactly twenty stories: ten named execution/observation variants in
both normal and compensation phases. Each record must be exact type and have no
recovery decision. This is coverage of the fixture's direct-value catalogue, not
twenty successful database roundtrips or proof of every possible outcome value.

The surface test enumerates public callable names defined directly on the store
class and requires only insert and get. inspect.signature checks parameter names
exactly, not all annotations, parameter kinds or behavioral semantics. It requires
effect_outcomes in the bundle's dataclass fields and its constructed value to be
exactly EffectAttemptOutcomeStore. A lookup against the empty connection must issue
one query and raise the fixed missing-outcome KeyError. The Operations and postgres
package roots must not expose the class. This protects a selected internal surface;
it is not a ban on all private helpers or proof of constructor purity in general.

The invalid-input test has twelve cases. Five insertion cases pass an arbitrary
object, a record subclass, an exact record with a str-subclass workspace, a missing
workspace field, or a nested forged attempt identity. Seven lookup cases pass an
arbitrary object, an exact forged identity, an identity missing activity_id, an
identity subclass, or an empty, 513-character or control-bearing event ID. forge_exact
uses object.__new__ and object.__setattr__ to bypass constructors; HostileStr is a
plain str subclass, not an adversarial callback implementation.

Each case requires the exact input-invalid message and the inherited safe-error
checks, with selected event/long-string canaries absent, then asserts no execute
calls. These are finite exact-type/reconstruction boundaries. They do not exercise
every nested field, hostile attribute hook, invalid Unicode value or maximal valid
identity. The actual [store](../src/control_plane_kit_operations/postgres/effect_outcome_store.py.md)
reconstructs identity and record inputs, rejects selected invalid values before SQL,
and separately checks event UTF-8 encoding. Do not infer untested boundary cases
solely from this table.

The lookup test captures one empty-result query, collapses its whitespace and
requires four predicate substrings: run_id, activity_id, attempt and direct_event_id
each compared with a placeholder. Parameters must be exactly the fixture's
run-a/activity-a/1/event-direct tuple. FOR UPDATE must not occur. Despite the test
name hard_bounded, this method does not inspect returned rows, database constraints,
query plans, latency or resource bounds. The current actual SELECT has those exact
identity predicates, but substring assertions alone are not a SQL equivalence proof.

The read-bound test captures the empty get query, invokes the private current-row
validator against an empty page, and directly reads _MEMBERSHIP_QUERY. For each of
get-preimage, current-preimage and membership-evidence, it asserts the strings
octet_length, the selected field name and 8192 occur. It does not assert the CASE
branch, predicate operators or LIMIT relationship and transfers no oversized value
through a driver. The inspected actual owner uses CASE to replace out-of-bound
selected preimage/evidence with NULL and validates membership count+1 on nonempty
reads. The separate PostgreSQL suite owns selected real transport/decoder witnesses.

The miss/error test requires the same fixed KeyError, then safe-error checks omitting
the requested run, activity and event canaries. For TypeError and RuntimeError from
the failing driver double, it requires the caught exception to be the exact supplied
object. That deliberately preserves unexpected faults rather than laundering every
exception into a successful miss or categorical input error. It is not evidence
that raw driver exceptions are safe for public responses or arbitrary logging.

The inventory test requires CPK_PACKAGE_MODULE_INVENTORY and reads that JSON file.
There must be one matching module row, owner operation, destination equal to the
module name, exact source path and three ordered protecting-test paths. It compares
the ordered internal_dependencies tuple, forbids a generic dependencies key and
requires optional_external_dependencies=(rfc8785,). It does not enumerate imports
from the Python source or validate every inventory row.

That distinction matters at the inspected version: the store actually imports Core
verification values and EffectAttemptIntentStore for HTTP completion association,
but those two modules are absent from this test's asserted dependency tuple. The
assertion records its declared inventory expectation, not an exhaustive statement
of actual dependency truth. The owner and its relevant imports must still be read;
this documentation does not amend source, inventory policy or the test expectation.

The [outcome owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_outcome_evidence.py)
validates direct state/event/outcome and projected observations. The
[attempt owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempts.py)
and Core identity/state constructors supply nested admission. The
[bundle](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/stores.py)
wires stores to one connection. Reading those actual dependencies explains why the
forged values fail; the tests do not independently implement their full languages.
The fixture uses real Core fingerprints, so its expected values are not a separate
cryptographic oracle.

No positive insertion through a database, ordered membership readback, HTTP-intent
join, current-row drift on a nonempty page, recovery snapshot, cross-workspace
constraint, duplicate insert, rollback, concurrency or restart is exercised here.
See the [PostgreSQL suite source](../../../../control-plane-kit-operations/tests/test_postgres_effect_outcome_store.py)
for its distinct finite persistence evidence. No provider mutation, authorization,
secret resolution, cleanup or replay policy is established by these seven tests.

Read depth: full 382-line test freshly reread; full 742-line store, 824-line PostgreSQL
suite, 297-line PostgreSQL fixture, 736-line pure fixture and parent fixture context
retained from the preceding source/fixture companions. Module-loader, forging and
bundle/event/current-validation paths were rechecked, with selected actual Core,
outcome/attempt/intent and schema contracts. This is not a full transitive dependency
audit. Validation for this note is links, whitespace and frozen-source comparison
only; no application imports, tests, database queries or provider actions were run.
