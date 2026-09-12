Source: [control-plane-kit-operations/tests/test_effect_outcome_record_contract.py](../../../../control-plane-kit-operations/tests/test_effect_outcome_record_contract.py).
Maintain this document alongside its source file. When the test or relevant outcome
record/projection contracts change, verify and update this companion in the same
change.

This 1,445-line unittest suite contains 22 tests: two predecessor-validity tests and
20 outcome-record/projection contract tests. It protects the pure boundary joining
an execution result or provider observation to an exact attempt snapshot and ordered
observation rows. Its values are constructed in memory. It does not open PostgreSQL,
call a provider, authenticate a principal, persist an outcome or execute compensation.
The suite and relevant sources were read, not imported or executed for this note.

The complete [outcome fixture](../../../../control-plane-kit-operations/tests/effect_outcome_evidence_fixture.py)
provides ten result stories in each of forward and compensation phases: four live
execution result kinds and six provider-observation variants, for 20 stories. The
provider absent, conflict, indeterminate and unsupported variants produce uncertain
attempt states; observer-unsupported carries operator-review failure categorization.
Compensation changes intent and event kinds, not a call to a teardown implementation.
Each story uses actual Core/Operations value constructors and computed fingerprints,
with start ordinal 3 and latest ordinal 7 at fixed synthetic timestamps.

Endpoint stories include zero, one or two RuntimeEndpointObservation values; the
conflict story deliberately orders endpoints b then a. These carry literal internal
HTTP addresses. Failed result/observation fixtures carry provider-detail canaries,
while their expected durable FailureEvidence uses fixed category/code/message and
profile/fingerprint details. This is reference/value evidence, not a provider receipt
or observation of a deployed service.

The fixture's optional loader tolerates absence of its exact outcome module but
rethrows unrelated ModuleNotFoundError. require_outcome_language asserts required
symbols are present; it is not a test skip. Hostile outcome subclasses can be defined
against object when the implementation is missing so collection can proceed to
explicit assertions. Tests importing this fixture would still load its dependencies
and compute fixture fingerprints; none of those imports were run here.

Selected inherited [attempt-fixture helpers](../../../../control-plane-kit-operations/tests/effect_attempt_record_fixture.py)
build identity/state/event commitments and synthetic intents. assert_fixed_error
requires OperationsRecordError with an exact message, then checks that cause and
context are absent, combined str/repr length is at most 256 and supplied canaries are
absent. This strong helper is used by many hostility cases. The HTTP negative tests
instead use assertRaisesRegex and do not all make those error-safety assertions.
HostileStr/HostileInt and record subclasses are plain subclasses; forge_exact bypasses
constructors with object.__new__/object.__setattr__. These fixtures are not a general
matrix of malicious attribute-access, equality or mapping hooks.

The [outcome owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_outcome_evidence.py)
revalidates admitted outcome values and reconstructs an EffectAttemptRecord for
snapshot validation. It then joins identity, original effect ID, request/outcome
fingerprints, direct terminal state, phase/event kinds and fixed failure projection.
The complete [attempt owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempts.py)
checks exact nested types and commitments from original/latest event evidence to
started/current states. These checks compare supplied values; neither owner queries
whether the snapshot is current in a store or authorizes a recovery decision.

The two predecessor tests establish that expected observation fixtures are ordinary
valid ObservationRecord values with the intended count, subject order and latest-
event time, and that selected alternate attempts/row mutations construct through
their earlier record contracts. The alternate attempts change identity, start event,
request fingerprint, terminal status, outcome fingerprint or recovery resolution.
The valid row mutations change IDs, workspace, time, subject, graph, evidence or
probe/status/context/freshness fields. Their later rejection therefore tests the
new outcome agreement boundary rather than relying only on earlier constructor
failure. Separate exact-forgery tests intentionally bypass those constructors.

The record-shape test fixes the four dataclass fields: workspace_id, outcome, attempt
and endpoint_observations. For all 20 stories it projects rows, constructs a record,
requires the original attempt object to be retained by identity, compares all rows
with fixture expectations and checks the outer descriptor containing workspace,
outcome descriptor, transition-event identity/ordinal and observation count. The
nested expected outcome descriptor is obtained from the outcome's own descriptor()
method, so this is not an independent golden oracle for every nested field. The
field check also does not directly assert frozen/slotted dataclass properties.

