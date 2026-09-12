Source: [control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_attempt_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_attempt_store.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 280-line Operations adapter owns PostgreSQL representation and conditional
mutation of effect-attempt rows. EffectAttemptStore exposes get, get_for_update,
insert_absent and compare_and_set; its constructor keeps a caller-supplied
connection with an execute interface. The class is exported from this module,
not the Operations or postgres root. PostgresStoreBundle supplies it as
effect_attempts on the bundle's shared connection. It does not create a connection,
commit, roll back, allocate IDs, append events, authorize a worker or run Core
transition logic.

_COLUMN_NAMES fixes a 21-column representation. Three primary coordinates are
run_id, activity_id and attempt. The remaining eighteen hold request fingerprint,
worker/generation fence, status/outcome fingerprint, optional prior coordinates,
optional recovery decision/resolution/fingerprints, and original/latest event
coordinate triples. _record_values maps a typed record into that order, including
explicit NULL groups for absent prior and recovery values. This row contains
references to event and intent evidence rather than those complete payloads.

get and get_for_update require an exact EffectAttemptIdentity, then select by
all three coordinates; the latter adds FOR UPDATE to that row query. A missing
row raises KeyError with the fixed effect-attempt-not-found message, without
embedding candidate coordinates. Existing rows go through reconstruction. The
input check does not rebuild the nested RunId or revalidate every identity bound.
The
adapter does not require a claim or policy scope for a read, and a row lock lasts
according to the caller's transaction. Original/latest events are loaded with
ordinary event reads rather than separate explicit FOR UPDATE queries.

insert_absent requires an exact EffectAttemptRecord and inserts all columns with
ON CONFLICT (run_id, activity_id, attempt) DO NOTHING RETURNING run_id. No returned
row means None; any non-None returned row produces the supplied record. It does
not reread or decode the inserted row, compare an existing record for equality,
or absorb a different uniqueness/foreign-key failure. Events, matching intent and
any required predecessor must already be available in the relevant transaction.
The adapter itself does not create them.

compare_and_set first requires exact current/replacement record types. It rejects
changes between those arguments to identity, request fingerprint, fence, prior
attempt or the complete original event, and rejects a lower latest ordinal.
Equal latest ordinals are not rejected by this precheck. These are selected
replacement invariants, not reconstruction of all record semantics or validation
that a Core state transition is lawful. Mutations check top-level record type
without rerunning its constructor; raw forged exact-type inputs are not generally
readmitted at this boundary.

The UPDATE sets all eighteen non-key columns to replacement values, matches the
three key coordinates with equality, and matches every prior non-key value with
IS NOT DISTINCT FROM. This makes nullable prior/recovery fields participate in
the comparison instead of disappearing under SQL NULL equality. A stale or missing
row produces None; a matched row produces the supplied replacement from the
RETURNING presence check. These cases do not distinguish missing identity from
stale state, and an identical replacement is not treated as a separate replay
category.

The complete prior is complete about these physical row columns. It does not
compare referenced event payloads, event times, intent preimage bytes or other
tables. The original-event equality precheck compares caller arguments, not a
freshly loaded event. CAS returns no decoded database candidate and performs no
compensating action when it misses. Callers own event creation, plan/claim checks,
retries and the transaction that groups this update with other writes.

_reconstruct_row requires an exact tuple/list with 21 entries, reconstructs the
identity, and treats a fully NULL prior or recovery group as absent. Otherwise
it sends the group's values through Core constructors. Recovery is reconstructed
against the row's current identity; that identity is not stored again inside a
separate recovery coordinate group. The function constructs fence/status/state,
loads original and latest events via PostgresExecutionStore.get_event, compares
their event-ID/run/ordinal triples with the row, then constructs
[EffectAttemptRecord](../effect_attempts.py.md).

That record constructor enforces exact state/event shapes and original/latest
state commitments, while the
[Core constructors](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/recovery.py)
validate state relationships, retry lineage and recovery evidence values.
Reconstruction spans the attempt read and separate event queries on the supplied
connection; this adapter does not create a snapshot or independently establish
transaction isolation. STARTED rows still load original and latest by their
references even when both identify the same event.

_decode_row catches ValueError and OperationsRecordError from reconstruction and
raises the fixed row-invalid OperationsRecordError outside the caught handler,
without cause/context. TypeError, RuntimeError and other categories escape.
The initial SELECT and the attempt-row miss occur outside that decoder wrapper;
a missing referenced event's KeyError is also not among the caught categories.
Thus bounded decoder errors coexist with raw collaborator/SQL failures. This is
not universal exception sanitization, automatic corruption repair or permission
to render every database error publicly.

Private _validate_current_rows scans in lexicographic primary-coordinate order,
using LIMIT 64 by default and a greater-than keyset predicate after each page.
Each result collection must be an exact tuple/list no larger than the requested
limit. Every row is passed through the same decoder before the last coordinates
become the next cursor; empty or short pages end the scan. The helper returns no
records and is not a public pagination API. It performs no writes, repair,
explicit row/schema locking or transaction management of its own.

The selected
[current-data validator](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_data_validation.py)
calls this private scanner before the separate intent/outcome scanners and
translates selected validation exceptions to CurrentRowDrift. Schema installation
has its own transaction and verification locks. Those surrounding responsibilities
must not be attributed to the four store methods or to a standalone scan; the
scan by itself does not prove every cross-table reference or current-schema law.

The relevant
[schema](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
owns primary identity and original/latest role uniqueness, predecessor/event/intent
foreign keys, scalar bounds and state/recovery checks. The store deliberately
leaves non-primary insert collisions and integrity errors raw. Schema checks and
typed event commitments are complementary: direct SQL can create some states
that satisfy scalar constraints but fail record reconstruction. CAS can still
compare such a row physically without decoding it first.

PostgresStoreBundle binds event, intent and attempt adapters to the same
connection, and the
[unit of work](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
owns commit/rollback/close. A successful store return is an acknowledgement inside
that transaction, not proof of durable commit. Correctly grouping prior event
writes and CAS is a caller concern; precommitted candidate events are not removed
when a later CAS loses. The store owns no timeout, cleanup or idempotency policy
beyond its primary-conflict and prior-match SQL behavior.

The eight fully read
[pure store-contract tests](../../../tests/test_postgres_effect_attempt_store_contract.py.md)
check the four-method surface and signatures, bundle wiring/root privacy, selected
pre-SQL exact-type and immutable-coordinate failures, all eighteen SQL predicate
fragments, exact read parameters/lock spelling, primary-conflict syntax and raw
exception identity. Its CAS SQL test checks predicate text, not every bound
parameter or a real concurrent database update. Its AST restrictions and selected
inventory row constrain named dependencies/calls rather than proving every
possible effect absent.

The eight fully read
[PostgreSQL tests](../../../tests/test_postgres_effect_attempt_store.py.md)
add cross-connection reconstruction, actual no-commit rollback, integrity classes,
forced NOWAIT row locking, a barrier-coordinated CAS winner/read-back/stale loser,
five grouped prior-drift cases and twenty-four injected decoder exceptions.
They do not restart the database, independently check every physical-column drift
or prove atomic event/outcome/attempt folding. Their decoder seams raise selected
exceptions rather than corrupting encoded payloads. The schema test named in
the selected inventory row was not fully reviewed for this companion, and these
two suites do not directly establish private scanner pagination coverage.

Read depth: all 280 source lines, helpers and query templates were read, along
with the complete 126-line fixture, 444-line pure contract suite and 488-line
PostgreSQL suite. Retained record/Core context and selected actual bundle, event,
unit-of-work, current-data validation, schema and inventory contracts were checked.
No full review of those adjacent owners is implied. Validation was limited to
local links, whitespace and frozen-source comparison; no application imports,
tests, database/provider calls, credential access, source/inventory edits or
publication were performed.
