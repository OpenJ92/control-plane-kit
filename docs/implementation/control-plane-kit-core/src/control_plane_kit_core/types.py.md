Source: [control-plane-kit-core/src/control_plane_kit_core/types.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/types.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Primitive topology vocabularies

`Protocol` is a validated product of transport and application semantics.
Compatibility is equality of that product, so sharing TCP does not make HTTP
and Postgres compatible. The closed allowed-transport table rules out invalid
pairs at construction; the canonical registry owns compact names, exact
two-field descriptors and endpoint scheme sets. A protocol's compact display
name is not its durable product descriptor.

When adding a protocol, keep its allowed transports, canonical registry,
endpoint schemes and codec behavior coherent. URL scheme acceptance describes
syntax for that protocol; it neither probes an endpoint nor establishes TLS
trust. The [protocol tests](../../../../../control-plane-kit-core/tests/test_protocol.py) protect
closed round trips, invalid pairs and semantic compatibility.

The remaining enums describe socket binding, endpoint visibility, runtime kind,
workspace lifecycle and block family. An `AWS` or `KUBERNETES` enum member
does not prove an implemented provider adapter. `PUBLIC` endpoint scope is
descriptive data, not firewall or exposure authorization. Those meanings depend
on the consuming compiler, validators and external interpreters.
