Source: [control-plane-kit-operations/src/control_plane_kit_operations/execution_lease_recovery.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/execution_lease_recovery.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This module owns the pure execution-lease recovery language: RecoveryAuthority,
four frozen command dataclasses, their ExecutionLeaseRecoveryCommand union and
the frozen ExecutionLeaseRecoveryResult. These seven names are its public exports.
Commands transform to descriptors and intent fingerprints; result construction
checks correspondence among supplied records. No command here reads a store,
observes expiry, locks a row, changes a claim or emits history. The
[recovery interpreter](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/execution_lease_recovery_interpreter.py)
owns those effects and the additional current-authority/eligibility checks.

RecoveryAuthority carries actor_id, authority_reference and a tuple of
RecoveryScope values. Actor/reference must be exact strings, nonempty, at most
512 characters and free of code points below 32. This is not canonical RunId
syntax, a byte limit, whitespace trimming or a general secret-content check.
Scopes must be an exact tuple of exact enum values; construction deduplicates
them and sorts by enum value. Empty scopes are allowed as data. No required
operation scope is enforced by this constructor.

authority_reference has repr=False, so default authority/command repr hides that
field while retaining actor and scopes. It remains accessible on the object and
enters the command fingerprint; it is not a resolved credential or proof of an
authenticated principal. RecoveryAuthority does not reject its own subclasses
in __post_init__, although commands require an exact RecoveryAuthority instance.
Frozen dataclasses here do not provide a recursive serialization/redaction policy.

Every command has request_id, retained_run_id, expected_fence, authority and
idempotency_key. Additional fields and descriptor discriminators are:

| Command | Additional fields | Descriptor command |
| --- | --- | --- |
| RenewActiveExecutionClaim | lease_duration | renew-active-claim |
| RenewExpiredExecutionClaim | lease_duration | renew-expired-claim |
| TakeOverExpiredExecutionClaim | next_worker_id, lease_duration | take-over-expired-claim |
| AbandonExpiredExecutionClaim | none | abandon-expired-claim |

_COMMAND_KINDS enforces exact command types, rejecting subclasses and unlisted
variants. request_id uses the same bounded exact-text rule as authority. The
other common fields must be exact RunId, ExecutionLeaseFence, RecoveryAuthority
and IdempotencyKey wrappers. Non-abandon commands require an exact
[ExecutionLeaseDuration](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/lifecycle.py),
whose actual constructor accepts exact integer seconds from 1 through 3600.
Takeover additionally validates next_worker_id as bounded exact text and rejects
the same worker as the expected fence. It does not itself create the replacement
fence or check whether that worker is available or authorized.

Exact outer wrappers preserve their actual imported contracts rather than making
all nested contents uniformly nominal. The actual
[fence](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/execution_leases.py)
allows a string subclass for worker_id, while generation is an exact integer in
1..2**63-1. The actual
[IdempotencyKey](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/workflows.py)
accepts isinstance-str text with nonempty strip and length at most 200; command
validation does not reapply the result's stricter exact-string/control-character
rule to its contents. RunId supplies its own canonical bounded ID language.

Descriptors have six common keys: command, request_id, retained_run_id,
expected_fence, actor_id and idempotency_key. Non-abandon adds lease_duration_seconds;
takeover also adds next_worker_id. They omit authority_reference and scopes but
retain identifiers and worker/generation. These projections are intentionally
inspectable command data, not universally safe logging of arbitrary nested values.

Fingerprint documents contain the discriminator, request/run IDs, expected fence,
actor and authority reference, plus applicable duration and next worker. They
exclude idempotency key and scopes. SHA256 hashes UTF-8 encoding of sorted compact
standard json.dumps output, with its default Unicode escaping; this is not an
RFC8785 implementation. Changing scopes or the key can therefore preserve the
same intent while a reference change changes the digest. The digest binds values;
it does not authenticate authority or grant permission.

The imported [ExecutionLeaseRecoveryEvidence](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
owns fence-transition laws. Either renewal increments generation by one on the
same worker; takeover increments by one on a different worker; abandonment has no
replacement. Rotating evidence rejects a prior generation at 2**63-1. This shared
evidence type also admits retry-as-new-run with an unchanged fence, but retry is
outside this module's four-command union and is rejected by its result classifier.
Command construction does not prove capacity to rotate or temporal eligibility.
In particular, pure abandonment evidence needs no increment, while the actual
interpreter's fresh-state check rejects an exhausted claim generation even for
abandonment; value admission and executable admission are separate contracts.

ExecutionLeaseRecoveryResult contains request, retained_run, decision_event,
consequence_event, action and replayed=False. The five record fields require exact
record types and replayed requires exact bool. Unlike the command classifier,
_validate_result_types does not reject a subclass of the result itself. It also
does not recursively enforce exact types for every field within each record or
copy an arbitrary action Mapping. These are frozen correspondence values, not
proof that every nested representation is immutable.

Lineage requires retained run admission and plan to match the request, and both
events to belong to that run. Event IDs must differ and consequence ordinal must
be decision ordinal plus one. Decision/consequence times and action creation must
be equal. The result does not establish a journal's preceding ordinals, next
available action ordinal, observation freshness or actual database timestamp
canonicalization. It receives no original command or original request claim.

The decision must be RECOVERY_DECISION_RECORDED with exact recovery evidence
naming the retained run, no failure and empty general evidence. Only the four
lease-recovery decisions are accepted. The consequence must match renewal,
takeover or abandonment as REQUEST_CLAIM_RENEWED, REQUEST_CLAIM_TAKEN_OVER or
REQUEST_CLAIM_ABANDONED respectively; it must have no failure, empty evidence and
no additional recovery evidence. Intrinsic event constructors supply overlapping
shape/exclusivity laws before these cross-record checks.

A direct active-renewal result requires a CLAIMED retained run. Its replay form
permits the explicit ten current run statuses, subject to actual record timing
rules. Expired renewal, takeover and abandonment require FAILED for direct results
and replay alike. ActivityRunRecord timing requires started/unsettled FAILED values;
the result does not replay their saga history or decide whether a failed effect is
safe to repeat. Request state must be ABANDONED with no claim for abandonment,
or CLAIMED with a fence equal to recovery.replacement_fence for other decisions.
It does not observe whether the represented lease is currently active or expired.

The action must be RECORD_RECOVERY_DECISION in the request session, with exact
idempotency text of length 1..200 containing no code points below 32 and a lowercase
64-hex fingerprint. Its Mapping must equal the complete expected dictionary of
ten common coordinates: request, plan, retained run, both event IDs/kinds/ordinals
and recovery descriptor. Non-abandon adds lease_duration_seconds, validated as an
exact integer in 1..3600; abandonment must not carry that key. Extra/missing or
different coordinates conflict. Equality uses Python dictionaries, not an
independent byte-canonical or recursively typed wire format.

For non-abandon results, expected duration is taken from the action payload itself
after range validation. This constructor does not compare it to a submitted
command or compute claim expiry from event time plus duration. Nor does it compare
action.actor_id to an originating authority or recompute the fingerprint. Those
bindings belong to the actual interpreter: replay checks action identity/actor,
idempotent fingerprint, decision/prior/replacement fences, submitted duration,
claimed_at and calculated lease_expires_at. Its fresh path also enforces the
appropriate scope, retained approval/journal eligibility, current fence/capacity
and database-observed active versus expired state.

The result descriptor exposes decision, request/plan/run identities, compact event
ID/kind/ordinal coordinates, action ID/kind, recovery descriptor and replay flag.
It omits action payload, authority/scopes, lease times and duration. Default result
repr still traverses nested records; descriptor omissions are not a promise that
the result object or generic serializers are redacted. Validation uses fixed local
InvalidOperationCommand or OperationsRecordError messages, but does not catch
arbitrary exceptions from nested objects, Mapping iteration or dependency code.

The fully read [688-line contract tests](../../../../../control-plane-kit-operations/tests/test_execution_lease_recovery_contract.py)
contain nine methods covering root/frozen shape, exact command fields and selected
nominal negatives, authority normalization/reference hiding, fingerprints, imported
fence/event laws and inventory. Fingerprint expectations are computed from four
explicit documents with the same standard JSON recipe, not hard-coded golden
digests; selected reference changes differ while scopes/key changes do not. Their
default authority uses renewal scope even for other command variants, reinforcing
that pure command admission does not enforce operation scopes. Inventory assertions
check selected ownership fields and test membership, not every export/dependency.

The fully read [513-line result tests](../../../../../control-plane-kit-operations/tests/test_execution_lease_recovery_result.py)
contain six methods using constructed records, placeholder times and fabricated
lowercase-hex fingerprints. They assert all four result descriptors, selected
cross-record/action drift, every common payload coordinate missing/changed for each
variant, extra/duration cases, claim/status matrices, exact ten-status active replay
coverage and categorical retry-evidence rejection. They do not supply a command,
query PostgreSQL, demonstrate lease-time arithmetic or test outer-result/record
subclass rejection exhaustively. Descriptor canary assertions do not inspect full
result repr; expected-error helpers bound str/repr and exception chains.

These tests protect pure values and selected representation laws. Durable approval,
transaction atomicity, row-lock ordering, replay evolution and effects are covered
by their actual owners and integration tests. The module itself emits no session,
action or event: it only represents records that an interpreter may persist. It
contains no automatic retry, compensation, deletion or cleanup policy.

Read depth: full 442-line owner, full contract688/result513 with all fixtures and
methods; actual imported fence, duration, idempotency, recovery evidence and run
timing contracts, with retained RunId context. Selected recovery-interpreter first,
replay, scope, expiry, persistence and command-binding paths were read; retained
full shared-support/retry-owner context distinguishes these recovery families.
No neighboring companions were authored. No source/pin changes, executable tests,
database setup, credentials/private-key access, provider/runtime actions or
publication occurred. Documentation adds no security surface or live authority.
