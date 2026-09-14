Source: [compiler.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/compiler.py).
Maintain this companion with source and imported contract changes.

Runtime management reference changes use the existing ReviewChange path. They
are excluded from generic runtime reconciliation because selecting a control
path is not physical runtime configuration. Reference changes alone cannot
produce ReconcileRuntime, StartRuntime or StopRuntime. Equal/name-only graph
pairs retain their existing empty plans. Observation-aware execution planning is
deferred to #1833; the coupled Operations guard is required before #1832 is
accepted. There is no new activity variant or runtime effect in this slice.
