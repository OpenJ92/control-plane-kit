Source: [control-plane-kit-operations/src/control_plane_kit_operations/failed_run_compensation_attempt.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/failed_run_compensation_attempt.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 488-line owner binds the next admitted compensation step to one fresh inverse
effect attempt. It defines the start command, result variants, service and errors,
and re-exports the binding record. It validates current execution authority and
persisted program/source/inverse truth, then writes one compensation-start event,
intent, attempt and binding in a transaction. It does not execute the inverse,
record its runtime outcome, advance the run to a final status or mutate graph
pointers. Those effects belong to other owners.

The preceding
[compensation admission owner](failed_run_compensation.py.md)
records a program, action/event provenance and COMPENSATING run status. This owner
consumes that program and uses ExecutionWorkerAuthority with EXECUTION_OPERATE and
an ExecutionLeaseFence; it does not accept the earlier operator RecoveryAuthority
or re-resolve its private reference. Current execution-request approval/lease
checks are explicit and apply before either fresh binding or existing replay.

StartFailedRunCompensationAttempt is a frozen slots dataclass with program_id,
position, intent, authority and fence. Its constructor requires exact nonempty str
for program_id but adds no local length, character or whitespace-only restriction;
position is exact positive int with no local upper bound. Intent, authority and
fence must be their exact public types. EXECUTION_OPERATE must be present and
authority.worker_id must equal fence.worker_id. It does not recursively reconstruct
these nested values. execute checks the exact command class but does not rerun
the constructor or provide a complete forged-object admission boundary.

The selected authority constructor requires nonblank worker text and canonicalizes
valid policy scopes. ExecutionLeaseFence accepts nonempty worker text up to 512
characters without control characters, and exact integer generations one through
2**63-1. Fresh binding later constructs the stricter Core EffectAttemptFence, whose
worker text is nonblank, UTF-8/PostgreSQL-compatible and at most 256 characters.
This owner does not translate every imported constructor failure into an attempt
error. Passing the outer command constructor is not proof that all later stored
or Core value constraints will accept the command.

FailedRunCompensationAttemptStartResult is a frozen slots dataclass carrying binding,
attempt, intent and replayed. NewlyBoundCompensationAttempt and
ExistingCompensationAttemptBinding are ordinary subclasses with no own dataclass
decorator, slots declaration or post-init validation. The service constructs them
with False and True respectively; the classes themselves do not enforce that
boolean or validate cross-field coherence. Their inherited base declaration should
not be described as an independently closed validated result union.

Conflict, Denied and NotFound are siblings under FailedRunCompensationAttemptError,
which derives directly from RuntimeError. Denied is used by command construction
for malformed/missing authority, scope, fence or worker agreement. Missing parent
program becomes NotFound; invalid parent readback becomes Conflict. Durable lease,
approval or lineage rejection also becomes Conflict, not necessarily Denied.
Selected KeyError, StopIteration and OperationsRecordError failures are wrapped
with chained causes. Other constructor, SQL, clock, ID and dependency failures can
propagate; this is not a universal error-redaction/normalization layer.

execute opens the injected unit of work, calls _start, requests commit and returns
through context exit. _start first calls the compensation store's get_for_update;
the parent row is locked and its persisted preimage/relational steps are decoded
and checked by that store. It then validates lineage before loading ordered
bindings. The owner relies on actual store/record contracts rather than explicitly
checking the exact type of every returned value or reconstructing all decoder
results at each call site.

_validate_program_lineage obtains an execution-request lease observation under
request lock, locks run and workspace, and reads session, plan, approval decision,
admission event and session actions. The action is selected by its recorded ID;
missing lookup data becomes an incomplete-lineage conflict. The selected execution
store compares lease_expires_at <= database clock_timestamp to set expired. Missing
claim is rejected by that store before the owner examines it. This is a database
time observation, not a clock injected by the caller.

The command must match a nonexpired CLAIMED request with the same claim fence and
worker. Request coordinates must match the parent record; the recorded approval
decision must exist, be APPROVED and refer to the request's approval request. The
run must match plan/request and remain COMPENSATING. Session must be open and owned
by the same workspace. Plan and workspace graph coordinates/revision must match
program lineage, and the request execution-intent fingerprint must match that
lineage. Record/lineage identities are also compared.

Admission provenance checks in this helper compare event ID/run/kind and action
ID/session/actor/creation time against the parent record. They do not repeat all
checks in the earlier compensation command's replay function: there is no local
comparison of the admission action payload, action kind/key/fingerprint, admission
event payload/time or operator-reference fingerprint here. The store supplies its
own parent/program consistency checks. These layers must not be summarized as
every possible provenance field being revalidated by this helper.

Bindings must have positions exactly one through their current count. An already
bound requested position enters replay immediately. Otherwise, position must equal
count plus one and not exceed the program's step count. Every earlier binding is
validated as a succeeded prior before a new binding can start. The parent lock
serializes cooperating starts for that program; there is no new idempotency key,
retry loop or independent worker lease renewal in this owner.

_source_truth locks the source attempt and reads the outcome for the program's
recorded completion event plus source intent. Source must still be SUCCEEDED with
the expected identity, request/outcome fingerprints and STEP_SUCCEEDED completion
event ID/ordinal. The outcome's attempt must equal that source, and its request/
outcome fingerprints must agree. Source intent identity/fingerprint and workspace,
request, run, plan and graph coordinates must match program lineage. Missing or
invalid decoded truth is wrapped as conflict; these checks do not query a provider.

Expected inverse intent is exactly:

```python
replace(source_intent.intent, operation=step.operation)
```

The operation comes from the admitted step. Kind, runtime kind, source coordinates,
authority reference/deliveries and products remain whatever the persisted source
intent contains. This owner does not separately interpret step.material_source to
reload a graph or rebuild material, and does not recompute the step's compensation
from the plan's activity operation. The parent program's admitted meaning and
stored source intent supply that handoff. Fresh command intent must equal this
expected value before an inverse is constructed.

Inverse identity keeps source run/activity and increments the source attempt by
one; source identity becomes prior_attempt. The imported
[binding record/store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/failed_run_compensation_attempt_store.py)
preserves program/position plus both identities, with lookup in either direction.
The binding value validates the same-run/activity adjacent-attempt law, including
the source maximum-attempt limit; database identity/unique/foreign-key constraints
also constrain persistence. These are distinct from selecting the next program
position: program position and effect-attempt number are different coordinates.

The owner fingerprints command intent and invokes Core fold_effect_attempt with
no existing inverse state, a STARTED transition, prior source identity and the
translated Core fence. This pure fold constructs STARTED state; it does not
perform external effects. The event then evaluates the injected ID factory once,
gets the next run event ordinal and queries clock_timestamp again through
_observed_at. This timestamp is a second observation after lease validation, not
reuse of the first observation or an additional lease-expiry check at commit.

STEP_COMPENSATION_STARTED carries the activity identity and bounded effect_attempt
evidence containing attempt number and state fingerprint. The same event is the
attempt record's original and latest transition event. The
[intent evidence record](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_intent_evidence.py)
binds inverse identity, original event and intent through its own exact-shape and
encode/decode validation. Its repr excludes intent/event fields, but the returned
record still provides access to the protected intent; result availability is not
authorization to publish that data. This owner adds no generic public descriptor
or redactor for arbitrary intent/evidence/exception content.

Writes occur in a fixed order: add event, insert intent, insert_absent attempt,
insert binding. The attempt store implements INSERT ... ON CONFLICT DO NOTHING
and returns None for a conflict, which the owner rejects. Event, intent and binding
acknowledgements are ignored, and a non-None attempt acknowledgement is not compared
to the expected record. Thus this owner does not have the complete acknowledgement
validation used by some other interpreter paths. The fresh result contains the
locally constructed values, not a full post-commit database readback.

The actual
[PostgresUnitOfWork](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
commits only on successful exit after commit is requested, otherwise rolls back,
and closes in finally. The four owned inserts share this transaction. ID allocation
is not transactional and can already have occurred on failure. The owner does not
write an outcome, alter source attempts/outcomes, advance run status or change
workspace pointers. It has no recovery loop for a lost commit acknowledgement,
and rollback/close failures are not universally normalized here.

_inverse_truth underlies both replay and prior-success checks. It validates binding
position against the program, rechecks source truth, derives the expected adjacent
inverse, locks that inverse and reads its intent and reverse binding. Reverse lookup
must equal the supplied binding; source/inverse/prior identities must agree; the
original event must be STEP_COMPENSATION_STARTED; intent must share that event,
identity and expected inverse value, with its fingerprint equal to attempt state.

Replay additionally matches the bound attempt's historical fence to the command
and compares stored intent/value fingerprint to the command. It returns the current
attempt without writing a new event, intent, attempt or binding, and allocates no
new ID. It still performs the earlier current lease/approval/lineage observation
and outer commit. A rotated lease therefore is not silently adopted. Replay does
not require that binding's current status be SUCCEEDED, does not inspect its own
inverse outcome record, and does not run the prior-success loop for other bindings.
It remains subject to source/inverse decoder invariants and COMPENSATING run state.

For progression, _require_succeeded_prior_binding first applies _inverse_truth and
then requires SUCCEEDED with STEP_COMPENSATION_SUCCEEDED as latest event. It loads
the outcome for that inverse/latest event and compares full attempt, identity,
request fingerprint and outcome fingerprint. STARTED, FAILED, UNSUPPORTED, UNCERTAIN
or ABANDONED do not satisfy this condition. This predicate does not itself compare
the prior inverse fence to the new command; current command authority was checked
earlier. No missing success outcome is fabricated to permit the next step.

Outcome recording belongs to the separate effect-attempt fold service. The reviewed
[fixture](../../tests/failed_run_compensation_attempt_fixture.py.md)
uses that service with synthetic results; abandonment follows a committed uncertain
fold with a separate recovery fold. Those transformations explain the later states
observed by this owner, but are not execution methods on the attempt-start service.
The original program remains available for structured history and downstream
execution; this owner has no route, provider dispatch, log output or cleanup task.

The reviewed
[PostgreSQL suite](../../tests/test_postgres_failed_run_compensation_attempt.py.md)
asserts selected language/schema shape, first linked inverse, duplicate/same-step
concurrent callers, five blocking prior states, missing/corrupt success truth,
successful second-step admission and replay of five settled states. It also checks
six changed-truth cases, four after-SQL rollback injections plus a pre-connection-
commit failure, bidirectional persisted lookup and selected drift rejection.
Snapshot checks omit several fields/tables; timed thread joins do not force a
database interleaving or stop blocked workers. must-not-allocate is ordinary ID
input without a count assertion. Its bounded/redacted-named test checks only nine
source substrings, not payload bounds or secret redaction. New-service/store reads
do not establish an actual process restart, and no provider was used by this review.

Read depth: the complete 488-line owner and every helper/export were refreshed,
with retained full fixture288, PostgreSQL suite518, parent admission529, binding
store and unit-of-work context. Selected authority/fence constructors, lease
observation SQL, Core first fold/fence bounds, intent record and attempt insert
paths were refreshed. No full execution/lifecycle/recovery dependency review is
claimed. Validation was documentation-only: local links, whitespace and frozen-
source comparison. No application imports, tests, database/provider calls,
credential access, source/inventory edits or publication were performed.
