"""Fresh mixed graph language; registered/native/provider evidence is external."""
from dataclasses import replace
import unittest

import control_plane_kit_core as core
from control_plane_kit_core.algebra import BlockSockets, ProviderSocket
from control_plane_kit_core.configuration import ConfigurationArtifact, ConfigurationMediaType
from control_plane_kit_core.environment import PublicStaticEnvironmentBinding, SocketDerivedEnvironmentBinding
from control_plane_kit_core.node_control import NodeHealthReadKind
from control_plane_kit_core.planning import ActivityPlan, StartNode, WaitForHealthy, ReviewChange, ObserveNodeHealth, ObserveManagementBootstrap, ManagementBootstrapStage
from control_plane_kit_core.receiver_configuration import ReceiverNodeControlConfigurationCodec
from control_plane_kit_core.secret_selections import SecretVersionSelection, UnselectedSecretUse
from control_plane_kit_core.secrets import SecretFileDelivery, SecretFilePathBinding, SecretReference, SecretUseIntent
from control_plane_kit_core.topology import GraphDescriptorCodec, validate_graph, NodeSubject
from control_plane_kit_core.topology.graph import Endpoint, LiteralAddress
from control_plane_kit_core.types import Protocol, RuntimeKind
from control_plane_kit_core.verification import PostgresQueryCheck, PostgresPasswordAuthentication, VerificationContract
from control_plane_kit_core.wrapper_configuration import WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT
from tests.test_management_bootstrap_planning import http_sample_bootstrap_graph, empty
from tests import test_receiver_configuration as receiver_fixtures


def mixed_graph():
    graph = http_sample_bootstrap_graph()
    template = graph.node("workload")
    reference = SecretReference("secret://local/workspace-a/postgres/password")
    check = PostgresQueryCheck(check_id="installation-postgres-ready", provider_socket="sql",
        authentication=PostgresPasswordAuthentication("cpk", "cpk", reference))
    surface = replace(template.block_spec.control_surfaces[0], health_reads=(NodeHealthReadKind.READINESS,))
    old = receiver_fixtures.ReceiverConfigurationTests()
    old.setUp()
    document = old.document(replace(old.declaration, surface=surface))
    document["target"].update(runtime_id=template.runtime_id, node_id="postgres", receiver_id="c" * 32,
        provider_socket_name=surface.provider_socket_name.value)
    receiver = old.codec.decode(document)
    artifact = ConfigurationArtifact("receiver", "/etc/test/postgres-control.json", ConfigurationMediaType.JSON,
        ReceiverNodeControlConfigurationCodec().encode_bytes(receiver).decode())
    control = surface.provider_socket_name.value
    delivery = SecretFileDelivery("/run/secrets/postgres/password", reference, SecretUseIntent.POSTGRES_PASSWORD,
        path_binding=SecretFilePathBinding("POSTGRES_PASSWORD_FILE"))
    node = replace(template, node_id="postgres", kind="container-server", block_spec=replace(template.block_spec, role_id="postgres",
        control_surfaces=(surface,), verification=VerificationContract((check,))),
        sockets=BlockSockets(providers=(ProviderSocket(control, Protocol.HTTP), ProviderSocket("sql", Protocol.POSTGRES))),
        endpoints={control: Endpoint(LiteralAddress("http://postgres:8080"), Protocol.HTTP),
            "sql": Endpoint(LiteralAddress("postgresql://postgres:5432"), Protocol.POSTGRES)},
        configuration_artifacts=(artifact,), public_environment=(PublicStaticEnvironmentBinding(
            WORKLOAD_NODE_CONTROL_CONFIGURATION_ENVIRONMENT, artifact.target_path),),
        secret_deliveries=(delivery,), secret_selections=(SecretVersionSelection(reference,
            SecretUseIntent.POSTGRES_PASSWORD, "version-1"),),
        metadata={"product_identity": "test/postgres/1", "product_descriptor_digest": "b" * 64})
    runtime = graph.runtimes[node.runtime_id]
    return replace(graph, nodes={**graph.nodes, node.node_id: node}, runtimes={**graph.runtimes,
        runtime.runtime_id: replace(runtime, children=tuple(sorted((*runtime.children, node.node_id))))})


