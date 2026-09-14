Source: [control-plane-kit-core/tests/test_graph_codec.py](../../../../control-plane-kit-core/tests/test_graph_codec.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This file protects the
[authoritative graph codec](../src/control_plane_kit_core/topology/codec.py.md),
including registered BlockSpec extensions, typed runtime-authority references,
opaque secret addresses and environment variants. PureImplementation provides
graph material; it is not a Docker adapter.

Unknown fields must not be silently lost, while explicitly supported tuple/list
normalization is preserved. Negative cases cover inline password material,
missing runtime/socket references, ingress target/connector relationships and
closed protocol/binding shapes. A codec round trip is representation evidence,
not complete semantic validation, public redaction or deployment success.
