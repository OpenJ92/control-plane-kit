Source: [control-plane-kit-operations/tests/failed_run_compensation_attempt_fixture.py](../../../../control-plane-kit-operations/tests/failed_run_compensation_attempt_fixture.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 288-line fixture extends
[FailedRunCompensationFixture](failed_run_compensation_fixture.py.md) with an admitted
program seed, inverse-intent/command/service builders, outcome folding for the first
bound inverse and selected source/binding snapshots. It has no test methods and
executes no runtime provider. Some helpers call actual admission/start/fold services
against PostgreSQL; their synthetic inputs are not provider observations.

The compensation-attempt target module is optional only when ModuleNotFoundError
names that exact module. Nested dependency and other import errors propagate;
fold services and other imports are unconditional. require_attempt_contract checks
only module presence and returns it, without checking exports or signatures.
The os import has no use in this file; database configuration, setup/truncation,
teardown and fresh-connection unit-of-work behavior are inherited from the base.

seed_admitted_program calls the base seed_truth, executes actual compensation
admission with fixed program/event/action IDs and then directly extends request-a's
claim timestamps into 2098/2099. It ignores the returned admission result and
does not check the update's affected-row count. The extension is a separate
autocommit update, not an atomic part of program admission or an operator lease
renewal command. The base seeding already spans multiple commits; this method does
not truncate before reuse or make the fixed identities idempotent.

intent opens a read unit of work, retrieves program-a and selects
program.steps[position - 1] after leaving that context. It does not request commit
for the read. Selection is ordinary Python indexing with no fixture-level positive
or upper-bound check: zero selects the last step, negative positions can select
earlier elements, and out-of-range/type failures escape before command admission.
Those semantics do not make such positions valid start commands.

The builder constructs a new Docker REALIZE_ACTIVITY intent with fixed workspace-a,
request-a, run-a, plan-a and graph-current/graph-desired. It takes activity identity
from the selected step's source effect and operation from the admitted inverse step,
but supplies no authority reference, deliveries or products. Dynamic __import__
retrieves ActivityId; it does not defer all imports or provide a provider adapter.
Keyword changes overwrite the constructed defaults before RuntimeEffectIntent
admission. The helper does not load and copy the source effect's stored intent.

That distinction matters because the actual
[compensation-attempt owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/failed_run_compensation_attempt.py)
derives expected inverse intent by replacing only operation in the persisted source
intent. The fixture's reconstructed defaults match the base seed's empty material
and fixed lineage. They are not a general builder for arbitrary program IDs,
source products, authority deliveries or graph coordinates.

start_command checks target-module presence, builds default values for program-a,
the requested position, self.intent(position), worker-a with EXECUTION_OPERATE and
ExecutionLeaseFence(worker-a, one), then applies keyword overrides. The default
intent/database read occurs eagerly even when an intent or program_id override
is supplied. It does not fetch the overridden program to choose a different step.
The actual StartFailedRunCompensationAttempt constructor supplies the positive
position, exact intent/authority/fence, scope and worker-agreement checks after
that fixture construction work.

attempt_service constructs the actual start service with a new Sequence and the
supplied truthy unit_of_work factory or the inherited factory. A falsey supplied
factory falls back to the default. It returns only the service, not the Sequence;
the helper itself does not assert allocation counts. Sequence records successfully
popped fixed IDs and raises IndexError on exhaustion. No clock is injected into
this service; its actual implementation reads database time.

The actual start service locks the admitted program and validates request, active
unexpired claim, run, workspace, session, plan, approval and admission lineage.
Existing bindings must be contiguous. A new command must select the next position
and every earlier binding must have a SUCCEEDED inverse with corresponding outcome
evidence. Source attempt/outcome/intent must still agree with the program. The
service derives the inverse identity in the same run/activity at source attempt
plus one and records the source identity as prior_attempt.

For this seed, position one undoes start-node attempt one through start-node attempt
two; position two similarly follows the runtime source. The service folds a fresh
STARTED transition, then writes compensation-start event, intent, attempt and
bidirectional binding within its unit of work. It treats an absent insert result
as conflict and requests commit before returning. The fixture does not itself
perform these checks or assert that every write acknowledgement is validated.

The actual
[binding store](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/failed_run_compensation_attempt_store.py)
provides lookup by program/position and by inverse attempt, plus ordered program
bindings. Service replay rechecks source truth, expected inverse identity, prior,
original event, intent and reverse binding, as well as current lineage/authority.
Replaying an existing position differs from admitting the next one: the latter
requires all previous inverses succeeded with outcomes, while replay can observe
the currently recorded status of its own bound inverse.

fold_bound_attempt always reads binding (program-a, position one) and its intent
record in a separate read unit of work. It then selects a synthetic RuntimeEffectResult
from a dictionary covering SUCCEEDED, FAILED, UNSUPPORTED, UNCERTAIN and ABANDONED.
All dictionary values are constructed eagerly before status lookup. Other statuses
raise the mapping's KeyError after the reads and value construction; STARTED is
not a no-op option handled by this helper.

Every synthetic result uses effect ID inverse-start-a. Success carries a fixed
resource_fingerprint; the failure variants carry fixed runtime failure codes and
messages. ABANDONED initially selects an uncertain result. Although identity and
request fingerprint come from the actual binding/intent, the result ID is not read
from the stored original start event. Consumers must have started this inverse
with the matching ID for the ordinary outcome-binding laws to hold. The helper is
not parameterized for later positions or arbitrary event IDs.

The helper wraps that result in ExecutionEffectOutcome and calls the actual
[fold service](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold_interpreter.py)
with effect_outcome_transition, effect_outcome_failure, request-a and worker-a/
generation-one authority. The first fold uses fixed event ID inverse-fold-a.
The service validates current request/attempt authority, applies the Core fold,
checks intent evidence and, for a changed state, writes event/outcome truth and
compares-and-sets the attempt in a transaction. This is real fold-service invocation
over a fabricated outcome, not execution of an inverse runtime operation.

For ABANDONED, the helper performs a second service call with an ABANDONED transition
and EffectRecoveryDecision inverse-recovery-a. It identifies the first call's
uncertain outcome fingerprint and uses synthetic d-times-64 recovery evidence.
That command has no outcome or failure value and uses event ID inverse-abandoned-a.
The first fold may already be committed if the second call fails; the pair has
no enclosing all-or-nothing transaction. The historical uncertain outcome remains
separate from the later recovery event/state, rather than becoming a fabricated
direct ABANDONED provider result.

Both fold calls discard their results and return None; there is no final-state
assertion inside fold_bound_attempt. Fixed event/decision IDs and hard-coded
coordinates do not promise safe reuse for arbitrary prior states or repeated
distinct folds. Errors propagate to consumers, which decide which terminal state
or next-step rejection should be observed afterward.

binding_snapshot returns five groups: all binding columns ordered by program/
position; selected STEP_COMPENSATION_STARTED event fields; selected attempt fields
for attempt > 1; selected intent fields/preimage for attempt > 1; and selected
outcome fields/preimage for attempt > 1. SELECT * makes the binding portion follow
table column layout. The event portion omits occurred_at and non-start compensation
events, and attempt selection omits such fields as request fingerprint and fence.
These queries are not scoped to just program-a or run-a.

source_truth_snapshot returns selected attempt and outcome fields for attempt one
across the database, ordered by run/activity. That identifies the base seed's
source effects by number, not by consulting each program binding; a generalized
source at a later attempt would not be included. It omits source intent rows,
full event payloads, workspace pointers and other durable fields. Both snapshot
helpers issue separate autocommit reads, not one consistent snapshot transaction,
and do not redact/bound or print selected preimages. Equality is evidence only
for their selected observations.

Selected
[compensation-attempt consumers](../../../../control-plane-kit-operations/tests/test_postgres_failed_run_compensation_attempt.py)
assert first inverse identity/prior/event kind, unchanged source snapshot and
workspace graph pointers. They use fold_bound_attempt to test five blocking prior
states, missing/incongruent success evidence, successful progression to position
two and replay of each selected terminal state. STARTED is prepared by starting
without calling the fold helper. Those consumer assertions establish the tested
behavior; helper availability alone is not coverage of every binding/status pair.

A selected coordinator consumer borrows the inherited seed and these snapshot
methods while testing compensation isolation. Its broader harness and the full
compensation-attempt suite are outside this companion's review. In particular,
this fixture supplies neither a runtime adapter nor a provider-dispatch assertion,
and source-snapshot equality is narrower than universal source immutability.

Read depth: the complete 288-line fixture and every helper were read, with retained
full base fixture459 and admission owner529 context. The complete compensation-
attempt owner488 and binding store were read. Selected actual fold command/service,
outcome projection/failure helpers and consumer methods were checked; the full fold
owner/service and full consumer suites were not reviewed for this slice. Validation
was documentation-only: local links, whitespace and frozen-source comparison. No
application imports, tests, database/provider calls, credential access,
source/inventory edits or publication were performed.
