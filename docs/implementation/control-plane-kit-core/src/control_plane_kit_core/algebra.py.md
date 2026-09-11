Source: [control-plane-kit-core/src/control_plane_kit_core/algebra.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/algebra.py).
Maintain this document alongside its source file. When the source or relevant imported contracts change, verify and update this companion in the same change.

# Deployment authoring language

This is the topology source language: application/data/proxy blocks, requirement
and provider sockets, connections and nested runtime contexts. A
`DeploymentTopology` names a tree rooted in a runtime context, with ingress and
delegation declarations. In the interpreter-oriented design, these explicit
values are the authoring syntax tree. They are compiled into a graph before
validation, diff and activity planning; constructing a DockerRuntime does not
launch Docker.

`RequirementSocket` distinguishes environment binding (at least one slot) from
runtime-control binding (no environment slots). It sorts typed secret deliveries
and rejects duplicate/colliding secret destinations. `BlockSockets` provides
named lookup but is not a complete graph validator. Protocol compatibility and
connection completeness belong to the
[compiler](../../../../../control-plane-kit-core/src/control_plane_kit_core/topology/compiler.py) and
[validator](../../../../../control-plane-kit-core/src/control_plane_kit_core/topology/validation.py).

`BlockSpec` requires its node-controllable capability to agree with the presence
of bounded, unique typed control surfaces. Those surface contracts come from
[node_control.py](../../../../../control-plane-kit-core/src/control_plane_kit_core/node_control.py); verification is an
explicit [VerificationContract](../../../../../control-plane-kit-core/src/control_plane_kit_core/verification.py).
Capabilities remain advertised powers, not behavior inferred from a block class.
The [lifecycle owner](../../../../../control-plane-kit-core/src/control_plane_kit_core/lifecycle.py) supplies default
owned-ephemeral and external-retained meanings; runtime authority is a reference
to an authority selected elsewhere.

`RuntimeImplementation.materialize` is the structural extension seam used by
compilation to obtain implementation-specific data. It is a Python protocol,
not a sandbox: an arbitrary implementation can contain code. Conforming
implementations must respect the pure compilation boundary, leaving provider
effects to runtime interpreters. Frozen outer dataclasses likewise do not make
all nested metadata dictionaries deeply immutable.

Delegation bindings are sorted and reject duplicate semantic identities.
`DeploymentRecipe` remains a compatibility alias of `DeploymentTopology`;
do not create a second language behind it. The
[kernel pipeline tests](../../../../../control-plane-kit-core/tests/test_kernel_pipeline.py) show
typed socket wiring and initial plans with pure fixtures. They demonstrate the
language transformation, not deployment success.
