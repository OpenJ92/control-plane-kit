Source: [control-plane-kit-core/tests/test_node_control.py](../../../../control-plane-kit-core/tests/test_node_control.py).
Maintain this document alongside its source file. When node-control values, audience derivation, grant comparison, route contracts or assertion limits change, verify and update this companion in the same change.

This 592-line suite has fourteen tests connecting node-control values, mapping
codecs, workload audience text, unsigned grant comparison and route declarations.
Helpers construct a weighted routing variable, two graph target references with
weights 2/1, an apply request with expected version 4, and matching grant claims
valid between epochs 100 and 200. These are synthetic values; no workload,
provider, signing key or durable idempotency store participates.

## Values and finite admission examples

The first test round-trips four values through their respective mapping codecs:
variable, command request, grant and transition-success result. It checks the
variable's route/capability labels and only the request digest's 64-character
length. A separate read request omits command material and round-trips from its
descriptor. These are computed round trips, not independent fixed-byte or hash
oracles. The declared result version 5 and applied evidence do not execute the
request's expected-version transition.

Strict-decoding examples reject an unknown variable kind, unknown nested command
codec, extra credential field, arbitrary request command-codec string and extra
grant signature field. They assert the contract-error class, without checking
diagnostic redaction or every missing/unknown key. Rejecting a signature field
keeps signed transport material out of this descriptor language; it does not
verify a signature.

The weighted-state test fixes one complete descriptor and rejects six snapshots:
missing weight coverage, duplicate target, negative weight, zero total weight,
infinity and NaN. The scalar/map test fixes two descriptors and rejects three
selected scalar strings, one loopback-address map value and 129 map entries.
Despite its name, that test does not attempt mutation or assert frozen-dataclass
behavior. These examples neither cover every numeric/size boundary nor prove
that every sensitive string is excluded. The actual
[node-control language](../../../../control-plane-kit-core/src/control_plane_kit_core/node_control.py)
uses finite public-material guards and typed immutable state constructors.

Reference examples reject URL node text, loopback socket text, credential-like
workspace text, an empty revision, malformed request/idempotency identifiers and
a URL variable name. Several fail while constructing the reference argument,
before the surrounding target/request constructor runs. They establish selected
local admission failures, not resolution against a graph or durable replay
semantics.

## Workload audience derivation

Five tests concern the audience helper. Each helper lookup asserts non-None root
presence and identity with the node-control module binding; it does not skip if
the function is missing. One example fixes `workload:router:control`. Another
uses 128 node characters and 118 socket characters to admit exactly 256 UTF-8
bytes; increasing the socket to 119 characters must fail with bounded error text,
no socket text in str/repr and no cause/context chain.

Three wrong-type values must fail with bounded text and no chained exceptions.
A dictionary containing credential and endpoint canaries must also fail, omitting
both canaries from str/repr. This rejects that dictionary rather than exercising
every possible duck-typed object. The actual helper requires NodeControlTarget,
formats `workload:{node}:{socket}` and applies the reference guard to the result.
It does not include workspace/revision in that string or establish their
provenance; the grant comparator checks those target fields separately.

## Exact unsigned claim comparison

The binding test accepts one matching grant/request at epoch 150, then swaps the
two routing weights. The changed request must have a different computed digest
and yield REQUEST_MISMATCH against the old grant. Nine altered grants separately
exercise issuer, audience, workspace, revision, node, socket, variable, command
and request mismatch codes. The command case changes both operation and codec to
a valid read-grant shape; it does not isolate a codec-only mismatch. The request
case changes request ID; there is no separate changed-idempotency-key test here.
The final benign descriptor substring check for "secret" is not an injected
secret-redaction test.

The temporal test rejects epochs 99 and 201. Source uses the half-open interval
`not_before <= now < expires_at`; this suite does not assert exact epochs 100 or
200. The same test passes a DelegatedGatewayProbeGrant to the workload comparator
and expects GRANT_TYPE_MISMATCH. Despite the local name `transit_grant`, the
foreign value is a gateway probe grant, not the node-control transit grant type.
Its probe request is constructed as data and never sent.

The actual comparator validates request type and expected issuer/audience/time,
then checks grant type, issuer, audience, time, target components, variable,
operation/codec and request ID/idempotency key/canonical digest in that order.
It returns a bounded result and performs no crypto or I/O. This suite exercises
selected valid inputs and mismatches, not every argument error, constructor
lifetime rule or multi-error precedence. Acceptance does not verify key trust,
signature, revocation, JTI replay, current graph membership or workload version;
those facts require other owners and evidence.

## Evidence and declared routes

The evidence test fixes a failed result's `internal-failure` evidence, excludes
message text and version/state/payload fields, and rejects applied evidence on a
rejected result. It does not exhaust all evidence/result combinations or feed a
driver exception through a redactor.

The final test fixes the ordered four method/path/scope triples from the
[node-control route set](../../../../control-plane-kit-core/src/control_plane_kit_core/control_routes.py):
GET capabilities and status use the surface-read scope, GET one variable uses
the node-control read scope, and POST variable commands uses the apply scope,
all under `/__control`. It checks the advertised capability's route-set identity,
the workload key-purpose string and presence of five selected root exports.
It does not compare route names/descriptions, all purpose members, root binding
identity for those five names or `__all__`. These declarations are protocol data;
the test does not instantiate HTTP handlers or enforce authentication.

Authoring read the full test/helpers, fresh audience helper, full unsigned grant
comparator/result and selected grant temporal constructor, route/capability/key
declarations, with retained request/state/variable/result codec and public-wire
guard context. No additional owner coverage follows from those dependency reads.
No imports, tests or runtime effects were executed for this companion. Its
security boundary is explicit: well-formed values and matching unsigned claims
are inputs to authority enforcement, not permission to perform an effect.
