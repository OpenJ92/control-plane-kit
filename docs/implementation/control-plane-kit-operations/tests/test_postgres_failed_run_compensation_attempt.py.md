Source: [control-plane-kit-operations/tests/test_postgres_failed_run_compensation_attempt.py](../../../../control-plane-kit-operations/tests/test_postgres_failed_run_compensation_attempt.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 518-line PostgreSQL suite has ten tests for binding admitted compensation
steps to inverse attempts. It covers selected public/schema shape, first admission,
duplicate and concurrent callers, ordered progression, replay of five settled
states, changed lineage/authority, rollback and persisted bidirectional lookup.
It starts and folds durable attempts using synthetic effects; it does not execute
the inverse runtime operations. The direct-execution guard calls unittest.main.

The inherited
[compensation-attempt fixture](failed_run_compensation_attempt_fixture.py.md)
extends the base failed-run seed, admits program-a, and separately updates the
execution claim to fixed 2098/2099 timestamps. It inherits database configuration,
schema installation and autocommit truncation/cleanup. Each unit of work opens a
fresh connection. Its intent builder reads program-a even when the command later
overrides program_id, and default intent construction happens before overrides.
The ID label must-not-allocate is ordinary Sequence input, not a callable that
fails on first use; these consumers do not retain or inspect its allocation count.

The language/schema test checks nine named module attributes are non-None and
that dataclasses.fields reports exactly program_id, position, source_attempt and
inverse_attempt for the binding. It does not assert root-export identity, exact
types/signatures, frozen/slots behavior or constructor rejection laws. The local
current_schema.sql must contain the CREATE TABLE substring. The imported static
schema contract must list eight ordered column names and exactly five constraint
names for the binding relation. Constraint names are compared as a set, so that
assertion does not distinguish duplicate contract entries or verify expressions,
types, foreign-key targets or live catalog parity.

The selected actual
[binding record](../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
is a frozen slots value requiring a positive position and identities in the same
run/activity, with inverse attempt equal to source attempt plus one. Its source
cannot be the maximum supported attempt. The selected schema rows separately
express identity, primary/unique and foreign-key constraints. The suite's shape
checks are narrower than these contracts; one later IntegrityError case exercises
an invalid persisted inverse identity without identifying the precise constraint.

First admission seeds the program and records a source_truth_snapshot. The result
must be an instance of NewlyBoundCompensationAttempt with replayed=False, program-a
position one, source (run-a, start-node, one) and inverse (run-a, start-node, two).
The attempt must be STARTED with source as prior_attempt and a
STEP_COMPENSATION_STARTED original event. The intent must be an
EffectAttemptIntentRecord whose identity is the inverse. Selected source rows must
remain unchanged; a separate query requires graph-current, graph-desired and
revision one on the workspace. This test does not compare every returned field,
read back every newly written row or independently verify intent/state hashes.

The actual
[attempt admission owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/failed_run_compensation_attempt.py)
locks the parent program, validates current request lease, run, workspace, session,
plan, approval and admission lineage, and requires contiguous bindings. Fresh
admission selects exactly the next position and requires every earlier inverse to
have congruent successful outcome truth. It derives expected intent by replacing
only operation in the stored source intent, folds STARTED with source as prior,
and writes start event, intent, attempt and binding in one unit of work. Existing
position replay revalidates lineage and inverse/source truth without applying the
next-position success requirement to that same binding.

The duplicate test first requires conflict when position two is requested before
position one. It then starts position one and replays through a new service;
ExistingCompensationAttemptBinding, replayed=True, equal binding and unchanged
binding_snapshot are required. Two changed replay commands alter activity identity
or fence generation and must raise the broad FailedRunCompensationAttemptError
base while preserving that snapshot. The initial skipped-position rejection has
no separate snapshot assertion. No clock/ID bomb, call count or equality of the
entire replay result is asserted.

The concurrency test launches two ordinary threading.Thread workers. A two-party
barrier with a ten-second timeout precedes command construction and execution;
each helper constructs a fresh service and performs its own program read before
the admission transaction. Workers append results or caught BaseException values
to shared lists. After a twenty-second join for each thread, the test requires no
recorded errors, two results, one fresh result and one replay. The five snapshot
section sizes must be (one, one, one, one, zero): binding, compensation-start event,
inverse attempt, inverse intent and inverse outcome.

This aligns worker entry but does not force a particular database interleaving,
inspect lock waits, require both winner orders or compare the returned bindings
and records. Timed joins do not stop a blocked worker; there is no is_alive check,
cancellation or guaranteed final join before fixture teardown. The fixture does
not set database statement/lock timeouts. These are static limits of the test
orchestration, not observed failures in this documentation review.

Progression is rejected for a prior STARTED, FAILED, UNSUPPORTED, UNCERTAIN or
ABANDONED inverse. Each subcase resets, admits and starts position one; STARTED
uses that state directly, while the others call fold_bound_attempt. The attempted
position-two command must raise conflict and preserve the post-fold snapshot.
Two further subcases first fold SUCCEEDED, then delete its outcome or corrupt its
intent preimage to empty JSON, requiring conflict and unchanged post-mutation
snapshot. A final successful case starts position two after the first succeeds,
asserting the fresh-result class and position two. The fixture has only two steps,
so this does not exercise multiple earlier bindings or every possible source state.

The fixture's fold helper calls the actual
[effect-attempt fold service](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold_interpreter.py)
with a fabricated RuntimeEffectResult, fixed IDs and current worker/fence values.
ABANDONED is prepared through two separate committed calls: an uncertain outcome
and then an abandonment recovery transition with no replacement outcome. The
historical uncertain outcome remains. This provides durable test states, not
evidence that a provider executed or an operator resolved a real uncertain effect.

Replay after each of SUCCEEDED, FAILED, UNSUPPORTED, UNCERTAIN and ABANDONED must
return ExistingCompensationAttemptBinding with replayed=True, the original binding,
the selected current status and unchanged binding_snapshot. A caught attempt-error
base becomes an explicit unittest failure. The five names are the test's fixed
status tuple; this is not enumeration-based future coverage. The cases demonstrate
the difference between observing an existing binding and permitting the next step.
They do not test replay after changed ownership or expired authority as successful.

The six changed-truth cases cover a missing program ID, changed relational source
outcome fingerprint, changed intent base graph, rejected approval decision, a
different worker with matching command fence, and an incremented fence generation.
Each expects FailedRunCompensationAttemptError and unchanged binding_snapshot.
The snapshot is taken before the optional SQL mutation, but it omits compensation
step and approval rows: equality does not mean those mutations were rolled back.
The missing-program case still constructs its default intent from valid program-a
first. This matrix does not exhaust lease expiry, missing scope, closed sessions,
all command fields or exact error subtype/message/chaining behavior.

Five injected-failure cases exercise event, intent, attempt, binding and connection
commit. The local wrapper executes the real SQL first, then counts normalized
INSERT prefixes and raises the exact RawWriteFailure after the selected statement.
For case five, connection.commit raises before delegating to psycopg. Each test
requires exception object identity, unchanged binding_snapshot and zero bindings.
The actual unit of work rolls back and closes on these failures. There is no
injection after successful server commit, lost-acknowledgement simulation or
rollback/close-failure case; the mapping depends on current write order and the
four recognized prefixes. Snapshot equality covers selected fields, not every
owned row/column suggested by the method name.

The persisted-lookup test starts one inverse, opens a fresh unit of work and checks
both store.get(program-a, one) and store.get_for_attempt(inverse_identity) equal the
returned binding. No commit is requested for that read context. The actual
[binding store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/failed_run_compensation_attempt_store.py)
reconstructs the eight-column row into the binding value for either lookup; this
does not restart a process or PostgreSQL server.

A direct update setting inverse_attempt to three must raise psycopg.IntegrityError
and preserve the snapshot. Two later drift cases change the program step's source
outcome fingerprint or inverse intent preimage, accepting OperationsRecordError
or FailedRunCompensationAttemptConflict around command construction plus service
execution. The first corruption may therefore be rejected by the fixture's program
read before the service is called. Each case truncates and reseeds afterward,
including after the final case; there is no snapshot comparison following these
two rejected drift commands. Freshly assigned before values there do not add an
assertion or repair guarantee.

Despite its bounded/redacted method name, the final test only reads the attempt
module's source and excludes nine literal substrings: RuntimeInterpreterDispatcher,
ActivityExecutionDispatcher, COMPENSATED, PARTIALLY_FAILED, docker, prune, provider,
http and mcp. It constructs no public evidence, measures no payload size and checks
no secret canary, descriptor or exception redaction. This finite text check is not
an import-graph analysis or proof that dependencies cannot execute providers.

The inherited binding_snapshot has five separately queried groups across the
database: all binding columns; selected compensation-start events; and selected
inverse attempt, intent and outcome fields for attempt numbers greater than one.
It excludes other event kinds/timestamps, attempt fences and several fingerprints,
source-attempt truth, parent programs/steps, approvals, requests and workspace state.
source_truth_snapshot separately selects attempt-one attempt/outcome fields and
omits source intent and full history. Neither helper supplies a consistent single
transaction snapshot or redacts/bounds preimages. Write-free/source-preservation
claims in this suite are limited to these explicit observations.

Read depth: the complete 518-line suite and all ten tests/local helpers were read.
The complete 288-line fixture, 488-line attempt owner and binding store were
refreshed, with retained full base fixture/admission owner and unit-of-work context.
Selected actual binding record, relation-specific schema/contract entries and
fold-service execution paths were checked. No full records/schema/fold dependency
review is claimed. Validation was documentation-only: local links, whitespace
and frozen-source comparison. No application imports, tests, database/provider
calls, credential access, source/inventory edits or publication were performed.
