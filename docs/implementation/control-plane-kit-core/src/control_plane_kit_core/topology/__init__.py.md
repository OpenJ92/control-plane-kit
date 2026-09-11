Source: [control-plane-kit-core/src/control_plane_kit_core/topology/__init__.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/topology/__init__.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

This is the public aggregation surface for graph values, authoritative codecs,
validation findings, structural change values and the pure compile/diff
interpreters. It re-exports owning identities; it must not become another
implementation or import Operations/provider code.

Keep the distinction between [changes.py](changes.py.md) (change language) and
[diff.py](diff.py.md) (comparison interpreter), and between
[codec.py](codec.py.md) (representation) and
[validation.py](validation.py.md) (semantic findings). The retained
compile_recipe alias points to compile_topology rather than a parallel path.
Public import and boundary expectations appear across the graph/kernel tests;
exporting a value is not certification that a graph is valid or executable.
