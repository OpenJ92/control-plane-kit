Source: [control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_outcome_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_outcome_store.py).
Maintain this document alongside its source. Recheck the outcome language, event,
intent, observation and schema contracts when this representation changes.

This 742-line owner stores an exact historical direct effect-attempt outcome. Its
public class exposes insert(record) and get(identity, transition_event_id). It does
not execute an effect, observe a provider, fold a recovery decision or return the
current attempt in place of the retained direct snapshot. The
[store bundle](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/stores.py)
constructs it as effect_outcomes on the caller's connection. The module exports
EffectAttemptOutcomeStore, but the package and postgres roots do not reexport it.

The store owns no commit, rollback, connection close, retry or lock acquisition.
An insertion has multiple statements; callers must supply the transaction that
makes them atomic. The normal [unit of work](unit_of_work.py.md) requests commit
explicitly and physically commits on successful context exit. Autocommit use would
not turn the sequence into one transaction. insert returns the original input
record, not a database readback or proof that a later commit succeeded.

The [outcome language](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_outcome_evidence.py)
owns the two profiles, exact typed outcome, direct attempt/event congruence and
ordered observation projection. This store represents that aggregate rather than
defining a second effect state machine. Its 22 outcome columns retain identity,
workspace/request, profile and canonical preimage, request/outcome fingerprints,
fence, status, optional prior identity, original/direct event triples and observation
count. Membership rows retain position, count, workspace and observation identity;
the observation bodies remain in cpk_observations.

insert first requires an exact EffectAttemptOutcomeRecord, extracts its parts,
reconstructs it through the actual constructor and encodes the inner descriptor.
Missing attributes and selected constructor/encoding failures become the fixed
input-invalid OperationsRecordError. Encoded preimage length must be 1..8192 bytes.
This is not merely an isinstance check on an allegedly valid record. It also is
not a universal exception sanitizer for arbitrary hostile Python behavior.

The first SQL read joins the run to its execution request. The supplied workspace
must equal the derived request workspace, and request_id is taken from that join,
not supplied as an independent record field. For verification completions, a second
validation reads the actual retained attempt intent and recomputes the observation
projection. Only then does the store insert the outcome and each membership in
tuple order. It does not insert the run, request, attempt, events or observations;
those prerequisites must already exist in the caller's transaction or database.
There is no ON CONFLICT replay path. Database uniqueness and other driver failures
are not converted into successful or idempotent insertion.

get requires an exact EffectAttemptIdentity and reconstructs its nested RunId and
fields. The transition event ID must be an exact nonempty string of at most 512
characters, without characters below U+0020 and with valid UTF-8 encoding. The
SELECT predicates are exact run/activity/attempt/direct-event identity. There is
no FOR UPDATE and no workspace/principal argument: this internal lookup is not a
tenant authorization boundary. A missing outcome produces a fixed KeyError without
embedding requested identifiers.

The outcome SELECT replaces preimages outside 1..8192 octets with NULL in SQL. The
membership SELECT similarly replaces observation.evidence outside 1..8192 octets
of its PostgreSQL text representation with NULL. Invalid values consequently fail
decoding rather than being returned in those selected fields. These are selected
transport guards, not a cap on total query work, all row fields or every wire byte.
The observation language's own evidence bound uses a different representation and
must not be equated with this 8 KiB transport check.

Before querying membership, the store checks the outcome row is an exact tuple/list
of 22 elements and observation_count is an exact int in 0..8192, excluding bool.
Membership is selected through an observation-ID/workspace join, ordered by position
and limited to count+1. Decoding requires exactly count rows, each with 13 elements,
the expected zero-based position and the same count. The position/count comparisons
use equality; they are not independent exact-type checks on every scalar. Each
linked observation is reconstructed by the actual
[observation decoder](observed_state.py.md). Missing, extra or reordered members
cannot silently become a shorter successful aggregate.

The inner preimage decoder requires exact bytes, UTF-8, an object root, unique JSON
members and no nonfinite JSON constants. It reencodes through rfc8785 and requires
byte equality before selecting the profile. Thus semantically equivalent but
noncanonical JSON is rejected. The profile then determines which real Core values
are constructed. Fingerprints are checked by the typed outcome/record contracts;
a canonical encoding or matching digest does not authenticate provider truth.

Execution-result descriptors have exact effect_id/kind/evidence/failure/observations
keys. Failure descriptors have code/message/details. Observed-result descriptors
add request_fingerprint and choose one of six fixed constructors: succeeded, failed,
absent, conflict, indeterminate or observer-unsupported. Empty observed-failure
details decode as None. Evidence/detail objects pass to the actual bounded Core
constructors; their contents are not a closed schema invented by this store.

Endpoint observations reconstruct subject/socket/graph, protocol, context and either
literal material or a secret reference. Execution results can additionally contain
tagged VerificationCompleted values. HTTP evidence accepts the legacy three-key
shape or the five-key shape with expected digest and match flag. Redis evidence has
its own two-key decoding shape. Codec recognition of Redis is not proof that every
Redis completion is admissible as durable verification membership: the current
authoritative projection used here requires an HTTP check.

