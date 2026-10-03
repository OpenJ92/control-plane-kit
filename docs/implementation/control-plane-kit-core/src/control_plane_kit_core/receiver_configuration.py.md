Source: [receiver_configuration.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/receiver_configuration.py).
Maintain this companion with source and imported contract changes.

`ReceiverNodeControlConfiguration(target, declaration, verifiers)` is the explicit
`workload-node-control-configuration.v2` successor. Runtime is inside the complete
logical receiver target. Selected graph authority is not installed configuration:
there is no authority context, graph revision or sibling runtime field.

The existing declaration and public-key values keep their wire identities. Shared
private structural validation in `wrapper_configuration` retains exact verifier
families: surface-read always, command when variables exist, health when health
reads exist. Keys remain purpose-scoped, sorted and unique by ID and fingerprint.
Core checks public PEM shape; cryptographic usability remains an SDK concern.

The codec has closed fields, canonical encoding and bounded duplicate-safe JSON
decoding. It reuses the existing 65,536-byte configuration bound and categorical
`WrapperConfigurationError`. Construction and encoding reconstruct nominal values
so mutated dataclasses and nested private material are refused.

The selector takes artifact/environment/surface values and resolves the exact
`CPK_WRAPPER_CONFIGURATION_FILE` public path binding to one JSON/0444 artifact.
It decodes only V2 and requires equality with the single declared surface. The
old selector decodes only V1. Shared slot validation does not probe profiles or
read files, environments or descriptor defaults on a caller's behalf.

For already constructed deployment values:

```python
configuration = ReceiverNodeControlConfiguration(target, declaration, verifiers)
content = ReceiverNodeControlConfigurationCodec().encode_bytes(configuration)
```

Operations still owns provenance, selected authority and lifecycle evidence; SDK
owns loading and verification. No adopter is released by this pure Core child.
Tests cover variable-only and mixed declarations, forged/private inputs, exact
family and byte limits, malformed JSON, profile isolation, slot ambiguity and
two independently named product/graph round trips. No live readiness is claimed.
