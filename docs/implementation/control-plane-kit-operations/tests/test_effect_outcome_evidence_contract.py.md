Source: [control-plane-kit-operations/tests/test_effect_outcome_evidence_contract.py](../../../../control-plane-kit-operations/tests/test_effect_outcome_evidence_contract.py).
Maintain this document alongside its test. When the test, owner or relevant imported
contracts change, verify and update this companion in the same change.

This 1946-line suite contains 22 tests: two predecessor checks and twenty contract
checks. It protects the pure direct-outcome evidence boundary, including its public
value shapes, transition/failure interpretation, bounded fingerprints/projections,
selected hostile-object rejection and declared architectural surface. Its motivation
is to prevent provider output from becoming arbitrary operational failure text or
unvalidated attempt evidence. It constructs values and reads source/inventory files;
it has no database fixture or provider client. No tests, imports or source analysis
were executed while writing this companion.

The governing [effect_outcome_evidence.py owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_outcome_evidence.py)
defines ExecutionEffectOutcome and ObservedEffectOutcome, their union/profile,
EffectAttemptOutcomeRecord and three transition/failure/observation projection
functions. Core owns the runtime result/observation languages and fingerprints;
Operations owns their correlation to attempt/event records and bounded evidence.
These tests mainly exercise outcome construction, transition/failure projection and
descriptors. They do not apply transitions, persist attempts or dispatch effects.
The current-versus-legacy FAILED record case is a focused overlap with the separate
[record-contract suite](../../../../control-plane-kit-operations/tests/test_effect_outcome_record_contract.py).

Both classes inherit the shared
[outcome fixture](../../../../control-plane-kit-operations/tests/effect_outcome_evidence_fixture.py).
Its twenty stories are four execution results plus six observer results, each in
normal and compensation phases. The stories carry actual Core values, direct
EffectAttemptRecord snapshots, original/latest events, expected status/transition
and fixed failure projections. The underlying
[attempt fixture](../../../../control-plane-kit-operations/tests/effect_attempt_record_fixture.py)
uses state fingerprints in event evidence and derives request fingerprints from the
selected [intent fixture](../../../../control-plane-kit-operations/tests/effect_attempt_intent_fixture.py).
That intent describes synthetic product/runtime material; it does not create a
container, contact a registry or resolve its secret references.

The fixture's module loader tolerates the specific target module being absent and
leaves missing exports as None; transitive ModuleNotFoundError is re-raised. Contract
tests call require_outcome_language, so absence becomes an assertion rather than an
intentional skip. Hostile outcome classes use the real base when present, or object
when absent, allowing that assertion path to remain collectable. These mechanics
do not prove successful collection in every broken environment.

The first predecessor test requires exactly twenty named phase/story combinations,
matching attempt status, original effect/event ID, phase-derived request fingerprint,
absent recovery decision and matching outcome fingerprint. It establishes the
fixture's direct-attempt world before the new wrapper is tested. Expected outcome
fingerprints come from the same actual Core fingerprint functions used by the owner;
this is composition evidence, not an independent cryptographic known-answer test.

The second predecessor test measures RFC8785 result and observation descriptors at
8192 and 8193 bytes and the Operations runtime_endpoint envelope at 4096 and 4097
JSON-encoded bytes. Endpoint objects have exact nominal type, but the oversized
endpoint is deliberately forged by the fixture because normal construction rejects
it. Therefore the method name's valid-predecessor language must not be generalized
to constructor-valid values at every boundary. The fixture uses different encodings
for Core canonical outcome size and the Operations evidence envelope.

That same test sends a tiny import-alias/call example through the shared architecture
analyzer and exact policies, requiring no findings. It is a positive wiring canary
for importing sample.tools.inspect as inspect_value and resolving the subsequent
call. It does not prove the helper's complete negative-case behavior.

Public-surface assertions require the two profile values in order and exact dataclass
field-name tuples: identity/request_fingerprint/result and identity/observation.
All eight intended public names must appear in Operations __all__ and be identical
to the objects exported by the owner. The earlier test named exact_public_surface
only checks that none of those names is missing; it does not prohibit unrelated or
additional root exports. The standalone inventory test only requires one matching
module row. Stronger selected inventory-field comparisons occur in the final test.

