Source: [ingress_authority_store.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/ingress_authority_store.py).
Maintain this companion alongside its source.

Configuration preparation selects the activity's exact ingress authority,
highest-epoch active resource and generated token-reference metadata/custody
through bounded owner reads. Existing decoders retain scope and source laws.
Only opaque references enter material; resolved token values are not exposed,
persisted or logged by this path.
