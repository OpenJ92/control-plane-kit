"""Current public selection for reads and fresh retained-wrapper admission."""

from control_plane_kit_core.planning import ReconcileNode
from control_plane_kit_core.receiver_configuration import (
    ReceiverNodeControlConfigurationCodec, select_receiver_node_control_configuration_artifact,
)
from control_plane_kit_core.wrapper_configuration import NodeControlVerificationConfiguration, WrapperConfigurationError
from control_plane_kit_operations.delegation_signing_keys import (
    DelegationSigningKeyNotFound, RegisteredDelegationSigningKey, RegisteredDelegationSigningKeyStatus,
)
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile


class WorkloadVerifierAuthorityUnavailable(ValueError):
    """The exact requested public material is not current installation authority."""


_UNAVAILABLE = "workload verifier configuration is unavailable"


def current_workload_verifiers(store, workspace_id, purposes):
    """Read one bounded snapshot, holding ordered purpose locks in the caller UoW.

    Callers validate their purpose selector and authorize the operation. This
    returns public values only, never a signing reference or an authority lease.
    """
    try:
        selected = tuple((purpose, store.require_unambiguous_active(workspace_id, purpose))
            for purpose in sorted(purposes, key=lambda value: value.value))
        families = []
        for purpose, active in selected:
            if (not isinstance(active, RegisteredDelegationSigningKey)
                    or active.workspace_id != workspace_id or active.purpose is not purpose
                    or active.status is not RegisteredDelegationSigningKeyStatus.ACTIVE):
                raise WorkloadVerifierAuthorityUnavailable(_UNAVAILABLE)
            keys = store.list_for_verification(workspace_id, purpose, active.issuer, limit=17)
            if (not 1 <= len(keys) <= 16 or active not in keys or any(
                    not isinstance(value, RegisteredDelegationSigningKey)
                    or value.workspace_id != workspace_id or value.purpose is not purpose
                    or value.issuer != active.issuer
                    or value.status not in (RegisteredDelegationSigningKeyStatus.ACTIVE,
                        RegisteredDelegationSigningKeyStatus.VERIFY_ONLY)
                    for value in keys)):
                raise WorkloadVerifierAuthorityUnavailable(_UNAVAILABLE)
            families.append(NodeControlVerificationConfiguration(
                purpose, active.issuer, tuple(value.public_key for value in keys)))
    except (DelegationSigningKeyNotFound, WrapperConfigurationError):
        unavailable = True
    else:
        unavailable = False
    if unavailable:
        raise WorkloadVerifierAuthorityUnavailable(_UNAVAILABLE)
    return tuple(families)


def require_verifier_refresh_authority(stores, workspace_id, current, desired, plan, profile, *, activity=None):
    """Bind already canonical retained Reconcile material to current key truth.

    Core and registered-product admission own supported graph relations. This
    guard neither classifies a new relation nor authorizes effects. It is used
    only for fresh planning/start, never original replay or grant reload.
    """
    if profile is not PlanDerivationProfile.MANAGED_UPDATE_V1 or not plan.ready_for_execution:
        return
    for selected in plan.activities:
        if (type(selected.operation) is not ReconcileNode
                or (activity is not None and selected != activity)):
            continue
        node_id = selected.operation.target.node_id
        if node_id not in current.nodes or node_id not in desired.nodes:
            continue
        nodes = (current.node(node_id), desired.node(node_id))
        runtime = desired.runtimes[nodes[1].runtime_id]
        # Gateway route replacements belong to the existing add/remove family.
        if runtime.management is None or node_id == runtime.management.gateway_node_id:
            continue
        if nodes[0].configuration_artifacts == nodes[1].configuration_artifacts:
            continue
        configured = []
        for node in nodes:
            artifact = select_receiver_node_control_configuration_artifact(
                artifacts=node.configuration_artifacts,
                environment=node.public_environment + node.socket_environment,
                control_surfaces=node.block_spec.control_surfaces)
            configured.append(ReceiverNodeControlConfigurationCodec().decode_bytes(artifact.content.encode("utf-8")))
        before, after = configured
        if before.verifiers == after.verifiers:
            continue
        if after.target.workspace_id.value != workspace_id:
            raise WorkloadVerifierAuthorityUnavailable(_UNAVAILABLE)
        actual = current_workload_verifiers(stores.delegation_signing_keys, workspace_id,
            tuple(family.purpose for family in after.verifiers))
        if actual != after.verifiers:
            raise WorkloadVerifierAuthorityUnavailable(_UNAVAILABLE)
