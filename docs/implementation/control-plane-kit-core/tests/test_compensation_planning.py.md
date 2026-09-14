Source: [control-plane-kit-core/tests/test_compensation_planning.py](../../../../control-plane-kit-core/tests/test_compensation_planning.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Declared inverse meaning survives representation

The selected operation matrix pairs starts/stops, socket add/remove/switch and
node/runtime reconciliation with explicit inverse operations and BASE_GRAPH or
DESIRED_GRAPH material. Observation has no required compensation; resource
removal and data destruction carry explicit non-compensatable reasons.
This is a selected matrix, not every canonical operation: ingress and review
variants require their other governing tests.

The plan codec test checks a start operation's exact encoded compensation and
round trip, then rejects a changed inverse. Version-one descriptors without
compensation and descriptors with extra top-level fields are rejected instead
of upgraded. These laws belong to the
[activity algebra](../src/control_plane_kit_core/planning/activity_plan.py.md)
and [codec](../src/control_plane_kit_core/planning/codec.py.md).

The final tests check plan-derived saga compensation availability and reverse
completion order after a supplied journal. They do not execute the declared
inverse, prove graph material is still available, or authorize automatic
compensation. Full 186-line file read with the owning algebra, codec and
[saga](../src/control_plane_kit_core/planning/saga.py.md); no executable or
provider validation was run for these notes.
