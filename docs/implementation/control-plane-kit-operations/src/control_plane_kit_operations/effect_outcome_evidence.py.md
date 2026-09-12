Source: [control-plane-kit-operations/src/control_plane_kit_operations/effect_outcome_evidence.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_outcome_evidence.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 1113-line owner joins a direct runtime-effect outcome to an effect-attempt
snapshot and produces bounded operational evidence. Its motivation is to preserve
the distinction between what execution returned, what a provider observer reported,
and what Operations can record about that particular attempt. It is pure value
construction and projection: it does not execute an effect, query a provider, apply
a transition, persist records, authorize recovery or decide whether redispatch is
safe. Provider absence remains uncertainty, not permission to retry.

The public surface is eight names: EffectOutcomeProfile, ExecutionEffectOutcome,
ObservedEffectOutcome, their EffectAttemptOutcome union, EffectAttemptOutcomeRecord,
effect_outcome_transition, effect_outcome_failure and
effect_outcome_observation_records. The two profiles are execution-result and
provider-observation. ExecutionEffectOutcome contains identity, request_fingerprint
and result; ObservedEffectOutcome contains identity and observation and obtains the
request fingerprint from that observation. Both are frozen dataclasses without
slots. Their raw result/observation fields are omitted from repr. Retaining a Core
result does not deeply freeze its nested mutable evidence, and these objects are
not independent immutable copies of every preimage.

The closed execution table maps SUCCEEDED, FAILED, UNSUPPORTED and UNCERTAIN to
the corresponding attempt status and transition kind. Success has no failure.
FAILED produces TERMINAL/runtime.effect-failed; UNSUPPORTED produces
OPERATOR_REVIEW/runtime.effect-unsupported; UNCERTAIN produces
UNCERTAIN/runtime.effect-uncertain. Each row supplies a fixed message rather than
copying a provider's failure message.

The six observation variants are interpreted separately. ObservedSucceeded becomes
SUCCEEDED, ObservedFailed becomes FAILED with TERMINAL evidence, and ObservedAbsent,
ObservedConflict and ObservedIndeterminate become UNCERTAIN with UNCERTAIN evidence.
ObserverUnsupported also becomes UNCERTAIN, but its failure category is
OPERATOR_REVIEW. Their fixed codes are respectively runtime.effect-observed-failed,
runtime.effect-observed-absent, runtime.effect-observed-conflict,
runtime.effect-observed-indeterminate and runtime.effect-observer-unsupported.
These are direct outcome transitions, not recovery-decision transitions.

Admission checks the exact outer outcome and selected nested nominal classes,
attempt identity, bounded text, request fingerprint and observation tuples. Run and
activity identifiers use the Core identity grammar and 200-character bounds;
attempt numbers are exact integers from 1 through 2147483647. Effect identifiers
are nonempty bounded text through 512 characters; request fingerprints are exactly
64 lowercase hexadecimal characters. Text checks reject control characters and
surrogates. Endpoint admission checks exact protocol, context and address-material
classes and reconstructs the protocol and endpoint, including secret-reference
material when present, to exercise their constructors again.

The selected [Core endpoint contracts](../../../../../control-plane-kit-core/src/control_plane_kit_core/probe_intents.py)
own literal endpoint validation and the bounded endpoint descriptor. Secret material
contains a secret:// reference, not resolved bytes. This owner neither resolves
references nor establishes that an endpoint is reachable. VerificationCompleted is
another locally admitted observation shape; ordinary Core provider-observation
constructors accept endpoint observations only. This local branch must not be read
as proof that every forged nested verification graph is reconstructed and rejected.

The selected [Core outcome contracts](../../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effect_observation.py)
own the two domain-separated RFC8785/SHA256 fingerprints and the 8192-byte canonical
outcome ceiling. Execution fingerprints validate the live result before encoding;
observation fingerprints encode the selected observation descriptor. The selected
[RuntimeEffectResult](../../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effects.py)
contract binds result kind, failure presence and observation shape; successful
execution cannot normally contain a non-passed verification completion. Admission
here also constructs each bounded evidence projection. The whole Core outcome
budget and each Operations evidence budget are separate limits.

The outcome descriptor contains profile, attempt identity, effect ID, request and
outcome fingerprints, transition kind and observation count. It omits raw provider
evidence, failure text and endpoint payloads. Fingerprints still commit to the raw
preimage: omission from the public descriptor does not remove it from the retained
Core object. Properties calculate from that object rather than using a frozen
cached projection.

The first bounded evidence entry contains effect_outcome with profile and outcome
fingerprint. Current FAILED execution evidence may additionally contain a
runtime_failure_code. The code must be an exact nonempty string of at most 128
characters using lowercase ASCII namespaced segments separated by dots, with
letters/digits and non-repeated interior hyphens. Invalid codes are omitted rather
than making the whole outcome invalid. This is a syntax restriction, not a closed
registry of allowed codes or a general secret scanner. Provider messages and
details do not supply fallback codes. Other execution kinds and observer failures
retain the profile/fingerprint-only projection.

Each subsequent bounded entry wraps one runtime_endpoint descriptor or one
verification_completion descriptor. The selected
[record contracts](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
own BoundedEvidence's 4096-byte serialized limit, nesting and collection limits,
bounded strings and rejection of secret-shaped mapping keys. Those restrictions
do not make every arbitrary string value safe. Raw provider failure data is omitted
by this owner's fixed projection; endpoint evidence intentionally contains the
admitted endpoint descriptor.

effect_outcome_transition validates the outcome and constructs a Core transition
containing kind, identity and outcome fingerprint. effect_outcome_failure validates
the same input and returns either None or fixed FailureEvidence with current
bounded details. Neither function advances a store or an attempt.

EffectAttemptOutcomeRecord contains workspace_id, outcome, attempt and an exact
tuple of endpoint_observations. Its name for the latter field also covers projected
HTTP verification rows. The constructor revalidates an exact
[EffectAttemptRecord](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempts.py)
through its constructor and binds identity, request fingerprint, status and outcome
fingerprint to the outcome. Recovery decisions are excluded. The original start
event ID must equal the effect ID; the latest event ID must differ. Both events
must belong to the same run and activity, and their bounded ordinals must increase.
The start kind chooses normal or compensation phase, and the latest kind must be
that phase's direct outcome kind. Original failure/recovery and latest recovery
are forbidden. Event timestamps are bounded text here, not independently parsed
as canonical UTC timestamps by this owner.

The latest failure must equal the current projection or the exact legacy
profile/fingerprint-only projection, including its fixed category, code and
message. This allows an older FAILED execution record to remain readable after
runtime_failure_code was introduced; it does not admit arbitrary missing or extra
fields, change the raw outcome fingerprint, or rewrite old evidence in place.
Success requires no failure. The record descriptor adds workspace and latest event
ID/run/ordinal to the compact outcome descriptor. It is not a database uniqueness
constraint, and separate valid terminal events can identify separate records.

effect_outcome_observation_records takes an outcome, attempt, workspace and caller
supplied observation IDs, plus optional intent_record. It validates the outcome,
attempt correlation, exact ID tuple, count, uniqueness and bounded coordinates.
Rows preserve observation order and use the latest transition event's occurred_at;
there is no caller supplied observed_at argument. It returns a tuple, not a complete
EffectAttemptOutcomeRecord. The latter adds the full snapshot and row-inverse
checks, including workspace, time, graph, unique IDs and exact bounded evidence.

A runtime endpoint becomes a FRESH observation with status UNKNOWN, TRANSPORT
probe kind and UNKNOWN probe outcome. Its graph, subject and endpoint context
come from the endpoint observation. FRESH means the projected record is fresh by
this construction; it does not prove health or continued provider freshness at
some later read. The record inverse rejects promoting this endpoint fact into
HEALTHY, REACHABLE or application-health evidence.

HTTP verification has a stronger projection boundary. The helper requires an exact
[EffectAttemptIntentRecord](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_intent_evidence.py)
whose identity, original event and request fingerprint match the attempt/outcome,
and whose desired graph matches the completion. It selects exactly one authored
product by node ID and exactly one HttpCheck by check ID. Provider socket, path and
expected digest come from that authored check, not arbitrary completion metadata.
The selected [Core verification values](../../../../../control-plane-kit-core/src/control_plane_kit_core/verification.py)
own normal check, evidence and completion construction. This lookup consumes the
intent record; it does not reconstruct that entire record or perform an HTTP call.

Evidence must be absent or exact HttpVerificationEvidence, with expected digest
matching the check. PASSED requires evidence, an expected status and a true body
match when a digest was authored. FAILED without evidence is transport-unavailable;
an unexpected status is status-mismatch; an expected digest with a false body match
is body-mismatch. Contradictory FAILED evidence is rejected. TIMED_OUT, MALFORMED and
REJECTED use fixed policy-exhausted, response-oversized and response-rejected
categories. These labels are this implementation's interpretation of completion
values, not separately observed causes obtained here.

HTTP rows use APPLICATION_HEALTH and RUNTIME_PRIVATE. PASSED maps to VERIFIED/HEALTHY;
FAILED to VERIFICATION_FAILED/UNHEALTHY; TIMED_OUT and MALFORMED to matching status
and probe outcomes; REJECTED to REJECTED/UNKNOWN. The subject is verification: plus
a SHA256 of the RFC8785 node/check/schema tuple, with schema
cpk.verification-subject.v1. Workspace and graph remain row coordinates, not inputs
to that subject hash. Evidence records attempt/effect/node/check coordinates,
authored socket/path/digest and bounded HTTP status, byte count and body-match flag;
failed rows also have stage and category. It does not retain response bodies or an
observed body digest.

The record constructor's HTTP inverse has no intent_record argument. It checks the
exact payload keys, completion-correlated coordinates, outcome/category, subject,
row status and probe/context fields. Socket/path are constrained structurally, but
their equality to an authored check cannot be reproved there. In particular, a
FAILED status-mismatch category cannot be checked against the authored expected
statuses at this inverse boundary. Use the intent-aware projection to establish
that authority; accepting a standalone record does not establish it independently.

Recognized invalid evidence, record and projection paths raise fixed
OperationsRecordError messages. Selected Core/Operations validation errors are
converted outside their handlers. This is not a blanket catch for every exception
from a forged object or retained dependency. Hidden repr fields and bounded
descriptors reduce accidental disclosure but do not authorize logging raw objects.

Authoring coverage was the full 1113-line owner and full 736-line
[evidence fixture](../../../../../control-plane-kit-operations/tests/effect_outcome_evidence_fixture.py),
plus selected definitions/helpers in the imported contracts linked above. The
fixture constructs ten outcome stories in both normal and compensation phases,
correlated predecessor attempts and exact-size boundary values. Its default fixed
failure expectations represent the legacy projection; focused tests cover the
new FAILED runtime code projection separately.

Selected bodies in [test_effect_outcome_evidence_contract.py](../../../../../control-plane-kit-operations/tests/test_effect_outcome_evidence_contract.py)
cover the public value surface, all ten transition/failure rows, allowed/omitted
runtime codes, current versus exact legacy records, compact descriptors, the
8192-byte outcome limit and 4096-byte endpoint bridge. Selected bodies in
[test_effect_outcome_record_contract.py](../../../../../control-plane-kit-operations/tests/test_effect_outcome_record_contract.py)
cover authored HTTP success/failure projection, contradictory evidence, fixed
failure equality, ordered unique snapshot-timed rows, the exact bridge boundary
and row inverse mutations. These were selected suite reads, not full-suite review.

The missing-authoritative-check test replaces the completion's check ID without
updating the attempt's outcome fingerprint. The projection can reject that mismatch
before reaching authored-check lookup, so that test alone does not isolate the
lookup guard. This is a test-evidence qualification, not a source or test change in
this documentation pass. No tests, application imports, database calls or provider
operations were executed. Persistence, concurrent writes, actual HTTP verification,
provider truth and recovery authorization remain outside this owner's evidence.
