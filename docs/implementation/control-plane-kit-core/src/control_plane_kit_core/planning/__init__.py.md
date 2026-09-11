Source: [control-plane-kit-core/src/control_plane_kit_core/planning/__init__.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/__init__.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Planning import surface

This facade re-exports the canonical activity values, codec and diff compiler,
alongside scenario expectations, recovery candidates and the saga/program,
decision, journal and schedule language. It defines no alternative planning
implementation. Importing an available name is not evidence that a runtime can
execute it or that recovery is authorized.

Read [activity_plan.py](activity_plan.py.md),
[codec.py](codec.py.md) and [compiler.py](compiler.py.md) for the initial
diff-to-plan path. The separate
[scenarios](../../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/scenarios.py),
[recovery](../../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/recovery.py)
and [saga](../../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/saga.py)
owners explain their transformations. Scenario expectations describe intended
test observations; exported compensation/recovery values do not expand product
permission or promise automatic provider repair.

Maintain imports and __all__ together with the defining owner and actual root
consumer. Re-exports load those modules; this file alone cannot establish a
transitive import, effect-isolation or complete API compatibility proof.
The planning test family consumes this facade, with behavior owned by the
specific values and transformations rather than the export list.
