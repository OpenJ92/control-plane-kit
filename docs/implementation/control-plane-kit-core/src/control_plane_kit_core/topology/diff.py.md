Source: [diff.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/topology/diff.py).
Maintain this companion with source and imported contract changes.

Runtime management additions, removals and replacements produce a runtime-owned
RUNTIME_MANAGEMENT ModifiedChange with typed optional before/after values. Equal
selections produce no field change. The planner maps this change to review,
preserving the distinction between control-path selection and physical runtime
configuration; no source-of-truth flags or generated bindings are retained.
