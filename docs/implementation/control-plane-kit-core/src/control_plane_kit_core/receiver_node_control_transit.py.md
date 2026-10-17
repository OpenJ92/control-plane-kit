Source: [receiver_node_control_transit.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/receiver_node_control_transit.py).
Maintain this companion with source and imported contract changes.

The V2 exact-command transit grant binds a separate gateway receiver target,
workload target, authority context, declaration, variable/operation, request ID,
idempotency key and full request digest. Gateway and workload share workspace
and runtime. Audience derives from gateway workspace/node; own receiver socket
is distinct from transit-route admission. No old workspace/revision/gateway-node
sibling fields survive.

The codec requires the received derived audience to agree, checks all closed
fields and canonical bytes, and reconstructs exact nominal values. The unchanged
caps are2834 bytes, audience265 and lifetime300. Shared command-family helpers
reuse semantic payload/JCS owners without importing health/surface private code.

The verifier validates independent local composition first. Incoming type,
missing/wrong purpose and invalid claims have explicit categorical precedence.
Valid claims check issuer/key/time/attempt, workspace/runtime, gateway receiver,
node/socket/receiver, context, declaration, variable, command and request. This
preserves command-family workspace-before-gateway ordering. Each scope dimension
checks local disagreement and then incoming disagreement before the next field.

No route admission, current authority, signature, forwarding, replay handling,
runtime effect or durable history is introduced. Gateway correlation does not
prove delivery or execution. Whole #1881 still gates downstream adoption.
