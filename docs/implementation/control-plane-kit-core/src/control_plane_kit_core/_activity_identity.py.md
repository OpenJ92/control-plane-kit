Source: [control-plane-kit-core/src/control_plane_kit_core/_activity_identity.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/_activity_identity.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This private predicate is the shared activity-identity grammar: an exact
built-in string, 1–200 ASCII characters, starting alphanumerically and continuing
with alphanumerics or `._:-`. It returns a boolean without coercion, disclosure
or exceptions of its own. Do not replace the exact-type check with an accepting
string-subclass protocol.

Selected consumers are [ActivityId](../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/activity_plan.py),
[EffectAttemptIdentity](../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/recovery.py) and
[secret grants](../../../../../control-plane-kit-core/src/control_plane_kit_core/secrets.py). They own nominal wrappers,
optional fields and categorical errors. Keeping the predicate beneath those
languages avoids importing one public owner merely to reuse its validation;
the [identity tests](../../../../../control-plane-kit-core/tests/test_activity_identity.py)
also protect import order and a private effect-free helper surface.
