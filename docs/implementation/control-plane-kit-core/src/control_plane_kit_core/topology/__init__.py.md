Source: [__init__.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/topology/__init__.py).
Maintain this companion with source and imported contract changes.

The topology entrance reexports RuntimeManagementValue from changes and
management_ingress_for_health_read from validation. These are the exact owner
identities, not wrapper implementations. The selection/transit language remains
owned below topology in runtime_management; no Operations or provider dependency
is introduced.
