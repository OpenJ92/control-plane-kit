Source: [control-plane-kit-core/src/control_plane_kit_core/topology/codec.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/topology/codec.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Authoritative graph representation

GraphDescriptorCodec owns the persisted graph descriptor language and registered
BlockSpec variants. Registration is explicit by variant and exact spec type;
duplicate variants/types reject, and unknown subclasses do not silently use the
generic codec. Comparing supported languages examines variant/spec-type/codec-
class mappings, not arbitrary runtime equivalence of codec implementations.

Decode constructs typed graph values, checks references and compares re-encoded
material with normalized input. This rejects unrepresented/unknown fields as
lossy rather than silently dropping them. List/tuple input is normalized and
empty runtime_authority_deliveries has an explicit omission compatibility rule;
do not generalize one supported normalization to arbitrary schema evolution.

Component owners validate protocol, lifecycle, environment, artifacts,
verification, authority and delegation shapes. This file checks node/runtime
identity and membership, edge references/protocols, ingress connector placement
and delegation projection relationships. Its checks are not the whole
[semantic validator](validation.py.md): a graph still needs required-connection,
cardinality and other findings before diff/planning.

The codec retains exact authored material, including literal endpoints,
configuration content and secret references. It is a storage representation,
not a browser-safe redacted projection or an authorization decision. Direct
Node.descriptor starts with generic BlockSpec fields; this codec replaces those
fields with the registered variant encoding.

[test_graph_codec.py](../../../../../../control-plane-kit-core/tests/test_graph_codec.py)
protects custom variants, reference-only authority/addresses, malformed links
and lossless closure. [test_graph_validation.py](../../../../../../control-plane-kit-core/tests/test_graph_validation.py)
shows why the selected codec travels with validation. Some error paths carry
underlying causes or identities; do not infer universal traceback redaction.
