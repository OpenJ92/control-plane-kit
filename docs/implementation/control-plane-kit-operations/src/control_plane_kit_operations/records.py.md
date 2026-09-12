Source: [control-plane-kit-operations/src/control_plane_kit_operations/records.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py).
Maintain this document alongside its source file. When record responsibilities,
validation, fingerprints or consumer assumptions change, verify and update this
companion in the same change.

This 1,452-line module defines Operations values for workspace and graph truth,
operator intent, approval, execution admission, run history and observations. It
constructs and checks values; it does not open a transaction, authenticate a caller,
persist a row, call a provider or execute compensation. A successfully constructed
record is not evidence that the referenced objects exist or that an operation is
authorized. Those relationships belong to the services and stores consuming it.

The complete owner was read. Contract-bearing imports were inspected at selected
depth: Core RunId and its grammar, ActivityId and its grammar, EffectAttemptIdentity,
approval subject constructors, lifecycle scope/contract construction, probe-outcome
admission and graph-codec entry points; Operations temporal validation and lease
fence owners were read completely. The package-root records import block was checked.
This is not a full audit of Core, every transitive dependency or every record consumer.
The selected consumer and test reads below state the limits of the supporting review.
No source or test was imported or executed for this companion.

Most records are frozen dataclasses without slots; FailedRunCompensationAttemptBinding
also has slots. Freezing prevents ordinary field reassignment, but Mapping fields
are generally retained without defensive copying, and nested values are often
accepted with isinstance. This is not uniform deep immutability or universal
revalidation of forged nested objects. The module has no explicit __all__; the
package root imports a selected record surface rather than every name defined here.

The common _validate_text rule accepts a nonempty str instance of at most 512
characters and rejects characters below U+0020. It does not require trimmed text,
reject str subclasses, reject DEL, or enforce UTF-8 encodability. Most timestamps
use this text rule rather than timestamp parsing. Exact integer checks exclude bool;
their upper bounds vary by family. These differences matter when a stronger caller
or persistence boundary validates the same field again.

Run and activity identities use Core's exact-string ASCII grammar instead:
`[A-Za-z0-9][A-Za-z0-9._:-]{0,199}`. The local wrappers translate rejected values to
fixed OperationsRecordError messages after leaving the caught exception handler.
They do not retain the rejected candidate in the error chain. This guarantee is
local to these wrappers, not every error path in this module.

WorkspaceRecord retains lifecycle, current/desired authored graph pointers,
current/desired realized projection pointers, metadata and a nonnegative desired
revision. It validates individual optional pointers without requiring each authored
and realized pair to be present together. Its lineage properties return None when
either member is missing, otherwise GraphProjectionLineage, which itself only checks
the two bounded identities. Neither value establishes database membership.

GraphVersionRecord holds a positive version, workspace/graph identity, descriptor,
creator/time and metadata. Its constructor checks Mapping shape rather than decoding
the graph. Its from_graph factory uses DEFAULT_GRAPH_CODEC.encode on DeploymentGraph.
RealizedGraphProjectionRecord adds source authored identity, IDENTITY or
DELEGATION_VERIFIER kind, a projection key of at most 256 characters, and a lowercase
SHA-256 digest. It decodes the descriptor, re-encodes it, requires equality with the
supplied mapping and recomputes the digest. The actual
[Core codec](../../../../../control-plane-kit-core/src/control_plane_kit_core/topology/codec.py)
performs its own reference validation and round-trip checks; records.py adds no
separate deployment-readiness or authorization pass.

The projection digest covers workspace_id, source_authored_graph_id,
projection_kind, projection_key and graph_descriptor in sorted compact JSON with
ASCII escaping. It excludes projection_id, created_by and created_at. This binds
semantic projection material rather than every audit field, and is a module-specific
JSON representation rather than an RFC8785 claim. identity_for_authored decodes and
re-encodes the authored descriptor, uses kind/key identity, and assigns
`projection-<digest>` while copying creator/time. Direct construction with IDENTITY
does not independently compare the descriptor with a persisted authored row.

The selected save/get and reconstruction paths in
[postgres/graph_store.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/graph_store.py)
show the next boundary: save admits canonical database time, verifies the source
authored graph belongs to the workspace, inserts within the caller's transaction,
and handles an existing semantic identity by requiring the same digest. Readback
reconstructs RealizedGraphProjectionRecord. Those are store responsibilities; the
record alone provides neither transactional idempotency nor a durable relationship.

