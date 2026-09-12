Source: [control-plane-kit-operations/tests/test_postgres_atomic_effect_attempt_fold.py](../../../../control-plane-kit-operations/tests/test_postgres_atomic_effect_attempt_fold.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These thirteen tests exercise durable direct-outcome aggregates, recovery's
separation from direct evidence, replay validation, write ordering, generated IDs
and exact acknowledgements. They inherit the
[guarded PostgreSQL fixture](../../../../control-plane-kit-operations/tests/postgres_guarded_observed_effect_fold_fixture.py)
and [ordinary fold fixture](postgres_effect_attempt_fold_fixture.py.md). Real stores
and transactions coexist with deliberate adapter/constructor substitutions. A
claim based on a substituted return must not be confused with a stored provider
fact. No Docker/provider calls or credential resolution occur in these tests;
their PostgreSQL setup belongs to the package's established Docker suite.

The first test wraps the real intent get method and requires exactly one lookup
of the current attempt identity plus NewlyFolded for execution-succeeded. It does
not separately compare every returned intent field or cover all profiles. The
next test returns a typed intent record rebuilt with changed product material and
therefore a different commitment. The service must reject with the invalid-truth
message before any observed-state put, and get must receive the expected identity
once. This is a substituted drifted record, not corrupt persisted bytes. That
method does not independently forbid every other store call or assert a complete
rollback snapshot; the actual interpreter supplies the early validation boundary.

The twenty-row direct matrix spans four execution and six observation variants in
ordinary/compensation families. Execution uses execute; observation rows explicitly
seed protected intent, register runtime authority and call execute_observed. The
registration writes a local Docker authority value to Operations storage; it does
not inspect a Docker socket or prove provider authenticity. Guard construction
joins synthetic original-event/request fingerprints before entry. These fixtures
do not include every payload or the separate verification-intent composition.

Each first fold must be NewlyFolded, preserve the original start event, allocate
the expected event-plus-endpoint IDs and equal a rebuilt expected outcome aggregate.
A fresh UoW reloads the typed outcome by exact identity/latest-event ID and compares
it to that expectation. The expected helper calls the same production observation
bridge and typed aggregate constructors; this is durable round-trip and composition
evidence, not a wholly independent implementation of projection semantics. The
expected aggregate uses the result's own attempt, so its equality alone is not an
independent prediction of every resulting state coordinate.

For each row, a new service replays with exactly one instrumented outcome lookup,
equal ExistingFold, no allocated IDs and an unchanged selected snapshot. Sentinels
forbid lease sampling, ordinal selection, event append, observation put, outcome
insert and attempt CAS. This is stronger than result equality alone. It still
does not count every SQL call or suppress commit: initial locks and nested store
reconstruction remain. The title's restart means a fresh service/UoW in the same
process, not database, process or deployed-runtime restart.

Six recovery worlds cover three decisions and both event families. First fold and
replay prohibit observation puts and outcome insert/get; first allocates one event
ID and requires the entire test outcome table to remain empty. Replay additionally
forbids database lease observation, allocates no IDs and preserves the snapshot.
These fixtures directly seed uncertain event/attempt truth without a direct outcome
row. A separate test first persists a real direct UNCERTAIN outcome, then recovers
without touching direct stores. Reusing the old direct command must conflict before
lease sampling/outcome lookup or observation put; the prior aggregate remains
readable through a fresh UoW. Thus recovery does not erase earlier direct evidence.

Six replay-corruption cases distinguish persisted mutation from injected return:
missing aggregate, invalid preimage, missing membership, reversed membership order,
changed observation evidence and a typed but different outcome-record return. The
first five alter test database rows; the foreign case substitutes observed-absent
evidence in the same workspace rather than proving cross-workspace isolation.
All require invalid-truth Conflict, safe fixed rendering, no ID allocation, forbidden
lease/ordinal/event/observation/CAS work and snapshot equality after the corruption.
That equality means replay does not repair or further alter the selected state;
it does not claim the preexisting corruption disappeared. Outcome insert is not
independently sentinel-patched in this particular method.

Two injected outcome-get exceptions, KeyError and OperationsRecordError, must become
fixed invalid-truth conflicts without the canaries or raw error chain. Two injected
TypeError/RuntimeError objects must escape identically. These are adapter exception
seams, not actual driver/codec failures. ID labels named must-not-allocate are not
call assertions in this error-translation method, and it does not take a separate
before/after snapshot for each error.

The ordering test wraps real observation/ordinal/event/observation-put/outcome/CAS
methods, the ID allocator, UoW.commit and five __post_init__ methods. Before every
tracked write, it requires construction markers for event, attempt, outcome and
result, and at least two observation constructions. This proves marker presence,
not exact constructor multiplicity, constructor order or per-object identity;
the validators themselves reconstruct values. The asserted filtered call ledger
is event, observation 0, observation 1, outcome, CAS, commit. Written observations
must equal the outcome's membership, and all observation timestamps equal the
latest event's timestamp.

Clock, ordinal and ID markers are collected but excluded from that final ordering
comparison. Only ID values are independently checked as the expected sequence.
The test therefore does not assert an exact clock-call count or the full title's
clock-to-ID-to-construction ordering. Its commit marker is a UoW commit request,
not the underlying connection's physical commit. Actual
[interpreter code](../src/control_plane_kit_operations/effect_attempt_fold_interpreter.py.md)
plans the full result before explicit event writes and requests commit at the end;
physical completion belongs to [PostgresUnitOfWork](../src/control_plane_kit_operations/postgres/unit_of_work.py.md).

The workspace test substitutes the same changed workspace into both locked-request
reads, checks exactly two equal returned requests, and requires observations and
the attempted outcome to carry that workspace. Observation puts return typed
records without calling the real store; outcome insertion records its argument
then raises an exact sentinel. It requires two observations, one outcome attempt
and rollback snapshot equality. This proves which input drives projection and
that the real preceding event is rolled back, not successful storage under a
different workspace or bypass of database ownership constraints.

Six generated-ID cases cover duplicate observations, collision with the event ID,
empty, control-character, surrogate and oversized observation IDs. All planned
IDs must be consumed, then fixed serialization conflict must precede four explicit
write methods. Four additional direct/recovery cases supply an unhashable list or
a str subclass with a hash spy. They catch any BaseException but require the exact
EffectAttemptFoldConflict type, fixed safe text, all selected IDs consumed, no hash dispatch and
snapshot equality. This is genuine selected no-hash evidence, not instrumentation
of every possible hostile protocol. IDs consumed before rejection are not reclaimed.

_hostile_acknowledgement builds a subclass of the returned record, copying fields
with object-level access and recording attribute/equality/inequality dispatch.
On replay, a substituted hostile outcome return must conflict before virtual
access or mutation, leave the dispatch/ID lists empty and preserve the snapshot.
On first writes, five targets cover event, each of two observations, outcome and
CAS. Each wrapper calls the real adapter first, then returns a hostile subclass
only at its chosen target. Fixed serialization conflict, no virtual access, exact
planned ID consumption and unchanged snapshots prove rollback after those selected
real writes, not merely rejection before the SQL operation.

The real [outcome store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_outcome_store.py)
reloads typed aggregates and validates membership; the
[attempt store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_attempt_store.py)
compares the complete prior state on CAS. Those implementations and the shared UoW
explain the test effects. The snapshot includes inherited execution/attempt/intent
truth, graph/projection/observation counts, selected outcome coordinates and ordered
membership. It does not compare every observation body, outcome preimage, authority
row or all graph values. Consequently snapshot equality alone is not a global
no-write audit, full isolation proof or acknowledgement-loss recovery guarantee.

Expected error checks inherit fixed messages, no cause/context, bounded combined
str/repr and selected canary exclusion. The fixture's checked service converts
NotImplementedError and two exact missing-outcome constructor TypeErrors into test
failures; other internal errors retain their normal paths. Synthetic decisions,
authority declarations and credential references are never approval to execute
recovery or external effects. This file has no concurrency schedule, physical
commit-failure injection, arbitrary replay recovery or live acceptance proof.

Read depth: all 969 source lines, thirteen tests and local callbacks; actual guarded
seed/registration/command helpers, ordinary expected-outcome/snapshot/checked-service
and ID helpers; retained full 567-line interpreter, complete UoW, language and
selected actual intent/outcome/CAS/observation dependencies. Shared expectation
helpers and selected adapter context are identified rather than counted as new
independent semantic or full-owner review. No imports/tests, PostgreSQL, Docker,
provider actions or source changes accompanied authoring.
