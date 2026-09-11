Source: [control-plane-kit-core/tests/fixtures/node_control_surface_read_canonical_wire_v1.json](../../../../../control-plane-kit-core/tests/fixtures/node_control_surface_read_canonical_wire_v1.json).
Maintain this document alongside its source file. When declaration/request identities, result shapes, canonical vectors or consumer assumptions change, verify and update this companion in the same change.

This 213-line fixture identifies `cpk.node-control.surface-read.canonical-wire.v1`
and `jcs-rfc8785.v1`. It stores eight descriptor/UTF-8/SHA-256 triples: two
declarations, two requests, one capabilities result and three status results.
Expected text and digest strings are fixed literals, separate from computations
that consumers compare against them. Their independent derivation was not
verified during this review. The file has no signatures, grants, credentials,
hexadecimal byte vectors, binary64 edge vectors or SDK execution records.

## Two related declaration/request contexts

The top-level declaration wraps one scalar mode variable at provider socket
control. It includes public description, state codec, read/apply operation
contracts, node-control route set and node-controllable capability. The
`workload-node-control-surface-declaration.v1` profile is inside its hashed
envelope; that envelope contains profile and surface, without a separate
canonicalization field. The top-level fixture label still names its encoding.

The capabilities request targets workspace-1/revision-7/router/control, names
surface-read-1 and embeds the first declaration's identity. Its own versioned
profile, canonicalization, target, kind, declaration identity and request ID all
belong to the request digest preimage. The capabilities result embeds the complete
declaration and repeats its identity, the request ID and request digest under the
common `workload-node-control-surface-read-result.v1` profile.

Under `results.status_context`, a second declaration instead contains scalar
alpha and beta variables. A separate status request names surface-status-1 and
binds that declaration's identity at the same target. All three status results
refer to this second request and declaration, not the top-level mode context:

| Vector | Installed variable names | Registry coverage |
| --- | --- | --- |
| status_none | empty | none |
| status_partial | alpha | partial |
| status_complete | alpha, beta | complete |

Status results contain the declaration identity rather than its whole body.
Their different name/coverage fields produce distinct complete result byte/hash
expectations. Declaration identity, request digest and a fixture's result SHA-256
are hashes of different preimages; none is a signature or proof of origin.

## How the tests use these vectors

Selected tests in
[test_node_control_surface_read_authority.py](../../../../../control-plane-kit-core/tests/test_node_control_surface_read_authority.py)
construct the mode declaration and capabilities request with their helpers. They
compare encoded descriptors and canonical bytes with the top-level vectors,
hash the stored expected bytes, compare the value's identity/digest and check
mapping round trips. The same tests construct separate maximum-size examples;
those limits are not encoded as maximum vectors in this fixture.

The canonical-result test in
[test_node_control_surface_read_results.py](../../../../../control-plane-kit-core/tests/test_node_control_surface_read_results.py)
decodes the appropriate fixture declaration/request to build a result codec bound
to that context. It generates the capability result and the three status results
from the listed installed names. Its helper compares descriptors, canonical bytes,
direct RFC 8785 encoding and the stored byte hashes, then checks mapping decode
equality. Another helper checks both status-context preimages against their
descriptor/text/hash vectors. These are mapping and canonical-output assertions,
not a raw-byte result decoder test or a cross-language SDK execution.

The result test also checks the two nominal result variants and their kinds,
request associations and derived coverage. Its exact two-member union assertion
belongs to the test, not the fixture schema. The selected consumers use nested
vectors; this note does not claim that they validate every top-level fixture
metadata field or that other SDKs have consumed the file.

## Meaning supplied by the language owners

The [surface-read owner](../../../../../control-plane-kit-core/src/control_plane_kit_core/node_control_surface_reads.py)
hashes the complete declaration/request canonical envelopes into distinct nominal
identity types. The [result owner](../../../../../control-plane-kit-core/src/control_plane_kit_core/node_control_surface_read_results.py)
requires request/declaration identity agreement and a matching provider socket.
Capabilities include the exact expected declaration. Status names must be a
canonical, unique tuple of variable references drawn from that declaration;
coverage is derived as empty, proper subset or complete tuple. Decode checks the
claimed coverage against the derived value.

Those owners explain why the fixture has a separate status context and why
`complete` is a statement about declared-name coverage. Neither these data nor
their pure codecs inspect a live registry, prove handlers were installed, contact
the target, read variable state or authorize a command. Hash binding supplies
correlation and shape consistency, not freshness, provenance or authentication.

The [wire contract](../../../../../control-plane-kit-core/docs/NODE_CONTROL_CANONICAL_WIRE.md)
requires SDKs to consume these fixed vectors before claiming compatibility.
Authoring read the full fixture, selected actual consumer assertions/helpers,
selected declaration/request/result constructors, codecs and coverage guards,
and the governing wire section. No additional owner or test rows gain review
credit from those reads. No imports, executable tests, hashing tools, SDK runs,
signing or provider effects were performed. This companion describes stored
expectations and assertion intent, not fresh acceptance evidence.