OperationSessionRecord groups actor, title, status and metadata with optional retry
key/fingerprint. OPEN forbids closed_at; CLOSED and CANCELLED require it. These are
presence rules, without chronological comparison. OperationActionRecord adds a
positive ordinal, a closed OperatorCommandKind or LifecycleOperationKind, and a
Mapping payload. Neither constructor applies the BoundedEvidence rules to arbitrary
metadata/payload, pairs optional retry fields, or recomputes their fingerprints.

SavedPreparationSourceRecord identifies the exact workspace/draft/revision admitted
by a session. Its three identities require exact str, nonblank stripped content,
at most 512 characters and no control characters or DEL; revision is an exact integer
from 1 through 2**63 - 1. It does not itself load the saved revision. The complete
[saved source store](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/saved_preparation_source_store.py)
was inspected: get reconstructs the record, insert requires its exact outer type,
and retained-row validation joins source, session and revision before calling the
separate saved-preparation validator. These additional checks must not be attributed
to the record constructor.

ActivityPlanRecord retains an ActivityPlan with session, base/desired graph IDs,
optional realized projections, status, creation time and nonnegative desired graph
revision. It neither compiles the plan nor verifies that the referenced graphs
produced it. Its lineage properties expose the authored/projection pair when a
projection ID is present. The previously inspected complete
[planning owner](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/planning.py)
is a real consumer: it constructs action and plan records while compiling and
pinning graph material inside its command transaction. Plan construction and
execution admission remain separate boundaries.

ApprovalRequestRecord accepts either ActivityPlanApprovalSubject or
GatewayKeyRotationApprovalSubject, together with required PolicyScope, RiskLevel,
an exact destructive bool, actor/time and optional comment/retry fields. Its plan_id
property projects a subject's plan_id when available. ApprovalDecisionRecord records
an APPROVED or REJECTED decision, request identity, actor, scope and time. These
constructors check types and fields; they do not derive risk or destructive status
from the subject, evaluate the principal, or prove that a decision authorizes a
particular execution. A reference to approval truth is not approval evaluation.

ExecutionRequestIdentity groups request/workspace/session/plan coordinates.
ExecutionRequestRecord adds status, requester/time, approval request/decision IDs
and ExecutionIdempotency. CLAIMED requires ClaimIdentity; every other request status
forbids it. ExecutionIdempotency only validates bounded key/fingerprint text and does
not require a SHA-256 value. ClaimIdentity validates worker text, generation from
1 through 2**63 - 1, and bounded claim/expiry text. Its fence property constructs
[ExecutionLeaseFence](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/execution_leases.py).
It does not compare expiry with database time or prove that this is the active claim.

AdmittedRun retains an execution request ID. RetryIdentity requires an exact attempt
from 1 through 2**31 - 1: attempt 1 forbids prior_run_id, later attempts require a
canonical prior run ID. ActivityRunRecord additionally forbids current and prior
run IDs from matching. It carries a plan, admission, retry, status, creation/start/
settlement times and BoundedEvidence metadata; it does not load a previous run or
verify the retry number against previous durable truth.

Run timing checks are explicit status/presence laws. CLAIMED forbids started_at.
RUNNING, PAUSED, SUCCEEDED, FAILED, COMPENSATING, COMPENSATED, PARTIALLY_FAILED and
UNCOMPENSATED_FAILURE require started_at. SUCCEEDED, COMPENSATED, PARTIALLY_FAILED,
UNCOMPENSATED_FAILURE and CANCELLED require settled_at; other statuses forbid it.
FAILED therefore remains unsettled, while CANCELLED permits absence of started_at.
These fields use bounded text, so the constructor does not establish timestamp
chronology or replay the event history to prove the status.

ExecutionCommandResultRecord retains a typed run, CoordinatorStatus, nonnegative
exact effects_attempted and optional canonical activity ID. It does not independently
prove that coordinator status agrees with the run's lifecycle status.
ExecutionCommandReceiptRecord adds stronger command-specific laws: a run/key, worker,
canonical ordered unique nonempty scopes, bounded claim generation, positive exact
max_effects, admission time and initial run, followed by optional completion/result.
The key has a 200-character ceiling. Scopes are retained as a tuple and must already
be sorted by scope.value without duplicates.

