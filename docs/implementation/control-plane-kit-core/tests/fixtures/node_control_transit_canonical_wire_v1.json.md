Source: [control-plane-kit-core/tests/fixtures/node_control_transit_canonical_wire_v1.json](../../../../../control-plane-kit-core/tests/fixtures/node_control_transit_canonical_wire_v1.json).
Maintain this document alongside its source file. When transit grant descriptors, canonical bytes, digest expectations or consumer assumptions change, verify and update this companion in the same change.

This 34-line fixture contains one `grant` object with three entries: descriptor,
canonical UTF-8 text and SHA-256. It fixes a concrete unsigned
`gateway-node-control-transit-grant.v1` value using `jcs-rfc8785.v1` and the
`gateway-node-control-transit` purpose. The expected text and digest are stored
literals, separate from the implementation computations compared against them;
their independent derivation was not verified during this review. There is no
hexadecimal byte vector, top-level fixture schema, consumer-language list,
signature or private key in this file.

## What the descriptor binds

The example names issuer/key, attempt, workspace/revision and gateway node,
alongside the workload target router/control, variable mode, scalar replacement
codec, request/idempotency identities and request digest. Its audience is
`gateway:workspace-1:gateway-1`. Issued-at and not-before are 100, expiry is 200,
and JTI is transit-grant-1. These are synthetic coordinates and epochs, not
evidence of a deployed graph, active key or currently valid grant.

Two hashes serve different purposes. The nested `request_digest` binds the
request preimage; the outer `sha256` identifies the complete canonical grant
descriptor, which itself includes that request digest. The fixture does not
contain the request payload. The selected transit-test helper supplies the
corresponding mode request with scalar state green and expected version 7;
matching its constructed grant to the fixed descriptor also checks that computed
request digest against the fixture's literal.

## Actual consumers

The canonical-vector test in
[test_node_control_transit.py](../../../../../control-plane-kit-core/tests/test_node_control_transit.py)
reads the grant entry, compares direct `rfc8785.dumps(descriptor)` with the stored
text encoded as UTF-8, and hashes those fixture bytes against the stored digest.
It compares a separately constructed grant's encoded descriptor and canonical
bytes with the fixture, checks the value's canonical digest, and round-trips both
mapping and canonical bytes. These assertions cover this one grant, not a
complete range of profiles, numeric boundaries or malformed wire inputs. The
same test checks eleven selected public binding identities/`__all__` entries;
that export list belongs to the test rather than this JSON data.

The pairwise-substitution test in
[test_node_control_workload_wire.py](../../../../../control-plane-kit-core/tests/test_node_control_workload_wire.py)
uses the descriptor to construct its transit value and the stored canonical text
as raw transit input. It rejects foreign objects and descriptors across five
request/grant languages, and foreign canonical bytes across request, workload
grant and transit grant codecs. It does not use this fixture's outer digest in
that test, exercise a gateway, or prove every malformed input is rejected.

The [transit codec](../../../../../control-plane-kit-core/src/control_plane_kit_core/node_control_transit.py)
checks exact descriptor keys, reconstructs the typed value and checks the encoded
audience against the derived workspace/gateway audience. Its raw decoder requires
bytes, enforces the aggregate limit, parses the mapping and requires re-encoded
canonical bytes to equal the input. The grant digest hashes those complete bytes.
These owner details explain the consumer assertions; this fixture does not
independently exercise every guard or the grant verifier.

## Signing and evidence boundary

The [canonical-wire contract](../../../../../control-plane-kit-core/docs/NODE_CONTROL_CANONICAL_WIRE.md)
requires consumers to sign or verify the complete canonical payload rather than
re-encoding individual fields at the signing boundary. This file is the unsigned
payload vector for that requirement. It does not supply a signed envelope,
cryptographic acceptance vector, key-trust decision, replay record, gateway
forwarding result or workload mutation evidence.

Authoring read the full fixture, the selected transit test/helpers and complete
workload substitution test, selected actual transit descriptor/digest/codec and
the wire-document section, with retained transit constructor/parser context.
Other tests and owner files gain no coverage credit from these reads. No imports,
executable tests, hashing tools, signing or provider effects were run for this
companion; expected digest agreement is described from assertions, not a fresh
execution result. The fixture contains public claims, not credentials or an
authorization to execute them.
