Source: [control-plane-kit-operations/src/control_plane_kit_operations/postgres/failed_run_compensation_attempt_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/failed_run_compensation_attempt_store.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 126-line adapter persists and reconstructs the relation between a compensation
program step, its source effect attempt and its inverse attempt. Its sole export
is FailedRunCompensationAttemptStore. It supplies insert, program/position lookup,
inverse-attempt lookup and ordered bindings for a program. It has no update/delete
method, retry policy, authority check, provider call or transaction lifecycle of
its own. The store bundle passes the caller's connection into its constructor.

_COLUMNS fixes eight selected/written columns in this order: program_id, position,
source_run_id, source_activity_id, source_attempt, inverse_run_id,
inverse_activity_id and inverse_attempt. SQL text interpolates that internal
constant; caller values are passed separately as parameters. The adapter stores
coordinates only, with no intent preimage, outcome payload, fingerprint, timestamp
or authority reference in this relation.

The imported
[FailedRunCompensationAttemptBinding](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
is a frozen slots value with four fields: program ID, position, source identity
and inverse identity. Its program text helper accepts isinstance(str), requires
nonempty text up to 512 characters and rejects control characters below code point
32; it does not apply the parent program's stricter identifier regex or reject
whitespace-only strings locally. Position must be exact positive int. Source and
inverse must be exact EffectAttemptIdentity values in the same run/activity, with
inverse attempt equal to source plus one and source not at 2,147,483,647.

The selected Core identity constructors admit RunId/activity identity and exact
positive attempt integers up to 2,147,483,647. These value laws govern ordinary
construction and decoder reconstruction. The binding constructor does not load
the program or attempts from a store, establish that the source succeeded, verify
the inverse's prior pointer, or authorize a worker to create the relation.

insert passes its input through _require_binding, which checks only the exact outer
binding type and returns the same object. It does not reconstruct the binding or
its nested identities to defend against every forged exact-type object. It reads
the two identities, executes one eight-parameter INSERT and returns that same
binding. It ignores the cursor/affected-row count and performs no RETURNING or
post-write readback. A returned binding is therefore not an independent database
acknowledgement or evidence that the caller has committed.

There is no ON CONFLICT clause or duplicate-as-replay behavior in insert. Database
constraint failures propagate rather than becoming an existing-binding result.
The selected
[current schema](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/current_schema.sql)
uses a primary key on program/position and a unique key on inverse run/activity/
attempt. A foreign key binds program/position/source identity to an admitted step;
another binds inverse identity to an effect-attempt row. Its identity check requires
positive position/source attempt, source below the maximum, same run/activity and
adjacent inverse attempt. These constraints do not check success/outcome, current
worker authority, contiguous program progression or intent congruence.

The module describes bindings as immutable because it exposes no mutation operation
and reconstructs a frozen value. Neither this adjective nor the ordinary table
constraints is a universal prohibition on direct SQL UPDATE. Constraints reject
incongruent changes but are not an append-only database policy. The service and
schema together supply the intended relation lifecycle.

get parameterizes program_id and position without local type/shape admission. It
fetches one row and raises KeyError with a fixed message if absent. get_for_attempt
first requires exact EffectAttemptIdentity, then queries by its inverse coordinates.
It does not recursively reconstruct that supplied identity; nested attribute or
SQL adaptation failures can still propagate. It uses the same fixed missing-row
error and decoder as get. The database unique constraint supplies inverse lookup
uniqueness; the method itself does not count matches or verify a second row is
absent.

for_program selects all bindings for the parameterized program ID ordered by
position, decodes each and returns a tuple. No rows yields an empty tuple rather
than NotFound. There is no pagination, size limit, continuity check or comparison
with the parent program's step count here. All lookup queries omit FOR UPDATE,
and the adapter acquires no advisory locks. They also do not verify that a decoded
row matches the lookup parameters beyond relying on the SQL predicate.

_decode requires an exact tuple or list of exactly eight elements, rejecting row
subclasses and other mapping/sequence shapes. It constructs fresh RunId and
EffectAttemptIdentity values for both sides, then reconstructs the binding. This
applies constructor identity/adjacency checks on readback without fetching either
attempt, the admitted step or the parent program. It is representation validation,
not a join-based proof that all related execution truth is current and coherent.

The decoder wraps TypeError, ValueError and OperationsRecordError in a fixed
OperationsRecordError with the original exception as cause. OperationsRecordError
already derives from ValueError. The row-shape guard is inside that try block;
SQL/cursor calls and input attribute access are outside it. Unrelated exceptions
are not universally caught, and chained errors do not establish secret-safe
exception output. There is no custom redactor or logging behavior in this adapter.

The actual
[attempt-start service](../failed_run_compensation_attempt.py.md)
locks the parent compensation program before reading its bindings. It checks
current lease/approval/lineage, requires contiguous positions, and either replays
an existing binding or validates successful earlier inverses before creating the
next. It rechecks source attempt/outcome/intent and reverse binding truth. Those
rules are not implemented by for_program or get_for_attempt. Cooperating service
calls supply the serialization boundary; arbitrary callers of this adapter must
not assume the unlocked reads themselves perform it.

The service writes event, intent and inverse attempt before inserting the binding
inside one
[PostgresUnitOfWork](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py).
That caller defers commit until successful context exit and rolls back failures.
This store does not commit, compensate, close or retry; a caller supplying an
autocommit connection would get that connection's different transactional behavior.
There is no hidden parent creation or attempt allocation in insert, and foreign
keys require the referenced rows to be present when enforced.

The reviewed
[compensation-attempt fixture](../../../tests/failed_run_compensation_attempt_fixture.py.md)
uses lookup while building/folding synthetic inverse states and includes all binding
columns in its selected snapshot. The reviewed
[PostgreSQL suite](../../../tests/test_postgres_failed_run_compensation_attempt.py.md)
asserts first inverse linkage, same-step duplicate/concurrent callers, progression,
rollback including binding-write failure, and fresh-unit-of-work lookup by both
program/position and inverse identity. A direct update to inverse attempt three
must raise IntegrityError, without asserting which constraint rejected it.

Those are service-level/selected relational tests. The suite's schema check asserts
column and constraint names, not every constraint expression or live catalog field;
its persisted lookup does not restart a process. It does not provide an exhaustive
isolated matrix for malformed eight-column rows, forged nested input, duplicate
insert acknowledgements, all possible direct updates or unlocked-reader races.
Snapshot and timed-thread limits remain those documented in the suite companion.

The binding is a durable coordinate for history and later fold/execution work, not
a runtime result. This module does not emit events, expose routes or decide whether
compensation may execute. Although the relation contains no credential fields,
its identifiers are not transformed into a separate public/redacted representation.
No provider or live resource is accessed by the documentation review.

Read depth: the complete 126-line adapter and all helpers were refreshed, along
with the binding record/text helper and selected Core identity/int validation,
relation-specific schema constraints and bundle wiring. Full attempt owner488,
fixture288, PostgreSQL suite518 and unit-of-work context were retained. No full
records/schema/Core dependency review is claimed. Validation was documentation-
only: local links, whitespace and frozen-source comparison. No application imports,
tests, database/provider calls, credential access, source/inventory edits or
publication were performed.