execution_command_intent_fingerprint hashes sorted compact ASCII JSON containing
domain `control-plane-kit.operations.execution-command.v1`, command
`deployment.execute`, run, worker, scopes, generation and max_effects. The effect
bound is encoded as a decimal string. The key, admission time and initial run are
outside this intent fingerprint. The receipt recomputes it and checks equality.
canonical_positive_decimal and its inverse require exact positive int and exact
canonical decimal str respectively, with no explicit fixed-width upper bound;
ordinary Python integer/string conversion limits still apply.

Receipt timestamps use the complete
[canonical UTC helper](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/_temporal.py):
exact UTC text ending in Z, calendar parsing and canonical round-trip, with seconds
or nonzero six-digit fractional precision. INCOMPLETE forbids completion/result.
COMPLETED requires a result for the same run, completion no earlier than admission,
effects_attempted no greater than max_effects, and equality of the initial/result
run lineage `(run_id, plan_id, request_id, retry attempt, prior_run_id, created_at)`.
Status, start/settlement times and metadata are outside that lineage tuple. The
receipt does not independently verify current lease authority or replay history.
Timestamp translation uses raise from None, which suppresses display of context
rather than clearing the __context__ object.

Selected receipt methods and row reconstruction in
[postgres/execution.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/execution.py)
show the persistence boundary: the bound is stored as decimal text, readback
reconstructs the receipt, and completion updates only an incomplete row matching
run/key/fingerprint before reconstructing its returned value. These selected reads
do not constitute a full execution-store or concurrency audit. The constructor's
two allowed shapes alone do not implement a durable one-way transition or replay.

BoundedEvidence stores deterministic JSON text and returns a fresh decoded dictionary
from descriptor(). from_mapping copies the outer mapping, validates the value tree,
serializes sorted compact JSON with the default ASCII escaping, and constructs the
record, which parses and validates again. Direct construction requires an object
whose reserialized text exactly matches canonical_json. Bounds are 4,096 encoded
bytes, depth at most 4 with root depth 0, at most 32 members per dictionary/list,
and at most 512 characters per string value. Allowed leaves are strings, None and
exact bool/int/finite float. Nested tuples and arbitrary non-dict mappings are not
accepted. These are this module's JSON conventions, not RFC8785 guarantees.

Evidence keys must be nonblank strings. Lowercased/hyphen-normalized key matching
rejects substrings password, secret, token, credential and private_key. This is a
key-shape filter, not a detector for secrets in arbitrary string values. Keys have
no separate explicit character-length/control rule beyond the overall encoding
limit; value strings have no general content or control-character filter. Errors
can include a path/key, and malformed JSON errors are chained. FailureEvidence
combines FailureCategory, bounded code/message and BoundedEvidence details; it does
not universally redact provider text placed in its message. Callers must supply
appropriate fixed or sanitized evidence rather than infer safety from these names.

ActivityEventRecord retains canonical run/activity coordinates, a positive ordinal,
closed event kind, occurrence text, bounded evidence and optional failure/recovery.
Core activity_event_scope decides whether activity_id is required or forbidden.
At import, this module derives failure-permitted kinds from Core's canonical
lifecycle contract set. Permitted failure evidence is optional; other kinds reject
it. The constructor does not enforce sequence uniqueness, monotonic time, or
consistency with a stored run or attempt.

RECOVERY_DECISION_RECORDED requires exact ExecutionLeaseRecoveryEvidence for the same
run, empty ordinary evidence and no failure. Other event kinds forbid recovery.
Recovery evidence admits five decisions: RETRY_AS_NEW_RUN retains the same fence;
ABANDON_EXPIRED_CLAIM has no replacement; renew-active, renew-expired and takeover
advance generation by one within the lease bound. Renewal preserves worker identity
and takeover changes it. RunId and fence outer types are exact, but this constructor
does not deeply reconstruct a forged nested RunId or fence. These value laws do not
prove expiry, operator authorization or agreement with the current stored claim.

