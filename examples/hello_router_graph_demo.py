"""Graph-only demo for the hello/router switch topology.

This example stops at the current stable boundary:

    DeploymentRecipe -> compile_recipe -> DeploymentGraph

It does not start containers, mutate a live router, or pretend that the runtime
interpreter exists yet.  It is the example to read when you want to see the
construction language and graph compiler without Docker noise.

Run from the repository root:

    python3 -m examples.hello_router_graph_demo
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from control_plane_kit import DeploymentGraph, compile_recipe
from examples.hello_router_switch_demo import EARTH_ID, MARS_ID, ROUTER_ID, recipe


@dataclass(frozen=True)
class HelloRouterGraphRunner:
    """Pure runner for the hello/router recipe compiler."""

    def graph(self) -> DeploymentGraph:
        """Compile the package construction language into a pure graph."""

        return compile_recipe(recipe())

    def descriptor(self) -> dict[str, Any]:
        """Return a JSON-ready graph descriptor."""

        return self.graph().descriptor()

    def summary(self) -> dict[str, Any]:
        """Return the smallest useful view of the compiled topology."""

        graph = self.graph()
        return {
            "name": graph.name,
            "runtime_ids": sorted(graph.runtimes),
            "node_ids": sorted(graph.nodes),
            "edge_ids": sorted(graph.edges),
            "router_runtime_targets": {
                edge.provider_role: dict(edge.runtime_assignments)
                for edge in graph.edges.values()
                if edge.consumer_role == ROUTER_ID
            },
            "hello_worlds": {
                EARTH_ID: graph.node(EARTH_ID).metadata["hello_world"],
                MARS_ID: graph.node(MARS_ID).metadata["hello_world"],
            },
        }


def main() -> None:
    runner = HelloRouterGraphRunner()
    print(json.dumps(runner.summary(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
