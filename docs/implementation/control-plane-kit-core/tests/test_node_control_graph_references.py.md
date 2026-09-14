Source: [control-plane-kit-core/tests/test_node_control_graph_references.py](../../../../control-plane-kit-core/tests/test_node_control_graph_references.py).
Maintain this document alongside its source file. When nominal roles, codec behavior, fixture assertions or provenance boundaries change, verify and update this companion in the same change.

This 336-line suite has seven tests for nominal node-control graph references.
Helpers build a weighted-routing variable, request and unsigned grant around
references with explicit roles. The tests exercise direct Python composition
and mapping codecs; they neither create an admitted topology graph nor prove
that any referenced node, socket, variable or target belongs to one.

## Nominal values and substitution

The first test checks the expected six role names/values, constructs one valid
identifier for each, rejects a raw string role and selected empty, oversized or
non-string values, and checks root exports are the same two type objects. Its
dictionary comprehension selects the six expected members; it does not enumerate
the enum to exclude additional members despite the test's “closed” title. The
actual enum currently contains those six members. The 129-character failure is
not a separate test of successful admission at 128.

The role-placement test rejects five target-field substitutions, selected raw
or wrong-role variable positions in requests, variables and grants, and three
weighted-target/weight substitutions. Actual
[NodeControlGraphReference and field guards](../../../../control-plane-kit-core/src/control_plane_kit_core/node_control.py)
check a nominal reference instance and the required role. They do not consult a
graph store. `isinstance` admission also is not an exact-class or tamper-proof
provenance boundary.

Two foreign wrapper values are deliberately constructed:
[LiteralEndpointMaterial](../../../../control-plane-kit-core/src/control_plane_kit_core/probe_intents.py)
and [SecretReference](../../../../control-plane-kit-core/src/control_plane_kit_core/secrets.py).
Each must fail as a reference's text, a target node, a request variable and a
weighted-state target. The wrappers themselves are valid examples of their own
languages; the test rejects substituting those objects for graph references.
It does not resolve a secret or contact an endpoint. The weighted case puts the
foreign object in both target and weight positions, so first target rejection
does not independently establish the weight-position failure.

## Wire and provenance evidence

Request, workload-grant and variable-descriptor mapping codecs round-trip one
value each. Their `variable_name` remains an ordinary string. The request's
target object and weighted target/weight descriptors are compared with explicit
expected structures. Actual decoders recover roles from each enclosing field,
using `_decode_graph_reference`; the wire does not carry a caller-selected role
or provenance wrapper.

The unknown-field test rejects six request variants, four added grant fields and
one added variable field. Request cases cover top-level endpoint/provenance,
target URL/host/port and weighted-state address. These assert strict descriptor
shape through the existing key guards. They do not constitute a comprehensive
endpoint/secret detector or prove that ordinary admitted strings are public.
No error-text, cause/context or diagnostic-redaction assertions occur here.

The DNS-looking example preserves `router.internal` in a nominal node's target
descriptor and separately rejects a `LiteralEndpointMaterial` containing the
same characters. It also checks the reference class docstring says
“producer-attested” and “does not prove graph membership”. That is the intended
boundary: role/syntax admission and producer provenance are different facts.
The docstring assertion itself does not perform a graph-membership check.

The final test uses all three request vectors in the
[canonical-wire fixture](fixtures/node_control_canonical_wire_v1.json.md), checking
mapping re-encoding equality, fixed canonical text and fixed stored digest.
It does not consume workload-grant or number-vector sections. Five raw strings
must be absent from `node_control.py`: the topology, secrets, probe-intents and
Operations module names plus `fastapi`. This checks source text, including
comments/strings; it is not an import graph or dynamic/transitive dependency
proof. The test module itself imports the two foreign wrappers to test rejection.

Review depth: full suite/helpers; retained full canonical fixture/shared wire
owner and selected nominal/request/grant/weighted/field-guard paths; actual
variable codec, root bindings and foreign wrapper constructors. No whole
node-control owner, graph compiler, provider or SDK audit is claimed. No imports,
tests, secret resolution, graph mutation or provider actions ran. Source review
and nominal values confer no execution or graph-membership authority.