FailedRunCompensationRecord retains program/workspace/request/run/plan/session/action/
event coordinates, actor/reason, exact outer FailureEvidence, four exact lowercase
SHA-256 strings and creation text. It validates hash spelling without recomputing
the authority-reference, command, evidence or program commitments. Its run_id uses
the general text validator, unlike ActivityRunRecord. FailedRunCompensationAttemptBinding
requires exact outer source/inverse EffectAttemptIdentity values for the same run
and activity, with inverse attempt exactly source + 1 and no overflow from the
maximum source attempt. It does not deeply reconstruct forged identities or prove
that a provider operation is the correct inverse. Neither record executes cleanup.

ObservationRecord separates observed evidence from desired graph truth. It retains
workspace/subject/status/time, BoundedEvidence, FRESH or STALE, and optional graph,
probe kind/outcome and endpoint context. Graph/kind/outcome must be all present or
all absent; PROCESS and READINESS forbid endpoint context, and Core checks the
kind/outcome pairing. It does not couple ObservationStatus to ProbeOutcome, derive
freshness from elapsed time, or look up the current graph. ObservationStaleReason
provides vocabulary for consumers rather than performing stale-state evaluation.

The previously inspected complete
[effect outcome owner](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_outcome_evidence.py)
and attempt owner demonstrate stronger consumers: they validate selected exact
nested types and correlate run/activity/effect identity, commitments and ordered
rows. Those checks cannot be assumed for an arbitrary ObservationRecord or
ActivityEventRecord. Similarly, the previously inspected server adapter/read
projections apply their own output policy; raw dataclass repr, Mapping payloads and
FailureEvidence are not universally redacted public responses.

The governing test inspection was deliberately bounded for this foundational file:

- The complete 293-line
  [realized projection suite](../../../../../control-plane-kit-operations/tests/test_realized_graph_projection_store.py)
  has six PostgreSQL tests for authored stability across projections, semantic
  idempotency/conflict, noncanonical timestamp rejection at the store, cross-workspace
  source rejection, deterministic durable identity projection, and joint rollback.
  Setup requires the package database, installs schema and truncates workspaces;
  these are not pure constructor tests or evidence of a real key rotation.
- The RunRecordLawTests portion of
  [test_run_lifecycle.py](../../../../../control-plane-kit-operations/tests/test_run_lifecycle.py)
  was inspected for timing/scope rejection, secret-shaped keys, tuple and infinite
  evidence rejection, uncertainty-abandonment scope, and failure permission for
  every ordinary canonical event kind. The full database lifecycle suite was not
  read for this slice.
- The identity matrix and record/command boundary tests in
  [test_authoritative_run_identity.py](../../../../../control-plane-kit-operations/tests/test_authoritative_run_identity.py)
  exercise valid 1/200-character IDs and malformed types, subclasses, prefixes,
  separators, controls/DEL and length, plus current/prior inequality. Selected
  assertions check bounded candidate-free errors with no cause/context. This does
  not make every field in records.py subject to the RunId grammar.
- Selected receipt/replay tests in
  [test_execution_coordinator.py](../../../../../control-plane-kit-operations/tests/test_execution_coordinator.py)
  preserve all scopes and max_effects 2**31 as stored text; reject changed command
  intent; preserve incomplete/failed-completion attention receipts; and reject
  fingerprint, effect-budget, run-lineage and completion-time mutations, including
  corrupted stored initial-run material. They combine PostgreSQL with a recording
  adapter; this slice did not inspect the whole coordinator suite or execute it.
- The evidence and intrinsic-event cases in
  [test_execution_lease_recovery_contract.py](../../../../../control-plane-kit-operations/tests/test_execution_lease_recovery_contract.py)
  cover four lease command decisions, invalid nominal/fence relations and maximum
  generation, and recovery-event exclusivity. This selected portion does not by
  itself cover every recovery service or the retained RETRY_AS_NEW_RUN branch.
- The previously read complete
  [outcome record suite](../../../../../control-plane-kit-operations/tests/test_effect_outcome_record_contract.py)
  supplies additional finite forged-record and row-correlation cases at that
  stronger consumer boundary. It is not exhaustive coverage of this module.

The key maintenance distinction is between a record's local invariant and a
consumer's authority, relational, persistence or projection invariant. Keep new
claims tied to the boundary that actually enforces them. This companion changes
documentation only: no new security surface, durable mutation, operational event,
provider action or live-test evidence is introduced.
