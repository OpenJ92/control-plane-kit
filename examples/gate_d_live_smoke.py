"""Gate D live proof through planning, admission, coordination, and adapters."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from itertools import count
import json
import os
from urllib.parse import urlsplit

import psycopg

from control_plane_kit.application.deploy import (
    AdmissionGrant,
    Admit,
    Advance,
    AdvancedDeployment,
    AdvancementGrant,
    ApprovalGrant,
    ApprovalSuspension,
    Approve,
    Claim,
    ClaimGrant,
    Deploy,
    DeploymentExecutionGrant,
    DeploymentPlanRequest,
    Execute,
    ExecuteApprovedDeployment,
    ExecutionLimits,
    Plan,
    PlanningServices,
    PrepareDeployment,
)

from control_plane_kit import (
    DeploymentGraph,
    DeploymentRecipe,
    DockerRuntime,
    SocketConnection,
    compile_recipe,
)
from control_plane_kit.adapters.control_http import (
    BlockControlHttpInterpreter,
    ControlAddressPolicy,
    ControlAddressSource,
    ControlAuthority,
    ControlEndpointObservation,
    CredentialReference,
    RuntimeEndpointProvenance,
    SecretValue,
    StaticControlAuthorityProvider,
)
from control_plane_kit.secrets import (
    SecretProviderAuthority,
    SecretProviderId,
    SecretReference,
    SecretResolved,
)
from control_plane_kit.adapters.probes import (
    HttpApplicationHealthProbeAdapter,
    ProbeAddressPolicy,
    ProbeEffectInterpreter,
    StaticRuntimeEndpointProvider,
    TcpTransportProbeAdapter,
)
from control_plane_kit.docker_runtime import DockerEffectInterpreter
from control_plane_kit.effects import (
    CapabilityInterpreterRegistry,
    EffectCapability,
    EndpointContext,
    EndpointMaterial,
    LiteralEndpointMaterial,
    RuntimeEndpointObservation,
    TimeoutPolicy,
)
from control_plane_kit.servers import (
    hello_server_block,
    managed_http_router_block,
)
from control_plane_kit.stores import (
    ApprovalDecisionKind,
    GraphVersionRecord,
    PostgresStoreBundle,
    PostgresUnitOfWork,
    WorkspaceRecord,
    install_schema,
)
from control_plane_kit.types import EndpointScope, Protocol
from control_plane_kit.workflows import (
    ActivityPlanningCommandService,
    AdvanceCurrentGraph,
    ApprovalCommandService,
    ClaimAndOpenActivityRun,
    CurrentGraphAdvancementCommandService,
    CoordinatorStatus,
    DecidePlanApproval,
    DesiredGraphCommandService,
    ExecuteActivityRun,
    ExecutionAdmissionCommandService,
    ExecutionCoordinator,
    ExecutionWorkerAuthority,
    IdempotencyKey,
    OperationCommandService,
    RequestPlanExecution,
    RunLifecycleCommandService,
    StartActivityRun,
)
from examples.scenarios.workflow import plan_graph_transition


WORKSPACE_ID = "gate-d-live"
NETWORK_NAME = "cpk-gate-d-live"
CONTROL_REFERENCE = "secret://gate-d/router-control"
CONTROL_TOKEN = "gate-d-synthetic-control-token"
ROUTER_PORT = 18180
BLUE_PORT = 18101
GREEN_PORT = 18102


class Ids:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.values = count(1)

    def __call__(self) -> str:
        return f"{self.prefix}-{next(self.values)}"


@dataclass(frozen=True, repr=False)
class DemoSecrets:
    value: str = CONTROL_TOKEN

    authority = SecretProviderAuthority(SecretProviderId("gate-d"))

    def resolve(self, reference: CredentialReference) -> SecretResolved:
        if reference.reference_id != CONTROL_REFERENCE:
            raise KeyError("unknown control credential reference")
        return SecretResolved(reference, SecretValue(self.value))

    def resolve_environment(self, reference_id: str) -> str:
        if reference_id != CONTROL_REFERENCE:
            raise KeyError("unknown environment credential reference")
        return self.value

    def __repr__(self) -> str:
        return "DemoSecrets(<redacted>)"


class DockerEnvironmentSecrets:
    authority = SecretProviderAuthority(SecretProviderId("gate-d"))

    def resolve(self, reference: SecretReference) -> SecretResolved:
        return DemoSecrets().resolve(reference)


def router_recipe(active: str) -> DeploymentRecipe:
    """Return the complete blue/green graph with one selected active edge."""

    if active not in {"hello-blue", "hello-green"}:
        raise ValueError("active target must be hello-blue or hello-green")
    return DeploymentRecipe(
        f"gate-d-live-{active}",
        DockerRuntime(
            runtime_id="gate-d-runtime",
            network_name=NETWORK_NAME,
            children=(
                hello_server_block(
                    "hello-blue", message="Hello, blue!", image="control-plane-kit-live-test:local", host_port=BLUE_PORT
                ),
                hello_server_block(
                    "hello-green", message="Hello, green!", image="control-plane-kit-live-test:local", host_port=GREEN_PORT
                ),
                managed_http_router_block(
                    "router", image="control-plane-kit-live-test:local", host_port=ROUTER_PORT
                ),
                SocketConnection(
                    "hello-blue", "internal", "router", "target-blue", edge_id="router.target-blue"
                ),
                SocketConnection(
                    "hello-green", "internal", "router", "target-green", edge_id="router.target-green"
                ),
                SocketConnection(
                    active, "internal", "router", "active", edge_id="router.active"
                ),
            ),
        ),
    )


def empty_graph() -> DeploymentGraph:
    return DeploymentGraph("gate-d-empty")


def _unit_of_work_factory(database_url: str):
    return lambda: PostgresUnitOfWork(lambda: psycopg.connect(database_url))


def _planning_services(database_url: str, prefix: str) -> PlanningServices:
    factory = _unit_of_work_factory(database_url)
    return PlanningServices(
        OperationCommandService(factory, clock=_clock, id_factory=Ids(f"{prefix}-operation")),
        DesiredGraphCommandService(factory, clock=_clock, id_factory=Ids(f"{prefix}-graph")),
        ActivityPlanningCommandService(factory, clock=_clock, id_factory=Ids(f"{prefix}-plan")),
        ApprovalCommandService(factory, clock=_clock, id_factory=Ids(f"{prefix}-approval")),
    )


def _clock() -> str:
    return "2026-07-17T12:00:00Z"


def initialize(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        install_schema(connection)
        stores = PostgresStoreBundle(connection)
        try:
            stores.workspace.get(WORKSPACE_ID)
            return
        except KeyError:
            pass
        stores.workspace.create(WorkspaceRecord(WORKSPACE_ID, "Gate D live smoke"))
        stores.graph_topology.save(
            GraphVersionRecord.from_graph(
                graph_id="graph-empty",
                workspace_id=WORKSPACE_ID,
                version=1,
                graph=empty_graph(),
                created_by="gate-d-operator",
                created_at=_clock(),
            )
        )
        stores.workspace.set_current_graph(WORKSPACE_ID, "graph-empty")
        stores.workspace.set_desired_graph(WORKSPACE_ID, "graph-empty")


def plan_approve_admit_open(
    database_url: str,
    *,
    prefix: str,
    current_graph_id: str,
    desired_graph: DeploymentGraph,
) -> tuple[str, str, str, int]:
    services = _planning_services(database_url, prefix)
    planned = plan_graph_transition(
        services,
        workspace_id=WORKSPACE_ID,
        actor_id="gate-d-operator",
        title=f"Gate D {prefix}",
        approval_comment="Gate D synthetic live proof",
        current_graph_id=current_graph_id,
        expected_desired_graph_id=current_graph_id,
        desired_graph=desired_graph,
        idempotency_prefix=prefix,
    )
    assert planned.approval is not None
    approval = services.approvals.execute(
        DecidePlanApproval(
            planned.session.session.session_id,
            planned.approval.request.request_id,
            "gate-d-approver",
            ("plan:approve", "plan:approve-destructive"),
            ApprovalDecisionKind.APPROVED,
            IdempotencyKey(f"{prefix}:approval-decision"),
            "Approved for the local Gate D smoke.",
        )
    )
    factory = _unit_of_work_factory(database_url)
    admitted = ExecutionAdmissionCommandService(
        factory, clock=_clock, id_factory=Ids(f"{prefix}-admission")
    ).execute(
        RequestPlanExecution(
            WORKSPACE_ID,
            planned.session.session.session_id,
            planned.plan.plan_record.plan_id,
            planned.approval.request.request_id,
            "gate-d-operator",
            ("plan:execute",),
            IdempotencyKey(f"{prefix}:admit"),
        )
    )
    authority = ExecutionWorkerAuthority("gate-d-worker", ("execution:operate",))
    lifecycle = RunLifecycleCommandService(
        factory, clock=_clock, id_factory=Ids(f"{prefix}-run")
    )
    opened = lifecycle.execute(
        ClaimAndOpenActivityRun(
            admitted.request.identity.request_id,
            authority,
            "2026-07-17T13:00:00Z",
            IdempotencyKey(f"{prefix}:claim"),
        )
    )
    lifecycle.execute(
        StartActivityRun(
            opened.run.run_id,
            authority,
            IdempotencyKey(f"{prefix}:start"),
        )
    )
    return (
        opened.run.run_id,
        planned.plan.plan_record.plan_id,
        planned.desired_graph.graph_version.graph_id,
        len(planned.plan.plan_record.plan.activities),
    )


def _interpreter(graph_ids: tuple[str, ...]) -> CapabilityInterpreterRegistry:
    graphs = tuple(router_recipe(active) for active in ("hello-blue", "hello-green"))
    endpoints = {}
    private_authorities = set()
    for graph_id, recipe in zip(graph_ids, graphs, strict=False):
        graph = compile_recipe(recipe)
        for node_id in graph.nodes:
            endpoint = graph.node(node_id).endpoint("internal")
            private_authorities.add(_origin(endpoint.url))
            endpoints[(node_id, graph_id)] = RuntimeEndpointObservation(
                node_id,
                "internal",
                graph_id,
                Protocol.HTTP,
                EndpointContext.RUNTIME_PRIVATE,
                LiteralEndpointMaterial(endpoint.url),
            )
    probe_policy = ProbeAddressPolicy(
        runtime_private_authorities=frozenset(private_authorities)
    )
    probe = ProbeEffectInterpreter(
        StaticRuntimeEndpointProvider(endpoints),
        TcpTransportProbeAdapter(probe_policy),
        HttpApplicationHealthProbeAdapter(probe_policy),
    )
    router_endpoint = compile_recipe(router_recipe("hello-blue")).node("router").endpoint("internal")
    control = BlockControlHttpInterpreter(
        StaticControlAuthorityProvider(
            {
                "router": ControlAuthority(
                    ControlEndpointObservation(
                        "router",
                        EndpointMaterial(
                            "internal",
                            Protocol.HTTP,
                            EndpointScope.PRIVATE,
                            LiteralEndpointMaterial(router_endpoint.url),
                        ),
                        RuntimeEndpointProvenance(
                            ControlAddressSource.DOCKER_PRIVATE,
                            "gate-d-runtime",
                            NETWORK_NAME,
                        ),
                    ),
                    CredentialReference(CONTROL_REFERENCE),
                )
            }
        ),
        DemoSecrets(),
        ControlAddressPolicy(docker_networks=frozenset({NETWORK_NAME})),
    )
    docker = DockerEffectInterpreter(
        project_name="",
        secrets=DockerEnvironmentSecrets(),
    )
    assignments = {capability: docker for capability in docker.capabilities}
    assignments[EffectCapability.HEALTH_PROBE] = probe
    for capability in control.capabilities - {EffectCapability.HEALTH_PROBE}:
        assignments[capability] = control
    return CapabilityInterpreterRegistry(assignments)


def execute_run(
    database_url: str,
    run_id: str,
    *,
    graph_ids: tuple[str, ...],
    max_effects: int,
):
    factory = _unit_of_work_factory(database_url)
    authority = ExecutionWorkerAuthority("gate-d-worker", ("execution:operate",))
    lifecycle = RunLifecycleCommandService(factory, clock=_clock)
    coordinator = ExecutionCoordinator(
        factory,
        lifecycle,
        _interpreter(graph_ids),
        clock=lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    )
    return coordinator.execute(
        ExecuteActivityRun(
            run_id,
            authority,
            timeout=TimeoutPolicy(30, 1),
            max_effects=max_effects,
        )
    )


def advance(database_url: str, run_id: str, plan_id: str, current_id: str, desired_id: str, prefix: str) -> None:
    CurrentGraphAdvancementCommandService(
        _unit_of_work_factory(database_url), clock=_clock, id_factory=Ids(f"{prefix}-advance")
    ).execute(
        AdvanceCurrentGraph(
            WORKSPACE_ID,
            run_id,
            plan_id,
            current_id,
            desired_id,
            ExecutionWorkerAuthority("gate-d-worker", ("execution:operate",)),
            IdempotencyKey(f"{prefix}:advance"),
        )
    )


def prepare(database_url: str) -> dict[str, str]:
    initialize(database_url)
    run_id, plan_id, graph_id, _activity_count = plan_approve_admit_open(
        database_url,
        prefix="deploy",
        current_graph_id="graph-empty",
        desired_graph=compile_recipe(router_recipe("hello-blue")),
    )
    result = execute_run(database_url, run_id, graph_ids=(graph_id,), max_effects=1)
    if result.status is not CoordinatorStatus.PROGRESSED:
        raise RuntimeError(f"runtime preparation did not make bounded progress: {result.status.value}")
    return {"run_id": run_id, "plan_id": plan_id, "graph_id": graph_id}


def resume_deploy(database_url: str, run_id: str, plan_id: str, graph_id: str) -> dict[str, str]:
    deployed = execute_run(database_url, run_id, graph_ids=(graph_id,), max_effects=100)
    if deployed.status is not CoordinatorStatus.COMPLETED:
        raise RuntimeError(
            "deployment did not complete: "
            f"status={deployed.status.value} activity={deployed.activity_id!r} "
            f"failure={_latest_failure(database_url, run_id)!r}"
        )
    advance(database_url, run_id, plan_id, "graph-empty", graph_id, "deploy")
    return {
        "run_id": run_id,
        "plan_id": plan_id,
        "graph_id": graph_id,
        "router_url": f"http://127.0.0.1:{ROUTER_PORT}/",
    }


def switch(database_url: str, graph_id: str) -> dict[str, str]:
    current = compile_recipe(router_recipe("hello-blue"))
    desired = compile_recipe(router_recipe("hello-green"))
    factory = _unit_of_work_factory(database_url)
    planning = _planning_services(database_url, "switch")
    lifecycle = RunLifecycleCommandService(
        factory,
        clock=_clock,
        id_factory=Ids("switch-run"),
    )
    deploy = Deploy(
        current,
        desired,
        PrepareDeployment(Plan(planning)),
        Approve(planning.approvals),
        ExecuteApprovedDeployment(
            Admit(
                ExecutionAdmissionCommandService(
                    factory,
                    clock=_clock,
                    id_factory=Ids("switch-admission"),
                )
            ),
            Claim(lifecycle),
            Execute(
                ExecutionCoordinator(
                    factory,
                    lifecycle,
                    _interpreter((graph_id, "switch-graph-1")),
                    clock=lambda: __import__("datetime").datetime.now(
                        __import__("datetime").timezone.utc
                    ),
                    id_factory=Ids("switch-coordinator"),
                )
            ),
            Advance(
                CurrentGraphAdvancementCommandService(
                    factory,
                    clock=_clock,
                    id_factory=Ids("switch-advance"),
                )
            ),
        ),
    )
    prepared = deploy(
        DeploymentPlanRequest(
            deploy.transition,
            WORKSPACE_ID,
            graph_id,
            graph_id,
            "gate-d-operator",
            "Gate F live router switch",
            "Approve the authenticated blue-to-green router mutation.",
            "switch",
        )
    )
    if not isinstance(prepared, ApprovalSuspension):
        raise RuntimeError("live switch did not reach approval suspension")
    approved = deploy.approve(
        prepared,
        ApprovalGrant(
            "gate-d-approver",
            (prepared.approval_request.request.required_scope,),
            IdempotencyKey("switch:approval-decision"),
            "Approved for the local Gate F smoke.",
        ),
    )
    switched = deploy.execute_approved(
        approved,
        DeploymentExecutionGrant(
            AdmissionGrant(
                "gate-d-operator",
                ("plan:execute",),
                IdempotencyKey("switch:admit"),
            ),
            ClaimGrant(
                ExecutionWorkerAuthority("gate-d-worker", ("execution:operate",)),
                "2026-07-17T13:00:00Z",
                IdempotencyKey("switch:claim"),
                IdempotencyKey("switch:start"),
            ),
            AdvancementGrant(IdempotencyKey("switch:advance")),
            ExecutionLimits(TimeoutPolicy(30, 1), 100),
        ),
    )
    if not isinstance(switched, AdvancedDeployment):
        raise RuntimeError(f"live switch suspended with {type(switched).__name__}")
    return {
        "run_id": switched.executed.execution.run.run_id,
        "plan_id": switched.advancement.plan_id,
        "graph_id": switched.advancement.to_graph_id,
        "router_url": f"http://127.0.0.1:{ROUTER_PORT}/",
    }


def begin_teardown(database_url: str, graph_id: str) -> dict[str, str]:
    run_id, plan_id, empty_id, activity_count = plan_approve_admit_open(
        database_url,
        prefix="teardown",
        current_graph_id=graph_id,
        desired_graph=empty_graph(),
    )
    result = execute_run(
        database_url,
        run_id,
        graph_ids=(graph_id,),
        max_effects=activity_count - 1,
    )
    if result.status is not CoordinatorStatus.PROGRESSED:
        raise RuntimeError(
            "teardown did not pause before runtime removal: "
            f"status={result.status.value} activity={result.activity_id!r} "
            f"failure={_latest_failure(database_url, run_id)!r}"
        )
    return {"run_id": run_id, "plan_id": plan_id, "graph_id": empty_id}


def finish_teardown(database_url: str, run_id: str, plan_id: str, current_id: str, empty_id: str) -> dict[str, str]:
    result = execute_run(database_url, run_id, graph_ids=(current_id,), max_effects=10)
    if result.status is not CoordinatorStatus.COMPLETED:
        raise RuntimeError(
            "teardown did not complete: "
            f"status={result.status.value} activity={result.activity_id!r} "
            f"failure={_latest_failure(database_url, run_id)!r}"
        )
    advance(database_url, run_id, plan_id, current_id, empty_id, "teardown")
    return {"run_id": run_id, "plan_id": plan_id, "graph_id": empty_id}


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"


def _latest_failure(database_url: str, run_id: str) -> object:
    with psycopg.connect(database_url, autocommit=True) as connection:
        events = PostgresStoreBundle(connection).execution.events_for_run(run_id)
    failures = [event.failure for event in events if event.failure is not None]
    return failures[-1] if failures else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=("prepare", "resume-deploy", "switch", "begin-teardown", "finish-teardown"),
    )
    parser.add_argument("--run-id")
    parser.add_argument("--plan-id")
    parser.add_argument("--graph-id")
    args = parser.parse_args()
    database_url = os.environ["CPK_GATE_D_DATABASE_URL"]
    if args.command == "prepare":
        result = prepare(database_url)
    elif args.command == "resume-deploy":
        if not args.run_id or not args.plan_id or not args.graph_id:
            parser.error("resume-deploy requires --run-id, --plan-id, and --graph-id")
        result = resume_deploy(database_url, args.run_id, args.plan_id, args.graph_id)
    elif args.command == "switch":
        if not args.graph_id:
            parser.error("switch requires --graph-id")
        result = switch(database_url, args.graph_id)
    elif args.command == "begin-teardown":
        if not args.graph_id:
            parser.error("begin-teardown requires --graph-id")
        result = begin_teardown(database_url, args.graph_id)
    else:
        if not args.run_id or not args.plan_id or not args.graph_id:
            parser.error("finish-teardown requires --run-id, --plan-id, and --graph-id")
        result = finish_teardown(
            database_url,
            args.run_id,
            args.plan_id,
            "switch-graph-1",
            args.graph_id,
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
