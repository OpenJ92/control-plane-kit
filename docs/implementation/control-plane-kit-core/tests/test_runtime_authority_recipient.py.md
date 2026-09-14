Source: [control-plane-kit-core/tests/test_runtime_authority_recipient.py](../../../../control-plane-kit-core/tests/test_runtime_authority_recipient.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Bind process delivery to the exact intended node

The same vectors exercise RuntimeEffectRequest and RuntimeEffectIntent.
StartNode and ReconcileNode must select the target product's exact delivery
declaration. Missing, foreign or extra product material, mismatched delivery
and absent/foreign authority are rejected. Runtime and teardown operations
cannot select process delivery; teardown may retain that declaration inside
historical product material while selecting no delivery.

A request without authority, deliveries or product material remains valid.
The observation wrapper retains the committed intent and its fingerprint;
removing a declaration changes that identity. These checks concern represented
permission and recipient correspondence, not provider execution.

Full 134-line file and fixtures read. The shared recipient validator in
[runtime_effects.py](../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effects.py),
product-material propagation and the
[intent/observation owner](../src/control_plane_kit_core/runtime_effect_observation.py.md)
were checked at their consequential boundaries; this is not a complete product
or request implementation audit. No secret delivery, container or mount occurs.
