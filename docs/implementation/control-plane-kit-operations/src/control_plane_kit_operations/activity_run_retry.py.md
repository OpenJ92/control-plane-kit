Source: [control-plane-kit-operations/src/control_plane_kit_operations/activity_run_retry.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/activity_run_retry.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This pure owner defines RetryFailedActivityRun and ActivityRunRetryResult. The
command names one prior run and expected lease fence; the result checks agreement
between a claimed request, failed prior run, linked successor, two events and one
recovery action. It does not locate runs, inspect their journal, authenticate an
operator, observe expiry, allocate IDs or write a retry. The
[retry interpreter](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/activity_run_retry_interpreter.py)
owns those transactional decisions and effects.

RetryFailedActivityRun is a frozen five-field dataclass requiring its exact own
type, not a subclass. request_id is exact str, nonempty, at most 512 characters
and free of characters below code point 32. This is bounded text, not the narrower
canonical run-ID grammar or a UTF-8 byte limit. prior_run_id must be exact Core
[RunId](../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/run_identity.py);
expected_fence, authority and idempotency_key must be exact ExecutionLeaseFence,
RecoveryAuthority and IdempotencyKey values. Their constructors own their internal
laws; this command does not flatten them into untyped strings/dictionaries.

The actual [recovery authority](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/execution_lease_recovery.py)
holds actor, a repr-hidden authority reference and normalized closed recovery
scopes. Exact type admission does not mean OPERATE is granted: the interpreter
checks that scope before opening a UoW. The actual
[idempotency value](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/workflows.py)
owns nonblank text and its 200-character maximum. The command itself does not
reserve the key, decide its scope or inspect existing action history.

descriptor returns retry-as-new-run, request/prior-run IDs, expected fence,
actor ID and idempotency key. It omits authority_reference and scopes. The command
repr still includes scopes through its nested authority value, while that value
hides its reference. Fence data is worker/generation, not lease timestamps. This
is a deliberate descriptor projection, not automatic redaction of arbitrary
serialization or every nested object's representation.

intent_fingerprint hashes UTF-8 bytes from sorted, compact json.dumps of decision,
request/prior-run IDs, expected fence, actor and authority reference. It uses the
standard JSON serializer here, not the node-control RFC8785 codec. Idempotency key
and scope set are excluded: they serve replay lookup and current authorization
separately from semantic intent. The reference influences the digest even though
it is omitted from descriptor/repr. There is no clock, expiry, generated successor
identity or plan read in this computation, and the digest is not a signature or
proof that the authority reference was authenticated.

ActivityRunRetryResult is a frozen seven-field dataclass. It requires its exact
own type, exact ExecutionRequestRecord, both ActivityRunRecords, both
ActivityEventRecords and OperationActionRecord, and an exact bool replayed.
These checks apply to the six supplied record wrappers, not a recursively exact
type policy for every field inside those records. The frozen outer values also
do not deep-freeze or copy every nested Mapping; ordinary record constructors
remain responsible for their own contained values.

Lineage requires a CLAIMED request with claim evidence; a FAILED prior run with
started_at set and settled_at absent; both runs admitted to the request and plan;
a distinct successor naming the prior run; and an attempt number exactly one
greater. Both metadata descriptors must equal the derived retry metadata: attempt
alone for an initial run, plus prior_run_id when present. Extra or missing entries
are rejected through those dictionary comparisons. Actual
[record laws](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
bound RetryIdentity to exact integers 1..2147483647, require a prior ID after the
first attempt and reject self-reference at the run boundary. A prior run at the
maximum cannot have a representable next attempt; the command value alone does
not know the prior run's counter.

A direct result requires the successor still CLAIMED. With replayed=True, the
explicit set permits all ten current ActivityRunStatus values: claimed, running,
paused, succeeded, failed, compensating, compensated, partially_failed,
uncompensated_failure and cancelled. This allows an earlier retry result to be
represented after its successor evolves. It does not establish that transitions
were lawful by replaying history; underlying record timing laws still apply, and
the interpreter must verify retained history/current claim separately.

The decision event must be RECOVERY_DECISION_RECORDED on the prior run, carrying
exact ExecutionLeaseRecoveryEvidence for RETRY_AS_NEW_RUN. Its retained run ID
must match and its prior/replacement fences must be equal to each other and to
the request's current claim fence. Unlike lease renewal/takeover, retry does not
increment generation or change workers. Record evidence permits that same-fence
retry even at the maximum fence generation; no lease-time comparison occurs here.

The successor's opening event must be RUN_OPENED on the new run at ordinal 1,
with exactly the expected retry metadata as evidence and no failure. Decision
and opening event IDs must differ. Successor created_at, decision/opened times
and action created_at must agree. This is equality of supplied time strings, not
validation that a database clock produced them. The prior run's decision ordinal
is compared with action payload later; this owner does not query the journal to
prove it was the next available ordinal.

The action must be RECORD_RECOVERY_DECISION in the request's session, with exact-str
idempotency text of length 1..200 containing no character below code point 32,
and a lowercase 64-hex fingerprint. Its Mapping, converted to dict, must equal
the complete expected 13-key payload: request/plan/prior/new-run coordinates,
both attempt numbers, both event identities/kinds/ordinals and recovery descriptor.
Missing, extra or changed dictionary entries conflict. Metadata and payload use
Python dictionary equality; this owner does not add byte-canonical serialization
or recursively type-check every scalar beyond the record contracts.

The result does not receive the originating RetryFailedActivityRun. Consequently
it cannot independently compare action actor, idempotency key or fingerprint
content to that command. Fingerprint validation here is shape validation, not
recomputation; action actor identity remains the record's ordinary text contract.
The selected interpreter replay check supplies the missing relationship by comparing
actor, key, command fingerprint and expected fence with retained result evidence.
Constructing a coherent result directly is not proof of approval, command authority,
unexpired lease or successful commit.

descriptor exposes decision, request/plan/run IDs, attempt counters, compact event
IDs/kinds/ordinals, action identity/kind, recovery fence evidence and replay flag.
It omits request claim timestamps, authority reference/scopes and full action
payload. Default result repr still contains its nested records; the descriptor's
omissions should not be generalized to repr or arbitrary object serialization.
Expected command/result mismatches raise InvalidOperationCommand or
OperationsRecordError with fixed messages. There is no broad exception handler
around every nested attribute, Mapping conversion or descriptor method, so this
is not a universal exception-sanitization boundary for arbitrary object behavior.

Fresh interpreter execution adds the missing operational checks: scope, request/
prior/latest-run locks, current expected fence and attempt capacity, retained
approval, eligible failed journal and database-observed unexpired lease. It then
allocates and persists the successor, decision/opened events and action in one UoW.
Replay revalidates retained evidence and command identity instead of allocating a
new run. Shared
[recovery support](_execution_lease_recovery_support.py.md)
uses this result type to validate each link of a historical retry chain. None of
those reads, locks, mutations or graph/history decisions is implemented here.

The fully read
[command/record contract tests](../../../../../control-plane-kit-operations/tests/test_activity_run_retry_contract.py)
contain 12 tests for root identity/frozen shape, subclass rejection, exact descriptor
and four fingerprint golden vectors, included/excluded fingerprint coordinates,
selected malformed command inputs, retry counter/fence laws and inventory/static
import ownership. The counter/fence tests exercise imported record contracts as
well as this owner. No database is involved. Scope membership remains an interpreter
test obligation; changing scopes while preserving the fingerprint is intentional.

The fully read
[result tests](../../../../../control-plane-kit-operations/tests/test_activity_run_retry_result.py)
contain ten tests using constructed request/run/event/action records with synthetic
time strings and a fabricated well-shaped action fingerprint. They check exact
outer/nested record types, lineage/fence drift, initial/later metadata, opened
evidence, equal times/distinct event IDs, maximum attempt and all ten replay states.
Each of the 13 expected action payload entries is tested missing and changed,
with an additional extra-entry case. These are value-coherence tests, not durable
replay, cryptographic approval or a complete adversarial scalar-type matrix. Selected
errors must be chain-free, bounded and omit supplied canaries.

Read depth: full 299-line owner, full 505-line command/record/ownership test and
671-line result test, including all fixtures. Actual RecoveryAuthority,
IdempotencyKey, RunId, retry/evidence record laws and selected interpreter replay
checks were inspected with retained full shared-support and caller context. No
source/pin changes, executable tests, database setup, credential/private-key access,
provider/runtime actions or publication occurred. Documentation adds no security
surface or permission to create a live retry.
