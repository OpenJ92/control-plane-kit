Source: [control-plane-kit-core/src/control_plane_kit_core/node_control_surface_read_results.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/node_control_surface_read_results.py).
Maintain this document alongside its source file. When result variants, request/declaration binding, coverage laws, context bounds or codec admission change, verify and update this companion in the same change.

This module owns two pure surface-read result values and a codec tied to an
expected request and declaration. It turns supplied public facts into an exact
capability response or a bounded installed-name coverage response. It performs
no HTTP parsing, authentication, registry lookup, handler invocation, persistence
or external effect.

```text
request + expected declaration -> validate context -> result codec
  capabilities: expected declaration -> capability result
  status: canonical installed subset -> derived coverage result

untrusted mapping + that codec -> bounded, context-checked result value
```

## Objects and derived claims

NodeControlSurfaceCapabilitiesResult stores only request and declaration.
NodeControlSurfaceStatusResult adds installed_variable_names. Both are frozen
ordered dataclasses whose stored fields are hidden from repr. Their sum is
NodeControlSurfaceReadResult; the module's nominal type tuple is used by encode.
There is no generic success/failure envelope, freeform diagnostic, health flag,
variable state or command result in this language.

Each value validates that its request and declaration have the right nominal
types, that request.declaration_identity equals declaration.identity(), and that
the request target's provider socket matches the declaration's socket. Capability
and status variants additionally require the corresponding request kind. A
declaration does not independently name a workspace/revision/node, so matching
its identity/socket does not establish graph provenance for the other target
coordinates.

Profile is always workload-node-control-surface-read-result.v1 and
canonicalization is JCS RFC 8785. Request ID, request digest and declaration
identity are derived properties, not separately writable dataclass inputs.
The [declaration/request owner](../../../../../control-plane-kit-core/src/control_plane_kit_core/node_control_surface_reads.py)
owns their canonical preimages and distinct identity types. The capability
descriptor has seven exact fields and includes the complete expected declaration;
the status descriptor has eight and instead includes installed names and derived
coverage. Both expose canonical bytes; this module introduces no separate result
digest type or raw-byte result decoder.

## Coverage is a structural law

Installed names must be a tuple of at most 128 role-tagged VARIABLE references,
already sorted, unique and drawn from the declaration's variable names. Input is
validated, not silently sorted or deduplicated. Coverage is derived:

| Installed tuple | Coverage |
| --- | --- |
| Empty | none |
| Equal to the complete declared tuple | complete |
| Nonempty proper subset | partial |

The declaration's underlying surface requires at least one variable, so these
cases do not overlap on an empty declaration. Coverage says what the supplied
names represent relative to the declaration. The caller must obtain those names
from an appropriate registry; the value cannot establish that a handler exists,
is reachable, is healthy or implements its advertised operation correctly.

## A codec with a fixed expected context

NodeControlSurfaceReadResultCodec validates and retains the request/declaration.
Its two factory methods build the corresponding values from that context. The
wrong-kind factory fails through the value's constructor. Encode requires a
nominal result and equality with the codec's expected kind, request and
declaration; equal-shaped output for another request cannot be substituted just
because its byte count fits.

The global canonical bounds are 16,902 bytes for capabilities and 4,811 for status.
Construction enforces each variant's bound. The codec also derives a tighter
context maximum: exact capabilities-result length, or the length of the complete
installed-name status result for its expected declaration. Thus codec construction
itself may reject a context whose corresponding full result exceeds the global
bound. A published name-count cap alone does not promise that every combination
of individually bounded values is admitted.

Decode first checks Mapping/string-key shape, canonicalizes the mapping under
the global limit and checks the context maximum. Only then does it require the
exact key set selected by the trusted request kind and compare profile,
canonicalization, kind, request ID, computed request digest and declaration
identity. Claimed kind does not select an arbitrary decoding branch.

For capabilities, the nested declaration must decode and equal the expected
declaration. The codec returns a newly constructed capability result from its
own context. For status, the wire list becomes VARIABLE references, then passes
the tuple/order/uniqueness/subset laws. The claimed coverage enum must equal the
derived coverage before a result is returned. Shared profile and canonicalization
do not make these two descriptor variants interchangeable.

## Representation and error boundary

Canonical output comes from the shared
[public-wire owner](../../../../../control-plane-kit-core/src/control_plane_kit_core/_node_control_public_wire.py).
This codec accepts mappings, so raw-byte limits, duplicate JSON keys and
noncanonical incoming byte spelling require handling at a parsing boundary;
there are no raw bytes left to inspect here. Aggregate admission canonicalizes
before exact-key and nested-value checks. It bounds admitted output size, not
all work performed by arbitrary Mapping callbacks or serializer recursion.

Known canonical-domain failures and selected nested declaration/reference errors
are translated into categorical NodeControlSurfaceReadContractError messages
without retaining the caught exception chain. Other exceptions are not globally
wrapped. The code emits fixed field/category messages rather than including
untrusted key/value text, but the finite tested cases are not a universal
exception-safety or disclosure guarantee.

Result repr omits request/declaration/installed names. Descriptors deliberately
expose their public request correlations, declaration material or installed-name
subset. That is a distinct disclosure surface, with no general secret scrubber
added by this result module. The underlying public-wire and variable/declaration
contracts remain consequential dependencies.

## Governing evidence and remaining obligations

The [eight-test suite](../../../../../control-plane-kit-core/tests/test_node_control_surface_read_results.py)
checks fixed canonical vectors, exact stored dataclass fields and union members,
derived claims, invalid context, cross-kind/request/declaration substitution and
coverage contradictions. A same-size substituted declaration isolates semantic
binding from the aggregate check. Other cases exercise missing/extra fields,
selected wrong types, reachable global maxima and context-plus-one rejection
before later nested or request checks. A 129-name input below the byte bound
isolates the count rejection.

Nested canaries and result repr checks cover selected diagnostics/disclosure.
Eight selected root bindings must equal module bindings and appear in the module's
`__all__`; the test does not compare the complete export lists. Its AST excludes
six dependency roots and its milestone check looks for a source string, not a
complete current package inventory. These are finite ownership guards.

The [wire contract](../../../../../control-plane-kit-core/docs/NODE_CONTROL_CANONICAL_WIRE.md)
states that structural registry coverage does not prove names came from a live
registry. Consumers must preserve trusted request/declaration context, obtain
observations through the owning interpreter and enforce applicable authority.
This language records no execution attempt or durable history and supplies no
freshness, authentication, adoption or mutation permission.

Authoring read the full 591-line owner, full 792-line test/helpers and fixed
surface-read fixture, with selected actual declaration/request and shared-wire
dependency context. Only this owner receives coverage in this slice. No imports,
executable tests, hashing tools, HTTP, registry, signing, persistence or provider
effects were run. Changes to result claims or limits must keep the fixed vectors,
consumer contexts and downstream parsing/disclosure obligations aligned.
