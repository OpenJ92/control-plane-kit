Source: [control-plane-kit-operations/src/control_plane_kit_operations/failed_run_compensation.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/failed_run_compensation.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 529-line owner admits one failed-run compensation program and persists its
provenance, action, event and run-state change. It defines the command, result,
service and error vocabulary. Core owns the program/evidence values and plan
compensation meanings; PostgreSQL stores own their representation and row mutations.
The service composes those boundaries inside one unit of work. It does not execute
inverse operations, allocate inverse attempts or settle the compensation program.

BeginFailedRunCompensation is a frozen slots dataclass with twelve fields: workspace,
request, run and plan identity; expected current/desired graph coordinates and
desired revision; execution-intent fingerprint; recovery authority; reason; source
failure; and idempotency key. Five string identifiers must be exact str values of
one through 200 ASCII characters, beginning alphanumeric and continuing with
alphanumeric, dot, underscore, colon or hyphen. run_id must be exact RunId. Revision
must be exact int, excluding bool, and nonnegative; this constructor adds no upper
bound. The execution fingerprint must be exact lowercase 64-character hex text.

Authority must be exact RecoveryAuthority with explicit COMPENSATE scope. Reason
must be exact FailedRunCompensationReason, whose current Core member is
POST_EFFECT_FAILURE. The key must be exact IdempotencyKey. Source failure must be
exact FailureEvidence; its detail keys must be a subset of activity_id, node_id,
phase and runtime_id, and every detail value must be exact str. Empty details are
allowed. Imported failure/evidence constructors supply their own bounds; the local
key restriction is not a detector for arbitrary secret text placed in permitted
values or the failure message.

The selected
[RecoveryAuthority](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/execution_lease_recovery.py)
constructor bounds actor and reference text to one through 512 characters, rejects
control characters below code point 32 and canonicalizes an exact tuple of exact
RecoveryScope values by sorting/deduplication. authority_reference is excluded from
that value's repr. This owner accepts that admitted value; it neither resolves the
reference nor authenticates an operator against an external authority service.
It does not check an execution-worker fence, active claim expiry or approval
decision as part of this operator compensation-admission command.

descriptor returns command kind, public coordinates/revision, execution fingerprint,
actor, reason, full failure descriptor and key. It omits the private authority
reference and scope tuple. authority_reference_fingerprint is SHA-256 of the
reference's UTF-8 bytes. intent_fingerprint hashes an object containing the public
descriptor and that private-reference hash. Thus a changed reference changes
idempotency intent even when the public descriptor is identical; changing other
scopes while retaining COMPENSATE does not independently enter this fingerprint.
_fingerprint uses sorted compact JSON with ensure_ascii=True, encoded as ASCII;
there is no extra domain prefix or generic RFC canonicalization layer here.

The public command descriptor still includes caller-supplied source-failure text.
Omitting a reference and hashing provenance does not make the entire descriptor,
result or exception stream a universal secret-redaction boundary. The private
reference remains in the in-memory command's authority value; the service stores
its fingerprint, not its raw bytes. Hashes are commitments to supplied material,
not proof that an authority or runtime observation is authentic.

FailedRunCompensationConflict derives from RunLifecycleConflict. NotFound and
IdempotencyConflict specialize that conflict; Denied derives from both
RunLifecycleDenied and InvalidOperationCommand. Invalid command shapes generally
raise InvalidOperationCommand, with missing COMPENSATE raising Denied. Selected
missing store coordinates are wrapped with chained causes. The service does not
normalize all OperationsRecordError, constructor, clock, ID, database or dependency
failures, and no universal cause/context suppression is implemented.

FailedRunCompensationResult is a frozen slots dataclass containing record, program,
run, event, action and replayed=False. It has no post-init validation or independent
proof that those values were committed together. execute requires the exact command
class before opening its injected unit-of-work factory, calls _admit, requests
commit and returns through context exit. It does not rerun the command constructor
or recursively reconstruct potentially forged nested values at execution time.
Ordinary construction is the intended command-admission path.

_admit first locks the execution request and checks its workspace and plan against
the command. Missing request becomes NotFound. It then takes a session/key-scoped
action-idempotency lock and looks up an existing action. The selected PostgreSQL
adapter implements that lock with pg_advisory_xact_lock over a hash of the session
and key, in addition to the already-held request row lock. An existing action
enters _replay immediately; fresh-only checks below are not repeated on that branch.

Fresh admission locks session, run and workspace, and reads the plan. It requires
an open session owned by the workspace, matching plan/session and graph/revision
coordinates, and a FAILED run bound to the plan and execution request. Workspace
current/desired pointers and revision must equal the command, as must the request's
execution-intent fingerprint. These checks compare stored coordinates; the owner
does not recompile the deployment graph, recompute the execution plan or query a
provider to establish live state.

events_for_run supplies ordinal-ordered history. The final event must be RUN_FAILED
with FailureEvidence equal to the command. The compensation projection must report
zero STARTED/UNCERTAIN attempts, and its count of SUCCEEDED attempts must equal the
number of complete projected successes. The actual
[compensation store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/failed_run_compensation_store.py)
joins outcome, attempt and direct event coordinates, matching success statuses and
request/outcome fingerprints plus STEP_SUCCEEDED event kind. It orders by descending
completion ordinal, then activity identity. Admission uses this selected projection;
it does not read and fully revalidate each source intent/outcome preimage here.

For each projected success, the owner looks up its activity in the admitted plan.
An exact Compensate contributes a consecutive step with that success, inverse
operation and material source. Exact NoCompensationRequired contributes no step
or selected evidence. Exact NonCompensatable, an unknown compensation value or a
missing activity rejects admission. A resulting empty program is rejected. Thus
the program covers the selected compensatable successes, not every successful
activity irrespective of recovery meaning.

The selected Core plan mapping gives StartNode -> StopNode and StartRuntime ->
StopRuntime with desired-graph material; stopping those objects maps back to starts
with base-graph material. This owner consumes activity.compensation rather than
maintaining its own operation switch or discovering runtime inverses. In the
reviewed fixture, node completion six and runtime completion four become positions
one and two in that order.

The actual
[Core compensation values](../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/compensation.py)
validate nonempty selected evidence in the failed run, unique identities, unique
descending completion ordinals, and contiguous steps whose source effects exactly
equal the evidence tuple. Each step validates operation representation and material
source. The program descriptor carries schema cpk.failed-run-compensation-program
and version one; its fingerprint covers ID, evidence and steps. These are imported
value laws, not independently restated assertions in every Operations test.

After building lineage and evidence, fresh admission allocates program ID, event ID
and action ID, then samples the injected clock once. The event uses the next run
ordinal, RUN_COMPENSATION_STARTED and bounded program ID/fingerprint evidence.
The action uses the next session ordinal, BEGIN_COMPENSATION, actor, idempotency
key and command fingerprint, with decision/program/run/event coordinates in its
payload. Both share the supplied timestamp. Store ordinal helpers lock their parent
run/session before choosing MAX(ordinal)+1. The injected clock is not a lease
observation or independently verified database time.

The record adds durable workspace/request/run/plan/session lineage, action/event
and actor coordinates, reason, full source failure, private-reference/command/
evidence/program fingerprints and creation time. Writes occur in this order:
event, action, program parent plus each step, then FAILED -> COMPENSATING run CAS.
The first three writer results are ignored; only a None run-update result is
explicitly rejected here. A non-None returned run is used without a local exact
acknowledgement comparison. The selected execution store also requires settled_at
IS NULL for this CAS, so a FAILED row with a settlement timestamp cannot pass that
store mutation even though the earlier owner check only tests status.

The actual
[PostgresUnitOfWork](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py)
defers connection commit until successful context exit, rolls back uncommitted or
exceptional exit, and attempts rollback on a commit exception before closing.
No partial program is intentionally committed by the service. There is no retry
loop, provider compensation for database failure or lost-commit-acknowledgement
resolver in this module. IDs/clock calls can already have been consumed when a
transaction fails; they are external callables, not rolled-back database state.
The service does not rewrite source attempts/outcomes or workspace graph pointers.

Replay first requires the action kind and command fingerprint to match, exactly
five payload keys, the BEGIN_COMPENSATION decision and requested run. A mismatch
is IdempotencyConflict. It then loads program/record through store readback, plus
the recorded event and run. Store readback reconstructs the Core program from its
JSON preimage and compares parent lineage, failure/evidence/program fingerprints
and ordered relational steps. Missing or invalid readback becomes a chained
compensation conflict.

The remaining replay checks bind record to command coordinates, actor, reason,
failure and private-reference fingerprint; action to record ID/session/actor/key/
time and program/event payload; event to the run, kind, time and exact program
evidence with no activity/failure/recovery; and run to record plan/request with
COMPENSATING status. Only then does it return replayed=True. No fresh ID or clock
is used and no admission writer is called, although the outer execute still
requests transaction commit and initial locking still occurs.

Replay does not reload fresh session/workspace/plan state or reproject current
source effects. It also requires the run still be COMPENSATING; this API does not
promise the same successful replay after later settlement or another run status.
Same-key congruent callers can observe one persisted program, while a competing
key reaches fresh admission and encounters the changed run state. The current
locking and tests support those cases; they are not a proof for arbitrary direct
SQL writers bypassing the service's locks and invariants.

The reviewed
[command contract suite](../../tests/test_failed_run_compensation_command_contract.py.md)
checks literal descriptor shape, reference-sensitive fingerprints, selected scope/
fingerprint/revision and hostile-value rejections. Its result-named test only checks
symbol presence and three source substrings, not result construction or transaction
behavior. The reviewed
[PostgreSQL suite](../../tests/test_postgres_failed_run_compensation.py.md)
checks the two reverse-order inverses, selected persistence/source preservation,
new-service replay without IDs/clock, reference and relational drift, seven rollback
injections, same/competing-key callers and selected rejections. Snapshot equality
is limited to the suite's queries; entry barriers do not force particular database
interleavings. Its restart wording means persisted new-service reads, not process
restart, and commit injection occurs before the underlying connection commit.

The reviewed
[base fixture](../../tests/failed_run_compensation_fixture.py.md)
builds synthetic successful source effects and failed-run history; it supplies no
provider evidence. The subsequent attempt owner separately binds and starts inverse
attempts, checks active execution authority and revalidates source intent/outcome
truth. Admission's program/action/event records supply that handoff and operational
history; this owner has no routes, logging output, network exposure or automatic
resource cleanup.

Read depth: the complete 529-line owner and every helper/export were refreshed,
with retained full command184, base fixture459 and PostgreSQL suite569 context.
The full Core compensation-value owner and PostgreSQL compensation store were read;
selected authority/record, action idempotency/ordinal, request/event/run-CAS and
planning inverse-mapping boundaries were checked, retaining full unit-of-work
context. No full execution/history/records/planning dependency review is claimed.
Validation was documentation-only: local links, whitespace and frozen-source
comparison. No application imports, tests, database/provider calls, credential
access, source/inventory edits or publication were performed.
