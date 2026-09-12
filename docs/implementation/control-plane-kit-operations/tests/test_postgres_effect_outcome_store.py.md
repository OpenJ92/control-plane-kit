Source: [control-plane-kit-operations/tests/test_postgres_effect_outcome_store.py](../../../../control-plane-kit-operations/tests/test_postgres_effect_outcome_store.py).
Maintain this document alongside its source and recheck actual store, fixture,
outcome, intent, observation and schema contracts when those assumptions change.

This 824-line suite has 14 tests of direct effect-outcome persistence and decoding
using actual PostgreSQL stores. It tests synthetic typed results and observations,
not provider execution or live inspection. It distinguishes a retained direct
outcome from later current-attempt recovery. Test names mentioning restart refer
to fresh unit-of-work connections, not a database, process or provider restart.
This companion records source inspection; no imports, SQL or tests were run.

The class inherits
[PostgresEffectOutcomeStoreFixture](postgres_effect_outcome_store_fixture.py.md),
which uses the established database URL/schema and destructive workspace fixture
reset, seeds a leased run and supplies real Postgres units of work. Setup and
teardown reset through the inherited fixture. They belong only to the isolated
owning Operations Docker apparatus. Ordinary test mutation queries use the setup
connection in autocommit mode, so mutations are committed rather than automatically
rolled back with a later read unit of work.

The fixture constructs exact outcome records from its ten stories in both normal
and compensation phases. persist_prerequisites commits events, retained intent,
attempt and observations. persist_outcome then inserts the outcome in a separate
unit of work and requests commit. Those phases are not one atomic operation; prior
committed truth can remain after outcome insertion fails. The actual
[unit of work](../src/control_plane_kit_operations/postgres/unit_of_work.py.md)
physically commits on successful exit when requested, otherwise rolls back and closes.
The synthetic context does not demonstrate a fully executed deployment history.

The HTTP test constructs a product with one authored HttpCheck, path / and expected
digest d repeated 64 times. It builds a correlated intent and synthetic PASSED
completion with status 200, response size 21 and body-match True. No response body
is read or hashed here. The
[intent-aware projection](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_outcome_evidence.py)
produces the verification observation from that authored check.

Replacing only the observation's path with /safe-but-foreign still produces a
constructible standalone record. Inserting it after its prerequisites must raise
the fixed store input error: the actual store recomputes HTTP membership from the
retained intent. The zero-count assertions in that block query self.connection,
not the active writer's connection. They cannot see uncommitted writes from the
writer and therefore do not independently prove that it issued no INSERT. The
block requests no commit and rolls back. The subsequent lawful case inserts all
prerequisites and outcome in one committed unit of work, then requires exact fresh
readback and an inner preimage within 8192 bytes.

The predecessor test checks workspace-a, request-a and run-a exist in the seeded
database, persists prerequisites and reloads the exact attempt. It counts matching
observation IDs. This proves selected seed availability, not full graph/plan/activity
semantic alignment. The twenty-story test resets truth for each case, persists the
record, requires its exact type/value through a fresh get and compares the stored
preimage bytes with the fixture's RFC8785 encoding. Real Core constructors and
fingerprints are shared with the expected-value fixture, not an independent oracle.

The historical-recovery test changes an uncertain result's raw failure details,
recomputes its direct attempt and confirms the fingerprint changed. After persisting
that outcome, a fixture helper changes the current attempt through a recovery CAS.
A fresh outcome read must retain the original details/fingerprint, uncertain status
and absence of recovery_decision. This demonstrates separate historical snapshot
storage, not authorization of that recovery or redispatch to a provider.

The attempt-two test explicitly inserts the preceding started attempt, both required
intents, direct events, current attempt, observations and outcome in one unit of work.
Fresh readback must have attempt=2 and the supplied immediate-prior identity. It
does not issue a retry command or prove that external retry was safe or performed.

Ordered-membership inspection queries position, observation ID and count and requires
exact equality with the record tuple. Deleting a linked observation must fail with
the named membership observation foreign key. A separately inserted unrelated
observation is deletable. These are database retention constraints, not a general
cleanup policy or live-resource ownership check.

The first corruption matrix has six committed mutations, resetting and persisting
before each: a missing membership, exchanged observation IDs, invalid preimage,
changed linked observation evidence, changed original-event state fingerprint and
changed direct-event state fingerprint. Each fresh get must raise the exact row-invalid
OperationsRecordError and pass bounded, cause/context-free canary checks inherited
from the fixture. A seventh extra-membership scenario inserts a third observation,
replaces memberships and changes the count consistently to three while leaving the
two-observation outcome unchanged; decoding must still reject the mismatch.

