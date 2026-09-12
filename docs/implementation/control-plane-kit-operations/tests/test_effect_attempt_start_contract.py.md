Source: [control-plane-kit-operations/tests/test_effect_attempt_start_contract.py](../../../../control-plane-kit-operations/tests/test_effect_attempt_start_contract.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 428-line suite contains eleven unittest methods for the public effect-attempt
start command, result variants, error hierarchy and optional-import guard. It
constructs actual values and hostile candidates, checks admission failures and
temporarily replaces one projection function to assert early rejection. It does
not instantiate the start service, open a unit of work, persist events, dispatch
runtime effects or establish lease authority. The main guard runs unittest when
the file is executed directly; this documentation pass did not execute it.

EffectAttemptStartLanguageTests inherits
[EffectAttemptStartFixture](effect_attempt_start_fixture.py.md) before
[EffectAttemptRecordFixture](effect_attempt_record_fixture.py.md), then TestCase.
That order selects the start fixture's require_language, identity and
assert_safe_error methods. Error assertions throughout this suite therefore use
the 512-character combined str/repr limit and skip empty canaries, including the
result tests. They do not use the record fixture's 256-character limit. record's
self.require_language() also resolves to the start-language presence check, not
the record fixture's check. Record construction still calls the actual record
owner. Its state builder passes explicit run/activity arguments to self.identity,
so its activity-a default is preserved despite the start identity's different
default activity.

The missing-module test injects a loader that raises a specific nested-dependency
ModuleNotFoundError and asserts that the identical exception escapes. A second
loader raises ImportError, whose category must propagate. It does not test the
exact-requested-module absence case returning None, successful loading or every
other possible import error. Ordinary root/fixture imports remain unconditional.

The command surface test checks identity with the Operations root export, the
owner module name, dataclass/frozen metadata and exact ordered fields
request_id, transition, intent, authority and fence. A fixture-built command must
equal a direct positional construction. A normally constructed subclass must
raise InvalidOperationCommand with the fixed command-invalid message and safe
rendering. This checks nominal admission and frozen metadata, not an attempted
field assignment, hash behavior or every constructor-signature property.

The main invalid-coordinate table has eighteen entries. Six cover request text:
empty, None, boolean, 513 characters, newline and a str subclass. Other entries
cover a transition subclass, nested identity subclass, fingerprint str subclass,
intent subclass, an exact forged intent with a foreign request ID, and normally
built intents with different request/run/activity coordinates. The remaining
cases exercise authority/fence subclasses, an authority worker mismatching the
default fence, and matching worker IDs carried by a str subclass. Each must raise
the fixed InvalidOperationCommand and satisfy the applicable canary check.
The method's broad name does not establish exhaustive coordinate coverage or
positive boundary acceptance at length 512.

Most local Hostile classes are empty subclasses constructed normally, so their
inherited constructors still run. The forged intent instead uses the imported
forge_exact helper to allocate actual RuntimeEffectIntent and source objects with
object.__new__ and assign fields without post-init. That case preserves other
coordinates while changing the request ID. It is one concrete exact-type forgery,
not a proof that all arbitrary malformed exact-type objects are safely admitted
or rejected.

The start fixture matters to these rejection paths: it eagerly builds a default
intent even when one is supplied. If building a transition from the supplied
intent raises RuntimeEffectContractError, it builds a default transition while
retaining the supplied intent for command admission. Custom request/run/activity
values are not automatically propagated into all command coordinates. The tests
therefore exercise the actual command boundary rather than treating the fixture
as a pass-through constructor or a general coherence repair mechanism.

The first-attempt test admits the default command and rejects two actual core
transitions: SUCCEEDED, and STARTED attempt two with an explicit valid predecessor.
The latter is a valid retry transition at the core layer but invalid for this
first-start command. The test does not enumerate every transition kind or forge
every prohibited prior-attempt arrangement.

The binding test asserts that command.intent is the exact supplied object, and
that request, run, activity and transition fingerprint agree with that intent.
Fingerprint expectation uses the same production fingerprint function as the
fixture, so this is a binding check rather than an independent digest oracle.
A separate test constructs a validly shaped transition with f-times-64 as a
different fingerprint, proves it differs from the intent's fingerprint and
requires fixed, canary-free command rejection.

The hostile-intent method first uses class_access_hostile_copy from the imported
[intent helpers](../../../../control-plane-kit-operations/tests/effect_attempt_intent_fixture.py).
That subclass records and raises on __class__ access. The fixture command call
must produce the fixed InvalidOperationCommand with no recorded class access.
The method then directly reconstructs a lawful command as a positive control
before testing four deeper candidates generated by deep_coordinate_intent_candidates.

Those four candidates place hostile values at the run wrapper, run text value,
activity wrapper and activity text value, while preserving exact outer intent
and source types through forge_exact. Wrapper probes record __class__/value
access, equality and hashing. Text probes record __class__ access, equality,
hashing, strip and encode. Each candidate has its own dispatch list, cleared
immediately before the command call. These probes cover the named operations on
these four shapes; an empty list is not a universal assertion that no Python
operation touched any part of a candidate.

For each deep candidate, the test temporarily replaces the start module's
runtime_effect_request_for_intent binding with a callable that records invocation
and raises AssertionError. It calls StartEffectAttempt directly, captures any
BaseException and restores the original binding in finally before asserting.
Both the coordinate-dispatch list and projection-call list must remain empty;
the captured exception must have exact type InvalidOperationCommand, the fixed
message and safe rendering without the candidate label. This proves rejection
before that projection seam for these cases. Restoration is explicit, but the
module-level replacement does not provide isolation against concurrent callers.

The actual
[start-language owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_start.py)
uses exact type checks before reading the nested wrapper values or projecting
the intent. It reconstructs the start transition, admits only attempt one without
a prior, and binds the reconstructed intent and fingerprint to command coordinates.
Construction does not require EXECUTION_OPERATE or query a current lease. The
suite does not call the service path that enforces those operational conditions.

The result surface test requires the union to equal NewlyStarted | ExistingAttempt,
root identity for the union and both variants, frozen dataclass metadata and one
field named attempt. It asserts that from_descriptor is absent from each variant's
own __dict__; that is narrower than checking all inherited attributes or every
possible deserialization entry point. No field-mutation attempt occurs here.

The result-story test builds all eight record stories for both ordinary and
compensation phases: started, succeeded, failed, unsupported, uncertain,
recovered-succeeded, recovered-failed and abandoned. ExistingAttempt accepts all
sixteen records. NewlyStarted accepts the two started records and rejects the
fourteen others with the fixed result-invalid error. These are constructor
assertions over synthetic records; no dispatch or persisted observation occurs
despite the test's dispatch/observation terminology. The fixture builds state
and event commitments directly, including synthetic outcome fingerprints, rather
than executing transitions. Successful wrapping is checked by equality, not identity.

The result hostility test copies a valid record into an uninitialized record
subclass, then rejects that nested subclass through each variant. It also rejects
normally constructed subclasses of NewlyStarted and ExistingAttempt wrapping a
valid record: four constructor failures in total. The owner requires exact outer
variant and record types; NewlyStarted additionally requires STARTED and equal
original/latest events. This suite does not independently corrupt that event pair
inside a forged exact record or establish universal nested-record reconstruction.

The error-hierarchy test checks the base's direct RuntimeError parent, the three
named errors' direct EffectAttemptStartError parent and root identities for all
four. It constructs each named error with fixed text and checks rendering against
secret/address canaries absent from that text. The method name calls the sum closed,
but the assertions do not enumerate all subclasses, prohibit future subclasses or
exercise service failures containing hostile input. They establish the named
hierarchy and the safety of those constructed errors, not runtime redaction.

Read depth: the complete 428-line suite and every local helper/probe were read.
The complete 208-line start fixture, 378-line record fixture and 211-line actual
start-language owner were reviewed or retained from their full companion reads.
The imported forge_exact, class-access probe and complete deep-coordinate generator
were read, with retained selected intent-construction and core-contract context.
The entire imported intent fixture and interpreter suite were not reviewed for
this slice. Validation was documentation-only: local links, whitespace and frozen
source comparison. No application imports, executable tests, database/provider
calls, credential access, source/inventory edits or publication were performed.
