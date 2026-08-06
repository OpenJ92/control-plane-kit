from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
import unittest

import psycopg

import control_plane_kit_operations.ingress_realization as ingress_realization
from control_plane_kit_core.algebra import BlockSockets, BlockSpec, ProviderSocket
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
    FailureCategory,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.planning import (
    ActivityId,
    ActivityImpact,
    ActivityPlan,
    AllocatePublicIngress,
    PlannedActivity,
    PublicIngressActivityTarget,
    PublicIngressReservationTarget,
    ReleasePublicIngressReservation,
    RemovePublicIngress,
    RiskLevel,
    WaitForPublicIngressReady,
)
from control_plane_kit_core.probe_intents import (
    EndpointContext,
    LiteralEndpointMaterial,
    RuntimeEndpointObservation,
)
from control_plane_kit_core.public_ingress import (
    IngressAuthorityReference,
    NamedPublicIngress,
    PublicIngressLifecycle,
    PublicIngressObservation,
    PublicIngressObservationStatus,
    PublicIngressTarget,
)
from control_plane_kit_core.secrets import (
    SecretCustodyGrant,
    SecretCustodyReceipt,
    SecretProviderEndpointReference,
    SecretProviderId,
    SecretReference,
    SecretResolutionGrant,
    SecretUseIntent,
)
from control_plane_kit_core.topology import (
    DeploymentGraph,
    Endpoint,
    LiteralAddress,
    Node,
    RuntimeRecord,
)
from control_plane_kit_core.types import BlockFamily, Protocol, RuntimeKind
from control_plane_kit_core.verification import (
    HttpCheck,
    PostgresQueryCheck,
    VerificationContract,
    VerificationPolicy,
)
from control_plane_kit_operations.coordinator import ActivityRealizationContext
from control_plane_kit_operations.ingress_authorities import (
    CloudflareOwnedHostnameReservation,
    CloudflareOwnedIngressResource,
    CloudflareZoneIngressAuthority,
    GeneratedIngressSecretReference,
    GeneratedSecretPurpose,
    IngressAuthorityProviderKind,
    OwnedHostnameReservationStatus,
    OwnedIngressResourceStatus,
)
from control_plane_kit_operations.ingress_realization import (
    IngressOwnedResourceCoordinates,
    IngressRealizationAdapter,
    _public_ingress_http_check,
)
from control_plane_kit_operations.lifecycle import ExecutionWorkerAuthority
from control_plane_kit_operations.postgres import PostgresUnitOfWork, install_schema
from control_plane_kit_operations.records import (
    ActivityEventRecord,
    ActivityPlanRecord,
    ActivityPlanStatus,
    ActivityRunRecord,
    AdmittedRun,
    ClaimIdentity,
    ExecutionIdempotency,
    ExecutionRequestIdentity,
    ExecutionRequestRecord,
    GraphVersionRecord,
    RealizedGraphProjectionRecord,
    RetryIdentity,
    ObservationStatus,
)
from control_plane_kit_operations.secret_providers import (
    AuthorizeSecretUse,
    RegisteredSecretProvider,
    RegisteredSecretReference,
    SecretProviderKind,
)
from control_plane_kit_operations.workflows import InvalidOperationCommand


class TrackingUnitOfWorkFactory:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self.active = 0
        self.entered = 0
        self.committed = 0

    def __call__(self) -> "TrackingUnitOfWork":
        return TrackingUnitOfWork(self, PostgresUnitOfWork(self._connect))

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url)


class TrackingUnitOfWork:
    def __init__(
        self,
        factory: TrackingUnitOfWorkFactory,
        inner: PostgresUnitOfWork,
    ) -> None:
        self._factory = factory
        self._inner = inner

    @property
    def stores(self):
        return self._inner.stores

    def __enter__(self) -> "TrackingUnitOfWork":
        self._factory.entered += 1
        self._factory.active += 1
        self._inner.__enter__()
        return self

    def commit(self) -> None:
        self._factory.committed += 1
        self._inner.commit()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            self._inner.__exit__(exc_type, exc, traceback)
        finally:
            self._factory.active -= 1


@dataclass(frozen=True)
class FakeIngressAllocation:
    secret_custody_receipt: SecretCustodyReceipt
    tunnel_id: str = "tunnel-001"
    tunnel_name: str = "cpk-gateway-001"
    dns_record_id: str = "dns-001"
    hostname: str = "cpk-gateway-001.openj92.dev"
    endpoint_url: str = "https://cpk-gateway-001.openj92.dev"


class RecordingIngressInterpreter:
    def __init__(self, tracker: TrackingUnitOfWorkFactory) -> None:
        self.tracker = tracker
        self.create_active_counts: list[int] = []
        self.create_allocation_names: list[str] = []
        self.create_origins: list[str] = []
        self.create_authorities: list[CloudflareZoneIngressAuthority] = []
        self.create_grants: list[SecretResolutionGrant] = []
        self.create_custody_grants: list[SecretCustodyGrant] = []
        self.teardown_active_counts: list[int] = []
        self.teardown_resources: list[IngressOwnedResourceCoordinates] = []
        self.teardown_grants: list[SecretResolutionGrant] = []
        self.teardown_custody_grants: list[SecretCustodyGrant] = []
        self.rebind_active_counts: list[int] = []
        self.rebind_reservations: list[object] = []
        self.rebind_custody_grants: list[SecretCustodyGrant] = []
        self.deactivate_active_counts: list[int] = []
        self.deactivate_reservations: list[object] = []
        self.deactivate_resources: list[IngressOwnedResourceCoordinates] = []
        self.release_active_counts: list[int] = []
        self.release_reservations: list[object] = []
        self.fail_teardown = False
        self.fail_rebind = False
        self.fail_deactivate = False
        self.contradict_deactivation = False
        self.contradict_rebind = False
        self.fail_release = False
        self.contradict_release = False
        self.return_mismatched_custody_receipt = False
        self.return_invalid_coordinates = False
        self.on_create: Callable[[], None] | None = None
        self.on_rebind: Callable[[], None] | None = None
        self.on_deactivate: Callable[[], None] | None = None
        self.on_release: Callable[[], None] | None = None

    def create(
        self,
        ingress: NamedPublicIngress,
        *,
        authority: CloudflareZoneIngressAuthority,
        allocation_name: str,
        origin_service_url: str,
        secret_resolution_grant: SecretResolutionGrant,
        secret_custody_grant: SecretCustodyGrant,
    ) -> FakeIngressAllocation:
        self.create_active_counts.append(self.tracker.active)
        self.create_allocation_names.append(allocation_name)
        self.create_origins.append(origin_service_url)
        self.create_authorities.append(authority)
        self.create_grants.append(secret_resolution_grant)
        self.create_custody_grants.append(secret_custody_grant)
        if self.on_create is not None:
            self.on_create()
        receipt_reference = secret_custody_grant.reference
        if self.return_mismatched_custody_receipt:
            receipt_reference = SecretReference(
                "secret://generated/ingress/untrusted-reference"
            )
        return FakeIngressAllocation(
            secret_custody_receipt=SecretCustodyReceipt(
                custody_id=secret_custody_grant.custody_id,
                provider_registration_id=(
                    secret_custody_grant.provider_registration_id
                ),
                reference=receipt_reference,
                version_id="version-tunnel-token",
                version_number=1,
            ),
            tunnel_id=(
                "tunnel\ninvalid"
                if self.return_invalid_coordinates
                else "tunnel-001"
            ),
            tunnel_name=allocation_name,
            hostname=ingress.hostname,
        )

    def teardown(
        self,
        *,
        authority: CloudflareZoneIngressAuthority,
        resources: IngressOwnedResourceCoordinates,
        secret_resolution_grant: SecretResolutionGrant,
        secret_custody_grant: SecretCustodyGrant,
    ) -> None:
        del authority
        self.teardown_active_counts.append(self.tracker.active)
        self.teardown_resources.append(resources)
        self.teardown_grants.append(secret_resolution_grant)
        self.teardown_custody_grants.append(secret_custody_grant)
        if self.fail_teardown:
            raise RuntimeError("provider teardown failed")

    def rebind(
        self,
        ingress: NamedPublicIngress,
        *,
        authority: CloudflareZoneIngressAuthority,
        reservation: object,
        allocation_name: str,
        origin_service_url: str,
        secret_resolution_grant: SecretResolutionGrant,
        secret_custody_grant: SecretCustodyGrant,
    ) -> FakeIngressAllocation:
        del authority, origin_service_url, secret_resolution_grant
        self.rebind_active_counts.append(self.tracker.active)
        self.rebind_reservations.append(reservation)
        self.rebind_custody_grants.append(secret_custody_grant)
        if self.on_rebind is not None:
            self.on_rebind()
        if self.fail_rebind:
            raise RuntimeError("Bearer must-not-survive")
        return FakeIngressAllocation(
            secret_custody_receipt=SecretCustodyReceipt(
                custody_id=secret_custody_grant.custody_id,
                provider_registration_id=(
                    secret_custody_grant.provider_registration_id
                ),
                reference=secret_custody_grant.reference,
                version_id="version-rebound-token",
                version_number=2,
            ),
            tunnel_id=("tunnel-001" if self.contradict_rebind else "tunnel-002"),
            tunnel_name=allocation_name,
            dns_record_id=("dns-foreign" if self.contradict_rebind else "dns-001"),
            hostname=ingress.hostname,
        )

    def deactivate_preserving_reservation(
        self,
        *,
        authority: CloudflareZoneIngressAuthority,
        reservation: object,
        resources: IngressOwnedResourceCoordinates,
        secret_resolution_grant: SecretResolutionGrant,
        secret_custody_grant: SecretCustodyGrant,
    ) -> object:
        del authority, secret_resolution_grant, secret_custody_grant
        self.deactivate_active_counts.append(self.tracker.active)
        self.deactivate_reservations.append(reservation)
        self.deactivate_resources.append(resources)
        if self.on_deactivate is not None:
            self.on_deactivate()
        if self.fail_deactivate:
            raise RuntimeError("Bearer must-not-survive")
        presence = ingress_realization.IngressResourcePresence
        return ingress_realization.RetainedIngressDeactivationResult(
            reservation=ingress_realization.IngressReservationObservation(
                dns_record_id=resources.dns_record_id,
                hostname=resources.hostname,
                presence=presence.PRESENT,
                tunnel_id=(
                    "tunnel-foreign"
                    if self.contradict_deactivation
                    else resources.tunnel_id
                ),
            ),
            tunnel=ingress_realization.IngressTunnelObservation(
                tunnel_id=resources.tunnel_id,
                presence=(
                    presence.PRESENT
                    if self.contradict_deactivation
                    else presence.ABSENT
                ),
            ),
        )

    def release_reservation(
        self,
        *,
        authority: CloudflareZoneIngressAuthority,
        reservation: object,
        secret_resolution_grant: SecretResolutionGrant,
    ) -> object:
        del authority, secret_resolution_grant
        self.release_active_counts.append(self.tracker.active)
        self.release_reservations.append(reservation)
        if self.on_release is not None:
            self.on_release()
        if self.fail_release:
            raise RuntimeError("Bearer must-not-survive")
        presence = ingress_realization.IngressResourcePresence
        return ingress_realization.IngressReservationObservation(
            dns_record_id=reservation.dns_record_id,
            hostname=reservation.hostname,
            presence=(
                presence.PRESENT
                if self.contradict_release
                else presence.ABSENT
            ),
            tunnel_id=(
                reservation.expected_tunnel_id
                if self.contradict_release
                else None
            ),
        )


