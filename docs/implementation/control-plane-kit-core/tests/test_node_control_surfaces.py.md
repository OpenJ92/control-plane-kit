Source: [control-plane-kit-core/tests/test_node_control_surfaces.py](../../../../control-plane-kit-core/tests/test_node_control_surfaces.py).
Maintain this document alongside its source file. When surface admission, product/graph propagation, legacy descriptors or assertion limits change, verify and update this companion in the same change.

This 379-line suite has nine tests for static workload control surfaces. Helpers
build scalar variables with read-state and apply-command operation contracts,
surfaces with role-tagged provider references, and a container product whose HTTP
providers are derived from those surfaces. Product instantiation and topology
compilation build graph values under a Docker runtime value; no container is
started and no HTTP endpoint is contacted.

The structural path is:

```text
scalar variable descriptors -> provider-socket surface
  -> product runtime contract -> instantiated BlockSpec
    -> topology graph -> descriptor round trip / graph diff
```

## Surface and product admission

The first test encodes variables supplied as zeta/alpha and expects alpha/zeta,
then checks mapping round-trip equality and the provider-reference role. It
rejects an empty surface, duplicate variable names, one unknown descriptor key
and a list with 129 variables. The actual
[surface constructor and codec](../../../../control-plane-kit-core/src/control_plane_kit_core/node_control.py)
require a nonempty tuple of descriptors, at most 128 variables, unique names,
sorted order and bounded descriptor size. Decode requires a list and the exact
surface keys. This test does not exercise every type/key failure, the maximum
accepted count or the byte-size boundary. Its two repr substring checks use
benign values without an injected URL or token, so they do not prove redaction.

The product-contract test admits one HTTP surface and rejects five shapes:
surface without capability, 17 surface entries, capability without a surface,
missing provider and a PostgreSQL provider. The oversized entries are `None`:
the expected "too many" diagnostic tests count admission before item typing.
The [runtime contract](../../../../control-plane-kit-core/src/control_plane_kit_core/products.py)
enforces the node-controllable capability iff surfaces are present, unique
surface sockets, matching HTTP providers and a 16-surface maximum.

A separate test creates the same variable name under two different surface
sockets, expects socket-sorted surfaces and rejects a repeated socket. This
supports the distinction between socket identity and variable identity within
that socket; it is not a graph-membership or request-routing test. The field-order
test checks only that `control_surfaces` is last in `dataclasses.fields` for
BlockSpec and ProductRuntimeContract. It does not instantiate old positional
calls or freeze every preceding field, default or signature.

## Descriptor compatibility and graph propagation

The product-codec test reads the
[external proxy fixture](../../../../control-plane-kit-core/tests/fixtures/external-products/proxy/product.cpk.json),
checks retained document bytes and their fixed SHA-256, and checks omission of
`control_surfaces` from the decoded legacy runtime contract. It round-trips one
new surface-bearing contract, checks one encoded surface and rejects an explicit
empty list, 17 placeholder objects and an unknown key. The size diagnostic again
distinguishes the count guard from item validation. The fixture digest is a
fixed compatibility oracle for that one document, not image provenance or a
general proof that all historical descriptors remain compatible.

The graph test compares a routing-only product with a routing-plus-mode product.
After encoding/decoding the first graph, it compares the router's surfaces.
After validating both graphs and diffing them, it filters for modified
BLOCK_SPECIFICATION fields and expects one. It does not assert full graph
equality, the complete diff, the exact change payload or an executable plan.
Instantiation copies the surfaces into BlockSpec; the graph diff compares block
specifications and represents a changed specification as a field modification.

The validation test deliberately bypasses product admission using a direct
ApplicationBlock. Missing providers and a same-named PostgreSQL provider each
produce an invalid graph with a NODE_CONTROL_SURFACE error. The actual
[graph validator](../../../../control-plane-kit-core/src/control_plane_kit_core/topology/validation.py)
checks the referenced provider and HTTP protocol at a socket subject. The test
asserts neither that exact subject/message nor a sole error. These declarations
do not establish endpoint reachability, handler behavior or authenticated access.

The legacy graph test constructs one hello block without surfaces, checks field
omission and descriptor round-trip equality, and compares the SHA-256 of compact
`json.dumps` UTF-8 bytes with a fixed literal. Its byte contract is that concrete
serialization and graph; it is not the node-control RFC 8785 request wire format.
Adding an explicit empty surfaces list must fail. The
[generic block codec](../../../../control-plane-kit-core/src/control_plane_kit_core/topology/codec.py)
selects the legacy key set or the extended key set and requires omission when
empty. No independent complete expected descriptor or graph byte string is
asserted alongside the fixed digest.

## Public boundary and evidence limits

The final test checks root identity for the surface descriptor type and only
non-None presence for the root codec. It does not assert codec identity,
`__all__` membership or every export. The helper's missing-type/codec assertions
are failures, not conditional skips.

These tests preserve static declarations across pure representations. They do
not issue a node-control request, verify a grant, execute a state transition,
probe an advertised capability or authorize deployment. Their descriptor and
count checks are finite examples, not a universal disclosure or hostile-input
audit. Review read the full test/helpers, full surface constructor/codec and
proxy fixture, plus selected actual product admission/instantiation, BlockSpec,
graph codec/validation/diff and root bindings. That dependency context gives no
additional owner coverage. No tests or runtime effects were executed while
authoring this companion.
