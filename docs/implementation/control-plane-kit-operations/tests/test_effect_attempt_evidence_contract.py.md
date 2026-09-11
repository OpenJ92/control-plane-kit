Source: [control-plane-kit-operations/tests/test_effect_attempt_evidence_contract.py](../../../../control-plane-kit-operations/tests/test_effect_attempt_evidence_contract.py).
Maintain this document alongside its source file. When the source or relevant
imported contracts change, verify and update this companion in the same change.

These five tests cover public effect-attempt evidence shape, selected state
commitments, bounded evidence admission, one nominal-state rejection and an
import/inventory check. They inherit the
[record fixture](effect_attempt_record_fixture.py.md)
for eight state stories, a local digest formula, subclass probes and safe-error
assertions. There are no local helper functions, database fixtures, provider
operations or executions of effect attempts. The tests construct values and read
source/inventory files; they do not run the record consumer suite named in the
inventory assertion.

The public-shape test requires the three owner bindings and asserts that the
Operations root exposes the identical evidence class, record class and state
fingerprint function. Both classes must be dataclasses with frozen=True. Their
field-name tuples must be exactly (attempt, state_fingerprint) and
(state, original_start_event, latest_transition_event), respectively, preserving
order. This checks metadata and binding identity, not attempted mutation,
deep immutability, field annotations/defaults or every constructor signature.

The commitment test builds the eight ordinary-phase stories plus STARTED attempt
two. For each, the production fingerprint must equal the fixture's local SHA-256
over state.descriptor serialized by json.dumps with sorted keys, compact separators
and ensure_ascii=False, then UTF-8 encoded. All nine expected digests must differ.
It separately compares succeeded with recovered-succeeded and failed with
recovered-failed, and pins the default current STARTED digest to the literal
beginning 43337b1a in source.

The local helper does not call the production fingerprint, but both implementations
share the actual descriptor and the same JSON formula. The literal digest adds a
fixed commitment for one fixture world; it depends on the fixture's current intent
fingerprint as well as the state shape. This is not an independent hand-authored
descriptor oracle, an exhaustive sensitivity test or a general collision proof.
The matrix does not vary compensation, worker/fence, Unicode, key insertion order
or every individual descriptor field.

The actual
[effect-attempt owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempts.py)
requires exact top-level EffectAttemptState before applying that plain SHA-256
formula. Its state digest has no domain prefix. In contrast, the actual
[runtime intent fingerprint](../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effect_observation.py)
hashes the runtime-effect-intent v1 prefix plus RFC 8785 canonical descriptor bytes.
The two canonicalization steps are distinct even though the state embeds the
resulting request fingerprint.

The same test preserves an older selected-only commitment as raw evidence. It
takes the current default intent descriptor and deletes runtime_authority_deliveries
from every product, retaining the selected top-level delivery. It computes the
historical request digest directly with the v1 domain bytes and rfc8785.dumps,
substitutes that digest into a plain state-descriptor dictionary, applies the
state JSON/SHA formula and compares with the literal beginning 6aa8cca9 in source.
It does not decode this descriptor, construct a historical typed intent/state or
feed it to replay, adoption, a store or an interpreter.

The inspected
[product material and recipient admission](../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effects.py)
allows an omitted per-product delivery field in isolation, decoding it as empty.
For the default StartNode world, however, selected top-level delivery must match
the target product's declared delivery. Removing only the product field leaves
that relationship inconsistent. The actual
[intent decoder](../../../../control-plane-kit-operations/src/control_plane_kit_operations/effect_attempt_intent_evidence.py)
reconstructs RuntimeProductMaterial and RuntimeEffectIntent and verifies canonical
bytes; this test bypasses it. The historical literal is therefore not current
admission or backward-compatibility evidence. Decoder rejection is explained by
the inspected contracts, not asserted by an executable rejection row here.

The evidence test constructs EffectAttemptEventEvidence(1, fingerprint) and checks
its exact two-key descriptor. Wrapping it under effect_attempt with
BoundedEvidence.from_mapping and reading descriptor must reproduce the expected
nested mapping. This is a mapping-to-bounded-JSON-to-mapping check, not a dedicated
EffectAttemptEventEvidence decode round trip, exact byte-string assertion or
database codec test.

Seven invalid constructor inputs cover bool and int subclass attempts, zero,
2,147,483,648, a string-subclass fingerprint, uppercase hexadecimal and malformed
secret-canary text. Each must raise OperationsRecordError with the exact fixed
event-evidence-invalid message and pass the inherited safe-error assertion. The
actual constructor requires exact int in one through 2,147,483,647 and exact str
matching 64 lowercase hexadecimal characters. The test does not independently
assert successful admission of the maximum value or enumerate all malformed
length/type combinations.

The separate nominal-state test constructs HostileEffectAttemptState from a valid
state's field dictionary, then requires the fixed state-must-be-typed error and
safe rendering. This is one ordinary subclass rejection. The subclass does not
override descriptor access or track dispatch, and the test does not present
forged exact-type states, hostile nested fields, arbitrary non-state values or
descriptor exceptions. The production fingerprint's exact top-level type check
does not itself perform complete nested-state readmission.

The actual
[BoundedEvidence owner](../../../../control-plane-kit-operations/src/control_plane_kit_operations/records.py)
validates canonical JSON objects with encoded size, depth, item and text limits
and rejects secret-shaped keys. Those broader limits are not exercised by this
small envelope round trip. assert_safe_error checks no cause/context, combined
str/repr length at most 256 characters and absence of supplied canaries. It neither
sanitizes an exception nor examines logs/tracebacks. The evidence rows supply
secret-canary; the nominal-state row supplies no canary. These checks are scoped
error evidence, not universal secret-value detection.

The final test reads the loaded owner's source file and walks its AST. It gathers
names from Import nodes and module names from ImportFrom nodes, rejecting any
name containing postgres, psycopg, store, unit_of_work or transactions. It does
not execute the parsed tree, inspect function calls, follow transitive imports,
discover dynamic imports or prove absence of every external effect. The imported
module has already been loaded by the fixture before this source check runs.
The test's effect-free name should be read as this selected dependency guard.

Inventory location comes from CPK_PACKAGE_MODULE_INVENTORY when set, otherwise
the repository docs/architecture/package-module-inventory.json relative to the
test file. The test selects rows for control_plane_kit_operations.effect_attempts
and requires exactly one, owner "operation", the same module destination, no
optional external dependencies and the ordered list of the three canonical
public exports. Both evidence-contract and record-contract test paths must occur
in protecting_tests; extra protecting tests are not forbidden.

The inspected default
[inventory row](../../../../docs/architecture/package-module-inventory.json)
matches those fields. The assertion does not validate every row, internal
dependencies, known consumers, source path, package acyclicity or whether the
listed record tests run or pass. It is exhaustive only about the one selected
module-row count and the fields explicitly compared. Environment redirection
changes which inventory file the test examines; this documentation check did not
set or inspect an alternate runtime inventory.

Read depth: the complete 213-line source and all five tests were read, together
with the complete record fixture and effect-attempt owner from the preceding
review. Relevant actual root exports, evidence JSON, intent hash/decoder and
product recipient-admission paths and the selected default inventory row were
checked. No consumer suite or full imported codec/records module was reviewed.
Companion validation used local-link, whitespace and frozen-source checks only;
no application imports, executable tests, database/provider calls, credential
access, source/inventory changes or publication were performed.