For any VerificationCompleted, both insertion and read reconstruction load
[EffectAttemptIntentStore](effect_attempt_intent_store.py.md) with the attempt
identity and call effect_outcome_observation_records using the actual supplied or
linked observation IDs. Intent workspace/request must match the outcome row, and
the recomputed observations must equal the retained tuple. The selected actual
projection joins intent identity, original event and request fingerprint, desired
graph and node/check identity to the declared HTTP check. It validates status/body
expectation evidence, including digest/match rules for passed results. This is
validation of retained intent and completion data, not a new HTTP request or an
independent measurement of the response body.

Read reconstruction creates EffectAttemptState from the historical outcome columns,
including its original fence/status/prior identity. It fetches the original and
direct events through the actual
[execution store](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
and compares their event-ID/run-ID/ordinal triples with the row. EffectAttemptRecord
then validates the state/event evidence, and the final outcome record validates the
aggregate. The store does not consult the current attempt row to replace that state.
A later recovery of the current attempt therefore does not rewrite an uncertain
direct outcome into a successful historical result.

Handled malformed row/codec/event/membership cases become the fixed row-invalid
OperationsRecordError, raised without raw exception context. Deep bounded JSON
RecursionError is handled at the preimage boundary. The catches are deliberately
not catch-all: unexpected TypeError/RuntimeError faults from a replaced decoder and
ordinary driver failures can escape unchanged. The fixed-error contract should not
be described as universal redaction of every operational failure.

The selected [schema](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
has an outcome primary key on the attempt identity, unique direct-event coordinates,
and foreign keys to attempt, run/request, request/workspace and event triples.
Membership has an attempt/position primary key, relation-wide unique observation
identity and foreign keys to the outcome's identity/workspace/count and observation
identity/workspace. The outcome membership foreign key is initially deferred; the
observation uniqueness is deferrable but initially immediate. These relationships
retain linked observations and reject duplicate ownership. They do not alone prove
complete positions, canonical preimages, fingerprint agreement or provider facts;
the decoder and language provide those additional checks.

The private current-row validator scans keyset pages ordered by run/activity/attempt,
default limit 64, and applies the same count, membership and decoding path to every
row. It rejects an oversized returned page and advances from the last tuple until
empty or short. The actual
[current-data validator](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_data_validation.py)
calls it alongside attempt, intent and saved-preparation checks and translates its
handled drift into CurrentRowDrift. This is schema-admission inspection, not a
public page API, repair procedure or guarantee of a consistent snapshot across
concurrent writes between pages.

The complete 382-line
[contract suite](../../../../../../control-plane-kit-operations/tests/test_postgres_effect_outcome_store_contract.py)
uses no-SQL, recording and failing connections. Its seven tests cover fixture
shapes, exact insert/get exposure and bundle wiring, invalid-input rejection before
SQL, lookup predicates and absence of FOR UPDATE, selected SQL bound tokens,
categorical misses/raw driver faults and inventory declarations. SQL-token checks
do not measure database work. The inventory assertion compares its declared list;
it is not a complete audit of every actual current import.

The complete 824-line
[PostgreSQL suite](../../../../../../control-plane-kit-operations/tests/test_postgres_effect_outcome_store.py)
contains 14 tests. It covers HTTP completion/intent association, prerequisite seeds,
20 synthetic direct variants, a historical uncertain snapshot after current recovery,
explicit immediate-prior linkage, ordered membership and linked-row retention,
membership/event/preimage drift, strict JSON/profile/fingerprint decoding, oversized
selected values, deep JSON, raw unexpected decoder faults, shared observation-ID
rejection, the exact 8192-byte input boundary, derived workspace and rollback/duplicate
behavior. These are selected cases, not an exhaustive concurrency or provider matrix.

The [PostgreSQL fixture](../../../tests/postgres_effect_outcome_store_fixture.py.md)
commits prerequisites separately from ordinary outcome insertion and uses actual
stores with synthetic graphs, events and outcome values. Fresh connections used by
the tests named after_restart do not restart PostgreSQL, an application or a provider.
The HTTP mismatch test's separate setup connection cannot observe uncommitted writes
inside another unit of work; its zero counts must not be read as proof of no writes
on the active writer. The 8192-byte case checks persistence's returned input, not a
separate get for that particular boundary. Other cases do perform fresh readback.

Oversize tests observe selected fetched fields through a recording connection and
exercise current-schema drift handling. Their bounds are not a general memory,
network or query-performance proof. Duplicate insert raises a raw UniqueViolation;
an uncommitted outcome/membership transaction rolls back while earlier committed
prerequisites remain. These tests do not inject a lost commit acknowledgement or
prove retry eligibility after an uncertain commit.

Private preimages can retain bounded failure text/details, endpoint material and
secret-reference descriptors accepted by Core. This store is not a public report
serializer or a general secret scrubber. Access to its caller connection and to
public projections remains a separate authorization/redaction responsibility.
It grants no approval, effect, cleanup, recovery or provider-inspection authority.

Read depth: full owner, both governing suites, the full 297-line PostgreSQL fixture,
full 736-line pure fixture and its parent context, and full unit of work; selected
actual outcome/projection, Core value, intent/event/observation, schema/bundle and
current-data-validation dependencies. Some full fixture/test context was retained
from the immediately preceding companion work. This is not a full audit of all
transitive dependencies. No source/tests were changed, no application imports,
tests, SQL, database/provider actions or credentials were used for this note.
