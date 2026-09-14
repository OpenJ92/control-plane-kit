Source: [control-plane-kit-core/tests/test_node_control_result_variants.py](../../../../control-plane-kit-core/tests/test_node_control_result_variants.py).
Maintain this document alongside its source file. When result variants, evidence matrices, descriptor/variable binding or assertion limits change, verify and update this companion in the same change.

This 504-line suite has six tests for the nominal node-control result sum. Its
helpers require four result types to exist and build scalar, map and weighted
variables with one state example each. The suite constructs values and runs
mapping codecs; it does not obtain results from a workload or verify a request's
authorization, version freshness or completion.

The actual [result language](../../../../control-plane-kit-core/src/control_plane_kit_core/node_control.py)
separates these shapes:

| Variant | Operation | Result material |
| --- | --- | --- |
| Read success | read-state | state codec, version and state; no evidence |
| Transition success | apply-command | version and applied/no-change evidence; no state |
| Rejected | read-state or apply-command | one operation-compatible evidence code; no version/state |
| Failed | read-state or apply-command | fixed internal-failure evidence; no version/state |

These are declared outcomes. A valid `applied` value does not establish that an
external transition occurred.

## Positive construction and local contradictions

The first test compares each of three successful-read descriptors with an
explicit expected structure and decodes it back to the result. It then uses
the weighted variable for eight other current cases: two transition successes,
one read rejection, three apply rejections and a failure for each operation.
Those cases round-trip and check operation-local codec, status and dictionary
evidence. They are not repeated across all three variable kinds, and their
complete descriptors are not independently fixed literals.

Constructor negatives cover four read-state/codec mismatches or wrong types,
every currently enumerated transition evidence code except applied/no-change,
and all current operation/evidence pairs outside the allowed rejection sets.
Three additional cases reject tuple evidence and raw operation strings. These
protect selected nominal and contradiction rules, not every identifier, version,
state-size, subclass or malformed-evidence boundary.

## The status/evidence matrix

`matrix_descriptor` always inserts evidence and adds version/state fields for
success as appropriate. The test traverses both current operations, three
statuses and six evidence codes: 36 descriptor cases. Eight combinations are
expected to round-trip; all others must raise the contract error. In particular,
every read-success row in this matrix is invalid because it contains evidence.
Valid read successes without evidence are covered by the separate positive test.
“Complete” therefore means this finite enum product with this descriptor builder,
not every possible result descriptor or all combinations of absent fields.

The expected matrix is declared independently in the test, while actual
constructors and codec branches enforce the result shapes and rejection-code
sets. The test is meaningful protection against cross-variant contradictions;
it is not provider evidence or a generalized response validator.

## Strict shapes and variable compatibility

The stale/cross-variant test adds version, state codec, state and payload to one
rejection and one failure, requiring all eight additions to fail. It separately
rejects evidence on a read success and state material on a transition success.
The “stale” wording concerns forbidden descriptor material, not checking a
response against current workload state or the latest request.

The strict-codec test rejects a map read result through a scalar variable's
codec on both encode and decode. For one read-success and one transition-success
descriptor, it removes every required key individually and adds a diagnostic
field. Nine additional cases cover unknown operation/status/result codec,
cross-operation result codecs, unknown state codec, list/unknown evidence and
one legacy shape with several simultaneous incompatibilities. It also rejects
a raw string as the codec's variable. The legacy case does not isolate a single
missing or contradictory field. `provider-secret` in an extra diagnostic field
tests rejection, not redaction: these negative assertions check exception class,
without checking error length, str/repr, cause/context or logs.

Actual `NodeControlResultCodec` uses the variable's
[operation contract](test_node_control_operation_contracts.py.md) to check the
result codec, and checks successful-read state codec compatibility. That is not
full identity binding: the codec has no expected request argument and does not
prove request-ID correlation, admitted graph membership or freshness. Results
contain bounded identities/versions and structural state, not proof of those
external facts. Constructor aggregate-size guards are present in source but
not challenged at their maximum by this suite.

## Exports and review limits

The final test checks the four result types are identical to their
[Core root attributes](../../../../control-plane-kit-core/src/control_plane_kit_core/__init__.py),
the evidence-item constant equals one, and `NodeControlResult` is non-null.
It does not inspect the exact union members or root `__all__`, nor does the
constant assertion by itself prove evidence cardinality. Actual source declares
the four-way union and single-code evidence values; the constructor/codec cases
provide separate shape evidence. No canonical byte/hash fixture is consumed.

Review depth: full test/helpers; actual complete evidence/result constructor
sections and result-codec consumer; retained operation/variable descriptors,
scalar/map/weighted constructors and field/size guards; actual enum/key/mapping
tables and selected root bindings. No whole large-owner, SDK, workload or
provider audit is claimed. No imports, tests, database or provider actions ran.
Structural admission conveys no authority to execute, retry or advance a graph.
