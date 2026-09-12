Source: [__init__.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/__init__.py).
Maintain this companion with source and imported contract changes.

The Core entrance reexports pure owner-defined values. NodeHealthReadKind is
owned by node_control; the v2 static status ceiling is owned by
node_control_surface_read_results. Reexports introduce no wrapper implementation,
new module ownership, optional server dependency, signing or process bootstrap.
Existing clean-import and module-boundary tests remain governing checks.

Core #1826 exports the new pure health request/grant/verifier and result
contracts, profiles and derived byte limits from node_health_reads and
node_health_read_results. No SDK, persistence or provider import is introduced.

Core #1827 exports the pure node_health_transit grant/profile/digest/codecs,
verification result/predicate and derived bounds. It reuses the existing health
request language and adds no effects, provider imports or new health result.
