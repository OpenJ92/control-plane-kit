Source: [control-plane-kit-operations/src/control_plane_kit_operations/deployment_program_projections.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/deployment_program_projections.py).
Maintain this document alongside its source file. When the source or relevant
imported value contracts change, verify and update this companion in the same change.

This 528-line module owns 21 pure deployment-program projection variants and their
PEP 604 union, DeploymentProgramProjection. __all__ contains the union followed by
those 21 variants in declared order, and the Operations root reexports them. The
module defines value shapes and descriptors; it does not compute a projection from
history, select the next stage, authorize a command or execute an operation. A
constructed completed/ready/running value is not proof that the named event or
runtime state exists.

The private _Projection base and all public variants are frozen slots dataclasses.
Every instance has reference as its first field. stage and the projection tag are
class attributes, not constructor fields; the base annotates them as ClassVar.
Dataclass freezing restricts ordinary instance assignment, not class mutation,
object.__setattr__ bypasses or recursively forged imported objects. The union lists
the supported public variants but does not seal Python subclassing or provide an
exhaustive runtime dispatch/registration mechanism.

The common descriptor allocates a dictionary containing projection, reference's
descriptor and stage.value, then each variant adds its own public fields. RunId
and ActivityId become value strings, enums become their value strings, and an
effect-attempt identity contributes its descriptor. There is no inverse decoder,
schema version field, durable read, callback or record write here. Descriptors are
representation values rather than authenticated observations.

Every row below also contains the shared reference field. Stage names are the
actual DeploymentProgramStage members; tags are the literal projection descriptor
values. Status restrictions are constructor laws, not runtime-state queries.

| Variant | Stage / tag | Additional fields and restrictions |
| --- | --- | --- |
| DeploymentCompleted | ADVANCE / completed | event_id |
| DeploymentSessionStopped | PLAN / session-stopped | session_status: CLOSED or CANCELLED |
| DeploymentPlanStopped | PLAN / plan-stopped | plan_status: SUPERSEDED or CANCELLED |
| DeploymentNoChanges | PLAN / no-changes | None |
| DeploymentReviewBlocked | PLAN / review-blocked | None |
| DeploymentApprovalRequestReady | APPROVE / approval-request-ready | None |
| DeploymentApprovalRequired | APPROVE / approval-required | approval_request_id |
| DeploymentApprovalRejected | APPROVE / approval-rejected | approval_request_id, approval_decision_id |
| DeploymentReadinessRequired | ADMIT / readiness-required | approval_request_id, approval_decision_id, exact ActivityId |
| DeploymentAdmissionReady | ADMIT / admission-ready | approval_request_id, approval_decision_id |
| DeploymentClaimReady | CLAIM / claim-ready | execution_request_id |
| DeploymentExecutionStopped | CLAIM / execution-stopped | execution_request_id; execution_request_status: CANCELLED or ABANDONED |
| DeploymentExecutionReady | EXECUTE / execution-ready | exact RunId |
| DeploymentExecutionRunning | EXECUTE / execution-running | exact RunId |
| DeploymentExecutionPaused | EXECUTE / execution-paused | exact RunId |
| DeploymentEffectInFlight | EXECUTE / effect-in-flight | exact RunId, live run_status, congruent exact EffectAttemptIdentity |
| DeploymentRecoveryRequired | EXECUTE / recovery-required | exact RunId, live run_status, congruent exact EffectAttemptIdentity |
| DeploymentCompensationInProgress | EXECUTE / compensation-in-progress | exact RunId |
| DeploymentExecutionFailed | EXECUTE / execution-failed | exact RunId; run_status: FAILED |
| DeploymentExecutionSettled | EXECUTE / execution-settled | exact RunId; run_status: COMPENSATED, PARTIALLY_FAILED, UNCOMPENSATED_FAILURE or CANCELLED |
| DeploymentAdvancementReady | ADVANCE / advancement-ready | exact RunId |

_reference requires exact
[DeploymentProgramReference](deployment_program.py.md), rejecting subclasses. Normal
reference construction checks workspace and plan IDs; this module does not repeat
those nested checks or verify their ownership. Event, approval-request/decision and
execution-request IDs use _bounded_identity: exact str, nonempty, at most 512
characters and no code point below 32. Type is checked before len, excluding an
ordinary str subclass's hostile length hook. The helper does not strip whitespace,
reject DEL, enforce an ASCII identifier grammar, require UTF-8 encodability or resolve
the ID in a store. Such scalar IDs can contain text that would be sensitive if a
caller put sensitive material there.

All nine variants carrying run_id require exact
[Core RunId](../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/run_identity.py).
Normal RunId construction uses an exact-string ASCII grammar: one initial
alphanumeric character followed by up to 199 alphanumeric, dot, underscore, colon
or hyphen characters. DeploymentReadinessRequired similarly requires exact
[Core ActivityId](../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/activity_plan.py),
whose current identity grammar has the same shape. These projection guards check
nominal outer types, not a second parse of retained value strings. They do not prove
that a run/activity belongs to the referenced plan or workspace.

_status requires the exact enum family before accepted-subset membership. Equal
textual values from another StrEnum family cannot substitute for the required
status. Session and plan statuses come from the
[Operations records owner](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py);
request/run statuses come from
[Core lifecycle](../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/lifecycle.py).
The latter currently defines four request states and ten run states. The six stage
values come from Core's services vocabulary. Projection variants consume these
existing languages rather than defining new status enums or transition rules.

