Source: [receiver_identity.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/receiver_identity.py).
Maintain this companion with source and imported contract changes.

`NodeControlReceiverTarget` is the immutable tuple of workspace, runtime, node,
provider socket and logical receiver ID. The first four fields use existing
typed graph references; the ID is exactly 32 lowercase hexadecimal characters.
Core validates this value but does not allocate or retire identities.

`NodeControlAuthorityContext` carries the selected authored graph and realized
projection IDs separately. Its field names distinguish the roles without adding
a legacy graph-reference enum member. Changing context does not change an equal
receiver target. Neither value proves current authority, membership or delivery.
The accepted [lifecycle contract](../../../../design/0007-logical-receiver-lifecycle.md)
assigns those decisions to later owners.

Each closed codec reconstructs nominal values, emits canonical JSON, and accepts
bounded ordinary UTF-8 JSON while rejecting duplicate keys, nonfinite constants,
unknown fields and forged values. Exact canonical maxima are 635 bytes for the
target and 308 for context, derived from the existing 128-byte identifier bounds.
Raw input is bounded before parsing. Refusals are categorical, detached from
input exceptions, and do not include candidate material.

`receiver_node_control_audience(target)` retains `workload:node:socket` routing
text. Different workspace/runtime/receiver scopes can share that text; consumers
must independently verify the full target. Historical graph-bound targets are
not accepted by this API. Existing target codecs remain unchanged.

The target tests cover context changes with a stable receiver, each scope change,
nominal and forged inputs, exact byte limits, malformed JSON, and root exports.
This module has no persistence, signing, network, provider or credential effects.
