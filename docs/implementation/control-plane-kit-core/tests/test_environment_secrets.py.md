Source: [control-plane-kit-core/tests/test_environment_secrets.py](../../../../control-plane-kit-core/tests/test_environment_secrets.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Environment and secret-delivery law navigation

The three test groups join the local
[environment constructors](../src/control_plane_kit_core/environment.py.md),
[secret value/delivery contracts](../../../../control-plane-kit-core/src/control_plane_kit_core/secrets.py), and the
[graph owner](../../../../control-plane-kit-core/src/control_plane_kit_core/topology/graph.py). Start here when changing
assignment admission, reference-only descriptors, secret error disclosure or
name collisions across delivery sources.

`EnvironmentBindingTests` covers bounded public literals, secret-shaped names,
URL password rejection, exact decoder variants and node collision rules.
`SecretContractTests` exercises local reference resolution outcomes, explicit
value release, redacted failures and closed delivery/intention shapes.
`SecretDeliveryTopologyTests` follows delivery references through graph
round-tripping, explicit diffs and desired-node planning material.

`EnvironmentImplementation` and `MaterializedBlock` are pure local fixtures.
The local development resolver uses supplied in-memory values; no test here
establishes authenticated remote custody, actual container injection, protected
mount permissions or cleanup. Keep those assertions with their external owners.
This navigation note identifies existing tests; it is not a fresh suite result.
