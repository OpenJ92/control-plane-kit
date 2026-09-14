Source: [admission.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/admission.py).
Maintain this companion alongside its source.

Execution admission retains its existing plan/session/workspace lineage,
permission, approval, risk and readiness checks inside the caller-owned
transaction. Recording a plan derivation profile grants no new authority.

Rotation-child congruence validates the exact approved realized projections and
uses the persisted plan profile to select one Core derivation through the shared
Operations dispatcher. A mismatch does not retry another compiler. Known new
profile/observation failures become bounded conflicts outside parser context.

The rotation publication action and planning-request action have distinct
ownership. Rotation keeps its existing publication/approval checks; sharing a
derivation does not reinterpret publication evidence as a planning-request action.
Direct and pre-effect management transport guards remain in their existing
owners. No provider effects, secret delivery, historical rewrite or rollout is
introduced by this provenance prerequisite.
