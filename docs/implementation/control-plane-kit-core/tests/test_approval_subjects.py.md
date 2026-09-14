Source: [control-plane-kit-core/tests/test_approval_subjects.py](../../../../control-plane-kit-core/tests/test_approval_subjects.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Approval review identity examples

This pure suite checks the
[closed approval subjects](../src/control_plane_kit_core/approval_subjects.py.md):
plan identity/digest shape and round trip, rotation role lists and round trip,
a changed lifetime producing a changed review digest, and distinct request/
decision scopes for rotation.

The plan test does not hash plan contents; it only constructs a plan-ID subject.
The rotation absence checks concern the selected fixture descriptor and named
secret/key fields. They do not prove a generic content scrub, durable
subject-to-plan/rotation linkage or installed gateway verifier state.

The file contains positive examples and selected policy denials, not a complete
malformed-descriptor or constructor-type matrix. In particular, the owner's
different numeric admission for direct construction and descriptor decoding is
not tested here. Fixture IDs and the repeated digest are examples, not live
authority coordinates.

The full file and owner were read. No execution was performed; Operations
approval/admission tests own durable composition, and the existing Core
Docker-backed suite owns executable validation of this pure layer.