The strict-codec test has eight mutations: invalid UTF-8, duplicate JSON member,
NaN, a nonobject root, noncanonical whitespace, profile mismatch, changed request
fingerprint and changed outcome fingerprint. A synthetic result with finite 1.5
evidence is persisted first, and its preimage supplies the NaN replacement. This
contrasts finite JSON with the forbidden constant; it is not an exhaustive numeric
or JSON grammar matrix. Each mutated fresh get must be categorical and omit the
selected raw canaries.

The selected actual
[store](../src/control_plane_kit_operations/postgres/effect_outcome_store.py.md)
requires canonical inner RFC8785 bytes, reconstructs Core values, loads both events,
checks event triples and validates ordered observations through the outcome record.
Its SQL uses CASE guards on selected preimage and observation-evidence fields, and
membership retrieval is bounded by validated count+1. Schema foreign keys alone
would not establish all those semantic relationships.

The oversize test has four boundaries. For preimage get and the private current-row
validator, it opens a separate connection, drops the preimage length constraint and
writes 8193 bytes inside that connection's transaction. Both reads must raise the
fixed row error while the transport recorder remains empty. finally rolls back the
DDL/data and closes that connection. This is fault injection in test apparatus,
not a schema migration or production repair path.

For linked observations, it instead commits oversized JSON evidence through the
setup connection. get must reject it, and install_schema must raise the fixed
operations schema reset is required error. Neither operation repairs the row.
These selected cases exercise ordinary decoding and current-schema admission.
They do not prove every current-schema check or all oversized-field paths.

_TransportConnection delegates to the real connection and wraps fetched cursors.
For queries whose text contains one of the two outcome relation names, its recorder
flags top-level exact bytes longer than 8192 or exact dicts whose ASCII escaped,
sorted compact JSON exceeds 8192 bytes. It inspects fetchone/fetchall results, not
the PostgreSQL wire stream, all fields recursively, decoder allocations or database
work. Its JSON encoding is not PostgreSQL jsonb::text or the Core RFC8785 profile.
An empty oversized list proves only the selected fetched-value witness.

The deep-JSON case puts 1100 nested arrays in the evidence slot while retaining an
overall preimage no larger than 8192 bytes. It requires the fixed row error instead
of exposing RecursionError. The nested token passed to the safe-error checker is
not itself part of that numeric payload, so that canary alone adds little; the
fixed bounded error and control-flow type are the substantive assertions.

For unexpected codec faults, the test temporarily replaces _decode_preimage with a
function raising a supplied TypeError or RuntimeError, restores the original in
finally and requires exact exception-object identity. This protects the distinction
between recognized malformed data and unexpected implementation faults. It does not
authorize exposing those raw exceptions through public routes or logs.

Observation uniqueness is tested across two distinct attempts sharing exactly the
same observation records. The second outcome insert must hit the named relation-wide
observation-key UniqueViolation; the unit of work is not committed. After resetting,
two equivalent indexed outcomes with distinct observation IDs both persist. This is
a sequential constraint witness, not concurrent insertion or a general uniqueness
proof across every history shape.

The exact-8192 test constructs an inner preimage of precisely that size and compares
persist_outcome's returned record to the input. Despite the name roundtrips, this
specific branch does not call get afterward; the store returns the input rather
than a readback. Other tests perform actual fresh reads. After reset, a standalone
foreign-workspace outcome with no observations has its attempt prerequisites committed
but its insertion rejected by derived request/workspace ownership. The outcome table
must remain empty and the error must omit the foreign workspace canary.

The final test inserts an already persisted record again and requires raw
UniqueViolation, not an idempotent success. It then resets, commits prerequisites
and inserts an outcome without requesting commit. Outcome and membership counts
must both remain zero after exit. Earlier prerequisites are not asserted absent.
This tests ordinary rollback, not lost commit acknowledgement or resolution of an
ambiguous commit. There is no injected concurrency, deadlock, network loss or process
termination in this suite.

The [outcome contract tests](test_postgres_effect_outcome_store_contract.py.md)
separately own the private surface, before-SQL invalid input and selected query-token
laws. The [schema suite source](../../../../control-plane-kit-operations/tests/test_postgres_effect_outcome_schema.py)
is a separate owner; it was not audited in full for this note. This suite does not
establish authentication, HTTP/MCP parity, secret custody, provider health, cleanup,
recovery eligibility or current fence admission. Private failure details and endpoint
material may be retained by the permitted outcome language; bounded errors are not
universal raw-preimage redaction.

Read depth: fresh full 824-line test, full 742-line store and 382-line contract suite,
full 297-line PostgreSQL fixture and 736-line pure fixture/378-line parent retained
from adjacent companions, full unit of work, and selected actual outcome/HTTP-intent,
Core verification/fingerprint, event/observation and schema-validation contracts.
No full transitive dependency audit is claimed. This note changes no source, test,
data, schema or execution policy; validation was documentation links, whitespace
and frozen-source comparison only.
