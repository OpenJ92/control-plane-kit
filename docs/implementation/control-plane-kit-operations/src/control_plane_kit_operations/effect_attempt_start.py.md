Source: [control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_start.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_start.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 211-line Operations module owns the command and result language for starting
the first external-effect attempt. Its eight public exports are StartEffectAttempt,
NewlyStarted, ExistingAttempt, their result union and four interpretation errors.
The Operations root re-exports them. The module uses Core transition/intent values
and Operations authority, fence and record values; it does not open transactions,
allocate event IDs, query leases, resolve secrets or call runtime providers.

StartEffectAttempt is a frozen dataclass with five ordered fields:

```text
request_id, transition, intent, authority, fence
```

These contain request text, EffectAttemptTransition, RuntimeEffectIntent,
ExecutionWorkerAuthority and ExecutionLeaseFence respectively. __post_init__ calls
_valid_start_command and raises InvalidOperationCommand with the fixed message
"effect attempt start command is invalid" when that predicate returns false.
It does not normalize fields or replace the supplied intent. There is no command
descriptor, decoder or alternate factory in this module.

_valid_start_command first rejects any non-exact StartEffectAttempt, including
subclasses. It then retrieves the four structured fields and applies short-circuit
shape guards. Request text must be an exact str of one through 512 characters,
contain no character with ordinal below 32 and encode successfully as UTF-8.
The bound counts characters rather than encoded bytes; text is not stripped or
otherwise normalized. This helper does not detect arbitrary secret content.

The transition must be an exact EffectAttemptTransition with an exact identity,
RunId wrapper, run text, activity text, integer attempt and string request
fingerprint. Boolean attempts and scalar subclasses are rejected. The validator
then calls _valid_start_transition before continuing to the intent and authority
checks. The exact wrapper checks precede access to their nested value fields.

_valid_start_transition reconstructs EffectAttemptIdentity and then the complete
EffectAttemptTransition, carrying all original transition fields. ValueError from
that reconstruction becomes false. The final predicate requires STARTED, attempt
one, no prior attempt and equality with the reconstructed transition. The actual
[Core transition contract](../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/recovery.py)
supplies fingerprint syntax, retry-lineage and outcome/recovery exclusion laws.
A valid Core retry transition for attempt two is therefore outside this command
language. Reconstruction is validation; no transition is folded into durable state.

Intent checks require exact RuntimeEffectIntent and RuntimeEffectIntentSource,
an exact source RunId containing exact str, and an exact ActivityId containing
exact str. This order rejects the tested hostile coordinate wrappers and text
subclasses before their selected access/equality hooks or the public request
projection can run. It is a guard for the named shapes, not a universal validator
for every possible forged object graph.

Authority must be exact ExecutionWorkerAuthority, with exact worker str and an
exact tuple whose members are exact PolicyScope values. Fence must be exact
ExecutionLeaseFence, with exact worker str and exact integer generation. Worker
IDs must agree. Both worker checks reject characters whose ordinals satisfy
0 < ordinal < 32. Unlike _bounded_command_text, these checks do not include NUL;
they also do not repeat all length, nonempty or encoding checks. Generation is
checked for exact integer type here, not for its numeric range.

Those limits matter for constructor-bypassing forgeries. The normal
[lease-fence constructor](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/execution_leases.py)
requires nonempty worker text of at most 512 characters with no character below
32, and generation from one through 2**63 - 1. The normal
[worker-authority constructor](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/lifecycle.py)
requires nonblank worker text and normalizes valid scope members into a sorted,
deduplicated tuple. This command does not reconstruct either value or reapply
that normalization. Empty scopes are constructible and not rejected here merely
for lacking EXECUTION_OPERATE.

After the shape checks, the validator calls the actual
[Core intent projections and fingerprint](../../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effect_observation.py).
It projects the intent into a RuntimeEffectRequest with the fixed validation
effect ID "effect-attempt-start-validation" and an empty secret-resolution-grant
tuple, then projects that request back into a pre-start intent. The request uses
that validation ID as both effect and intent-event identity; the reverse projection
excludes generated event identity and transient grants from the intent. This
constructs pure validation values, not an event allocation or secret grant.

The validator also fingerprints the supplied intent. The inspected Core helper
uses RFC 8785 canonical descriptor bytes, a 1,048,576-byte descriptor limit and
SHA-256 over its intent-specific domain prefix plus those bytes. This module
delegates that contract; it does not introduce a second digest format. The digest
binds described intent and is not authentication or proof of an external effect.

ValueError from the projection/reconstruction/fingerprint try block becomes false.
Afterward, reconstructed intent must equal the supplied intent, and command
request ID, transition run/activity and transition request fingerprint must match
the intent's corresponding coordinates and computed digest. The command does not
query whether workspace, plan or graph IDs exist or whether the operation matches
an admitted plan. Those durable relationships belong to the interpreter.

Error normalization is bounded. Initial attribute reads and shape checks occur
outside the projection try block, and the final equality/binding checks occur
after it. _valid_start_transition likewise catches ValueError only around its
reconstruction. Missing attributes, unexpected exception categories or failures
outside those blocks are not universally converted into false. The fixed
post-init message therefore does not imply a catch-all sanitization boundary
for arbitrary objects forged without their public constructors.

NewlyStarted and ExistingAttempt are frozen one-field dataclasses containing an
EffectAttemptRecord. Both reject subclasses of themselves and non-exact record
types with OperationsRecordError and the fixed result-invalid message.
NewlyStarted additionally requires STARTED status and equality of original start
event with latest transition event. Equality, rather than object identity, is the
law. ExistingAttempt imposes no status restriction, so terminal and recovered
records can be observed through that variant.

Neither variant reconstructs the nested record or independently verifies its
event commitments. NewlyStarted directly reads status and compares events;
ExistingAttempt checks only exact outer/record types. Ordinary records receive
their commitment checks from the
[effect-attempt record owner](effect_attempts.py.md). Wrapping a record is not
itself proof that it was committed, and the result types do not create dispatch
authority. Their meaning depends on the interpreter path that returns them.
EffectAttemptStartResult is the Python union NewlyStarted | ExistingAttempt.

The selected actual
[start-service consumer](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_start_interpreter.py)
reuses _valid_start_command before opening a unit of work, then requires
EXECUTION_OPERATE and translates the execution fence into a Core effect fence.
That translation separately checks nonblank worker text, a 256-character limit,
no control characters, UTF-8 encoding and generation range. Thus even a normally
constructed execution fence can exceed the narrower effect-fence text bound.
Command construction alone does not establish service eligibility.

Inside its unit of work, the inspected service checks durable request/run/lease
truth and either returns ExistingAttempt after replay checks or prepares a fresh
start. Its _plan_result folds the transition, creates a state commitment and an
event, and constructs NewlyStarted before persistence. execute then checks event,
intent-evidence and attempt insertion acknowledgements and requests commit before
returning. This placement explains why the result constructor cannot itself
certify persistence. The command/result owner emits no history or cleanup actions.

EffectAttemptStartError directly extends RuntimeError; NotFound, Conflict and
Denied directly extend that base. They carry ordinary caller-supplied exception
arguments and add no payload validation, redaction or subclass prohibition.
Command invalidity uses the separate workflow InvalidOperationCommand hierarchy;
result invalidity uses the record layer's ValueError-derived OperationsRecordError.
The named interpretation errors are available for service categorization, not
raised by this module's command/result predicates themselves.

The fully read
[start-contract suite](../../tests/test_effect_attempt_start_contract.py.md)
checks exact root identities, frozen metadata, field layout, eighteen invalid
command candidates, valid intent binding, foreign fingerprint rejection and
first-attempt restrictions. Its deep-coordinate probes assert rejection before
projection and recorded hostile dispatch. Result tests cover sixteen ordinary/
compensation stories and four outer/nested subtype failures. Error tests establish
the named hierarchy and safe rendering of fixed test messages, not arbitrary
service-error redaction. The selected interpreter test additionally asserts that
missing scope is rejected before the unit-of-work factory is called.

Read depth: the complete 211-line owner and all three private validators were read,
with retained full 428-line command suite and 208-line start fixture context.
Selected actual Core transition, intent/source/projection/fingerprint helpers,
authority/fence constructors, root exports and exception bases were checked.
Service initialization, execute, _plan_result and fence translation were read;
the full interpreter and its full test suite were not reviewed. Validation was
documentation-only: local links, whitespace and frozen-source comparison. No
application imports, executable tests, database/provider calls, credential access,
source/inventory edits or publication were performed.
