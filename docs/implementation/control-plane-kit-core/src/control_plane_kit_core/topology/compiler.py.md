Source: [compiler.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/topology/compiler.py).
Maintain this companion with source and imported contract changes.

Compilation copies the authored runtime's management selection into RuntimeRecord.
Gateway, connector and ingress must already exist in the supplied complete
topology. No implicit children, ingress, routes, credentials or permission are
inserted. Product instantiation preserves the transit role through BlockSpec;
canonical graph validation subsequently checks the relationship.
