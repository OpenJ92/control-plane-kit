Source: [control-plane-kit-core/tests/test_protocol.py](../../../../control-plane-kit-core/tests/test_protocol.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This file protects [Protocol](../src/control_plane_kit_core/types.py.md) as a
closed transport/application product. Tests distinguish raw TCP from HTTP and
DNS-over-TCP from DNS-over-UDP, reject unsupported combinations, and preserve
canonical compact-name and descriptor round trips by object identity.
Every declared protocol must have a nonempty endpoint-scheme set.

These are vocabulary and compatibility laws, not network interoperability,
service-health or TLS tests. Adding a transport/application member requires
coherent registry and descriptor changes; accepting an arbitrary string to make
a fixture pass would weaken the closed language.
