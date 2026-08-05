from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
import unittest

import psycopg

from control_plane_kit_core.algebra import BlockSockets, BlockSpec, ProviderSocket
from control_plane_kit_core.operations.lifecycle import (
    ActivityEventKind,
    ActivityRunStatus,
    ExecutionRequestStatus,
)
from control_plane_kit_core.policies import PolicyScope
from control_plane_kit_core.planning import (
    ActivityId,
    ActivityPlan,
    AllocatePublicIngress,
    PlannedActivity,
    PublicIngressActivityTarget,
    RemovePublicIngress,
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
    VerificationContract,
    VerificationPolicy,
)
from control_plane_kit_operations.coordinator import ActivityRealizationContext
from control_plane_kit_operations.ingress_authorities import (
    CloudflareOwnedIngressResource,
    CloudflareZoneIngressAuthority,
    GeneratedIngressSecretReference,
    GeneratedSecretPurpose,
    IngressAuthorityProviderKind,
    OwnedIngressResourceStatus,
)
from control_plane_kit_operations.ingress_realization import (
    IngressOwnedResourceCoordinates,
    IngressRealizationAdapter,
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
        self.fail_teardown = False
        self.return_mismatched_custody_receipt = False
        self.return_invalid_coordinates = False
        self.on_create: Callable[[], None] | None = None

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
        self.active_counts: list[int] = []
        self.calls: list[
            tuple[NamedPublicIngress, HttpCheck, RuntimeEndpointObservation]
        ] = []

    def observe(
        self,
        *,
        ingress: NamedPublicIngress,
        check: HttpCheck,
        endpoint: RuntimeEndpointObservation,
    ) -> PublicIngressObservation:
        self.active_counts.append(self.tracker.active)
        self.calls.append((ingress, check, endpoint))
        return PublicIngressObservation(
            ingress_id=ingress.ingress_id,
            hostname=ingress.hostname,
            url=f"https://{ingress.hostname}",
            target=ingress.target,
            observed_at="2026-07-28T08:01:30Z",
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

    def test_unready_public_ingress_fails_activity_and_keeps_bounded_observation(
        self,
    ) -> None:
        verifier = RecordingPublicIngressReadinessVerifier(
            self.tracker,
            PublicIngressObservationStatus.UNREADY,
        )
        adapter = IngressRealizationAdapter(
            self.unit_of_work,
            interpreters={},
            clock=lambda: "2026-07-28T08:01:00Z",
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
        self.assertEqual(outcome.failure.code, "ingress.readiness-unready")
        self.assertEqual(len(outcome.observations), 1)
        self.assertIs(
            outcome.observations[0].status,
            ObservationStatus.VERIFICATION_FAILED,
        )
        self.assertNotIn("secret://", repr(outcome))

    def test_public_ingress_readiness_requires_one_exact_target_http_check(self) -> None:
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
        )

        for ordinal, values in enumerate(checks):
            with self.subTest(count=len(values)):
                outcome = adapter.execute(
                    self.context(
                        activity_id=f"wait-gateway-public-{ordinal}",
                        operation=WaitForPublicIngressReady(
                            PublicIngressActivityTarget("gateway-001")
                        ),
                        desired_graph=self.graph(
                            verification=VerificationContract(values)
                        ),
                    )
                )
                self.assertEqual(outcome.kind.name, "UNSUPPORTED")
                self.assertEqual(
                    outcome.failure.code,
                    "ingress.readiness-contract-unsupported",
                )
        self.assertEqual(verifier.calls, [])

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
        verification: VerificationContract = VerificationContract(),
    ) -> DeploymentGraph:
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
                ),
            ),
        )

    def cloudflare_resource(self) -> CloudflareOwnedIngressResource:
        return CloudflareOwnedIngressResource(
            workspace_id="workspace-a",
            runtime_id="docker-a",
            ingress_id="gateway-001",
            authority_ref=IngressAuthorityReference("openj92-public-ingress"),
            provider_kind=IngressAuthorityProviderKind.CLOUDFLARE,
            tunnel_name="cpk-gateway-001",
            tunnel_id="tunnel-001",
            dns_record_id="dns-001",
            hostname="cpk-gateway-001.openj92.dev",
            zone_id="zone-openj92",
            lifecycle=self.graph().public_ingresses[0].lifecycle,
            created_at="2026-07-28T08:01:00Z",
            observed_at="2026-07-28T08:01:00Z",
            source_run_id="run-a",
            source_activity_id="allocate-gateway",
            source_event_id="event-001",
        )

    def record_existing_ingress(self, unit_of_work: TrackingUnitOfWork) -> None:
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
            self.cloudflare_resource()
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

    def context(
        self,
        *,
        activity_id: str = "allocate-gateway",
        run_id: str = "run-a",
        intent_event_id: str = "event-001",
        operation: object | None = None,
        base_graph: DeploymentGraph | None = None,
        desired_graph: DeploymentGraph | None = None,
    ) -> ActivityRealizationContext:
        graph = self.graph()
        operation = operation or AllocatePublicIngress(
            PublicIngressActivityTarget("gateway-001")
        )
        activity = PlannedActivity(
            ActivityId(activity_id),
            operation,
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
                (
                    PolicyScope.EXECUTION_OPERATE,
                    PolicyScope.SECRET_PROVIDER_USE,
                ),
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