class RecordingSecretUseAuthorizer:
    def __init__(self) -> None:
        self.commands: list[AuthorizeSecretUse] = []

    def authorize_resolution(
        self,
        command: AuthorizeSecretUse,
    ) -> SecretResolutionGrant:
        self.commands.append(command)
        return SecretResolutionGrant(
            authorization_id="suse_" + "a" * 64,
            workspace_id=command.workspace_id,
            reference_registration_id="sref_" + "b" * 64,
            provider_registration_id="sprov_" + "c" * 64,
            endpoint_reference=SecretProviderEndpointReference("provider-a"),
            credential_reference=SecretReference(
                "secret://bootstrap/provider-a-token"
            ),
            reference=command.reference,
            intent=command.intent,
            actor_subject=command.actor_subject,
            correlation_id=command.correlation_id,
            intent_fingerprint="d" * 64,
            operation_id=command.operation_id,
            session_id=command.session_id,
            run_id=command.run_id,
            activity_id=command.activity_id,
            effect_id=command.effect_id,
            probe_id=command.probe_id,
        )


class RecordingPublicIngressReadinessVerifier:
    def __init__(
        self,
        tracker: TrackingUnitOfWorkFactory,
        status: PublicIngressObservationStatus,
    ) -> None:
        self.tracker = tracker
        self.status = status
        self.observed_at = "2026-07-28T08:01:30Z"
        self.active_counts: list[int] = []
        self.attempt_timeouts: list[float] = []
        self.error: Exception | None = None
        self.calls: list[
            tuple[NamedPublicIngress, HttpCheck, RuntimeEndpointObservation]
        ] = []

    def observe(
        self,
        *,
        ingress: NamedPublicIngress,
        check: HttpCheck,
        endpoint: RuntimeEndpointObservation,
        attempt_timeout_seconds: float,
    ) -> PublicIngressObservation:
        self.active_counts.append(self.tracker.active)
        self.attempt_timeouts.append(attempt_timeout_seconds)
        self.calls.append((ingress, check, endpoint))
        if self.error is not None:
            raise self.error
        return PublicIngressObservation(
            ingress_id=ingress.ingress_id,
            hostname=ingress.hostname,
            url=f"https://{ingress.hostname}",
            target=ingress.target,
            observed_at=self.observed_at,
            status=self.status,
            evidence={"verification": "bounded"},
        )


class IngressRealizationAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        database_url = os.environ.get("CPK_OPERATIONS_TEST_DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "CPK_OPERATIONS_TEST_DATABASE_URL is required. Run "
                "./control-plane-kit-operations/test.sh so Docker starts Postgres."
            )
        self.database_url = database_url
        self.connection = psycopg.connect(database_url, autocommit=True)
        install_schema(self.connection)
        self.connection.execute("TRUNCATE TABLE cpk_workspaces CASCADE")
        self.connection.execute(
            """
            INSERT INTO cpk_workspaces (workspace_id, name, lifecycle)
            VALUES ('workspace-a', 'Workspace A', 'created')
            """
        )
        self.tracker = TrackingUnitOfWorkFactory(database_url)
        self.authorizer = RecordingSecretUseAuthorizer()
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.secret_providers.register(
                RegisteredSecretProvider(
                    registration_id="sprov-generated-ingress",
                    workspace_id="workspace-a",
                    provider_id=SecretProviderId("generated"),
                    provider_kind=SecretProviderKind.CONTROL_PLANE_KIT_SECRETS,
                    display_name="Generated ingress secrets",
                    endpoint_reference=SecretProviderEndpointReference(
                        "workspace-secrets"
                    ),
                    credential_reference=SecretReference(
                        "secret://bootstrap/provider-token"
                    ),
                    allowed_reference_prefixes=(
                        SecretReference("secret://generated/ingress"),
                    ),
                    allowed_intents=(
                        SecretUseIntent.CLOUDFLARE_TUNNEL_TOKEN,
                    ),
                    admitted_by="operator-a",
                    admitted_at="2026-07-28T08:00:00Z",
                )
            )
            unit_of_work.stores.ingress_authorities.register(
                workspace_id="workspace-a",
                authority_ref=IngressAuthorityReference("openj92-public-ingress"),
                authority=CloudflareZoneIngressAuthority(
                    account_id="account-openj92",
                    zone_id="zone-openj92",
                    zone_name="openj92.dev",
                    api_token_ref=SecretReference(
                        "secret://cloudflare/openj92/api-token"
                    ),
                    allowed_hostname_pattern="cpk-gateway-*.openj92.dev",
                    generated_secret_provider_registration_id=(
                        "sprov-generated-ingress"
                    ),
                    generated_secret_reference_prefix=SecretReference(
                        "secret://generated/ingress"
                    ),
                ),
                admitted_by="operator-a",
                admitted_at="2026-07-28T08:00:00Z",
            )
            unit_of_work.commit()

    def tearDown(self) -> None:
        self.connection.close()

    def unit_of_work(self) -> TrackingUnitOfWork:
        return self.tracker()

    def test_allocate_public_ingress_calls_provider_outside_transaction_and_records_references(
        self,
    ) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:01:00Z",
            secret_use_authorizer=self.authorizer,
        )

        outcome = adapter.execute(self.context())

        self.assertEqual(interpreter.create_active_counts, [0])
        self.assertEqual(interpreter.create_origins, ["http://gateway:8000"])
        self.assertEqual(
            interpreter.create_authorities[0].api_token_ref.reference_id,
            "secret://cloudflare/openj92/api-token",
        )
        self.assertEqual(len(interpreter.create_grants), 1)
        self.assertEqual(len(interpreter.create_custody_grants), 1)
        self.assertTrue(
            interpreter.create_custody_grants[0].permits(
                interpreter.create_custody_grants[0].reference,
                SecretUseIntent.CLOUDFLARE_TUNNEL_TOKEN,
            )
        )
        self.assertTrue(
            interpreter.create_grants[0].permits(
                SecretReference("secret://cloudflare/openj92/api-token"),
                SecretUseIntent.CLOUDFLARE_API_TOKEN,
            )
        )
        self.assertEqual(self.authorizer.commands[0].actor_subject, "worker-a")
        self.assertEqual(self.authorizer.commands[0].effect_id, "event-001")
        self.assertEqual(outcome.kind.name, "SUCCEEDED")
        self.assertEqual(
            interpreter.create_allocation_names,
            ["cpk-gateway-001-c0303ba7369e"],
        )
        descriptor = outcome.evidence.descriptor()
        self.assertEqual(descriptor["provider_kind"], "cloudflare")
        self.assertEqual(descriptor["ingress_id"], "gateway-001")
        self.assertEqual(descriptor["runtime_id"], "docker-a")
        self.assertIs(descriptor["connector_material_recorded"], True)
        self.assertNotIn("secret://", repr(descriptor).lower())
        self.assertNotIn("eyj-cloudflare", repr(descriptor).lower())
        self.assertNotIn("bearer-value", repr(descriptor).lower())

        with self.unit_of_work() as unit_of_work:
            resource = unit_of_work.stores.ingress_resources.get_cloudflare(
                "workspace-a",
                "gateway-001",
            )
            generated = unit_of_work.stores.generated_ingress_secrets.get_by_source(
                workspace_id="workspace-a",
                purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
                source_run_id="run-a",
                source_activity_id="allocate-gateway",
                source_event_id="event-001",
            )

        self.assertEqual(resource.tunnel_id, "tunnel-001")
        self.assertEqual(resource.tunnel_name, "cpk-gateway-001-c0303ba7369e")
        self.assertEqual(resource.dns_record_id, "dns-001")
        self.assertEqual(resource.hostname, "cpk-gateway-001.openj92.dev")
        self.assertEqual(resource.lifecycle, PublicIngressLifecycle.EPHEMERAL)
        self.assertEqual(descriptor["tunnel_id"], resource.tunnel_id)
        self.assertEqual(descriptor["tunnel_name"], resource.tunnel_name)
        self.assertEqual(descriptor["dns_record_id"], resource.dns_record_id)
        self.assertEqual(descriptor["hostname"], resource.hostname)
        self.assertEqual(descriptor["lifecycle"], resource.lifecycle.value)
        self.assertEqual(
            generated.secret_ref.reference_id,
            interpreter.create_custody_grants[0].reference.reference_id,
        )
        self.assertEqual(generated.provider_registration_id, "sprov-generated-ingress")
        self.assertEqual(generated.provider_version_id, "version-tunnel-token")
        self.assertEqual(generated.provider_version_number, 1)

    def test_public_ingress_readiness_derives_exact_http_check_outside_transaction(
        self,
    ) -> None:
        verifier = RecordingPublicIngressReadinessVerifier(
            self.tracker,
            PublicIngressObservationStatus.READY,
        )
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={},
            clock=lambda: "2026-07-28T08:01:00Z",
            readiness_verifier=verifier,
        )
        graph = self.graph(
            verification=VerificationContract(
                (
                    HttpCheck(
                        check_id="gateway-live",
                        provider_socket="control",
                        path="/health/live",
                    ),
                    HttpCheck(
                        check_id="gateway-ready",
                        provider_socket="control",
                        path="/health/ready",
                        policy=VerificationPolicy(
                            maximum_attempts=4,
                            interval_seconds=0.25,
                        ),
                    ),
                )
            )
        )

        outcome = adapter.execute(
            self.context(
                activity_id="wait-gateway-public",
                operation=WaitForPublicIngressReady(
                    PublicIngressActivityTarget("gateway-001")
                ),
                desired_graph=graph,
            )
        )

        self.assertEqual(outcome.kind.name, "SUCCEEDED")
        self.assertEqual(verifier.active_counts, [0])
        self.assertEqual(len(verifier.calls), 1)
        self.assertEqual(verifier.attempt_timeouts, [5.0])
        ingress, check, endpoint = verifier.calls[0]
        self.assertEqual(ingress.ingress_id, "gateway-001")
        self.assertEqual(check.check_id, "gateway-ready")
        self.assertEqual(check.path, "/health/ready")
        self.assertEqual(check.policy.maximum_attempts, 4)
        self.assertEqual(endpoint.subject_id, "gateway")
        self.assertEqual(endpoint.socket_name, "control")
        self.assertEqual(endpoint.graph_id, "graph-desired")
        self.assertIs(endpoint.context, EndpointContext.PUBLIC)
        self.assertEqual(
            endpoint.address,
            LiteralEndpointMaterial(
                "https://cpk-gateway-001.openj92.dev:443"
            ),
        )
        self.assertEqual(len(outcome.observations), 1)
        self.assertIs(outcome.observations[0].status, ObservationStatus.VERIFIED)

    def test_unready_public_ingress_records_bounded_resumable_progress(
        self,
    ) -> None:
        verifier = RecordingPublicIngressReadinessVerifier(
            self.tracker,
            PublicIngressObservationStatus.UNREADY,
        )
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={},
            clock=lambda: "2026-07-28T08:01:30Z",
            readiness_verifier=verifier,
        )

        outcome = adapter.execute(
            self.context(
                activity_id="wait-gateway-public",
                operation=WaitForPublicIngressReady(
                    PublicIngressActivityTarget("gateway-001")
                ),
                desired_graph=self.graph(
                    verification=VerificationContract(
                        (
                            HttpCheck(
                                check_id="gateway-ready",
                                provider_socket="control",
                                path="/health/ready",
                            ),
                        )
                    )
                ),
            )
        )

        self.assertEqual(outcome.kind.name, "LIMITED_PROGRESS")
        self.assertIsNone(outcome.failure)
        self.assertEqual(
            outcome.evidence.descriptor(),
            {
                "deadline": "2026-07-28T08:06:00Z",
                "ingress_id": "gateway-001",
                "next_attempt_not_before": "2026-07-28T08:01:35Z",
                "progress_kind": "public-ingress-convergence",
                "public_ingress_observation": {
                    "ingress_id": "gateway-001",
                    "hostname": "cpk-gateway-001.openj92.dev",
                    "url": "https://cpk-gateway-001.openj92.dev",
                    "target": {
                        "node_id": "gateway",
                        "provider_socket": "control",
                    },
                    "observed_at": "2026-07-28T08:01:30Z",
                    "status": "unready",
                    "evidence": {"verification": "bounded"},
                },
            },
        )
        self.assertEqual(len(outcome.observations), 1)
        self.assertIs(
            outcome.observations[0].status,
            ObservationStatus.VERIFICATION_FAILED,
        )
        self.assertNotIn("secret://", repr(outcome))

    def test_readiness_retries_record_distinct_immutable_observations(self) -> None:
        verifier = RecordingPublicIngressReadinessVerifier(
            self.tracker,
            PublicIngressObservationStatus.UNREADY,
        )
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={},
            clock=lambda: "2026-07-28T08:01:30Z",
            readiness_verifier=verifier,
        )
        context = self.context(
            activity_id="wait-gateway-public",
            operation=WaitForPublicIngressReady(
                PublicIngressActivityTarget("gateway-001")
            ),
            desired_graph=self.graph(
                verification=VerificationContract(
                    (
                        HttpCheck(
                            check_id="gateway-ready",
                            provider_socket="control",
                            path="/health/ready",
                        ),
                    )
                )
            ),
        )

        first = adapter.execute(context)
        verifier.observed_at = "2026-07-28T08:01:31Z"
        second = adapter.execute(context)
        first_observation = first.observations[0]
        second_observation = second.observations[0]

        self.assertNotEqual(
            first_observation.observation_id,
            second_observation.observation_id,
        )
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.observed_state.put(first_observation)
            unit_of_work.stores.observed_state.put(second_observation)
            unit_of_work.commit()
        with self.unit_of_work() as unit_of_work:
            latest = unit_of_work.stores.observed_state.latest(
                "workspace-a",
                "gateway-001",
            )

        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.observed_at, "2026-07-28T08:01:31Z")

    def test_public_ingress_readiness_binding_fails_closed_before_verifier_io(self) -> None:
        checks = (
            (),
            (
                HttpCheck(
                    check_id="first",
                    provider_socket="control",
                    path="/health/live",
                ),
                HttpCheck(
                    check_id="second",
                    provider_socket="control",
                    path="/health/ready",
                ),
            ),
            (
                HttpCheck(
                    check_id="gateway-ready",
                    provider_socket="other",
                    path="/health/ready",
                ),
            ),
            (
                PostgresQueryCheck(
                    check_id="gateway-ready",
                    provider_socket="control",
                ),
            ),
        )

        for ordinal, values in enumerate(checks):
            with self.subTest(count=len(values)):
                graph = self.graph(verification=VerificationContract(values))
                with self.assertRaises(InvalidOperationCommand):
                    _public_ingress_http_check(graph, graph.public_ingresses[0])

    def test_convergence_deadline_fails_without_another_external_attempt(self) -> None:
        verifier = RecordingPublicIngressReadinessVerifier(
            self.tracker,
            PublicIngressObservationStatus.READY,
        )
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={},
            clock=lambda: "2026-07-28T08:06:00Z",
            readiness_verifier=verifier,
        )

        outcome = adapter.execute(
            self.context(
                activity_id="wait-gateway-public",
                operation=WaitForPublicIngressReady(
                    PublicIngressActivityTarget("gateway-001")
                ),
                desired_graph=self.graph(
                    verification=VerificationContract(
                        (
                            HttpCheck(
                                check_id="gateway-ready",
                                provider_socket="control",
                                path="/health/ready",
                            ),
                        )
                    )
                ),
            )
        )

        self.assertEqual(outcome.kind.name, "FAILED")
        assert outcome.failure is not None
        self.assertEqual(outcome.failure.code, "ingress.convergence-timeout")
        self.assertIs(outcome.failure.category, FailureCategory.TERMINAL)
        self.assertEqual(verifier.calls, [])

    def test_transient_attempt_error_records_redacted_limited_progress(self) -> None:
        verifier = RecordingPublicIngressReadinessVerifier(
            self.tracker,
            PublicIngressObservationStatus.READY,
        )
        verifier.error = RuntimeError("Bearer should-not-be-recorded")
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={},
            clock=lambda: "2026-07-28T08:01:30Z",
            readiness_verifier=verifier,
        )

        outcome = adapter.execute(
            self.context(
                activity_id="wait-gateway-public",
                operation=WaitForPublicIngressReady(
                    PublicIngressActivityTarget("gateway-001")
                ),
                desired_graph=self.graph(
                    verification=VerificationContract(
                        (
                            HttpCheck(
                                check_id="gateway-ready",
                                provider_socket="control",
                                path="/health/ready",
                            ),
                        )
                    )
                ),
            )
        )

        self.assertEqual(outcome.kind.name, "LIMITED_PROGRESS")
        self.assertEqual(
            outcome.evidence.descriptor()["exception_type"],
            "RuntimeError",
        )
        self.assertNotIn("should-not-be-recorded", repr(outcome))

    def test_mismatched_custody_receipt_compensates_without_admission(self) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        interpreter.return_mismatched_custody_receipt = True
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:01:00Z",
            secret_use_authorizer=self.authorizer,
        )

        outcome = adapter.execute(self.context())

        self.assertEqual(outcome.kind.name, "FAILED")
        self.assertEqual(len(interpreter.teardown_resources), 1)
        self.assertEqual(
            interpreter.teardown_custody_grants,
            interpreter.create_custody_grants,
        )
        with self.unit_of_work() as unit_of_work:
            resources = unit_of_work.stores.ingress_resources.list_cloudflare(
                "workspace-a"
            )
            references = unit_of_work.stores.secret_references.list_active(
                "workspace-a"
            )
        self.assertEqual(resources, ())
        self.assertEqual(references, ())

    def test_projection_failure_after_create_compensates_from_allocation_result(
        self,
    ) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        interpreter.return_invalid_coordinates = True
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:01:00Z",
            secret_use_authorizer=self.authorizer,
        )

        outcome = adapter.execute(self.context())

        self.assertEqual(outcome.kind.name, "FAILED")
        self.assertEqual(len(interpreter.teardown_resources), 1)
        allocation = interpreter.teardown_resources[0]
        self.assertEqual(allocation.tunnel_id, "tunnel\ninvalid")
        self.assertEqual(allocation.dns_record_id, "dns-001")
        self.assertEqual(interpreter.teardown_active_counts, [0])

    def test_invalid_fold_timestamp_fails_before_provider_mutation(self) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "invalid\ntimestamp",
            secret_use_authorizer=self.authorizer,
        )

        outcome = adapter.execute(self.context())

        self.assertEqual(outcome.kind.name, "UNSUPPORTED")
        self.assertEqual(interpreter.create_active_counts, [])
        self.assertEqual(interpreter.teardown_resources, [])

    def test_durable_fold_race_after_create_compensates_exact_allocation(
        self,
    ) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)

        def revoke_generated_secret_provider() -> None:
            with self.unit_of_work() as unit_of_work:
                unit_of_work.stores.secret_providers.revoke_active(
                    "workspace-a",
                    SecretProviderId("generated"),
                    revoked_by="concurrent-operator",
                    revoked_at="2026-07-28T08:00:59Z",
                )
                unit_of_work.commit()

        interpreter.on_create = revoke_generated_secret_provider
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:01:00Z",
            secret_use_authorizer=self.authorizer,
        )

        outcome = adapter.execute(self.context())

        self.assertEqual(outcome.kind.name, "FAILED")
        self.assertEqual(len(interpreter.teardown_resources), 1)
        self.assertEqual(interpreter.teardown_resources[0].tunnel_id, "tunnel-001")
        with self.unit_of_work() as unit_of_work:
            self.assertEqual(
                unit_of_work.stores.ingress_resources.list_cloudflare("workspace-a"),
                (),
            )

    def test_compensation_failure_preserves_bounded_owned_coordinates(self) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        interpreter.return_mismatched_custody_receipt = True
        interpreter.fail_teardown = True
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:01:00Z",
            secret_use_authorizer=self.authorizer,
        )

        outcome = adapter.execute(self.context())

        self.assertEqual(outcome.kind.name, "UNCERTAIN")
        self.assertEqual(outcome.failure.code, "ingress.compensation-uncertain")
        details = outcome.failure.details.descriptor()
        self.assertEqual(details["tunnel_id"], "tunnel-001")
        self.assertEqual(details["dns_record_id"], "dns-001")
        self.assertEqual(details["hostname"], "cpk-gateway-001.openj92.dev")
        self.assertEqual(details["fold_exception_type"], "InvalidOperationCommand")
        self.assertEqual(details["compensation_exception_type"], "RuntimeError")

    def test_allocate_public_ingress_uses_unique_tunnel_names_for_distinct_runs(
        self,
    ) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:01:00Z",
            secret_use_authorizer=self.authorizer,
        )

        first = adapter.execute(self.context())
        with self.unit_of_work() as unit_of_work:
            unit_of_work.stores.ingress_resources.mark_removed(
                "workspace-a",
                "gateway-001",
                removed_at="2026-07-28T08:02:00Z",
                removed_by_run_id="run-a",
            )
            unit_of_work.commit()
        second = adapter.execute(
            self.context(run_id="run-b", intent_event_id="event-002")
        )

        self.assertEqual(first.kind.name, "SUCCEEDED")
        self.assertEqual(second.kind.name, "SUCCEEDED")
        self.assertEqual(len(interpreter.create_allocation_names), 2)
        self.assertNotEqual(
            interpreter.create_allocation_names[0],
            interpreter.create_allocation_names[1],
        )
        for allocation_name in interpreter.create_allocation_names:
            self.assertRegex(allocation_name, r"^cpk-gateway-001-[0-9a-f]{12}$")

    def test_retained_provider_boundary_is_owned_by_operations(self) -> None:
        for name in (
            "IngressReservationCoordinates",
            "IngressResourcePresence",
            "IngressReservationObservation",
            "IngressTunnelObservation",
            "RetainedIngressDeactivationResult",
        ):
            with self.subTest(name=name):
                self.assertTrue(hasattr(ingress_realization, name))

    def test_initial_retained_allocation_creates_bound_reservation(self) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:01:00Z",
            secret_use_authorizer=self.authorizer,
        )

        outcome = adapter.execute(
            self.context(
                desired_graph=self.graph(lifecycle=PublicIngressLifecycle.RETAINED)
            )
        )

        self.assertEqual(outcome.kind.name, "SUCCEEDED")
        self.assertEqual(interpreter.create_active_counts, [0])
        self.assertEqual(interpreter.rebind_active_counts, [])
        with self.unit_of_work() as unit_of_work:
            reservations = unit_of_work.stores.ingress_reservations.list_cloudflare(
                "workspace-a"
            )
            resources = unit_of_work.stores.ingress_resources.list_cloudflare(
                "workspace-a"
            )
        self.assertEqual(len(reservations), 1)
        self.assertIs(reservations[0].status, OwnedHostnameReservationStatus.BOUND)
        self.assertEqual(resources[0].reservation_id, reservations[0].reservation_id)
        self.assertEqual(resources[0].epoch, 1)

    def test_retained_removal_deactivates_exact_epoch_and_reserves_hostname(
        self,
    ) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:03:00Z",
            secret_use_authorizer=self.authorizer,
        )
        with self.unit_of_work() as unit_of_work:
            self.record_retained_ingress(unit_of_work)
            unit_of_work.commit()

        outcome = adapter.execute(
            self.context(
                activity_id="remove-gateway",
                run_id="run-off",
                intent_event_id="event-off",
                operation=RemovePublicIngress(
                    PublicIngressActivityTarget("gateway-001")
                ),
                base_graph=self.graph(lifecycle=PublicIngressLifecycle.RETAINED),
                desired_graph=DeploymentGraph("empty"),
            )
        )

        self.assertEqual(outcome.kind.name, "SUCCEEDED")
        self.assertEqual(interpreter.teardown_active_counts, [])
        self.assertEqual(interpreter.deactivate_active_counts, [0])
        coordinates = interpreter.deactivate_reservations[0]
        self.assertEqual(coordinates.dns_record_id, "dns-001")
        self.assertEqual(coordinates.hostname, "cpk-gateway-001.openj92.dev")
        self.assertEqual(coordinates.expected_tunnel_id, "tunnel-001")
        with self.unit_of_work() as unit_of_work:
            resource = unit_of_work.stores.ingress_resources.list_cloudflare(
                "workspace-a"
            )[0]
            reservation = unit_of_work.stores.ingress_reservations.list_cloudflare(
                "workspace-a"
            )[0]
        self.assertIs(resource.status, OwnedIngressResourceStatus.REMOVED)
        self.assertIs(
            reservation.status,
            OwnedHostnameReservationStatus.RESERVED,
        )

    def test_retained_deactivation_ambiguity_marks_both_truths_uncertain(self) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        interpreter.contradict_deactivation = True
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:03:00Z",
            secret_use_authorizer=self.authorizer,
        )
        with self.unit_of_work() as unit_of_work:
            self.record_retained_ingress(unit_of_work)
            unit_of_work.commit()

        outcome = adapter.execute(
            self.context(
                activity_id="remove-gateway",
                run_id="run-off",
                intent_event_id="event-off",
                operation=RemovePublicIngress(
                    PublicIngressActivityTarget("gateway-001")
                ),
                base_graph=self.graph(lifecycle=PublicIngressLifecycle.RETAINED),
                desired_graph=DeploymentGraph("empty"),
            )
        )

        self.assertEqual(outcome.kind.name, "UNCERTAIN")
        self.assertNotIn("must-not-survive", repr(outcome))
        with self.unit_of_work() as unit_of_work:
            resource = unit_of_work.stores.ingress_resources.get_cloudflare(
                "workspace-a", "gateway-001"
            )
            reservation = unit_of_work.stores.ingress_reservations.require_cloudflare(
                "workspace-a", "reservation-001"
            )
        self.assertIs(resource.status, OwnedIngressResourceStatus.UNCERTAIN)
        self.assertIs(
            reservation.status,
            OwnedHostnameReservationStatus.UNCERTAIN,
        )

    def test_retained_deactivation_error_is_redacted_and_uncertain(self) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        interpreter.fail_deactivate = True
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:03:00Z",
            secret_use_authorizer=self.authorizer,
        )
        with self.unit_of_work() as unit_of_work:
            self.record_retained_ingress(unit_of_work)
            unit_of_work.commit()

        outcome = adapter.execute(
            self.context(
                activity_id="remove-gateway",
                run_id="run-off",
                intent_event_id="event-off",
                operation=RemovePublicIngress(
                    PublicIngressActivityTarget("gateway-001")
                ),
                base_graph=self.graph(lifecycle=PublicIngressLifecycle.RETAINED),
                desired_graph=DeploymentGraph("empty"),
            )
        )

        self.assertEqual(outcome.kind.name, "UNCERTAIN")
        self.assertNotIn("must-not-survive", repr(outcome))
        self.assertEqual(interpreter.deactivate_active_counts, [0])

    def test_retained_deactivation_fold_race_keeps_both_truths_uncertain(
        self,
    ) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:03:00Z",
            secret_use_authorizer=self.authorizer,
        )
        with self.unit_of_work() as unit_of_work:
            self.record_retained_ingress(unit_of_work)
            unit_of_work.commit()

        def race_reservation() -> None:
            with self.unit_of_work() as unit_of_work:
                unit_of_work.stores.ingress_reservations.mark_uncertain(
                    "workspace-a",
                    "reservation-001",
                    expected_version=1,
                    transitioned_at="2026-07-28T08:02:59Z",
                    source_run_id="run-race",
                    source_activity_id="race",
                    source_event_id="event-race",
                )
                unit_of_work.commit()

        interpreter.on_deactivate = race_reservation
        outcome = adapter.execute(
            self.context(
                activity_id="remove-gateway",
                run_id="run-off",
                intent_event_id="event-off",
                operation=RemovePublicIngress(
                    PublicIngressActivityTarget("gateway-001")
                ),
                base_graph=self.graph(lifecycle=PublicIngressLifecycle.RETAINED),
                desired_graph=DeploymentGraph("empty"),
            )
        )

        self.assertEqual(outcome.kind.name, "UNCERTAIN")
        self.assertEqual(
            outcome.failure.code,
            "ingress.deactivate-fold-uncertain",
        )
        with self.unit_of_work() as unit_of_work:
            resource = unit_of_work.stores.ingress_resources.get_cloudflare(
                "workspace-a", "gateway-001"
            )
            reservation = unit_of_work.stores.ingress_reservations.require_cloudflare(
                "workspace-a", "reservation-001"
            )
        self.assertIs(resource.status, OwnedIngressResourceStatus.UNCERTAIN)
        self.assertIs(
            reservation.status,
            OwnedHostnameReservationStatus.UNCERTAIN,
        )

    def test_reserved_reentry_rebinds_distinct_epoch_and_custody_lineage(self) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:04:00Z",
            secret_use_authorizer=self.authorizer,
        )
        with self.unit_of_work() as unit_of_work:
            self.record_reserved_ingress(unit_of_work)
            unit_of_work.commit()

        outcome = adapter.execute(
            self.context(
                run_id="run-on",
                intent_event_id="event-on",
                desired_graph=self.graph(lifecycle=PublicIngressLifecycle.RETAINED),
            )
        )

        self.assertEqual(outcome.kind.name, "SUCCEEDED")
        self.assertEqual(interpreter.create_active_counts, [])
        self.assertEqual(interpreter.rebind_active_counts, [0])
        coordinates = interpreter.rebind_reservations[0]
        self.assertEqual(coordinates.dns_record_id, "dns-001")
        self.assertEqual(coordinates.expected_tunnel_id, "tunnel-001")
        with self.unit_of_work() as unit_of_work:
            reservation = unit_of_work.stores.ingress_reservations.require_cloudflare(
                "workspace-a", "reservation-001"
            )
            resources = unit_of_work.stores.ingress_resources.list_cloudflare(
                "workspace-a"
            )
            secrets = unit_of_work.stores.generated_ingress_secrets.list_for_workspace(
                "workspace-a"
            )
        self.assertIs(reservation.status, OwnedHostnameReservationStatus.BOUND)
        self.assertEqual([resource.epoch for resource in resources], [1, 2])
        self.assertEqual(resources[0].tunnel_id, "tunnel-001")
        self.assertEqual(resources[1].tunnel_id, "tunnel-002")
        self.assertEqual(
            {secret.provider_version_id for secret in secrets},
            {"version-existing-ingress", "version-rebound-token"},
        )
        self.assertEqual(len({secret.secret_ref for secret in secrets}), 2)

    def test_bound_reservation_without_active_epoch_never_fresh_creates(self) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:04:00Z",
            secret_use_authorizer=self.authorizer,
        )
        with self.unit_of_work() as unit_of_work:
            self.record_retained_ingress(unit_of_work)
            unit_of_work.stores.ingress_resources.mark_removed(
                "workspace-a",
                "gateway-001",
                removed_at="2026-07-28T08:02:00Z",
                removed_by_run_id="run-off",
            )
            unit_of_work.commit()

        outcome = adapter.execute(
            self.context(
                run_id="run-on",
                intent_event_id="event-on",
                desired_graph=self.graph(lifecycle=PublicIngressLifecycle.RETAINED),
            )
        )

        self.assertEqual(outcome.kind.name, "UNSUPPORTED")
        self.assertEqual(interpreter.create_active_counts, [])
        self.assertEqual(interpreter.rebind_active_counts, [])

    def test_contradictory_rebind_result_is_uncertain_without_teardown(self) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        interpreter.contradict_rebind = True
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:04:00Z",
            secret_use_authorizer=self.authorizer,
        )
        with self.unit_of_work() as unit_of_work:
            self.record_reserved_ingress(unit_of_work)
            unit_of_work.commit()

        outcome = adapter.execute(
            self.context(
                run_id="run-on",
                intent_event_id="event-on",
                desired_graph=self.graph(lifecycle=PublicIngressLifecycle.RETAINED),
            )
        )

        self.assertEqual(outcome.kind.name, "UNCERTAIN")
        self.assertEqual(interpreter.teardown_resources, [])
        self.assertNotIn("dns-foreign", repr(outcome))
        with self.unit_of_work() as unit_of_work:
            reservation = unit_of_work.stores.ingress_reservations.require_cloudflare(
                "workspace-a", "reservation-001"
            )
        self.assertIs(
            reservation.status,
            OwnedHostnameReservationStatus.UNCERTAIN,
        )

    def test_rebind_fold_race_stays_uncertain_without_generic_teardown(self) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:04:00Z",
            secret_use_authorizer=self.authorizer,
        )
        with self.unit_of_work() as unit_of_work:
            self.record_reserved_ingress(unit_of_work)
            unit_of_work.commit()

        def race_reservation() -> None:
            with self.unit_of_work() as unit_of_work:
                unit_of_work.stores.ingress_reservations.mark_uncertain(
                    "workspace-a",
                    "reservation-001",
                    expected_version=2,
                    transitioned_at="2026-07-28T08:03:59Z",
                    source_run_id="run-race",
                    source_activity_id="race",
                    source_event_id="event-race",
                )
                unit_of_work.commit()

        interpreter.on_rebind = race_reservation
        outcome = adapter.execute(
            self.context(
                run_id="run-on",
                intent_event_id="event-on",
                desired_graph=self.graph(lifecycle=PublicIngressLifecycle.RETAINED),
            )
        )

        self.assertEqual(outcome.kind.name, "UNCERTAIN")
        self.assertEqual(interpreter.teardown_resources, [])
        self.assertNotIn("secret://", repr(outcome))

    def test_external_lifecycle_performs_no_owned_provider_effect(self) -> None:
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={},
            clock=lambda: "2026-07-28T08:04:00Z",
            secret_use_authorizer=self.authorizer,
        )
        external = self.graph(lifecycle=PublicIngressLifecycle.EXTERNAL)
        entered_before = self.tracker.entered

        allocation = adapter.execute(self.context(desired_graph=external))
        removal = adapter.execute(
            self.context(
                activity_id="remove-gateway",
                operation=RemovePublicIngress(
                    PublicIngressActivityTarget("gateway-001")
                ),
                base_graph=external,
                desired_graph=DeploymentGraph("empty"),
            )
        )

        self.assertEqual(allocation.kind.name, "SUCCEEDED")
        self.assertEqual(removal.kind.name, "SUCCEEDED")
        self.assertEqual(self.tracker.entered, entered_before)

    def test_exact_reservation_release_commits_released_after_absence(self) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:05:00Z",
            secret_use_authorizer=self.authorizer,
        )
        with self.unit_of_work() as unit_of_work:
            self.record_reserved_ingress(unit_of_work)
            unit_of_work.commit()
        context = self.context(
            activity_id="release-reservation",
            run_id="run-release",
            intent_event_id="event-release",
            operation=self.release_operation(),
            base_graph=DeploymentGraph("empty"),
            desired_graph=DeploymentGraph("empty"),
        )

        outcome = adapter.execute(context)
        replay = adapter.execute(context)

        self.assertEqual(outcome.kind.name, "SUCCEEDED")
        self.assertEqual(replay.kind.name, "UNSUPPORTED")
        self.assertEqual(interpreter.release_active_counts, [0])
        coordinates = interpreter.release_reservations[0]
        self.assertEqual(coordinates.dns_record_id, "dns-001")
        self.assertEqual(coordinates.hostname, "cpk-gateway-001.openj92.dev")
        self.assertEqual(coordinates.expected_tunnel_id, "tunnel-001")
        with self.unit_of_work() as unit_of_work:
            reservation = unit_of_work.stores.ingress_reservations.require_cloudflare(
                "workspace-a", "reservation-001"
            )
        self.assertIs(
            reservation.status,
            OwnedHostnameReservationStatus.RELEASED,
        )
        self.assertEqual(reservation.released_by_run_id, "run-release")

    def test_release_rejects_wrong_version_and_active_graph_before_io(self) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:05:00Z",
            secret_use_authorizer=self.authorizer,
        )
        with self.unit_of_work() as unit_of_work:
            self.record_reserved_ingress(unit_of_work)
            unit_of_work.commit()

        wrong_version = adapter.execute(
            self.context(
                activity_id="release-reservation",
                operation=self.release_operation(version=1),
                base_graph=DeploymentGraph("empty"),
                desired_graph=DeploymentGraph("empty"),
            )
        )
        graph_present = adapter.execute(
            self.context(
                activity_id="release-reservation",
                operation=self.release_operation(),
                base_graph=self.graph(lifecycle=PublicIngressLifecycle.RETAINED),
                desired_graph=self.graph(lifecycle=PublicIngressLifecycle.RETAINED),
            )
        )

        self.assertEqual(wrong_version.kind.name, "UNSUPPORTED")
        self.assertEqual(graph_present.kind.name, "UNSUPPORTED")
        self.assertEqual(wrong_version.failure.code, "ingress.release-unsupported")
        self.assertEqual(graph_present.failure.code, "ingress.release-unsupported")
        self.assertEqual(interpreter.release_active_counts, [])

    def test_release_rejects_blocking_realization_and_missing_epoch(self) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:05:00Z",
            secret_use_authorizer=self.authorizer,
        )
        with self.unit_of_work() as unit_of_work:
            self.record_reserved_ingress(unit_of_work)
            unit_of_work.stores.ingress_resources.record_cloudflare(
                self.cloudflare_resource(
                    lifecycle=PublicIngressLifecycle.RETAINED,
                    reservation_id="reservation-001",
                )
            )
            unit_of_work.commit()

        blocking = adapter.execute(
            self.context(
                activity_id="release-reservation",
                operation=self.release_operation(),
                base_graph=DeploymentGraph("empty"),
                desired_graph=DeploymentGraph("empty"),
            )
        )
        self.connection.execute(
            "DELETE FROM cpk_cloudflare_ingress_resources "
            "WHERE workspace_id = 'workspace-a'"
        )
        missing_epoch = adapter.execute(
            self.context(
                activity_id="release-reservation",
                operation=self.release_operation(),
                base_graph=DeploymentGraph("empty"),
                desired_graph=DeploymentGraph("empty"),
            )
        )

        self.assertEqual(blocking.kind.name, "UNSUPPORTED")
        self.assertEqual(missing_epoch.kind.name, "UNSUPPORTED")
        self.assertEqual(blocking.failure.code, "ingress.release-unsupported")
        self.assertEqual(missing_epoch.failure.code, "ingress.release-unsupported")
        self.assertEqual(interpreter.release_active_counts, [])

    def test_release_rejects_foreign_truth_and_worker_before_io(self) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:05:00Z",
            secret_use_authorizer=self.authorizer,
        )
        with self.unit_of_work() as unit_of_work:
            self.record_reserved_ingress(unit_of_work)
            unit_of_work.commit()
        context = self.context(
            activity_id="release-reservation",
            operation=self.release_operation(),
            base_graph=DeploymentGraph("empty"),
            desired_graph=DeploymentGraph("empty"),
        )

        self.connection.execute(
            "UPDATE cpk_cloudflare_ingress_resources "
            "SET dns_record_id = 'dns-foreign' "
            "WHERE workspace_id = 'workspace-a'"
        )
        foreign_resource = adapter.execute(context)
        self.connection.execute(
            "UPDATE cpk_cloudflare_ingress_resources "
            "SET dns_record_id = 'dns-001', zone_id = 'zone-foreign' "
            "WHERE workspace_id = 'workspace-a'"
        )
        self.connection.execute(
            "UPDATE cpk_cloudflare_hostname_reservations "
            "SET zone_id = 'zone-foreign' "
            "WHERE workspace_id = 'workspace-a'"
        )
        foreign_authority = adapter.execute(context)
        self.connection.execute(
            "UPDATE cpk_cloudflare_ingress_resources "
            "SET zone_id = 'zone-openj92' "
            "WHERE workspace_id = 'workspace-a'"
        )
        self.connection.execute(
            "UPDATE cpk_cloudflare_hostname_reservations "
            "SET zone_id = 'zone-openj92' "
            "WHERE workspace_id = 'workspace-a'"
        )
        missing_worker_scope = adapter.execute(
            self.context(
                activity_id="release-reservation",
                operation=self.release_operation(),
                base_graph=DeploymentGraph("empty"),
                desired_graph=DeploymentGraph("empty"),
                worker_scopes=(PolicyScope.SECRET_PROVIDER_USE,),
            )
        )

        for outcome in (
            foreign_resource,
            foreign_authority,
            missing_worker_scope,
        ):
            self.assertEqual(outcome.kind.name, "UNSUPPORTED")
            self.assertEqual(outcome.failure.code, "ingress.release-unsupported")
        self.assertEqual(interpreter.release_active_counts, [])

    def test_release_provider_error_is_redacted_and_uncertain(self) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        interpreter.fail_release = True
        outcome = self.release_with(interpreter)

        self.assertEqual(outcome.kind.name, "UNCERTAIN")
        self.assertNotIn("must-not-survive", repr(outcome))
        self.assert_release_reservation_uncertain()

    def test_release_present_result_is_uncertain(self) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        interpreter.contradict_release = True
        outcome = self.release_with(interpreter)

        self.assertEqual(outcome.kind.name, "UNCERTAIN")
        self.assert_release_reservation_uncertain()

    def test_release_fold_race_remains_uncertain(self) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:05:00Z",
            secret_use_authorizer=self.authorizer,
        )
        with self.unit_of_work() as unit_of_work:
            self.record_reserved_ingress(unit_of_work)
            unit_of_work.commit()

        def race_release() -> None:
            with self.unit_of_work() as unit_of_work:
                unit_of_work.stores.ingress_reservations.mark_uncertain(
                    "workspace-a",
                    "reservation-001",
                    expected_version=3,
                    transitioned_at="2026-07-28T08:04:59Z",
                    source_run_id="run-race",
                    source_activity_id="race",
                    source_event_id="event-race",
                )
                unit_of_work.commit()

        interpreter.on_release = race_release
        outcome = adapter.execute(
            self.context(
                activity_id="release-reservation",
                run_id="run-release",
                intent_event_id="event-release",
                operation=self.release_operation(),
                base_graph=DeploymentGraph("empty"),
                desired_graph=DeploymentGraph("empty"),
            )
        )

        self.assertEqual(outcome.kind.name, "UNCERTAIN")
        self.assertEqual(
            outcome.failure.code,
            "ingress.release-fold-uncertain",
        )

    def test_remove_public_ingress_marks_resource_removed_around_provider_io(
        self,
    ) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:02:00Z",
            secret_use_authorizer=self.authorizer,
        )
        with self.unit_of_work() as unit_of_work:
            self.record_existing_ingress(unit_of_work)
            unit_of_work.commit()

        outcome = adapter.execute(
            self.context(
                activity_id="remove-gateway",
                operation=RemovePublicIngress(PublicIngressActivityTarget("gateway-001")),
                base_graph=self.graph(),
                desired_graph=DeploymentGraph("empty"),
            )
        )

        self.assertEqual(outcome.kind.name, "SUCCEEDED")
        self.assertEqual(interpreter.teardown_active_counts, [0])
        self.assertEqual(len(interpreter.teardown_grants), 1)
        self.assertTrue(
            interpreter.teardown_grants[0].permits(
                SecretReference("secret://cloudflare/openj92/api-token"),
                SecretUseIntent.CLOUDFLARE_API_TOKEN,
            )
        )
        self.assertEqual(interpreter.teardown_resources[0].status.name, "REMOVING")
        with self.unit_of_work() as unit_of_work:
            history = unit_of_work.stores.ingress_resources.list_cloudflare(
                "workspace-a"
            )
        self.assertEqual(history[0].status, OwnedIngressResourceStatus.REMOVED)
        self.assertEqual(history[0].removed_at, "2026-07-28T08:02:00Z")
        self.assertEqual(history[0].removed_by_run_id, "run-a")

    def test_remove_public_ingress_marks_uncertain_when_provider_fails(
        self,
    ) -> None:
        interpreter = RecordingIngressInterpreter(self.tracker)
        interpreter.fail_teardown = True
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:02:00Z",
            secret_use_authorizer=self.authorizer,
        )
        with self.unit_of_work() as unit_of_work:
            self.record_existing_ingress(unit_of_work)
            unit_of_work.commit()

        outcome = adapter.execute(
            self.context(
                activity_id="remove-gateway",
                operation=RemovePublicIngress(PublicIngressActivityTarget("gateway-001")),
                base_graph=self.graph(),
                desired_graph=DeploymentGraph("empty"),
            )
        )

        self.assertEqual(outcome.kind.name, "UNCERTAIN")
        self.assertEqual(interpreter.teardown_active_counts, [0])
        with self.unit_of_work() as unit_of_work:
            resource = unit_of_work.stores.ingress_resources.get_cloudflare(
                "workspace-a",
                "gateway-001",
            )
        self.assertEqual(resource.status, OwnedIngressResourceStatus.UNCERTAIN)

    def graph(
        self,
        *,
        verification: VerificationContract | None = None,
        lifecycle: PublicIngressLifecycle = PublicIngressLifecycle.EPHEMERAL,
    ) -> DeploymentGraph:
        if verification is None:
            verification = VerificationContract(
                (
                    HttpCheck(
                        check_id="gateway-ready",
                        provider_socket="control",
                        path="/health/ready",
                    ),
                )
            )
        return DeploymentGraph(
            "ingress-test",
            nodes={
                "gateway": Node(
                    node_id="gateway",
                    block_family=BlockFamily.PROXY,
                    block_spec=BlockSpec("gateway", verification=verification),
                    kind="container",
                    runtime_id="docker-a",
                    sockets=BlockSockets(
                        providers=(ProviderSocket("control", Protocol.HTTP),)
                    ),
                    endpoints={
                        "control": Endpoint(
                            LiteralAddress("http://gateway:8000"),
                            Protocol.HTTP,
                        )
                    },
                ),
                "cloudflared": Node(
                    node_id="cloudflared",
                    block_family=BlockFamily.PROXY,
                    block_spec=BlockSpec("cloudflared"),
                    kind="container",
                    runtime_id="docker-a",
                    sockets=BlockSockets(),
                ),
            },
            runtimes={
                "docker-a": RuntimeRecord(
                    "docker-a",
                    RuntimeKind.DOCKER,
                    children=("gateway", "cloudflared"),
                )
            },
            public_ingresses=(
                NamedPublicIngress(
                    ingress_id="gateway-001",
                    authority_ref=IngressAuthorityReference("openj92-public-ingress"),
                    target=PublicIngressTarget("gateway", "control"),
                    connector_node_id="cloudflared",
                    hostname="cpk-gateway-001.openj92.dev",
                    readiness_check_id="gateway-ready",
                    lifecycle=lifecycle,
                ),
            ),
        )

    def cloudflare_resource(
        self,
        *,
        lifecycle: PublicIngressLifecycle = PublicIngressLifecycle.EPHEMERAL,
        reservation_id: str | None = None,
    ) -> CloudflareOwnedIngressResource:
        return CloudflareOwnedIngressResource(
            workspace_id="workspace-a",
            runtime_id="docker-a",
            ingress_id="gateway-001",
            reservation_id=reservation_id,
            authority_ref=IngressAuthorityReference("openj92-public-ingress"),
            provider_kind=IngressAuthorityProviderKind.CLOUDFLARE,
            tunnel_name="cpk-gateway-001",
            tunnel_id="tunnel-001",
            dns_record_id="dns-001",
            hostname="cpk-gateway-001.openj92.dev",
            zone_id="zone-openj92",
            lifecycle=lifecycle,
            created_at="2026-07-28T08:01:00Z",
            observed_at="2026-07-28T08:01:00Z",
            source_run_id="run-a",
            source_activity_id="allocate-gateway",
            source_event_id="event-001",
        )

    def record_existing_ingress(
        self,
        unit_of_work: TrackingUnitOfWork,
        *,
        lifecycle: PublicIngressLifecycle = PublicIngressLifecycle.EPHEMERAL,
        reservation_id: str | None = None,
    ) -> None:
        reference = SecretReference(
            "secret://generated/ingress/cloudflared-tunnel-token/existing"
        )
        registered = unit_of_work.stores.secret_references.register(
            RegisteredSecretReference(
                registration_id="sref-existing-ingress",
                workspace_id="workspace-a",
                reference=reference,
                provider_registration_id="sprov-generated-ingress",
                allowed_intents=(SecretUseIntent.CLOUDFLARE_TUNNEL_TOKEN,),
                admitted_by="worker-a",
                admitted_at="2026-07-28T08:01:00Z",
            )
        )
        unit_of_work.stores.ingress_resources.record_cloudflare(
            self.cloudflare_resource(
                lifecycle=lifecycle,
                reservation_id=reservation_id,
            )
        )
        unit_of_work.stores.generated_ingress_secrets.record(
            GeneratedIngressSecretReference(
                workspace_id="workspace-a",
                purpose=GeneratedSecretPurpose.CLOUDFLARED_TUNNEL_TOKEN,
                secret_ref=reference,
                provider_registration_id="sprov-generated-ingress",
                reference_registration_id=registered.registration_id,
                custody_id="scust-existing-ingress",
                provider_version_id="version-existing-ingress",
                provider_version_number=1,
                recorded_at="2026-07-28T08:01:00Z",
                source_run_id="run-a",
                source_activity_id="allocate-gateway",
                source_event_id="event-001",
            )
        )

    def record_retained_ingress(self, unit_of_work: TrackingUnitOfWork) -> None:
        unit_of_work.stores.ingress_reservations.record_cloudflare(
            CloudflareOwnedHostnameReservation(
                reservation_id="reservation-001",
                workspace_id="workspace-a",
                ingress_id="gateway-001",
                authority_ref=IngressAuthorityReference(
                    "openj92-public-ingress"
                ),
                provider_kind=IngressAuthorityProviderKind.CLOUDFLARE,
                dns_record_id="dns-001",
                hostname="cpk-gateway-001.openj92.dev",
                zone_id="zone-openj92",
                lifecycle=PublicIngressLifecycle.RETAINED,
                status=OwnedHostnameReservationStatus.BOUND,
                created_at="2026-07-28T08:01:00Z",
                observed_at="2026-07-28T08:01:00Z",
                source_run_id="run-a",
                source_activity_id="allocate-gateway",
                source_event_id="event-001",
            )
        )
        self.record_existing_ingress(
            unit_of_work,
            lifecycle=PublicIngressLifecycle.RETAINED,
            reservation_id="reservation-001",
        )

    def record_reserved_ingress(self, unit_of_work: TrackingUnitOfWork) -> None:
        self.record_retained_ingress(unit_of_work)
        unit_of_work.stores.ingress_resources.mark_removed(
            "workspace-a",
            "gateway-001",
            removed_at="2026-07-28T08:02:00Z",
            removed_by_run_id="run-off",
        )
        unit_of_work.stores.ingress_reservations.mark_reserved(
            "workspace-a",
            "reservation-001",
            expected_version=1,
            transitioned_at="2026-07-28T08:02:00Z",
            source_run_id="run-off",
            source_activity_id="remove-gateway",
            source_event_id="event-off",
        )

    def release_operation(
        self,
        *,
        version: int = 2,
    ) -> ReleasePublicIngressReservation:
        return ReleasePublicIngressReservation(
            PublicIngressReservationTarget(
                ingress_id="gateway-001",
                reservation_id="reservation-001",
                reservation_version=version,
            )
        )

    def release_with(
        self,
        interpreter: RecordingIngressInterpreter,
    ):
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={IngressAuthorityProviderKind.CLOUDFLARE: interpreter},
            clock=lambda: "2026-07-28T08:05:00Z",
            secret_use_authorizer=self.authorizer,
        )
        with self.unit_of_work() as unit_of_work:
            self.record_reserved_ingress(unit_of_work)
            unit_of_work.commit()
        return adapter.execute(
            self.context(
                activity_id="release-reservation",
                run_id="run-release",
                intent_event_id="event-release",
                operation=self.release_operation(),
                base_graph=DeploymentGraph("empty"),
                desired_graph=DeploymentGraph("empty"),
            )
        )

    def assert_release_reservation_uncertain(self) -> None:
        with self.unit_of_work() as unit_of_work:
            reservation = unit_of_work.stores.ingress_reservations.require_cloudflare(
                "workspace-a", "reservation-001"
            )
        self.assertIs(
            reservation.status,
            OwnedHostnameReservationStatus.UNCERTAIN,
        )

    def context(
        self,
        *,
        activity_id: str = "allocate-gateway",
        run_id: str = "run-a",
        intent_event_id: str = "event-001",
        operation: object | None = None,
        base_graph: DeploymentGraph | None = None,
        desired_graph: DeploymentGraph | None = None,
        worker_scopes: tuple[PolicyScope, ...] = (
            PolicyScope.EXECUTION_OPERATE,
            PolicyScope.SECRET_PROVIDER_USE,
        ),
    ) -> ActivityRealizationContext:
        graph = self.graph()
        operation = operation or AllocatePublicIngress(
            PublicIngressActivityTarget("gateway-001")
        )
        activity = PlannedActivity(
            ActivityId(activity_id),
            operation,
            risk=(
                RiskLevel.CRITICAL
                if isinstance(operation, ReleasePublicIngressReservation)
                else RiskLevel.LOW
            ),
            impact=(
                ActivityImpact.DESTRUCTIVE
                if isinstance(operation, ReleasePublicIngressReservation)
                else ActivityImpact.NON_DESTRUCTIVE
            ),
        )
        return ActivityRealizationContext(
            activity=activity,
            request=ExecutionRequestRecord(
                ExecutionRequestIdentity(
                    "request-a",
                    "workspace-a",
                    "session-a",
                    "plan-a",
                ),
                ExecutionRequestStatus.CLAIMED,
                "operator-a",
                "2026-07-28T08:00:00Z",
                "approval-request-a",
                "approval-decision-a",
                ExecutionIdempotency("execute-a", "fingerprint-a"),
                claim=ClaimIdentity(
                    "worker-a",
                    "2026-07-28T08:00:30Z",
                    "2026-07-28T08:10:30Z",
                ),
            ),
            run=ActivityRunRecord(
                run_id,
                "plan-a",
                AdmittedRun("request-a"),
                RetryIdentity(1),
                ActivityRunStatus.RUNNING,
                "2026-07-28T08:00:45Z",
                started_at="2026-07-28T08:00:50Z",
            ),
            plan_record=ActivityPlanRecord(
                "plan-a",
                "session-a",
                "graph-current",
                "graph-desired",
                ActivityPlanStatus.PLANNED,
                "2026-07-28T08:00:10Z",
                ActivityPlan((activity,)),
            ),
            base_graph=RealizedGraphProjectionRecord.identity_for_authored(
                authored_record=GraphVersionRecord.from_graph(
                    graph_id="graph-current",
                    workspace_id="workspace-a",
                    version=1,
                    graph=base_graph or DeploymentGraph("empty"),
                    created_by="operator-a",
                    created_at="2026-07-28T08:00:00Z",
                )
            ),
            desired_graph=RealizedGraphProjectionRecord.identity_for_authored(
                authored_record=GraphVersionRecord.from_graph(
                    graph_id="graph-desired",
                    workspace_id="workspace-a",
                    version=2,
                    graph=desired_graph or graph,
                    created_by="operator-a",
                    created_at="2026-07-28T08:00:05Z",
                )
            ),
            registered_products=(),
            authority=ExecutionWorkerAuthority(
                "worker-a",
                worker_scopes,
            ),
            intent_event=ActivityEventRecord(
                intent_event_id,
                run_id,
                1,
                ActivityEventKind.STEP_STARTED,
                "2026-07-28T08:01:00Z",
                activity_id=activity_id,
            ),
        )


if __name__ == "__main__":
    unittest.main()
