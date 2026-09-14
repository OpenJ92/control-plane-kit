Source: [control-plane-kit-core/tests/test_topology_graph.py](../../../../control-plane-kit-core/tests/test_topology_graph.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This file protects additive versus replacement semantics in
[DeploymentGraph](../src/control_plane_kit_core/topology/graph.py.md). Duplicate
node/runtime/edge additions reject without erasing the existing value; explicit
node update requires an existing identity. Compiled duplicate blocks, nested
runtime reuse and duplicate edge identities must not disappear into dict
overwrites.

The local PureImplementation supplies endpoints to the compiler and has no
provider behavior. These are construction/compilation laws, not a global
uniqueness proof for every kind of identifier or externally owned resource.
Graph validity still belongs to the semantic validator.
