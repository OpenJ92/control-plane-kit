from __future__ import annotations

from ipaddress import IPv4Address, IPv6Address
import unittest

from control_plane_kit import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DeploymentGraph,
    DeploymentRecipe,
    DockerImageImplementation,
    DockerRuntime,
    HostPublication,
    HostPublicationMaterial,
    PinnedGraphSet,
    Protocol,
    ProviderSocket,
    compile_activity_plan,
    compile_recipe,
    diff_graphs,
    effect_request_for_activity,
    materialize_effect_request,
    validate_graph,
)
from control_plane_kit.planning import ReconcileNode, RiskLevel, StartNode


class HostPublicationTests(unittest.TestCase):
    def test_private_only_is_the_authoring_default(self) -> None:
        graph = compile_recipe(_recipe())

        self.assertEqual(graph.node("api").metadata["host_publications"], [])
        self.assertEqual(graph.node("api").endpoint("internal").scope.value, "private")

    def test_explicit_publication_survives_graph_and_pinned_materialization(self) -> None:
        desired = compile_recipe(
            _recipe(
                HostPublication(
                    IPv6Address("::1"),
                    18_000,
                )
            )
        )
        current = DeploymentGraph("host-publication")
        plan = compile_activity_plan(
            diff_graphs(validate_graph(current), validate_graph(desired))
        )
        activity = next(
            value
            for value in plan.activities
            if isinstance(value.operation, StartNode)
        )
        request = effect_request_for_activity(
            activity,
            run_id="run",
            attempt=1,
            idempotency_key="run:start-api:1",
        )

        materialized = materialize_effect_request(
            request,
            activity,
            PinnedGraphSet("workspace", "plan", "base", "desired"),
            base_graph_id="base",
            base_graph=current,
            desired_graph_id="desired",
            desired_graph=desired,
        )

        self.assertEqual(
            materialized.material.implementation.host_publications,
            (
                HostPublicationMaterial(
                    "internal",
                    Protocol.HTTP,
                    8000,
                    IPv6Address("::1"),
                    18_000,
                ),
            ),
        )
        self.assertEqual(
            materialized.material.endpoints[0].address.value,
            "http://docker-api:8000",
        )
        self.assertNotEqual(
            materialized.material.endpoints[0].address.value,
            "http://[::1]:18000",
        )

    def test_unknown_socket_and_fixed_port_collision_fail_at_authoring(self) -> None:
        runtime = DockerRuntime()
        sockets = BlockSockets(
            providers=(
                ProviderSocket("first", Protocol.HTTP),
                ProviderSocket("second", Protocol.HTTP),
            )
        )

        with self.assertRaisesRegex(ValueError, "unknown provider"):
            DockerImageImplementation(
                "api:latest",
                ports={"first": 8000, "second": 8001},
                host_publications={"missing": HostPublication.loopback_v4()},
            ).materialize("api", sockets, runtime)

        with self.assertRaisesRegex(ValueError, "fixed-port collision"):
            DockerImageImplementation(
                "api:latest",
                ports={"first": 8000, "second": 8001},
                host_publications={
                    "first": HostPublication.loopback_v4(18_000),
                    "second": HostPublication.loopback_v4(18_000),
                },
            ).materialize("api", sockets, runtime)

    def test_same_fixed_host_port_is_valid_once_per_transport(self) -> None:
        desired = compile_recipe(_dual_transport_recipe())
        current = DeploymentGraph("dual-transport-publication")
        plan = compile_activity_plan(
            diff_graphs(validate_graph(current), validate_graph(desired))
        )
        activity = next(
            value
            for value in plan.activities
            if isinstance(value.operation, StartNode)
        )
        request = effect_request_for_activity(
            activity,
            run_id="run",
            attempt=1,
            idempotency_key="run:start-dns:1",
        )
        materialized = materialize_effect_request(
            request,
            activity,
            PinnedGraphSet("workspace", "plan", "base", "desired"),
            base_graph_id="base",
            base_graph=current,
            desired_graph_id="desired",
            desired_graph=desired,
        )

        self.assertEqual(
            {
                (publication.socket_name, publication.protocol)
                for publication in materialized.material.implementation.host_publications
            },
            {
                ("dns-tcp", Protocol.DNS_TCP),
                ("dns-udp", Protocol.DNS_UDP),
            },
        )

    def test_adding_publication_is_explicit_disruptive_plan_work(self) -> None:
        current = validate_graph(compile_recipe(_recipe()))
        desired = validate_graph(
            compile_recipe(_recipe(HostPublication.loopback_v4(18_000)))
        )

        plan = compile_activity_plan(diff_graphs(current, desired))

        self.assertEqual(len(plan.activities), 1)
        self.assertIsInstance(plan.activities[0].operation, ReconcileNode)
        self.assertIs(plan.activities[0].risk, RiskLevel.MEDIUM)
        self.assertEqual(plan.activities[0].impact.value, "disruptive")

    def test_loopback_factories_are_closed_ip_values(self) -> None:
        self.assertEqual(
            HostPublication.loopback_v4().bind_address,
            IPv4Address("127.0.0.1"),
        )
        self.assertEqual(
            HostPublication.loopback_v6().bind_address,
            IPv6Address("::1"),
        )


def _recipe(publication: HostPublication | None = None) -> DeploymentRecipe:
    host_publications = {} if publication is None else {"internal": publication}
    api = ApplicationBlock(
        BlockSpec("api"),
        DockerImageImplementation(
            "api:latest",
            ports={"internal": 8000},
            host_publications=host_publications,
        ),
        BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
    )
    return DeploymentRecipe(
        "host-publication",
        DockerRuntime(children=(api,)),
    )


def _dual_transport_recipe() -> DeploymentRecipe:
    dns = ApplicationBlock(
        BlockSpec("dns"),
        DockerImageImplementation(
            "dns:latest",
            ports={"dns-tcp": 53, "dns-udp": 53},
            host_publications={
                "dns-tcp": HostPublication.loopback_v4(10_053),
                "dns-udp": HostPublication.loopback_v4(10_053),
            },
        ),
        BlockSockets(
            providers=(
                ProviderSocket("dns-tcp", Protocol.DNS_TCP),
                ProviderSocket("dns-udp", Protocol.DNS_UDP),
            )
        ),
    )
    return DeploymentRecipe(
        "dual-transport-publication",
        DockerRuntime(children=(dns,)),
    )


if __name__ == "__main__":
    unittest.main()
