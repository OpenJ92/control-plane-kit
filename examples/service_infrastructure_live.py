"""Canonical live proof for the heterogeneous service-infrastructure graph."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

import httpx
import psycopg

from control_plane_kit import (
    DeregisterDiscoveryInstance,
    DiscoveryIdentity,
    DiscoveryLease,
    DiscoveryRegistration,
    DiscoveryRegistrationMode,
    EffectResult,
    Endpoint,
    EndpointScope,
    ExpireDiscoveryLeases,
    HeartbeatDiscoveryInstance,
    LiteralAddress,
    LocalDevelopmentSecretResolver,
    Protocol,
    RegisterDiscoveryInstance,
    SecretReference,
    SecretProviderAuthority,
    SecretProviderId,
    StartNode,
    VerificationCapability,
    VerificationCompleted,
    VerificationInterpreterRegistry,
    VerificationOutcome,
    WebhookContentType,
    WebhookDeliveryIdentity,
    WebhookDeliveryIntent,
    WebhookEndpoint,
    WebhookPayload,
    WebhookRetryPolicy,
    WebhookSigning,
    compile_recipe,
    discovery_command_descriptor,
    materialize_verification_contract,
)
from control_plane_kit.adapters.probes import (
    HttpApplicationHealthProbeAdapter,
    ProbeAddressPolicy,
    ProbeEffectInterpreter,
    StaticRuntimeEndpointProvider,
    TcpTransportProbeAdapter,
)
from control_plane_kit.adapters.verification import HttpVerificationInterpreter
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
from control_plane_kit.docker_runtime import (
    DockerEffectInterpreter,
    DockerProcessProbeAdapter,
)
from control_plane_kit.effects import (
    CapabilityInterpreterRegistry,
    EffectCapability,
    EndpointContext,
    EffectInterpreter,
    LiteralEndpointMaterial,
    MaterializedEffectRequest,
    RuntimeEndpointObservation,
    TimeoutPolicy,
)
from control_plane_kit.read_services import InstanceReadService
from control_plane_kit.stores import (
    GraphVersionRecord,
    PostgresStoreBundle,
    PostgresUnitOfWork,
    WorkspaceRecord,
    install_schema,
)
from control_plane_kit.topology import DeploymentGraph
from control_plane_kit.workflows import (
    ActivityPlanningCommandService,
    ApprovalCommandService,
    CurrentGraphAdvancementCommandService,
    DeploymentPlanContextQueryService,
    DesiredGraphCommandService,
    ExecuteVerification,
    ExecutionAdmissionCommandService,
    ExecutionCoordinator,
    ExecutionWorkerAuthority,
    IdempotencyKey,
    OperationCommandService,
    RunLifecycleCommandService,
    VerificationAuthority,
    VerificationCommandService,
    VerificationScope,
)
from examples.service_infrastructure import (
    DISCOVERY_IDENTITY_REFERENCE,
    SERVICE_RUNTIME_ID,
    WEBHOOK_IDENTITY_REFERENCE,
    WEBHOOK_SIGNING_REFERENCE,
    service_infrastructure_recipe,
)


WORKSPACE_ID = "service-infrastructure-live"
EMPTY_GRAPH_ID = "service-infrastructure-live-empty"
IMAGE = os.environ.get(
    "CPK_SERVICE_INFRASTRUCTURE_IMAGE",
    "control-plane-kit-live-test:service-infrastructure",
)
DISCOVERY_TOKEN = "service-infrastructure-discovery-attestation"
WEBHOOK_TOKEN = "service-infrastructure-webhook-attestation"
WEBHOOK_SIGNING_SECRET = "service-infrastructure-webhook-signing"
WORKER = ExecutionWorkerAuthority(
    "service-infrastructure-worker",
    ("execution:operate",),
)


@dataclass(frozen=True)
class StoredPlan:
    plan_id: str
    approval_request_id: str
    current_graph_id: str
    desired_graph_id: str
    activity_count: int


class TransactionTracker:
    """Live assertion that no external effect runs inside a Postgres UoW."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.active = 0

    def __call__(self) -> "TrackingUnitOfWork":
        return TrackingUnitOfWork(
            PostgresUnitOfWork(lambda: psycopg.connect(self.database_url)),
            self,
        )


