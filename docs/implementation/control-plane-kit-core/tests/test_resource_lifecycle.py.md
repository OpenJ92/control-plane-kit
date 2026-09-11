Source: [control-plane-kit-core/tests/test_resource_lifecycle.py](../../../../control-plane-kit-core/tests/test_resource_lifecycle.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

These tests compose
[lifecycle values](../src/control_plane_kit_core/lifecycle.py.md), graph codec,
diff and activity planning. The key law is independent compute/data lifecycle:
ordinary topology removal produces appropriate stop/remove-compute activities
without inventing data destruction. Retained compute and external ownership
take distinct planning paths.

Explicit data destruction must carry critical risk and destructive impact;
changing lifecycle policy produces a ReviewChange blocker instead of automatic
reconciliation. The fixtures and operation assertions are pure planning
evidence. They do not delete containers, retain real volumes or exercise the
approval/execution path. Keep runtime cleanup evidence with the interpreter
that performs those effects.
