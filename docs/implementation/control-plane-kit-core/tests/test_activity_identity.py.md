Source: [control-plane-kit-core/tests/test_activity_identity.py](../../../../control-plane-kit-core/tests/test_activity_identity.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This file protects one activity-identity law across
[ActivityId](../../../../control-plane-kit-core/src/control_plane_kit_core/planning/activity_plan.py), planned/dependency
codec identities, [effect-attempt identity](../../../../control-plane-kit-core/src/control_plane_kit_core/operations/recovery.py)
and secret-resolution/custody/revocation grants. Valid boundary lengths and
hostile text/subclass candidates must receive the same admission decision,
with errors that omit the candidate and chained context.

The compiled-ingress fixture checks generated activity IDs against that grammar;
it does not execute ingress. Import-order checks and the private-helper scan
protect ownership without promoting
[_activity_identity.py](../src/control_plane_kit_core/_activity_identity.py.md)
to a public API. Preserve consumer-specific error categories when consolidating
validation. The tests are existing laws, not a new executable result from this
documentation work.
