Source: [lifecycle.py](../../../../../control-plane-kit-operations/src/control_plane_kit_operations/lifecycle.py).
Maintain this companion alongside its source.

The private receiver-only check validates held current pins, immutable original scope, request identity and material. Generic fresh lifecycle operations compose it with their approval check. Retry/recovery reuse the receiver-only check alongside their existing approval owner, avoiding two incompatible policies. Replay/evidence-only transitions and L-before-execution lock order remain unchanged.

No schema, provider, public token or caller bypass is introduced. These changes
remain unvalidated until the released owning Operations Docker gate passes.
