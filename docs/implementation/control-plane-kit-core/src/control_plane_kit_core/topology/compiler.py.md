Source: [control-plane-kit-core/src/control_plane_kit_core/topology/compiler.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/topology/compiler.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Authoring tree to graph interpreter

compile_topology traverses the
[authoring algebra](../../../../../../control-plane-kit-core/src/control_plane_kit_core/algebra.py), materializes blocks
under their enclosing runtime and then applies collected connections. The
runtime record contains its direct block children; nested runtime expressions
are traversed separately. A connection may therefore refer to nodes collected
elsewhere before the connection pass.

The materialization protocol supplies implementation-specific graph data.
Calling a Python materialize method is not process isolation; conforming
implementations must preserve the pure boundary. This compiler does not itself
start containers, resolve secrets or contact providers. Metadata combines spec
and materialized values, so display metadata is not a trustworthy authority
source.

For each connection, both socket protocols must equal the selected protocol.
The provider endpoint becomes the consumer's environment assignment, tagged with
the producing edge ID. Socket-bound secret delivery becomes node delivery only
through a present connection. Runtime-control requirements carry no environment
slots and remain an explicit edge for later planning. Duplicate graph identities
and delivery/name collisions remain the [graph owner's](graph.py.md) checks.

Compilation returns a DeploymentGraph, not a ValidatedGraph or approved plan.
Missing required connections and other global laws still need
[validation](validation.py.md). compile_recipe is an alias of the same function.
The [kernel pipeline tests](../../../../../../control-plane-kit-core/tests/test_kernel_pipeline.py)
exercise these transformations using pure implementations; they do not prove
provider deployment or externally delivered environment values.
