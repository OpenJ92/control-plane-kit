"""Graph-pinned live Docker lifecycle for the official Collector product."""

from __future__ import annotations

import json
import os
import sys
import urllib.request

from control_plane_kit import (
    ActivityPlan,
    DeploymentGraph,
    DeploymentRecipe,
    DockerRuntime,
    EffectSucceeded,
    PinnedGraphSet,
    RemoveNodeResource,
    RemoveRuntimeResource,
    StartNode,
    StartRuntime,
    StopNode,
    StopRuntime,
    compile_activity_plan,
    compile_recipe,
    diff_graphs,
    effect_request_for_activity,
    materialize_effect_request,
    opentelemetry_collector_block,
    validate_graph,
)
from control_plane_kit.docker_runtime import DockerEffectInterpreter


PROJECT = "cpk-live-otel"
GRAPH_NAME = "otel-live"
BASE_GRAPH_ID = "otel-empty"
DESIRED_GRAPH_ID = "otel-desired"


def main(mode: str) -> None:
    if mode == "send":
        _send_trace()
        return
    empty = DeploymentGraph(GRAPH_NAME)
    desired = compile_recipe(_recipe())
    interpreter = DockerEffectInterpreter(project_name=PROJECT)
    if mode == "start":
        plan = compile_activity_plan(
            diff_graphs(validate_graph(empty), validate_graph(desired))
        )
        _execute_types(
            interpreter,
            plan,
            (StartRuntime, StartNode),
            empty,
            desired,
            PinnedGraphSet(
                "otel-live-workspace",
                "otel-live-start-plan",
                BASE_GRAPH_ID,
                DESIRED_GRAPH_ID,
            ),
            BASE_GRAPH_ID,
            DESIRED_GRAPH_ID,
        )
        return
    if mode == "cleanup":
        plan = compile_activity_plan(
            diff_graphs(validate_graph(desired), validate_graph(empty))
        )
        _execute_types(
            interpreter,
            plan,
            (StopNode, RemoveNodeResource, StopRuntime, RemoveRuntimeResource),
            desired,
            empty,
            PinnedGraphSet(
                "otel-live-workspace",
                "otel-live-cleanup-plan",
                DESIRED_GRAPH_ID,
                BASE_GRAPH_ID,
            ),
            DESIRED_GRAPH_ID,
            BASE_GRAPH_ID,
        )
        return
    raise ValueError(f"unknown Collector live mode: {mode}")


def _send_trace() -> None:
    endpoint = os.environ["CPK_OTEL_LIVE_ENDPOINT"].rstrip("/")
    payload = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {"stringValue": "cpk-live"},
                        }
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "cpk-live"},
                        "spans": [
                            {
                                "traceId": "0123456789abcdef0123456789abcdef",
                                "spanId": "0123456789abcdef",
                                "name": "cpk-live-span",
                                "kind": 1,
                                "startTimeUnixNano": "1000000000",
                                "endTimeUnixNano": "2000000000",
                                "status": {"code": 1},
                            }
                        ],
                    }
                ],
            }
        ]
    }
    request = urllib.request.Request(
        f"{endpoint}/v1/traces",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(f"OTLP HTTP request returned {response.status}")


def _execute_types(
    interpreter: DockerEffectInterpreter,
    plan: ActivityPlan,
    operation_types: tuple[type, ...],
    base: DeploymentGraph,
    desired: DeploymentGraph,
    graphs: PinnedGraphSet,
    base_graph_id: str,
    desired_graph_id: str,
) -> None:
    by_type = {type(activity.operation): activity for activity in plan.activities}
    for attempt, operation_type in enumerate(operation_types, start=1):
        activity = by_type[operation_type]
        request = effect_request_for_activity(
            activity,
            run_id="otel-live-run",
            attempt=attempt,
            idempotency_key=f"otel-live:{operation_type.__name__}:{attempt}",
        )
        materialized = materialize_effect_request(
            request,
            activity,
            graphs,
            base_graph_id=base_graph_id,
            base_graph=base,
            desired_graph_id=desired_graph_id,
            desired_graph=desired,
        )
        result = interpreter.execute(materialized)
        if not isinstance(result, EffectSucceeded):
            raise RuntimeError(
                f"Collector live {operation_type.__name__} failed: {result!r}"
            )


def _recipe() -> DeploymentRecipe:
    return DeploymentRecipe(
        GRAPH_NAME,
        DockerRuntime(
            runtime_id="docker",
            network_name=f"{PROJECT}-network",
            children=(opentelemetry_collector_block("collector"),),
        ),
    )


if __name__ == "__main__":
    main(sys.argv[1])