class ManagedVerificationPlanningTests(unittest.TestCase):
    def plans(self, desired, current=None):
        left, right = validate_graph(empty() if current is None else current), validate_graph(desired)
        left.require_valid()
        right.require_valid()
        return (core.compile_http_sample_bootstrap_activity_plan(left, right),
            core.compile_managed_verification_bootstrap_activity_plan(left, right))

    def test_same_graph_changes_only_sql_review_and_keeps_original_wait_dependencies(self):
        desired = mixed_graph()
        codec = GraphDescriptorCodec()
        before = codec.encode(desired)
        old, selected = self.plans(desired)
        review = tuple(item for item in old.activities if type(item.operation) is ReviewChange
            and item.operation.target.subject == NodeSubject("postgres"))
        self.assertEqual(len(review), 1)
        self.assertFalse(old.ready_for_execution)
        self.assertTrue(selected.ready_for_execution)
        self.assertEqual(ActivityPlan(tuple(item for item in old.activities if item not in review)), selected)
        wait, = (item for item in selected.activities if type(item.operation) is WaitForHealthy
            and item.operation.target.node_id == "postgres")
        parents = {item.predecessor for item in wait.dependencies}
        for kind, predicate in ((StartNode, lambda op: op.target.node_id == "postgres"),
                (ObserveNodeHealth, lambda op: op.node_id == "postgres"),
                (ObserveManagementBootstrap, lambda op: op.stage is ManagementBootstrapStage.AUTHENTICATED_MANAGEMENT_PATH)):
            expected, = (item for item in selected.activities if type(item.operation) is kind and predicate(item.operation))
            self.assertIn(expected.activity_id, parents)
        self.assertEqual(codec.encode(desired), before)
        self.assertIs(type(desired.node("postgres").block_spec.verification.checks[0]), PostgresQueryCheck)

    def test_unselected_password_missing_file_or_unsupported_policy_keeps_sql_review(self):
        desired = mixed_graph()
        node = desired.node("postgres")
        check, = node.block_spec.verification.checks
        selection, = node.secret_selections
        for changed in (replace(node, secret_selections=(UnselectedSecretUse(selection.reference, selection.intent),)),
                replace(node, secret_deliveries=(replace(node.secret_deliveries[0], path_binding=None),)),
                replace(node, block_spec=replace(node.block_spec,
                    verification=VerificationContract((replace(check, authentication=None),)))),
                replace(node, block_spec=replace(node.block_spec, verification=VerificationContract((
                    replace(check, policy=replace(check.policy, maximum_attempts=2)),))))):
            with self.subTest(node=changed.node_id):
                _, selected = self.plans(desired.update_node(changed))
                self.assertFalse(selected.ready_for_execution)
                self.assertTrue(any(type(item.operation) is ReviewChange
                    and item.operation.target.subject == NodeSubject("postgres") for item in selected.activities))

    def test_no_sql_or_retained_graph_cannot_acquire_the_new_fresh_profile(self):
        old, selected = self.plans(http_sample_bootstrap_graph())
        self.assertTrue(old.ready_for_execution)
        self.assertFalse(selected.ready_for_execution)
        desired = mixed_graph()
        _, selected = self.plans(desired, desired)
        self.assertFalse(selected.ready_for_execution)

    def test_qualified_sql_cannot_clear_another_nodes_original_review(self):
        desired = mixed_graph()
        node = desired.node("workload")
        checks = node.block_spec.verification.checks
        changed = replace(node, block_spec=replace(node.block_spec, verification=VerificationContract((
            replace(checks[0], path="/not-configured"), *checks[1:]))))
        _, selected = self.plans(desired.update_node(changed))
        self.assertFalse(selected.ready_for_execution)
        self.assertTrue(any(type(item.operation) is ReviewChange
            and item.operation.target.subject == NodeSubject("workload") for item in selected.activities))

    def test_equal_protocol_values_keep_the_same_plan(self):
        desired = mixed_graph()
        _, original = self.plans(desired)
        node = desired.node("postgres")
        def copied(protocol):
            return Protocol(protocol.transport, protocol.application)
        changed = replace(node, sockets=replace(node.sockets, providers=tuple(
            replace(provider, protocol=copied(provider.protocol)) if provider.name == "sql" else provider
            for provider in node.sockets.providers)),
            endpoints={name: replace(endpoint, protocol=copied(endpoint.protocol)) if name == "sql" else endpoint
                for name, endpoint in node.endpoints.items()})
        _, equivalent = self.plans(desired.update_node(changed))
        self.assertEqual(equivalent, original)
        self.assertTrue(equivalent.ready_for_execution)

    def test_graph_owner_refuses_password_path_shadow_and_non_docker_stays_reviewed(self):
        desired = mixed_graph()
        node = desired.node("postgres")
        with self.assertRaises(ValueError):
            replace(node, socket_environment=(SocketDerivedEnvironmentBinding(
                "POSTGRES_PASSWORD_FILE", "/run/secrets/other", "postgres.http-api->postgres.sql"),))
        runtime = desired.runtimes[node.runtime_id]
        changed = replace(desired, runtimes={**desired.runtimes,
            runtime.runtime_id: replace(runtime, kind=RuntimeKind.KUBERNETES)})
        _, selected = self.plans(changed)
        self.assertFalse(selected.ready_for_execution)
