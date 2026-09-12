Source: [control-plane-kit-operations/tests/effect_outcome_evidence_fixture.py](../../../../control-plane-kit-operations/tests/effect_outcome_evidence_fixture.py).
Maintain this document alongside its source file. When the fixture or its actual
outcome, attempt, fingerprint or observation contracts change, verify and update
this companion in the same change.

This 736-line support module constructs synthetic direct-effect outcome stories,
boundary-size values and deliberately malformed objects for Operations contract
tests. It defines no test methods or database setup. It does not invoke a runtime
interpreter, observe a provider, fold a journal, grant compensation authority or
persist evidence. Its assertions become executable only through consuming tests.

The module imports actual Core result/observation values and Operations records.
_load_language dynamically imports effect_outcome_evidence, returning None only
when ModuleNotFoundError names that exact module; missing nested dependencies escape.
Eight public names are then obtained with getattr defaults. require_outcome_language
asserts none is missing. This is not universal collection protection: ordinary
imports, subclass bases and the inherited fixture can still fail before that guard.
outcome_for explicitly calls the guard; other helpers can build predecessor values
without using the new outcome interface.

The fully read [parent record fixture](effect_attempt_record_fixture.py.md) supplies
identity, intent fingerprints, started-state/event construction and error assertions.
Its selected underlying intent builder constructs actual RuntimeEffectRequest and
RuntimeEffectIntent values for StartNode(api), or StopNode(api) in compensation
mode, with synthetic products and authority-reference/delivery values. These are
language objects, not credential resolution or external calls. The outcome fixture
uses the real intent fingerprint for each run/activity and phase rather than an
arbitrary request digest. The larger inherited intent-fixture machinery was not
fully reviewed for this note.

OutcomeStory is a frozen dataclass holding name/profile/value, expected status and
transition, optional failure-row key, compensation flag and an EffectAttemptRecord.
It has no independent constructor validation of those relationships. fingerprint
delegates to the actual Core execution-result or observation fingerprint function;
endpoint_observations simply returns value.observations. Frozen fields do not turn
the fixture into an independent verifier of nested truth.

raw_rows constructs ten rows. The four execution variants map succeeded, failed,
unsupported and uncertain to their corresponding attempt statuses/transitions.
The six observation variants map observed-succeeded to succeeded, observed-failed
to failed, and absent/conflict/indeterminate/observer-unsupported to uncertain.
In particular observer-unsupported is not execution UNSUPPORTED. Its failure
category is operator-review even though the attempt status remains uncertain.

There are eight literal failure rows: the two successes have no failure. Failed
execution/observation use terminal categories; execution unsupported and observer
unsupported use operator-review; the remaining uncertain rows use uncertain.
Codes and messages are fixed runtime.effect-* descriptions. failure_for builds
FailureEvidence with only profile and outcome fingerprint nested under effect_outcome
in BoundedEvidence. It does not copy the provider or observer failure message/details.

The actual [outcome owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_outcome_evidence.py)
has matching execution/observation tables. Its current failed-execution projection
can additionally retain a syntactically admitted namespaced runtime_failure_code.
The fixture's provider-canary code lacks that namespace form, so these base rows
exercise the fallback shape, not that newer code-retention path. The production
record accepts both current and exact legacy failure evidence. The literal fixture
table is useful independent mapping evidence, but fingerprints and typed value
validation still reuse production dependencies.

Endpoint fixtures are HTTP, runtime-private, graph-correlated values containing
synthetic http://service-<suffix>:8080 literal addresses. Execution success and
observation success carry endpoints a/b; observed failure and indeterminate carry
one; observed conflict deliberately carries b/a. Absent and observer-unsupported
carry none, as do the other execution rows. These endpoint facts are not probes,
and their order is input order rather than sorted subject order. No secret endpoint
or HTTP VerificationCompleted value is supplied by raw_rows.

stories repeats all ten rows for ordinary and compensation phases, producing twenty
configured stories. It recomputes phase-appropriate request fingerprints and replaces
the request fingerprint on observation values. Each synthetic state uses identity
run-a/activity-a/attempt one and fence worker-a/generation seven. It directly creates
an original started event at ordinal three/time 2030-01-01T00:00:01Z and a terminal
or uncertain event at ordinal seven/time one second later, then attaches the expected
fixed failure. Compensation changes the intent and event-kind family, not an actual
inverse provider execution. There is no complete seven-event journal here.

The parent builds bounded event evidence containing attempt number and a separately
computed state descriptor hash. Typed EffectAttemptRecord construction checks its
own snapshot contract, but this setup does not establish store ownership, a live
fence, transaction history or durable outcome acknowledgment. Constants such as
workspace-a, event-start and ordinal seven are fixture examples, not runtime laws.

direct_attempt_for constructs alternate snapshots for negative/association tests.
Callers can vary identity, request/outcome fingerprint, status, IDs, ordinals, kinds
and failure. Unspecified request fingerprints are recomputed from the chosen identity
and original phase; unspecified outcome fingerprints still derive from the story's
value. _UNSET distinguishes automatic failure selection from explicitly supplied
None. A changed status chooses the execution failure table, even for an observation
story, enabling typed but incongruent cases. Unsupported combinations may fail during
construction or lookup; this is not a total arbitrary-input generator. Some ID/kind
defaults use truthiness, so empty values can select defaults rather than test emptiness.

