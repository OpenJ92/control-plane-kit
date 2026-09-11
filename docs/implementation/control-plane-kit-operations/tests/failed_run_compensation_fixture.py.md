Source: [control-plane-kit-operations/tests/failed_run_compensation_fixture.py](../../../../control-plane-kit-operations/tests/failed_run_compensation_fixture.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 459-line fixture prepares a failed deployment run with two persisted successful
effects and a later failed health-check history. It supplies command/service
builders, typed failure evidence, success construction, a deterministic ID source
and a selected original-truth query. It has no test methods and does not execute
compensation or runtime providers. Unlike fixtures that only synthesize terminal
states, its successful effects use the actual Core transition fold before being
persisted through stores.

The target failed_run_compensation module is imported optionally: only a
ModuleNotFoundError naming that exact module is converted to None. Nested missing
dependencies and other import errors propagate. All other imports remain ordinary,
including psycopg and actual record/outcome owners. require_contract asserts module
presence and returns it; it does not validate individual exports, signatures or
root identities. command and service then access the expected attributes directly.

Sequence keeps supplied IDs in a list, pops from the front and records each
successfully returned value. Exhaustion raises IndexError before appending a call.
It is deterministic test input, not a durable or globally unique ID allocator.
maxDiff=None changes unittest failure display. This fixture defines no safe-error
assertion helper, error redactor or class-access probe.

setUp requires CPK_OPERATIONS_TEST_DATABASE_URL, opens an autocommit psycopg
connection, installs schema and truncates cpk_workspaces with CASCADE. It does not
call seed_truth; consumers seed explicitly. tearDown truncates and then closes
the connection if still open. These cleanup operations are sequential, so a
truncation exception can prevent close. No database operation was performed during
this documentation review.

unit_of_work supplies an actual
[PostgresUnitOfWork](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
with a fresh psycopg connection. Its commit method requests commit on successful
context exit; exceptions or uncommitted exit roll back, followed by close. Direct
SQL on self.connection is separately autocommitted. The fixture does not configure
per-connection statement/lock timeouts or wrap all setup in one transaction.

service takes the caller's Sequence and constructs the actual command service with
this unit-of-work factory and a fixed clock returning 2026-08-25T12:00:00Z. Creating
the service does not execute it. The fixed clock supplies admission record time;
it is not a measurement of current database time or proof of a current execution
lease. The actual admission service's fresh path allocates program, event and
action IDs in that order before sampling this clock.

command constructs BeginFailedRunCompensation with workspace-a/request-a/run-a/
plan-a, expected graph-current, desired graph-desired, revision one and the synthetic
a-times-64 execution-intent fingerprint. It supplies operator-a with the synthetic
authority-reference-a and RecoveryScope.COMPENSATE, POST_EFFECT_FAILURE, freshly
constructed source failure and idempotency key compensate-a. Remaining keyword
changes overwrite these defaults after they are constructed; related coordinates
or seeded truth are not automatically recomputed.

failure returns actual FailureEvidence with TERMINAL category, code
runtime.effect-failed, a fixed runtime-failure message and bounded details
{"phase": "start"}. Separate calls construct equal values, not a shared observed
provider exception. The same failure shape is used in the seeded failed step,
failed run and default command so their evidence can agree.

The actual
[compensation command/admission owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/failed_run_compensation.py)
validates identifier shapes, nonnegative revision, fingerprint shape, exact
authority/reason/idempotency-key types and explicit COMPENSATE scope. Its source
failure check requires an exact FailureEvidence whose detail keys are among
activity_id, node_id, phase and runtime_id, with string values. It does not turn
the fixture's synthetic failure into runtime evidence. Authority-reference material
is excluded from the public command descriptor and its hash contributes to the
command fingerprint; this helper does not authenticate or resolve that reference.

seed_truth builds the three-activity dependency chain:

```text
StartRuntime(runtime-a) -> StartNode(node-a) -> WaitForHealthy(node-a)
```

The actual
[PlannedActivity compensation derivation](../../../../control-plane-kit-core/src/control_plane_kit_core/planning/activity_plan.py)
supplies StopRuntime and StopNode with desired-graph material for the first two
operations. WaitForHealthy has NoCompensationRequired. Those meanings come from
the Core operation mapping, not a separately maintained fixture inverse list.

The workspace row is inserted first through the autocommit connection. One later
unit of work seeds authored/identity-realized graphs, an open session, the plan,
its low-risk approval request/approved decision, a claimed execution request and
a FAILED run. The graph helper starts from empty DeploymentGraph values; this
setup does not supply a deployable runtime/product topology for the named targets.
The request stores the same synthetic a-times-64 execution fingerprint, worker-a/
generation one and fixed 2026 claim/expiry times.

That transaction writes nine ordered events: run opened and started; runtime
started/succeeded; node started/succeeded; wait-node started/failed; and run failed.
The two success pairs are accompanied by actual attempt, intent and outcome rows.
The failed wait step is represented only by its ordinary events and FailureEvidence;
the helper does not create a failed effect-attempt/outcome row for wait-node. The
run record is already constructed as FAILED rather than transitioned by a lifecycle
service during seeding.

After requesting commit for that transaction, seed_truth separately updates the
workspace's current/desired graph IDs, revision and realized-projection pointers
using direct SQL. The complete setup therefore spans the initial workspace insert,
the grouped seed transaction and a final pointer update. Later failure does not
automatically undo earlier commits. seed_truth does not truncate first or provide
idempotent repeat behavior; consumers reset before reseeding the fixed identities.

event constructs ActivityEventRecord for run-a using the supplied ID, ordinal,
kind, optional activity and failure. Its timestamp embeds the ordinal as a seconds
field under a fixed date/minute; it does not perform datetime arithmetic or make
arbitrary ordinals valid timestamps. Evidence uses the supplied truthy object or
an empty BoundedEvidence. For the nine seeded ordinals this produces fixed ordered
times; no wall-clock observation or independent timestamp assertion occurs here.

_add_success is a store-writing helper with no local transaction or commit. It
chooses StartRuntime only for activity_id start-runtime; any other supplied activity
ID selects StartNode(node-a), without a lookup against the plan. It builds an actual
Docker RuntimeEffectIntent with the fixed lineage, no authority reference, no
authority deliveries and no product material. The actual intent fingerprint is
used for attempt one under EffectAttemptFence(worker-a, one).

The helper constructs STARTED state directly and places its state fingerprint
in the start-event evidence. It then constructs a successful RuntimeEffectResult
whose effect ID is that start event and whose synthetic resource_fingerprint value
is the activity ID. ExecutionEffectOutcome wraps that result, and
effect_outcome_transition supplies its actual outcome transition to
[fold_effect_attempt](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/recovery.py).
The resulting SUCCEEDED state supplies the success-event commitment. This exercises
typed transformations over test values; no provider generated or verified the result.

It writes start event, success event, intent evidence, the succeeded attempt and
EffectAttemptOutcomeRecord in that order through the caller's stores. The outcome
record has an empty endpoint-observation tuple, matching the synthetic result's
absence of observations. The selected actual
[outcome contracts](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_outcome_evidence.py)
bind identity, request/outcome fingerprints, status and original/latest event
coordinates. The fixture delegates those constructors rather than independently
checking their laws. It ignores all five writer acknowledgements, including
insert_absent's possible None result; duplicate/rejection behavior is left to
constructors and stores, not asserted by this helper.

The selected actual
[compensation success projection](../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/failed_run_compensation_store.py)
joins successful outcome, attempt and event rows with matching coordinates and
fingerprints and orders by descending completion ordinal. Admission rejects
unresolved started/uncertain attempts and incomplete success evidence, selects
plan-declared compensations, then records a program, event and action and changes
the failed run to COMPENSATING in its transaction. It does not execute StopNode
or StopRuntime. For this seed, node completion six precedes runtime completion
four in the resulting compensation program.

original_truth returns two tuples of selected rows: current attempts with status,
outcome fingerprint and latest-event coordinates, and outcome rows with status,
fingerprint, direct-event coordinates and preimage. Both queries cover their whole
tables and order only by activity_id; they are not fully ordered for arbitrary
multiple-run/multiple-attempt data. They use separate autocommit reads and omit
intent rows, full event payloads, run/request/workspace state and other attempt
fields. Equality is a selected original-effect observation, not an atomic complete
snapshot or proof that every durable field remained unchanged. The preimage is
neither redacted nor bounded by this helper, and the helper does not print it.

The selected
[admission consumer](../../../../control-plane-kit-operations/tests/test_postgres_failed_run_compensation.py)
asserts the two reverse-order steps, StopNode/StopRuntime operation types, event
ordinal ten, COMPENSATING status and unchanged original_truth. Its own broader
snapshot helper adds data beyond this fixture. Selected rejection cases alter
scope/lineage, mark an attempt uncertain or remove success outcome evidence; those
consumer assertions, not seed_truth, establish rejection behavior. A replay case
constructs a new service with failing clock/ID callables over persisted admission
truth; it does not restart a process.

The selected descendant
[compensation-attempt fixture](../../../../control-plane-kit-operations/tests/failed_run_compensation_attempt_fixture.py)
calls seed_truth and actual admission before extending claim dates for later
attempt work. A selected coordinator consumer also reuses the seed while replacing
its run-failed history. These uses do not change what the base fixture itself
executes or establish full coverage of either consumer.

Read depth: the complete 459-line fixture, every helper and the complete 529-line
compensation command/admission owner were read. Selected actual Core compensation
mapping/fold, outcome value/record/transition, success-store projection, graph seed,
authority and PostgresUnitOfWork boundaries were checked. Selected admission,
descendant and coordinator consumers were read; no full consumer suite, full
outcome owner or full compensation store review is claimed. Validation was
documentation-only: local links, whitespace and frozen-source comparison. No
application imports, tests, database/provider calls, credential access,
source/inventory edits or publication were performed.
