Source: [control-plane-kit-core/tests/test_graph_diff.py](../../../../control-plane-kit-core/tests/test_graph_diff.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This file protects the split between
[change values](../src/control_plane_kit_core/topology/changes.py.md) and
[the diff interpreter](../src/control_plane_kit_core/topology/diff.py.md).
It checks deterministic empty/add/remove/modified forms, explicit field
subjects, unsupported kind transitions and identity/codec-language ambiguity.

Custom BlockSpec fixtures retain their registered before/after shape. A router
target switch is an edge change; authority is a separate field from runtime
metadata. Selected configuration/secret cases assert non-disclosure, but are
not a proof that every diff descriptor is public-safe. Raw or invalid graphs
must be rejected. No assertion here executes a generated plan or approves an
unsupported transition.
