"""O2 values consume Core V2; they never synthesize durable permission."""

from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.node_control import NodeControlCanonicalization
from control_plane_kit_core.receiver_identity import (
    NodeControlAuthorityContext, NodeControlReceiverTarget, receiver_node_control_audience,
)
from control_plane_kit_core.receiver_health_reads import (
    ReceiverHealthReadRequest, DelegatedWorkloadReceiverHealthReadGrant,
    DelegatedWorkloadReceiverHealthReadGrantProfile,
)
from control_plane_kit_core.receiver_health_transit import (
    DelegatedGatewayReceiverHealthReadTransitGrant, DelegatedGatewayReceiverHealthReadTransitGrantProfile,
)
from tests.health_effect_preparation_fixture import HealthEffectPreparationFixture, wire_identity
from tests.health_receiver_trust_fixture import reference


def receiver_target(node, receiver_id, socket="http"):
    return NodeControlReceiverTarget(reference("workspace", "workspace-a"),
        reference("runtime", "docker"), reference("node", node),
        reference("provider-socket", socket), receiver_id)


class ReceiverHealthPreparationFixture(HealthEffectPreparationFixture):
    def receiver_material(self):
        # Existing Core constructors run before the missing Operations API
        # assertion. A failure here is apparatus/fixture, never R1 evidence.
        values = self.material()
        old = values["request"]
        target = receiver_target("api", "a" * 32)
        gateway = receiver_target("gateway", "b" * 32, "control")
        context = NodeControlAuthorityContext("health-desired", values["desired_realized_projection_id"])
        request = ReceiverHealthReadRequest(target, context, old.kind,
            old.declaration_identity, old.request_id)
        common = dict(canonicalization=NodeControlCanonicalization.JCS_RFC8785_V1,
            issuer="cpk-server", target=target, authority_context=context,
            kind=request.kind, declaration_identity=request.declaration_identity,
            request_id=request.request_id, request_digest=request.canonical_digest(),
            issued_at=1_700_000_000, not_before=1_700_000_000, expires_at=1_700_000_060)
        return dict(values, request=request,
            transit_grant=DelegatedGatewayReceiverHealthReadTransitGrant(
                profile=DelegatedGatewayReceiverHealthReadTransitGrantProfile.V2,
                purpose=DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT,
                key_id="health-transit", attempt_id=wire_identity(values["identity"]),
                gateway_target=gateway, jti="transit-jti-a", **common),
            workload_grant=DelegatedWorkloadReceiverHealthReadGrant(
                profile=DelegatedWorkloadReceiverHealthReadGrantProfile.V2,
                purpose=DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ,
                key_id="health-workload", audience=receiver_node_control_audience(target),
                jti="workload-jti-a", **common))

    def receiver_record(self, values=None):
        values = self.receiver_material() if values is None else values
        record_type = getattr(self.api(), "ReceiverHealthEffectPreparationRecord", None)
        self.assertTrue(callable(record_type), "#1883 missing ReceiverHealthEffectPreparationRecord")
        return record_type(**values)
