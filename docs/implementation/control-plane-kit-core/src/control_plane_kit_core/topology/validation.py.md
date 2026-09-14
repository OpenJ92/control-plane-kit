Source: [control-plane-kit-core/src/control_plane_kit_core/topology/validation.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/topology/validation.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Semantic graph findings

validate_graph returns the original graph, deterministic typed findings and the
selected [codec](codec.py.md). Errors make valid false; warnings do not.
require_valid returns that graph or raises GraphValidationError carrying the
result. This wrapper records validation findings; it does not freeze every
nested graph object or cryptographically attest to a caller's prior validation.

The validator checks runtime membership, declared endpoints, socket uniqueness
and connection cardinality, protocol/binding agreement, exact edge environment
coverage and producing-edge provenance. Missing required sockets are errors;
unconnected optional sockets are warnings. Self-connections reject. Control
surfaces require declared HTTP sockets, and verification checks require compatible
declared providers using the [verification owner](../../../../../../control-plane-kit-core/src/control_plane_kit_core/verification.py).
Capability route lookup verifies catalogue membership, not live route existence.

A codec encode/decode pass contributes descriptor failures as findings.
GraphValidationPolicy currently has no fields and production() returns that same
empty policy; it is not production provider/catalogue admission. That authority
belongs outside Core.

[test_graph_validation.py](../../../../../../control-plane-kit-core/tests/test_graph_validation.py)
protects findings, original-value/codec preservation and negative graph
relationships. The [diff interpreter](diff.py.md) requires the resulting valid
wrapper and compatible codec language. Neither step inspects actual provider
health, resources or permissions. Finding messages can include supplied
identities; read projections must own disclosure policy.
