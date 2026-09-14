Source: [control-plane-kit-core/tests/test_node_control_public_material.py](../../../../control-plane-kit-core/tests/test_node_control_public_material.py).
Maintain this document alongside its source file. When construction paths, vectors, representation rules, assertions or evidence limits change, verify and update this companion in the same change.

This 284-line suite has six tests connecting public-material rules to the
[node_control.py language](../../../../control-plane-kit-core/src/control_plane_kit_core/node_control.py).
It constructs nominal graph references, a scalar variable with read/apply
contracts, an apply-command request and an unsigned workload grant. Its request
helper places the same canary in request/idempotency identities and scalar
payload; its grant helper places it in issuer, key, audience and JTI. Those
fixtures are values, not authenticated requests or executed transitions.

## Field admission

The [JSON fixture](fixtures/node_control_public_material_v1.json.md) test checks
the exact schema/projection labels, then sends every general accepted/rejected
vector through `ControlPlaneVariableDescriptor.description`. The actual owner
checks nonempty stripped text, a 512-character bound and no NUL before shared
classification. This suite does not independently exercise all those bounds.
Accepted descriptions are compared directly; rejected descriptions must raise
`NodeControlContractError` matching the vector's law regex.

The identifier test admits four ordinary names in both `ScalarControlState`
and a NODE-role graph reference. Its four rejected compact-token/localhost/IP
examples exercise scalar state only; it does not repeat that negative matrix
through graph references. Actual string scalar and graph-reference guards both
use the identifier classifier. Map keys use that classifier, and map values use
the scalar validator, which admits a bounded numeric/bool/null domain as well
as identifier-shaped strings. Description admission therefore does not imply
that the same text can be a payload string.

The authority test uses `dataclasses.replace` to change only an otherwise
constructed grant's issuer for each authority vector. That invokes constructor
validation again. Issuer and audience use reference grammar before shared
material checks; key/JTI/request identities use identifier grammar. The test
does not independently vary every authority field or authenticate any issuer.
Its `bounded reference` failures and envelope failures protect different layers.

## Error and representation evidence

The three malformed-input cases make deliberately narrow assertions:

- An unknown command-descriptor key is absent from error `str`, and its value
  is absent from error `repr`. The codec rejects unknown fields without
  rendering their names; this case does not assert cause/context emptiness.
- A map with an admissible key and rejected compact-token value excludes both
  selected canaries from combined error str/repr. It does not check cause/context.
- An invalid operation string is absent from error repr, with cause and context
  both `None`. The actual enum helper raises its fixed field error after leaving
  the caught `ValueError` handler.

The representation test supplies one admitted canary to ten selected value
shapes: a graph reference, scalar/map state, request, grant, variable and four
result variants. It requires that canary absent from object repr. Actual
dataclass fields use `repr=False` for open identities/text; nested state and
references preserve that selected behavior. The test explicitly requires the
canary present in request and variable descriptor repr. Object diagnostics and
wire projections thus have different disclosure contracts. This is not a check
of every object field, every exception path, all logging or semantic secret
redaction. Result constructors validate structural variants without proving an
external read or state change occurred.

## Wire and ownership limits

The final test round-trips one request through the mapping codec: `encode`
returns a descriptor dictionary, not canonical bytes. It compares a computed
request digest with a new nominal digest built from that same computed value;
there is no independent hash oracle or fixed-byte vector here. One evidence
descriptor equals `{"code": "internal-failure"}`; this does not exhaust the
closed evidence algebra or its negative cases. Actual request hashing delegates
canonical serialization to the shared helper before SHA-256.

Four raw substrings must be absent from the entire source text: `urllib.parse`,
`control_plane_kit_operations`, `control_plane_kit_core.secrets` and `fastapi`.
This is a finite textual check, including comments/strings, rather than an AST
dependency inventory or transitive/dynamic effect proof.

Review depth: full suite/helpers and JSON fixture; retained full shared wire
owner; actual selected graph-reference, scalar/map, payload/request/codec,
grant, evidence/result, variable/operation-contract, decoder and validation
paths. The large language owner was not reviewed in full for this packet.
No imports, tests, credential resolution or provider actions ran. Admission,
representation omission and digest construction confer no execution authority.
