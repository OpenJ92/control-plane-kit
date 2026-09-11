Source: [control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This is Operations' command/result language for folding one effect attempt, not
the fold interpreter or a provider adapter. It composes Core transition values,
Operations authority/fence and protected outcome/intent records. It defines four
frozen dataclasses and an error family, performs value validation and reconstruction,
and makes no database, clock, secret-resolution or external-effect call. Merely
constructing a command never grants permission to execute or settle uncertainty.

FoldEffectAttempt has request_id, transition, authority, fence, failure and outcome.
The direct arm requires an exact ExecutionEffectOutcome or ObservedEffectOutcome;
the supplied transition and failure must equal the actual outcome owner's derived
values. The recovery arm has a recovery decision in its transition and requires
outcome=None. A STARTED transition is never a fold command. Failed, unsupported
and uncertain direct transitions require failure; reconciled failed recovery also
requires it. Successful recovery and abandonment do not. A recovery failure must
be a valid exact FailureEvidence, but this owner does not derive it from a provider
outcome or impose the fixture's chosen terminal category/message.

Command admission checks exact outer/nested types before selected reconstruction:
Core transition, identity, RunId/text, decision and primitive fields; worker
authority/scopes; the Operations execution fence; failure/details; and direct
outcome. Rebuilding through the actual constructors reapplies their invariants,
then relevant equality checks reject inconsistent forged values. This is not
coercion: normalizing authority scopes into a different value fails equality.
The actual [Core recovery language](../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/recovery.py)
still owns transition/decision shape. No Core state-machine fold occurs here.

request_id must be exact str, 1..512 characters, contain no characters below U+0020
and encode as UTF-8. That is not the canonical run-ID grammar or a 512-byte limit.
The worker/fence must agree, generation must satisfy the actual
[lease value](execution_leases.py.md), and reconstructed authority must match.
An empty scope tuple is representable: EXECUTION_OPERATE membership, current claim,
fence freshness and conversion to Core's effect fence belong to the interpreter.
The command has no idempotency key or durable replay receipt of its own.

GuardedObservedEffectFold pairs a valid ordinary fold with a protected
[intent record](effect_attempt_intent_evidence.py.md) and optional registered runtime
authority. It accepts only an ObservedEffectOutcome, never an execution result or
recovery arm. It reconstructs the exact intent record, then requires a common
attempt identity across intent/transition/outcome, request ID, request fingerprint
and original start-event ID equal to the observation's effect ID. These joins bind
the supplied evidence; they do not authenticate that the observation came from a
provider or that any supplied record is the currently locked database row.

The runtime-authority relation has two arms. With no intent authority reference,
the supplied registration must be None; this arm adds no separate Docker-kind
check. With a reference, an exact active Docker registration is required, matching
the intent's workspace, reference and runtime kind. Thus the referenced arm is
Docker-specific; it is not a general claim that every reference-free non-Docker
intent is rejected here. LocalDockerSocketAuthority and RemoteDockerTlsAuthority
are the admitted concrete registrations.

Registration validation requires exact selected strings/enums/reference values
and an exact dict for metadata, then reconstructs the registration and concrete
authority. Remote TLS reconstruction rebuilds three SecretReference values and
uses the [authority owner's](runtime_authorities.py.md) endpoint checks. It does
not resolve those references, open a socket or verify TLS possession. ACTIVE and
admitted_at are supplied record fields, not evidence of current storage or a
fresh authentication check. Metadata is shallow-copied for validation, not given
a closed schema or deeply frozen by this module. Reconstruction validates inputs
but does not replace the original values retained in the frozen command.

NewlyFolded and ExistingFold share the fields attempt and optional outcome_record.
Their constructors enforce exact variant/attempt types, reconstruct the attempt's
event commitments, reject STARTED and require failure presence exactly for FAILED,
UNSUPPORTED or UNCERTAIN state. UNCERTAIN is therefore representable, not silently
promoted to success. A recovery decision requires outcome_record=None; a direct
attempt requires an exact valid EffectAttemptOutcomeRecord whose reconstructed
attempt equals the supplied one. The actual outcome-record owner checks its own
workspace, outcome, failure and observations. There is no separate request or
workspace store lookup here. The result names distinguish interpreter outcomes;
calling either constructor does not prove insertion, commit or replay.

The [interpreter](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold_interpreter.py)
revalidates commands at entry. Its inspected flow checks scope before the unit of
work, locks request/run/attempt through stores, checks current claim and transition
authority, and invokes Core's fold. Plain execute rejects observed outcomes after
those reads; execute_observed supplies the guard. On a new guarded fold it compares
stored intent with the guard, observes the request lease, and checks a referenced
active authority under lock. Replay follows a separate exact-result path rather
than rerunning all fresh-write checks. Event/outcome/observation writes and attempt
CAS, acknowledgement checks and commit belong to that interpreter and its stores,
not to the dataclasses or their "locked" descriptive terminology.

Repr deliberately hides FoldEffectAttempt.outcome, both protected guard fields
and result.outcome_record. Other fields remain visible, including supplied failure
evidence and attempt/event values. Hiding selected fields is neither universal
secret redaction nor safe public serialization; frozen outer values also do not
make nested objects immutable. This module provides no descriptor codec or public
report projection. Protected intent may still contain addresses and references.

Failed validation normally becomes fixed InvalidOperationCommand or
OperationsRecordError text. Helpers catch selected owned construction failures;
they do not blanket-catch unexpected exceptions or promise normalization of every
partially forged object. EffectAttemptFoldError derives from RuntimeError, with
NotFound, Conflict and Denied subclasses for interpretation failures. Those ordinary
exception classes do not sanitize an arbitrary message supplied by a caller.

Governing tests were read as evidence, not executed. The
[language contract](../../../../../control-plane-kit-operations/tests/test_effect_attempt_fold_contract.py)
covers exports, nominal/frozen shapes, selected malformed coordinates/nested values,
failure presence and direct/recovery result construction. The
[atomic contract](../../../../../control-plane-kit-operations/tests/test_atomic_effect_attempt_fold_contract.py)
covers twenty synthetic direct outcome rows, recovery arms, selected hostile and
forged inputs, hidden repr fields and entry rejection before a failing UoW factory.
Its lexical import/call policy is source-structure evidence, not execution or lock
proof. Constructing ExistingFold in those tests is not durable idempotent replay.

The [guarded contract](../../../../../control-plane-kit-operations/tests/test_guarded_observed_effect_fold_contract.py)
covers twelve synthetic observed rows, local/remote/no-reference registration
cases, mismatched joins, selected hostile dispatch traps, and service-entry/scope
behavior using a failing UoW factory. Its non-Docker rejection supplies a referenced
registration; it does not close the reference-free runtime-kind domain. These
credential-free value tests do not prove live authority, concurrency, persistence
or provider truth. Those require their owning interpreter/store evidence.

Read depth: full 518-line owner and full 568-line language, 776-line atomic and
505-line guarded contract tests; full intent-record owner, retained full fold and
atomic fixtures, full guarded fixture; actual selected outcome/attempt-record,
Core recovery, runtime authority/endpoint, fence/worker/failure and root bindings;
selected interpreter admission, fresh-write/replay and commit paths. Dependency
owners and interpreter were not universally reviewed end-to-end. Documentation
only: no imports/tests, Docker, database, provider, credentials or source edits.
