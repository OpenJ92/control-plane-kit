Source: [control-plane-kit-core/tests/test_verification_capabilities.py](../../../../control-plane-kit-core/tests/test_verification_capabilities.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This file covers two related but distinct owners:
[verification.py](../src/control_plane_kit_core/verification.py.md) defines
semantic checks/results; [capabilities.py](../src/control_plane_kit_core/capabilities.py.md)
advertises operator powers and route sets. The materialization fixture proves
their graph/metadata representation without running a server.

Verification tests preserve closed variants/protocol sets, socket-relative HTTP
targets, restricted Postgres operations and reference-only authentication.
Legacy policy/check descriptors remain readable where explicitly tested.
Expected HTTP digest and match evidence are bounded metadata, not retained
response bodies; successful effect results must not carry a failed completion.
The range-bound policy cases are not a complete finite-number admission audit.

Catalogue tests protect lookup, route associations and RESTARTABLE's absent
route. They do not establish live endpoint availability, permission to invoke a
power or support for every verification variant in a runtime interpreter.
