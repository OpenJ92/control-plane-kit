Source: [__init__.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/__init__.py).
Maintain this companion with source and imported contract changes.

The Core entrance reexports pure owner-defined values. NodeHealthReadKind is
owned by node_control; the v2 static status ceiling is owned by
node_control_surface_read_results. Reexports introduce no wrapper implementation,
new module ownership, optional server dependency, signing or process bootstrap.
Existing clean-import and module-boundary tests remain governing checks.

Core #1910 also reexports the pure managed-update compiler. The export adds no
product dependency, permission, execution admission or provider interpretation.

Core #1826 exports the new pure health request/grant/verifier and result
contracts, profiles and derived byte limits from node_health_reads and
node_health_read_results. No SDK, persistence or provider import is introduced.

Core #1827 exports the pure node_health_transit grant/profile/digest/codecs,
verification result/predicate and derived bounds. It reuses the existing health
request language and adds no effects, provider imports or new health result.

Core #1832 reexports the pure management selection/transit values and their
strict codecs, the typed management diff value, and the health path selector.
Each export is its owner-defined identity. Selection adds no runtime authority,
provider client or deployment effect.

Core #1887 reexports the receiver identity/context values, their codecs and byte
limits, the receiver audience function, and explicit V2 configuration/selector.
Canonical ownership stays in receiver_identity and receiver_configuration; reused
historical wrapper types and constants keep their existing export owners.

Core #1888 reexports the explicit successor health request/workload grant,
gateway transit and result profiles, nominal digests, codecs and verification
results/predicates. Existing health kinds/outcomes/bounds retain their original
canonical owners. Imports introduce no signing, network, SDK or Operations edge.

Core #1889 reexports explicit V2 receiver surface-description request/grant and
V3 capabilities/status result contracts. Existing declaration versions, kinds,
coverage and bounds retain their original owners. Surface description remains
distinct from health and variable READ_STATE; no transit or route is introduced.
