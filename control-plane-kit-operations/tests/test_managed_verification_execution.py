"""Configured route and supplied-grant congruence, never dispatch evidence."""
from dataclasses import replace
import json
import unittest

from control_plane_kit_core.configuration import ConfigurationArtifactRequirement
from control_plane_kit_core.delegation_keys import DelegationKeyPurpose
from control_plane_kit_core.node_control import NodeControlCanonicalization
from control_plane_kit_core.planning import PlanGraphSide
from control_plane_kit_core.receiver_identity import receiver_node_control_audience
from control_plane_kit_core.receiver_identity import NodeControlReceiverTargetCodec
from control_plane_kit_core.runtime_authority import RuntimeAuthorityReference
from control_plane_kit_core.receiver_configuration import ReceiverNodeControlConfigurationCodec, select_receiver_node_control_configuration_artifact
from control_plane_kit_core.receiver_health_reads import DelegatedWorkloadReceiverHealthReadGrant, DelegatedWorkloadReceiverHealthReadGrantProfile
from control_plane_kit_core.receiver_health_transit import DelegatedGatewayReceiverHealthReadTransitGrant, DelegatedGatewayReceiverHealthReadTransitGrantProfile
from control_plane_kit_core.receiver_management_routes import ReceiverManagementRouteBinding, ReceiverManagementRoutes, ReceiverManagementRoutesCodec
from control_plane_kit_core.secret_version_grants import SecretVersionResolutionGrant
from control_plane_kit_core.secrets import SecretProviderEndpointReference, SecretReference, SecretResolutionGrant, SecretUseIntent
from control_plane_kit_core.topology import DEFAULT_GRAPH_CODEC, validate_graph
from control_plane_kit_operations.deployment_transitions import Deploy
from control_plane_kit_operations.health_effect_preparations import health_effect_attempt_wire_id
from control_plane_kit_operations.health_receiver_trust import HealthReceiverDecoders, HealthReceiverDecoderBinding, HealthReceiverSelection, GatewayHealthReceiverTrust
from control_plane_kit_operations.health_signing_authority import GatewayNodeHealthReadTransitSigningAuthority, WorkloadNodeHealthReadSigningAuthority
from control_plane_kit_operations.http_sample_compatibility import GatewayHttpSampleBinding
from control_plane_kit_operations.http_sample_execution import HttpSampleExecutionSupport
from control_plane_kit_operations.managed_verification_execution import (
    ManagedVerificationExecutionSupport, ManagedVerificationSupportUnavailable,
    PreparedPostgresReadinessCheck, PreparedPostgresReadinessRoute, select_postgres_readiness_route,
)
from control_plane_kit_operations.plan_derivation import PlanDerivationProfile, derive_activity_plan
from control_plane_kit_operations.records import RealizedGraphProjectionRecord
from control_plane_kit_operations.postgres_readiness import bind_original_postgres_readiness
from tests.health_receiver_trust_fixture import public_key, ByteDecoder, PROFILE
from tests.test_http_sample_compatibility import case, ROUTE_SLOT
from tests.test_postgres_readiness_binding import OriginalPostgresReadinessRecordTests


class ReceiverDecoder:
    def __init__(self):
        self.targets = []

    def decode(self, selection):
        trust = ByteDecoder(__import__("control_plane_kit_operations.health_receiver_trust", fromlist=["*"])).decode(selection)
        raw = json.loads(selection.artifact.content)
        target = raw.get("gateway_target")
        target = None if target is None else NodeControlReceiverTargetCodec().decode(target)
        self.targets.append(target)
        return replace(trust, gateway_target=target)


def reproject(original, graph):
    return RealizedGraphProjectionRecord.from_graph(projection_id=original.projection_id,
        workspace_id=original.workspace_id, source_authored_graph_id=original.source_authored_graph_id,
        projection_kind=original.projection_kind, projection_key=original.projection_key,
        graph=graph, created_by=original.created_by, created_at=original.created_at)


def with_desired(values, graph):
    desired = reproject(values["desired"], graph)
    graphs = (validate_graph(DEFAULT_GRAPH_CODEC.decode(values["current"].graph_descriptor)), validate_graph(graph))
    plan = replace(values["plan"], desired_realized_projection_id=desired.projection_id,
        plan=derive_activity_plan(Deploy(*graphs), profile=values["plan"].derivation_profile))
    return values | dict(desired=desired, plan=plan)


