Source: [control-plane-kit-operations/tests/postgres_effect_attempt_store_fixture.py](../../../../control-plane-kit-operations/tests/postgres_effect_attempt_store_fixture.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 126-line fixture combines the pure
[effect-attempt record builders](effect_attempt_record_fixture.py.md)
with inherited PostgreSQL execution-lease setup. It can persist original/latest
events, start-intent evidence and an attempt row, and can construct a replacement
record. It defines no tests, runtime observer, authorization service, concurrency
schedule or transition interpreter of its own.

_load_module imports the PostgreSQL effect-attempt store and returns None only
when ModuleNotFoundError names that exact module. Other missing dependencies or
import failures propagate. EffectAttemptStore is captured with getattr defaulting
to None. require_store checks only its presence, not signatures or bundle identity,
and is not automatically called by setUp or persist. Other fixture imports remain
unconditional; this optional loader does not make the complete fixture independent
of the PostgreSQL package or its transitive imports.

setUp calls the inherited
[lease-recovery fixture](../../../../control-plane-kit-operations/tests/execution_lease_recovery_fixture.py)
setup, then reset_truth(RENEW_ACTIVE_CLAIM, history="active-empty"). Base setup
requires CPK_OPERATIONS_TEST_DATABASE_URL, opens an autocommit psycopg connection,
calls install_schema and truncates cpk_workspaces CASCADE. reset_truth truncates
again before seeding. These are real destructive test-namespace operations, so
execution belongs in the isolated PostgreSQL test context.

The inspected
[schema installer](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/schema.py)
creates an empty owned namespace or verifies current schema/data under its
transaction and locks; it does not silently migrate an incompatible existing
namespace. Installation happens before this fixture's cleanup truncation, so
schema verification can fail before reset. The fixture adds no retry or fallback
installation path.

The selected seed creates workspace/graph/session/plan/approval/request/run truth
through autocommit statements and separate committed units of work. Its request
is claimed by worker-a at generation seven with synthetic future lease dates;
the active-empty history contains RUN_OPENED, while the run is CLAIMED. This is
supporting store-test truth, not an executed runtime activity or proof that every
arbitrary record built later agrees with a planner's activity. Setup as a whole
is not one atomic transaction.

tearDown delegates to the base implementation, which truncates and closes when
the setup connection remains open. That cleanup has no surrounding finally to
guarantee close if truncation fails; setup also has no local failure-cleanup
wrapper. unit_of_work creates a fresh ordinary psycopg connection for each use,
without explicit statement/lock timeouts. The autocommit fixture connection and
individual unit-of-work connections have different transaction lifetimes.

add_record_events writes the original event and writes latest only when it is
not equal to original. This is value equality, not an object-identity or status
check. The helper ignores add_event return values and neither commits nor handles
duplicates. The actual execution store performs an INSERT with encoded timestamp
and event payload and returns the supplied record. Unique/event-coordinate errors
remain exceptions in the caller's transaction.

add_record_intent uses an explicit supplied intent unless it is None. Otherwise
it derives compensation from the original kind's step_compensation prefix and
builds an intent for the record's run/activity. It constructs the actual
EffectAttemptIntentRecord, asserts that its request fingerprint equals the state's
request_fingerprint, inserts it and requires the returned value to equal that evidence.
It returns the evidence and does not own a transaction or call commit.

The actual
[intent evidence constructor](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_intent_evidence.py)
reconstructs identity/event and round-trips canonical typed intent, checking their
coordinate agreement. The fixture's fingerprint assertion adds its connection
to the supplied state. The
[intent store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_attempt_intent_store.py)
validates and encodes the record, then inserts the protected canonical preimage;
it has no absent-insert behavior in this path. The fixture does not allocate an
intent ID, resolve its secret references or make its material available to a
runtime provider.

persist opens one unit of work, adds events, adds intent if the bundle has an
effect_attempt_intents attribute, calls insert_absent and requests commit before
returning that call's result. The current actual store bundle always supplies
both intent and attempt stores on the same connection. The hasattr branch can
skip intent writing for a differently supplied bundle; it does not prove that
current-schema persistence is valid without required intent evidence.

The actual
[attempt store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/effect_attempt_store.py)
uses ON CONFLICT on the primary attempt coordinates to return None for an absent
insert miss, otherwise returning the supplied record. persist does not assert
that the result equals the candidate and requests commit even if it is None.
Because event and intent writes occur first, persist is not an idempotent wrapper
around insert_absent: repeated calls can encounter earlier uniqueness failures
before reaching that branch. No retry, preflight or compensating delete is added.

The actual
[unit of work](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
performs a physical commit only after successful context exit with commit
requested, otherwise rolls back, and closes in finally. A failed fingerprint or
insert-result assertion inside persist therefore prevents its commit request and
rolls back that call's prior writes. Direct calls to add_record_events and
add_record_intent inherit whichever transaction their caller supplies; they do
not independently ensure rollback or commit.

transition constructs a candidate state for the requested story and current
coordinates, then replaces identity, request fingerprint, fence and prior attempt
with the current record's values. The candidate state call does not pass a
compensation argument; phase for the new event is separately derived from the
current original event. The latest event uses caller-supplied ID/ordinal and fixed
2030-01-01T00:00:01Z time, and the returned record retains the original start event.
No new failure evidence is supplied.

This helper does not call fold_effect_attempt, verify that the story is a lawful
transition from current state, allocate an ordinal, persist the new event or
perform compare-and-set. It builds a record that must satisfy record-level
commitment/order admission; stronger transition eligibility, complete fold-result
requirements and concurrent prior-state matching belong to other layers.

Selected sections of the
[PostgreSQL consumer](../../../../control-plane-kit-operations/tests/test_postgres_effect_attempt_store.py)
show these distinctions. Its story/phase and maximum-event-ID cases persist and
read through a later fresh unit-of-work connection. Its rollback case directly
calls the event/intent/attempt helpers without requesting commit, then checks a
read miss; its duplicate case calls insert_absent directly after successful
persist. Its predecessor case tests a raw foreign-key rejection before seeding
the prior attempt. Its selected CAS test separately persists transition events
before coordinating competing compare-and-set calls. Those are consumer-owned
assertions, not behavior automatically exercised by this fixture. A later fresh
connection is not a PostgreSQL/server process restart.

Read depth: the complete 126-line source and helpers were read, with retained
complete record-fixture context. Relevant inherited setup/reset/seed/cleanup and
connection helpers, selected actual schema installation, bundle wiring, event and
intent insertion/admission, attempt insert/CAS and unit-of-work contracts were
checked. Only the named consumer sections and a selected store-contract surface
section were read; neither consumer suite is claimed as fully reviewed. Validation
used local links, whitespace and frozen-source comparison only. No application
imports, tests, database/provider calls, credential access, source/inventory
changes or publication were performed.
