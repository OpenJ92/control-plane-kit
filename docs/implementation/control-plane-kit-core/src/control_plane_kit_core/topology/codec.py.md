Source: [codec.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/topology/codec.py).
Maintain this companion with source and imported contract changes.

The canonical graph codec preserves optional runtime management and block transit
declarations by delegating nested representation to their value owner. Absent
fields are omitted; explicit null, unknown fields and unknown profiles cannot
silently normalize into accepted descriptors.

The shared graph validation path checks transit providers even without a selected
management path. For a present selection, the gateway and ingress must exist, the
connector and gateway must belong to the selecting runtime, and the ingress must
target that gateway's exact declared HTTP transit socket. Constructed graphs and
decoded graph values use the same resolver. Infrastructure is never synthesized.
Absent management remains valid for legacy graphs, including SDK declarations.

These pure checks establish authored relationships, not registered product or
interpreter support. Operations must refuse unsupported execution at admission;
canonical graph visibility alone is not authorization.
