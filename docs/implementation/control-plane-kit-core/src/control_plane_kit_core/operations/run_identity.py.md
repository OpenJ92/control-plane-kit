Source: [control-plane-kit-core/src/control_plane_kit_core/operations/run_identity.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/run_identity.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This is the canonical public `RunId` class: a frozen, ordered, slotted nominal
wrapper around text admitted by the
[private run grammar](../../../../../../control-plane-kit-core/src/control_plane_kit_core/_run_identity.py). It rejects
malformed input with a fixed error and has no durable state or I/O, despite
living under `operations` within Core.

Consumers must preserve this class identity through exports rather than define
an equivalent-looking wrapper. A RunId is not equal to its raw string.
[test_run_identity.py](../../../../../../control-plane-kit-core/tests/test_run_identity.py)
protects construction, immutability, import order and use in request/attempt
descriptors. Journal and grant fields that deliberately retain strings use the
shared predicate; they are not invitations to coerce arbitrary identifiers.
