from unittest import TestCase, main

from control_plane_kit import DeploymentGraph, Endpoint, Node
from control_plane_kit.proxies import (
    ActiveTarget,
    HAProxyImplementation,
    HttpProtocol,
    PostgresProtocol,
    ProxyNode,
    RoundRobin,
)


class ProxyNodeTests(TestCase):
    def test_active_http_proxy_adds_mutable_edge(self) -> None:
        graph = DeploymentGraph("demo").add_node(
            Node("api-v1", "fastapi", {"default": Endpoint("http://api-v1:8000")})
        )

        graph = ProxyNode(
            "api-router",
            protocol=HttpProtocol(),
            behavior=ActiveTarget("api-v1"),
            implementation=HAProxyImplementation(),
        ).attach(graph)

        router = graph.nodes["api-router"]
        edge = graph.edges["api-router.active"]
        self.assertEqual("http-proxy", router.kind)
        self.assertTrue(edge.mutable)
        self.assertEqual("api-v1", edge.target)
        self.assertEqual("haproxy", router.metadata["implementation"])

    def test_round_robin_proxy_adds_one_edge_per_target(self) -> None:
        graph = (
            DeploymentGraph("demo")
            .add_node(Node("api-a", "fastapi", {"default": Endpoint("http://api-a:8000")}))
            .add_node(Node("api-b", "fastapi", {"default": Endpoint("http://api-b:8000")}))
        )

        graph = ProxyNode(
            "api-lb",
            protocol=HttpProtocol(),
            behavior=RoundRobin(["api-a", "api-b"]),
            implementation=HAProxyImplementation(),
        ).attach(graph)

        self.assertEqual({"api-lb.target.0", "api-lb.target.1"}, set(graph.edges))
        self.assertFalse(graph.edges["api-lb.target.0"].mutable)

    def test_postgres_proxy_uses_postgres_endpoint_scheme(self) -> None:
        graph = DeploymentGraph("demo").add_node(
            Node(
                "postgres-v1",
                "postgres",
                {"default": Endpoint("postgresql://postgres-v1:5432/app")},
            )
        )

        graph = ProxyNode(
            "postgres-switch",
            protocol=PostgresProtocol(),
            behavior=ActiveTarget("postgres-v1"),
            implementation=HAProxyImplementation(),
        ).attach(graph)

        self.assertEqual(
            "postgresql://postgres-switch:5432",
            graph.nodes["postgres-switch"].endpoint().url,
        )
        self.assertEqual("postgres", graph.edges["postgres-switch.active"].protocol)


if __name__ == "__main__":
    main()
