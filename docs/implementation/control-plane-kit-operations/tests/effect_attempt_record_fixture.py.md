Source: [control-plane-kit-operations/tests/effect_attempt_record_fixture.py](../../../../control-plane-kit-operations/tests/effect_attempt_record_fixture.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 378-line fixture constructs effect-attempt states and their original/latest
event commitments for eight stories. It provides optional owner bindings,
subclass probes, a locally computed state fingerprint and assertion helpers.
It contains no test methods, store, transaction, provider call or persistence
operation. Consumer suites must supply assertions establishing the laws exercised
with these values; availability of a builder is not coverage of its combinations.

The module imports the Operations root unconditionally and computes
REQUEST_FINGERPRINT immediately from a default intent for activity-a. It then
loads effect_attempts through _load_effect_attempts_module, which returns None
only for ModuleNotFoundError naming that exact module. Missing transitive
dependencies, other import failures and intent-construction failures propagate.
The injected import_module argument supports targeted loader tests; it does not
make the whole fixture import independent of its dependencies. The inspected
Operations root itself imports the three effect-attempt exports.

EffectAttemptEventEvidence, EffectAttemptRecord and effect_attempt_state_fingerprint
are captured with getattr defaults of None. require_language reports which are
missing, but does not assert signatures, behavior or root-export identity.
record calls this presence check; state and the other value builders do not.
operations_root and the loaded module are exposed for consumer inspection, without
an assertion here that their exports agree. maxDiff=None affects failure display.

The nine Hostile classes are empty subclasses of state, event, bounded evidence,
identity, fence, recovery decision, failure evidence, int and str. They support
exact-type rejection cases but do not override attribute access, equality, repr,
hashing or conversion, and do not track dispatch. This file has no object.__new__
forging helper or constructor bypass. Calling these classes normally still invokes
their inherited constructors; consumers decide how to construct malformed objects
or substitute subclasses. No unreviewed consumer suite receives credit here for
using those probes.

identity constructs EffectAttemptIdentity(RunId(run_id), activity_id, attempt),
defaulting to run-a/activity-a/one. intent_for_attempt delegates to
[EffectAttemptIntentFixture](../../../../control-plane-kit-operations/tests/effect_attempt_intent_fixture.py)
with compensation and run/activity coordinates. request_fingerprint_for_attempt
uses the actual runtime intent fingerprint on that freshly built intent; it does
not reuse the module constant or include the attempt number. Changing run,
activity or compensation can change this fingerprint, while consecutive attempts
over the same intent retain it.

The inspected intent helper builds a synthetic Docker request with one product,
reference-valued application/database/pull credentials and remote authority
delivery, then projects it to the actual pre-start intent. Compensation changes
StartNode to StopNode and removes the top-level delivery while retaining the
helper's product-delivery rule. These are typed descriptions, not registration,
authorization, secret resolution or runtime access. The actual intent projection
excludes generated effect/event identity from the intent source; its fingerprint
has a separate canonicalization/domain contract from the fixture's state digest.

state maps started, succeeded, failed, unsupported, uncertain, recovered-succeeded,
recovered-failed and abandoned to their statuses. Every state uses worker-a with
generation seven and the actual intent fingerprint. Attempt one has no prior;
later attempts point to the immediately preceding number in the same run/activity.
An unknown story raises the mapping's KeyError rather than a fixture-defined error.

STARTED has no outcome fingerprint. UNCERTAIN uses the synthetic c-times-64 value;
all other non-started stories use b-times-64. Recovery and abandonment add a
decision-a value identifying the same attempt, with the appropriate resolution,
the c-valued uncertain fingerprint and b-valued evidence fingerprint. This builds
a final state directly. It does not execute transitions, establish a persisted
uncertain predecessor or prove that the referenced recovery evidence exists.

The actual
[core state contracts](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/recovery.py)
validate identity/fence values, lowercase SHA-256 fingerprints and immediate retry
lineage. STARTED disallows outcome/recovery values; non-started states require an
outcome, and ABANDONED requires recovery. A supplied recovery decision must identify
the state, agree with its status and retain its evidence fingerprint as the
outcome. These constructor laws are used by the fixture, not reimplemented by it.
started_state preserves identity, request fingerprint, fence and prior attempt
while clearing terminal outcome/recovery values to construct the original state.

canonical_state_fingerprint serializes state.descriptor with sorted keys, compact
separators and ensure_ascii=False, encodes UTF-8 and applies plain SHA-256. It
does not call the exported production state-fingerprint function. Thus it is a
separate implementation of that digest formula, but shares the production
descriptor and Python JSON behavior, with no independent hand-written descriptor
oracle. It adds no domain prefix or input type check. The actual
[effect-attempt owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempts.py)
uses the same formula after an exact top-level EffectAttemptState check; that
fingerprint function alone does not fully readmit every nested state field.

evidence_for wraps the local digest and attempt number under exactly
{"effect_attempt": {"attempt": ..., "state_fingerprint": ...}} through
BoundedEvidence.from_mapping. It does not construct EffectAttemptEventEvidence
itself. That actual evidence value separately requires an exact integer from one
through 2,147,483,647 and an exact lowercase 64-character digest string. The record
owner reconstructs this evidence envelope and compares it for equality, so extra
evidence fields are not accepted merely because the expected commitment is present.

event_kind selects the ordinary or compensation member of each story's event
family; recovered stories use uncertainty-resolved events and abandonment uses
uncertainty-abandoned. event constructs ActivityEventRecord using supplied ID,
ordinal and time, with run/activity/evidence falling back via truthiness to state
coordinates and generated evidence. Empty run/activity strings therefore select
defaults rather than reaching the constructor as invalid candidates. An explicit
empty BoundedEvidence object is truthy and is preserved. The helper supplies no
failure or lease-recovery evidence, regardless of event kind.

record creates the state, then an original start event committed to started_state.
For STARTED, latest is that same event object. Other stories receive a distinct
latest event committed to the final state, and the actual EffectAttemptRecord
constructor admits the pair. Defaults use ordinals three and seven, but original
time is 2030-01-01T00:00:02Z while latest time is one second earlier. The actual
record law uses increasing ordinal and distinct event IDs for non-started records;
it does not require chronological timestamps. ActivityEventRecord checks time as
bounded text rather than parsing it as a timestamp.

The record owner requires exact nested state/event/evidence types, matching
run/activity coordinates, a start-kind original event and the correct latest
kind for phase, status and recovery presence. STARTED requires latest equality
with original. It does not require a failure value for failed, unsupported or
uncertain records, so this fixture can build them with failure=None. The
[fold-result contract](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_fold.py)
adds a failure-presence law and other outcome requirements; these bare records
are not automatically valid completed fold results or proof of runtime failure.
Recovered state evidence is distinct from ActivityEventRecord.recovery, which
this record layer forbids on effect-attempt events.

The owner performs exact nested shape checks and constructs a STARTED state for
the original commitment; it does not reconstruct the entire final state or each
nested event through all public constructors. Do not describe the fixture or
record constructor as universal admission of arbitrary forged exact-type values.
The inspected
[record/evidence owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
supplies bounded canonical JSON, event scope and failure-kind checks. Evidence
validation bounds encoded size, depth, items and text and rejects secret-shaped
keys; it is not arbitrary secret-value detection or runtime credential handling.

assert_safe_error requires absent cause/context, combined str/repr length at most
256 characters and absence of every supplied canary from that rendering. It does
not mutate or sanitize errors, inspect traceback/log output or assert an error
category/message. Canary selection belongs to callers; an empty canary would
make the absence assertion fail. Reusing this helper does not establish safety
for unexamined error paths.

Read depth: the complete 378-line fixture, every builder/helper and subclass probe,
and the complete 294-line effect-attempt owner were read. Selected actual core
state/identity/fence/recovery, record/event/evidence, intent construction/projection,
root exports and stricter fold-result contracts were checked. Consumer suites and
the full imported intent fixture were not reviewed. Validation was documentation-only:
local links, whitespace and frozen-source comparison. No application imports,
tests, database/provider calls, credential access, source/inventory edits or
publication were performed.