recovery_attempt_for deliberately builds a succeeded state with a recovery decision,
using fixed decision-a and a repeated-c prior fingerprint, and a recovered-success
event. It does not perform recovery. The direct-outcome record/projection owner
rejects recovery-bearing snapshots, so a value can be a valid predecessor attempt
yet unsuitable for direct-outcome association. The helper preserves that distinction.

outcome_for wraps an execution story with identity, request fingerprint and result,
or an observation story with identity and the already-correlated observation. Actual
wrapper admission checks nominal/nested types, bounded identities, fingerprints,
result/observation variants and endpoint evidence. Direct outcome records additionally
join original effect ID, identity, request/outcome fingerprint, status, event order,
phase-specific latest kind and fixed failure. No fixture wrapper authenticates the
source of the supplied observations.

observation_ids supplies one-based observation-<story-name>-<index> values. These do
not include the compensation flag and can repeat across separate stories; they are
not globally unique persistence IDs. expected_observation_records independently
constructs rows in endpoint order using strict zip: workspace-a, endpoint subject/
graph/context, latest-event timestamp, UNKNOWN status, TRANSPORT/UNKNOWN probe and
recorded FRESH. Evidence contains the endpoint descriptor under runtime_endpoint.
FRESH here is a recorded flag, not a present-time freshness calculation or health
success. Even a succeeded effect does not make its endpoint HEALTHY.

The selected actual observation projection checks matching attempt identity/request/
status/fingerprint, no recovery, original effect ID and unique caller-supplied IDs
with exact count. Its endpoint branch produces the same UNKNOWN transport rows.
The owner also supports an authoritative HTTP-verification path requiring additional
intent evidence; that path is outside these base fixture stories. This note does
not promote endpoint-only examples into coverage of all current projection variants.

Three size helpers target distinct serialization boundaries. live_result_for_size
adds up to 31 full 512-character evidence fields and a tail to a succeeded result
with z/a endpoints, measuring the whole descriptor with rfc8785.dumps.
observed_result_for_size uses a failed observation, separate x/y evidence mappings,
a fixed seven-character message and two endpoints; it adjusts padding four times
and asserts the exact final RFC8785 byte count. These build selected 8192/8193-byte
vectors, not a generator proven for every requested size. Both raise AssertionError
if their construction cannot hit the target.

endpoint_for_bridge_size instead measures sorted compact standard json.dumps with
default ASCII escaping around runtime_endpoint. Light-bulb characters increase
encoded size while staying within the 512-character subject limit; an ASCII tail
fills the remainder. At or below 4096 it uses the real endpoint constructor. Above
4096 it uses forge_exact to bypass that constructor deliberately. Thus an exact
RuntimeEndpointObservation type at 4097 bytes is not an admitted Core endpoint.
The selected Core endpoint constructor itself enforces the 4096-byte bridge limit.

Actual [Core fingerprint functions](../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effect_observation.py)
hash domain bytes plus RFC8785 descriptor bytes and reject whole outcomes above 8192.
Operations [BoundedEvidence](../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
uses a different sorted compact JSON profile with default ASCII escaping and a
4096-byte bound. These limits are not interchangeable. The local canonical_fingerprint
helper hashes a sorted JSON domain/value envelope with ensure_ascii=False; the
fixture's OutcomeStory does not use it, and it is not the Core fingerprint algorithm.

Hostile subclasses cover attempts, states, events, bounded/failure evidence, str and
int. forge_exact uses object.__new__ and object.__setattr__ to bypass constructors
and frozen assignment. These are test inputs, not production deserialization paths.
assert_fixed_error requires OperationsRecordError and an exact message, then invokes
the parent assertion: cause/context None, combined str/repr at most 256 characters
and absence of caller-supplied canaries. It does not scan traceback locals or logs,
and no generic secret-exfiltration proof follows from the finite canaries.

Selected [evidence-contract tests](../../../../control-plane-kit-operations/tests/test_effect_outcome_evidence_contract.py)
check all twenty predecessor stories, exact 8192/8193 and 4096/4097 vector lengths,
and expected transition/failure values with provider/observer canaries excluded.
Selected [record-contract tests](../../../../control-plane-kit-operations/tests/test_effect_outcome_record_contract.py)
check fixture observation rows and production projection equality/order/timestamp,
plus missing/duplicate/excess IDs. These consuming assertions were read at that
scope, not run or reviewed as complete suites. Shared Core constructors, descriptor
methods and fingerprint functions mean not every expected value is independent of
the implementation under test.

Security boundary: the fixture intentionally retains synthetic provider messages,
detail canaries and private endpoint text in input values. Fixed failure/summary
projections omit selected material; raw ObservationRecord evidence still contains
endpoint descriptors. BoundedEvidence checks structural limits and secret-shaped
keys, not every sensitive string under harmless keys. Do not log fixture input
reprs or infer that all descriptors are safe external reports.

Read depth: full 736-line fixture and full 378-line parent record fixture; selected
underlying intent construction; actual outcome admission/tables/direct-record and
endpoint projection paths; selected Core observation/endpoint/fingerprint and
Operations evidence/failure/observation contracts; named consuming test sections.
No full 1113-line outcome owner, larger intent fixture, Core modules, records module
or consuming suites audit is claimed. Documentation-only work: no imports, tests,
database/provider/credential actions or source changes. Independent review is
required before inventory promotion or publication.