For every story and phase, transition projection must return an exact Core
EffectAttemptTransition with expected kind, identity and fingerprint. Failure must
equal the fixture's latest-event failure and, when present, have exact FailureEvidence
type. Provider/observer message and detail canaries must be absent from its rendered
str/repr. Execution outcomes map to succeeded/failed/unsupported/uncertain; observed
success/failure map to succeeded/failed, while absent/conflict/indeterminate and
unsupported observation remain uncertain. Failure-category expectations come from
the fixture's fixed rows; observer unsupported retains operator-review category.
These assertions do not establish retry authority from an uncertain observation.

The focused runtime-code test gives FAILED execution the code
docker.secret-resolution-reference-not-found and eight injected raw-data canaries
in its message/details. Its failure projection must contain exactly profile,
outcome_fingerprint and runtime_failure_code, with all eight raw canaries absent
from failure str/repr/canonical JSON. The raw result is still fingerprinted and
retained by the outcome; this assertion concerns the projected failure.

The grammar test describes the code syntax with an ASCII regular expression and a
128-character maximum. It tests ten rejected forms: missing namespace, whitespace,
slash, colon, equals, URL, uppercase, Unicode, control character and overlength.
Each is accepted as part of a Core FAILED result but omitted from the Operations
failure details, preserving only profile and fingerprint. A separate candidate
places a valid-looking code in message/details while its code field is invalid;
the projection must not mine those fields for a replacement. The owner's manual
grammar admission is syntactic, not a fixed registry or a universal secret detector.
This suite does not exercise every grammar edge or a positive exactly-128-character
code.

The current/legacy compatibility test uses one normal-phase FAILED execution with
the valid runtime code. It rebuilds the attempt with the new outcome fingerprint
and candidate latest failure, then constructs a full EffectAttemptOutcomeRecord.
Both the current details and exact legacy profile/fingerprint-only details must
survive unchanged. Six inner-detail mutations and four outer failure mutations must
raise the fixed record error: extra/missing fields, wrong profile/fingerprint/code,
and changed category/code/message/details are not compatible legacy evidence.
This proves pure constructor compatibility for that case, not stored-row migration
or a database round trip.

The test named nonfailed_and_observer_failure_rows_remain_byte_exact checks execution
success has no failure, unsupported/uncertain omit even a valid runtime code, and
normal-phase observer failures equal the legacy fixture projection. Unsupported and
uncertain compare decoded canonical JSON mappings; observer cases use dataclass
equality. It is not a frozen database-byte fixture or every possible provider payload.

Descriptor tests cover all twenty stories and require exactly profile, identity,
effect ID, request/outcome fingerprints, transition kind and observation count.
Ten injected evidence/message/address canaries must be absent from outcome str/repr
and the public descriptor. These checks protect the compact exterior; they do not
prove deep immutability or prohibit access to retained raw result objects.

Nominal rejection cases include subclassed attempt identity, RuntimeEffectResult,
observed-success value and outcome wrappers. The wrapper-subclass candidates are
constructed inside lambdas passed toward projection functions; construction can
reject them before those projection functions run. Separate request-coordinate
cases reject a string subclass, malformed execution fingerprint and an exact forged
observer value with malformed fingerprint. They do not independently prove every
outcome-to-attempt join, which belongs to the record-contract suite.

Effect-ID boundaries accept 512 characters and reject a subclassed string, 513
characters, NUL and a surrogate using forged exact Core results. Despite the test's
Postgres-text-domain name, it makes no SQL call. Attempt-identity boundaries accept
200-character run/activity IDs and attempt 2147483647, retaining the exact supplied
identity object. Ten forged variants cover scalar subclasses, zero/overflow attempt,
NUL/surrogate/oversized run, oversized activity and slash-bearing activity. These
are selected vectors, not exhaustive enumeration of Core's identity grammar.