_live_attempt is shared by DeploymentEffectInFlight and DeploymentRecoveryRequired.
It accepts only RUNNING, PAUSED or COMPENSATING run status, requires exact
[EffectAttemptIdentity](../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/recovery.py)
and requires effect_attempt.run_id == run_id. This is value equality, not object
identity. Normal attempt construction requires exact RunId, a canonical activity
string and exact int attempt from one through 2,147,483,647, rejecting bool. Its
descriptor has run_id, activity_id and attempt.

The projection helper does not reconstruct that attempt, read its outcome/status,
validate a fence, check provider receipts or establish uncertainty. The two live
variants have the same field shape and accepted statuses; their distinct tags carry
the classification chosen elsewhere. No constructor distinguishes started from
uncertain effect attempts through actual effect-attempt state. Likewise run-only
variants such as execution-running, compensation-in-progress and advancement-ready
have no run_status field or success-history check at all.

The shared error is imported InvalidDeploymentProgramContract, a value-contract
error from the command owner. It is available as a module attribute but omitted
from this module's __all__. DeploymentProgramStateConflict belongs to interpretation
and is not imported here. Local invalid-type, text, status and run-mismatch messages
name fields without echoing rejected values. Descriptors and dataclass repr still
include admitted public coordinates. There is no secret registry, lexical secret
filter, encryption or universal redaction boundary, and exact outer-type checks
are not complete validation of arbitrarily forged nested values.

No authorization context, graph descriptor, readiness evidence body, provider URL,
failure body or execution capability is a declared projection field. This keeps
the normal shape small, but callers remain responsible for supplying public IDs.
The model does not turn a reference, approval ID or stage label into authority.
Changing a projection or serializing one cannot advance durable deployment state.

The fully read
[governing projection suite](../../../../../control-plane-kit-operations/tests/test_deployment_program_projections.py)
contains eleven tests and a case for every variant. It checks exact ordered module
exports/union arguments, root identity/reexport membership, no __dict__ and rejected
extra assignment, exact dataclass field order, class stage identity, absence of
stage from signatures and expected descriptor mappings. The descriptor witness
independently assembles the outer mapping but uses imported reference/attempt
descriptor methods for nested values. It does not independently validate every
nested descriptor implementation.

Every variant is tested with object/subclass reference substitutes and with each
required argument removed. Scalar ID paths accept 512 characters and reject object,
bool, empty, 513-character, newline-canary and hostile str-subclass inputs. All nine
run variants reject bare strings, RunId subclasses and objects. Seven status
matrices enumerate every member of their current enum universe and reject a wrong
family, raw string and object. Both live-attempt variants accept 200-character run
IDs at each accepted status and reject wrong outer type, attempt subclass and
different run. Readiness preserves a 200-character ActivityId by identity and
rejects a string, subclass and object.

Those tests cover ordinary construction and named boundaries. They do not forge
exact-type objects, mutate every declared field, prove recursive immutability or
test all identity-grammar bounds in the imported owners. The frozen test uses extra
attribute assignment; it does not independently demonstrate every dataclass
freezing path. Required-argument failures are ordinary TypeError assertions rather
than bounded contract-error checks.

The contract-error helper asserts InvalidDeploymentProgramContract, no cause/context,
combined str/repr length at most 512 and absence of nonempty supplied canaries.
The normal repr/descriptor check uses sample IDs and requires combined length at
most 4096 plus absence of seven forbidden substrings, including secret:// and
graph_descriptor. These are sample-value assertions, not a global maximum for
every admitted identity combination or proof that caller-supplied IDs cannot contain
those substrings. The dedicated hostile-text case does exercise rejection without
leaking its candidate.

The suite's source guard permits an import subset, rejects selected import-name
substrings and seven direct call names/attributes, and checks that deployment_program
does not import projections back. It does not prove a full transitive DAG, resolve
every alias/dynamic import or inspect effects in every imported descriptor method.
Its inventory checks select one legacy destination row, require owner operation
and require exports to be included in canonical_public_exports. This does not mean
every legacy public name in that row is implemented here. The selected inventory
row and actual root exports were inspected without changing them.

The actual
[preparation interpreter](deployment_program_interpreter.py.md)
returns only DeploymentNoChanges, DeploymentReviewBlocked or
DeploymentApprovalRequired. The inspected cpk-server preparation response helper
also accepts exactly those three types. Their existence and the broader union do
not establish an implemented progression interpreter for every projection variant.
The projection module itself contains no history fold, precedence algorithm,
transaction, retry/resume mechanism or provider boundary crossing.

Read depth: the complete 528-line owner, all helpers/exports and complete 788-line
projection test were read. Full RunId and private run/activity grammar modules,
selected ActivityId/EffectAttemptIdentity validators, all imported enum declarations,
root reexports and the selected inventory row were inspected. Full command and
preparation interpreter reading and the selected server response boundary were
retained. No full records, recovery, planning, lifecycle/server or transitive
dependency review is claimed. Validation was documentation-only: local links,
whitespace and frozen-source comparison. No application imports, tests, database,
provider, credential, source/inventory mutation or publication was performed.
