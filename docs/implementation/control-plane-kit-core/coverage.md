# Core implementation companion coverage

Scope: tracked `control-plane-kit-core/**` paths at base
`087a89253b14b3bb438af9042ea779976886a79e`, inventoried for
[#1801](https://github.com/OpenJ92/control-plane-kit/issues/1801), under
[#1799](https://github.com/OpenJ92/control-plane-kit/issues/1799).
This is a rollout index, not a companion to a source file or a recurring
per-file freshness ledger. The historical package-module inventory is not this
scope's source of truth.

167 tracked paths: 12 pending, 0 authored,
147 reviewed, 8 excluded.
“Authored” means the note exists after author source inspection. It does not
claim peer approval or fresh executable validation. Peer-review depth and source
coordinates belong in the batch PR. The first calibration is not completion
of Core coverage.

Calibration review: North read both implementation owners and their notes in
full, checked consequential contracts/imports and sampled governing test
navigation. Meridian separately checked the Operations information/authority
boundary. These are source reviews, not a new executable suite result or an
exhaustive review of every test assertion. Review dispositions are recorded in
the calibration PR. North additionally read all seven foundation source
owners and their twelve companions, with targeted identity/lifecycle/protocol
test sampling. North then reviewed all sixteen configuration/verification and
harness/navigation notes, directly checking the configuration/renderer/capability
owners and full shell harness, with targeted verification/source-test checks.
This does not claim a full verification-file audit. No executable validation
was added. North reviewed the twelve graph notes, reading the full compiler,
checking graph construction, codec/validation/diff and disclosure contracts,
and sampling relevant test bodies. The secret-reference fingerprint wording
was corrected against the exact descriptor variants before publication.

North reviewed the next eleven planning/policy notes: all five substantive
source owners and the planning facade in full, both policy/approval-subject
test files in full, and selected activity/codec/compiler assertions. The
missing start-draft compiler lookup was independently confirmed from source;
no executed reproducer or live-impact claim is made. The separate follow-up
is not part of coverage completion or the held deployment diagnosis.
North then read the full recovery owner and its complete test file, including
fixtures, and both companions, checking the previously reviewed compiler,
activity and policy contracts. No documentation findings remained.
North also read the full 90-line public-key identity owner, full 80-line test
and both companions; normalization, fingerprint and projection limits matched
source. No cryptographic or runtime execution was claimed.

North reviewed all six saga/scenario notes. He directly read saga syntax,
state/transitions, compensation, schedule/evidence checks and the full journal
projection, omitting selected simple command/event declarations. He checked
scenario constructors, identity projection, expectation generation, catalogue
membership and consequential graph helpers. Compensation behavioral tests and
selected saga, scheduling and scenario assertions were read. Downstream
coordinator/advancement use was checked narrowly. This was not a full read of
both large source files or all associated tests; no documentation findings
remained and no executable validation was performed.
North then read the full 653-line probe-intent owner, both notes and selected
policy/descriptor/outcome tests. TimeoutPolicy's finite-timing gap and direct
intent-kind override limits were confirmed from source, without executed or
downstream-impact claims. No documentation findings remained.

North read both control-contract and control-route owners and all four notes in
full, the complete route test, and selected contract validation, projection and
patch assertions. Raw values, snapshot redaction, direct construction limits
and route metadata versus enforcement matched source. No documentation findings
remained; no executable validation was performed.

North read the full runtime-authority owner, exact recipient validator and all
six authority companions, with selected observation paths, test bodies and
fixture inspection. The observation-carrier rejection wording was qualified to
connection grants requiring a carrier. This does not claim full reads of every
test body or executed validation. No documentation findings remained.

North read the full 260-line transaction owner, full 125-line governing test,
both companions and actual Postgres unit-of-work owner, with selected imported
service binding/program-role contracts. The author read that imported owner in
full; the reviewer did not claim a full imported-service audit. Declaration
limits and negative-test guard precedence matched source. No documentation
findings or executable validation were added.

North completed the service-language owner read across the transaction-context
inspection and remaining stage decoder lines, and read the full 164-line test
and both service notes. Role normalization, stage mapping/predecessors, custom
false handoff flags, falsy canonical fallback and the static-import guard's
limited scope matched source. No companion changes or executed orchestration
were claimed.

North read the full 420-line public-ingress owner, full 157-line test, both notes,
exact graph-codec ingress references and the actual origin helper. Hostname
wording was corrected to distinguish the lowercased ASCII-label check from
retained original text. The author's additional authority-context reading is
not attributed to this independent review. No source fix or executable
validation was performed.

North read the full 320-line delegation-authority owner, full 331-line test,
both notes, actual 16-KiB environment binding, PEM-omitting diff projection and
graph-codec binding checks. His prior full key-owner review supplied lexical
PEM/fingerprint context. Intent, constructor/rendering limits, the single-node
projection slot and planned operations versus provider results matched source.
No companion correction or executable validation was needed.

North read the full 444-line gateway-delegation owner, full 342-line test and
both notes, plus selected actual target admission and Operations grant
construction. Unsigned values/digests versus signature/replay authority, partial
path validation, lifetime versus clock checks and type-name test limits matched
source. No documentation correction or executable validation was required.

North read the full 531-line process-contract owner, full 155-line test, both
notes and shared security assertion, with selected actual HTTP/MCP/handoff
boundaries and previously read verification context. Declaration/effect
separation, constructor limits, optional-dependency asymmetry and the finite
shutdown guard matched source. No documentation correction or executable
validation was required.

North read the full 598-line persistence owner, full 154-line test and both
notes, with the actual enforcement enum and selected PostgresStoreBundle
declarations; prior service/transaction/unit-of-work review supplied context.
Canonical counts versus duplicate-admitting coverage, accepted flag combinations,
role vocabulary and declaration/effect limits matched source. No documentation
correction or executable validation was required.

North read both full root/operations facades (706/303 lines), both companions,
the full package-boundary test and selected deployment/compensation import and
fixture portions. Selected exports, eager imports, root/subpackage differences
and the finite static test limits matched source. This does not extend review
to every transitive owner; no executable validation was performed.

North read the full MCP contract (269 lines), full test (124 lines) and both
companions, checking actual Protocol construction, compatibility and canonical
decoding; his prior full process review supplied consumer context. Identity and
StrEnum admission limits, path/codec/error boundaries and declaration versus
transport conformance matched source. No corrections or executable validation
were required.

North read the full projection-contract owner (611 lines), full test (515 lines)
and both notes, retaining the previously read full security helper. Actual
HTTP scope/safety/schema and parity constructor/factory/overview binding were
checked selectively. Canonical associations versus wider admission, boolean
page limits and declaration versus adapter evidence matched source. No
correction or executable validation was required.

North read the full effect-recovery owner (679 lines), full test (750 lines),
RunId/activity/run identity helpers and both companions. Actual Operations
fold authority/historical-fence and start transaction/persistence paths were
checked selectively. Local fold consistency, retry lineage, evidence matching,
character/integer bounds and event-vocabulary test limits matched source.
No source change or executable validation was performed.

North read both history-contract test files in full (33 and 96 lines) and their
companions, checking selected exact HTTP/parity route and tool entries with
prior full projection/MCP context. Route/projection aliases, ten/100-item
declaration checks, positive-only assertions and unasserted fields matched
source. These tests do not prove adapter behavior, authorization, pagination
execution or universal association integrity. No executable validation occurred.

North read the full draft-catalogue/selection contract tests (60/33 lines), both
notes and complete program/transaction/UoW fixture helpers. Selected exact
HTTP/parity/projection entries were checked with prior projection/MCP context.
Positive declarations, byte-bound limits, unasserted fields and catalogue
approval metadata matched source. North checked whitespace/source guards;
Kepler checked nine local links, acknowledged but not rerun by North. No
corrections or executable validation were required.

North read the full failed-run compensation owner (427 lines), test/helpers
(265 lines) and both notes. He checked selected recovery/RunId construction,
standalone operation codec and target helpers, PlannedActivity validation,
compensation selection, facade exports and Operations admission lines 165–360.
Structural evidence/order versus durable inverse selection, decoder limits and
test assertion boundaries matched source. Source/whitespace guards passed;
Kepler's link check was acknowledged. No corrections or executable validation
were required.

North read both OCI image-reference and product-reference tests (170/131 lines)
and their companions in full, checking selected actual product-module imports,
constants, identity/reference/OCI values and codecs, document construction/hash,
catalogue projection and validators. The OCI AST guard's import-root count was
corrected from nine to eight before PASS. Digest identities, constructor/codec
differences and selected assertion limits matched source. Six links, whitespace
and source guards passed; no full products.py audit or executable validation
was claimed.

North read the full identity/catalogue tests (155/133 lines), their companions
and the complete catalogue class, retaining the immediately preceding independent
identity/codec/constants/validator and document/hash review at the same source.
Exact rejection examples, structural ordering, differing duplicate policies,
the concrete associativity example and finite AST/digest evidence matched source.
Seven links, whitespace and source guards passed. No correction, full products.py
audit or executable validation was required or claimed.

North read both descriptor/hardening tests (269/158 lines), all their helpers,
both companions and the complete bounded-snapshot implementation. He retained
the actual document/hash/codec and catalogue reviews and checked selected
runtime/container decoding, protocol, text, configuration and OCI validators.
Mapping normalization, exact budgets, one-shot fixture limits, differing error
disclosure assertions and non-isolating negative fixtures matched source.
Nine links, whitespace and source guards passed. No correction, executable
validation or full products.py review credit was added.

North read the complete container-product test (138 lines), its companion and
runtime-contract construction/descriptor, retaining the immediately preceding
product/runtime codec, identity/OCI and text-validator reviews. Fixture-level
hash/frozen assertions, isolated variant rejection, metadata examples and the
eight-import/ten-annotation source guard matched source. Three links, whitespace
and source guards passed; no corrections, executable validation or full
products.py review credit were added.

North read both instantiation/pipeline tests (340/156 lines) and their notes,
checking actual product configuration/materialization/instantiation and matching
helpers, selected secret keys, topology compiler/node/codec propagation,
metadata diff and activity reconciliation/start/readiness contracts. Exact key
matching versus configurable values, inactive unconnected requirements, planned
addresses and assertion/readiness limits matched source. Ten links, whitespace
and source guards passed; no corrections, executable validation or new full
products.py/compiler review credit were added.

North read the full runtime-contract test (755 lines), hostile fixtures and
companion. He retained actual runtime constructor/descriptor/codec/materializer
context and checked exact socket admission, socket/port/lifecycle/path helpers,
algebra sockets, graph validation loops and selected environment/configuration
contracts. The static-import wording was corrected to acknowledge local import
aliases remain covered. All 23 tests' stated limits matched source; six links,
whitespace and source guards passed. No executable validation, inferred code
defect or full products.py audit was claimed.

North read the full private public-wire owner (163 lines), ownership test
(197 lines), both companions and both JSON fixture inputs. He independently
checked the selected consumer guard, error and size-bound paths. Finite lexical
classification, one ASCII decoding pass, shape/material precedence, exact epoch
type, selected canonicalization failures, actual fixture consumption and AST
limits matched source. Nine local links, whitespace and source guards passed;
no corrections, imports, tests or provider execution were required. Fixture
dependency reads add no companion coverage.

North reviewed the full public-material fixture, full 284-line test and both
companions, retaining the shared wire owner and checking selected actual command
language constructors, codecs, representations, enum/field guards and the public
contract. He also checked selected surface-read fixture/helper/issuer paths.
All six tests' described limits matched source: field-specific grammar,
issuer-only vectors, scalar-only negative cases, distinct error assertions,
ten selected repr cases versus descriptors, self-derived digest and four raw
source-text exclusions. Nine links, whitespace and source guards passed. No
corrections, execution or full large-owner coverage credit were added.

North reviewed the full canonical-wire fixture (151 lines), full 195-line test
and both companions. He checked actual weighted-state, grant-codec and raw/numeric
parser paths, selected workload-wire and graph-reference tests, the dependency
pin and public contract, with retained request/grant/shared-owner context. All
eight tests' stated limits matched source: stored expectations versus computed
equality, number substrings, mapping versus raw decoding, and the first-issued
epoch rejection. Twelve local links, whitespace and source guards passed. No
corrections, execution or full large-owner coverage credit were added.

North reviewed the full graph-reference test (336 lines), topology test (79
lines) and both companions, with actual reference/codec/wrapper/export paths and
the complete governing topology document. Nominal-role and codec evidence,
producer provenance, finite textual exclusions and topology substring guards
were accurately distinguished. Historical prefix/adopter decisions were not
presented as current readiness. Ten links, whitespace and source/test guards
passed; no corrections, execution or full large-owner credit were added.

North reviewed the full workload-wire test (544 lines), helpers and companion,
full transit fixture and retained canonical fixture. He checked actual selected
transit constructor/codec/parser, surface/probe type and codec, and shared
request/grant/raw numeric parser paths. The notes correctly distinguish twenty
object and twenty descriptor substitutions from six raw substitutions, synthetic
maximum-grant claims, limited malformed canaries and process-global recursion
restoration. Eight links, whitespace and source/test/dependency-declaration
guards passed; no corrections, execution or large-owner credit were added.

North reviewed the full operation-contract test (300 lines), helpers and note,
full result-codec consumer and operation/variable owner sections, with retained
variable codec and checked kind-codec table, key guards and root binding. All
five tests' counts and limits matched, including the map state-codec mismatch
and the legacy shape's simultaneous missing/unknown fields. Three links,
whitespace and source/test guards passed; no corrections, execution or full
large-owner credit were added.

North reviewed the full result-variants test (504 lines), helpers and companion,
fresh evidence/result constructors and retained full result-codec/variable
context. The finite enum-product matrix was distinguished from valid read
successes; variable codec compatibility was not presented as request binding.
Strict-negative and root/union assertion limits matched source. Four links,
whitespace and source/test guards passed; no corrections, execution or full
large-owner credit were added.

North reviewed the full surface test (379 lines), nine tests/helpers and note,
surface constructor/codec and proxy fixture. Selected actual BlockSpec, product
instantiation, graph codec/validation/diff and PureImplementation were checked,
with retained product admission context. Count-before-item failures, fixed
compatibility fixtures, filtered diff/error-code assertions and limits on
reachability/authority claims matched. Six links, whitespace and source/test
guards passed; no corrections, execution or additional owner credit were added.

North reviewed the full node-control test (592 lines), fourteen tests/helpers
and note, fresh full unsigned comparator/result/audience and actual routes and
capability with retained state/request/grant/codec context. Five audience tests,
the 256/257-byte witness, nine mismatch cases, untested temporal endpoints and
the misnamed probe-grant substitution were accurately bounded. Three links,
whitespace and source/test guards passed; no corrections, execution or additional
owner credit were added.

North reviewed the transit fixture companion, retaining the full 34-line fixture,
full workload substitution test and transit codec/parser. Selected actual transit
helpers/canonical-vector test and governing wire document were refreshed. The
request preimage, nested/outer digest distinction, unsigned/signing boundary and
consumer/export limits matched. Five links, whitespace and source/test guards
passed; no corrections, execution or additional owner credit were added.

North reviewed the full transit owner (767 lines), full governing test/helpers
(898 lines), note/fixture and selected actual Operations intent/signing-reload
consumers. Exact bounds/key counts, local enum purpose versus verifier purpose,
fourteen-code order and temporal endpoints matched. Mapping/raw admission and
selected trust/resource/error limits were accurate; the retained gateway argument
was not presented as a whole-workflow defect. Seven links, whitespace and source
guards passed; no corrections, execution or additional test credit were added.

North reviewed the transit test companion against retained full source/helpers
(898 lines), full owner (767 lines) and fixture. The nine test groups, 21-key
missing/None matrix, fourteen precedence cases, twelve cross-family pairs per
representation and five verifier directions matched. Repr/error/AST guarantees
were appropriately finite. Four links, whitespace and source/test guards passed;
no corrections, execution or additional owner credit were added.

North reviewed the full surface-read fixture (213 lines) and note, selected actual
authority helpers/canonical assertions, result context/assertion helpers and full
canonical-result test, declaration owner and result context/name/coverage guards,
plus the governing wire section. Eight vectors, two contexts, identity preimages,
mapping/raw evidence and names/live-handler limits matched. Six local links,
whitespace and source/test guards passed. Other selected owner reads remain
author context; no whole-owner/test credit or execution was added.

North reviewed the full surface-read result owner (591 lines), note and retained
fixture, plus selected tests 175–290, 355–466 and 559–792. Kepler's full 792-line
test read supplies the remaining author context, not a whole-test North review.
Variant fields, derived claims, exact 7/8 keys, construction-time context bounds,
size-before-keys, trusted-kind routing, selective errors and structural coverage
matched. Five links, whitespace and source/test guards passed; no execution or
additional test credit was added.

North completed the full surface-read result test (792 lines), all eight tests
and helpers, with full owner/fixture context. Exact fields/union, socket and
same-size declaration binding, finite coverage/field matrices, maximum witnesses,
count-before-subset rejection, diagnostics and export/AST/milestone limits matched.
Four links, whitespace and source/test guards passed; no execution or additional
owner credit was added.

North reviewed the full surface-read owner (790 lines) and note, selected authority
tests 48–140, 180–237, 335–491 and 653–721, with full result-owner/fixture context.
Kepler's full 721-line authority-test read remains separate author evidence.
Distinct preimages, caller audience, locally admitted foreign purpose, exact keys
and bounds, grant API, thirteen-check order, expectation errors and disclosure
limits matched. Five links, whitespace and source/test guards passed; no execution
or additional test credit was added.

North completed the full surface-read authority test (721 lines), all nine tests
and helpers, with full owner/note/fixture context and refreshed authority-reference
vectors. Fixed versus synthetic evidence, finite precedence/cross-family cases,
str-only diagnostics, issuer-only material matrix, strict-field and constructor
cases and selected export/from-import guards matched. Five links, whitespace and
source/test guards passed; no execution or additional owner credit was added.

North reviewed the full milestone-closeout test (135 lines), all three tests and
helpers, full note and Core harness, with retained surface-result textual-assertion
context. Filesystem inventory, literal initializer names, relative-from handling,
finite import roots and pytest-only discovery limits matched. Three links,
whitespace and source/test/harness guards passed; no execution or extra coverage
credit was added.

North reviewed the full execution-coordinator test (180 lines), all six tests and
note, selected owner 1–212, 237–350, 455–705 and 781–902, semantic guards, sorted
factory and selected error wrapping. Kepler's full 917-line owner read remains
separate author context. Enum-set/union coverage, selected flags, source-only
approval/idempotency laws and the two sorted-command negatives matched. Three
links, whitespace and source/test guards passed; no execution or owner credit
was added.

North completed the full execution contract owner (917 lines), full 180-line
governing test and note. Constructor/factory distinctions, set coverage versus
multiplicity, selective error wrapping and implementation obligations matched.
The published note matched North's reviewed SHA-256. Four links, whitespace and
source/test guards passed; no execution or additional test credit was added.

North reviewed all 519 lines and thirteen tests of the lifecycle contract suite,
its companion and selected actual lifecycle constructor/table/factory/aggregate
paths. Literal, enum-relative and self-derived expectations, fourteen direct
and fifteen descriptor negatives, the single fixed-message assertion and the
metadata/execution distinction matched. The published note matched North's
reviewed SHA-256. Two links, whitespace and source/test guards passed. No whole
lifecycle-owner credit or executable evidence was added.

North completed the full 1,301-line lifecycle owner and companion, retained the
full 519-line test context and refreshed actual Operations records 1–83 and
1180–1256. Timing/boolean laws, one-way event permission, five claim tuples,
factory transitions and flags, kind/ID uniqueness distinctions, record consumers
and descriptor/error limits matched. The note matched North's reviewed SHA-256.
Five links, whitespace and frozen Core source/test plus selected records guards
passed. No source repair, executable validation or additional test credit.

North reviewed the full 476-line command-workflow suite, all four tests, the full
security assertion helper and note, with actual owner 154–434 and 814–850 checked
alongside Kepler's selected definition/mapping reads. The explicit 31-row,
eight-field ordering, benign helper checks without a bound assertion, selected
flags, three negatives with duplicate overlap and cause-retention limits matched.
The note matched North's reviewed SHA-256. Three links, whitespace and frozen
source/test guards passed; no whole-owner credit or executable evidence was added.

North completed the full 850-line commands owner, full 476-line suite/helper and
note, with actual shared policy enums and Operations workflows 1–82/records
448–485. Scoped symbol search confirmed only owner/facade references for the
factory, contract records and payload-policy enum in Core/Operations sources.
The 11-family/31-kind/14-policy catalogue, factory defaults versus constructor
freedoms, ID/kind integrity distinctions, mapping/error limits and actual enum
consumers matched. The note matched North's reviewed SHA-256. Seven links,
whitespace and frozen Core source/test plus selected Operations guards passed.
No source repair, executable evidence or additional consumer coverage was added.

North reviewed the full 515-line command-parity suite, all five tests, three
helpers and note, with actual parity 375–512/784–818, transaction 36–106 and the
HTTP execution definition checked. The ordered 37-row/six-field mapping, valid
weakened unit-of-work fixture, destructive AFTER_COMMIT rejection, permitted
singleton bindings and overlapping policy/identity negatives matched. The note
matched North's reviewed SHA-256. Four links, whitespace and frozen source/test
guards passed. No whole-owner credit, live transport or executable evidence.

North reviewed the full 310-line authorization/history parity suite, all five
tests/helpers and note, actual parity 198–281/514–708/738–783 and retained the
complete shared disclosure helper and command/transaction context. The literal
74 count versus selected policies, four typed changed-field candidates and
route-before-READ overlap matched. The note matched North's reviewed SHA-256.
Three links, whitespace and frozen Core source/test guards passed. No real
authorization, journal, redaction, bounds or executable evidence was claimed.

Existing Markdown is maintained as its own prose/agent contract and is excluded
from recursive mirroring. Authored fixtures, test policy JSON, package metadata,
exports and test harnesses remain included. No generated/vendor/lock files
occur in this tracked scope. New or removed files require an inventory update
during rollout.

The shared [request/intent relation](../../architecture/runtime-effect-request-intent-boundary.md)
and Core AGENTS maintenance change are additional documentation deliverables.
The known historical-inventory/test discrepancy is described in the
[boundary test companion](tests/test_runtime_effect_observation_boundary.py.md);
its resolution is separate from coverage status.

| Tracked source | Kind | Coverage | Note | Disposition |
| --- | --- | --- | --- | --- |
| [control-plane-kit-core/AGENTS.md](../../../control-plane-kit-core/AGENTS.md) | existing prose / agent instructions | excluded | — | Maintained at the linked source; no recursive prose companion. |
| [control-plane-kit-core/README.md](../../../control-plane-kit-core/README.md) | existing prose / agent instructions | excluded | — | Maintained at the linked source; no recursive prose companion. |
| [control-plane-kit-core/docs/EXTRACTION.md](../../../control-plane-kit-core/docs/EXTRACTION.md) | existing prose / agent instructions | excluded | — | Maintained at the linked source; no recursive prose companion. |
| [control-plane-kit-core/docs/EXTRACT_D_TOPOLOGY.md](../../../control-plane-kit-core/docs/EXTRACT_D_TOPOLOGY.md) | existing prose / agent instructions | excluded | — | Maintained at the linked source; no recursive prose companion. |
| [control-plane-kit-core/docs/NODE_CONTROL_CANONICAL_WIRE.md](../../../control-plane-kit-core/docs/NODE_CONTROL_CANONICAL_WIRE.md) | existing prose / agent instructions | excluded | — | Maintained at the linked source; no recursive prose companion. |
| [control-plane-kit-core/docs/NODE_CONTROL_PUBLIC_MATERIAL.md](../../../control-plane-kit-core/docs/NODE_CONTROL_PUBLIC_MATERIAL.md) | existing prose / agent instructions | excluded | — | Maintained at the linked source; no recursive prose companion. |
| [control-plane-kit-core/docs/NODE_CONTROL_TOPOLOGY.md](../../../control-plane-kit-core/docs/NODE_CONTROL_TOPOLOGY.md) | existing prose / agent instructions | excluded | — | Maintained at the linked source; no recursive prose companion. |
| [control-plane-kit-core/examples/external-product-descriptor.md](../../../control-plane-kit-core/examples/external-product-descriptor.md) | existing prose / agent instructions | excluded | — | Maintained at the linked source; no recursive prose companion. |
| [control-plane-kit-core/pyproject.toml](../../../control-plane-kit-core/pyproject.toml) | build / dependencies | reviewed | [companion](pyproject.toml.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/__init__.py](../../../control-plane-kit-core/src/control_plane_kit_core/__init__.py) | source | reviewed | [companion](src/control_plane_kit_core/__init__.py.md) | North: both facades and boundary test fully read; selected consumer imports. |
| [control-plane-kit-core/src/control_plane_kit_core/_activity_identity.py](../../../control-plane-kit-core/src/control_plane_kit_core/_activity_identity.py) | source | reviewed | [companion](src/control_plane_kit_core/_activity_identity.py.md) | North: seven owners read; governing tests sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/_node_control_public_wire.py](../../../control-plane-kit-core/src/control_plane_kit_core/_node_control_public_wire.py) | source | reviewed | [companion](src/control_plane_kit_core/_node_control_public_wire.py.md) | North PASS: full owner/test/notes/fixtures and selected actual consumer guards. No corrections or execution. |
| [control-plane-kit-core/src/control_plane_kit_core/_run_identity.py](../../../control-plane-kit-core/src/control_plane_kit_core/_run_identity.py) | source | reviewed | [companion](src/control_plane_kit_core/_run_identity.py.md) | North: seven owners read; governing tests sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/algebra.py](../../../control-plane-kit-core/src/control_plane_kit_core/algebra.py) | source | reviewed | [companion](src/control_plane_kit_core/algebra.py.md) | North: seven owners read; governing tests sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/approval_subjects.py](../../../control-plane-kit-core/src/control_plane_kit_core/approval_subjects.py) | source | reviewed | [companion](src/control_plane_kit_core/approval_subjects.py.md) | North: owners read fully; planning tests sampled, policy tests read fully. |
| [control-plane-kit-core/src/control_plane_kit_core/capabilities.py](../../../control-plane-kit-core/src/control_plane_kit_core/capabilities.py) | source | reviewed | [companion](src/control_plane_kit_core/capabilities.py.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/configuration.py](../../../control-plane-kit-core/src/control_plane_kit_core/configuration.py) | source | reviewed | [companion](src/control_plane_kit_core/configuration.py.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/configuration_rendering.py](../../../control-plane-kit-core/src/control_plane_kit_core/configuration_rendering.py) | source | reviewed | [companion](src/control_plane_kit_core/configuration_rendering.py.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/control_contracts.py](../../../control-plane-kit-core/src/control_plane_kit_core/control_contracts.py) | source | reviewed | [companion](src/control_plane_kit_core/control_contracts.py.md) | North: full owners, four notes and route test; contract tests sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/control_routes.py](../../../control-plane-kit-core/src/control_plane_kit_core/control_routes.py) | source | reviewed | [companion](src/control_plane_kit_core/control_routes.py.md) | North: full owners, four notes and route test; contract tests sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/delegation_authority.py](../../../control-plane-kit-core/src/control_plane_kit_core/delegation_authority.py) | source | reviewed | [companion](src/control_plane_kit_core/delegation_authority.py.md) | North: full delegation owner/test/notes; actual environment/diff/graph checks. |
| [control-plane-kit-core/src/control_plane_kit_core/delegation_keys.py](../../../control-plane-kit-core/src/control_plane_kit_core/delegation_keys.py) | source | reviewed | [companion](src/control_plane_kit_core/delegation_keys.py.md) | North: full public-key owner, test and both notes read. |
| [control-plane-kit-core/src/control_plane_kit_core/environment.py](../../../control-plane-kit-core/src/control_plane_kit_core/environment.py) | source | reviewed | [companion](src/control_plane_kit_core/environment.py.md) | North: source claims checked; test navigation sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/gateway_delegation.py](../../../control-plane-kit-core/src/control_plane_kit_core/gateway_delegation.py) | source | reviewed | [companion](src/control_plane_kit_core/gateway_delegation.py.md) | North: full gateway owner/test/notes; selected target admission and grant construction. |
| [control-plane-kit-core/src/control_plane_kit_core/identity.py](../../../control-plane-kit-core/src/control_plane_kit_core/identity.py) | source | reviewed | [companion](src/control_plane_kit_core/identity.py.md) | North: seven owners read; governing tests sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/lifecycle.py](../../../control-plane-kit-core/src/control_plane_kit_core/lifecycle.py) | source | reviewed | [companion](src/control_plane_kit_core/lifecycle.py.md) | North: seven owners read; governing tests sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/node_control.py](../../../control-plane-kit-core/src/control_plane_kit_core/node_control.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/node_control_surface_read_results.py](../../../control-plane-kit-core/src/control_plane_kit_core/node_control_surface_read_results.py) | source | reviewed | [companion](src/control_plane_kit_core/node_control_surface_read_results.py.md) | North PASS: full owner/note/fixture, selected actual tests; Kepler full governing-test author context. No execution. |
| [control-plane-kit-core/src/control_plane_kit_core/node_control_surface_reads.py](../../../control-plane-kit-core/src/control_plane_kit_core/node_control_surface_reads.py) | source | reviewed | [companion](src/control_plane_kit_core/node_control_surface_reads.py.md) | North PASS: full owner/note, selected authority tests, full result-owner/fixtures. Kepler full authority-test context. No execution. |
| [control-plane-kit-core/src/control_plane_kit_core/node_control_transit.py](../../../control-plane-kit-core/src/control_plane_kit_core/node_control_transit.py) | source | reviewed | [companion](src/control_plane_kit_core/node_control_transit.py.md) | North PASS: full owner/test/helpers/note/fixture and selected actual Operations consumers. No corrections or execution. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/__init__.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/__init__.py) | source | reviewed | [companion](src/control_plane_kit_core/operations/__init__.py.md) | North: both facades and boundary test fully read; selected consumer imports. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/commands.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/commands.py) | source | reviewed | [companion](src/control_plane_kit_core/operations/commands.py.md) | North PASS: full 850-line owner, 476-line suite/helper/note, actual selected policy/Operations consumers and scoped symbol search. No execution or consumer coverage. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/compensation.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/compensation.py) | source | reviewed | [companion](src/control_plane_kit_core/operations/compensation.py.md) | North PASS: full owner/test/notes; selected identity, codec, planned-activity validation, facade and Operations admission contracts. No execution. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/execution.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/execution.py) | source | reviewed | [companion](src/control_plane_kit_core/operations/execution.py.md) | North PASS: full owner/test/note, selected actual shared context. Reviewed note hash verified. No execution. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/handoff.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/handoff.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/http.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/http.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/lifecycle.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/lifecycle.py) | source | reviewed | [companion](src/control_plane_kit_core/operations/lifecycle.py.md) | North PASS: full 1301-line owner/note, retained full 519-line test and selected actual Operations records. No source repair or execution. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/mcp.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/mcp.py) | source | reviewed | [companion](src/control_plane_kit_core/operations/mcp.py.md) | North: full owner/test and notes; selected Protocol contract, prior process context. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/parity.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/parity.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/persistence.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/persistence.py) | source | reviewed | [companion](src/control_plane_kit_core/operations/persistence.py.md) | North: full persistence owner/test/notes; actual enforcement enum and selected store bundle. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/process.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/process.py) | source | reviewed | [companion](src/control_plane_kit_core/operations/process.py.md) | North: full process owner/test/notes/security helper; selected imported contracts. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/projections.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/projections.py) | source | reviewed | [companion](src/control_plane_kit_core/operations/projections.py.md) | North: full owner/test/notes, prior security helper; selected HTTP/parity. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/recovery.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/recovery.py) | source | reviewed | [companion](src/control_plane_kit_core/operations/recovery.py.md) | North: full owner/test/identity helpers; selected Operations fold/start boundaries. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/run_identity.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/run_identity.py) | source | reviewed | [companion](src/control_plane_kit_core/operations/run_identity.py.md) | North: seven owners read; governing tests sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/services.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/services.py) | source | reviewed | [companion](src/control_plane_kit_core/operations/services.py.md) | North: complete service owner across two reads, full test and both notes. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/transactions.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/transactions.py) | source | reviewed | [companion](src/control_plane_kit_core/operations/transactions.py.md) | North: full transaction owner/test/notes and Postgres UOW; selected service contracts. |
| [control-plane-kit-core/src/control_plane_kit_core/planning/__init__.py](../../../control-plane-kit-core/src/control_plane_kit_core/planning/__init__.py) | source | reviewed | [companion](src/control_plane_kit_core/planning/__init__.py.md) | North: owners read fully; planning tests sampled, policy tests read fully. |
| [control-plane-kit-core/src/control_plane_kit_core/planning/activity_plan.py](../../../control-plane-kit-core/src/control_plane_kit_core/planning/activity_plan.py) | source | reviewed | [companion](src/control_plane_kit_core/planning/activity_plan.py.md) | North: owners read fully; planning tests sampled, policy tests read fully. |
| [control-plane-kit-core/src/control_plane_kit_core/planning/codec.py](../../../control-plane-kit-core/src/control_plane_kit_core/planning/codec.py) | source | reviewed | [companion](src/control_plane_kit_core/planning/codec.py.md) | North: owners read fully; planning tests sampled, policy tests read fully. |
| [control-plane-kit-core/src/control_plane_kit_core/planning/compiler.py](../../../control-plane-kit-core/src/control_plane_kit_core/planning/compiler.py) | source | reviewed | [companion](src/control_plane_kit_core/planning/compiler.py.md) | North: owners read fully; planning tests sampled, policy tests read fully. |
| [control-plane-kit-core/src/control_plane_kit_core/planning/recovery.py](../../../control-plane-kit-core/src/control_plane_kit_core/planning/recovery.py) | source | reviewed | [companion](src/control_plane_kit_core/planning/recovery.py.md) | North: full owner, test and both recovery notes read. |
| [control-plane-kit-core/src/control_plane_kit_core/planning/saga.py](../../../control-plane-kit-core/src/control_plane_kit_core/planning/saga.py) | source | reviewed | [companion](src/control_plane_kit_core/planning/saga.py.md) | North: consequential saga/scenario owners checked; tests sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/planning/scenarios.py](../../../control-plane-kit-core/src/control_plane_kit_core/planning/scenarios.py) | source | reviewed | [companion](src/control_plane_kit_core/planning/scenarios.py.md) | North: consequential saga/scenario owners checked; tests sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/policies.py](../../../control-plane-kit-core/src/control_plane_kit_core/policies.py) | source | reviewed | [companion](src/control_plane_kit_core/policies.py.md) | North: owners read fully; planning tests sampled, policy tests read fully. |
| [control-plane-kit-core/src/control_plane_kit_core/probe_intents.py](../../../control-plane-kit-core/src/control_plane_kit_core/probe_intents.py) | source | reviewed | [companion](src/control_plane_kit_core/probe_intents.py.md) | North: full probe owner and notes read; selected tests checked. |
| [control-plane-kit-core/src/control_plane_kit_core/products.py](../../../control-plane-kit-core/src/control_plane_kit_core/products.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/public_ingress.py](../../../control-plane-kit-core/src/control_plane_kit_core/public_ingress.py) | source | reviewed | [companion](src/control_plane_kit_core/public_ingress.py.md) | North: full ingress owner/test/notes; exact graph references and origin helper. |
| [control-plane-kit-core/src/control_plane_kit_core/runtime_authority.py](../../../control-plane-kit-core/src/control_plane_kit_core/runtime_authority.py) | source | reviewed | [companion](src/control_plane_kit_core/runtime_authority.py.md) | North: full authority owner and recipient validator; selected observation/tests/fixture. |
| [control-plane-kit-core/src/control_plane_kit_core/runtime_effect_observation.py](../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effect_observation.py) | source | reviewed | [companion](src/control_plane_kit_core/runtime_effect_observation.py.md) | North: source claims checked; test navigation sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/runtime_effects.py](../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effects.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/secrets.py](../../../control-plane-kit-core/src/control_plane_kit_core/secrets.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/topology/__init__.py](../../../control-plane-kit-core/src/control_plane_kit_core/topology/__init__.py) | source | reviewed | [companion](src/control_plane_kit_core/topology/__init__.py.md) | North: consequential graph contracts checked; test bodies sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/topology/changes.py](../../../control-plane-kit-core/src/control_plane_kit_core/topology/changes.py) | source | reviewed | [companion](src/control_plane_kit_core/topology/changes.py.md) | North: consequential graph contracts checked; test bodies sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/topology/codec.py](../../../control-plane-kit-core/src/control_plane_kit_core/topology/codec.py) | source | reviewed | [companion](src/control_plane_kit_core/topology/codec.py.md) | North: consequential graph contracts checked; test bodies sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/topology/compiler.py](../../../control-plane-kit-core/src/control_plane_kit_core/topology/compiler.py) | source | reviewed | [companion](src/control_plane_kit_core/topology/compiler.py.md) | North: consequential graph contracts checked; test bodies sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/topology/diff.py](../../../control-plane-kit-core/src/control_plane_kit_core/topology/diff.py) | source | reviewed | [companion](src/control_plane_kit_core/topology/diff.py.md) | North: consequential graph contracts checked; test bodies sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/topology/graph.py](../../../control-plane-kit-core/src/control_plane_kit_core/topology/graph.py) | source | reviewed | [companion](src/control_plane_kit_core/topology/graph.py.md) | North: consequential graph contracts checked; test bodies sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/topology/validation.py](../../../control-plane-kit-core/src/control_plane_kit_core/topology/validation.py) | source | reviewed | [companion](src/control_plane_kit_core/topology/validation.py.md) | North: consequential graph contracts checked; test bodies sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/types.py](../../../control-plane-kit-core/src/control_plane_kit_core/types.py) | source | reviewed | [companion](src/control_plane_kit_core/types.py.md) | North: seven owners read; governing tests sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/verification.py](../../../control-plane-kit-core/src/control_plane_kit_core/verification.py) | source | reviewed | [companion](src/control_plane_kit_core/verification.py.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/test.sh](../../../control-plane-kit-core/test.sh) | suite harness | reviewed | [companion](test.sh.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/tests/approved_skips.json](../../../control-plane-kit-core/tests/approved_skips.json) | test policy data | reviewed | [companion](tests/approved_skips.json.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/tests/contract_security_assertions.py](../../../control-plane-kit-core/tests/contract_security_assertions.py) | test / assertion support | reviewed | [companion](tests/contract_security_assertions.py.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/tests/fixtures/external-products/proxy/product.cpk.json](../../../control-plane-kit-core/tests/fixtures/external-products/proxy/product.cpk.json) | authored fixture | reviewed | [companion](tests/fixtures/external-products/proxy/product.cpk.json.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/tests/fixtures/node_authority_empty_graph.json](../../../control-plane-kit-core/tests/fixtures/node_authority_empty_graph.json) | authored fixture | reviewed | [companion](tests/fixtures/node_authority_empty_graph.json.md) | North: full authority owner and recipient validator; selected observation/tests/fixture. |
| [control-plane-kit-core/tests/fixtures/node_control_canonical_wire_v1.json](../../../control-plane-kit-core/tests/fixtures/node_control_canonical_wire_v1.json) | authored fixture | reviewed | [companion](tests/fixtures/node_control_canonical_wire_v1.json.md) | North PASS: full fixture/test/notes, actual selected canonical/raw/numeric paths and fixture consumers. No corrections or execution. |
| [control-plane-kit-core/tests/fixtures/node_control_public_material_v1.json](../../../control-plane-kit-core/tests/fixtures/node_control_public_material_v1.json) | authored fixture | reviewed | [companion](tests/fixtures/node_control_public_material_v1.json.md) | North PASS: full fixture/test/notes, retained shared owner and selected actual consumers. No corrections or execution. |
| [control-plane-kit-core/tests/fixtures/node_control_surface_read_canonical_wire_v1.json](../../../control-plane-kit-core/tests/fixtures/node_control_surface_read_canonical_wire_v1.json) | authored fixture | reviewed | [companion](tests/fixtures/node_control_surface_read_canonical_wire_v1.json.md) | North PASS: full fixture/note, selected actual consumers and declaration/result context. No whole-owner/test credit or execution. |
| [control-plane-kit-core/tests/fixtures/node_control_transit_canonical_wire_v1.json](../../../control-plane-kit-core/tests/fixtures/node_control_transit_canonical_wire_v1.json) | authored fixture | reviewed | [companion](tests/fixtures/node_control_transit_canonical_wire_v1.json.md) | North PASS: full fixture/note, selected actual consumer tests/helpers and descriptor/codec context. No corrections or execution. |
| [control-plane-kit-core/tests/test_activity_identity.py](../../../control-plane-kit-core/tests/test_activity_identity.py) | test / assertion support | reviewed | [companion](tests/test_activity_identity.py.md) | North: seven owners read; governing tests sampled. |
| [control-plane-kit-core/tests/test_activity_plan.py](../../../control-plane-kit-core/tests/test_activity_plan.py) | test / assertion support | reviewed | [companion](tests/test_activity_plan.py.md) | North: owners read fully; planning tests sampled, policy tests read fully. |
| [control-plane-kit-core/tests/test_activity_plan_codec.py](../../../control-plane-kit-core/tests/test_activity_plan_codec.py) | test / assertion support | reviewed | [companion](tests/test_activity_plan_codec.py.md) | North: owners read fully; planning tests sampled, policy tests read fully. |
| [control-plane-kit-core/tests/test_activity_plan_compiler.py](../../../control-plane-kit-core/tests/test_activity_plan_compiler.py) | test / assertion support | reviewed | [companion](tests/test_activity_plan_compiler.py.md) | North: owners read fully; planning tests sampled, policy tests read fully. |
| [control-plane-kit-core/tests/test_adapter_parity_contract.py](../../../control-plane-kit-core/tests/test_adapter_parity_contract.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_approval_subjects.py](../../../control-plane-kit-core/tests/test_approval_subjects.py) | test / assertion support | reviewed | [companion](tests/test_approval_subjects.py.md) | North: owners read fully; planning tests sampled, policy tests read fully. |
| [control-plane-kit-core/tests/test_authorization_history_parity_contract.py](../../../control-plane-kit-core/tests/test_authorization_history_parity_contract.py) | test / assertion support | reviewed | [companion](tests/test_authorization_history_parity_contract.py.md) | North PASS: full 310-line suite/helpers/note and actual selected parity security paths; retained shared helper/context. No owner/other-test credit or execution. |
| [control-plane-kit-core/tests/test_command_parity_contract.py](../../../control-plane-kit-core/tests/test_command_parity_contract.py) | test / assertion support | reviewed | [companion](tests/test_command_parity_contract.py.md) | North PASS: full 515-line suite/helpers/note and selected actual parity/transaction/HTTP guards. No whole-owner credit or execution. |
| [control-plane-kit-core/tests/test_command_workflow_contract.py](../../../control-plane-kit-core/tests/test_command_workflow_contract.py) | test / assertion support | reviewed | [companion](tests/test_command_workflow_contract.py.md) | North PASS: full 476-line suite/helper/note and selected actual owner guards/factory; Kepler selected definitions/mapping. No owner credit or execution. |
| [control-plane-kit-core/tests/test_compensation_planning.py](../../../control-plane-kit-core/tests/test_compensation_planning.py) | test / assertion support | reviewed | [companion](tests/test_compensation_planning.py.md) | North: consequential saga/scenario owners checked; tests sampled. |
| [control-plane-kit-core/tests/test_configuration_artifacts.py](../../../control-plane-kit-core/tests/test_configuration_artifacts.py) | test / assertion support | reviewed | [companion](tests/test_configuration_artifacts.py.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/tests/test_container_server_product.py](../../../control-plane-kit-core/tests/test_container_server_product.py) | test / assertion support | reviewed | [companion](tests/test_container_server_product.py.md) | North PASS: full test/helper/note and runtime constructor/descriptor; retained product/codec contracts. No execution. |
| [control-plane-kit-core/tests/test_control_contracts.py](../../../control-plane-kit-core/tests/test_control_contracts.py) | test / assertion support | reviewed | [companion](tests/test_control_contracts.py.md) | North: full owners, four notes and route test; contract tests sampled. |
| [control-plane-kit-core/tests/test_control_routes.py](../../../control-plane-kit-core/tests/test_control_routes.py) | test / assertion support | reviewed | [companion](tests/test_control_routes.py.md) | North: full owners, four notes and route test; contract tests sampled. |
| [control-plane-kit-core/tests/test_cpk_server_entrypoint_handoff.py](../../../control-plane-kit-core/tests/test_cpk_server_entrypoint_handoff.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_delegation_authority_projection.py](../../../control-plane-kit-core/tests/test_delegation_authority_projection.py) | test / assertion support | reviewed | [companion](tests/test_delegation_authority_projection.py.md) | North: full delegation owner/test/notes; actual environment/diff/graph checks. |
| [control-plane-kit-core/tests/test_delegation_keys.py](../../../control-plane-kit-core/tests/test_delegation_keys.py) | test / assertion support | reviewed | [companion](tests/test_delegation_keys.py.md) | North: full public-key owner, test and both notes read. |
| [control-plane-kit-core/tests/test_deployment_program_boundary.py](../../../control-plane-kit-core/tests/test_deployment_program_boundary.py) | test / assertion support | reviewed | [companion](tests/test_deployment_program_boundary.py.md) | North: complete service owner across two reads, full test and both notes. |
| [control-plane-kit-core/tests/test_draft_catalogue_contract.py](../../../control-plane-kit-core/tests/test_draft_catalogue_contract.py) | test / assertion support | reviewed | [companion](tests/test_draft_catalogue_contract.py.md) | North: full tests/notes/UoW helpers; selected exact HTTP/parity/projection entries. |
| [control-plane-kit-core/tests/test_draft_selection_contract.py](../../../control-plane-kit-core/tests/test_draft_selection_contract.py) | test / assertion support | reviewed | [companion](tests/test_draft_selection_contract.py.md) | North: full tests/notes/UoW helpers; selected exact HTTP/parity/projection entries. |
| [control-plane-kit-core/tests/test_effect_recovery_contract.py](../../../control-plane-kit-core/tests/test_effect_recovery_contract.py) | test / assertion support | reviewed | [companion](tests/test_effect_recovery_contract.py.md) | North: full owner/test/identity helpers; selected Operations fold/start boundaries. |
| [control-plane-kit-core/tests/test_environment_secrets.py](../../../control-plane-kit-core/tests/test_environment_secrets.py) | test / assertion support | reviewed | [companion](tests/test_environment_secrets.py.md) | North: source claims checked; test navigation sampled. |
| [control-plane-kit-core/tests/test_execution_coordinator_contract.py](../../../control-plane-kit-core/tests/test_execution_coordinator_contract.py) | test / assertion support | reviewed | [companion](tests/test_execution_coordinator_contract.py.md) | North PASS: full six-test source/note and selected owner guards/factory; Kepler full owner context. No execution. |
| [control-plane-kit-core/tests/test_execution_lifecycle_contract.py](../../../control-plane-kit-core/tests/test_execution_lifecycle_contract.py) | test / assertion support | reviewed | [companion](tests/test_execution_lifecycle_contract.py.md) | North PASS: full 519-line test/note and selected actual owner constructor/table/factory/aggregate paths. No execution or whole-owner credit. |
| [control-plane-kit-core/tests/test_external_product_fixture.py](../../../control-plane-kit-core/tests/test_external_product_fixture.py) | test / assertion support | reviewed | [companion](tests/test_external_product_fixture.py.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/tests/test_extract_d_closeout.py](../../../control-plane-kit-core/tests/test_extract_d_closeout.py) | test / assertion support | reviewed | [companion](tests/test_extract_d_closeout.py.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/tests/test_extract_d_topology.py](../../../control-plane-kit-core/tests/test_extract_d_topology.py) | test / assertion support | reviewed | [companion](tests/test_extract_d_topology.py.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/tests/test_failed_run_compensation_contract.py](../../../control-plane-kit-core/tests/test_failed_run_compensation_contract.py) | test / assertion support | reviewed | [companion](tests/test_failed_run_compensation_contract.py.md) | North PASS: full test/helpers, owner and notes; selected actual exports and consequential dependencies. No execution. |
| [control-plane-kit-core/tests/test_gateway_delegation.py](../../../control-plane-kit-core/tests/test_gateway_delegation.py) | test / assertion support | reviewed | [companion](tests/test_gateway_delegation.py.md) | North: full gateway owner/test/notes; selected target admission and grant construction. |
| [control-plane-kit-core/tests/test_graph_codec.py](../../../control-plane-kit-core/tests/test_graph_codec.py) | test / assertion support | reviewed | [companion](tests/test_graph_codec.py.md) | North: consequential graph contracts checked; test bodies sampled. |
| [control-plane-kit-core/tests/test_graph_diff.py](../../../control-plane-kit-core/tests/test_graph_diff.py) | test / assertion support | reviewed | [companion](tests/test_graph_diff.py.md) | North: consequential graph contracts checked; test bodies sampled. |
| [control-plane-kit-core/tests/test_graph_validation.py](../../../control-plane-kit-core/tests/test_graph_validation.py) | test / assertion support | reviewed | [companion](tests/test_graph_validation.py.md) | North: consequential graph contracts checked; test bodies sampled. |
| [control-plane-kit-core/tests/test_http_api_contract.py](../../../control-plane-kit-core/tests/test_http_api_contract.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_identity.py](../../../control-plane-kit-core/tests/test_identity.py) | test / assertion support | reviewed | [companion](tests/test_identity.py.md) | North: seven owners read; governing tests sampled. |
| [control-plane-kit-core/tests/test_kernel_pipeline.py](../../../control-plane-kit-core/tests/test_kernel_pipeline.py) | test / assertion support | reviewed | [companion](tests/test_kernel_pipeline.py.md) | North: consequential graph contracts checked; test bodies sampled. |
| [control-plane-kit-core/tests/test_mcp_streamable_http_contract.py](../../../control-plane-kit-core/tests/test_mcp_streamable_http_contract.py) | test / assertion support | reviewed | [companion](tests/test_mcp_streamable_http_contract.py.md) | North: full owner/test and notes; selected Protocol contract, prior process context. |
| [control-plane-kit-core/tests/test_milestone_closeout.py](../../../control-plane-kit-core/tests/test_milestone_closeout.py) | test / assertion support | reviewed | [companion](tests/test_milestone_closeout.py.md) | North PASS: full three-test file/helpers/note/harness; actual surface-result assertion retained. No execution. |
| [control-plane-kit-core/tests/test_node_authority_delivery.py](../../../control-plane-kit-core/tests/test_node_authority_delivery.py) | test / assertion support | reviewed | [companion](tests/test_node_authority_delivery.py.md) | North: full authority owner and recipient validator; selected observation/tests/fixture. |
| [control-plane-kit-core/tests/test_node_control.py](../../../control-plane-kit-core/tests/test_node_control.py) | test / assertion support | reviewed | [companion](tests/test_node_control.py.md) | North PASS: full fourteen-test suite/helpers/note, audience/grant comparator and selected declaration/constructor context. No corrections or execution. |
| [control-plane-kit-core/tests/test_node_control_canonical_wire.py](../../../control-plane-kit-core/tests/test_node_control_canonical_wire.py) | test / assertion support | reviewed | [companion](tests/test_node_control_canonical_wire.py.md) | North PASS: full test/fixture/notes and selected owners/dependency declaration. Fixed/computed and numeric evidence limits checked; no execution. |
| [control-plane-kit-core/tests/test_node_control_graph_references.py](../../../control-plane-kit-core/tests/test_node_control_graph_references.py) | test / assertion support | reviewed | [companion](tests/test_node_control_graph_references.py.md) | North PASS: full test/note, actual selected nominal/codec/wrapper/export paths and fixture context. No corrections or execution. |
| [control-plane-kit-core/tests/test_node_control_operation_contracts.py](../../../control-plane-kit-core/tests/test_node_control_operation_contracts.py) | test / assertion support | reviewed | [companion](tests/test_node_control_operation_contracts.py.md) | North PASS: full test/note, actual operation/variable owner and result consumer; retained descriptor codec and selected guards. No corrections or execution. |
| [control-plane-kit-core/tests/test_node_control_public_material.py](../../../control-plane-kit-core/tests/test_node_control_public_material.py) | test / assertion support | reviewed | [companion](tests/test_node_control_public_material.py.md) | North PASS: full test/fixture/notes and selected construction, codec, representation and validation paths. No corrections or execution. |
| [control-plane-kit-core/tests/test_node_control_public_wire_ownership.py](../../../control-plane-kit-core/tests/test_node_control_public_wire_ownership.py) | test / assertion support | reviewed | [companion](tests/test_node_control_public_wire_ownership.py.md) | North PASS: full test/shared owner/notes/fixtures and selected consumer composition. No corrections or execution. |
| [control-plane-kit-core/tests/test_node_control_result_variants.py](../../../control-plane-kit-core/tests/test_node_control_result_variants.py) | test / assertion support | reviewed | [companion](tests/test_node_control_result_variants.py.md) | North PASS: full test/helpers/note, actual result constructors and retained codec/variable context. Matrix and binding limits verified; no corrections or execution. |
| [control-plane-kit-core/tests/test_node_control_surface_read_authority.py](../../../control-plane-kit-core/tests/test_node_control_surface_read_authority.py) | test / assertion support | reviewed | [companion](tests/test_node_control_surface_read_authority.py.md) | North PASS: full nine-test suite/helpers/note/owner/fixtures; authority-reference vectors refreshed. No execution. |
| [control-plane-kit-core/tests/test_node_control_surface_read_results.py](../../../control-plane-kit-core/tests/test_node_control_surface_read_results.py) | test / assertion support | reviewed | [companion](tests/test_node_control_surface_read_results.py.md) | North PASS: full eight-test suite/helpers/note/owner/fixture. No execution or additional owner credit. |
| [control-plane-kit-core/tests/test_node_control_surfaces.py](../../../control-plane-kit-core/tests/test_node_control_surfaces.py) | test / assertion support | reviewed | [companion](tests/test_node_control_surfaces.py.md) | North PASS: full nine-test suite/helpers/note, surface constructor/codec and fixture; selected actual product/graph consumers. No corrections or execution. |
| [control-plane-kit-core/tests/test_node_control_topology.py](../../../control-plane-kit-core/tests/test_node_control_topology.py) | test / assertion support | reviewed | [companion](tests/test_node_control_topology.py.md) | North PASS: full documentation guard/note and governing topology document; historical/current limits checked. No corrections or execution. |
| [control-plane-kit-core/tests/test_node_control_transit.py](../../../control-plane-kit-core/tests/test_node_control_transit.py) | test / assertion support | reviewed | [companion](tests/test_node_control_transit.py.md) | North PASS: full nine-test suite/helpers/owner/fixture retained, full note checked. No corrections or execution. |
| [control-plane-kit-core/tests/test_node_control_workload_wire.py](../../../control-plane-kit-core/tests/test_node_control_workload_wire.py) | test / assertion support | reviewed | [companion](tests/test_node_control_workload_wire.py.md) | North PASS: full test/helpers/note and fixture context, selected actual raw/codec/constructor paths. No corrections or execution. |
| [control-plane-kit-core/tests/test_observation_connection_admission.py](../../../control-plane-kit-core/tests/test_observation_connection_admission.py) | test / assertion support | reviewed | [companion](tests/test_observation_connection_admission.py.md) | North: full authority owner and recipient validator; selected observation/tests/fixture. |
| [control-plane-kit-core/tests/test_oci_image_reference.py](../../../control-plane-kit-core/tests/test_oci_image_reference.py) | test / assertion support | reviewed | [companion](tests/test_oci_image_reference.py.md) | North PASS: full test/note, selected OCI/platform/codec and validators; import-root count corrected. No execution. |
| [control-plane-kit-core/tests/test_package_boundary.py](../../../control-plane-kit-core/tests/test_package_boundary.py) | test / assertion support | reviewed | [companion](tests/test_package_boundary.py.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/tests/test_persistence_boundary_contract.py](../../../control-plane-kit-core/tests/test_persistence_boundary_contract.py) | test / assertion support | reviewed | [companion](tests/test_persistence_boundary_contract.py.md) | North: full persistence owner/test/notes; actual enforcement enum and selected store bundle. |
| [control-plane-kit-core/tests/test_planning_scenarios.py](../../../control-plane-kit-core/tests/test_planning_scenarios.py) | test / assertion support | reviewed | [companion](tests/test_planning_scenarios.py.md) | North: consequential saga/scenario owners checked; tests sampled. |
| [control-plane-kit-core/tests/test_policies.py](../../../control-plane-kit-core/tests/test_policies.py) | test / assertion support | reviewed | [companion](tests/test_policies.py.md) | North: owners read fully; planning tests sampled, policy tests read fully. |
| [control-plane-kit-core/tests/test_probe_intents.py](../../../control-plane-kit-core/tests/test_probe_intents.py) | test / assertion support | reviewed | [companion](tests/test_probe_intents.py.md) | North: full probe owner and notes read; selected tests checked. |
| [control-plane-kit-core/tests/test_process_operational_contract.py](../../../control-plane-kit-core/tests/test_process_operational_contract.py) | test / assertion support | reviewed | [companion](tests/test_process_operational_contract.py.md) | North: full process owner/test/notes/security helper; selected imported contracts. |
| [control-plane-kit-core/tests/test_product_catalog.py](../../../control-plane-kit-core/tests/test_product_catalog.py) | test / assertion support | reviewed | [companion](tests/test_product_catalog.py.md) | North PASS: full test/helper/note and catalogue class; retained selected identity/document contracts. No execution. |
| [control-plane-kit-core/tests/test_product_descriptor.py](../../../control-plane-kit-core/tests/test_product_descriptor.py) | test / assertion support | reviewed | [companion](tests/test_product_descriptor.py.md) | North PASS: full test/helpers/note and bounded snapshot; retained actual document codec. No execution. |
| [control-plane-kit-core/tests/test_product_descriptor_hardening.py](../../../control-plane-kit-core/tests/test_product_descriptor_hardening.py) | test / assertion support | reviewed | [companion](tests/test_product_descriptor_hardening.py.md) | North PASS: full test/helper/note, selected nested rejection contracts and retained catalogue. No execution. |
| [control-plane-kit-core/tests/test_product_identity.py](../../../control-plane-kit-core/tests/test_product_identity.py) | test / assertion support | reviewed | [companion](tests/test_product_identity.py.md) | North PASS: full test/note; retained selected identity/codec/uniqueness and validators. No execution. |
| [control-plane-kit-core/tests/test_product_instantiation.py](../../../control-plane-kit-core/tests/test_product_instantiation.py) | test / assertion support | reviewed | [companion](tests/test_product_instantiation.py.md) | North PASS: full test/helpers/note, actual instantiation/configuration and selected graph contracts. No execution. |
| [control-plane-kit-core/tests/test_product_pipeline_propagation.py](../../../control-plane-kit-core/tests/test_product_pipeline_propagation.py) | test / assertion support | reviewed | [companion](tests/test_product_pipeline_propagation.py.md) | North PASS: full test/helpers/note, selected actual materialization/graph/diff/planning paths. No execution. |
| [control-plane-kit-core/tests/test_product_reference.py](../../../control-plane-kit-core/tests/test_product_reference.py) | test / assertion support | reviewed | [companion](tests/test_product_reference.py.md) | North PASS: full test/helper/note, selected reference/identity/document/catalogue boundaries. No execution. |
| [control-plane-kit-core/tests/test_product_runtime_contract.py](../../../control-plane-kit-core/tests/test_product_runtime_contract.py) | test / assertion support | reviewed | [companion](tests/test_product_runtime_contract.py.md) | North PASS: full test/hostile fixtures/note; actual runtime contract and selected validators. Static-import alias wording corrected; no execution. |
| [control-plane-kit-core/tests/test_protocol.py](../../../control-plane-kit-core/tests/test_protocol.py) | test / assertion support | reviewed | [companion](tests/test_protocol.py.md) | North: seven owners read; governing tests sampled. |
| [control-plane-kit-core/tests/test_public_ingress.py](../../../control-plane-kit-core/tests/test_public_ingress.py) | test / assertion support | reviewed | [companion](tests/test_public_ingress.py.md) | North: full ingress owner/test/notes; exact graph references and origin helper. |
| [control-plane-kit-core/tests/test_read_projection_contract.py](../../../control-plane-kit-core/tests/test_read_projection_contract.py) | test / assertion support | reviewed | [companion](tests/test_read_projection_contract.py.md) | North: full owner/test/notes, prior security helper; selected HTTP/parity. |
| [control-plane-kit-core/tests/test_recovery_planning.py](../../../control-plane-kit-core/tests/test_recovery_planning.py) | test / assertion support | reviewed | [companion](tests/test_recovery_planning.py.md) | North: full owner, test and both recovery notes read. |
| [control-plane-kit-core/tests/test_resource_lifecycle.py](../../../control-plane-kit-core/tests/test_resource_lifecycle.py) | test / assertion support | reviewed | [companion](tests/test_resource_lifecycle.py.md) | North: seven owners read; governing tests sampled. |
| [control-plane-kit-core/tests/test_revision_history_contract.py](../../../control-plane-kit-core/tests/test_revision_history_contract.py) | test / assertion support | reviewed | [companion](tests/test_revision_history_contract.py.md) | North: full tests/notes; selected exact HTTP/parity entries; prior projection/MCP context. |
| [control-plane-kit-core/tests/test_run_identity.py](../../../control-plane-kit-core/tests/test_run_identity.py) | test / assertion support | reviewed | [companion](tests/test_run_identity.py.md) | North: seven owners read; governing tests sampled. |
| [control-plane-kit-core/tests/test_runtime_authority_recipient.py](../../../control-plane-kit-core/tests/test_runtime_authority_recipient.py) | test / assertion support | reviewed | [companion](tests/test_runtime_authority_recipient.py.md) | North: full authority owner and recipient validator; selected observation/tests/fixture. |
| [control-plane-kit-core/tests/test_runtime_connection_admission.py](../../../control-plane-kit-core/tests/test_runtime_connection_admission.py) | test / assertion support | reviewed | [companion](tests/test_runtime_connection_admission.py.md) | North: full authority owner and recipient validator; selected observation/tests/fixture. |
| [control-plane-kit-core/tests/test_runtime_effect_intent.py](../../../control-plane-kit-core/tests/test_runtime_effect_intent.py) | test / assertion support | reviewed | [companion](tests/test_runtime_effect_intent.py.md) | North: source claims checked; test navigation sampled. |
| [control-plane-kit-core/tests/test_runtime_effect_observation.py](../../../control-plane-kit-core/tests/test_runtime_effect_observation.py) | test / assertion support | reviewed | [companion](tests/test_runtime_effect_observation.py.md) | North: source claims checked; test navigation sampled. |
| [control-plane-kit-core/tests/test_runtime_effect_observation_boundary.py](../../../control-plane-kit-core/tests/test_runtime_effect_observation_boundary.py) | test / assertion support | reviewed | [companion](tests/test_runtime_effect_observation_boundary.py.md) | North: source claims checked; test navigation sampled. |
| [control-plane-kit-core/tests/test_runtime_effects.py](../../../control-plane-kit-core/tests/test_runtime_effects.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_saga.py](../../../control-plane-kit-core/tests/test_saga.py) | test / assertion support | reviewed | [companion](tests/test_saga.py.md) | North: consequential saga/scenario owners checked; tests sampled. |
| [control-plane-kit-core/tests/test_scaffold.py](../../../control-plane-kit-core/tests/test_scaffold.py) | test / assertion support | reviewed | [companion](tests/test_scaffold.py.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/tests/test_scheduling.py](../../../control-plane-kit-core/tests/test_scheduling.py) | test / assertion support | reviewed | [companion](tests/test_scheduling.py.md) | North: consequential saga/scenario owners checked; tests sampled. |
| [control-plane-kit-core/tests/test_secret_provider_contract.py](../../../control-plane-kit-core/tests/test_secret_provider_contract.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_temporal_history_read_contract.py](../../../control-plane-kit-core/tests/test_temporal_history_read_contract.py) | test / assertion support | reviewed | [companion](tests/test_temporal_history_read_contract.py.md) | North: full tests/notes; selected exact HTTP/parity entries; prior projection/MCP context. |
| [control-plane-kit-core/tests/test_topology_graph.py](../../../control-plane-kit-core/tests/test_topology_graph.py) | test / assertion support | reviewed | [companion](tests/test_topology_graph.py.md) | North: consequential graph contracts checked; test bodies sampled. |
| [control-plane-kit-core/tests/test_unit_of_work_boundary.py](../../../control-plane-kit-core/tests/test_unit_of_work_boundary.py) | test / assertion support | reviewed | [companion](tests/test_unit_of_work_boundary.py.md) | North: full transaction owner/test/notes and Postgres UOW; selected service contracts. |
| [control-plane-kit-core/tests/test_verification_capabilities.py](../../../control-plane-kit-core/tests/test_verification_capabilities.py) | test / assertion support | reviewed | [companion](tests/test_verification_capabilities.py.md) | North: consequential claims checked; navigation sampled. |
