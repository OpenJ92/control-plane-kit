"""Live canonical graph proof for Postgres secret-file credentials."""

from __future__ import annotations

import sys

import psycopg

from control_plane_kit.adapters.probes import (
    HttpApplicationHealthProbeAdapter,
    ProbeAddressPolicy,
    ProbeEffectInterpreter,
    StaticRuntimeEndpointProvider,
    TcpTransportProbeAdapter,
)
from control_plane_kit.effects import (
    CapabilityInterpreterRegistry,
    EffectInterpreter,
    MaterializedEffectRequest,
)
from control_plane_kit import (
    BlockSockets,
    BlockSpec,
    DataBlock,
    DeploymentGraph,
    DeploymentRecipe,
    DockerEffectInterpreter,
    DockerProcessProbeAdapter,
    DockerImageImplementation,
    DockerRuntime,
    EffectFailed,
    EffectCapability,
    EffectSucceeded,
    EndpointContext,
    LiteralEndpointMaterial,
    LocalDevelopmentSecretResolver,
    PinnedGraphSet,
    Protocol,
    PublicStaticEnvironmentBinding,
    ProviderSocket,
    RuntimeEndpointObservation,
    SecretFileDelivery,
    SecretFilePathBinding,
    SecretProviderAuthority,
    SecretProviderId,
    SecretReference,
    StartNode,
    StartRuntime,
    TimeoutPolicy,
    compile_activity_plan,
    compile_recipe,
    diff_graphs,
    effect_request_for_activity,
    materialize_effect_request,
    require_resolved_secret,
    validate_graph,
)


PROJECT = "cpk-live-secret"
REFERENCE = SecretReference("secret://live/postgres/password")
FIXTURE_VALUE = "live-secret-fixture-value"
SERVICE_NAME = "docker-postgres"


