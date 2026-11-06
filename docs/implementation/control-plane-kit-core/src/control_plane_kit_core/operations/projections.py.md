Source: [projections.py](../../../../../../control-plane-kit-core/src/control_plane_kit_core/operations/projections.py).
Maintain this companion alongside its source.

#2041 declares `workspaces` / `authorized-workspace-inventory`, paged to 100,
with no single target-workspace requirement. This is transport metadata;
Operations filters every row under the current exact durable read grant.

Adds the canonical workload-verifier-configuration kind and public-workload-verifier-configuration policy. The focused projection is unpaged and uses the existing bounded read contract. Operations owns key authority selection; this module owns only pure public metadata.

Receiver authoring context adds the nonpaged `receiver-authoring-context` kind
and `public-receiver-authoring-context` policy with its named response schema.
Operations owns exact selected-public-material disclosure and combined focused
scope enforcement; this declaration confers no mutation or key-selection power.