Workspace revalidation is tested directly on a record with no observation rows,
without the projection helper. Exactly 512 characters are accepted. Empty, 513-
character, NUL/newline, surrogate and string-subclass values must fail with the fixed
record error and selected canaries hidden. This validates coordinate shape; it does
not prove that the workspace exists or is the authenticated workspace. A separate
outcome-preimage test forges outcome subclasses and exact values containing hostile
identity, request-fingerprint, runtime effect-ID or observed request-fingerprint
fields. All seven candidates still pass an isinstance check before the new record
boundary must reject them.

The snapshot-binding test rejects six otherwise constructible mismatches against a
success outcome: another run/activity identity, another original effect event,
another request fingerprint, another status, another outcome fingerprint and a
recovery-resolved attempt. The direct-outcome record must not accept a later recovery
snapshot merely because that snapshot is lawful in the broader attempt language.
This is not a recovery interpreter test or a prohibition on retries as such.

The retry test constructs a lawful failed attempt 2 with prior attempt 1, valid
start/latest commitments and the corresponding result. Both the outcome record and
observation projection must accept it, retaining the attempt and yielding no rows.
Five forged variants then alter original commitment, latest commitment, fence worker
text, outcome-fingerprint type or prior-attempt number type. Each is rejected at
three boundaries: the predecessor attempt constructor, outcome-record constructor
and observation helper, with their respective fixed error messages. This protects
revalidation of selected retry structure; it does not exercise lease acquisition,
a retry workflow or concurrent attempts.

Six latest-event mutations reject reused start event ID, nonincreasing ordinal,
foreign run, foreign activity, wrong compensation event kind and a forged NUL-bearing
event ID. Conversely, two valid distinct terminal event IDs produce distinct outcome
records. The laws permit differing event identities that still satisfy the join;
they do not prescribe a deterministic generated event ID. The test title's word
only should not be read as an exhaustive classification of every possible invalid
event graph.

Failure agreement is tested with a valid failed attempt whose latest failure is
removed or whose code is changed to a private canary. Both attempts construct under
the predecessor contract but are rejected by EffectAttemptOutcomeRecord. The fixture
uses fixed legacy profile/fingerprint failure details. The source also admits its
current fixed failure projection, which may include an admitted runtime_failure_code;
this file does not separately characterize that optional code grammar or all
current-versus-legacy compatibility cases.

The projection law checks that the helper signature has no observed_at argument,
then compares exact tuple type, exact ObservationRecord rows, caller-supplied ID
order and endpoint subject order for all 20 stories. Expected time is copied from
the snapshot's latest transition event. Too few, duplicate or too many IDs fail.
There is no wall clock, timestamp chronology test or proof of fresh external truth:
FRESH is the projected freshness value, and the event time is supplied evidence.