class TrackingUnitOfWork:
    def __init__(self, inner: PostgresUnitOfWork, tracker: TransactionTracker) -> None:
        self._inner = inner
        self._tracker = tracker

    def __enter__(self) -> "TrackingUnitOfWork":
        self._inner.__enter__()
        self._tracker.active += 1
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        try:
            self._inner.__exit__(exc_type, exc_value, traceback)
        finally:
            self._tracker.active -= 1

    @property
    def stores(self):
        return self._inner.stores

    def commit(self) -> None:
        self._inner.commit()


@dataclass
class RecordingEffectInterpreter:
    """Observe canonical dispatch while delegating every effect unchanged."""

    inner: EffectInterpreter
    tracker: TransactionTracker
    requests: list[MaterializedEffectRequest] = field(default_factory=list)

    @property
    def capabilities(self) -> frozenset[EffectCapability]:
        return self.inner.capabilities

    def execute(self, request: MaterializedEffectRequest) -> EffectResult:
        if self.tracker.active:
            raise AssertionError("external effect executed inside a Postgres UnitOfWork")
        self.requests.append(request)
        return self.inner.execute(request)


@dataclass(frozen=True)
class ProgramComposition:
    program: DeploymentProgram
    effects: RecordingEffectInterpreter
    tracker: TransactionTracker


def empty_graph() -> DeploymentGraph:
    return DeploymentGraph("service-infrastructure-live-empty")


def desired_graph() -> DeploymentGraph:
    return compile_recipe(service_infrastructure_recipe(package_image=IMAGE))


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
                created_by="service-infrastructure-operator",
                created_at=_clock(),
            )
        )
        stores.workspace.set_current_graph(WORKSPACE_ID, EMPTY_GRAPH_ID)
        stores.workspace.set_desired_graph(WORKSPACE_ID, EMPTY_GRAPH_ID)


def prepare(database_url: str) -> None:
    initialize(database_url)
    planned = _plan_and_approve(
        database_url,
        "deploy",
        EMPTY_GRAPH_ID,
        empty_graph(),
        desired_graph(),
    )
    composition = _composition(
        database_url,
        {planned.desired_graph_id: desired_graph()},
    )
    result = composition.program.for_plan(planned.plan_id).run(
        planned.approval_request_id,
        _grant("deploy", 1),
    )
    if not isinstance(result, ExecutionContinuation):
        raise RuntimeError("service deployment must pause after runtime creation")


def resume_deploy(database_url: str) -> None:
    planned = _stored(database_url, "deploy")
    graph = desired_graph()
    composition = _composition(database_url, {planned.desired_graph_id: graph})
    result = composition.program.for_plan(planned.plan_id).run(
        planned.approval_request_id,
        _grant("deploy", 128),
    )
    if not isinstance(result, AdvancedDeployment):
        raise RuntimeError("heterogeneous service deployment did not advance")
    _execute_package_verification(
        database_url,
        composition,
        tuple(
            request
            for request in composition.effects.requests
            if isinstance(request.action, StartNode)
        ),
    )
    if composition.tracker.active:
        raise RuntimeError("deployment left an active UnitOfWork")


def verify_products(database_url: str) -> None:
    _verify_discovery()
    _verify_collector()
    _verify_webhook()
    _verify_operator_evidence(database_url)
    print(
        "Heterogeneous service semantics passed: discovery lifecycle, OTLP trace, "
        "signed webhook, verification evidence, and redaction."
    )


def begin_teardown(database_url: str) -> None:
    deployed = _stored(database_url, "deploy")
    planned = _plan_and_approve(
        database_url,
        "teardown",
        deployed.desired_graph_id,
        desired_graph(),
        empty_graph(),
    )
    composition = _composition(
        database_url,
        {
            deployed.desired_graph_id: desired_graph(),
            planned.desired_graph_id: empty_graph(),
        },
    )
    result = composition.program.for_plan(planned.plan_id).run(
        planned.approval_request_id,
        _grant("teardown", planned.activity_count - 1),
    )
    if not isinstance(result, ExecutionContinuation):
        raise RuntimeError("service teardown must pause before runtime removal")


def finish_teardown(database_url: str) -> None:
    planned = _stored(database_url, "teardown")
    composition = _composition(
        database_url,
        {
            planned.current_graph_id: desired_graph(),
            planned.desired_graph_id: empty_graph(),
        },
    )
    result = composition.program.for_plan(planned.plan_id).run(
        planned.approval_request_id,
        _grant("teardown", 16),
    )
    if not isinstance(result, AdvancedDeployment):
        raise RuntimeError("heterogeneous service teardown did not advance")


