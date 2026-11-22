Source: [validation.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/topology/validation.py).
Maintain this companion with source and imported contract changes.

The health management selector consumes a ValidatedGraph and one exact workload
node/provider socket/NodeHealthReadKind. It requires that kind on that particular
SDK surface, follows the workload's assigned runtime and returns the existing
selected NamedPublicIngress through the canonical relationship resolver. Another
socket's health kind does not satisfy the request. Readiness never downgrades to
liveness, and a non-health operation is rejected. No endpoint registry, node,
route, permission, credential or transport is created.

Invalid ValidatedGraph inputs receive a fixed categorical management error;
the selector does not expose the graph's arbitrary display label through the
general GraphValidationError message. The target test uses a long canary label.
