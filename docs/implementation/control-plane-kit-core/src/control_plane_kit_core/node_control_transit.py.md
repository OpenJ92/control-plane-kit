Source: [control-plane-kit-core/src/control_plane_kit_core/node_control_transit.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/node_control_transit.py).
Maintain this document alongside its source file. When transit claims, canonical bytes, verifier ordering, public bounds or consumer obligations change, verify and update this companion in the same change.

This module owns the pure language for a selected gateway to relay one exact
node-control request. It defines unsigned grant claims, their canonical identity,
a strict mapping/raw-byte codec and an ordered claim comparator. It does not
select endpoints, sign or verify signatures, read keys, retain replay state,
dispatch HTTP or execute the workload command.

```text
typed request + selected graph/key/attempt coordinates
  -> unsigned transit grant -> exact canonical bytes -> grant digest

unsigned grant + request + caller-supplied expected coordinates/time
  -> accepted | bounded rejection code
```

## Grant values and local laws

DelegatedGatewayNodeControlTransitGrant is a frozen ordered dataclass. Its profile
and canonicalization must be the exact V1/JCS enum members. Purpose must be a
DelegationKeyPurpose member, but construction does not restrict it
to the transit purpose: the verifier rejects other recognized purposes. Typed
construction therefore establishes local shape, not transit authorization.

The grant carries issuer/key, attempt, workspace/revision/gateway, workload target,
variable, operation/codec, request/idempotency identities, request digest, three
epochs and JTI. Workspace and revision must match the nested target. References
must carry the expected roles; read-state forbids a command codec and apply-command
requires a recognized codec. The grant has no payload or version field: its
NodeControlRequestDigest binds the separate request containing that material.
The constructor checks the digest type, not a request preimage supplied alongside
it. GatewayNodeControlTransitGrantDigest separately validates 64 lowercase hex
characters for the complete grant identity.

Shared [public-wire admission](../../../../../control-plane-kit-core/src/control_plane_kit_core/_node_control_public_wire.py)
bounds identifiers to 128 ASCII grammar characters and issuer reference text to
256. Role-tagged graph references use the identifier bound. Epochs are exact
integers in 0..2**53-1, excluding bool; issued-at <= not-before < expiry, with
expiry minus issued-at at most 300 seconds. These are local temporal laws, not a
clock-trust or revocation policy.

Audience is a derived property, not a dataclass input:
`gateway:{workspace_id}:{gateway_node_id}`. Two maximum graph identifiers yield
265 ASCII bytes. A serialized audience must match that derivation. The complete
canonical grant is bounded to 2,834 bytes. Reference roles and matching coordinates
do not prove membership in a currently accepted graph.

## Representation, identity and disclosure

The descriptor has exactly 21 top-level keys, including the derived audience,
and exactly four target keys. Optional command codec is represented as null for
read-state; the key is still present. `canonical_bytes()` delegates to the shared
canonical JSON owner and enforces the aggregate bound; `canonical_digest()` hashes
those complete bytes into GatewayNodeControlTransitGrantDigest. This outer hash
and the nested request digest identify different preimages.

Mapping decode first checks Mapping/string-key shape and canonical-domain/size
admission, then exact keys, target/reference reconstruction, audience equality,
enums, scalar fields and the constructor laws. It returns a reconstructed typed
grant. Raw decode requires exact bytes and checks length before JSON parsing.
Duplicate object keys, malformed UTF-8/JSON, nonstandard constants and parser
recursion failures take the bounded malformed path. Successful parsing still
passes mapping admission and must re-encode to exactly the supplied bytes;
noncanonical whitespace/order/spelling cannot silently acquire canonical identity.

Known canonical-domain errors and selected type/value conversion errors are
translated after their handlers, avoiding retained exception chains. This is not
a blanket wrapper around arbitrary Python Mapping callbacks or every serializer
exception. Mapping admission canonicalizes before checking field names, and this
module declares no independent mapping depth budget. The raw byte cap and finite
negative tests should not be described as a universal resource or error-safety
proof.

Issuer/key/attempt/request/idempotency/digest/JTI fields are hidden from grant
repr; nested [graph references](../../../../../control-plane-kit-core/src/control_plane_kit_core/node_control.py)
hide their text as well. Descriptors deliberately expose the public claim values,
including audience and coordinates. Repr suppression is not descriptor redaction,
and the shared finite credential/endpoint recognizers do not classify every
sensitive value or prove an address is safe to contact.

## Ordered verification and caller obligations

The verifier first validates request type and expected issuer/key/attempt/gateway
and time. Those invalid inputs raise a contract error before any grant verdict.
For valid inputs it returns the first failing comparison in this order:

```text
grant type -> transit purpose -> issuer -> key -> time -> attempt
  -> workspace -> revision -> gateway -> workload node -> provider socket
    -> variable -> operation/command codec
      -> request ID / idempotency key / canonical request digest
```

Time uses the half-open interval `not_before <= now < expires_at`. The three
request comparisons share REQUEST_MISMATCH. Acceptance is exactly a true bool
with no code; rejection is false with one of fourteen bounded codes. No arbitrary
provider message is added to the result descriptor.

The caller must supply trusted expectations. The comparator does not authenticate
their origin, validate an Ed25519 signature, check active/revoked key status,
consume JTI/idempotency state, read graph lineage, or observe a workload version.
It does not independently compare an expected audience: the typed grant derives
that from the workspace and gateway coordinates being checked. Passing the
comparison means the unsigned claims agree with these inputs, not that an effect
is approved or has happened.

Selected [Operations intent construction](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/node_control_intents.py)
authorizes a graph target, selects distinct transit/workload signing authorities
and constructs both grant values. The selected
[signing-authority reload helper](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/node_control_signing_authority.py)
uses this comparator against a retained attempt and selected key, then rejects
unavailable/inconsistent unsigned grants. Its expected gateway argument is taken
from the retained transit grant itself; this call alone is not an independent
gateway-selection check. Graph provenance, durable authority and cryptographic
effects remain obligations of their owning services. These selected consumers
were not audited as complete workflows for this companion.

## Evidence and maintenance boundary

The governing [transit tests](../../../../../control-plane-kit-core/tests/test_node_control_transit.py)
include a fixed canonical fixture, reachable 265/2,834-byte maxima, field-bound
negatives, duplicate/noncanonical raw inputs, controlled parser recursion,
constructor contradictions, exact acceptance/expiry endpoints and fourteen
ordered rejection examples. Some cases deliberately violate multiple claims to
exercise precedence; the final combined request failure does not isolate all
three request comparisons. Four authority families reject foreign objects and
descriptors, with selected verifier cross-family checks. Repr/error canaries and
finite AST import/definition exclusions protect selected disclosure and ownership
boundaries; they are not a complete transitive dependency or hostile-input audit.

The [wire contract](../../../../../control-plane-kit-core/docs/NODE_CONTROL_CANONICAL_WIRE.md)
requires signing/verifying the complete canonical bytes. The fixture contains no
signature, and these pure tests provide no gateway-forwarding or workload-mutation
evidence. Authoring read the full 767-line owner and full 898-line governing test,
the fixed fixture, selected actual Operations consumers and shared guard/reference
context. Only this owner receives a companion in this slice. No imports, tests,
signing, persistence or runtime effects were executed. Source changes to claims,
canonicalization or error ordering must update the fixture/tests and actual
consumers coherently, without treating a new digest as an authorization.
