Source: [control-plane-kit-operations/tests/test_effect_attempt_record_contract.py](../../../../control-plane-kit-operations/tests/test_effect_attempt_record_contract.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These nine tests exercise typed effect-attempt records: accepted story/phase
combinations, retry and event ordering, exact nested types, event coordinates,
state commitments and derived event kinds. They use the
[record fixture](effect_attempt_record_fixture.py.md)
for states, events, local fingerprints and plain subclass probes. There is no
database, codec round trip, real restart, provider operation or transition
execution in this file.

The local assert_invalid_record constructs EffectAttemptRecord and requires
OperationsRecordError with the exact message "effect attempt record is invalid".
It then requires absent cause/context, combined str/repr length at most 256
characters and absence of the supplied canaries. These assertions cover the
constructor error, not errors during prior candidate construction, test-runner
subTest rendering, logs or arbitrary traceback content.

All altered candidates here are created through public constructors,
dataclasses.replace or normal subclass construction from an existing __dict__.
There is no object.__new__, object.__setattr__ or raw exact-type forgery. Exact
outer events can still be inconsistent with a state or contain a nominal subclass
that their own constructor permits. This is a different boundary from readmitting
objects whose constructors were bypassed.

The positive matrix constructs all eight stories in both ordinary and compensation
phases. Each record must retain the fixture-generated state, original start kind
and story-specific latest kind. This covers sixteen arranged values, with expected
states/kinds supplied by the same fixture helpers; it is not an independent state
transition oracle or evidence that recovery predecessors were durably recorded.
The failed/unsupported/uncertain fixture records omit failure details, which this
record layer allows; they are not thereby complete fold-result values.

The retry/time test accepts a succeeded second attempt with reversed timestamps
and a failed first attempt with equal timestamps. It explicitly checks attempt
two's prior number is one, latest ordinal is greater than original, and the
equal-time pair is equal. Reversed time is exercised through successful record
construction rather than a separate assertion comparing those timestamp strings.
The actual record contract uses event order, not wall-clock chronology. This
test includes no malformed retry lineage, timestamp parser, timezone conversion
or database timestamp representation case.

Three nominal-type rows substitute a state subclass, event subclass or an event
carrying a BoundedEvidence subclass. The event/evidence substitutions are used
as both original and latest in a STARTED record. Every row requires the fixed
record error. The inherited Hostile classes are empty subclasses, not spies that
raise or count calls on attribute access, equality, hashing or descriptors.

The nested-state test substitutes subclass identity, fence, prior identity and
recovery decision in otherwise constructor-admitted states. It constructs fresh
original/latest events and corresponding commitments for each candidate before
requiring rejection. This avoids relying on an accidentally stale evidence hash
to reject the nested subtype. A fifth case inserts a FailureEvidence subclass
into a failed latest event. Despite the test's exact-restart name, it performs
no serialization, decoder invocation, persistence or process restart.

The actual
[effect-attempt owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempts.py)
checks exact nested state shape, then original and latest event shape, before
deriving phase or reconstructing commitments. Its state checks include identity,
fence, prior identity and recovery-decision substructure; event checks include
bounded evidence and optional failure substructure. These early guards explain
the nominal rejections, but the tests have no patched commitment function or
dispatch sentinel to independently establish short-circuit order or absence of
hostile method calls. Not every nested scalar/wrapper or malformed exact-type
combination is represented.

The coordinate test admits ordinal 2,147,483,647 for a STARTED event pair and
admits a settled pair at maximum-minus-one/maximum. It rejects a pair with both
ordinals oversized, then separately rejects an oversized latest ordinal. Thus
the first negative pair does not independently isolate only the original ordinal.
Two further rows put a lone Unicode surrogate in the original or latest event
ID and require the fixed safe error without the supplied canary.

The inspected
[ActivityEventRecord constructor](../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
requires a positive exact integer ordinal but has no PostgreSQL integer upper
bound; its bounded-text event-ID check does not reject that lone surrogate.
Those candidates can reach EffectAttemptRecord, whose event guard adds the
integer maximum and event-ID UTF-8 encodability. No PostgreSQL adapter is invoked
to prove actual database acceptance, and this test does not exhaust every
coordinate, Unicode or storage constraint.

Nine latest-event faults start from succeeded truth: foreign run, foreign
activity, absent evidence, missing attempt, missing fingerprint, wrong attempt,
wrong fingerprint, an extra commitment field and reuse of the original event ID.
Each altered event still passes its own constructor, then must fail record
admission with the fixed message and selected canaries absent. The extra field
case uses bounded ordinary text, showing that valid generic evidence can still
be invalid as an exact effect-attempt commitment envelope.

Nine original-event faults start from recovered-failed truth and cover the same
run/activity and six evidence alterations, plus an original kind changed from
start to success. Its wrong-fingerprint candidate commits to the final recovered
state rather than reconstructed STARTED state. This specifically distinguishes
the original commitment from the latest commitment. These candidates do not
establish that every valid-shaped corruption or every recovery decision is checked.

The actual commitment check reconstructs exactly
{"effect_attempt": {"attempt": ..., "state_fingerprint": ...}} with
BoundedEvidence and compares the whole evidence value. It also compares event
kind, run and activity to the state. The fixture computes its digest separately
with the same production state descriptor and compact sorted JSON formula; these
tests are not an independent oracle for descriptor semantics or hash algorithms.
Extra fields fail equality even when the expected commitment fields are present.

Four kind/phase faults swap direct success for recovered success, recovered
success for direct success, compensation failure for ordinary failure, and an
ordinary original start for a compensation start while retaining its ordinary
latest event. All require record rejection. Source derives compensation from
the original start kind and looks up the latest kind from that phase, final
status and recovery-decision presence. There is no independent caller-supplied
phase field in EffectAttemptRecord. The tests cover these four mismatches rather
than an exhaustive cross-product of kinds.

The final ordering test rejects equal and decreasing settled latest ordinals
against original ordinal three. For STARTED, three rows change only latest event
ID, ordinal or time and must fail. Source requires latest == original for STARTED,
not Python object identity, and requires distinct IDs plus strictly increasing
ordinals for non-started records. The test's identity wording does not establish
an is requirement or verify acceptance of a distinct but equal event object.

The actual
[core state contract](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/recovery.py)
already checks ordinary constructor relationships such as immediate retry lineage
and recovery identity/status/fingerprint agreement. EffectAttemptRecord adds
exact shapes and event commitments but does not reconstruct the entire final
state and every nested event through all public constructors. This file should
not be cited as comprehensive defense against raw forged exact-type values,
all nested scalar subclasses or malicious dispatch behavior.

The tests likewise establish selected event/evidence and error laws, not general
secret sanitization. BoundedEvidence applies its own canonical JSON, size and
secret-shaped-key restrictions before these record checks; the negative envelope
values deliberately remain valid at that generic layer. No authorization,
credential resolution, external exposure, transaction or durable history mutation
is introduced by the tests themselves.

Read depth: the complete 519-line source, all nine tests and local assertion helper
were read. The complete record fixture and actual 294-line effect-attempt owner
were retained from the preceding review; relevant core state, ActivityEventRecord,
BoundedEvidence, failure and stricter fold-result contracts were cross-checked.
No adjacent consumer suite was reviewed or executed for this companion. Validation
was limited to local links, whitespace and frozen-source consistency; no
application imports, tests, database/provider calls, credential access,
source/inventory edits or publication were performed.