def _plan_and_approve(
    database_url: str,
    prefix: str,
    current_graph_id: str,
    current: DeploymentGraph,
    desired: DeploymentGraph,
) -> StoredPlan:
    deployment = _composition(database_url, {}).program.between(current, desired)
    prepared = deployment.plan(
        DeploymentPlanRequest(
            deployment.transition,
            WORKSPACE_ID,
            current_graph_id,
            current_graph_id,
            "service-infrastructure-operator",
            f"Service infrastructure live {prefix}",
            "Approve the canonical heterogeneous service transition.",
            prefix,
        )
    )
    if not isinstance(prepared, ApprovalSuspension):
        raise RuntimeError("service transition did not suspend for approval")
    request = prepared.approval_request.request
    plan_id = prepared.preparation.plan.plan_record.plan_id
    _composition(database_url, {}).program.for_plan(plan_id).approve(
        request.request_id,
        ApprovalGrant(
            "service-infrastructure-approver",
            (request.required_scope,),
            IdempotencyKey(f"{prefix}:approval-decision"),
            "Approved for heterogeneous live acceptance.",
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
            raise RuntimeError(f"service {prefix} session is missing")
        plans = stores.activity_history.plans_for_session(sessions[0].session_id)
        approvals = stores.activity_history.approval_requests_for_session(
            sessions[0].session_id
        )
    plan = plans[0]
    return StoredPlan(
        plan.plan_id,
        approvals[0].request_id,
        plan.base_graph_id,
        plan.desired_graph_id,
        len(plan.plan.activities),
    )


def _composition(
    database_url: str,
    graphs: dict[str, DeploymentGraph],
) -> ProgramComposition:
    tracker = TransactionTracker(database_url)
    approvals = ApprovalCommandService(tracker, clock=_clock)
    lifecycle = RunLifecycleCommandService(tracker, clock=_clock)
    effects = RecordingEffectInterpreter(_effects(graphs), tracker)
    program = DeploymentProgram(
        DeploymentProgramServices(
            PlanningServices(
                OperationCommandService(tracker, clock=_clock),
                DesiredGraphCommandService(tracker, clock=_clock),
                ActivityPlanningCommandService(tracker, clock=_clock),
                approvals,
            ),
            approvals,
            ExecutionAdmissionCommandService(tracker, clock=_clock),
            lifecycle,
            ExecutionCoordinator(
                tracker,
                lifecycle,
                effects,
                clock=lambda: datetime.now(timezone.utc),
            ),
            CurrentGraphAdvancementCommandService(tracker, clock=_clock),
            DeploymentPlanContextQueryService(tracker),
        )
    )
    return ProgramComposition(program, effects, tracker)


def _effects(graphs: dict[str, DeploymentGraph]) -> CapabilityInterpreterRegistry:
    policy, endpoints = _endpoint_policy(graphs)
    probe = ProbeEffectInterpreter(
        StaticRuntimeEndpointProvider(endpoints),
        TcpTransportProbeAdapter(policy),
        HttpApplicationHealthProbeAdapter(policy),
        process=DockerProcessProbeAdapter(project_name=""),
    )
    secrets = LocalDevelopmentSecretResolver(
        SecretProviderAuthority(SecretProviderId("service-acceptance")),
        {
            DISCOVERY_IDENTITY_REFERENCE: DISCOVERY_TOKEN,
            WEBHOOK_IDENTITY_REFERENCE: WEBHOOK_TOKEN,
            WEBHOOK_SIGNING_REFERENCE: WEBHOOK_SIGNING_SECRET,
        },
    )
    docker = DockerEffectInterpreter(project_name="", secrets=secrets)
    assignments = {capability: docker for capability in docker.capabilities}
    assignments[EffectCapability.HEALTH_PROBE] = probe
    return CapabilityInterpreterRegistry(assignments)


def _endpoint_policy(
    graphs: dict[str, DeploymentGraph],
) -> tuple[
    ProbeAddressPolicy,
    dict[tuple[str, str], RuntimeEndpointObservation],
]:
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
    return (
        ProbeAddressPolicy(runtime_private_authorities=frozenset(authorities)),
        endpoints,
    )


def _execute_package_verification(
    database_url: str,
    composition: ProgramComposition,
    start_requests: tuple[MaterializedEffectRequest, ...],
) -> None:
    materials = tuple(
        material
        for request in start_requests
        for material in materialize_verification_contract(request)
    )
    policy, _ = _endpoint_policy(
        {_stored(database_url, "deploy").desired_graph_id: desired_graph()}
    )
    verification = VerificationCommandService(
        composition.tracker,
        VerificationInterpreterRegistry(
            {
                VerificationCapability.HTTP: HttpVerificationInterpreter(policy),
            }
        ),
        id_factory=lambda: f"service-verification-{uuid4().hex}",
    )
    authority = VerificationAuthority(
        "service-infrastructure-verifier",
        frozenset((VerificationScope.EXECUTE,)),
    )
    if len(materials) != 2:
        raise RuntimeError("expected Collector and webhook verification material")
    for material in materials:
        result = verification.execute(
            ExecuteVerification(WORKSPACE_ID, material, authority)
        ).result
        if (
            not isinstance(result, VerificationCompleted)
            or result.outcome is not VerificationOutcome.PASSED
        ):
            raise RuntimeError("package semantic verification did not pass")


def _verify_discovery() -> None:
    base = f"http://{SERVICE_RUNTIME_ID}-service-discovery:8080"
    now = datetime.now(timezone.utc)
    headers = {
        "x-cpk-identity-attestation": DISCOVERY_TOKEN,
        "x-cpk-authenticated-subject": "service-infrastructure-manager",
        "x-cpk-authenticated-workspace": WORKSPACE_ID,
        "x-cpk-discovery-scopes": "discovery:manage,discovery:resolve",
    }
    denied = httpx.post(
        f"{base}/__deploy/discovery/registrations",
        json=discovery_command_descriptor(
            RegisterDiscoveryInstance(
                "discovery-denied",
                _registration("denied", now),
            )
        ),
        timeout=5,
    )
    _http_status(denied, 401)
    if DISCOVERY_TOKEN in denied.text:
        raise RuntimeError("discovery rejection leaked identity material")

    first = _registration("hello-a", now)
    registered = _discovery_post(
        base,
        "/__deploy/discovery/registrations",
        RegisterDiscoveryInstance("register-a", first),
        headers,
    )
    if registered["result"]["outcome"] != "registered":
        raise RuntimeError("discovery registration did not persist")
    if _resolve_discovery(base, "resolve-a", now, headers) != ["hello-a"]:
        raise RuntimeError("discovery resolution did not return registered instance")

    renewed = DiscoveryLease(
        now + timedelta(seconds=10),
        now + timedelta(seconds=120),
    )
    heartbeat = _discovery_post(
        base,
        "/__deploy/discovery/registrations/hello-a/heartbeat",
        HeartbeatDiscoveryInstance(
            "heartbeat-a",
            first.identity,
            first.lease.expires_at,
            renewed,
        ),
        headers,
    )
    if heartbeat["result"]["registrations"][0]["revision"] != 2:
        raise RuntimeError("discovery heartbeat did not advance revision")
    _discovery_post(
        base,
        "/__deploy/discovery/registrations/hello-a/deregister",
        DeregisterDiscoveryInstance(
            "deregister-a",
            first.identity,
            renewed.expires_at,
        ),
        headers,
    )
    if _resolve_discovery(base, "resolve-after-deregister", now, headers):
        raise RuntimeError("deregistered discovery instance remained visible")

    second = _registration("hello-b", now)
    _discovery_post(
        base,
        "/__deploy/discovery/registrations",
        RegisterDiscoveryInstance("register-b", second),
        headers,
    )
    expired = _discovery_post(
        base,
        "/__deploy/discovery/expiry",
        ExpireDiscoveryLeases(
            "expire-b",
            WORKSPACE_ID,
            second.lease.expires_at,
            100,
        ),
        headers,
    )
    if expired["result"]["affected_count"] != 1:
        raise RuntimeError("discovery expiry did not remove exact lease")
    if _resolve_discovery(
        base,
        "resolve-after-expiry",
        second.lease.expires_at,
        headers,
    ):
        raise RuntimeError("expired discovery instance remained visible")


def _registration(instance_id: str, now: datetime) -> DiscoveryRegistration:
    return DiscoveryRegistration(
        DiscoveryIdentity(WORKSPACE_ID, "hello", instance_id),
        Endpoint(
            LiteralAddress(f"http://{instance_id}:8080"),
            Protocol.HTTP,
            EndpointScope.PRIVATE,
        ),
        DiscoveryRegistrationMode.CONTROL_PLANE,
        DiscoveryLease(now, now + timedelta(seconds=60)),
    )


def _discovery_post(
    base: str,
    path: str,
    command,
    headers: dict[str, str],
) -> dict[str, object]:
    response = httpx.post(
        f"{base}{path}",
        json=discovery_command_descriptor(command),
        headers=headers,
        timeout=5,
    )
    _http_status(response, 200)
    return response.json()


def _resolve_discovery(
    base: str,
    command_id: str,
    observed_at: datetime,
    headers: dict[str, str],
) -> list[str]:
    response = httpx.get(
        f"{base}/__deploy/discovery/services/hello",
        params={
            "command_id": command_id,
            "workspace_id": WORKSPACE_ID,
            "observed_at": observed_at.isoformat(),
            "limit": 100,
        },
        headers=headers,
        timeout=5,
    )
    _http_status(response, 200)
    return [
        value["registration"]["identity"]["instance_id"]
        for value in response.json()["result"]["registrations"]
    ]


def _verify_collector() -> None:
    collector = f"{SERVICE_RUNTIME_ID}-opentelemetry-collector"
    endpoint = f"http://{collector}:4318"
    payload = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {"stringValue": "service-infrastructure-live"},
                        }
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "service-infrastructure-live"},
                        "spans": [
                            {
                                "traceId": "0123456789abcdef0123456789abcdef",
                                "spanId": "0123456789abcdef",
                                "name": "service-infrastructure-live-span",
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
    response = httpx.post(
        f"{endpoint}/v1/traces",
        json=payload,
        timeout=5,
    )
    _http_status(response, 200)
    for _ in range(30):
        completed = subprocess.run(
            ("docker", "logs", collector),
            check=True,
            capture_output=True,
            text=True,
        )
        logs = completed.stdout + completed.stderr
        if "service-infrastructure-live-span" in logs:
            return
        time.sleep(1)
    raise RuntimeError("Collector debug exporter did not emit the accepted span")


def _verify_webhook() -> None:
    base = f"http://{SERVICE_RUNTIME_ID}-webhook-delivery:8080"
    receiver = f"http://{SERVICE_RUNTIME_ID}-webhook-receiver:8090"
    intent = _webhook_intent("delivery-live", "receiver", f"{receiver}/hook")
    unauthorized = _webhook_request(
        f"{base}/__deploy/webhooks",
        intent.descriptor(),
        authorized=False,
    )
    if unauthorized[0] != 401:
        raise RuntimeError("unauthorized webhook enqueue did not fail closed")
    if WEBHOOK_TOKEN in str(unauthorized[1]):
        raise RuntimeError("webhook rejection leaked identity material")
    queued = _webhook_request(
        f"{base}/__deploy/webhooks",
        intent.descriptor(),
    )[1]
    replay = _webhook_request(
        f"{base}/__deploy/webhooks",
        intent.descriptor(),
    )[1]
    if (
        queued["delivery"]["status"] != "queued"
        or queued["replayed"]
        or not replay["replayed"]
    ):
        raise RuntimeError("webhook enqueue did not persist queued intent")
    claimed = _webhook_request(
        f"{base}/__deploy/webhooks/delivery-live/claims",
        {
            "command_id": "claim-live",
            "worker_id": "worker-live",
            "lease_seconds": 60,
        },
    )[1]
    claim_id = claimed["delivery"]["active_claim"]["claim_id"]
    delivered = _webhook_request(
        f"{base}/__deploy/webhooks/delivery-live/dispatch",
        {
            "command_id": "dispatch-live",
            "claim_id": claim_id,
            "worker_id": "worker-live",
        },
    )[1]
    received = _webhook_request(
        f"{receiver}/received/delivery-live",
        method="GET",
        authorized=False,
    )[1]
    if (
        delivered["delivery"]["status"] != "delivered"
        or not received["signature_valid"]
    ):
        raise RuntimeError("allowed signed webhook did not reach receiver")
    read = _webhook_request(
        f"{base}/__deploy/webhooks/delivery-live",
        method="GET",
    )[1]
    if WEBHOOK_SIGNING_SECRET in str(read) or "live-proof" in str(read):
        raise RuntimeError("webhook durable read exposed secret or payload")

    denied = _webhook_intent(
        "delivery-denied",
        "not-granted",
        f"{receiver}/hook",
    )
    _webhook_request(f"{base}/__deploy/webhooks", denied.descriptor())
    denied_claim = _webhook_request(
        f"{base}/__deploy/webhooks/delivery-denied/claims",
        {
            "command_id": "claim-denied",
            "worker_id": "worker-live",
            "lease_seconds": 60,
        },
    )[1]
    denied_result = _webhook_request(
        f"{base}/__deploy/webhooks/delivery-denied/dispatch",
        {
            "command_id": "dispatch-denied",
            "claim_id": denied_claim["delivery"]["active_claim"]["claim_id"],
            "worker_id": "worker-live",
        },
    )[1]
    if denied_result["delivery"]["status"] != "dead-letter":
        raise RuntimeError("ungranted webhook endpoint did not fail closed")


def _webhook_intent(
    delivery_id: str,
    endpoint_id: str,
    endpoint_url: str,
) -> WebhookDeliveryIntent:
    return WebhookDeliveryIntent(
        f"enqueue-{delivery_id}",
        WebhookDeliveryIdentity(WORKSPACE_ID, delivery_id),
        WebhookEndpoint(endpoint_id, endpoint_url),
        WebhookPayload(WebhookContentType.JSON, b'{"event":"live-proof"}'),
        WebhookRetryPolicy(max_attempts=2, deadline_seconds=300),
        _intent_created_at(),
        WebhookSigning(SecretReference(WEBHOOK_SIGNING_REFERENCE)),
    )


def _webhook_request(
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
                "X-CPK-Identity-Attestation": WEBHOOK_TOKEN,
                "X-CPK-Authenticated-Subject": "service-infrastructure-operator",
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
            raw = error.read()
            try:
                return error.code, json.loads(raw)
            except json.JSONDecodeError as decode_error:
                raise RuntimeError(
                    f"webhook request returned non-JSON HTTP {error.code}"
                ) from decode_error


def _verify_operator_evidence(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        stores = PostgresStoreBundle(connection)
        reads = InstanceReadService(
            workspace_store=stores.workspace,
            graph_topology_store=stores.graph_topology,
            activity_history_store=stores.activity_history,
            execution_store=stores.execution,
            observed_state_store=stores.observed_state,
        )
        observations = reads.observed_state(WORKSPACE_ID).observations
        projected = str(reads.workspace(WORKSPACE_ID).descriptor())
    semantic = tuple(
        value
        for value in observations
        if value["probe_kind"] == "semantic-verification"
    )
    if len(semantic) != 2 or any(
        value["status"] != "verified" for value in semantic
    ):
        raise RuntimeError("package verification observations are incomplete")
    forbidden = (
        DISCOVERY_TOKEN,
        WEBHOOK_TOKEN,
        WEBHOOK_SIGNING_SECRET,
        "secret://service-acceptance",
    )
    if any(value in str(semantic) or value in projected for value in forbidden):
        raise RuntimeError("operator projection exposed secret material")


def _grant(prefix: str, max_effects: int) -> DeploymentExecutionGrant:
    return DeploymentExecutionGrant(
        AdmissionGrant(
            "service-infrastructure-operator",
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
        raise ValueError("service endpoint has no authority")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    scheme = "postgresql" if protocol is Protocol.POSTGRES else parsed.scheme
    return f"{scheme}://{host}:{parsed.port}"


def _http_status(response: httpx.Response, expected: int) -> None:
    if response.status_code != expected:
        raise RuntimeError(
            f"expected HTTP {expected}, got {response.status_code}: {response.text}"
        )


def _intent_created_at() -> datetime:
    value = os.environ.get("CPK_SERVICE_INFRASTRUCTURE_CREATED_AT", "")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeError("service live creation time is malformed") from error
    if parsed.tzinfo is None:
        raise RuntimeError("service live creation time must be timezone-aware")
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
            "verify-products",
            "begin-teardown",
            "finish-teardown",
        ),
    )
    command = parser.parse_args().command
    database_url = os.environ.get("CPK_SERVICE_INFRASTRUCTURE_DATABASE_URL", "")
    if command == "prepare":
        prepare(database_url)
    elif command == "resume-deploy":
        resume_deploy(database_url)
    elif command == "verify-products":
        verify_products(database_url)
    elif command == "begin-teardown":
        begin_teardown(database_url)
    else:
        finish_teardown(database_url)


if __name__ == "__main__":
    main()
