Source: [__init__.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/__init__.py).
Maintain this companion with source and imported contract changes.

The Core entrance reexports pure owner-defined values. NodeHealthReadKind is
owned by node_control; the v2 static status ceiling is owned by
node_control_surface_read_results. Reexports introduce no wrapper implementation,
new module ownership, optional server dependency, signing or process bootstrap.
Existing clean-import and module-boundary tests remain governing checks.
