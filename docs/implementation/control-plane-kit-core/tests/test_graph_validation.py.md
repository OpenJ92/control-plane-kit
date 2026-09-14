Source: [control-plane-kit-core/tests/test_graph_validation.py](../../../../control-plane-kit-core/tests/test_graph_validation.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This file owns structured semantic finding laws for
[validate_graph](../src/control_plane_kit_core/topology/validation.py.md).
It distinguishes required-socket errors from optional-socket warnings, preserves
the original graph/selected codec, and rejects malformed membership, endpoints,
duplicate sockets, multiple provider connections, edge environments and
verification socket/protocol mismatches.

ExtendedBlockSpec fixtures require an explicit registered codec; passing the
default codec is deliberately insufficient. The tests demonstrate pure
validation facts, not a production admission policy or a live provider check.
Do not collapse warnings into errors or drop codec evidence merely to simplify
a consumer interface.