def main(mode: str) -> None:
    if mode == "denied":
        result = DockerEffectInterpreter(
            project_name="",
            secrets=LocalDevelopmentSecretResolver(
                SecretProviderAuthority(SecretProviderId("different")),
                {},
            ),
        ).execute(_materialized_start_node())
        if not isinstance(result, EffectFailed):
            raise RuntimeError("unauthorized secret reference did not fail closed")
        if result.failure.code != "docker.secret-denied":
            raise RuntimeError("unauthorized secret reference returned the wrong failure")
        return

    resolver = LocalDevelopmentSecretResolver(
        SecretProviderAuthority(SecretProviderId("live")),
        {REFERENCE.reference_id: FIXTURE_VALUE},
    )
    if mode == "verify":
        password = require_resolved_secret(resolver, REFERENCE)
        with psycopg.connect(
            host=SERVICE_NAME,
            dbname="cpk",
            user="cpk",
            password=password.reveal(),
            connect_timeout=5,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                if cursor.fetchone() != (1,):
                    raise RuntimeError("Postgres credentialed query returned wrong result")
        return

    docker = DockerEffectInterpreter(
        project_name="",
        secrets=resolver,
    )
    interpreter = _effect_interpreter(docker)
    if mode == "bootstrap":
        _bootstrap_runtime(interpreter, _empty_graph(), _product_graph())
        return
    if mode == "start":
        _execute_transition(interpreter, _empty_graph(), _product_graph())
        return
    if mode == "cleanup":
        _execute_transition(interpreter, _product_graph(), _empty_graph())
        return
    raise ValueError(f"unknown live Docker mode: {mode}")


def _recipe() -> DeploymentRecipe:
    postgres = DataBlock(
        BlockSpec("postgres", "Credentialed Postgres"),
        DockerImageImplementation(
            image="postgres:16-alpine",
            ports={"internal": 5432},
            environment=(
                PublicStaticEnvironmentBinding("POSTGRES_DB", "cpk"),
                PublicStaticEnvironmentBinding("POSTGRES_USER", "cpk"),
            ),
            secret_deliveries=(
                SecretFileDelivery(
                    "/run/secrets/postgres-password",
                    REFERENCE,
                    path_binding=SecretFilePathBinding("POSTGRES_PASSWORD_FILE"),
                ),
            ),
        ),
        BlockSockets(providers=(ProviderSocket("internal", Protocol.POSTGRES),)),
    )
    return DeploymentRecipe(
        "postgres-secret-product",
        DockerRuntime(
            network_name=f"{PROJECT}-network",
            children=(postgres,),
        ),
    )


def _empty_graph() -> DeploymentGraph:
    return DeploymentGraph("postgres-secret-product")


def _product_graph() -> DeploymentGraph:
    return compile_recipe(_recipe())


def _materialized_start_node() -> MaterializedEffectRequest:
    current = _empty_graph()
    desired = _product_graph()
    plan = compile_activity_plan(
        diff_graphs(validate_graph(current), validate_graph(desired))
    )
    activity = next(
        value for value in plan.activities if isinstance(value.operation, StartNode)
    )
    request = effect_request_for_activity(
        activity,
        run_id="secret-denied-run",
        attempt=1,
        idempotency_key="secret-denied:start-node:1",
    )
    return materialize_effect_request(
        request,
        activity,
        PinnedGraphSet("secret-workspace", "secret-plan", "base", "desired"),
        base_graph_id="base",
        base_graph=current,
        desired_graph_id="desired",
        desired_graph=desired,
    )


def _effect_interpreter(
    docker: DockerEffectInterpreter,
) -> CapabilityInterpreterRegistry:
    graph = _product_graph()
    endpoint = graph.node("postgres").endpoint("internal")
    runtime_endpoint = RuntimeEndpointObservation(
        "postgres",
        "internal",
        "desired",
        endpoint.protocol,
        EndpointContext.RUNTIME_PRIVATE,
        LiteralEndpointMaterial(endpoint.url),
    )
    policy = ProbeAddressPolicy(
        runtime_private_authorities=frozenset({endpoint.url})
    )
    probe = ProbeEffectInterpreter(
        StaticRuntimeEndpointProvider(
            {("postgres", "desired"): runtime_endpoint}
        ),
        TcpTransportProbeAdapter(policy),
        HttpApplicationHealthProbeAdapter(policy),
        process=DockerProcessProbeAdapter(project_name=""),
    )
    assignments = {capability: docker for capability in docker.capabilities}
    assignments[EffectCapability.HEALTH_PROBE] = probe
    return CapabilityInterpreterRegistry(assignments)


def _execute_transition(
    interpreter: EffectInterpreter,
    current: DeploymentGraph,
    desired: DeploymentGraph,
) -> None:
    plan = compile_activity_plan(
        diff_graphs(validate_graph(current), validate_graph(desired))
    )
    graphs = PinnedGraphSet("secret-workspace", "secret-plan", "base", "desired")
    for activity in plan.activities:
        request = effect_request_for_activity(
            activity,
            run_id="secret-live-run",
            attempt=1,
            idempotency_key=f"secret-live:{activity.activity_id.value}:1",
            timeout=TimeoutPolicy(30, 1),
        )
        materialized = materialize_effect_request(
            request,
            activity,
            graphs,
            base_graph_id="base",
            base_graph=current,
            desired_graph_id="desired",
            desired_graph=desired,
        )
        result = interpreter.execute(materialized)
        if not isinstance(result, EffectSucceeded):
            raise RuntimeError(
                f"live Docker Postgres secret effect failed: {result!r}"
            )


def _bootstrap_runtime(
    interpreter: EffectInterpreter,
    current: DeploymentGraph,
    desired: DeploymentGraph,
) -> None:
    plan = compile_activity_plan(
        diff_graphs(validate_graph(current), validate_graph(desired))
    )
    activity = next(
        value for value in plan.activities if isinstance(value.operation, StartRuntime)
    )
    request = effect_request_for_activity(
        activity,
        run_id="secret-bootstrap-run",
        attempt=1,
        idempotency_key="secret-live:bootstrap-runtime:1",
    )
    materialized = materialize_effect_request(
        request,
        activity,
        PinnedGraphSet("secret-workspace", "secret-plan", "base", "desired"),
        base_graph_id="base",
        base_graph=current,
        desired_graph_id="desired",
        desired_graph=desired,
    )
    result = interpreter.execute(materialized)
    if not isinstance(result, EffectSucceeded):
        raise RuntimeError(f"live Docker runtime bootstrap failed: {result!r}")


if __name__ == "__main__":
    main(sys.argv[1])
