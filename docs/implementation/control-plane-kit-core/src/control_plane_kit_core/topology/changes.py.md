Source: [control-plane-kit-core/src/control_plane_kit_core/topology/changes.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/topology/changes.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Structural change language

This file owns typed subjects, before/after values and the added/removed/
modified/unsupported/ambiguous sum. GraphDiff is deterministic structural data
when produced by the [diff interpreter](diff.py.md); it is not an execution
schedule. The summary counts change forms without deciding their safety or
executability.

Values retain richer typed material than every descriptor exposes. Environment
and edge assignments are redacted. SecretDeliveriesValue and runtime-authority
secret-reference entries add reference fingerprints while masking reference
identities. EndpointValue masks secret_ref without adding a fingerprint.
Metadata uses selected key-based filtering. Conversely, literal endpoints and
configuration content
remain visible, and socket-contract delivery descriptors use their owning
descriptor directly. Do not describe the entire GraphDiff descriptor as
uniformly public-safe or use its lossy projection to reconstruct original values.

RuntimeValue's descriptor is a selected summary; its retained runtime object
contains more material, including authority reference. NodeValue carries its
registered BlockSpec encoding. Public ingress and delegation values likewise
delegate portions of representation to their owners. Review each affected
variant when changing disclosure or approval presentation, rather than applying
one general redaction claim.

[test_graph_diff.py](../../../../../../control-plane-kit-core/tests/test_graph_diff.py)
protects the language/interpreter split, explicit subjects and selected
redaction cases. The [plan compiler](../../../../../../control-plane-kit-core/src/control_plane_kit_core/planning/compiler.py)
interprets changes into activities; unsupported/ambiguous values are explicit
inputs to review decisions, not permission to guess a migration.
