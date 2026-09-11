Source: [control-plane-kit-core/src/control_plane_kit_core/topology/graph.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/topology/graph.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Compiled topology values

This owner holds nodes, edges, endpoint addresses and runtime records, plus
graph construction/update operations. add_node/add_edge/add_runtime reject
duplicate identities instead of overwriting; update_node requires an existing
identity. Ingress and delegation identities have their own duplicate checks.
These methods return replacement values, but nested caller mappings are not
deeply frozen or copied everywhere. Treat the outer frozen dataclass as a value
convention, not tamper-proof persistence.

Endpoint addresses distinguish retained literals from unresolved secret
references. Endpoint.url returns either the literal or the opaque reference
token; it does not resolve or probe either. LiteralAddress rejects parsed
passwords, not every possible sensitive string. Endpoint scope is descriptive.

Node enforces typed assignment/artifact/delivery families, duplicate artifact
identities/targets and cross-source environment-name uniqueness. The
[environment](../../../../../../control-plane-kit-core/src/control_plane_kit_core/environment.py),
[secret delivery](../../../../../../control-plane-kit-core/src/control_plane_kit_core/secrets.py) and
[runtime-authority](../../../../../../control-plane-kit-core/src/control_plane_kit_core/runtime_authority.py) owners define
the admitted components. Normalizing access-delivery values does not validate
a provider credential or grant permission to use an authority.

Connection material appends edge-derived environment and secret deliveries.
non_secret_environment returns stored public/socket strings, which can include
opaque references; it does not perform delivery. Graph completeness, socket
cardinality and consistent ownership are checked by
[validation.py](validation.py.md), beyond these local constructors.

DeploymentGraph.descriptor delegates to the authoritative
[codec](codec.py.md). Node/edge descriptors retain material such as literal
addresses, configuration content and reference identities; they are not public
redacted read projections. The node's base-spec descriptor is replaced by the
registered spec codec when encoding a whole graph. Do not use its direct
descriptor as a lossless codec for arbitrary BlockSpec extensions.

[test_topology_graph.py](../../../../../../control-plane-kit-core/tests/test_topology_graph.py)
protects duplicate-versus-update behavior and compiled identity rejection;
environment/configuration tests protect local collision laws. None proves
resource ownership or current runtime state.
