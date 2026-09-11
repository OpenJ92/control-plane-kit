# Core implementation companion coverage

Scope: tracked `control-plane-kit-core/**` paths at base
`087a89253b14b3bb438af9042ea779976886a79e`, inventoried for
[#1801](https://github.com/OpenJ92/control-plane-kit/issues/1801), under
[#1799](https://github.com/OpenJ92/control-plane-kit/issues/1799).
This is a rollout index, not a companion to a source file or a recurring
per-file freshness ledger. The historical package-module inventory is not this
scope's source of truth.

167 tracked paths: 102 pending, 0 authored,
57 reviewed, 8 excluded.
“Authored” means the note exists after author source inspection. It does not
claim peer approval or fresh executable validation. Peer-review depth and source
coordinates belong in the batch PR. The first calibration is not completion
of Core coverage.

Calibration review: North read both implementation owners and their notes in
full, checked consequential contracts/imports and sampled governing test
navigation. Meridian separately checked the Operations information/authority
boundary. These are source reviews, not a new executable suite result or an
exhaustive review of every test assertion. Review dispositions are recorded in
the calibration PR. North additionally read all seven foundation source
owners and their twelve companions, with targeted identity/lifecycle/protocol
test sampling. North then reviewed all sixteen configuration/verification and
harness/navigation notes, directly checking the configuration/renderer/capability
owners and full shell harness, with targeted verification/source-test checks.
This does not claim a full verification-file audit. No executable validation
was added. North reviewed the twelve graph notes, reading the full compiler,
checking graph construction, codec/validation/diff and disclosure contracts,
and sampling relevant test bodies. The secret-reference fingerprint wording
was corrected against the exact descriptor variants before publication.

North reviewed the next eleven planning/policy notes: all five substantive
source owners and the planning facade in full, both policy/approval-subject
test files in full, and selected activity/codec/compiler assertions. The
missing start-draft compiler lookup was independently confirmed from source;
no executed reproducer or live-impact claim is made. The separate follow-up
is not part of coverage completion or the held deployment diagnosis.

Existing Markdown is maintained as its own prose/agent contract and is excluded
from recursive mirroring. Authored fixtures, test policy JSON, package metadata,
exports and test harnesses remain included. No generated/vendor/lock files
occur in this tracked scope. New or removed files require an inventory update
during rollout.

The shared [request/intent relation](../../architecture/runtime-effect-request-intent-boundary.md)
and Core AGENTS maintenance change are additional documentation deliverables.
The known historical-inventory/test discrepancy is described in the
[boundary test companion](tests/test_runtime_effect_observation_boundary.py.md);
its resolution is separate from coverage status.

| Tracked source | Kind | Coverage | Note | Disposition |
| --- | --- | --- | --- | --- |
| [control-plane-kit-core/AGENTS.md](../../../control-plane-kit-core/AGENTS.md) | existing prose / agent instructions | excluded | — | Maintained at the linked source; no recursive prose companion. |
| [control-plane-kit-core/README.md](../../../control-plane-kit-core/README.md) | existing prose / agent instructions | excluded | — | Maintained at the linked source; no recursive prose companion. |
| [control-plane-kit-core/docs/EXTRACTION.md](../../../control-plane-kit-core/docs/EXTRACTION.md) | existing prose / agent instructions | excluded | — | Maintained at the linked source; no recursive prose companion. |
| [control-plane-kit-core/docs/EXTRACT_D_TOPOLOGY.md](../../../control-plane-kit-core/docs/EXTRACT_D_TOPOLOGY.md) | existing prose / agent instructions | excluded | — | Maintained at the linked source; no recursive prose companion. |
| [control-plane-kit-core/docs/NODE_CONTROL_CANONICAL_WIRE.md](../../../control-plane-kit-core/docs/NODE_CONTROL_CANONICAL_WIRE.md) | existing prose / agent instructions | excluded | — | Maintained at the linked source; no recursive prose companion. |
| [control-plane-kit-core/docs/NODE_CONTROL_PUBLIC_MATERIAL.md](../../../control-plane-kit-core/docs/NODE_CONTROL_PUBLIC_MATERIAL.md) | existing prose / agent instructions | excluded | — | Maintained at the linked source; no recursive prose companion. |
| [control-plane-kit-core/docs/NODE_CONTROL_TOPOLOGY.md](../../../control-plane-kit-core/docs/NODE_CONTROL_TOPOLOGY.md) | existing prose / agent instructions | excluded | — | Maintained at the linked source; no recursive prose companion. |
| [control-plane-kit-core/examples/external-product-descriptor.md](../../../control-plane-kit-core/examples/external-product-descriptor.md) | existing prose / agent instructions | excluded | — | Maintained at the linked source; no recursive prose companion. |
| [control-plane-kit-core/pyproject.toml](../../../control-plane-kit-core/pyproject.toml) | build / dependencies | reviewed | [companion](pyproject.toml.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/__init__.py](../../../control-plane-kit-core/src/control_plane_kit_core/__init__.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/_activity_identity.py](../../../control-plane-kit-core/src/control_plane_kit_core/_activity_identity.py) | source | reviewed | [companion](src/control_plane_kit_core/_activity_identity.py.md) | North: seven owners read; governing tests sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/_node_control_public_wire.py](../../../control-plane-kit-core/src/control_plane_kit_core/_node_control_public_wire.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/_run_identity.py](../../../control-plane-kit-core/src/control_plane_kit_core/_run_identity.py) | source | reviewed | [companion](src/control_plane_kit_core/_run_identity.py.md) | North: seven owners read; governing tests sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/algebra.py](../../../control-plane-kit-core/src/control_plane_kit_core/algebra.py) | source | reviewed | [companion](src/control_plane_kit_core/algebra.py.md) | North: seven owners read; governing tests sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/approval_subjects.py](../../../control-plane-kit-core/src/control_plane_kit_core/approval_subjects.py) | source | reviewed | [companion](src/control_plane_kit_core/approval_subjects.py.md) | North: owners read fully; planning tests sampled, policy tests read fully. |
| [control-plane-kit-core/src/control_plane_kit_core/capabilities.py](../../../control-plane-kit-core/src/control_plane_kit_core/capabilities.py) | source | reviewed | [companion](src/control_plane_kit_core/capabilities.py.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/configuration.py](../../../control-plane-kit-core/src/control_plane_kit_core/configuration.py) | source | reviewed | [companion](src/control_plane_kit_core/configuration.py.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/configuration_rendering.py](../../../control-plane-kit-core/src/control_plane_kit_core/configuration_rendering.py) | source | reviewed | [companion](src/control_plane_kit_core/configuration_rendering.py.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/control_contracts.py](../../../control-plane-kit-core/src/control_plane_kit_core/control_contracts.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/control_routes.py](../../../control-plane-kit-core/src/control_plane_kit_core/control_routes.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/delegation_authority.py](../../../control-plane-kit-core/src/control_plane_kit_core/delegation_authority.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/delegation_keys.py](../../../control-plane-kit-core/src/control_plane_kit_core/delegation_keys.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/environment.py](../../../control-plane-kit-core/src/control_plane_kit_core/environment.py) | source | reviewed | [companion](src/control_plane_kit_core/environment.py.md) | North: source claims checked; test navigation sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/gateway_delegation.py](../../../control-plane-kit-core/src/control_plane_kit_core/gateway_delegation.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/identity.py](../../../control-plane-kit-core/src/control_plane_kit_core/identity.py) | source | reviewed | [companion](src/control_plane_kit_core/identity.py.md) | North: seven owners read; governing tests sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/lifecycle.py](../../../control-plane-kit-core/src/control_plane_kit_core/lifecycle.py) | source | reviewed | [companion](src/control_plane_kit_core/lifecycle.py.md) | North: seven owners read; governing tests sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/node_control.py](../../../control-plane-kit-core/src/control_plane_kit_core/node_control.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/node_control_surface_read_results.py](../../../control-plane-kit-core/src/control_plane_kit_core/node_control_surface_read_results.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/node_control_surface_reads.py](../../../control-plane-kit-core/src/control_plane_kit_core/node_control_surface_reads.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/node_control_transit.py](../../../control-plane-kit-core/src/control_plane_kit_core/node_control_transit.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/__init__.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/__init__.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/commands.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/commands.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/compensation.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/compensation.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/execution.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/execution.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/handoff.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/handoff.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/http.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/http.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/lifecycle.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/lifecycle.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/mcp.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/mcp.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/parity.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/parity.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/persistence.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/persistence.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/process.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/process.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/projections.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/projections.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/recovery.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/recovery.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/run_identity.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/run_identity.py) | source | reviewed | [companion](src/control_plane_kit_core/operations/run_identity.py.md) | North: seven owners read; governing tests sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/services.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/services.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/operations/transactions.py](../../../control-plane-kit-core/src/control_plane_kit_core/operations/transactions.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/planning/__init__.py](../../../control-plane-kit-core/src/control_plane_kit_core/planning/__init__.py) | source | reviewed | [companion](src/control_plane_kit_core/planning/__init__.py.md) | North: owners read fully; planning tests sampled, policy tests read fully. |
| [control-plane-kit-core/src/control_plane_kit_core/planning/activity_plan.py](../../../control-plane-kit-core/src/control_plane_kit_core/planning/activity_plan.py) | source | reviewed | [companion](src/control_plane_kit_core/planning/activity_plan.py.md) | North: owners read fully; planning tests sampled, policy tests read fully. |
| [control-plane-kit-core/src/control_plane_kit_core/planning/codec.py](../../../control-plane-kit-core/src/control_plane_kit_core/planning/codec.py) | source | reviewed | [companion](src/control_plane_kit_core/planning/codec.py.md) | North: owners read fully; planning tests sampled, policy tests read fully. |
| [control-plane-kit-core/src/control_plane_kit_core/planning/compiler.py](../../../control-plane-kit-core/src/control_plane_kit_core/planning/compiler.py) | source | reviewed | [companion](src/control_plane_kit_core/planning/compiler.py.md) | North: owners read fully; planning tests sampled, policy tests read fully. |
| [control-plane-kit-core/src/control_plane_kit_core/planning/recovery.py](../../../control-plane-kit-core/src/control_plane_kit_core/planning/recovery.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/planning/saga.py](../../../control-plane-kit-core/src/control_plane_kit_core/planning/saga.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/planning/scenarios.py](../../../control-plane-kit-core/src/control_plane_kit_core/planning/scenarios.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/policies.py](../../../control-plane-kit-core/src/control_plane_kit_core/policies.py) | source | reviewed | [companion](src/control_plane_kit_core/policies.py.md) | North: owners read fully; planning tests sampled, policy tests read fully. |
| [control-plane-kit-core/src/control_plane_kit_core/probe_intents.py](../../../control-plane-kit-core/src/control_plane_kit_core/probe_intents.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/products.py](../../../control-plane-kit-core/src/control_plane_kit_core/products.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/public_ingress.py](../../../control-plane-kit-core/src/control_plane_kit_core/public_ingress.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/runtime_authority.py](../../../control-plane-kit-core/src/control_plane_kit_core/runtime_authority.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/runtime_effect_observation.py](../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effect_observation.py) | source | reviewed | [companion](src/control_plane_kit_core/runtime_effect_observation.py.md) | North: source claims checked; test navigation sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/runtime_effects.py](../../../control-plane-kit-core/src/control_plane_kit_core/runtime_effects.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/secrets.py](../../../control-plane-kit-core/src/control_plane_kit_core/secrets.py) | source | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/src/control_plane_kit_core/topology/__init__.py](../../../control-plane-kit-core/src/control_plane_kit_core/topology/__init__.py) | source | reviewed | [companion](src/control_plane_kit_core/topology/__init__.py.md) | North: consequential graph contracts checked; test bodies sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/topology/changes.py](../../../control-plane-kit-core/src/control_plane_kit_core/topology/changes.py) | source | reviewed | [companion](src/control_plane_kit_core/topology/changes.py.md) | North: consequential graph contracts checked; test bodies sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/topology/codec.py](../../../control-plane-kit-core/src/control_plane_kit_core/topology/codec.py) | source | reviewed | [companion](src/control_plane_kit_core/topology/codec.py.md) | North: consequential graph contracts checked; test bodies sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/topology/compiler.py](../../../control-plane-kit-core/src/control_plane_kit_core/topology/compiler.py) | source | reviewed | [companion](src/control_plane_kit_core/topology/compiler.py.md) | North: consequential graph contracts checked; test bodies sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/topology/diff.py](../../../control-plane-kit-core/src/control_plane_kit_core/topology/diff.py) | source | reviewed | [companion](src/control_plane_kit_core/topology/diff.py.md) | North: consequential graph contracts checked; test bodies sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/topology/graph.py](../../../control-plane-kit-core/src/control_plane_kit_core/topology/graph.py) | source | reviewed | [companion](src/control_plane_kit_core/topology/graph.py.md) | North: consequential graph contracts checked; test bodies sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/topology/validation.py](../../../control-plane-kit-core/src/control_plane_kit_core/topology/validation.py) | source | reviewed | [companion](src/control_plane_kit_core/topology/validation.py.md) | North: consequential graph contracts checked; test bodies sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/types.py](../../../control-plane-kit-core/src/control_plane_kit_core/types.py) | source | reviewed | [companion](src/control_plane_kit_core/types.py.md) | North: seven owners read; governing tests sampled. |
| [control-plane-kit-core/src/control_plane_kit_core/verification.py](../../../control-plane-kit-core/src/control_plane_kit_core/verification.py) | source | reviewed | [companion](src/control_plane_kit_core/verification.py.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/test.sh](../../../control-plane-kit-core/test.sh) | suite harness | reviewed | [companion](test.sh.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/tests/approved_skips.json](../../../control-plane-kit-core/tests/approved_skips.json) | test policy data | reviewed | [companion](tests/approved_skips.json.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/tests/contract_security_assertions.py](../../../control-plane-kit-core/tests/contract_security_assertions.py) | test / assertion support | reviewed | [companion](tests/contract_security_assertions.py.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/tests/fixtures/external-products/proxy/product.cpk.json](../../../control-plane-kit-core/tests/fixtures/external-products/proxy/product.cpk.json) | authored fixture | reviewed | [companion](tests/fixtures/external-products/proxy/product.cpk.json.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/tests/fixtures/node_authority_empty_graph.json](../../../control-plane-kit-core/tests/fixtures/node_authority_empty_graph.json) | authored fixture | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/fixtures/node_control_canonical_wire_v1.json](../../../control-plane-kit-core/tests/fixtures/node_control_canonical_wire_v1.json) | authored fixture | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/fixtures/node_control_public_material_v1.json](../../../control-plane-kit-core/tests/fixtures/node_control_public_material_v1.json) | authored fixture | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/fixtures/node_control_surface_read_canonical_wire_v1.json](../../../control-plane-kit-core/tests/fixtures/node_control_surface_read_canonical_wire_v1.json) | authored fixture | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/fixtures/node_control_transit_canonical_wire_v1.json](../../../control-plane-kit-core/tests/fixtures/node_control_transit_canonical_wire_v1.json) | authored fixture | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_activity_identity.py](../../../control-plane-kit-core/tests/test_activity_identity.py) | test / assertion support | reviewed | [companion](tests/test_activity_identity.py.md) | North: seven owners read; governing tests sampled. |
| [control-plane-kit-core/tests/test_activity_plan.py](../../../control-plane-kit-core/tests/test_activity_plan.py) | test / assertion support | reviewed | [companion](tests/test_activity_plan.py.md) | North: owners read fully; planning tests sampled, policy tests read fully. |
| [control-plane-kit-core/tests/test_activity_plan_codec.py](../../../control-plane-kit-core/tests/test_activity_plan_codec.py) | test / assertion support | reviewed | [companion](tests/test_activity_plan_codec.py.md) | North: owners read fully; planning tests sampled, policy tests read fully. |
| [control-plane-kit-core/tests/test_activity_plan_compiler.py](../../../control-plane-kit-core/tests/test_activity_plan_compiler.py) | test / assertion support | reviewed | [companion](tests/test_activity_plan_compiler.py.md) | North: owners read fully; planning tests sampled, policy tests read fully. |
| [control-plane-kit-core/tests/test_adapter_parity_contract.py](../../../control-plane-kit-core/tests/test_adapter_parity_contract.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_approval_subjects.py](../../../control-plane-kit-core/tests/test_approval_subjects.py) | test / assertion support | reviewed | [companion](tests/test_approval_subjects.py.md) | North: owners read fully; planning tests sampled, policy tests read fully. |
| [control-plane-kit-core/tests/test_authorization_history_parity_contract.py](../../../control-plane-kit-core/tests/test_authorization_history_parity_contract.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_command_parity_contract.py](../../../control-plane-kit-core/tests/test_command_parity_contract.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_command_workflow_contract.py](../../../control-plane-kit-core/tests/test_command_workflow_contract.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_compensation_planning.py](../../../control-plane-kit-core/tests/test_compensation_planning.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_configuration_artifacts.py](../../../control-plane-kit-core/tests/test_configuration_artifacts.py) | test / assertion support | reviewed | [companion](tests/test_configuration_artifacts.py.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/tests/test_container_server_product.py](../../../control-plane-kit-core/tests/test_container_server_product.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_control_contracts.py](../../../control-plane-kit-core/tests/test_control_contracts.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_control_routes.py](../../../control-plane-kit-core/tests/test_control_routes.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_cpk_server_entrypoint_handoff.py](../../../control-plane-kit-core/tests/test_cpk_server_entrypoint_handoff.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_delegation_authority_projection.py](../../../control-plane-kit-core/tests/test_delegation_authority_projection.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_delegation_keys.py](../../../control-plane-kit-core/tests/test_delegation_keys.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_deployment_program_boundary.py](../../../control-plane-kit-core/tests/test_deployment_program_boundary.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_draft_catalogue_contract.py](../../../control-plane-kit-core/tests/test_draft_catalogue_contract.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_draft_selection_contract.py](../../../control-plane-kit-core/tests/test_draft_selection_contract.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_effect_recovery_contract.py](../../../control-plane-kit-core/tests/test_effect_recovery_contract.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_environment_secrets.py](../../../control-plane-kit-core/tests/test_environment_secrets.py) | test / assertion support | reviewed | [companion](tests/test_environment_secrets.py.md) | North: source claims checked; test navigation sampled. |
| [control-plane-kit-core/tests/test_execution_coordinator_contract.py](../../../control-plane-kit-core/tests/test_execution_coordinator_contract.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_execution_lifecycle_contract.py](../../../control-plane-kit-core/tests/test_execution_lifecycle_contract.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_external_product_fixture.py](../../../control-plane-kit-core/tests/test_external_product_fixture.py) | test / assertion support | reviewed | [companion](tests/test_external_product_fixture.py.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/tests/test_extract_d_closeout.py](../../../control-plane-kit-core/tests/test_extract_d_closeout.py) | test / assertion support | reviewed | [companion](tests/test_extract_d_closeout.py.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/tests/test_extract_d_topology.py](../../../control-plane-kit-core/tests/test_extract_d_topology.py) | test / assertion support | reviewed | [companion](tests/test_extract_d_topology.py.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/tests/test_failed_run_compensation_contract.py](../../../control-plane-kit-core/tests/test_failed_run_compensation_contract.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_gateway_delegation.py](../../../control-plane-kit-core/tests/test_gateway_delegation.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_graph_codec.py](../../../control-plane-kit-core/tests/test_graph_codec.py) | test / assertion support | reviewed | [companion](tests/test_graph_codec.py.md) | North: consequential graph contracts checked; test bodies sampled. |
| [control-plane-kit-core/tests/test_graph_diff.py](../../../control-plane-kit-core/tests/test_graph_diff.py) | test / assertion support | reviewed | [companion](tests/test_graph_diff.py.md) | North: consequential graph contracts checked; test bodies sampled. |
| [control-plane-kit-core/tests/test_graph_validation.py](../../../control-plane-kit-core/tests/test_graph_validation.py) | test / assertion support | reviewed | [companion](tests/test_graph_validation.py.md) | North: consequential graph contracts checked; test bodies sampled. |
| [control-plane-kit-core/tests/test_http_api_contract.py](../../../control-plane-kit-core/tests/test_http_api_contract.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_identity.py](../../../control-plane-kit-core/tests/test_identity.py) | test / assertion support | reviewed | [companion](tests/test_identity.py.md) | North: seven owners read; governing tests sampled. |
| [control-plane-kit-core/tests/test_kernel_pipeline.py](../../../control-plane-kit-core/tests/test_kernel_pipeline.py) | test / assertion support | reviewed | [companion](tests/test_kernel_pipeline.py.md) | North: consequential graph contracts checked; test bodies sampled. |
| [control-plane-kit-core/tests/test_mcp_streamable_http_contract.py](../../../control-plane-kit-core/tests/test_mcp_streamable_http_contract.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_milestone_closeout.py](../../../control-plane-kit-core/tests/test_milestone_closeout.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_node_authority_delivery.py](../../../control-plane-kit-core/tests/test_node_authority_delivery.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_node_control.py](../../../control-plane-kit-core/tests/test_node_control.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_node_control_canonical_wire.py](../../../control-plane-kit-core/tests/test_node_control_canonical_wire.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_node_control_graph_references.py](../../../control-plane-kit-core/tests/test_node_control_graph_references.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_node_control_operation_contracts.py](../../../control-plane-kit-core/tests/test_node_control_operation_contracts.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_node_control_public_material.py](../../../control-plane-kit-core/tests/test_node_control_public_material.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_node_control_public_wire_ownership.py](../../../control-plane-kit-core/tests/test_node_control_public_wire_ownership.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_node_control_result_variants.py](../../../control-plane-kit-core/tests/test_node_control_result_variants.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_node_control_surface_read_authority.py](../../../control-plane-kit-core/tests/test_node_control_surface_read_authority.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_node_control_surface_read_results.py](../../../control-plane-kit-core/tests/test_node_control_surface_read_results.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_node_control_surfaces.py](../../../control-plane-kit-core/tests/test_node_control_surfaces.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_node_control_topology.py](../../../control-plane-kit-core/tests/test_node_control_topology.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_node_control_transit.py](../../../control-plane-kit-core/tests/test_node_control_transit.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_node_control_workload_wire.py](../../../control-plane-kit-core/tests/test_node_control_workload_wire.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_observation_connection_admission.py](../../../control-plane-kit-core/tests/test_observation_connection_admission.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_oci_image_reference.py](../../../control-plane-kit-core/tests/test_oci_image_reference.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_package_boundary.py](../../../control-plane-kit-core/tests/test_package_boundary.py) | test / assertion support | reviewed | [companion](tests/test_package_boundary.py.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/tests/test_persistence_boundary_contract.py](../../../control-plane-kit-core/tests/test_persistence_boundary_contract.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_planning_scenarios.py](../../../control-plane-kit-core/tests/test_planning_scenarios.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_policies.py](../../../control-plane-kit-core/tests/test_policies.py) | test / assertion support | reviewed | [companion](tests/test_policies.py.md) | North: owners read fully; planning tests sampled, policy tests read fully. |
| [control-plane-kit-core/tests/test_probe_intents.py](../../../control-plane-kit-core/tests/test_probe_intents.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_process_operational_contract.py](../../../control-plane-kit-core/tests/test_process_operational_contract.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_product_catalog.py](../../../control-plane-kit-core/tests/test_product_catalog.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_product_descriptor.py](../../../control-plane-kit-core/tests/test_product_descriptor.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_product_descriptor_hardening.py](../../../control-plane-kit-core/tests/test_product_descriptor_hardening.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_product_identity.py](../../../control-plane-kit-core/tests/test_product_identity.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_product_instantiation.py](../../../control-plane-kit-core/tests/test_product_instantiation.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_product_pipeline_propagation.py](../../../control-plane-kit-core/tests/test_product_pipeline_propagation.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_product_reference.py](../../../control-plane-kit-core/tests/test_product_reference.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_product_runtime_contract.py](../../../control-plane-kit-core/tests/test_product_runtime_contract.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_protocol.py](../../../control-plane-kit-core/tests/test_protocol.py) | test / assertion support | reviewed | [companion](tests/test_protocol.py.md) | North: seven owners read; governing tests sampled. |
| [control-plane-kit-core/tests/test_public_ingress.py](../../../control-plane-kit-core/tests/test_public_ingress.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_read_projection_contract.py](../../../control-plane-kit-core/tests/test_read_projection_contract.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_recovery_planning.py](../../../control-plane-kit-core/tests/test_recovery_planning.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_resource_lifecycle.py](../../../control-plane-kit-core/tests/test_resource_lifecycle.py) | test / assertion support | reviewed | [companion](tests/test_resource_lifecycle.py.md) | North: seven owners read; governing tests sampled. |
| [control-plane-kit-core/tests/test_revision_history_contract.py](../../../control-plane-kit-core/tests/test_revision_history_contract.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_run_identity.py](../../../control-plane-kit-core/tests/test_run_identity.py) | test / assertion support | reviewed | [companion](tests/test_run_identity.py.md) | North: seven owners read; governing tests sampled. |
| [control-plane-kit-core/tests/test_runtime_authority_recipient.py](../../../control-plane-kit-core/tests/test_runtime_authority_recipient.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_runtime_connection_admission.py](../../../control-plane-kit-core/tests/test_runtime_connection_admission.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_runtime_effect_intent.py](../../../control-plane-kit-core/tests/test_runtime_effect_intent.py) | test / assertion support | reviewed | [companion](tests/test_runtime_effect_intent.py.md) | North: source claims checked; test navigation sampled. |
| [control-plane-kit-core/tests/test_runtime_effect_observation.py](../../../control-plane-kit-core/tests/test_runtime_effect_observation.py) | test / assertion support | reviewed | [companion](tests/test_runtime_effect_observation.py.md) | North: source claims checked; test navigation sampled. |
| [control-plane-kit-core/tests/test_runtime_effect_observation_boundary.py](../../../control-plane-kit-core/tests/test_runtime_effect_observation_boundary.py) | test / assertion support | reviewed | [companion](tests/test_runtime_effect_observation_boundary.py.md) | North: source claims checked; test navigation sampled. |
| [control-plane-kit-core/tests/test_runtime_effects.py](../../../control-plane-kit-core/tests/test_runtime_effects.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_saga.py](../../../control-plane-kit-core/tests/test_saga.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_scaffold.py](../../../control-plane-kit-core/tests/test_scaffold.py) | test / assertion support | reviewed | [companion](tests/test_scaffold.py.md) | North: consequential claims checked; navigation sampled. |
| [control-plane-kit-core/tests/test_scheduling.py](../../../control-plane-kit-core/tests/test_scheduling.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_secret_provider_contract.py](../../../control-plane-kit-core/tests/test_secret_provider_contract.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_temporal_history_read_contract.py](../../../control-plane-kit-core/tests/test_temporal_history_read_contract.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_topology_graph.py](../../../control-plane-kit-core/tests/test_topology_graph.py) | test / assertion support | reviewed | [companion](tests/test_topology_graph.py.md) | North: consequential graph contracts checked; test bodies sampled. |
| [control-plane-kit-core/tests/test_unit_of_work_boundary.py](../../../control-plane-kit-core/tests/test_unit_of_work_boundary.py) | test / assertion support | pending | — | Initial authoring backlog. |
| [control-plane-kit-core/tests/test_verification_capabilities.py](../../../control-plane-kit-core/tests/test_verification_capabilities.py) | test / assertion support | reviewed | [companion](tests/test_verification_capabilities.py.md) | North: consequential claims checked; navigation sampled. |
