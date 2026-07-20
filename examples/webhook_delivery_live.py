"""Canonical DeploymentProgram proof for the packaged webhook server."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import psycopg

from control_plane_kit import (
    ApplicationBlock,
    BlockSockets,
    BlockSpec,
    DataBlock,
    DeploymentRecipe,
    DockerImageImplementation,
    DockerRuntime,
    LocalDevelopmentSecretResolver,
    Protocol,
    ProviderSocket,
    PublicStaticEnvironmentBinding,
    SecretEnvironmentDelivery,
    SecretProviderAuthority,
    SecretProviderId,
    SecretReference,
    SocketConnection,
    compile_recipe,
)
from control_plane_kit.servers import (
    webhook_delivery_block,
)
from control_plane_kit.domains.webhook import (
    WebhookEndpointGrant,
    WebhookEndpointScope,
)
from control_plane_kit.domains.webhook import (
    WebhookContentType,
    WebhookDeliveryIdentity,
    WebhookDeliveryIntent,
    WebhookEndpoint,
    WebhookPayload,
    WebhookRetryPolicy,
    WebhookSigning,
)
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
from control_plane_kit.docker_runtime import DockerEffectInterpreter, DockerProcessProbeAdapter
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
from control_plane_kit.workflows import (
    ActivityPlanningCommandService,
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


WORKSPACE_ID = "webhook-live"
RUNTIME_ID = "webhook-live-runtime"
EMPTY_GRAPH_ID = "webhook-live-empty"
IMAGE = os.environ.get("CPK_WEBHOOK_LIVE_IMAGE", "control-plane-kit-live-test:webhook")
IDENTITY_TOKEN = "webhook-live-attestation"
SIGNING_SECRET = "webhook-live-signing-secret"
IDENTITY_REFERENCE_V1 = "secret://webhook-delivery/identity-attestation-v1"
IDENTITY_REFERENCE_V2 = "secret://webhook-delivery/identity-attestation-v2"
SIGNING_REFERENCE = "secret://webhook-delivery/signing-key"
RECEIVER_SIGNING_REFERENCE = "secret://webhook-delivery/receiver-signing-key"
WORKER = ExecutionWorkerAuthority("webhook-live-worker", ("execution:operate",))


@dataclass(frozen=True)
class StoredPlan:
    plan_id: str
    approval_request_id: str
    current_graph_id: str
    desired_graph_id: str
    activity_count: int


def empty_graph() -> DeploymentGraph:
    return DeploymentGraph("webhook-live-empty")


def desired_graph(
    identity_reference: str = IDENTITY_REFERENCE_V1,
) -> DeploymentGraph:
    receiver_url = f"http://{RUNTIME_ID}-receiver:8090/hook"
    postgres = DataBlock(
        BlockSpec("postgres", "Ephemeral webhook Postgres"),
        DockerImageImplementation(
            image="postgres:16-alpine",
            ports={"internal": 5432},
            environment=(
                PublicStaticEnvironmentBinding("POSTGRES_DB", "root"),
                PublicStaticEnvironmentBinding("POSTGRES_USER", "root"),
                PublicStaticEnvironmentBinding("POSTGRES_HOST_AUTH_METHOD", "trust"),
            ),
        ),
        BlockSockets(providers=(ProviderSocket("internal", Protocol.POSTGRES),)),
    )
    receiver = ApplicationBlock(
        BlockSpec("receiver", "Controlled signed receiver", health_path="/health"),
        DockerImageImplementation(
            image=IMAGE,
            command=("python", "-m", "tests.fixtures.webhook_receiver"),
            ports={"internal": 8090},
            secret_deliveries=(
                SecretEnvironmentDelivery(
                    "CPK_WEBHOOK_RECEIVER_SECRET",
                    SecretReference(RECEIVER_SIGNING_REFERENCE),
                ),
            ),
        ),
        BlockSockets(providers=(ProviderSocket("internal", Protocol.HTTP),)),
    )
    webhook = webhook_delivery_block(
        endpoint_grants=(
            WebhookEndpointGrant(
                "receiver",
                receiver_url,
                WebhookEndpointScope.RUNTIME_PRIVATE,
            ),
        ),
        image=IMAGE,
        identity_secret_reference=identity_reference,
    )
    return compile_recipe(
        DeploymentRecipe(
            "webhook-live",
            DockerRuntime(
                runtime_id=RUNTIME_ID,
                network_name=RUNTIME_ID,
                children=(
                    postgres,
                    receiver,
                    webhook,
                    SocketConnection(
                        "postgres",
                        "internal",
                        "webhook-delivery",
                        "database",
                    ),
                ),
            ),
        )
    )


def initialize(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        install_schema(connection)
        stores = PostgresStoreBundle(connection)
        try:
            stores.workspace.get(WORKSPACE_ID)
            return
        except KeyError:
            pass
        stores.workspace.create(WorkspaceRecord(WORKSPACE_ID, WORKSPACE_ID))
        stores.graph_topology.save(
            GraphVersionRecord.from_graph(
                graph_id=EMPTY_GRAPH_ID,
                workspace_id=WORKSPACE_ID,
                version=1,
                graph=empty_graph(),
                created_by="webhook-live-operator",
                created_at=_clock(),
            )
        )
        stores.workspace.set_current_graph(WORKSPACE_ID, EMPTY_GRAPH_ID)
        stores.workspace.set_desired_graph(WORKSPACE_ID, EMPTY_GRAPH_ID)


def prepare(database_url: str) -> None:
    initialize(database_url)
    planned = _plan_and_approve(database_url, "deploy", EMPTY_GRAPH_ID, empty_graph(), desired_graph())
    result = _program(database_url, {planned.desired_graph_id: desired_graph()}).for_plan(
        planned.plan_id
    ).run(planned.approval_request_id, _grant("deploy", 1))
    if not isinstance(result, ExecutionContinuation):
        raise RuntimeError("webhook deployment must pause after runtime creation")


def resume_deploy(database_url: str) -> None:
    planned = _stored(database_url, "deploy")
    result = _program(database_url, {planned.desired_graph_id: desired_graph()}).for_plan(
        planned.plan_id
    ).run(planned.approval_request_id, _grant("deploy", 64))
    if not isinstance(result, AdvancedDeployment):
        raise RuntimeError("webhook deployment did not advance")


def verify_before_restart() -> None:
    base = f"http://{RUNTIME_ID}-webhook-delivery:8080"
    intent = _delivery_intent()
    unauthorized = _request(f"{base}/__deploy/webhooks", intent.descriptor(), authorized=False)
    if unauthorized[0] != 401:
        raise RuntimeError("unauthorized webhook enqueue did not fail closed")
    first = _request(f"{base}/__deploy/webhooks", intent.descriptor())[1]
    if first["replayed"] or first["delivery"]["status"] != "queued":
        raise RuntimeError("webhook enqueue did not create durable queued intent")
    print("Webhook pre-restart proof passed: unauthorized rejected and intent persisted.")


def restart_webhook(database_url: str) -> None:
    deployed = _stored(database_url, "deploy")
    current = desired_graph(IDENTITY_REFERENCE_V1)
    desired = desired_graph(IDENTITY_REFERENCE_V2)
    planned = _plan_and_approve(
        database_url,
        "restart",
        deployed.desired_graph_id,
        current,
        desired,
    )
    result = _program(
        database_url,
        {
            deployed.desired_graph_id: current,
            planned.desired_graph_id: desired,
        },
    ).for_plan(planned.plan_id).run(
        planned.approval_request_id,
        _grant("restart", 16),
    )
    if isinstance(result, ExecutionContinuation):
        result = _program(
            database_url,
            {
                deployed.desired_graph_id: current,
                planned.desired_graph_id: desired,
            },
        ).for_plan(planned.plan_id).run(
            planned.approval_request_id,
            _grant("restart", 16),
        )
    if not isinstance(result, AdvancedDeployment):
        raise RuntimeError("webhook identity-reference reconciliation did not advance")


def verify_after_restart() -> None:
    base = f"http://{RUNTIME_ID}-webhook-delivery:8080"
    receiver = f"http://{RUNTIME_ID}-receiver:8090"
    intent = _delivery_intent()
    replay = _request(f"{base}/__deploy/webhooks", intent.descriptor())[1]
    if not replay["replayed"] or replay["delivery"]["status"] != "queued":
        raise RuntimeError("restarted webhook process did not reconstruct exact replay")
    claimed = _request(
        f"{base}/__deploy/webhooks/delivery-live/claims",
        {"command_id": "claim-live", "worker_id": "worker-live", "lease_seconds": 60},
    )[1]
    claim_id = claimed["delivery"]["active_claim"]["claim_id"]
    dispatched = _request(
        f"{base}/__deploy/webhooks/delivery-live/dispatch",
        {"command_id": "dispatch-live", "claim_id": claim_id, "worker_id": "worker-live"},
    )[1]
    if dispatched["delivery"]["status"] != "delivered":
        raise RuntimeError("allowed signed webhook was not delivered")
    read = _request(f"{base}/__deploy/webhooks/delivery-live", method="GET")[1]
    received = _request(f"{receiver}/received/delivery-live", method="GET", authorized=False)[1]
    if read["delivery"]["status"] != "delivered" or not received["signature_valid"]:
        raise RuntimeError("durable delivery or signature evidence is incorrect")

    denied_intent = WebhookDeliveryIntent(
        "enqueue-denied",
        WebhookDeliveryIdentity(WORKSPACE_ID, "delivery-denied"),
        WebhookEndpoint("not-granted", f"{receiver}/hook"),
        intent.payload,
        WebhookRetryPolicy(max_attempts=1, deadline_seconds=300),
        _intent_created_at(),
    )
    _request(f"{base}/__deploy/webhooks", denied_intent.descriptor())
    denied_claim = _request(
        f"{base}/__deploy/webhooks/delivery-denied/claims",
        {"command_id": "claim-denied", "worker_id": "worker-live", "lease_seconds": 60},
    )[1]
    denied_claim_id = denied_claim["delivery"]["active_claim"]["claim_id"]
    denied = _request(
        f"{base}/__deploy/webhooks/delivery-denied/dispatch",
        {"command_id": "dispatch-denied", "claim_id": denied_claim_id, "worker_id": "worker-live"},
    )[1]
    if (
        denied["delivery"]["status"] != "dead-letter"
        or denied["delivery"]["last_outcome"] != "terminal-failure"
    ):
        raise RuntimeError("disallowed webhook endpoint did not fail closed")
    print(
        "Webhook post-restart proof passed: replay, signature, durable history, "
        "and allowlist."
    )


def begin_teardown(database_url: str) -> None:
    restarted = _stored(database_url, "restart")
    current = desired_graph(IDENTITY_REFERENCE_V2)
    planned = _plan_and_approve(
        database_url,
        "teardown",
        restarted.desired_graph_id,
        current,
        empty_graph(),
    )
    result = _program(
        database_url,
        {
            restarted.desired_graph_id: current,
            planned.desired_graph_id: empty_graph(),
        },
    ).for_plan(planned.plan_id).run(
        planned.approval_request_id,
        _grant("teardown", planned.activity_count - 1),
    )
    if not isinstance(result, ExecutionContinuation):
        raise RuntimeError("webhook teardown must pause before network removal")


def finish_teardown(database_url: str) -> None:
    planned = _stored(database_url, "teardown")
    result = _program(
        database_url,
        {
            planned.current_graph_id: desired_graph(IDENTITY_REFERENCE_V2),
            planned.desired_graph_id: empty_graph(),
        },
    ).for_plan(planned.plan_id).run(planned.approval_request_id, _grant("teardown", 16))
    if not isinstance(result, AdvancedDeployment):
        raise RuntimeError("webhook teardown did not advance")


def _plan_and_approve(
    database_url: str,
    prefix: str,
    current_graph_id: str,
    current: DeploymentGraph,
    desired: DeploymentGraph,
) -> StoredPlan:
    deployment = _program(database_url, {}).between(current, desired)
    prepared = deployment.plan(
        DeploymentPlanRequest(
            deployment.transition,
            WORKSPACE_ID,
            current_graph_id,
            current_graph_id,
            "webhook-live-operator",
            f"Webhook live {prefix}",
            "Approve the canonical webhook live transition.",
            prefix,
        )
    )
    if not isinstance(prepared, ApprovalSuspension):
        raise RuntimeError("webhook transition did not suspend for approval")
    request = prepared.approval_request.request
    plan_id = prepared.preparation.plan.plan_record.plan_id
    _program(database_url, {}).for_plan(plan_id).approve(
        request.request_id,
        ApprovalGrant(
            "webhook-live-approver",
            (request.required_scope,),
            IdempotencyKey(f"{prefix}:approval-decision"),
            "Approved for live webhook acceptance.",
        ),
    )
    return StoredPlan(
        plan_id,
        request.request_id,
        current_graph_id,
        prepared.preparation.desired_graph.graph_version.graph_id,
        len(prepared.preparation.plan.plan_record.plan.activities),
    )


def _stored(database_url: str, prefix: str) -> StoredPlan:
    with psycopg.connect(database_url, autocommit=True) as connection:
        stores = PostgresStoreBundle(connection)
        sessions = tuple(
            value
            for value in stores.activity_history.sessions_for_workspace(WORKSPACE_ID)
            if value.idempotency_key == f"{prefix}:session"
        )
        if len(sessions) != 1:
            raise RuntimeError("webhook live deployment session is missing")
        plans = stores.activity_history.plans_for_session(sessions[0].session_id)
        approvals = stores.activity_history.approval_requests_for_session(sessions[0].session_id)
    plan = plans[0]
    return StoredPlan(
        plan.plan_id,
        approvals[0].request_id,
        plan.base_graph_id,
        plan.desired_graph_id,
        len(plan.plan.activities),
    )


def _program(database_url: str, graphs: dict[str, DeploymentGraph]) -> DeploymentProgram:
    factory = lambda: PostgresUnitOfWork(lambda: psycopg.connect(database_url))
    approvals = ApprovalCommandService(factory, clock=_clock)
    lifecycle = RunLifecycleCommandService(factory, clock=_clock)
    planning = PlanningServices(
        OperationCommandService(factory, clock=_clock),
        DesiredGraphCommandService(factory, clock=_clock),
        ActivityPlanningCommandService(factory, clock=_clock),
        approvals,
    )
    return DeploymentProgram(
        DeploymentProgramServices(
            planning,
            approvals,
            ExecutionAdmissionCommandService(factory, clock=_clock),
            lifecycle,
            ExecutionCoordinator(
                factory,
                lifecycle,
                _effects(graphs),
                clock=lambda: datetime.now(timezone.utc),
            ),
            CurrentGraphAdvancementCommandService(factory, clock=_clock),
            DeploymentPlanContextQueryService(factory),
        )
    )


def _effects(graphs: dict[str, DeploymentGraph]) -> CapabilityInterpreterRegistry:
    endpoints: dict[tuple[str, str], RuntimeEndpointObservation] = {}
    authorities: set[str] = set()
    for graph_id, graph in graphs.items():
        for node_id, node in graph.nodes.items():
            for socket_name, endpoint in node.endpoints.items():
                address = _probe_address(endpoint.url, endpoint.protocol)
                authorities.add(address)
                endpoints[(node_id, graph_id)] = RuntimeEndpointObservation(
                    node_id,
                    socket_name,
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
    secrets = LocalDevelopmentSecretResolver(
        SecretProviderAuthority(SecretProviderId("webhook-delivery")),
        {
            IDENTITY_REFERENCE_V1: IDENTITY_TOKEN,
            IDENTITY_REFERENCE_V2: IDENTITY_TOKEN,
            SIGNING_REFERENCE: SIGNING_SECRET,
            RECEIVER_SIGNING_REFERENCE: SIGNING_SECRET,
        },
    )
    docker = DockerEffectInterpreter(project_name="", secrets=secrets)
    assignments = {capability: docker for capability in docker.capabilities}
    assignments[EffectCapability.HEALTH_PROBE] = probe
    return CapabilityInterpreterRegistry(assignments)


def _grant(prefix: str, max_effects: int) -> DeploymentExecutionGrant:
    return DeploymentExecutionGrant(
        AdmissionGrant(
            "webhook-live-operator",
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
        raise ValueError("webhook live endpoint has no authority")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    scheme = "postgresql" if protocol is Protocol.POSTGRES else parsed.scheme
    return f"{scheme}://{host}:{parsed.port}"


def _request(
    url: str,
    body: dict[str, object] | None = None,
    *,
    method: str = "POST",
    authorized: bool = True,
) -> tuple[int, dict[str, object]]:
    headers = {"Content-Type": "application/json"}
    if authorized:
        headers.update(
            {
                "X-CPK-Identity-Attestation": IDENTITY_TOKEN,
                "X-CPK-Authenticated-Subject": "webhook-live-operator",
                "X-CPK-Authenticated-Workspace": WORKSPACE_ID,
                "X-CPK-Webhook-Scopes": (
                    "webhook:enqueue,webhook:dispatch,webhook:recover,webhook:read"
                ),
            }
        )
    request = Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        with error:
            body = error.read()
            try:
                return error.code, json.loads(body)
            except json.JSONDecodeError as decode_error:
                raise RuntimeError(
                    f"webhook live request returned non-JSON HTTP {error.code}"
                ) from decode_error


def _delivery_intent() -> WebhookDeliveryIntent:
    receiver = f"http://{RUNTIME_ID}-receiver:8090"
    return WebhookDeliveryIntent(
        "enqueue-live",
        WebhookDeliveryIdentity(WORKSPACE_ID, "delivery-live"),
        WebhookEndpoint("receiver", f"{receiver}/hook"),
        WebhookPayload(WebhookContentType.JSON, b'{"event":"live-proof"}'),
        WebhookRetryPolicy(max_attempts=2, deadline_seconds=300),
        _intent_created_at(),
        WebhookSigning(SecretReference(SIGNING_REFERENCE)),
    )


def _intent_created_at() -> datetime:
    value = os.environ.get("CPK_WEBHOOK_LIVE_CREATED_AT", "")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeError("webhook live creation time is malformed") from error
    if parsed.tzinfo is None:
        raise RuntimeError("webhook live creation time must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _clock() -> str:
    return "2026-07-19T12:00:00Z"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "prepare",
            "resume-deploy",
            "verify-before-restart",
            "restart-webhook",
            "verify-after-restart",
            "begin-teardown",
            "finish-teardown",
        ),
    )
    command = parser.parse_args().command
    database_url = os.environ.get("CPK_WEBHOOK_LIVE_DATABASE_URL", "")
    if command == "prepare":
        prepare(database_url)
    elif command == "resume-deploy":
        resume_deploy(database_url)
    elif command == "verify-before-restart":
        verify_before_restart()
    elif command == "restart-webhook":
        restart_webhook(database_url)
    elif command == "verify-after-restart":
        verify_after_restart()
    elif command == "begin-teardown":
        begin_teardown(database_url)
    else:
        finish_teardown(database_url)


if __name__ == "__main__":
    main()