Both execution and observer wrappers accept complete 8192-byte canonical outcomes
and reject 8193-byte candidates with the fixed evidence error. Expected hashes are
again the actual Core functions. The endpoint bridge test separately accepts the
4096-byte endpoint in both profiles and preserves its object identity, then rejects
4097-byte or subclassed endpoints placed in forged exact results. This test exercises
wrapper admission, not effect_outcome_observation_records or a persisted observation
row. The selected [Core fingerprint owner](../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effect_observation.py)
and [endpoint contracts](../../../../control-plane-kit-core/src/control_plane_kit_core/probe_intents.py)
own the underlying canonicalization, constructor and descriptor bounds.

One direct invalid-kind case checks candidate-free errors. A larger hostile-object
test covers nine families: spoofed run ID, observation evidence/failure, endpoint,
literal material, context, protocol, protocol transport and protocol application.
Several unrelated classes copy module/qualname metadata; forged exact containers
place them at the new boundary. Their selected property/descriptor hooks record a
dispatch and raise a canary. Each constructor must leave its dispatch list empty
and raise exactly OperationsRecordError with the fixed message. A separate list-valued
result kind must also produce that exact error instead of leaking an unhashable-key
exception. Metadata resemblance is not admission authority.

That test also accepts a real secret-reference endpoint and preserves its material
identity. Another test forges exact SecretEndpointMaterial with a non-secret:// value
and requires rejection, exercising reference revalidation beyond nominal type.
A non-tuple observations object with a hostile iterator must be rejected with no
iteration. These concrete no-dispatch witnesses do not prove that every Python
forgery, dynamic attribute or nested verification object is safe.

The inherited assert_safe_error requires absent __cause__ and __context__, a combined
str/repr rendering at most 256 characters and absence of supplied canaries. Most
fixed-error cases use assertRaises(OperationsRecordError); the dispatch tests catch
BaseException and additionally require exact exception type. The owner converts
selected validation failures to fixed errors, rather than catching every possible
dependency/object exception. Finite canary checks must not be advertised as a
general redaction theorem.

The final test reads the imported owner's source with inspect.getsourcefile, analyzes
it under a declared package-relative path/module and evaluates exact import and
lexical-call policies. The expected call tuple preserves duplicate occurrences and
includes two unresolved targets. The shared helper parses Python's stdlib AST,
projects imports and lexically resolved alias/name/attribute calls, sorts the
location-free multisets and compares them exactly. Matching these lists does not
establish runtime reachability, implicit property behavior or the effects of imported
functions. Refactors that preserve behavior can still change the required surface.

The inventory path comes from CPK_PACKAGE_MODULE_INVENTORY or the repository's
[package-module inventory](../../../../docs/architecture/package-module-inventory.json).
The final assertions require owner operation, destination equal to the module,
the exact eight canonical export names, seven declared internal dependencies and
an empty optional-external-dependencies list. The seven-name inventory list omits
control_plane_kit_core.verification and the Operations effect_attempt_intent_evidence
module even though the current source and exact import expectations include them.
Thus these assertions can agree with the current inventory while that dependency
list is incomplete. No inventory or test correction was made in this pass.

Reading depth was the complete suite, complete 1113-line outcome owner and complete
736-line outcome fixture; selected inherited attempt-fixture helpers, intent/product
construction, Core result/fingerprint/endpoint/identity/verification and Operations
record contracts. The shared architecture package's analyzer/alias/call extraction,
exact-policy values/projection/evaluation were read at selected-definition depth.
Its source tree in the available mirror matches the commit pinned by the package
[test runner](../../../../control-plane-kit-operations/test.sh),
7ebc362da40e9d7b2bdf78357e6ed8abd9a275ef, by a read-only Git comparison. No helper,
test runner or application module was executed.

HTTP verification lookup/category behavior, ordered observation-row inversion,
database persistence/replay, concurrency, provider health and actual effect execution
need their own owner-level evidence. This companion does not extend the suite's
proof to those boundaries or release any live acceptance gate.
