Source: [runtime_effects.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effects.py).
Maintain this companion with source and imported contract changes.

Runtime effect request/material/result values describe the pure execution
boundary. `RuntimeProductMaterial` must contain a requirement-free concrete
product; construction and descriptor decoding reject unresolved configuration
requirements. Its reference still identifies the original registered document,
while its product may contain selected instance payloads. Product identity must
match, but materialized descriptor digest need not equal the registered digest.

This guard does not prove selection or registration. Operations #2012 must match
the registered required/concrete slot union before discharging requirements,
preserve reference/image and retain historical no-fresh-resolution cleanup.
No effect dispatch, secret selection, history, retry or provider behavior changes.
