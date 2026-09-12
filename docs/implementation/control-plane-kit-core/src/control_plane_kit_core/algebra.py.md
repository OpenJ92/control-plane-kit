Source: [algebra.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/algebra.py).
Maintain this companion with source and imported contract changes.

BlockSpec carries the same immutable control surface values consumed by product
instantiation and graph compilation. It preserves unique provider-socket identity
and NODE_CONTROLLABLE agreement. A surface that explicitly declares health reads
also requires HEALTH_CHECKABLE. Legacy health-checkable blocks without SDK
surfaces stay valid; no capability or runtime permission is inferred.

Socket existence/protocol validation remains in graph validation. Serializers
delegate surface encoding to its owner, so health-only and mixed declarations
survive graph round trips and participate in structural diff without a consumer
edge. These operations perform no runtime or data mutation.
