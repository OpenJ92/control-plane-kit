"""Live generated Hello topology through the canonical deployment program."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import psycopg

from control_plane_kit.application.deploy import (
    AdvancedDeployment,
    AdmissionGrant,
    AdvancementGrant,
    ApprovalGrant,
    ApprovalSuspension,
    ClaimGrant,
    DeploymentExecutionGrant,
    DeploymentPlanRequest,
    DeploymentProgram,
    DeploymentProgramServices,
    ExecutionContinuation,
    ExecutionLimits,
    PlanningServices,
)
from control_plane_kit.adapters.probes import (
    HttpApplicationHealthProbeAdapter,
    ProbeAddressPolicy,
    ProbeEffectInterpreter,
    StaticRuntimeEndpointProvider,
    TcpTransportProbeAdapter,
)
from control_plane_kit.docker_runtime import (
    DockerEffectInterpreter,
    DockerProcessProbeAdapter,
)
from control_plane_kit.effects import (
    CapabilityInterpreterRegistry,
    EffectCapability,
    EndpointContext,
    LiteralEndpointMaterial,
    RuntimeEndpointObservation,
    TimeoutPolicy,
)
from control_plane_kit.stores import (
    GraphVersionRecord,
    PostgresStoreBundle,
    PostgresUnitOfWork,
    WorkspaceRecord,
    install_schema,
)
from control_plane_kit.core.topology import DeploymentGraph
from control_plane_kit.core.types import Protocol
from control_plane_kit.workflows import (
    ActivityPlanningCommandService,
    ActivityPlanningGraphInvalid,
    ApprovalCommandService,
    CurrentGraphAdvancementCommandService,
    DeploymentPlanContextQueryService,
    DesiredGraphCommandService,
    ExecutionAdmissionCommandService,
    ExecutionCoordinator,
    ExecutionWorkerAuthority,
    IdempotencyKey,
    OperationCommandService,
    RunLifecycleCommandService,
)
from examples.generated_hello_graphs import (
    HelloGraphShape,
    MissingDatabaseConnection,
    generated_hello_graph,
)


WORKSPACE_ID = "generated-hello-live"
INVALID_WORKSPACE_ID = "generated-hello-invalid"
EMPTY_GRAPH_ID = "graph-empty"
ROOT_PORT = 18280
MAX_RESPONSE_BYTES = 65_536
WORKER = ExecutionWorkerAuthority(
    "generated-hello-worker",
    ("execution:operate",),
)


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


HTTP = build_opener(NoRedirects)


@dataclass(frozen=True)
class PlannedLiveDeployment:
    plan_id: str
    approval_request_id: str
    current_graph_id: str
    desired_graph_id: str
    activity_count: int

    def descriptor(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "approval_request_id": self.approval_request_id,
            "current_graph_id": self.current_graph_id,
            "desired_graph_id": self.desired_graph_id,
            "activity_count": self.activity_count,
        }


def shape_from_environment() -> HelloGraphShape:
    branching = int(os.environ.get("CPK_GENERATED_HELLO_BRANCHING_FACTOR", "2"))
    depth = int(os.environ.get("CPK_GENERATED_HELLO_DEPTH", "1"))
    max_live_nodes = int(os.environ.get("CPK_GENERATED_HELLO_MAX_LIVE_NODES", "31"))
    if branching < 1 or depth < 1:
        raise ValueError("the live generated proof requires branching and depth of at least one")
    if max_live_nodes < 1:
        raise ValueError("CPK_GENERATED_HELLO_MAX_LIVE_NODES must be positive")
    shape = HelloGraphShape(branching, depth, root_host_port=ROOT_PORT)
    live_node_count = shape.application_count + shape.database_count
    if live_node_count > max_live_nodes:
        raise ValueError(
            "generated Hello live topology requires "
            f"{live_node_count} containers, exceeding the configured "
            f"{max_live_nodes}-container limit"
        )
    return shape


def empty_graph(name: str = "generated-hello-empty") -> DeploymentGraph:
    return DeploymentGraph(name)


def initialize(database_url: str, workspace_id: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        install_schema(connection)
        stores = PostgresStoreBundle(connection)
        try:
            stores.workspace.get(workspace_id)
            return
        except KeyError:
            pass
        stores.workspace.create(WorkspaceRecord(workspace_id, workspace_id))
        stores.graph_topology.save(
            GraphVersionRecord.from_graph(
                graph_id=f"{workspace_id}-{EMPTY_GRAPH_ID}",
                workspace_id=workspace_id,
                version=1,
                graph=empty_graph(f"{workspace_id}-empty"),
                created_by="generated-hello-operator",
                created_at=_text_clock(),
            )
        )
        stores.workspace.set_current_graph(
            workspace_id,
            f"{workspace_id}-{EMPTY_GRAPH_ID}",
        )
        stores.workspace.set_desired_graph(
            workspace_id,
            f"{workspace_id}-{EMPTY_GRAPH_ID}",
        )


def prove_invalid_graph(database_url: str, shape: HelloGraphShape) -> dict[str, object]:
    initialize(database_url, INVALID_WORKSPACE_ID)
    current = empty_graph("generated-invalid-empty")
    desired = generated_hello_graph(shape, MissingDatabaseConnection())
    program = deployment_program(database_url, {})
    deployment = program.between(current, desired)
    try:
        deployment.plan(
            DeploymentPlanRequest(
                deployment.transition,
                INVALID_WORKSPACE_ID,
                f"{INVALID_WORKSPACE_ID}-{EMPTY_GRAPH_ID}",
                f"{INVALID_WORKSPACE_ID}-{EMPTY_GRAPH_ID}",
                "generated-hello-operator",
                "Reject invalid generated Hello graph",
                "Invalid topology must not reach approval or execution.",
                "invalid",
            )
        )
    except ActivityPlanningGraphInvalid:
        pass
    else:
        raise RuntimeError("invalid generated graph unexpectedly produced a plan")

    with psycopg.connect(database_url, autocommit=True) as connection:
        stores = PostgresStoreBundle(connection)
        sessions = stores.activity_history.sessions_for_workspace(INVALID_WORKSPACE_ID)
        plans = tuple(
            plan
            for session in sessions
            for plan in stores.activity_history.plans_for_session(session.session_id)
        )
        approvals = tuple(
            request
            for session in sessions
            for request in stores.activity_history.approval_requests_for_session(
                session.session_id
            )
        )
    if plans or approvals:
        raise RuntimeError("invalid graph left plan or approval evidence")
    return {
        "invalid_graph_rejected": True,
        "plans": len(plans),
        "approvals": len(approvals),
    }


def prepare(database_url: str, shape: HelloGraphShape) -> dict[str, object]:
    initialize(database_url, WORKSPACE_ID)
    current = empty_graph()
    desired = generated_hello_graph(shape)
    planned = plan_and_approve(
        database_url,
        prefix="deploy",
        workspace_id=WORKSPACE_ID,
        current_graph_id=f"{WORKSPACE_ID}-{EMPTY_GRAPH_ID}",
        current=current,
        desired=desired,
    )
    result = deployment_program(
        database_url,
        {planned.desired_graph_id: desired},
    ).for_plan(planned.plan_id).run(
        planned.approval_request_id,
        execution_grant("deploy", max_effects=1),
    )
    if not isinstance(result, ExecutionContinuation):
        raise RuntimeError("runtime bootstrap must suspend after one bounded effect")
    return {**planned.descriptor(), "status": result.execution.status.value}


def resume_deploy(
    database_url: str,
    shape: HelloGraphShape,
) -> dict[str, object]:
    planned = stored_deployment(database_url, "deploy")
    desired = generated_hello_graph(shape)
    result = deployment_program(
        database_url,
        {planned.desired_graph_id: desired},
    ).for_plan(planned.plan_id).run(
        planned.approval_request_id,
        execution_grant("deploy", max_effects=512),
    )
    if not isinstance(result, AdvancedDeployment):
        raise RuntimeError(f"generated deployment did not advance: {type(result).__name__}")
    return {
        "plan_id": planned.plan_id,
        "graph_id": result.advancement.to_graph_id,
        "run_id": result.executed.execution.run.run_id,
        "status": result.executed.execution.status.value,
    }


def verify_live_graph(shape: HelloGraphShape) -> dict[str, object]:
    graph = generated_hello_graph(shape)
    checked: list[str] = []
    if _get(f"http://hello-stress-runtime-hello-root:8000/") != b"Hello from hello-root!":
        raise RuntimeError("generated root response did not match graph material")
    for edge in sorted(graph.edges.values(), key=lambda value: value.edge_id):
        consumer = graph.node(edge.consumer_role)
        base = _probe_address(consumer.endpoint("internal").url, Protocol.HTTP)
        if edge.protocol is Protocol.HTTP:
            dependency = edge.requirement_socket.removeprefix("http-")
            expected = f"Hello from {edge.provider_role}!".encode()
            path = f"/dependencies/{dependency}/http"
        elif edge.protocol is Protocol.POSTGRES:
            dependency = edge.requirement_socket.removeprefix("database-")
            expected = f"database {dependency}: reachable".encode()
            path = f"/dependencies/{dependency}/database"
        else:
            raise RuntimeError("generated graph contains an unsupported verification protocol")
        if _get(f"{base}{path}") != expected:
            raise RuntimeError(f"generated dependency route failed for {edge.edge_id}")
        checked.append(edge.edge_id)
    return {
        "root": "Hello from hello-root!",
        "connections_checked": checked,
    }


def begin_teardown(
    database_url: str,
    shape: HelloGraphShape,
) -> dict[str, object]:
    deployed = stored_deployment(database_url, "deploy")
    current = generated_hello_graph(shape)
    desired = empty_graph("generated-hello-teardown")
    planned = plan_and_approve(
        database_url,
        prefix="teardown",
        workspace_id=WORKSPACE_ID,
        current_graph_id=deployed.desired_graph_id,
        current=current,
        desired=desired,
    )
    result = deployment_program(
        database_url,
        {
            deployed.desired_graph_id: current,
            planned.desired_graph_id: desired,
        },
    ).for_plan(planned.plan_id).run(
        planned.approval_request_id,
        execution_grant(
            "teardown",
            max_effects=planned.activity_count - 1,
        ),
    )
    if not isinstance(result, ExecutionContinuation):
        raise RuntimeError("teardown must suspend before removing the controller network")
    return {**planned.descriptor(), "status": result.execution.status.value}


def finish_teardown(
    database_url: str,
    shape: HelloGraphShape,
) -> dict[str, object]:
    planned = stored_deployment(database_url, "teardown")
    current = generated_hello_graph(shape)
    desired = empty_graph("generated-hello-teardown")
    result = deployment_program(
        database_url,
        {
            planned.current_graph_id: current,
            planned.desired_graph_id: desired,
        },
    ).for_plan(planned.plan_id).run(
        planned.approval_request_id,
        execution_grant("teardown", max_effects=32),
    )
    if not isinstance(result, AdvancedDeployment):
        raise RuntimeError(f"generated teardown did not advance: {type(result).__name__}")
    return {
        "plan_id": planned.plan_id,
        "graph_id": result.advancement.to_graph_id,
        "run_id": result.executed.execution.run.run_id,
        "status": result.executed.execution.status.value,
    }


def plan_and_approve(
    database_url: str,
    *,
    prefix: str,
    workspace_id: str,
    current_graph_id: str,
    current: DeploymentGraph,
    desired: DeploymentGraph,
) -> PlannedLiveDeployment:
    deployment = deployment_program(database_url, {}).between(current, desired)
    prepared = deployment.plan(
        DeploymentPlanRequest(
            deployment.transition,
            workspace_id,
            current_graph_id,
            current_graph_id,
            "generated-hello-operator",
            f"Generated Hello {prefix}",
            "Approve the generated local Docker topology transition.",
            prefix,
        )
    )
    if not isinstance(prepared, ApprovalSuspension):
        raise RuntimeError(f"generated {prefix} did not reach approval suspension")
    plan_id = prepared.preparation.plan.plan_record.plan_id
    request = prepared.approval_request.request
    deployment_program(database_url, {}).for_plan(plan_id).approve(
        request.request_id,
        ApprovalGrant(
            "generated-hello-approver",
            (request.required_scope,),
            IdempotencyKey(f"{prefix}:approval-decision"),
            "Approved for the generated local Docker proof.",
        ),
    )
    return PlannedLiveDeployment(
        plan_id,
        request.request_id,
        current_graph_id,
        prepared.preparation.desired_graph.graph_version.graph_id,
        len(prepared.preparation.plan.plan_record.plan.activities),
    )


def stored_deployment(
    database_url: str,
    prefix: str,
) -> PlannedLiveDeployment:
    """Rediscover one deployment request from durable idempotency identity."""

    with psycopg.connect(database_url, autocommit=True) as connection:
        stores = PostgresStoreBundle(connection)
        sessions = tuple(
            session
            for session in stores.activity_history.sessions_for_workspace(
                WORKSPACE_ID
            )
            if session.idempotency_key == f"{prefix}:session"
        )
        if len(sessions) != 1:
            raise RuntimeError(f"expected one durable {prefix} session")
        plans = stores.activity_history.plans_for_session(sessions[0].session_id)
        approvals = stores.activity_history.approval_requests_for_session(
            sessions[0].session_id
        )
    if len(plans) != 1 or len(approvals) != 1:
        raise RuntimeError(f"durable {prefix} session is incomplete")
    plan = plans[0]
    return PlannedLiveDeployment(
        plan.plan_id,
        approvals[0].request_id,
        plan.base_graph_id,
        plan.desired_graph_id,
        len(plan.plan.activities),
    )


def deployment_program(
    database_url: str,
    graphs: dict[str, DeploymentGraph],
) -> DeploymentProgram:
    factory = lambda: PostgresUnitOfWork(lambda: psycopg.connect(database_url))
    approvals = ApprovalCommandService(
        factory,
        clock=_text_clock,
    )
    planning = PlanningServices(
        OperationCommandService(
            factory,
            clock=_text_clock,
        ),
        DesiredGraphCommandService(
            factory,
            clock=_text_clock,
        ),
        ActivityPlanningCommandService(
            factory,
            clock=_text_clock,
        ),
        approvals,
    )
    lifecycle = RunLifecycleCommandService(
        factory,
        clock=_text_clock,
    )
    return DeploymentProgram(
        DeploymentProgramServices(
            planning=planning,
            approvals=approvals,
            admission=ExecutionAdmissionCommandService(
                factory,
                clock=_text_clock,
            ),
            lifecycle=lifecycle,
            coordinator=ExecutionCoordinator(
                factory,
                lifecycle,
                effect_interpreter(graphs),
                clock=lambda: datetime.now(timezone.utc),
            ),
            advancement=CurrentGraphAdvancementCommandService(
                factory,
                clock=_text_clock,
            ),
            contexts=DeploymentPlanContextQueryService(factory),
        )
    )


def effect_interpreter(
    graphs: dict[str, DeploymentGraph],
) -> CapabilityInterpreterRegistry:
    endpoints: dict[tuple[str, str], RuntimeEndpointObservation] = {}
    authorities: set[str] = set()
    for graph_id, graph in graphs.items():
        for node_id, node in graph.nodes.items():
            endpoint = node.endpoint("internal")
            address = _probe_address(endpoint.url, endpoint.protocol)
            authorities.add(address)
            endpoints[(node_id, graph_id)] = RuntimeEndpointObservation(
                node_id,
                "internal",
                graph_id,
                endpoint.protocol,
                EndpointContext.RUNTIME_PRIVATE,
                LiteralEndpointMaterial(address),
            )
    policy = ProbeAddressPolicy(runtime_private_authorities=frozenset(authorities))
    probe = ProbeEffectInterpreter(
        StaticRuntimeEndpointProvider(endpoints),
        TcpTransportProbeAdapter(policy),
        HttpApplicationHealthProbeAdapter(policy),
        process=DockerProcessProbeAdapter(project_name=""),
    )
    docker = DockerEffectInterpreter(project_name="")
    assignments = {capability: docker for capability in docker.capabilities}
    assignments[EffectCapability.HEALTH_PROBE] = probe
    return CapabilityInterpreterRegistry(assignments)


def execution_grant(prefix: str, *, max_effects: int) -> DeploymentExecutionGrant:
    return DeploymentExecutionGrant(
        AdmissionGrant(
            "generated-hello-operator",
            ("plan:execute",),
            IdempotencyKey(f"{prefix}:admit"),
        ),
        ClaimGrant(
            WORKER,
            "2099-01-01T00:00:00Z",
            IdempotencyKey(f"{prefix}:claim"),
            IdempotencyKey(f"{prefix}:start"),
        ),
        AdvancementGrant(IdempotencyKey(f"{prefix}:advance")),
        ExecutionLimits(TimeoutPolicy(60, 1), max_effects),
    )


def _probe_address(value: str, protocol: Protocol) -> str:
    parsed = urlsplit(value)
    if parsed.hostname is None or parsed.port is None:
        raise ValueError("generated runtime endpoint has no authority")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    scheme = {
        Protocol.HTTP: parsed.scheme,
        Protocol.POSTGRES: "postgresql",
        Protocol.TCP: "tcp",
    }[protocol]
    return f"{scheme}://{host}:{parsed.port}"


def _get(url: str) -> bytes:
    response = HTTP.open(Request(url, method="GET"), timeout=10)
    with response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise RuntimeError("generated live response exceeded its bound")
    return body


def _text_clock() -> str:
    return "2026-07-18T12:00:00Z"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "prove-invalid",
            "prepare",
            "resume-deploy",
            "verify",
            "begin-teardown",
            "finish-teardown",
        ),
    )
    args = parser.parse_args()
    database_url = os.environ["CPK_GENERATED_HELLO_DATABASE_URL"]
    shape = shape_from_environment()
    if args.command == "prove-invalid":
        result = prove_invalid_graph(database_url, shape)
    elif args.command == "prepare":
        result = prepare(database_url, shape)
    elif args.command == "verify":
        result = verify_live_graph(shape)
    elif args.command == "begin-teardown":
        result = begin_teardown(database_url, shape)
    elif args.command == "resume-deploy":
        result = resume_deploy(database_url, shape)
    else:
        result = finish_teardown(database_url, shape)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