class ManagedVerificationRouteTests(unittest.TestCase):
    def inputs(self, **options):
        values, reference, slot = case(self, **options)
        graph = DEFAULT_GRAPH_CODEC.decode(values["desired"].graph_descriptor)
        graph = replace(graph, runtimes={identity: replace(runtime,
            authority_ref=RuntimeAuthorityReference("local-docker")) for identity, runtime in graph.runtimes.items()})
        gateway = graph.node("gateway")
        configured = ReceiverNodeControlConfigurationCodec().decode_bytes(select_receiver_node_control_configuration_artifact(
            artifacts=gateway.configuration_artifacts, environment=gateway.public_environment,
            control_surfaces=gateway.block_spec.control_surfaces).content.encode())
        artifacts = tuple(replace(artifact, content=json.dumps({**json.loads(artifact.content),
            "gateway_target": configured.target.descriptor()})) if artifact.artifact_id == "test-transit" else artifact
            for artifact in gateway.configuration_artifacts)
        graph = graph.update_node(replace(gateway, configuration_artifacts=artifacts))
        values["desired"] = reproject(values["desired"], graph)
        graphs = tuple(validate_graph(DEFAULT_GRAPH_CODEC.decode(values[name].graph_descriptor))
            for name in ("current", "desired"))
        profile = PlanDerivationProfile.MANAGED_SQL_HTTP_BOOTSTRAP_V1
        values["plan"] = replace(values["plan"], derivation_profile=profile,
            plan=derive_activity_plan(Deploy(*graphs), profile=profile))
        original = OriginalPostgresReadinessRecordTests().supplied_records()
        # This decoder is not invoked by route selection. Transit public trust
        # uses the separate existing health decoder owner, not HTTP sample keys.
        decoder = type("UnusedDecoder", (), {"decode": lambda *_: None})()
        support = ManagedVerificationExecutionSupport(HttpSampleExecutionSupport(
            GatewayHttpSampleBinding(reference, slot, decoder, route_slot=ROUTE_SLOT),
            graphs[1].graph.node("gateway").block_spec.gateway_transit.provider_socket_name),
            (original["observer"],), HealthReceiverDecoders((HealthReceiverDecoderBinding(reference,
                DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT, PROFILE, "test-transit", "/etc/test/transit.json",
                artifacts[0].media_type, artifacts[0].file_mode, ReceiverDecoder()),)))
        return values, support

    def test_selected_route_keeps_registered_gateway_slot_and_original_projection(self):
        values, support = self.inputs(socket="control", receiver_port=8081)
        selected = select_postgres_readiness_route(**values, support=support)
        self.assertEqual(selected.binding.origin, "http://api:8081")
        self.assertEqual(selected.binding.target, selected.receiver.target)
        self.assertEqual(selected.selection.product_reference, support.http_samples.gateway_binding.product)
        self.assertEqual(selected.selection.realized_projection_id, values["desired"].projection_id)
        self.assertEqual(selected.selection.artifact.target_path, ROUTE_SLOT.target_path)
        self.assertFalse(values["plan"].plan.ready_for_execution)  # Intermediate profile still blocked.

    def test_wrong_route_port_missing_slot_or_gateway_product_refuses(self):
        for options in (dict(route_port=8001), dict(route_changes={"artifact_id": "foreign-route"})):
            values, support = self.inputs(**options)
            with self.subTest(options=options), self.assertRaises(ManagedVerificationSupportUnavailable):
                select_postgres_readiness_route(**values, support=support)
        values, support = self.inputs()
        graph = DEFAULT_GRAPH_CODEC.decode(values["desired"].graph_descriptor)
        workload = next(product for product in values["products"]
            if product.reference != support.http_samples.gateway_binding.product)
        bad_gateway = replace(support.http_samples.gateway_binding, product=workload.reference)
        bad_support = replace(support, http_samples=replace(support.http_samples, gateway_binding=bad_gateway))
        for changed in (dict(products=(workload,)), dict(support=bad_support),
                dict(desired=values["current"]), dict(workspace_id="foreign")):
            with self.subTest(fields=tuple(changed)), self.assertRaises(ManagedVerificationSupportUnavailable) as caught:
                select_postgres_readiness_route(**(values | {"support": support} | changed))
            self.assertIsNone(caught.exception.__context__)
        # Same runtime contract with a mismatched desired product identity is
        # insufficient; the pinned registered material remains the owner.
        gateway = graph.node("gateway")
        changed = graph.update_node(replace(gateway, metadata={**gateway.metadata,
            "product_descriptor_digest": "f" * 64}))
        with self.assertRaises(ManagedVerificationSupportUnavailable):
            select_postgres_readiness_route(**with_desired(values, changed), support=support)

    def prepared(self):
        """Supplied public records only; none claim current admission or signing."""
        original = OriginalPostgresReadinessRecordTests().supplied_records()
        binding = bind_original_postgres_readiness(**original)
        request = binding.request()
        values, support = self.inputs()
        selected = select_postgres_readiness_route(**values, support=support)
        gateway = replace(binding.receiver.target, node_id=replace(binding.receiver.target.node_id, value="gateway"),
            receiver_id="b" * 32)
        route = ReceiverManagementRouteBinding("api", binding.receiver.target, binding.receiver.declaration, "http://api:8000")
        artifact = replace(selected.selection.artifact, content=ReceiverManagementRoutesCodec().encode_bytes(
            ReceiverManagementRoutes(gateway, (route,))).decode())
        selection = HealthReceiverSelection(binding.wait.source.workspace_id, binding.wait.source.desired_graph_id,
            binding.desired_projection_id, PlanGraphSide.DESIRED_GRAPH, "gateway", gateway.runtime_id.value,
            selected.transit_provider_socket_name, selected.selection.product_reference,
            selected.selection.descriptor_document, artifact)
        raw = json.loads(selected.transit_selection.artifact.content)
        raw.update(workspace=gateway.workspace_id.value, runtime=gateway.runtime_id.value,
            node=gateway.node_id.value, gateway_target=gateway.descriptor())
        transit_selection = replace(selection, artifact=replace(selected.transit_selection.artifact,
            content=json.dumps(raw)))
        trust = selected.transit_decoder.decoder.decode(transit_selection)
        selected = PreparedPostgresReadinessRoute(binding.wait.source.plan_id, selection, transit_selection,
            selected.transit_decoder, trust, gateway, binding.receiver, route,
            selected.ingress, selected.transit_provider_socket_name)
        common = dict(workspace_id=binding.wait.source.workspace_id, reference_registration_id="reference",
            provider_registration_id="provider", endpoint_reference=SecretProviderEndpointReference("workspace-secrets"),
            credential_reference=SecretReference("secret://local/provider/credential"), actor_subject="operator-a",
            session_id="session-a", run_id=binding.attempt.run_id.value, activity_id=binding.attempt.activity_id,
            intent_fingerprint="a" * 64)
        password = SecretVersionResolutionGrant(**common, authorization_id="password-use", correlation_id="password",
            selection=binding.installation.products[0].secret_selections[0], operation_id=binding.wait.source.request_id,
            effect_id=binding.wait.effect_id, request_fingerprint=binding.descriptor()["request_fingerprint"],
            selection_fingerprint=binding.receipt.selection_fingerprint)
        signers = []
        claims = dict(canonicalization=NodeControlCanonicalization.JCS_RFC8785_V1, target=request.target,
            authority_context=request.authority_context, kind=request.kind, declaration_identity=request.declaration_identity,
            request_id=request.request_id, request_digest=request.canonical_digest(),
            issued_at=binding.started_at_epoch_ms // 1000, not_before=binding.started_at_epoch_ms // 1000,
            expires_at=binding.deadline_epoch_ms // 1000)
        for family, intent, constructor in (("transit", SecretUseIntent.GATEWAY_NODE_HEALTH_READ_TRANSIT_SIGNING_KEY,
                GatewayNodeHealthReadTransitSigningAuthority), ("workload", SecretUseIntent.WORKLOAD_NODE_HEALTH_READ_SIGNING_KEY,
                WorkloadNodeHealthReadSigningAuthority)):
            grant = SecretResolutionGrant(**common, authorization_id=family + "-use", correlation_id=family,
                reference=SecretReference("secret://local/signing/" + family), intent=intent,
                operation_id=health_effect_attempt_wire_id(binding.attempt))
            signers.append(constructor(public_key(family), grant))
        transit = DelegatedGatewayReceiverHealthReadTransitGrant(**claims,
            profile=DelegatedGatewayReceiverHealthReadTransitGrantProfile.V2,
            purpose=DelegationKeyPurpose.GATEWAY_NODE_HEALTH_READ_TRANSIT, issuer="cpk-server",
            key_id=signers[0].public_key.key_id, attempt_id=health_effect_attempt_wire_id(binding.attempt),
            gateway_target=gateway, jti="transit-jti")
        workload = DelegatedWorkloadReceiverHealthReadGrant(**claims,
            profile=DelegatedWorkloadReceiverHealthReadGrantProfile.V2,
            purpose=DelegationKeyPurpose.WORKLOAD_NODE_HEALTH_READ, issuer="cpk-server",
            key_id=signers[1].public_key.key_id, audience=receiver_node_control_audience(request.target), jti="workload-jti")
        return PreparedPostgresReadinessCheck(binding, password, *signers, transit, workload, selected)

    def test_prepared_roles_preserve_existing_signer_scope_and_original_wait_binding(self):
        prepared = self.prepared()
        self.assertEqual(prepared.transit.resolution_grant.operation_id,
            health_effect_attempt_wire_id(prepared.binding.attempt))
        self.assertEqual(prepared.password_grant.effect_id, prepared.binding.wait.effect_id)
        for role in (prepared.transit, prepared.workload):
            for change in (dict(effect_id=prepared.binding.wait.effect_id), dict(probe_id="probe"), dict(session_id=None)):
                changed = replace(role, resolution_grant=replace(role.resolution_grant, **change))
                with self.subTest(change=change), self.assertRaises(ManagedVerificationSupportUnavailable):
                    replace(prepared, **({"transit": changed} if role is prepared.transit else {"workload": changed}))

    def test_key_id_alone_issuer_or_crosswired_route_never_binds(self):
        prepared = self.prepared()
        other_key = public_key("other", key_id=prepared.workload.public_key.key_id)
        for change in (dict(workload=replace(prepared.workload, public_key=other_key)),
                dict(workload_grant=replace(prepared.workload_grant, issuer="other-issuer")),
                dict(route=replace(prepared.route, plan_id="other-plan")),
                dict(password_grant=replace(prepared.password_grant, effect_id="other-effect"))):
            with self.subTest(fields=tuple(change)), self.assertRaises(ManagedVerificationSupportUnavailable):
                replace(prepared, **change)
        same_correlation = replace(prepared.workload, resolution_grant=replace(prepared.workload.resolution_grant,
            correlation_id=prepared.transit.resolution_grant.correlation_id))
        with self.assertRaises(ManagedVerificationSupportUnavailable):
            replace(prepared, workload=same_correlation)

    def test_legacy_decoder_is_compatible_but_cannot_qualify_sql_receiver_join(self):
        values, support = self.inputs()
        selected = select_postgres_readiness_route(**values, support=support)
        legacy = replace(selected.gateway_trust, gateway_target=None)
        self.assertEqual(legacy.gateway_node_id, selected.gateway_target.node_id)
        # The optional field preserves construction and legacy coverage. SQL
        # requires the full logical receiver, not merely the same node.
        class LegacyDecoder:
            def decode(self, selection):
                return replace(ReceiverDecoder().decode(selection), gateway_target=None)
        decoder = replace(support.health_receiver_decoders.bindings[0], decoder=LegacyDecoder())
        with self.assertRaises(ManagedVerificationSupportUnavailable):
            select_postgres_readiness_route(**values,
                support=replace(support, health_receiver_decoders=HealthReceiverDecoders((decoder,))))

    def test_same_node_transit_artifact_with_another_receiver_id_refuses(self):
        values, support = self.inputs()
        graph = DEFAULT_GRAPH_CODEC.decode(values["desired"].graph_descriptor)
        gateway = graph.node("gateway")
        artifacts = []
        for artifact in gateway.configuration_artifacts:
            if artifact.artifact_id == "test-transit":
                raw = json.loads(artifact.content)
                raw["gateway_target"]["receiver_id"] = "f" * 32
                artifact = replace(artifact, content=json.dumps(raw))
            artifacts.append(artifact)
        graph = graph.update_node(replace(gateway, configuration_artifacts=tuple(artifacts)))
        changed_values = with_desired(values, graph)
        decoder = support.health_receiver_decoders.bindings[0].decoder
        self.assertEqual(decoder.targets, [])
        with self.assertRaises(ManagedVerificationSupportUnavailable):
            select_postgres_readiness_route(**changed_values, support=support)
        self.assertEqual(len(decoder.targets), 1)
        self.assertEqual(decoder.targets[0].receiver_id, "f" * 32)
