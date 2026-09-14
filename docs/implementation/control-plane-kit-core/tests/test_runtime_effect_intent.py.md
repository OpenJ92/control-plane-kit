Source: [control-plane-kit-core/tests/test_runtime_effect_intent.py](../../../../control-plane-kit-core/tests/test_runtime_effect_intent.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Intent identity law navigation

This file protects the pre-start projection and canonical identity implemented
by [runtime_effect_observation.py](../src/control_plane_kit_core/runtime_effect_observation.py.md).
The [relation note](../../../architecture/runtime-effect-request-intent-boundary.md)
is the reading order for interpreting the word “inverse” in test names.

The central law is that projecting a request reconstructed from an admitted
intent returns that intent. Reconstruction binds a supplied start-event identity;
it does not recover the original generated identity or transient grants.
Separate tests show that changing generated event coordinates leaves the
pre-start intent fingerprint unchanged, while changing retained intent
coordinates changes its identity.

Product fixtures matter: the material golden and the historical selected-only
descriptor are intentionally distinct. The latter is historical raw evidence,
not a presently valid typed intent to copy into a new fixture. Socket-order
permutations exercise canonical product material, not arbitrary list sorting.

RFC8785 goldens/domain separation, exact frozen nominal shapes, the 1 MiB
ceiling and selective conversion of categorical operation errors protect the
public boundary. These pure tests cannot establish provider-wire equality,
secret authorization, durable insertion or safe redispatch. No tests were run
as part of authoring this note.
