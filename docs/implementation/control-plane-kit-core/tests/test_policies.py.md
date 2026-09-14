Source: [control-plane-kit-core/tests/test_policies.py](../../../../control-plane-kit-core/tests/test_policies.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Pure policy distinctions

These unittest cases exercise
[policies.py](../src/control_plane_kit_core/policies.py.md) with supplied scope
enums, actor strings and hand-built activity plans. They lock the current scope
vocabulary, selected hub/instance read-versus-write decisions, plan risk/impact
aggregation, stronger destructive approval and requester/decider separation.

The tests cover default distinct-principal behavior, explicit ALLOW_SELF,
nondestructive self-approval, blank identities and a raw-string scope rejection.
They demonstrate policy over caller-supplied identities, not credential
authentication, durable approval or HTTP authorization.

Selected destructive-name and typed data-destruction classification, plus
stopped/deleted retention descriptors, have assertions. This is not exhaustive
classification of arbitrary operations or a witness that retention/deletion
actually occurred. The plan risk example supplies its own labels rather than
testing inference of risk from provider effects.

The full local file was read with its owner. No tests were executed for the
companion; validation remains the existing Core Docker-backed suite.
