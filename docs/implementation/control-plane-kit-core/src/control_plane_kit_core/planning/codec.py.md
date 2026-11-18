Source: [codec.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/codec.py).
Maintain this companion with source and imported contract changes.

New management observation operations use additive distinct v1 discriminants
through the shared bounded observation codec. Existing v1 bytes, compensation,
unknown-kind rejection and legacy operation meaning remain unchanged. New readers
read old plans; old readers refuse the new variants. There is no silent upgrade.

ManagementObservationError escapes the outer plan decoder without the legacy
ValueError chaining wrapper, so new nested observation failures remain bounded,
categorical and candidate-context-free at the public plan boundary. Other legacy
error-policy behavior is unchanged. This codec neither accepts runtime results
nor introduces a persistence/transport capability.
