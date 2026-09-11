Source: [control-plane-kit-core/src/control_plane_kit_core/environment.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/environment.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Environment binding values

This file owns the closed public-static and socket-derived assignment values and
their descriptor decoder. It describes process inputs; it neither writes a
process environment nor resolves a secret.

## Constructor contract before consumer assumptions

`PublicStaticEnvironmentBinding` admits uppercase identifier names, bounded
string values (16 KiB of UTF-8), and no NUL. Its name check rejects substrings
such as `secret`, `token`, and `password` even when the supplied value is an
ordinary path or public setting. A variable named `CPK_SECRETS_DATABASE_PATH`
therefore fails this constructor. Inspect the selected product/image contract
before supplying overrides; the apparent harmlessness of a literal does not
create an exception.

`SocketDerivedEnvironmentBinding` instead retains a nonempty `edge_id` and
requires a nonblank, NUL-free value. Do not transfer the public-static size and
secret-name restrictions to this variant: its constructor does not apply them.
Both variants reject parsed URL passwords; socket values may carry opaque
`secret://` references, whereas public-static values may not. These checks are
a specific rejection policy, not a general secret detector. The caller remains
responsible for supplying public material.

The decoder admits only the two declared variants and their exact field sets.
Adding an assignment category requires an explicit language/codec decision;
open dictionaries must not become an escape hatch.

## Related owners

There are no CPK imports in this module. Selected consumers and adjacent
contracts that a change must inspect are:

- [secrets.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/secrets.py):
  `SecretEnvironmentDelivery` describes resolving a reference for an explicit
  use; `SecretReferenceEnvironmentDelivery` carries the reference itself.
  `SecretFileDelivery.path_binding` uses `SecretFilePathBinding` to expose a
  mounted file's path without treating that path as the secret value. These
  are distinct delivery meanings, not interchangeable ways to bypass validation.
- [topology/graph.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/topology/graph.py):
  `Node` rejects name collisions across public, socket and secret delivery
  sources, including a secret file's path binding.
- [topology/validation.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/topology/validation.py):
  graph validation checks socket-derived assignments against edge provenance.
  Successful construction alone does not establish graph consistency.

These consumer links are non-exhaustive. Search source and verify the version
selected by an external product before changing or relying on these contracts.

## Evidence and motivation

[test_environment_secrets.py](../../../../../control-plane-kit-core/tests/test_environment_secrets.py)
protects constructor rejection, closed descriptors, non-disclosing errors and
cross-source name collisions, then follows reference-only deliveries through
graph codec/diff/planning. Those are pure fixture laws, not proof of environment
injection or mounted-file behavior in a running container.

The separation of explicit public values from reference-bearing deliveries is
visible in source and tests. This note records that implemented boundary; it
does not claim a recorded rationale for every substring restriction.