The 4,096-byte bridge test uses the fixture's escape-expanding Unicode/padding
construction to make one endpoint evidence document exactly that encoded size.
Projection must preserve it as one exact ObservationRecord with 4,096 UTF-8 bytes
in canonical_json. This file does not test the adjacent 4,097-byte rejection or the
separate 8,192-byte runtime-outcome limit; fixture helpers for other sizes are not
coverage merely because they are imported. The selected
[bounded/observation records](../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
provide the bridge encoding and row validation, while Core fingerprinting has its
own outcome admission boundary.

Projection coordinate tests accept a 512-character workspace and two 512-character
observation IDs. Thirteen hostile combinations exercise workspace/string subclasses,
empty/oversized/control/surrogate text, integer-subclass IDs, list IDs and a tuple
subclass. They require the fixed projection error and selected canary omission.
Another test supplies two different valid observation-ID tuples to otherwise
identical rows, requires distinct outcome records and explicitly compares every row
field other than observation_id for equality. Observation identity is therefore
caller-supplied record identity, not secretly recomputed from endpoint content.

The exact-row-container test rejects a list, tuple subclass, observation-row subclass
and exact forged row containing a string-subclass workspace. The row-agreement test
then uses exact tuples of independently constructible rows to reject missing/extra/
duplicate/reordered rows, wrong workspace/time/subject/graph/evidence, wrong probe
kind/outcome, public context, healthy status, stale freshness and an empty tuple for
a two-endpoint result. It also rejects an extra row on an observed-absent result.
These cases characterize the RuntimeEndpointObservation inverse: rows remain UNKNOWN,
transport/UNKNOWN, fresh, snapshot-timed and congruent with each endpoint's graph,
context and bounded descriptor. They do not establish the analogous complete inverse
for every verification-completion type.

The final forgery matrix puts plain subclasses or constructor-bypassed exact values
inside attempt state, events, bounded evidence, failure, identity or original start
evidence; it also includes an attempt-record subclass. All nine supplied snapshots
must fail at the new record boundary with the fixed safe error. Some candidates
combine multiple malformed fields, so their rejection does not isolate every field
as an independently decisive guard. The source uses selected nominal checks and
catch sets; these tests do not prove total fixed-error handling for arbitrary Python
objects or hostile __class__/attribute-access behavior.

HTTP verification has a separate helper using selected
[intent fixtures](../../../../control-plane-kit-operations/tests/effect_attempt_intent_fixture.py)
and the actual
[intent-record contract](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_intent_evidence.py).
It replaces a synthetic product's verification contract with a root-response HttpCheck,
constructs the resulting runtime intent, recomputes its request fingerprint and joins
the intent record to the same identity/original event as the outcome attempt. Other
fixture product ports, synthetic OCI coordinates and secret/delivery references stay
value data. No image is pulled and no HTTP request is sent.

The selected [Core verification values](../../../../control-plane-kit-core/src/control_plane_kit_core/verification.py)
carry HTTP status, response size, expected digest and optional digest-match evidence.
The helper builds a PASSED result or a FAILED runtime result containing a completion
and a provider-body canary. By default PASSED/FAILED include evidence, while
MALFORMED does not. This arrangement permits comparison of authoritative intent with
returned evidence instead of trusting a result-provided path/socket.

The successful HTTP test compares the entire projected row. The subject is independently
constructed from the versioned node/check document using RFC8785 and SHA256, matching
the owner's subject rule. Expected evidence includes run/activity/attempt/effect,
node/check, intent-derived socket/path, status/size and expected digest/match. Row
status is VERIFIED, probe outcome HEALTHY, graph comes from completion identity,
time from the terminal snapshot and context is RUNTIME_PRIVATE. The expected subject
uses the same canonicalization library; it is a schema/preimage assertion rather
than an independently hardcoded digest vector.

Failure projection tests cover FAILED/body-mismatch and MALFORMED/response-oversized,
checking status/probe outcome, stage/category, match value and the exact 14-key
payload shape. Provider-body canary, observed_body and observed_body_sha256 must not
appear. A further test derives status-mismatch from HTTP 503 without expected-body
intent and transport-unavailable from missing evidence. A FAILED completion with
successful status and body-match evidence is rejected because it cannot support a
failure category. Another changes check identity to foreign-check by replacing the
completion, result and outcome, but passes the unchanged attempt. The projection
compares that attempt's retained outcome_fingerprint with the changed outcome before
looking up the authoritative HTTP check. The test therefore requires rejection but
does not isolate the missing-check guard: the earlier fingerprint mismatch confounds
that claim.

Those HTTP tests all call effect_outcome_observation_records with an intent record;
they do not construct EffectAttemptOutcomeRecord from their resulting HTTP rows.
The source helper's authoritative check requires congruent intent/attempt/effect
coordinates and exactly one matching product/check, then checks result evidence
against the pinned HttpCheck. The record constructor itself has no intent_record
field: its HTTP matching helper checks retained completion/row consistency and only
shape-checks provider_socket/path. This suite therefore does not prove that a
standalone record reconstruction independently rederives those fields from protected
intent. Missing intent, wrong graph/node, multiple matching materials/checks, altered
pinned intent, timeout/rejected completions and HTTP-row tampering are not directly
covered here.

The repr test constructs one successful endpoint outcome record and requires both
str/repr to omit raw success data, effect/event IDs, internal URLs and observation
IDs. The source hides outcome, attempt and rows with repr=False. This is repr
suppression, not erasure or encryption: retained rows intentionally contain endpoint
material, and the descriptor exposes selected identities/fingerprints. No assertion
here establishes universal redaction of every record, workspace string, error or
nested payload.

Reading depth is the complete test, 1,113-line outcome owner, 736-line outcome fixture
and 294-line attempt owner. Selected reads cover inherited fixture error/identity/
intent/event construction, product/intent fixture builders, protected intent
construction/encoding, observation/bounded records, Core verification values and
runtime outcome fingerprint entry points. These are not full audits of both inherited
fixture files, Core verification/fingerprint internals or persistence/effect adapters.
No source, tests, inventory or publication was changed. The separate
test_effect_outcome_evidence_contract.py companion remains outside this slice.
