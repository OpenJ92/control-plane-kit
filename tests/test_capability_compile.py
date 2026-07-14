from unittest import TestCase, main

from control_plane_kit import (
    BlockSpec,
    CapabilityName,
    DockerRuntime,
    PlanOnlyImplementation,
    Protocol,
    ProxyBlock,
    ProviderSocket,
    RoleSockets,
    compile_recipe,
    DeploymentRecipe,
)


class CapabilityCompileTests(TestCase):
    def test_proxy_block_advertises_capabilities_in_compiled_metadata(self):
        router = ProxyBlock(
            spec=BlockSpec(
                role_id="api-router",
                display_name="API Router",
                capabilities=(
                    CapabilityName.HEALTH_CHECKABLE,
                    CapabilityName.TARGET_MUTABLE,
                    CapabilityName.SWITCHABLE,
                ),
            ),
            implementation=PlanOnlyImplementation(kind="plan-router"),
            sockets=RoleSockets(outputs=(ProviderSocket("internal", Protocol.HTTP),)),
        )
        graph = compile_recipe(DeploymentRecipe("capability-demo", DockerRuntime(children=(router,))))

        self.assertEqual(
            graph.node("api-router").metadata["capabilities"],
            [
                {
                    "name": "health-checkable",
                    "label": "Health",
                    "description": "Node exposes health and status state through the control protocol.",
                    "route_set": "common-status",
                },
                {
                    "name": "target-mutable",
                    "label": "Targets",
                    "description": "Node can register or replace downstream targets.",
                    "route_set": "targets",
                },
                {
                    "name": "switchable",
                    "label": "Switch",
                    "description": "Node can switch one active downstream target.",
                    "route_set": "targets",
                },
            ],
        )


if __name__ == "__main__":
    main()
