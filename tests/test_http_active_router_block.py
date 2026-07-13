from unittest import TestCase, main

from control_plane_kit import (
    CapabilityName,
    DockerRuntime,
    DeploymentRecipe,
    Protocol,
    compile_recipe,
    http_active_router_block,
)


class HttpActiveRouterBlockTests(TestCase):
    def test_http_active_router_block_compiles_with_expected_descriptor(self):
        graph = compile_recipe(
            DeploymentRecipe(
                "router-demo",
                DockerRuntime(children=(http_active_router_block("api-router"),)),
            )
        )
        router = graph.node("api-router")

        self.assertEqual(router.provider_socket("internal").protocol, Protocol.HTTP)
        self.assertEqual(router.requirement_socket("targets").protocol, Protocol.HTTP)
        self.assertEqual(router.endpoint("internal").url, "plan://api-router/internal")
        self.assertEqual(
            router.descriptor()["requirements"]["targets"],
            {
                "kind": "runtime",
                "protocol": "http",
                "route_set": "targets",
                "required": True,
            },
        )
        self.assertEqual(router.metadata["display_name"], "HTTP Active Router")
        self.assertEqual(
            [capability["name"] for capability in router.metadata["capabilities"]],
            [
                CapabilityName.HEALTH_CHECKABLE.value,
                CapabilityName.TARGET_MUTABLE.value,
                CapabilityName.SWITCHABLE.value,
                CapabilityName.DRAINABLE.value,
            ],
        )


if __name__ == "__main__":
    main()
