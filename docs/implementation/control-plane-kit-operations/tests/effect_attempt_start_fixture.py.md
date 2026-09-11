Source: [control-plane-kit-operations/tests/effect_attempt_start_fixture.py](../../../../control-plane-kit-operations/tests/effect_attempt_start_fixture.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

This 208-line fixture supplies optional start-language/service bindings, actual
intent and transition builders, worker authority/fence values and an error
assertion helper. It constructs commands for contract tests, including invalid
candidates that must reach command admission. It has no test methods, service
factory, unit of work, event-ID allocator, persistence operation or provider call.
Its builders alone do not prove durable start, replay or runtime dispatch.

REQUEST_FINGERPRINT is computed at import time from a default
EffectAttemptIntentFixture intent using the production fingerprint function.
OUTCOME_FINGERPRINT is the synthetic b-times-64 string used by settled_transition.
The ordinary imports and initial intent construction are unconditional. Optional
loading therefore does not make this module independent of its imported contracts
or protect it against failures constructing that default intent.

_load_optional loads the language and interpreter modules separately. It returns
None only when ModuleNotFoundError names the requested module exactly; missing
transitive dependencies and other import failures escape. The injected import
function is a loader-test seam. Module attributes are captured with getattr
defaults of None, so adding an attribute afterward does not refresh these bindings.

require_language checks that eight command, result and error bindings are present.
It does not check their types, signatures, behavior or root-export identity.
require_service separately checks the service binding for presence. command calls
require_language but not require_service; the other value builders call neither.
maxDiff=None affects unittest failure display. The export list exposes the helper,
captured bindings, module names, modules and REQUEST_FINGERPRINT, but does not
include OUTCOME_FINGERPRINT.

identity builds the actual EffectAttemptIdentity from RunId, activity text and
attempt number, defaulting to run-a/start-runtime/one. intent invokes
[EffectAttemptIntentFixture.intent](../../../../control-plane-kit-operations/tests/effect_attempt_intent_fixture.py)
as an unbound method with this fixture as self. It does not allocate a new intent
fixture for each call. The inspected method currently uses global helper functions
rather than additional instance methods; changing that dependency would affect
this delegation.

That imported helper constructs a synthetic Docker request with fixed workspace,
plan and graph coordinates, request/run/activity overrides and a default product.
It uses reference-valued credentials and remote authority delivery, then calls the
actual pre-start intent projection. Compensation selects StopNode instead of
StartNode and affects top-level delivery. These values do not resolve credentials,
contact providers or establish live authority. The fixture uses the production
intent fingerprint, so it is not an independent fingerprint oracle.

transition chooses intent or self.intent() by truthiness, computes its actual
fingerprint and constructs a STARTED EffectAttemptTransition. Its identity is the
explicit identity or a fresh default identity using attempt. It does not derive
run/activity from a custom intent, and an explicit identity takes precedence over
attempt. prior_attempt passes through unchanged; the helper does not synthesize a
retry predecessor. The actual
[core transition contract](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/recovery.py)
validates the fingerprint, retry lineage and absence of outcome/recovery values
for STARTED. An attempt-two transition needs a valid predecessor even before the
start command's stricter first-attempt rule is applied.

settled_transition directly builds a SUCCEEDED transition for the default identity
with the synthetic outcome fingerprint. It supplies a negative command candidate,
not a folded state, observed outcome or persisted success. Neither transition
builder invokes an interpreter or establishes an execution lease.

authority constructs the actual
[ExecutionWorkerAuthority](../../../../control-plane-kit-operations/src/control_plane_kit_operations/lifecycle.py),
defaulting to worker-a and EXECUTION_OPERATE. That owner validates scope members
and normalizes them to a sorted, deduplicated tuple; an empty scope tuple remains
constructible. fence constructs the actual
[ExecutionLeaseFence](../../../../control-plane-kit-operations/src/control_plane_kit_operations/execution_leases.py),
defaulting to worker-a and generation seven. Its constructor bounds worker text
and requires an exact positive integer generation within its range. These are
typed claims, not evidence that a durable lease exists or remains current.

command first checks language presence, then takes an intent override with
changes.pop("intent", self.intent()). Python evaluates that default expression
eagerly, so it constructs a default intent even when an override is present.
It removes the transition override separately; an absent or None transition is
built from the selected intent. If that construction raises
RuntimeEffectContractError, it builds a transition from the default valid intent
instead. Only that exception category is caught.

This fallback retains the original supplied intent in the command values. It
does not repair or replace the invalid candidate: its purpose is to let command
admission reject that candidate rather than stop at transition fingerprinting.
An explicit non-None transition bypasses this construction path. A falsey supplied
intent can also select the default through transition's truthiness fallback while
remaining the intent passed to command admission.

The initial values use request-a and freshly constructed default authority/fence,
then remaining changes overwrite those fields. Defaults are evaluated before
overrides, and changing request, authority or fence does not recompute any other
coordinate. Custom intent coordinates likewise require coherent explicit values
if a caller wants a valid command rather than a rejection case.

command inspects dataclasses.fields(StartEffectAttempt). When the exact ordered
field names are request_id, transition, intent, authority and fence, it calls the
normal constructor. The current actual owner has precisely those five fields,
so this is the current path. Otherwise it uses object.__new__, assigns the values
with object.__setattr__ and explicitly invokes StartEffectAttempt.__post_init__.
That compatibility branch bypasses generated initialization but not the explicit
post-init admission call. fields still requires a dataclass; this is not general
support for arbitrary missing or non-dataclass owners. The selected consumer reads
do not establish execution coverage of this compatibility branch.

The complete actual
[start-language owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_start.py)
checks exact command and selected nested types, bounded request text, matching
worker IDs and a reconstructed STARTED transition for attempt one with no prior.
It reconstructs the intent through request/intent projection and requires equality,
then binds request ID, run ID, activity ID and fingerprint to that intent. Invalid
admission produces the fixed InvalidOperationCommand message. These checks are
owned by production code; the fixture's fallback does not implement them.

The owner does not require EXECUTION_OPERATE during command construction. In the
inspected
[service execute path](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_start_interpreter.py),
command validation and the scope check precede opening a unit of work. Durable
authority, replay and first-start checks occur inside that boundary. The fresh
path coordinates start history, intent evidence and attempt insertion before
requesting commit. None of that is performed by this fixture. NewlyStarted checks
an exact record, STARTED status and equal original/latest events; ExistingAttempt
accepts an exact record without requiring STARTED. Constructing either result is
not independent proof of a commit or full reconstruction of a forged record.

Selected
[command-contract consumers](../../../../control-plane-kit-operations/tests/test_effect_attempt_start_contract.py)
exercise malformed request/nested values, intent coordinate mismatches, settled
and retry transitions, retention of the supplied valid intent, and a foreign
transition fingerprint. A class-access-hostile intent case asserts the fixed
command rejection and an empty dispatch list. Those assertions belong to the
consumer, not to the fixture's presence checks or builders. The selected
[interpreter-contract consumer](../../../../control-plane-kit-operations/tests/test_effect_attempt_start_interpreter_contract.py)
constructs an empty-scope command and asserts denial before a FailIfUnitOfWork
callable is invoked. Its separate surface test checks actual root-export identity
and service signatures. These selected reads are not a review of either full suite.

assert_safe_error requires absent cause and context, a combined str/repr rendering
of at most 512 characters and absence of each truthy supplied canary. Empty canaries
are skipped. It asserts properties of an existing error; it does not redact it,
choose an error category/message, inspect tracebacks or bound logs. Safety claims
remain limited to errors and canaries actually asserted by consumers.

Read depth: the complete 208-line fixture and every helper, plus the complete
211-line start-language owner, were read. Selected actual core transition,
authority/fence, imported intent/helper, service initialization/execute and two
consumer-suite paths were checked. The full imported intent fixture and full
service/consumer suites were not reviewed. Validation was documentation-only:
local links, whitespace and frozen-source comparison. No application imports,
tests, database/provider calls, credential access, source/inventory edits or
publication were performed.
